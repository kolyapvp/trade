from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ..domain.entities import (
    ArbitrageOpportunity, VirtualTrade, Portfolio,
    ClosedTradeAnalytics, CrossExchangeDetails, FuturesSpotPosition,
    FuturesSpotDetails, OpenPositionSnapshot, TriangularDetails,
    FuturesFundingDetails, FuturesFundingPosition,
)
from ..domain.ports import (
    IExchange,
    ExchangePosition,
    IOpenPositionSnapshotRepository,
    IOpenPositionStore,
    ITradeAnalyticsRepository,
    ITradeRepository,
    Ticker,
    FuturesTicker,
)
from ..domain.services import ArbitrageDetector
from ..domain.value_objects import Fee, OrderBook


logger = logging.getLogger(__name__)


@dataclass
class TriangularPathConfig:
    exchange: str
    pairs: list[str]
    coins: list[str]


@dataclass
class ScanConfig:
    symbols: list[str]
    position_size_usdt: float
    min_profit_percent: float
    triangular_paths: list[TriangularPathConfig]
    spot_scan_concurrency: int = 6
    futures_scan_concurrency: int = 4
    enable_cross_exchange: bool = True
    enable_triangular: bool = True
    enable_futures_spot: bool = True
    enable_futures_funding: bool = True
    futures_spot_long_only: bool = True


@dataclass
class ScanResult:
    opportunities: list[ArbitrageOpportunity]
    observed_opportunities: list[ArbitrageOpportunity]
    scanned_at: datetime
    duration_ms: int
    errors: list[str]
    spot_prices: dict[str, dict[str, float]] = field(default_factory=dict)
    futures_prices: dict[str, dict[str, float]] = field(default_factory=dict)
    futures_funding: dict[str, dict[str, float]] = field(default_factory=dict)


