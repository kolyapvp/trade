from __future__ import annotations

import asyncio
from typing import Optional

import ccxt.async_support as ccxt

from ..domain.ports import IExchange, ExchangeInfo, ExchangeOrder, Ticker, FuturesTicker
from ..domain.value_objects import Fee, OrderBook, OrderBookLevel


SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    'MATIC/USDT': ('POL/USDT',),
}


class CcxtExchangeAdapter(IExchange):
    def __init__(
        self,
        exchange: ccxt.Exchange,
        fee: Fee,
        supports_futures: bool = False,
    ):
        self._exchange = exchange
        self.info = ExchangeInfo(
            id=exchange.id,
            name=getattr(exchange, 'name', exchange.id),
            fee=fee,
            supports_spot=True,
            supports_futures=supports_futures,
        )
        self._markets_loaded = False
        self._markets_lock = asyncio.Lock()
        self._symbol_cache: dict[str, str] = {}
        self._requires_market_bootstrap = exchange.id == 'gateio'

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        market_symbol, raw = await self._call_exchange_method('fetch_order_book', symbol, limit)
        bids = [OrderBookLevel(price=b[0], quantity=b[1]) for b in (raw.get('bids') or [])]
        asks = [OrderBookLevel(price=a[0], quantity=a[1]) for a in (raw.get('asks') or [])]
        return OrderBook(
            symbol=market_symbol,
            exchange_id=self._exchange.id,
            bids=bids,
            asks=asks,
            timestamp=raw.get('timestamp') or 0,
        )

    async def fetch_ticker(self, symbol: str) -> Ticker:
        market_symbol, raw = await self._call_exchange_method('fetch_ticker', symbol)
        return Ticker(
            symbol=market_symbol,
            exchange_id=self._exchange.id,
            bid=raw.get('bid') or 0.0,
            ask=raw.get('ask') or 0.0,
            last=raw.get('last') or 0.0,
            volume=raw.get('baseVolume') or 0.0,
            timestamp=raw.get('timestamp') or 0,
        )

    async def fetch_tickers(self, symbols: list[str]) -> list[Ticker]:
        result: list[Ticker] = []
        if self._exchange.has.get('fetchTickers'):
            try:
                raw = await self._exchange.fetch_tickers(symbols)
                for symbol in symbols:
                    t = raw.get(symbol)
                    if t:
                        result.append(Ticker(
                            symbol=symbol,
                            exchange_id=self._exchange.id,
                            bid=t.get('bid') or 0.0,
                            ask=t.get('ask') or 0.0,
                            last=t.get('last') or 0.0,
                            volume=t.get('baseVolume') or 0.0,
                            timestamp=t.get('timestamp') or 0,
                        ))
                return result
            except Exception:
                pass

        for symbol in symbols:
            try:
                result.append(await self.fetch_ticker(symbol))
            except Exception:
                pass
        return result

    async def fetch_futures_ticker(self, symbol: str) -> Optional[FuturesTicker]:
        if not self.info.supports_futures:
            return None
        try:
            market_symbol, raw = await self._call_exchange_method('fetch_ticker', symbol)
            funding = None
            try:
                funding = await self._exchange.fetch_funding_rate(market_symbol)
            except Exception:
                pass

            funding_rate = 0.0
            next_funding = 0
            if funding:
                funding_rate = funding.get('fundingRate') or 0.0
                next_funding = funding.get('nextFundingDatetime') or 0

            info = raw.get('info') or {}
            return FuturesTicker(
                symbol=market_symbol,
                exchange_id=self._exchange.id,
                bid=raw.get('bid') or 0.0,
                ask=raw.get('ask') or 0.0,
                last=raw.get('last') or 0.0,
                volume=raw.get('baseVolume') or 0.0,
                timestamp=raw.get('timestamp') or 0,
                funding_rate=funding_rate,
                next_funding_time=next_funding,
                mark_price=float(info.get('markPrice') or raw.get('last') or 0),
                index_price=float(info.get('indexPrice') or raw.get('last') or 0),
            )
        except Exception:
            return None

    async def fetch_free_balance(self, currency: str) -> float:
        balance = await self._exchange.fetch_balance()
        free = balance.get('free') or {}
        value = free.get(currency)
        if value is None:
            account = balance.get(currency) or {}
            value = account.get('free', 0.0)
        return float(value or 0.0)

    async def normalize_order_amount(self, symbol: str, base_amount: float) -> float:
        market_symbol, market = await self._get_market(symbol)
        if base_amount <= 0:
            return 0.0
        if market.get('contract'):
            contract_size = float(market.get('contractSize') or 1.0)
            order_amount = base_amount / contract_size
        else:
            order_amount = base_amount
        precise = self._exchange.amount_to_precision(market_symbol, order_amount)
        return float(precise)

    async def convert_order_amount_to_base(self, symbol: str, order_amount: float) -> float:
        if order_amount <= 0:
            return 0.0
        _, market = await self._get_market(symbol)
        if market.get('contract'):
            contract_size = float(market.get('contractSize') or 1.0)
            return float(order_amount) * contract_size
        return float(order_amount)

    async def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reduce_only: bool = False,
    ) -> ExchangeOrder:
        market_symbol, market = await self._get_market(symbol)
        params = {'reduceOnly': True} if reduce_only else {}
        raw = await self._exchange.create_order(market_symbol, 'market', side, amount, None, params)
        filled = float(raw.get('filled') or raw.get('amount') or amount or 0.0)
        base_amount = await self.convert_order_amount_to_base(symbol, filled)
        return ExchangeOrder(
            id=str(raw.get('id') or ''),
            symbol=market_symbol,
            side=str(raw.get('side') or side),
            type=str(raw.get('type') or 'market'),
            amount=float(raw.get('amount') or amount or 0.0),
            filled=filled,
            base_amount=base_amount,
            average=float(raw.get('average') or raw.get('price') or 0.0),
            cost=float(raw.get('cost') or 0.0),
            status=str(raw.get('status') or 'open'),
            reduce_only=reduce_only and bool(market.get('contract')),
        )

    async def is_available(self) -> bool:
        try:
            await self._exchange.fetch_status()
            return True
        except Exception:
            try:
                await self._exchange.fetch_time()
                return True
            except Exception:
                return False

    async def close(self) -> None:
        await self._exchange.close()

    async def _ensure_markets_loaded(self) -> None:
        if self._markets_loaded:
            return
        async with self._markets_lock:
            if self._markets_loaded:
                return
            timeout_seconds = max((self._exchange.timeout or 10000) / 1000, 10)
            await asyncio.wait_for(self._exchange.load_markets(), timeout=timeout_seconds)
            self._markets_loaded = True

    async def _call_exchange_method(self, method_name: str, symbol: str, *args):
        market_symbol = await self._prepare_symbol(symbol)
        method = getattr(self._exchange, method_name)
        try:
            return market_symbol, await method(market_symbol, *args)
        except Exception as exc:
            fallback_symbol = await self._resolve_symbol_from_exception(symbol, exc)
            if fallback_symbol == market_symbol:
                raise
            return fallback_symbol, await method(fallback_symbol, *args)

    async def _prepare_symbol(self, symbol: str) -> str:
        if self._requires_market_bootstrap:
            await self._ensure_markets_loaded()
        return self._symbol_cache.get(symbol, symbol)

    async def _get_market(self, symbol: str) -> tuple[str, dict]:
        await self._ensure_markets_loaded()
        market_symbol = await self._resolve_alias_symbol(symbol)
        return market_symbol, self._exchange.market(market_symbol)

    async def _resolve_symbol_from_exception(self, symbol: str, exc: Exception) -> str:
        if not self._is_unknown_symbol_error(exc):
            return self._symbol_cache.get(symbol, symbol)
        return await self._resolve_alias_symbol(symbol)

    async def _resolve_alias_symbol(self, symbol: str) -> str:
        cached = self._symbol_cache.get(symbol)
        if cached:
            return cached

        await self._ensure_markets_loaded()

        resolved = symbol
        if symbol not in self._exchange.markets:
            for alias in SYMBOL_ALIASES.get(symbol, ()):
                if alias in self._exchange.markets:
                    resolved = alias
                    break
            if resolved == symbol:
                base, _, quote = symbol.partition('/')
                for market_symbol, market in self._exchange.markets.items():
                    if market.get('base') != base or market.get('quote') != quote:
                        continue
                    if bool(market.get('contract')) != self.info.supports_futures:
                        continue
                    resolved = market_symbol
                    break

        self._symbol_cache[symbol] = resolved
        return resolved

    def _is_unknown_symbol_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return 'does not have market symbol' in message or 'badsymbol' in message
