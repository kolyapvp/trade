from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ..domain.entities import (
    ArbitrageOpportunity, VirtualTrade, Portfolio,
    ClosedTradeAnalytics, CrossExchangeDetails, FuturesSpotPosition,
    FuturesSpotDetails, OpenPositionSnapshot, TriangularDetails,
)
from ..domain.ports import (
    IExchange,
    IOpenPositionSnapshotRepository,
    IOpenPositionStore,
    ITradeAnalyticsRepository,
    ITradeRepository,
    Ticker,
    FuturesTicker,
)
from ..domain.services import ArbitrageDetector
from ..domain.value_objects import OrderBook


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
    enable_cross_exchange: bool = True
    enable_triangular: bool = True
    enable_futures_spot: bool = True
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

    async def execute(self, cfg: ScanConfig) -> ScanResult:
        start = datetime.now()
        opportunities: list[ArbitrageOpportunity] = []
        observed_opportunities: list[ArbitrageOpportunity] = []
        errors: list[str] = []
        exchange_data: list[dict] = []

        async def load_exchange(exchange: IExchange) -> None:
            books: dict[str, OrderBook] = {}
            tickers: dict[str, Ticker] = {}

            async def load_symbol(symbol: str) -> None:
                try:
                    book, ticker = await asyncio.gather(
                        exchange.fetch_order_book(symbol, 20),
                        exchange.fetch_ticker(symbol),
                    )
                    books[symbol] = book
                    tickers[symbol] = ticker
                except Exception as e:
                    errors.append(f'{exchange.info.id} {symbol}: {e}')

            await asyncio.gather(*[load_symbol(s) for s in cfg.symbols], return_exceptions=True)

            if books:
                exchange_data.append({
                    'exchange_id': exchange.info.id,
                    'fee': exchange.info.fee,
                    'books': books,
                    'tickers': tickers,
                })

        await asyncio.gather(*[load_exchange(ex) for ex in self._spot], return_exceptions=True)

        spot_prices: dict[str, dict[str, float]] = {
            d['exchange_id']: {s: t.last for s, t in d['tickers'].items()}
            for d in exchange_data
        }

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

        futures_prices: dict[str, dict[str, float]] = {}
        futures_funding: dict[str, dict[str, float]] = {}

        if cfg.enable_futures_spot and self._futures:
            futures_tickers_cache: dict[str, dict[str, FuturesTicker]] = {}
            for futures_ex in self._futures:
                cache: dict[str, FuturesTicker] = {}
                for symbol in cfg.symbols:
                    try:
                        ft = await futures_ex.fetch_futures_ticker(symbol)
                        if ft:
                            cache[symbol] = ft
                    except Exception as e:
                        errors.append(f'futures {futures_ex.info.id} {symbol}: {e}')
                if cache:
                    futures_tickers_cache[futures_ex.info.id] = cache
                    futures_prices[futures_ex.info.id] = {s: ft.last for s, ft in cache.items()}
                    futures_funding[futures_ex.info.id] = {s: ft.funding_rate for s, ft in cache.items()}

            best_per_symbol: dict[str, ArbitrageOpportunity] = {}

            for spot_ex in self._spot:
                spot_data = next((d for d in exchange_data if d['exchange_id'] == spot_ex.info.id), None)
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
                            opp = self._detector.detect_futures_spot(
                                spot_ex.info.id,
                                futures_ex.info.id,
                                symbol,
                                spot_ticker,
                                futures_ticker,
                                spot_ex.info.fee,
                                futures_ex.info.fee,
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

        opportunities.sort(key=lambda o: o.profit_percent, reverse=True)

        duration = int((datetime.now() - start).total_seconds() * 1000)
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


class FuturesSpotPositionManager:
    def __init__(
        self,
        repository: ITradeRepository,
        portfolio: Portfolio,
        open_position_store: IOpenPositionStore,
        snapshot_repository: IOpenPositionSnapshotRepository,
        analytics_repository: ITradeAnalyticsRepository,
        analytics_timezone: str,
        spot_execution_exchanges: Optional[dict[str, IExchange]] = None,
        futures_execution_exchanges: Optional[dict[str, IExchange]] = None,
    ):
        self._repo = repository
        self._portfolio = portfolio
        self._open_position_store = open_position_store
        self._snapshot_repository = snapshot_repository
        self._analytics = analytics_repository
        self._analytics_timezone = analytics_timezone
        self._positions: dict[str, FuturesSpotPosition] = {}
        self._spot_execution_exchanges = spot_execution_exchanges or {}
        self._futures_execution_exchanges = futures_execution_exchanges or {}

    def has_open_position(self, symbol: str) -> bool:
        return symbol in self._positions

    async def open_position(self, opp: ArbitrageOpportunity) -> FuturesSpotPosition:
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
        await self._persist_position(pos)
        return pos

    async def open_live_position(self, opp: ArbitrageOpportunity) -> FuturesSpotPosition:
        d = opp.details
        assert isinstance(d, FuturesSpotDetails)
        spot_exchange = self._spot_execution_exchanges.get(d.spot_exchange)
        futures_exchange = self._futures_execution_exchanges.get(d.futures_exchange)
        if spot_exchange is None or futures_exchange is None:
            raise LiveExecutionError(
                f'Live execution unavailable for route {d.spot_exchange}->{d.futures_exchange}'
            )

        free_spot_usdt = await spot_exchange.fetch_free_balance('USDT')
        if free_spot_usdt < opp.position_size_usdt:
            raise LiveExecutionError(
                f'Insufficient USDT on {d.spot_exchange}: free={free_spot_usdt:.4f}, required={opp.position_size_usdt:.4f}'
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
            if futures_order is None and spot_order is not None:
                await self._rollback_spot_open(spot_exchange, opp.symbol, spot_order)
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
            spot_order_amount=spot_order.filled or spot_order.amount,
            futures_order_amount=futures_order.filled or futures_order.amount,
        )
        self._positions[opp.symbol] = pos
        await self._persist_position(pos)
        return pos

    async def check_and_close(
        self,
        spot_prices: dict[str, dict[str, float]],
        futures_prices: dict[str, dict[str, float]],
    ) -> list[tuple[FuturesSpotPosition, VirtualTrade]]:
        results = []
        for symbol, pos in list(self._positions.items()):
            current_spot = spot_prices.get(pos.spot_exchange, {}).get(symbol)
            current_futures = futures_prices.get(pos.futures_exchange, {}).get(symbol)
            if current_spot is None or current_futures is None:
                continue

            current_basis_pct = (
                (current_futures - current_spot) / current_spot * 100
            ) if current_spot > 0 else 999.0

            reason: Optional[str] = None
            if abs(current_basis_pct) < FuturesSpotPosition.CLOSE_THRESHOLD_PERCENT:
                reason = f'Базис сошёлся к {current_basis_pct:.4f}%'
            elif pos.hours_open() >= FuturesSpotPosition.MAX_HOLD_HOURS:
                reason = f'Таймаут {FuturesSpotPosition.MAX_HOLD_HOURS}ч'

            if reason:
                pos.close(current_spot, current_futures, reason)
                del self._positions[symbol]
                await self._open_position_store.delete(symbol)
                await self._snapshot_repository.delete(symbol)

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
    ) -> list[tuple[FuturesSpotPosition, VirtualTrade]]:
        results = []
        for symbol, pos in list(self._positions.items()):
            current_spot = spot_prices.get(pos.spot_exchange, {}).get(symbol)
            current_futures = futures_prices.get(pos.futures_exchange, {}).get(symbol)
            if current_spot is None or current_futures is None:
                continue

            current_basis_pct = (
                (current_futures - current_spot) / current_spot * 100
            ) if current_spot > 0 else 999.0

            reason: Optional[str] = None
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

            futures_close = await futures_exchange.create_market_order(
                symbol,
                'buy',
                pos.futures_order_amount,
                reduce_only=True,
            )
            try:
                spot_close = await spot_exchange.create_market_order(symbol, 'sell', pos.spot_order_amount)
            except Exception as exc:
                await self._rollback_futures_close(futures_exchange, symbol, pos.futures_order_amount)
                raise LiveExecutionError(str(exc)) from exc

            pos.close(
                spot_close.average or current_spot,
                futures_close.average or current_futures,
                reason,
            )
            del self._positions[symbol]
            await self._open_position_store.delete(symbol)
            await self._snapshot_repository.delete(symbol)

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
            self._portfolio.add_trade(trade)
            await self._repo.save(trade)
            await self._analytics.record_closed_trade(
                build_closed_trade_analytics(trade, self._analytics_timezone)
            )
            results.append((pos, trade))

        return results

    async def restore_positions(self, snapshots: list[OpenPositionSnapshot]) -> list[FuturesSpotPosition]:
        restored: list[FuturesSpotPosition] = []
        self._positions.clear()
        for snapshot in snapshots:
            position = FuturesSpotPosition.from_snapshot(snapshot)
            self._positions[position.symbol] = position
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

    async def _persist_position(self, position: FuturesSpotPosition) -> None:
        snapshot = position.to_snapshot()
        await self._open_position_store.save(snapshot)
        await self._snapshot_repository.upsert(snapshot)

    async def _rollback_spot_open(self, exchange: IExchange, symbol: str, order) -> None:
        rollback_amount = order.filled or order.amount
        if rollback_amount <= 0:
            return
        try:
            await exchange.create_market_order(symbol, 'sell', rollback_amount)
        except Exception:
            pass

    async def _rollback_futures_close(self, exchange: IExchange, symbol: str, amount: float) -> None:
        if amount <= 0:
            return
        try:
            await exchange.create_market_order(symbol, 'sell', amount)
        except Exception:
            pass

    @property
    def open_positions(self) -> list[FuturesSpotPosition]:
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

    details = trade.details
    assert isinstance(details, FuturesSpotDetails)
    route_type = 'cross_exchange' if details.spot_exchange != details.futures_exchange else 'same_exchange'
    return ClosedTradeAnalytics(
        trade_id=trade.id,
        closed_day=closed_day,
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