class ScanOpportunitiesUseCase:
    def __init__(self, spot_exchanges: list[IExchange], futures_exchanges: list[IExchange]):
        self._spot = spot_exchanges
        self._futures = futures_exchanges
        self._detector = ArbitrageDetector()
        self._fee_cache: dict[tuple[str, str], Fee] = {}

    async def execute(self, cfg: ScanConfig) -> ScanResult:
        start = datetime.now()
        opportunities: list[ArbitrageOpportunity] = []
        observed_opportunities: list[ArbitrageOpportunity] = []
        errors: list[str] = []
        exchange_data: list[dict] = []
        need_order_books = cfg.enable_cross_exchange
        spot_limit = max(cfg.spot_scan_concurrency, 1)
        futures_limit = max(cfg.futures_scan_concurrency, 1)

        async def load_exchange(exchange: IExchange) -> None:
            books: dict[str, OrderBook] = {}
            tickers: dict[str, Ticker] = {}
            try:
                fetched_tickers = await exchange.fetch_tickers(cfg.symbols)
                tickers.update({ticker.symbol: ticker for ticker in fetched_tickers})
            except Exception as e:
                errors.append(f'{exchange.info.id} tickers: {e}')

            missing_tickers = [symbol for symbol in cfg.symbols if symbol not in tickers]
            if missing_tickers:
                ticker_semaphore = asyncio.Semaphore(spot_limit)

                async def load_ticker(symbol: str) -> None:
                    async with ticker_semaphore:
                        try:
                            tickers[symbol] = await exchange.fetch_ticker(symbol)
                        except Exception as e:
                            errors.append(f'{exchange.info.id} ticker {symbol}: {e}')

                await asyncio.gather(*[load_ticker(symbol) for symbol in missing_tickers], return_exceptions=True)

            if need_order_books:
                book_semaphore = asyncio.Semaphore(spot_limit)

                async def load_book(symbol: str) -> None:
                    async with book_semaphore:
                        try:
                            books[symbol] = await exchange.fetch_order_book(symbol, 20)
                        except Exception as e:
                            errors.append(f'{exchange.info.id} book {symbol}: {e}')

                await asyncio.gather(*[load_book(symbol) for symbol in cfg.symbols], return_exceptions=True)

            if books or tickers:
                exchange_data.append({
                    'exchange_id': exchange.info.id,
                    'fee': exchange.info.fee,
                    'books': books,
                    'tickers': tickers,
                })

        await asyncio.gather(*[load_exchange(ex) for ex in self._spot], return_exceptions=True)
        spot_loaded_at = datetime.now()

        spot_prices: dict[str, dict[str, float]] = {
            d['exchange_id']: {s: t.last for s, t in d['tickers'].items()}
            for d in exchange_data
        }
        exchange_data_by_id = {data['exchange_id']: data for data in exchange_data}

        if cfg.enable_cross_exchange and len(exchange_data) >= 2:
            for symbol in cfg.symbols:
                found = self._detector.detect_cross_exchange(
                    exchange_data, symbol, cfg.position_size_usdt, cfg.min_profit_percent
                )
                opportunities.extend(found)
                observed_opportunities.extend(found)

        if cfg.enable_triangular:
            for ex_data in exchange_data:
                path_cfgs = [
                    {'exchange': p.exchange, 'pairs': p.pairs, 'coins': p.coins}
                    for p in cfg.triangular_paths
                ]
                found = self._detector.detect_triangular(
                    ex_data['exchange_id'],
                    ex_data['fee'],
                    ex_data['tickers'],
                    path_cfgs,
                    cfg.position_size_usdt,
                    cfg.min_profit_percent,
                )
                opportunities.extend(found)
                observed_opportunities.extend(found)
        spot_detected_at = datetime.now()

        futures_prices: dict[str, dict[str, float]] = {}
        futures_funding: dict[str, dict[str, float]] = {}
        futures_tickers_cache: dict[str, dict[str, FuturesTicker]] = {}
        spot_fee_cache: dict[tuple[str, str], Fee] = {}
        futures_fee_cache: dict[tuple[str, str], Fee] = {}

        if (cfg.enable_futures_spot or cfg.enable_futures_funding) and self._futures:
            async def load_futures_exchange(futures_ex: IExchange) -> None:
                cache: dict[str, FuturesTicker] = {}
                try:
                    fetched_tickers = await futures_ex.fetch_futures_tickers(cfg.symbols)
                    cache.update({ticker.symbol: ticker for ticker in fetched_tickers})
                except Exception as e:
                    errors.append(f'futures {futures_ex.info.id} tickers: {e}')

                missing_symbols = [symbol for symbol in cfg.symbols if symbol not in cache]
                if missing_symbols:
                    futures_semaphore = asyncio.Semaphore(futures_limit)

                    async def load_symbol(symbol: str) -> None:
                        async with futures_semaphore:
                            try:
                                ft = await futures_ex.fetch_futures_ticker(symbol)
                                if ft:
                                    cache[symbol] = ft
                            except Exception as e:
                                errors.append(f'futures {futures_ex.info.id} {symbol}: {e}')

                    await asyncio.gather(*[load_symbol(symbol) for symbol in missing_symbols], return_exceptions=True)
                if cache:
                    futures_tickers_cache[futures_ex.info.id] = cache
                    futures_prices[futures_ex.info.id] = {s: ft.last for s, ft in cache.items()}
                    futures_funding[futures_ex.info.id] = {s: ft.funding_rate for s, ft in cache.items()}

            await asyncio.gather(*[load_futures_exchange(ex) for ex in self._futures], return_exceptions=True)
            futures_loaded_at = datetime.now()

            async def load_fee_cache(
                exchange: IExchange,
                symbols: list[str],
                target: dict[tuple[str, str], Fee],
                prefix: str,
            ) -> None:
                fee_semaphore = asyncio.Semaphore(spot_limit)

                async def load_symbol_fee(symbol: str) -> None:
                    cache_key = (exchange.info.id, symbol)
                    cached_fee = self._fee_cache.get(cache_key)
                    if cached_fee is not None:
                        target[cache_key] = cached_fee
                        return
                    async with fee_semaphore:
                        try:
                            fee = await exchange.get_trading_fee(symbol)
                            target[cache_key] = fee
                            self._fee_cache[cache_key] = fee
                        except Exception as e:
                            errors.append(f'{prefix} {exchange.info.id} {symbol}: {e}')

                await asyncio.gather(*[load_symbol_fee(symbol) for symbol in symbols], return_exceptions=True)

            await asyncio.gather(
                *[
                    load_fee_cache(
                        spot_ex,
                        [symbol for symbol in cfg.symbols if symbol in exchange_data_by_id.get(spot_ex.info.id, {}).get('tickers', {})],
                        spot_fee_cache,
                        'spot-fee',
                    )
                    for spot_ex in self._spot
                    if spot_ex.info.id in exchange_data_by_id
                ],
                *[
                    load_fee_cache(
                        futures_ex,
                        list(futures_tickers_cache.get(futures_ex.info.id, {})),
                        futures_fee_cache,
                        'futures-fee',
                    )
                    for futures_ex in self._futures
                    if futures_ex.info.id in futures_tickers_cache
                ],
                return_exceptions=True,
            )
            fees_loaded_at = datetime.now()

            best_per_symbol: dict[str, ArbitrageOpportunity] = {}

            for spot_ex in self._spot:
                spot_data = exchange_data_by_id.get(spot_ex.info.id)
                if not spot_data:
                    continue

                for futures_ex in self._futures:
                    ftickers = futures_tickers_cache.get(futures_ex.info.id, {})

                    for symbol in cfg.symbols:
                        spot_ticker = spot_data['tickers'].get(symbol)
                        futures_ticker = ftickers.get(symbol)
                        if not spot_ticker or not futures_ticker:
                            continue
                        try:
                            spot_fee = spot_fee_cache.get((spot_ex.info.id, symbol), spot_ex.info.fee)
                            futures_fee = futures_fee_cache.get((futures_ex.info.id, symbol), futures_ex.info.fee)
                            opp = self._detector.detect_futures_spot(
                                spot_ex.info.id,
                                futures_ex.info.id,
                                symbol,
                                spot_ticker,
                                futures_ticker,
                                spot_fee,
                                futures_fee,
                                cfg.position_size_usdt,
                                cfg.min_profit_percent,
                                long_only=cfg.futures_spot_long_only,
                            )
                            if opp:
                                observed_opportunities.append(opp)
                                prev = best_per_symbol.get(symbol)
                                if prev is None or opp.profit_percent > prev.profit_percent:
                                    best_per_symbol[symbol] = opp
                        except Exception as e:
                            errors.append(f'futures-spot {spot_ex.info.id}×{futures_ex.info.id} {symbol}: {e}')

            opportunities.extend(best_per_symbol.values())

            if cfg.enable_futures_funding and len(futures_tickers_cache) >= 2:
                best_funding_per_symbol: dict[str, ArbitrageOpportunity] = {}
                futures_items = list(self._futures)
                for long_ex in futures_items:
                    long_tickers = futures_tickers_cache.get(long_ex.info.id, {})
                    if not long_tickers:
                        continue
                    for short_ex in futures_items:
                        if long_ex.info.id == short_ex.info.id:
                            continue
                        short_tickers = futures_tickers_cache.get(short_ex.info.id, {})
                        if not short_tickers:
                            continue
                        for symbol in cfg.symbols:
                            long_ticker = long_tickers.get(symbol)
                            short_ticker = short_tickers.get(symbol)
                            if not long_ticker or not short_ticker:
                                continue
                            try:
                                long_fee = futures_fee_cache.get((long_ex.info.id, symbol), long_ex.info.fee)
                                short_fee = futures_fee_cache.get((short_ex.info.id, symbol), short_ex.info.fee)
                                opp = self._detector.detect_futures_funding(
                                    long_ex.info.id,
                                    short_ex.info.id,
                                    symbol,
                                    long_ticker,
                                    short_ticker,
                                    long_fee,
                                    short_fee,
                                    cfg.position_size_usdt,
                                    cfg.min_profit_percent,
                                )
                                if opp:
                                    observed_opportunities.append(opp)
                                    prev = best_funding_per_symbol.get(symbol)
                                    if prev is None or opp.profit_percent > prev.profit_percent:
                                        best_funding_per_symbol[symbol] = opp
                            except Exception as e:
                                errors.append(f'futures-funding {long_ex.info.id}×{short_ex.info.id} {symbol}: {e}')

                opportunities.extend(best_funding_per_symbol.values())
            futures_detected_at = datetime.now()
        else:
            futures_loaded_at = spot_detected_at
            fees_loaded_at = futures_loaded_at
            futures_detected_at = fees_loaded_at

        opportunities.sort(key=lambda o: o.profit_percent, reverse=True)

        duration = int((datetime.now() - start).total_seconds() * 1000)
        logger.info(
            'scan_timing total_ms=%s spot_load_ms=%s spot_detect_ms=%s futures_load_ms=%s fee_load_ms=%s futures_detect_ms=%s opportunities=%s observed=%s errors=%s',
            duration,
            int((spot_loaded_at - start).total_seconds() * 1000),
            int((spot_detected_at - spot_loaded_at).total_seconds() * 1000),
            int((futures_loaded_at - spot_detected_at).total_seconds() * 1000),
            int((fees_loaded_at - futures_loaded_at).total_seconds() * 1000),
            int((futures_detected_at - fees_loaded_at).total_seconds() * 1000),
            len(opportunities),
            len(observed_opportunities),
            len(errors),
        )
        return ScanResult(
            opportunities=opportunities,
            observed_opportunities=observed_opportunities,
            scanned_at=datetime.now(),
            duration_ms=duration,
            errors=errors,
            spot_prices=spot_prices,
            futures_prices=futures_prices,
            futures_funding=futures_funding,
        )


