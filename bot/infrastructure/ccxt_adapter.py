from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import ccxt.async_support as ccxt

from ..domain.ports import (
    IExchange,
    ExchangeInfo,
    ExchangeOrder,
    ExchangePosition,
    FundingPayment,
    MarketDescriptor,
    Ticker,
    FuturesTicker,
)
from ..domain.value_objects import Fee, OrderBook, OrderBookLevel

logger = logging.getLogger(__name__)


SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    'MATIC/USDT': ('POL/USDT',),
}


class CcxtExchangeAdapter(IExchange):
    def __init__(
        self,
        exchange: ccxt.Exchange,
        fee: Fee,
        supports_futures: bool = False,
        exchange_id: str | None = None,
    ):
        self._exchange = exchange
        self._exchange_id = exchange_id or exchange.id
        self.info = ExchangeInfo(
            id=self._exchange_id,
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
            exchange_id=self.info.id,
            bids=bids,
            asks=asks,
            timestamp=raw.get('timestamp') or 0,
        )

    async def fetch_ticker(self, symbol: str) -> Ticker:
        market_symbol, raw = await self._call_exchange_method('fetch_ticker', symbol)
        requested_symbol = self._symbol_to_requested(symbol, market_symbol)
        return Ticker(
            symbol=requested_symbol,
            exchange_id=self.info.id,
            bid=raw.get('bid') or 0.0,
            ask=raw.get('ask') or 0.0,
            last=raw.get('last') or 0.0,
            volume=raw.get('baseVolume') or 0.0,
            timestamp=raw.get('timestamp') or 0,
        )

    async def fetch_tickers(self, symbols: list[str]) -> list[Ticker]:
        if not symbols:
            return []
        if not self._exchange.has.get('fetchTickers'):
            raise RuntimeError(f'{self.info.id} does not support fetch_tickers')
        requested_by_market = await self._prepare_symbol_map(symbols)
        raw = await self._invoke_public_data_call(
            'fetch_tickers',
            self._exchange.fetch_tickers,
            list(requested_by_market),
        )
        result: list[Ticker] = []
        for market_symbol, requested_symbol in requested_by_market.items():
            ticker = raw.get(market_symbol)
            if ticker is None:
                continue
            result.append(self._build_ticker(requested_symbol, ticker))
        return result

    async def fetch_futures_ticker(self, symbol: str) -> Optional[FuturesTicker]:
        if not self.info.supports_futures:
            return None
        market_symbol, raw = await self._call_exchange_method('fetch_ticker', symbol)
        funding = await self._fetch_funding_rate(market_symbol)
        return self._build_futures_ticker(
            self._symbol_to_requested(symbol, market_symbol),
            market_symbol,
            raw,
            funding,
        )

    async def fetch_futures_tickers(self, symbols: list[str]) -> list[FuturesTicker]:
        if not self.info.supports_futures:
            return []
        if not symbols:
            return []
        if not self._exchange.has.get('fetchTickers'):
            raise RuntimeError(f'{self.info.id} does not support fetch_tickers')

        requested_by_market = await self._prepare_symbol_map(symbols)
        raw_tickers = await self._invoke_public_data_call(
            'fetch_tickers',
            self._exchange.fetch_tickers,
            list(requested_by_market),
        )
        funding_map = await self._fetch_funding_rates(list(requested_by_market))

        result: list[FuturesTicker] = []
        for market_symbol, requested_symbol in requested_by_market.items():
            ticker = raw_tickers.get(market_symbol)
            if ticker is None:
                continue
            result.append(
                self._build_futures_ticker(
                    requested_symbol,
                    market_symbol,
                    ticker,
                    funding_map.get(market_symbol),
                )
            )
        return result

    async def fetch_free_balance(self, currency: str) -> float:
        balance = await self._exchange.fetch_balance()
        free = balance.get('free') or {}
        value = free.get(currency)
        if value is None:
            account = balance.get(currency) or {}
            value = account.get('free', 0.0)
        return float(value or 0.0)

    async def fetch_total_balance_usdt(self, quote_currency: str = 'USDT') -> float:
        params = {'type': 'swap'} if self.info.supports_futures else {}
        balance = await self._exchange.fetch_balance(params)
        if self.info.supports_futures:
            totals = balance.get('total') or {}
            value = totals.get(quote_currency)
            if value is None:
                account = balance.get(quote_currency) or {}
                value = account.get('total', 0.0)
            return float(value or 0.0)
        totals = balance.get('total') or {}
        total_balance_usdt = 0.0
        for currency, raw_amount in totals.items():
            amount = float(raw_amount or 0.0)
            if amount <= 0:
                continue
            if currency == quote_currency:
                total_balance_usdt += amount
                continue
            conversion_symbol = await self._find_conversion_symbol(currency, quote_currency)
            if conversion_symbol is None:
                continue
            ticker = await self._exchange.fetch_ticker(conversion_symbol)
            price = ticker.get('last') or ticker.get('bid') or ticker.get('ask')
            if price is None:
                continue
            total_balance_usdt += amount * float(price)
        return total_balance_usdt

    async def fetch_total_balances(self, currencies: list[str]) -> dict[str, float]:
        balance = await self._exchange.fetch_balance()
        totals = balance.get('total') or {}
        result: dict[str, float] = {}
        for currency in currencies:
            value = totals.get(currency)
            if value is None:
                account = balance.get(currency) or {}
                value = account.get('total', 0.0)
            result[currency] = float(value or 0.0)
        return result

    async def get_trading_fee(self, symbol: str) -> Fee:
        _, market = await self._get_market(symbol)
        maker = market.get('maker')
        taker = market.get('taker')
        return Fee(
            float(maker if maker is not None else self.info.fee.maker),
            float(taker if taker is not None else self.info.fee.taker),
        )

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
        precise_amount = float(self._exchange.amount_to_precision(market_symbol, amount))
        params = {'reduceOnly': True} if reduce_only else {}
        price = None
        requires_price_for_market_buy = (
            side == 'buy'
            and not bool(market.get('contract'))
            and bool(self._exchange.options.get('createMarketBuyOrderRequiresPrice'))
        )
        if requires_price_for_market_buy:
            ticker = await self._exchange.fetch_ticker(market_symbol)
            raw_price = ticker.get('ask') or ticker.get('last') or ticker.get('bid')
            if raw_price is None:
                raise RuntimeError(f'Cannot determine price for market buy on {self.info.id} {market_symbol}')
            price = float(self._exchange.price_to_precision(market_symbol, raw_price))
        raw = await self._exchange.create_order(market_symbol, 'market', side, precise_amount, price, params)
        execution = await self._resolve_order_execution(market_symbol, market, raw)
        filled = execution['filled'] or float(raw.get('filled') or raw.get('amount') or precise_amount or 0.0)
        base_amount = await self.convert_order_amount_to_base(symbol, filled)
        fee_currency = execution['fee_currency']
        fee_cost = execution['fee_cost']
        if side == 'buy' and not bool(market.get('contract')) and fee_currency == market.get('base'):
            base_amount = max(base_amount - fee_cost, 0.0)
        return ExchangeOrder(
            id=str(raw.get('id') or ''),
            symbol=market_symbol,
            side=str(raw.get('side') or side),
            type=str(raw.get('type') or 'market'),
            amount=float(raw.get('amount') or precise_amount or 0.0),
            filled=filled,
            base_amount=base_amount,
            average=execution['average'],
            cost=execution['cost'],
            status=str(raw.get('status') or 'open'),
            fee_currency=fee_currency or '',
            fee_cost=fee_cost,
            fee_cost_quote=execution['fee_cost_quote'],
            timestamp=execution['timestamp'],
            reduce_only=reduce_only and bool(market.get('contract')),
        )

    async def fetch_funding_payments(
        self,
        symbol: str,
        since: int | None = None,
        until: int | None = None,
        limit: int = 100,
    ) -> list[FundingPayment]:
        if not self.info.supports_futures or not self._supports('fetchFundingHistory'):
            return []
        market_symbol, _ = await self._get_market(symbol)
        params = {}
        if until is not None:
            params['until'] = until
        try:
            raw_items = await self._exchange.fetch_funding_history(
                market_symbol,
                since,
                limit,
                params,
            )
        except Exception:
            return []
        payments: list[FundingPayment] = []
        for item in raw_items or []:
            timestamp = int(item.get('timestamp') or 0)
            if since is not None and timestamp and timestamp < since:
                continue
            if until is not None and timestamp and timestamp > until:
                continue
            payments.append(FundingPayment(
                symbol=str(item.get('symbol') or market_symbol),
                code=str(item.get('code') or ''),
                amount=float(item.get('amount') or 0.0),
                timestamp=timestamp,
                id=str(item.get('id') or ''),
            ))
        return payments

    async def list_markets(self) -> list[MarketDescriptor]:
        await self._ensure_markets_loaded()
        result: list[MarketDescriptor] = []
        for market_symbol, market in self._exchange.markets.items():
            base = str(market.get('base') or '')
            quote = str(market.get('quote') or '')
            settle = str(market.get('settle') or '')
            if not base or not quote:
                continue
            normalized_symbol = f'{base}/{quote}'
            self._remember_symbol_alias(normalized_symbol, market_symbol, market)
            result.append(MarketDescriptor(
                exchange_id=self.info.id,
                symbol=normalized_symbol,
                base=base,
                quote=quote,
                active=bool(market.get('active', True)),
                spot=bool(market.get('spot')),
                future=bool(market.get('future')),
                swap=bool(market.get('swap')),
                contract=bool(market.get('contract')),
                linear=bool(market.get('linear')),
                settle=settle,
            ))
        return result

    def _extract_order_fee(self, raw: dict) -> tuple[str | None, float]:
        fee = raw.get('fee')
        if isinstance(fee, dict):
            currency = fee.get('currency')
            cost = fee.get('cost')
            if currency is not None and cost is not None:
                return str(currency), abs(float(cost))

        fees = raw.get('fees')
        if isinstance(fees, list):
            for item in fees:
                if not isinstance(item, dict):
                    continue
                currency = item.get('currency')
                cost = item.get('cost')
                if currency is not None and cost is not None:
                    return str(currency), abs(float(cost))

        info = raw.get('info') or {}
        fee_detail = info.get('feeDetail')
        if fee_detail:
            parsed = fee_detail
            if isinstance(parsed, str):
                try:
                    parsed = json.loads(parsed)
                except json.JSONDecodeError:
                    parsed = None
            if isinstance(parsed, dict):
                fee_coin = parsed.get('feeCoin')
                total_fee = parsed.get('totalFee')
                if fee_coin is not None and total_fee not in {None, ''}:
                    return str(fee_coin), abs(float(total_fee))
                for key, item in parsed.items():
                    if key == 'newFees' or not isinstance(item, dict):
                        continue
                    currency = item.get('feeCoinCode') or key
                    total_fee = item.get('totalFee')
                    if currency is None or total_fee is None:
                        continue
                    return str(currency), abs(float(total_fee))

        return None, 0.0

    async def _resolve_order_execution(self, market_symbol: str, market: dict, raw: dict) -> dict[str, float | str]:
        order_id = str(raw.get('id') or '')
        order_timestamp = int(raw.get('timestamp') or self._exchange.milliseconds() or 0)
        trades = await self._resolve_order_trades(market_symbol, order_id, order_timestamp, raw)

        average = float(raw.get('average') or raw.get('price') or 0.0)
        cost = float(raw.get('cost') or 0.0)
        filled = float(raw.get('filled') or raw.get('amount') or 0.0)
        fee_currency, fee_cost = self._extract_order_fee(raw)
        fee_cost_quote = await self._convert_fee_to_quote(
            market,
            fee_currency,
            fee_cost,
            average,
        )

        if trades:
            aggregated_cost = 0.0
            aggregated_amount = 0.0
            fee_totals: dict[str, float] = {}
            for trade in trades:
                trade_amount = abs(float(trade.get('amount') or 0.0))
                trade_cost = abs(float(trade.get('cost') or 0.0))
                trade_price = float(trade.get('price') or 0.0)
                if trade_cost <= 0 and trade_amount > 0 and trade_price > 0:
                    trade_cost = trade_amount * trade_price
                aggregated_amount += trade_amount
                aggregated_cost += trade_cost
                self._accumulate_fees(fee_totals, trade.get('fee'))
                self._accumulate_fee_list(fee_totals, trade.get('fees'))

            if aggregated_cost > 0:
                cost = aggregated_cost
            if aggregated_amount > 0:
                average = cost / aggregated_amount if cost > 0 else average

            if fee_totals:
                fee_cost_quote = 0.0
                for currency, total_cost in fee_totals.items():
                    fee_cost_quote += await self._convert_fee_to_quote(
                        market,
                        currency,
                        total_cost,
                        average,
                    )
                if len(fee_totals) == 1:
                    fee_currency, fee_cost = next(iter(fee_totals.items()))
                else:
                    fee_currency = ''
                    fee_cost = 0.0

        return {
            'average': average,
            'cost': cost,
            'filled': filled,
            'fee_currency': fee_currency or '',
            'fee_cost': fee_cost,
            'fee_cost_quote': fee_cost_quote,
            'timestamp': order_timestamp,
        }

    async def _resolve_order_trades(
        self,
        market_symbol: str,
        order_id: str,
        order_timestamp: int,
        raw: dict,
    ) -> list[dict]:
        raw_trades = raw.get('trades')
        if isinstance(raw_trades, list) and raw_trades:
            return [trade for trade in raw_trades if isinstance(trade, dict)]
        if not order_id or not self._supports('fetchMyTrades'):
            return []
        since = max(order_timestamp - 5 * 60 * 1000, 0)
        for attempt in range(3):
            try:
                trades = await self._exchange.fetch_my_trades(market_symbol, since, 100)
            except Exception:
                return []
            matched = [
                trade for trade in trades
                if isinstance(trade, dict) and str(trade.get('order') or '') == order_id
            ]
            if matched:
                return matched
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
        return []

    def _accumulate_fees(self, fee_totals: dict[str, float], fee: dict | None) -> None:
        if not isinstance(fee, dict):
            return
        currency = fee.get('currency')
        cost = fee.get('cost')
        if currency is None or cost is None:
            return
        fee_totals[str(currency)] = fee_totals.get(str(currency), 0.0) + abs(float(cost))

    def _accumulate_fee_list(self, fee_totals: dict[str, float], fees: list | None) -> None:
        if not isinstance(fees, list):
            return
        for item in fees:
            self._accumulate_fees(fee_totals, item if isinstance(item, dict) else None)

    async def _convert_fee_to_quote(
        self,
        market: dict,
        fee_currency: str | None,
        fee_cost: float,
        average_price: float,
    ) -> float:
        if not fee_currency or fee_cost <= 0:
            return 0.0
        quote_currency = str(market.get('quote') or market.get('settle') or '')
        base_currency = str(market.get('base') or '')
        if fee_currency == quote_currency:
            return fee_cost
        if fee_currency == base_currency and average_price > 0:
            return fee_cost * average_price
        conversion_symbol = await self._find_conversion_symbol(fee_currency, quote_currency)
        if conversion_symbol is None:
            return 0.0
        ticker = await self._exchange.fetch_ticker(conversion_symbol)
        price = ticker.get('last') or ticker.get('bid') or ticker.get('ask')
        if price is None:
            return 0.0
        return fee_cost * float(price)

    async def prepare_futures_execution(
        self,
        symbol: str,
        leverage: int,
        margin_mode: str,
        one_way: bool = True,
    ) -> None:
        if not self.info.supports_futures:
            raise RuntimeError(f'Exchange {self.info.id} does not support futures execution')
        if leverage < 1:
            raise RuntimeError(f'Invalid leverage: {leverage}')
        if margin_mode not in {'isolated', 'cross'}:
            raise RuntimeError(f'Invalid margin mode: {margin_mode}')

        market_symbol, market = await self._get_market(symbol)
        if not market.get('contract'):
            raise RuntimeError(f'Market {market_symbol} is not a futures market')

        current_state = await self._read_futures_state(market_symbol, market)
        if current_state['has_open_position']:
            raise RuntimeError(
                f'External futures position already exists on {self.info.id} for {market_symbol}'
            )

        if one_way and self._supports('setPositionMode'):
            await self._apply_futures_setting(
                self._exchange.set_position_mode(False, market_symbol),
            )

        current_margin_mode = current_state['margin_mode']
        if current_margin_mode != margin_mode and self._supports('setMarginMode'):
            await self._apply_futures_setting(
                self._exchange.set_margin_mode(
                    margin_mode,
                    market_symbol,
                    {'leverage': str(leverage)},
                ),
            )

        if self._supports('setLeverage'):
            await self._apply_futures_setting(
                self._exchange.set_leverage(leverage, market_symbol),
            )

        verified_state = await self._read_futures_state(market_symbol, market)
        if verified_state['has_open_position']:
            raise RuntimeError(
                f'Unexpected open futures position detected on {self.info.id} for {market_symbol}'
            )
        if one_way and verified_state['hedged']:
            raise RuntimeError(
                f'Position mode is hedged on {self.info.id} for {market_symbol}'
            )
        verified_margin_mode = verified_state['margin_mode']
        if self._supports('setMarginMode') and verified_margin_mode not in {None, margin_mode}:
            raise RuntimeError(
                f'Margin mode mismatch on {self.info.id} for {market_symbol}: {verified_margin_mode}'
            )
        verified_leverage = verified_state['leverage']
        if self._supports('setLeverage') and verified_leverage not in {None, float(leverage)}:
            raise RuntimeError(
                f'Leverage mismatch on {self.info.id} for {market_symbol}: {verified_leverage}'
            )

    async def fetch_futures_positions(self, symbols: list[str]) -> dict[str, ExchangePosition]:
        if not self.info.supports_futures:
            return {}
        result: dict[str, ExchangePosition] = {}
        if not symbols:
            return result
        await self._ensure_markets_loaded()
        requests: list[tuple[str, str, dict]] = []
        for symbol in symbols:
            market_symbol, market = await self._get_market(symbol)
            subtype = 'linear' if market.get('linear') else 'inverse'
            requests.append((symbol, market_symbol, {'subType': subtype}))
        try:
            positions = await self._exchange.fetch_positions(
                [market_symbol for _, market_symbol, _ in requests],
                requests[0][2],
            )
            await self._collect_positions(result, positions, {market_symbol: symbol for symbol, market_symbol, _ in requests})
            return result
        except Exception:
            pass
        for symbol, market_symbol, params in requests:
            try:
                positions = await self._exchange.fetch_positions([market_symbol], params)
            except Exception:
                continue
            await self._collect_positions(result, positions, {market_symbol: symbol})
        return result

    async def _collect_positions(
        self,
        target: dict[str, ExchangePosition],
        positions: list[dict],
        requested_by_prepared: dict[str, str],
    ) -> None:
        for position in positions:
            contracts = float(position.get('contracts') or 0.0)
            if abs(contracts) <= 0:
                continue
            market_symbol = str(position.get('symbol') or '')
            requested_symbol = requested_by_prepared.get(market_symbol, market_symbol)
            base_amount = await self.convert_order_amount_to_base(requested_symbol, abs(contracts))
            side = str(position.get('side') or '').lower()
            target[requested_symbol] = ExchangePosition(
                symbol=requested_symbol,
                side=side,
                contracts=abs(contracts),
                base_amount=base_amount,
                entry_price=float(position.get('entryPrice') or 0.0),
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
            return market_symbol, await self._invoke_public_data_call(
                method_name,
                method,
                market_symbol,
                *args,
            )
        except Exception as exc:
            fallback_symbol = await self._resolve_symbol_from_exception(symbol, exc)
            if fallback_symbol == market_symbol:
                raise
            return fallback_symbol, await self._invoke_public_data_call(
                method_name,
                method,
                fallback_symbol,
                *args,
            )

    async def _prepare_symbol(self, symbol: str) -> str:
        if self._requires_market_bootstrap:
            await self._ensure_markets_loaded()
        return self._symbol_cache.get(symbol, symbol)

    async def _prepare_symbol_map(self, symbols: list[str]) -> dict[str, str]:
        requested_by_market: dict[str, str] = {}
        for symbol in symbols:
            market_symbol = await self._resolve_alias_symbol(symbol)
            requested_by_market[market_symbol] = symbol
        return requested_by_market

    async def _invoke_public_data_call(self, operation_name: str, operation, *args):
        try:
            return await operation(*args)
        except Exception as exc:
            if not self._is_retryable_public_data_error(exc):
                raise
            logger.warning(
                'retry_public_data_call exchange=%s operation=%s reason=%s',
                self.info.id,
                operation_name,
                self._describe_exception(exc),
            )
            await asyncio.sleep(0.25)
            return await operation(*args)

    async def _get_market(self, symbol: str) -> tuple[str, dict]:
        await self._ensure_markets_loaded()
        market_symbol = await self._resolve_alias_symbol(symbol)
        return market_symbol, self._exchange.market(market_symbol)

    async def _find_conversion_symbol(self, base_currency: str, quote_currency: str) -> str | None:
        await self._ensure_markets_loaded()
        preferred_symbol = None
        for market_symbol, market in self._exchange.markets.items():
            if market.get('base') != base_currency or market.get('quote') != quote_currency:
                continue
            if not bool(market.get('active', True)):
                continue
            if not bool(market.get('contract')):
                return market_symbol
            if preferred_symbol is None:
                preferred_symbol = market_symbol
        return preferred_symbol

    async def _read_futures_state(self, market_symbol: str, market: dict) -> dict[str, float | str | bool | None]:
        params = {'subType': 'linear' if market.get('linear') else 'inverse'}
        positions = await self._exchange.fetch_positions([market_symbol], params)
        has_open_position = False
        hedged = False
        leverage = None

        for position in positions:
            contracts = float(position.get('contracts') or 0.0)
            if abs(contracts) > 0:
                has_open_position = True
            if position.get('hedged') is True:
                hedged = True
            current_leverage = position.get('leverage')
            if current_leverage is not None:
                leverage = float(current_leverage)

        margin_mode = None
        if self._supports('fetchMarginMode'):
            margin = await self._exchange.fetch_margin_mode(market_symbol)
            margin_mode = margin.get('marginMode')

        return {
            'has_open_position': has_open_position,
            'hedged': hedged,
            'leverage': leverage,
            'margin_mode': margin_mode,
        }

    async def _fetch_funding_rate(self, market_symbol: str) -> dict | None:
        try:
            return await self._exchange.fetch_funding_rate(market_symbol)
        except Exception:
            return None

    async def _fetch_funding_rates(self, market_symbols: list[str]) -> dict[str, dict]:
        if not market_symbols:
            return {}
        if not self._supports('fetchFundingRates'):
            return {}
        fetch_funding_rates = getattr(self._exchange, 'fetch_funding_rates', None)
        if not callable(fetch_funding_rates):
            return {}
        try:
            raw = await fetch_funding_rates(market_symbols)
        except Exception:
            return {}
        funding_map: dict[str, dict] = {}
        if isinstance(raw, dict):
            for symbol_key, funding in raw.items():
                if isinstance(funding, dict):
                    funding_symbol = str(funding.get('symbol') or symbol_key)
                    funding_map[funding_symbol] = funding
            return funding_map
        if isinstance(raw, list):
            for funding in raw:
                if not isinstance(funding, dict):
                    continue
                funding_symbol = str(funding.get('symbol') or '')
                if funding_symbol:
                    funding_map[funding_symbol] = funding
        return funding_map

    def _build_ticker(self, requested_symbol: str, raw: dict) -> Ticker:
        return Ticker(
            symbol=requested_symbol,
            exchange_id=self.info.id,
            bid=raw.get('bid') or 0.0,
            ask=raw.get('ask') or 0.0,
            last=raw.get('last') or 0.0,
            volume=raw.get('baseVolume') or 0.0,
            timestamp=raw.get('timestamp') or 0,
        )

    def _build_futures_ticker(
        self,
        requested_symbol: str,
        market_symbol: str,
        raw: dict,
        funding: dict | None,
    ) -> FuturesTicker:
        funding_rate = 0.0
        next_funding = 0
        if funding:
            funding_rate = funding.get('fundingRate') or 0.0
            next_funding = self._parse_next_funding_time(funding)

        info = raw.get('info') or {}
        return FuturesTicker(
            symbol=requested_symbol,
            exchange_id=self.info.id,
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

    def _parse_next_funding_time(self, funding: dict) -> int:
        raw_next_funding = (
            funding.get('nextFundingTimestamp')
            or funding.get('nextFundingDatetime')
            or funding.get('nextFundingTime')
            or 0
        )
        if isinstance(raw_next_funding, str):
            return self._exchange.parse8601(raw_next_funding) or 0
        return int(raw_next_funding or 0)

    async def _apply_futures_setting(self, operation) -> None:
        try:
            await operation
        except Exception as exc:
            if self._is_not_modified_error(exc):
                return
            raise

    def _is_not_modified_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return 'not modified' in message or 'same to original' in message

    def _supports(self, capability: str) -> bool:
        value = getattr(self._exchange, 'has', {}).get(capability)
        return bool(value)

    def _symbol_to_requested(self, requested_symbol: str, market_symbol: str) -> str:
        cached = self._symbol_cache.get(requested_symbol)
        if cached == market_symbol:
            return requested_symbol
        for source_symbol, cached_symbol in self._symbol_cache.items():
            if cached_symbol == market_symbol:
                return source_symbol
        return requested_symbol

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
        resolved_market = None
        initial_market = self._exchange.markets.get(symbol)
        if initial_market and bool(initial_market.get('contract')) == self.info.supports_futures:
            resolved_market = initial_market
        for alias in SYMBOL_ALIASES.get(symbol, ()):
            alias_market = self._exchange.markets.get(alias)
            if alias_market is None:
                continue
            if bool(alias_market.get('contract')) != self.info.supports_futures:
                continue
            if self._is_preferred_market(alias_market, resolved_market):
                resolved = alias
                resolved_market = alias_market
        base, _, quote = symbol.partition('/')
        for market_symbol, market in self._exchange.markets.items():
            if market.get('base') != base or market.get('quote') != quote:
                continue
            if bool(market.get('contract')) != self.info.supports_futures:
                continue
            if self._is_preferred_market(market, resolved_market):
                resolved = market_symbol
                resolved_market = market

        self._symbol_cache[symbol] = resolved
        return resolved

    def _remember_symbol_alias(self, requested_symbol: str, market_symbol: str, market: dict) -> None:
        current_symbol = self._symbol_cache.get(requested_symbol)
        if current_symbol is None:
            self._symbol_cache[requested_symbol] = market_symbol
            return
        current_market = self._exchange.markets.get(current_symbol)
        if self._is_preferred_market(market, current_market):
            self._symbol_cache[requested_symbol] = market_symbol

    def _is_preferred_market(self, candidate: dict, current: dict | None) -> bool:
        if current is None:
            return True
        return self._market_priority(candidate) > self._market_priority(current)

    def _market_priority(self, market: dict) -> tuple[int, int, int, int, int]:
        if self.info.supports_futures:
            return (
                1 if bool(market.get('active', True)) else 0,
                1 if bool(market.get('swap')) else 0,
                1 if bool(market.get('linear')) else 0,
                1 if str(market.get('settle') or '').upper() == str(market.get('quote') or '').upper() else 0,
                -len(str(market.get('symbol') or '')),
            )
        return (
            1 if bool(market.get('active', True)) else 0,
            1 if bool(market.get('spot')) else 0,
            0,
            0,
            -len(str(market.get('symbol') or '')),
        )

    def _is_unknown_symbol_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return 'does not have market symbol' in message or 'badsymbol' in message

    def _is_retryable_public_data_error(self, exc: Exception) -> bool:
        if isinstance(exc, (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout)):
            return True
        message = str(exc).lower()
        return (
            'exchangenotavailable' in message
            or 'requesttimeout' in message
            or 'timeout' in message
            or 'networkerror' in message
            or 'connection reset' in message
            or 'temporarily unavailable' in message
        )

    def _describe_exception(self, exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return f'{type(exc).__name__}: {message}'
        return type(exc).__name__