class ExecuteDemoTradeUseCase:
    def __init__(
        self,
        repository: ITradeRepository,
        portfolio: Portfolio,
        analytics_repository: ITradeAnalyticsRepository,
        analytics_timezone: str,
    ):
        self._repo = repository
        self._portfolio = portfolio
        self._analytics = analytics_repository
        self._analytics_timezone = analytics_timezone

    async def execute(self, opp: ArbitrageOpportunity) -> VirtualTrade:
        trade = VirtualTrade(
            strategy=opp.strategy,
            symbol=opp.symbol,
            mode='demo',
            position_size_usdt=opp.position_size_usdt,
            expected_profit_usdt=opp.profit_usdt,
            expected_profit_percent=opp.profit_percent,
            details=opp.details,
        )
        await self._repo.save(trade)
        self._portfolio.add_trade(trade)

        slippage = 0.0002
        actual_profit = opp.profit_usdt * (1 - slippage)
        trade.close(actual_profit, 'Demo: closed with 0.02% slippage')
        await self._repo.save(trade)
        await self._analytics.record_closed_trade(
            build_closed_trade_analytics(trade, self._analytics_timezone)
        )
        return trade


class LiveExecutionError(RuntimeError):
    pass


class SafetyViolationError(LiveExecutionError):
    pass


class FuturesSpotPositionManager:
    def __init__(
        self,
        repository: ITradeRepository,
        portfolio: Portfolio,
        open_position_store: IOpenPositionStore,
        snapshot_repository: IOpenPositionSnapshotRepository,
        analytics_repository: ITradeAnalyticsRepository,
        analytics_timezone: str,
        trading_mode: str = 'demo',
        futures_leverage: int = 5,
        futures_margin_mode: str = 'isolated',
        spot_execution_exchanges: Optional[dict[str, IExchange]] = None,
        futures_execution_exchanges: Optional[dict[str, IExchange]] = None,
        max_close_failures: int = 10,
    ):
        self._repo = repository
        self._portfolio = portfolio
        self._open_position_store = open_position_store
        self._snapshot_repository = snapshot_repository
        self._analytics = analytics_repository
        self._analytics_timezone = analytics_timezone
        self._trading_mode = trading_mode
        self._futures_leverage = futures_leverage
        self._futures_margin_mode = futures_margin_mode
        self._positions: dict[str, FuturesSpotPosition | FuturesFundingPosition] = {}
        self._spot_execution_exchanges = spot_execution_exchanges or {}
        self._futures_execution_exchanges = futures_execution_exchanges or {}
        self._max_close_failures = max(max_close_failures, 1)
        self._close_failure_counts: dict[str, int] = {}
        self._execution_lock = asyncio.Lock()

    def has_open_position(self, symbol: str) -> bool:
        return symbol in self._positions

    async def open_position(self, opp: ArbitrageOpportunity) -> FuturesSpotPosition | FuturesFundingPosition:
        if opp.strategy == 'futures_funding':
            d = opp.details
            assert isinstance(d, FuturesFundingDetails)
            target_close_at = datetime.fromtimestamp(d.target_funding_time / 1000) if d.target_funding_time else None
            pos = FuturesFundingPosition(
                symbol=opp.symbol,
                long_exchange=d.long_exchange,
                short_exchange=d.short_exchange,
                entry_long_price=d.long_price,
                entry_short_price=d.short_price,
                long_funding_rate=d.long_funding_rate,
                short_funding_rate=d.short_funding_rate,
                position_usdt=opp.position_size_usdt,
                long_taker_fee=d.long_taker_fee,
                short_taker_fee=d.short_taker_fee,
                target_close_at=target_close_at,
            )
        else:
            d = opp.details
            assert isinstance(d, FuturesSpotDetails)
            pos = FuturesSpotPosition(
                symbol=opp.symbol,
                spot_exchange=d.spot_exchange,
                futures_exchange=d.futures_exchange,
                entry_spot_price=d.spot_price,
                entry_futures_price=d.futures_price,
                entry_basis_percent=d.basis_percent,
                funding_rate=d.funding_rate,
                position_usdt=opp.position_size_usdt,
                spot_taker_fee=d.spot_taker_fee,
                futures_taker_fee=d.futures_taker_fee,
            )
        self._positions[opp.symbol] = pos
        self._reset_close_failures(opp.symbol)
        await self._persist_position(pos)
        return pos

    async def open_live_position(self, opp: ArbitrageOpportunity) -> FuturesSpotPosition | FuturesFundingPosition:
        async with self._execution_lock:
            if opp.strategy == 'futures_funding':
                return await self._open_live_futures_funding_position(opp)
            return await self._open_live_futures_spot_position(opp)

    async def _open_live_futures_spot_position(self, opp: ArbitrageOpportunity) -> FuturesSpotPosition:
        d = opp.details
        assert isinstance(d, FuturesSpotDetails)
        spot_exchange = self._spot_execution_exchanges.get(d.spot_exchange)
        futures_exchange = self._futures_execution_exchanges.get(d.futures_exchange)
        if spot_exchange is None or futures_exchange is None:
            raise LiveExecutionError(
                f'Live execution unavailable for route {d.spot_exchange}->{d.futures_exchange}'
            )

        await self._prepare_futures_exchange(futures_exchange, opp.symbol)

        free_spot_usdt = await spot_exchange.fetch_free_balance('USDT')
        if free_spot_usdt < opp.position_size_usdt:
            raise LiveExecutionError(
                f'Insufficient USDT on {d.spot_exchange}: free={free_spot_usdt:.4f}, required={opp.position_size_usdt:.4f}'
            )
        required_margin = opp.position_size_usdt / max(self._futures_leverage, 1)
        free_futures_usdt = await futures_exchange.fetch_free_balance('USDT')
        if free_futures_usdt < required_margin:
            raise LiveExecutionError(
                f'Insufficient futures margin on {d.futures_exchange}: free={free_futures_usdt:.4f}, required={required_margin:.4f}'
            )

        target_spot_amount = await spot_exchange.normalize_order_amount(
            opp.symbol,
            opp.position_size_usdt / d.spot_price if d.spot_price > 0 else 0.0,
        )
        if target_spot_amount <= 0:
            raise LiveExecutionError(f'Cannot normalize spot amount for {opp.symbol} on {d.spot_exchange}')

        spot_order = None
        futures_order = None
        try:
            spot_order = await spot_exchange.create_market_order(opp.symbol, 'buy', target_spot_amount)
            if spot_order.base_amount <= 0:
                raise LiveExecutionError(f'Spot order filled zero quantity on {d.spot_exchange}')

            futures_amount = await futures_exchange.normalize_order_amount(opp.symbol, spot_order.base_amount)
            if futures_amount <= 0:
                raise LiveExecutionError(f'Cannot normalize futures amount for {opp.symbol} on {d.futures_exchange}')

            futures_order = await futures_exchange.create_market_order(opp.symbol, 'sell', futures_amount)
            if futures_order.base_amount <= 0:
                raise LiveExecutionError(f'Futures order filled zero quantity on {d.futures_exchange}')
        except Exception as exc:
            if spot_order is not None and futures_order is None:
                rolled_back = await self._rollback_spot_open(spot_exchange, opp.symbol, spot_order)
                if not rolled_back:
                    raise SafetyViolationError(
                        f'Critical rollback failure after spot open on {d.spot_exchange} for {opp.symbol}'
                    ) from exc
            elif spot_order is not None and futures_order is not None:
                futures_rolled_back = await self._rollback_futures_open(futures_exchange, opp.symbol, futures_order, 'buy')
                spot_rolled_back = await self._rollback_spot_open(spot_exchange, opp.symbol, spot_order)
                if not futures_rolled_back or not spot_rolled_back:
                    raise SafetyViolationError(
                        f'Critical rollback failure after partial futures-spot open for {opp.symbol}'
                    ) from exc
            raise LiveExecutionError(str(exc)) from exc

        pos = FuturesSpotPosition(
            symbol=opp.symbol,
            spot_exchange=d.spot_exchange,
            futures_exchange=d.futures_exchange,
            entry_spot_price=spot_order.average or d.spot_price,
            entry_futures_price=futures_order.average or d.futures_price,
            entry_basis_percent=d.basis_percent,
            funding_rate=d.funding_rate,
            position_usdt=spot_order.cost or opp.position_size_usdt,
            spot_taker_fee=d.spot_taker_fee,
            futures_taker_fee=d.futures_taker_fee,
            spot_base_quantity=spot_order.base_amount,
            futures_base_quantity=futures_order.base_amount,
            spot_order_amount=spot_order.base_amount,
            futures_order_amount=futures_order.filled or futures_order.amount,
        )
        self._positions[opp.symbol] = pos
        self._reset_close_failures(opp.symbol)
        await self._persist_position(pos)
        return pos

    async def _open_live_futures_funding_position(self, opp: ArbitrageOpportunity) -> FuturesFundingPosition:
        d = opp.details
        assert isinstance(d, FuturesFundingDetails)
        long_exchange = self._futures_execution_exchanges.get(d.long_exchange)
        short_exchange = self._futures_execution_exchanges.get(d.short_exchange)
        if long_exchange is None or short_exchange is None:
            raise LiveExecutionError(
                f'Live execution unavailable for route {d.long_exchange}->{d.short_exchange}'
            )

        await self._prepare_futures_exchange(long_exchange, opp.symbol)
        await self._prepare_futures_exchange(short_exchange, opp.symbol)

        required_margin = opp.position_size_usdt / max(self._futures_leverage, 1)
        free_long_usdt = await long_exchange.fetch_free_balance('USDT')
        if free_long_usdt < required_margin:
            raise LiveExecutionError(
                f'Insufficient futures margin on {d.long_exchange}: free={free_long_usdt:.4f}, required={required_margin:.4f}'
            )
        free_short_usdt = await short_exchange.fetch_free_balance('USDT')
        if free_short_usdt < required_margin:
            raise LiveExecutionError(
                f'Insufficient futures margin on {d.short_exchange}: free={free_short_usdt:.4f}, required={required_margin:.4f}'
            )

        target_long_amount = await long_exchange.normalize_order_amount(
            opp.symbol,
            opp.position_size_usdt / d.long_price if d.long_price > 0 else 0.0,
        )
        if target_long_amount <= 0:
            raise LiveExecutionError(f'Cannot normalize long amount for {opp.symbol} on {d.long_exchange}')

        long_order = None
        short_order = None
        try:
            long_order = await long_exchange.create_market_order(opp.symbol, 'buy', target_long_amount)
            if long_order.base_amount <= 0:
                raise LiveExecutionError(f'Long order filled zero quantity on {d.long_exchange}')

            short_amount = await short_exchange.normalize_order_amount(opp.symbol, long_order.base_amount)
            if short_amount <= 0:
                raise LiveExecutionError(f'Cannot normalize short amount for {opp.symbol} on {d.short_exchange}')

            short_order = await short_exchange.create_market_order(opp.symbol, 'sell', short_amount)
            if short_order.base_amount <= 0:
                raise LiveExecutionError(f'Short order filled zero quantity on {d.short_exchange}')
        except Exception as exc:
            if short_order is None and long_order is not None:
                rolled_back = await self._rollback_futures_open(long_exchange, opp.symbol, long_order, 'sell')
                if not rolled_back:
                    raise SafetyViolationError(
                        f'Critical rollback failure after long open on {d.long_exchange} for {opp.symbol}'
                    ) from exc
            elif long_order is not None and short_order is not None:
                short_rolled_back = await self._rollback_futures_open(short_exchange, opp.symbol, short_order, 'buy')
                long_rolled_back = await self._rollback_futures_open(long_exchange, opp.symbol, long_order, 'sell')
                if not short_rolled_back or not long_rolled_back:
                    raise SafetyViolationError(
                        f'Critical rollback failure after partial futures-funding open for {opp.symbol}'
                    ) from exc
            raise LiveExecutionError(str(exc)) from exc

        target_close_at = datetime.fromtimestamp(d.target_funding_time / 1000) if d.target_funding_time else None
        pos = FuturesFundingPosition(
            symbol=opp.symbol,
            long_exchange=d.long_exchange,
            short_exchange=d.short_exchange,
            entry_long_price=long_order.average or d.long_price,
            entry_short_price=short_order.average or d.short_price,
            long_funding_rate=d.long_funding_rate,
            short_funding_rate=d.short_funding_rate,
            position_usdt=opp.position_size_usdt,
            long_taker_fee=d.long_taker_fee,
            short_taker_fee=d.short_taker_fee,
            target_close_at=target_close_at,
            long_base_quantity=long_order.base_amount,
            short_base_quantity=short_order.base_amount,
            long_order_amount=long_order.filled or long_order.amount,
            short_order_amount=short_order.filled or short_order.amount,
        )
        self._positions[opp.symbol] = pos
        self._reset_close_failures(opp.symbol)
        await self._persist_position(pos)
        return pos

    async def check_and_close(
        self,
        spot_prices: dict[str, dict[str, float]],
        futures_prices: dict[str, dict[str, float]],
    ) -> list[tuple[FuturesSpotPosition | FuturesFundingPosition, VirtualTrade]]:
        results = []
        for symbol, pos in list(self._positions.items()):
            if isinstance(pos, FuturesFundingPosition):
                current_long = futures_prices.get(pos.long_exchange, {}).get(symbol)
                current_short = futures_prices.get(pos.short_exchange, {}).get(symbol)
                if current_long is None or current_short is None:
                    continue
                reason = self._funding_close_reason(pos)
                if not reason:
                    continue
                pos.close(current_long, current_short, reason)
                trade = self._build_futures_funding_trade(pos, reason)
            else:
                current_spot = spot_prices.get(pos.spot_exchange, {}).get(symbol)
                current_futures = futures_prices.get(pos.futures_exchange, {}).get(symbol)
                if current_spot is None or current_futures is None:
                    continue
                current_basis_pct = (
                    (current_futures - current_spot) / current_spot * 100
                ) if current_spot > 0 else 999.0
                reason = None
                if abs(current_basis_pct) < FuturesSpotPosition.CLOSE_THRESHOLD_PERCENT:
                    reason = f'Базис сошёлся к {current_basis_pct:.4f}%'
                elif pos.hours_open() >= FuturesSpotPosition.MAX_HOLD_HOURS:
                    reason = f'Таймаут {FuturesSpotPosition.MAX_HOLD_HOURS}ч'
                if not reason:
                    continue
                pos.close(current_spot, current_futures, reason)
                trade = self._build_futures_spot_trade(pos, reason)

            del self._positions[symbol]
            await self._open_position_store.delete(symbol)
            await self._snapshot_repository.delete(symbol)
            self._portfolio.add_trade(trade)
            await self._repo.save(trade)
            await self._analytics.record_closed_trade(
                build_closed_trade_analytics(trade, self._analytics_timezone)
            )
            results.append((pos, trade))

        return results

    async def check_and_close_live(
        self,
        spot_prices: dict[str, dict[str, float]],
        futures_prices: dict[str, dict[str, float]],
    ) -> list[tuple[FuturesSpotPosition | FuturesFundingPosition, VirtualTrade]]:
        async with self._execution_lock:
            results = []
            for symbol, pos in list(self._positions.items()):
                try:
                    if isinstance(pos, FuturesFundingPosition):
                        current_long = futures_prices.get(pos.long_exchange, {}).get(symbol)
                        current_short = futures_prices.get(pos.short_exchange, {}).get(symbol)
                        if current_long is None or current_short is None:
                            continue
                        reason = self._funding_close_reason(pos)
                        if not reason:
                            continue
                        long_exchange = self._futures_execution_exchanges.get(pos.long_exchange)
                        short_exchange = self._futures_execution_exchanges.get(pos.short_exchange)
                        if long_exchange is None or short_exchange is None:
                            raise LiveExecutionError(
                                f'Live close unavailable for route {pos.long_exchange}->{pos.short_exchange}'
                            )
                        short_close = None
                        try:
                            short_close = await short_exchange.create_market_order(
                                symbol,
                                'buy',
                                pos.short_order_amount,
                                reduce_only=True,
                            )
                        except Exception as exc:
                            if not self._is_futures_leg_absent_error(exc):
                                raise LiveExecutionError(str(exc)) from exc
                        try:
                            long_close = await long_exchange.create_market_order(
                                symbol,
                                'sell',
                                pos.long_order_amount,
                                reduce_only=True,
                            )
                        except Exception as exc:
                            if short_close is not None:
                                rolled_back = await self._rollback_futures_close(
                                    short_exchange,
                                    symbol,
                                    pos.short_order_amount,
                                )
                                if not rolled_back:
                                    raise SafetyViolationError(
                                        f'Critical rollback failure after partial futures-funding close for {symbol}'
                                    ) from exc
                            raise LiveExecutionError(str(exc)) from exc
                        pos.close(
                            long_close.average or current_long,
                            short_close.average if short_close and short_close.average else current_short,
                            reason,
                        )
                        trade = self._build_futures_funding_trade(pos, reason)
                    else:
                        current_spot = spot_prices.get(pos.spot_exchange, {}).get(symbol)
                        current_futures = futures_prices.get(pos.futures_exchange, {}).get(symbol)
                        if current_spot is None or current_futures is None:
                            continue
                        current_basis_pct = (
                            (current_futures - current_spot) / current_spot * 100
                        ) if current_spot > 0 else 999.0
                        reason = None
                        if abs(current_basis_pct) < FuturesSpotPosition.CLOSE_THRESHOLD_PERCENT:
                            reason = f'Базис сошёлся к {current_basis_pct:.4f}%'
                        elif pos.hours_open() >= FuturesSpotPosition.MAX_HOLD_HOURS:
                            reason = f'Таймаут {FuturesSpotPosition.MAX_HOLD_HOURS}ч'
                        if not reason:
                            continue

                        spot_exchange = self._spot_execution_exchanges.get(pos.spot_exchange)
                        futures_exchange = self._futures_execution_exchanges.get(pos.futures_exchange)
                        if spot_exchange is None or futures_exchange is None:
                            raise LiveExecutionError(
                                f'Live close unavailable for route {pos.spot_exchange}->{pos.futures_exchange}'
                            )

                        futures_close = None
                        try:
                            futures_close = await futures_exchange.create_market_order(
                                symbol,
                                'buy',
                                pos.futures_order_amount,
                                reduce_only=True,
                            )
                        except Exception as exc:
                            if not self._is_futures_leg_absent_error(exc):
                                raise LiveExecutionError(str(exc)) from exc
                        try:
                            spot_close = await spot_exchange.create_market_order(symbol, 'sell', pos.spot_order_amount)
                        except Exception as exc:
                            if futures_close is not None:
                                rolled_back = await self._rollback_futures_close(
                                    futures_exchange,
                                    symbol,
                                    pos.futures_order_amount,
                                )
                                if not rolled_back:
                                    raise SafetyViolationError(
                                        f'Critical rollback failure after partial futures-spot close for {symbol}'
                                    ) from exc
                            else:
                                raise SafetyViolationError(
                                    f'Critical close failure left naked spot on {pos.spot_exchange} for {symbol}'
                                ) from exc
                            raise LiveExecutionError(str(exc)) from exc

                        pos.close(
                            spot_close.average or current_spot,
                            futures_close.average or current_futures,
                            reason,
                        )
                        trade = self._build_futures_spot_trade(pos, reason)

                    await self._delete_position(symbol)
                    self._portfolio.add_trade(trade)
                    await self._repo.save(trade)
                    await self._analytics.record_closed_trade(
                        build_closed_trade_analytics(trade, self._analytics_timezone)
                    )
                    results.append((pos, trade))
                except SafetyViolationError:
                    raise
                except LiveExecutionError as exc:
                    self._raise_for_close_failure(symbol, exc)
                    raise
                except Exception as exc:
                    self._raise_for_close_failure(symbol, exc)
                    raise LiveExecutionError(str(exc)) from exc

            return results

    async def reconcile_live_state(
        self,
        tracked_symbols: list[str],
        spot_prices: dict[str, dict[str, float]],
        futures_prices: dict[str, dict[str, float]],
        orphan_notional_threshold_usdt: float,
    ) -> list[str]:
        async with self._execution_lock:
            issues: list[str] = []
            symbols = list(dict.fromkeys(tracked_symbols))
            symbol_by_base = {symbol.split('/')[0]: symbol for symbol in symbols}
            tracked_bases = list(symbol_by_base.keys())
            spot_balances_by_exchange: dict[str, dict[str, float]] = {}
            for exchange_id, exchange in self._spot_execution_exchanges.items():
                spot_balances_by_exchange[exchange_id] = await exchange.fetch_total_balances(tracked_bases)

            futures_positions_by_exchange: dict[str, dict[str, ExchangePosition]] = {}
            for exchange_id, exchange in self._futures_execution_exchanges.items():
                futures_positions_by_exchange[exchange_id] = await exchange.fetch_futures_positions(symbols)

            for symbol, position in list(self._positions.items()):
                if isinstance(position, FuturesFundingPosition):
                    long_actual = futures_positions_by_exchange.get(position.long_exchange, {}).get(symbol)
                    short_actual = futures_positions_by_exchange.get(position.short_exchange, {}).get(symbol)
                    long_amount = long_actual.base_amount if long_actual else 0.0
                    short_amount = short_actual.base_amount if short_actual else 0.0
                    long_flat = self._is_flat_quantity(
                        long_amount,
                        self._quantity_tolerance(position.long_order_amount),
                    )
                    short_flat = self._is_flat_quantity(
                        short_amount,
                        self._quantity_tolerance(position.short_order_amount),
                    )
                    if long_flat and short_flat:
                        await self._delete_position(symbol)
                        logger.warning(
                            'reconcile_removed_flat_position symbol=%s strategy=futures_funding route=%s->%s',
                            symbol,
                            position.long_exchange,
                            position.short_exchange,
                        )
                else:
                    base_currency = symbol.split('/')[0]
                    spot_balance = spot_balances_by_exchange.get(position.spot_exchange, {}).get(base_currency, 0.0)
                    futures_actual = futures_positions_by_exchange.get(position.futures_exchange, {}).get(symbol)
                    futures_amount = futures_actual.base_amount if futures_actual else 0.0
                    spot_flat = self._is_flat_quantity(
                        spot_balance,
                        self._quantity_tolerance(position.spot_order_amount),
                    )
                    futures_flat = self._is_flat_quantity(
                        futures_amount,
                        self._quantity_tolerance(position.futures_order_amount),
                    )
                    if spot_flat and futures_flat:
                        await self._delete_position(symbol)
                        logger.warning(
                            'reconcile_removed_flat_position symbol=%s strategy=futures_spot route=%s->%s',
                            symbol,
                            position.spot_exchange,
                            position.futures_exchange,
                        )

            expected_spot: dict[str, dict[str, FuturesSpotPosition]] = {}
            expected_futures: dict[str, dict[str, tuple[str, float]]] = {}
            for position in self._positions.values():
                if isinstance(position, FuturesFundingPosition):
                    expected_futures.setdefault(position.long_exchange, {})[position.symbol] = (
                        'long',
                        position.long_order_amount,
                    )
                    expected_futures.setdefault(position.short_exchange, {})[position.symbol] = (
                        'short',
                        position.short_order_amount,
                    )
                    continue
                expected_spot.setdefault(position.spot_exchange, {})[position.symbol] = position
                expected_futures.setdefault(position.futures_exchange, {})[position.symbol] = (
                    'short',
                    position.futures_order_amount,
                )

            for exchange_id, balances in spot_balances_by_exchange.items():
                expected_by_symbol = expected_spot.get(exchange_id, {})
                seen_symbols: set[str] = set()
                for base_currency, total_balance in balances.items():
                    symbol = symbol_by_base.get(base_currency)
                    if symbol is None:
                        continue
                    seen_symbols.add(symbol)
                    expected_position = expected_by_symbol.get(symbol)
                    price = self._lookup_price(symbol, exchange_id, spot_prices, futures_prices)
                    notional = total_balance * price if price > 0 else 0.0
                    if expected_position is None:
                        if notional >= orphan_notional_threshold_usdt:
                            issues.append(
                                f'Orphan spot balance on {exchange_id} for {symbol}: actual={total_balance:.8f}'
                            )
                        continue
                    tolerance = self._quantity_tolerance(expected_position.spot_order_amount)
                    if abs(total_balance - expected_position.spot_order_amount) > tolerance:
                        issues.append(
                            f'Spot balance mismatch on {exchange_id} for {symbol}: '
                            f'actual={total_balance:.8f} expected={expected_position.spot_order_amount:.8f}'
                        )
                for symbol, expected_position in expected_by_symbol.items():
                    if symbol in seen_symbols:
                        continue
                    issues.append(
                        f'Spot balance mismatch on {exchange_id} for {symbol}: '
                        f'actual={0.0:.8f} expected={expected_position.spot_order_amount:.8f}'
                    )

            for exchange_id, actual_positions in futures_positions_by_exchange.items():
                expected_positions = expected_futures.get(exchange_id, {})
                for symbol, (expected_side, expected_amount) in expected_positions.items():
                    actual = actual_positions.get(symbol)
                    if actual is None:
                        issues.append(
                            f'Missing futures position on {exchange_id} for {symbol}: '
                            f'expected_side={expected_side} expected={expected_amount:.8f}'
                        )
                        continue
                    tolerance = self._quantity_tolerance(expected_amount)
                    if actual.side != expected_side:
                        issues.append(
                            f'Futures side mismatch on {exchange_id} for {symbol}: '
                            f'actual={actual.side} expected={expected_side}'
                        )
                    if abs(actual.base_amount - expected_amount) > tolerance:
                        issues.append(
                            f'Futures size mismatch on {exchange_id} for {symbol}: '
                            f'actual={actual.base_amount:.8f} expected={expected_amount:.8f}'
                        )
                for symbol, actual in actual_positions.items():
                    if symbol in expected_positions:
                        continue
                    price = (
                        self._lookup_price(symbol, exchange_id, spot_prices, futures_prices)
                        or actual.entry_price
                        or 0.0
                    )
                    notional = actual.base_amount * price if price > 0 else 0.0
                    if notional >= orphan_notional_threshold_usdt:
                        issues.append(
                            f'Orphan futures position on {exchange_id} for {symbol}: '
                            f'side={actual.side} actual={actual.base_amount:.8f}'
                        )

            return issues

    async def restore_positions(
        self,
        snapshots: list[OpenPositionSnapshot],
    ) -> list[FuturesSpotPosition | FuturesFundingPosition]:
        restored: list[FuturesSpotPosition | FuturesFundingPosition] = []
        self._positions.clear()
        self._close_failure_counts.clear()
        for snapshot in snapshots:
            if snapshot.strategy == 'futures_funding':
                position = FuturesFundingPosition.from_snapshot(snapshot)
            else:
                position = FuturesSpotPosition.from_snapshot(snapshot)
            self._positions[position.symbol] = position
            self._reset_close_failures(position.symbol)
            await self._open_position_store.save(snapshot)
            restored.append(position)
        return restored

    async def flush_open_positions(self) -> None:
        snapshots = [position.to_snapshot() for position in self._positions.values()]
        await self._snapshot_repository.replace_all(snapshots)
        stored = {snapshot.symbol for snapshot in await self._open_position_store.get_all()}
        current = {snapshot.symbol for snapshot in snapshots}
        for snapshot in snapshots:
            await self._open_position_store.save(snapshot)
        for symbol in stored - current:
            await self._open_position_store.delete(symbol)

    async def _persist_position(self, position: FuturesSpotPosition | FuturesFundingPosition) -> None:
        snapshot = position.to_snapshot()
        await self._open_position_store.save(snapshot)
        await self._snapshot_repository.upsert(snapshot)

    async def _delete_position(self, symbol: str) -> None:
        self._positions.pop(symbol, None)
        self._reset_close_failures(symbol)
        await self._open_position_store.delete(symbol)
        await self._snapshot_repository.delete(symbol)

    def _is_futures_leg_absent_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return 'current position is zero' in message or 'cannot fix reduce-only' in message

    def _is_flat_quantity(self, amount: float, tolerance: float) -> bool:
        return abs(amount) <= tolerance

    def _record_close_failure(self, symbol: str) -> int:
        count = self._close_failure_counts.get(symbol, 0) + 1
        self._close_failure_counts[symbol] = count
        return count

    def _reset_close_failures(self, symbol: str) -> None:
        self._close_failure_counts.pop(symbol, None)

    def _raise_for_close_failure(self, symbol: str, exc: Exception) -> None:
        count = self._record_close_failure(symbol)
        logger.warning(
            'close_failure symbol=%s count=%s limit=%s error=%s',
            symbol,
            count,
            self._max_close_failures,
            exc,
        )
        if count >= self._max_close_failures:
            raise SafetyViolationError(
                f'Close failure limit reached for {symbol}: {count}/{self._max_close_failures}'
            ) from exc

    def _funding_close_reason(self, position: FuturesFundingPosition) -> Optional[str]:
        if position.target_close_at and datetime.now() >= position.target_close_at:
            return 'Фандинг получен, цикл завершён'
        if position.hours_open() >= FuturesFundingPosition.MAX_HOLD_HOURS:
            return f'Таймаут {FuturesFundingPosition.MAX_HOLD_HOURS}ч'
        return None

    def _build_futures_spot_trade(self, pos: FuturesSpotPosition, reason: str) -> VirtualTrade:
        entry_basis_profit = (
            pos.spot_base_quantity * max(pos.entry_futures_price - pos.entry_spot_price, 0.0)
        ) + (
            pos.futures_base_quantity * max(pos.entry_spot_price - pos.entry_futures_price, 0.0)
        )
        entry_fees = (pos.spot_taker_fee + pos.futures_taker_fee) * pos.position_usdt * 2
        expected_profit = entry_basis_profit + pos.position_usdt * pos.funding_rate - entry_fees
        expected_pct = (expected_profit / pos.position_usdt * 100) if pos.position_usdt > 0 else 0.0
        trade = VirtualTrade(
            strategy='futures_spot',
            symbol=pos.symbol,
            mode=self._trading_mode,
            position_size_usdt=pos.position_usdt,
            expected_profit_usdt=expected_profit,
            expected_profit_percent=expected_pct,
            details=FuturesSpotDetails(
                spot_exchange=pos.spot_exchange,
                futures_exchange=pos.futures_exchange,
                symbol=pos.symbol,
                spot_price=pos.exit_spot_price,
                futures_price=pos.exit_futures_price,
                funding_rate=pos.funding_rate,
                basis=pos.exit_futures_price - pos.exit_spot_price,
                basis_percent=pos.exit_basis_percent,
                spot_taker_fee=pos.spot_taker_fee,
                futures_taker_fee=pos.futures_taker_fee,
            ),
        )
        trade.close(pos.actual_profit_usdt or 0.0, reason)
        return trade

    def _build_futures_funding_trade(self, pos: FuturesFundingPosition, reason: str) -> VirtualTrade:
        expected_profit = pos.position_usdt * pos.funding_rate_delta - (
            (pos.long_taker_fee + pos.short_taker_fee) * pos.position_usdt * 2
        )
        expected_pct = (expected_profit / pos.position_usdt * 100) if pos.position_usdt > 0 else 0.0
        target_funding_time = int(pos.target_close_at.timestamp() * 1000) if pos.target_close_at else 0
        trade = VirtualTrade(
            strategy='futures_funding',
            symbol=pos.symbol,
            mode=self._trading_mode,
            position_size_usdt=pos.position_usdt,
            expected_profit_usdt=expected_profit,
            expected_profit_percent=expected_pct,
            details=FuturesFundingDetails(
                long_exchange=pos.long_exchange,
                short_exchange=pos.short_exchange,
                symbol=pos.symbol,
                long_price=pos.exit_long_price,
                short_price=pos.exit_short_price,
                long_funding_rate=pos.long_funding_rate,
                short_funding_rate=pos.short_funding_rate,
                funding_rate_delta=pos.funding_rate_delta,
                entry_spread_percent=pos.entry_spread_percent,
                exit_spread_percent=pos.exit_spread_percent,
                target_funding_time=target_funding_time,
                long_taker_fee=pos.long_taker_fee,
                short_taker_fee=pos.short_taker_fee,
            ),
        )
        trade.close(pos.actual_profit_usdt or 0.0, reason)
        return trade

    async def _prepare_futures_exchange(self, exchange: IExchange, symbol: str) -> None:
        if self._futures_margin_mode not in {'isolated', 'cross'}:
            raise LiveExecutionError(
                f'Unsupported futures margin mode: {self._futures_margin_mode}'
            )
        if self._futures_leverage < 1:
            raise LiveExecutionError(
                f'Unsupported futures leverage: {self._futures_leverage}'
            )
        try:
            await exchange.prepare_futures_execution(
                symbol=symbol,
                leverage=self._futures_leverage,
                margin_mode=self._futures_margin_mode,
                one_way=True,
            )
        except Exception as exc:
            raise LiveExecutionError(
                f'Futures market setup failed for {exchange.info.id} {symbol}: {exc}'
            ) from exc

    async def _rollback_spot_open(self, exchange: IExchange, symbol: str, order) -> bool:
        rollback_amount = order.base_amount or order.filled or order.amount
        if rollback_amount <= 0:
            return True
        for attempt in range(3):
            try:
                await exchange.create_market_order(symbol, 'sell', rollback_amount)
                return True
            except Exception:
                if attempt == 2:
                    return False
                await asyncio.sleep(1)

    async def _rollback_futures_open(self, exchange: IExchange, symbol: str, order, side: str) -> bool:
        rollback_amount = order.filled or order.amount
        if rollback_amount <= 0:
            return True
        for attempt in range(3):
            try:
                await exchange.create_market_order(symbol, side, rollback_amount, reduce_only=True)
                return True
            except Exception:
                if attempt == 2:
                    return False
                await asyncio.sleep(1)

    async def _rollback_futures_close(self, exchange: IExchange, symbol: str, amount: float) -> bool:
        if amount <= 0:
            return True
        try:
            await exchange.create_market_order(symbol, 'sell', amount)
            return True
        except Exception:
            return False

    def _quantity_tolerance(self, amount: float) -> float:
        return max(abs(amount) * 0.002, 1e-8)

    def _lookup_price(
        self,
        symbol: str,
        exchange_id: str,
        spot_prices: dict[str, dict[str, float]],
        futures_prices: dict[str, dict[str, float]],
    ) -> float:
        direct_spot = spot_prices.get(exchange_id, {}).get(symbol)
        if direct_spot:
            return direct_spot
        direct_futures = futures_prices.get(exchange_id, {}).get(symbol)
        if direct_futures:
            return direct_futures
        for exchange_prices in spot_prices.values():
            price = exchange_prices.get(symbol)
            if price:
                return price
        for exchange_prices in futures_prices.values():
            price = exchange_prices.get(symbol)
            if price:
                return price
        return 0.0

    @property
    def open_positions(self) -> list[FuturesSpotPosition | FuturesFundingPosition]:
        return list(self._positions.values())


def build_closed_trade_analytics(trade: VirtualTrade, analytics_timezone: str) -> ClosedTradeAnalytics:
    closed_at = trade.closed_at or datetime.now()
    closed_day = (
        closed_at
        .replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo(analytics_timezone))
        .date()
    )

    if trade.strategy == 'cross_exchange':
        details = trade.details
        assert isinstance(details, CrossExchangeDetails)
        return ClosedTradeAnalytics(
            trade_id=trade.id,
            closed_day=closed_day,
            mode=trade.mode,
            strategy=trade.strategy,
            route_type='cross_exchange',
            symbol=trade.symbol,
            position_usdt=trade.position_size_usdt,
            expected_profit_usdt=trade.expected_profit_usdt,
            expected_profit_percent=trade.expected_profit_percent,
            realized_profit_usdt=trade.actual_profit_usdt or 0.0,
            buy_exchange=details.buy_exchange,
            sell_exchange=details.sell_exchange,
            opened_at=trade.opened_at,
            closed_at=closed_at,
        )

    if trade.strategy == 'triangular':
        details = trade.details
        assert isinstance(details, TriangularDetails)
        return ClosedTradeAnalytics(
            trade_id=trade.id,
            closed_day=closed_day,
            mode=trade.mode,
            strategy=trade.strategy,
            route_type='single_exchange',
            symbol=trade.symbol,
            position_usdt=trade.position_size_usdt,
            expected_profit_usdt=trade.expected_profit_usdt,
            expected_profit_percent=trade.expected_profit_percent,
            realized_profit_usdt=trade.actual_profit_usdt or 0.0,
            exchange=details.exchange,
            opened_at=trade.opened_at,
            closed_at=closed_at,
        )

    if trade.strategy == 'futures_funding':
        details = trade.details
        assert isinstance(details, FuturesFundingDetails)
        return ClosedTradeAnalytics(
            trade_id=trade.id,
            closed_day=closed_day,
            mode=trade.mode,
            strategy=trade.strategy,
            route_type='cross_exchange',
            symbol=trade.symbol,
            position_usdt=trade.position_size_usdt,
            expected_profit_usdt=trade.expected_profit_usdt,
            expected_profit_percent=trade.expected_profit_percent,
            realized_profit_usdt=trade.actual_profit_usdt or 0.0,
            buy_exchange=details.long_exchange,
            sell_exchange=details.short_exchange,
            opened_at=trade.opened_at,
            closed_at=closed_at,
        )

    details = trade.details
    assert isinstance(details, FuturesSpotDetails)
    route_type = 'cross_exchange' if details.spot_exchange != details.futures_exchange else 'same_exchange'
    return ClosedTradeAnalytics(
        trade_id=trade.id,
        closed_day=closed_day,
        mode=trade.mode,
        strategy=trade.strategy,
        route_type=route_type,
        symbol=trade.symbol,
        position_usdt=trade.position_size_usdt,
        expected_profit_usdt=trade.expected_profit_usdt,
        expected_profit_percent=trade.expected_profit_percent,
        realized_profit_usdt=trade.actual_profit_usdt or 0.0,
        spot_exchange=details.spot_exchange,
        futures_exchange=details.futures_exchange,
        opened_at=trade.opened_at,
        closed_at=closed_at,
    )


@dataclass
class SessionStats:
    total_trades: int
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_profit_usdt: float
    total_expected_profit_usdt: float
    average_profit_percent: float
    roi: float
    profit_last_hour: float
    profit_last_24h: float
    by_strategy: dict[str, dict]
    best_trade: Optional[dict]
    worst_trade: Optional[dict]
    open_positions_count: int = 0


class GenerateReportUseCase:
    def __init__(self, repository: ITradeRepository, portfolio: Portfolio):
        self._repo = repository
        self._portfolio = portfolio
        self._position_manager: Optional[FuturesSpotPositionManager] = None

    def set_position_manager(self, pm: FuturesSpotPositionManager) -> None:
        self._position_manager = pm

    async def execute(self) -> SessionStats:
        closed = self._portfolio.closed_trades
        best = worst = None
        if closed:
            best_t = max(closed, key=lambda t: t.actual_profit_usdt or 0)
            worst_t = min(closed, key=lambda t: t.actual_profit_usdt or 0)
            best = {'symbol': best_t.symbol, 'profit': best_t.actual_profit_usdt or 0, 'strategy': best_t.strategy}
            worst = {'symbol': worst_t.symbol, 'profit': worst_t.actual_profit_usdt or 0, 'strategy': worst_t.strategy}

        open_count = len(self._position_manager.open_positions) if self._position_manager else 0

        return SessionStats(
            total_trades=self._portfolio.total_trades,
            closed_trades=len(closed),
            winning_trades=len(self._portfolio.winning_trades),
            losing_trades=len(self._portfolio.losing_trades),
            win_rate=self._portfolio.win_rate,
            total_profit_usdt=self._portfolio.total_profit_usdt,
            total_expected_profit_usdt=self._portfolio.total_expected_profit_usdt,
            average_profit_percent=self._portfolio.average_profit_percent,
            roi=self._portfolio.roi,
            profit_last_hour=self._portfolio.profit_last_hour(),
            profit_last_24h=self._portfolio.profit_last_24h(),
            by_strategy=self._portfolio.get_stats_by_strategy(),
            best_trade=best,
            worst_trade=worst,
            open_positions_count=open_count,
        )
