from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from .use_cases import (
    ScanOpportunitiesUseCase, ExecuteDemoTradeUseCase,
    GenerateReportUseCase, LiveExecutionError, SafetyViolationError, ScanConfig, FuturesSpotPositionManager,
)
from ..domain.entities import ArbitrageOpportunity, VirtualTrade, Portfolio, FuturesSpotPosition, FuturesFundingPosition
from ..domain.entities import CrossExchangeDetails, TriangularDetails, FuturesSpotDetails, FuturesFundingDetails
from ..domain.ports import (
    DeploymentState,
    IAlertService,
    IDeploymentStateRepository,
    IMetricsService,
    IExchange,
    ScanTelemetry,
    SignalTelemetry,
    TradeAlert,
    TradeTelemetry,
)

logger = logging.getLogger(__name__)


@dataclass
class BotStats:
    is_running: bool = False
    scan_count: int = 0
    last_scan_at: Optional[datetime] = None
    last_scan_duration_ms: int = 0
    total_opportunities_found: int = 0
    total_trades_executed: int = 0
    open_positions_count: int = 0
    deployment_status: str = 'active'
    errors: list[str] = field(default_factory=list)


class ArbitrageBotService:
    def __init__(
        self,
        scanner: ScanOpportunitiesUseCase,
        executor: ExecuteDemoTradeUseCase,
        reporter: GenerateReportUseCase,
        portfolio: Portfolio,
        scan_config: ScanConfig,
        mode: str,
        scan_interval_ms: int,
        position_manager: FuturesSpotPositionManager,
        metrics_service: IMetricsService,
        deployment_state_repository: IDeploymentStateRepository,
        alert_service: Optional[IAlertService] = None,
        live_spot_exchange_ids: Optional[set[str]] = None,
        live_futures_exchange_ids: Optional[set[str]] = None,
        balance_exchanges: Optional[dict[str, IExchange]] = None,
        max_open_positions: int = 1,
        live_reconcile_interval_seconds: int = 30,
        live_orphan_notional_threshold_usdt: float = 5.0,
    ):
        self._scanner = scanner
        self._executor = executor
        self._reporter = reporter
        self._portfolio = portfolio
        self._scan_config = scan_config
        self._mode = mode
        self._scan_interval_ms = scan_interval_ms
        self._position_manager = position_manager
        self._metrics = metrics_service
        self._deployment_state_repository = deployment_state_repository
        self._alert_service = alert_service
        self._live_spot_exchange_ids = live_spot_exchange_ids or set()
        self._live_futures_exchange_ids = live_futures_exchange_ids or set()
        self._balance_exchanges = balance_exchanges or {}
        self._max_open_positions = max(max_open_positions, 1)
        self._live_reconcile_interval_seconds = max(live_reconcile_interval_seconds, 5)
        self._live_orphan_notional_threshold_usdt = max(live_orphan_notional_threshold_usdt, 0.0)
        self._running = False
        self._stats = BotStats()
        self._on_opportunity: Optional[Callable] = None
        self._on_scan: Optional[Callable] = None
        self._on_error: Optional[Callable] = None
        self._on_position_closed: Optional[Callable] = None
        self._deployment_state = DeploymentState()
        self._last_balance_sync_at: Optional[datetime] = None
        self._last_reconcile_at: Optional[datetime] = None
        self._panic_reason: Optional[str] = None

    def set_opportunity_handler(self, handler: Callable) -> None:
        self._on_opportunity = handler

    def set_scan_handler(self, handler: Callable) -> None:
        self._on_scan = handler

    def set_error_handler(self, handler: Callable) -> None:
        self._on_error = handler

    def set_position_closed_handler(self, handler: Callable) -> None:
        self._on_position_closed = handler

    def get_stats(self) -> BotStats:
        return BotStats(
            is_running=self._stats.is_running,
            scan_count=self._stats.scan_count,
            last_scan_at=self._stats.last_scan_at,
            last_scan_duration_ms=self._stats.last_scan_duration_ms,
            total_opportunities_found=self._stats.total_opportunities_found,
            total_trades_executed=self._stats.total_trades_executed,
            open_positions_count=len(self._position_manager.open_positions),
            deployment_status=self._deployment_state.status,
            errors=list(self._stats.errors),
        )

    async def start(self) -> None:
        self._metrics.start()
        self._running = True
        self._stats.is_running = True
        self._metrics.set_bot_running(True)
        self._metrics.set_open_positions(len(self._position_manager.open_positions))
        while self._running:
            await self._run_cycle()
            if self._running:
                await asyncio.sleep(self._scan_interval_ms / 1000)

    def stop(self) -> None:
        self._running = False
        self._stats.is_running = False
        self._metrics.set_bot_running(False)

    async def get_report(self):
        return await self._reporter.execute()

    def _can_execute_live(self, opp: ArbitrageOpportunity) -> bool:
        if self._mode != 'real':
            return False
        if opp.strategy not in {'futures_spot', 'futures_funding'}:
            return False
        d = opp.details
        if isinstance(d, FuturesFundingDetails):
            return (
                d.long_exchange in self._live_futures_exchange_ids
                and d.short_exchange in self._live_futures_exchange_ids
            )
        assert isinstance(d, FuturesSpotDetails)
        return (
            d.spot_exchange in self._live_spot_exchange_ids
            and d.futures_exchange in self._live_futures_exchange_ids
        )

    def _build_signal_telemetry(self, opp: ArbitrageOpportunity) -> SignalTelemetry:
        if opp.strategy == 'cross_exchange':
            d = opp.details
            assert isinstance(d, CrossExchangeDetails)
            return SignalTelemetry(
                strategy=opp.strategy,
                symbol=opp.symbol,
                route_type='cross_exchange',
                expected_profit_usdt=opp.profit_usdt,
                expected_profit_percent=opp.profit_percent,
                position_usdt=opp.position_size_usdt,
                buy_exchange=d.buy_exchange,
                sell_exchange=d.sell_exchange,
            )

        if opp.strategy == 'triangular':
            d = opp.details
            assert isinstance(d, TriangularDetails)
            return SignalTelemetry(
                strategy=opp.strategy,
                symbol=opp.symbol,
                route_type='single_exchange',
                expected_profit_usdt=opp.profit_usdt,
                expected_profit_percent=opp.profit_percent,
                position_usdt=opp.position_size_usdt,
                exchange=d.exchange,
            )

        if opp.strategy == 'futures_funding':
            d = opp.details
            assert isinstance(d, FuturesFundingDetails)
            return SignalTelemetry(
                strategy=opp.strategy,
                symbol=opp.symbol,
                route_type='cross_exchange',
                expected_profit_usdt=opp.profit_usdt,
                expected_profit_percent=opp.profit_percent,
                position_usdt=opp.position_size_usdt,
                buy_exchange=d.long_exchange,
                sell_exchange=d.short_exchange,
            )

        d = opp.details
        assert isinstance(d, FuturesSpotDetails)
        route_type = 'cross_exchange' if d.spot_exchange != d.futures_exchange else 'same_exchange'
        return SignalTelemetry(
            strategy=opp.strategy,
            symbol=opp.symbol,
            route_type=route_type,
            expected_profit_usdt=opp.profit_usdt,
            expected_profit_percent=opp.profit_percent,
            position_usdt=opp.position_size_usdt,
            spot_exchange=d.spot_exchange,
            futures_exchange=d.futures_exchange,
        )

    def _build_trade_telemetry(
        self,
        strategy: str,
        symbol: str,
        expected_profit_usdt: float,
        expected_profit_percent: float,
        realized_profit_usdt: float,
        position_usdt: float,
        details: CrossExchangeDetails | TriangularDetails | FuturesSpotDetails | FuturesFundingDetails,
    ) -> TradeTelemetry:
        if strategy == 'cross_exchange':
            assert isinstance(details, CrossExchangeDetails)
            return TradeTelemetry(
                strategy=strategy,
                symbol=symbol,
                route_type='cross_exchange',
                expected_profit_usdt=expected_profit_usdt,
                expected_profit_percent=expected_profit_percent,
                realized_profit_usdt=realized_profit_usdt,
                position_usdt=position_usdt,
                buy_exchange=details.buy_exchange,
                sell_exchange=details.sell_exchange,
            )

        if strategy == 'triangular':
            assert isinstance(details, TriangularDetails)
            return TradeTelemetry(
                strategy=strategy,
                symbol=symbol,
                route_type='single_exchange',
                expected_profit_usdt=expected_profit_usdt,
                expected_profit_percent=expected_profit_percent,
                realized_profit_usdt=realized_profit_usdt,
                position_usdt=position_usdt,
                exchange=details.exchange,
            )

        if strategy == 'futures_funding':
            assert isinstance(details, FuturesFundingDetails)
            return TradeTelemetry(
                strategy=strategy,
                symbol=symbol,
                route_type='cross_exchange',
                expected_profit_usdt=expected_profit_usdt,
                expected_profit_percent=expected_profit_percent,
                realized_profit_usdt=realized_profit_usdt,
                position_usdt=position_usdt,
                buy_exchange=details.long_exchange,
                sell_exchange=details.short_exchange,
            )

        assert isinstance(details, FuturesSpotDetails)
        route_type = 'cross_exchange' if details.spot_exchange != details.futures_exchange else 'same_exchange'
        return TradeTelemetry(
            strategy=strategy,
            symbol=symbol,
            route_type=route_type,
            expected_profit_usdt=expected_profit_usdt,
            expected_profit_percent=expected_profit_percent,
            realized_profit_usdt=realized_profit_usdt,
            position_usdt=position_usdt,
            spot_exchange=details.spot_exchange,
            futures_exchange=details.futures_exchange,
        )

    def _build_alert_details(self, opp: ArbitrageOpportunity) -> str:
        if opp.strategy == 'cross_exchange':
            d = opp.details
            assert isinstance(d, CrossExchangeDetails)
            coin = d.symbol.split('/')[0]
            return (
                f'{d.buy_exchange} ask ${d.buy_price:.4f} → '
                f'{d.sell_exchange} bid ${d.sell_price:.4f} | '
                f'{d.max_qty:.6f} {coin}'
            )
        if opp.strategy == 'triangular':
            d = opp.details
            assert isinstance(d, TriangularDetails)
            return f'Путь: {" → ".join(d.path)} | ${d.start_amount:.2f} → ${d.end_amount:.2f}'
        if opp.strategy == 'futures_funding':
            d = opp.details
            assert isinstance(d, FuturesFundingDetails)
            return (
                f'LONG {d.long_exchange}: ${d.long_price:.4f} | '
                f'SHORT {d.short_exchange}: ${d.short_price:.4f} | '
                f'Фандинг: {(d.funding_rate_delta * 100):.4f}% | '
                f'Спред входа: {d.entry_spread_percent:.4f}%'
            )
        d = opp.details
        assert isinstance(d, FuturesSpotDetails)
        return (
            f'Спот: ${d.spot_price:.4f} | Фьюч: ${d.futures_price:.4f} | '
            f'Базис: {d.basis_percent:.4f}% | Ставка: {d.funding_rate * 100:.4f}%'
        )

    def _build_workflow(self, opp: ArbitrageOpportunity) -> list[str]:
        if opp.strategy == 'cross_exchange':
            d = opp.details
            assert isinstance(d, CrossExchangeDetails)
            coin = d.symbol.split('/')[0]
            qty = d.max_qty
            return [
                f'1️⃣ Купить <b>{qty:.6f} {coin}</b> на <b>{d.buy_exchange}</b> по ${d.buy_price:.4f}',
                f'   Затраты: ${qty * d.buy_price:.4f} + комиссия ${d.buy_fee:.4f}',
                f'2️⃣ Перевести {coin} на <b>{d.sell_exchange}</b>',
                f'3️⃣ Продать <b>{qty:.6f} {coin}</b> на <b>{d.sell_exchange}</b> по ${d.sell_price:.4f}',
                f'   Выручка: ${qty * d.sell_price:.4f} − комиссия ${d.sell_fee:.4f}',
            ]

        if opp.strategy == 'triangular':
            d = opp.details
            assert isinstance(d, TriangularDetails)
            steps = []
            for i, step in enumerate(d.path[:-1], 1):
                steps.append(f'{i}️⃣ {d.path[i-1]} → <b>{d.path[i]}</b>  (биржа: {d.exchange})')
            steps.append(f'✅ Итог: ${d.start_amount:.2f} → <b>${d.end_amount:.4f}</b> USDT')
            return steps

        if opp.strategy == 'futures_funding':
            d = opp.details
            assert isinstance(d, FuturesFundingDetails)
            target_suffix = ''
            if d.target_funding_time:
                target_suffix = f' около {datetime.fromtimestamp(d.target_funding_time / 1000).strftime("%H:%M")}'
            return [
                f'📌 <b>Арбитраж фандинга</b> 🔀 <b>кросс-биржа</b>',
                f'1️⃣ Открыть <b>LONG</b> на <b>{d.long_exchange}</b> по ${d.long_price:.4f}',
                f'2️⃣ Открыть <b>SHORT</b> на <b>{d.short_exchange}</b> по ${d.short_price:.4f}',
                f'3️⃣ Получить разницу фандинга: <b>{(d.funding_rate_delta * 100):+.4f}%</b>',
                f'4️⃣ Закрыть обе фьючерсные ноги после начисления{target_suffix}',
            ]

        d = opp.details
        assert isinstance(d, FuturesSpotDetails)
        coin = d.symbol.split('/')[0]
        rate_pct = d.funding_rate * 100
        rate_sign = '+' if rate_pct >= 0 else ''
        cross_label = ' 🔀 <b>кросс-биржа</b>' if d.spot_exchange != d.futures_exchange else ''

        if d.basis < 0:
            who_receives = 'лонги получают' if rate_pct < 0 else 'шорты получают'
            return [
                f'📌 <b>Обратный кэш-энд-кэрри</b> (фьюч дешевле спота){cross_label}',
                f'1️⃣ Продать спот <b>{coin}</b> на <b>{d.spot_exchange}</b> по ${d.spot_price:.4f}',
                f'2️⃣ Купить фьюч <b>{coin} LONG</b> на <b>{d.futures_exchange}</b> по ${d.futures_price:.4f}',
                f'3️⃣ Ставка фин-я: {rate_sign}{rate_pct:.4f}%/8ч → <b>{who_receives}</b>',
                f'4️⃣ Закрыть обе позиции при схождении базиса к 0',
            ]
        else:
            who_receives = 'шорты получают' if rate_pct > 0 else 'лонги получают'
            return [
                f'📌 <b>Кэш-энд-кэрри</b> (фьюч дороже спота){cross_label}',
                f'1️⃣ Купить спот <b>{coin}</b> на <b>{d.spot_exchange}</b> по ${d.spot_price:.4f}',
                f'2️⃣ Открыть фьюч <b>{coin} SHORT</b> на <b>{d.futures_exchange}</b> по ${d.futures_price:.4f}',
                f'3️⃣ Ставка фин-я: {rate_sign}{rate_pct:.4f}%/8ч → <b>{who_receives}</b>',
                f'4️⃣ Позиция будет закрыта ботом автоматически при схождении базиса',
            ]

    async def _refresh_deployment_state(self) -> DeploymentState:
        try:
            state = await self._deployment_state_repository.get_state()
        except Exception as exc:
            msg = f'Deployment state sync error: {exc}'
            logger.warning(msg)
            self._metrics.record_error('deployment')
            if self._on_error:
                self._on_error(msg)
            return self._deployment_state

        previous_status = self._deployment_state.status
        previous_target = self._deployment_state.target_sha
        self._deployment_state = state
        self._stats.deployment_status = state.status

        if state.status != previous_status or state.target_sha != previous_target:
            logger.info(
                'deployment_state status=%s target_sha=%s requested_by=%s requested_at=%s',
                state.status,
                state.target_sha or '-',
                state.requested_by or '-',
                state.requested_at.isoformat() if state.requested_at else '-',
            )
        return state

    async def _allows_new_trades(self) -> bool:
        state = await self._refresh_deployment_state()
        if self._panic_reason:
            return False
        if state.is_draining:
            return False
        return len(self._position_manager.open_positions) < self._max_open_positions

    def _activate_panic(self, reason: str) -> None:
        if self._panic_reason == reason:
            return
        if self._panic_reason is None:
            self._panic_reason = reason
        else:
            self._panic_reason = f'{self._panic_reason} | {reason}'
        logger.error('panic_mode activated reason=%s', reason)
        self._metrics.record_error('panic')
        self._stats.errors.append(f'PANIC: {reason}')
        self._stats.errors = self._stats.errors[-10:]
        if self._on_error:
            self._on_error(f'PANIC: {reason}')

    async def _maybe_reconcile_live_state(
        self,
        spot_prices: dict[str, dict[str, float]],
        futures_prices: dict[str, dict[str, float]],
    ) -> list[str]:
        if self._mode != 'real':
            return []
        now = datetime.now()
        if self._last_reconcile_at and (now - self._last_reconcile_at).total_seconds() < self._live_reconcile_interval_seconds:
            return []
        issues = await self._position_manager.reconcile_live_state(
            tracked_symbols=self._scan_config.symbols,
            spot_prices=spot_prices,
            futures_prices=futures_prices,
            orphan_notional_threshold_usdt=self._live_orphan_notional_threshold_usdt,
        )
        self._last_reconcile_at = now
        return issues

    async def _sync_balances(self) -> None:
        if self._mode != 'real' or not self._balance_exchanges:
            return
        now = datetime.now()
        if self._last_balance_sync_at and (now - self._last_balance_sync_at).total_seconds() < 30:
            return
        total_balance_usdt = 0.0
        successful = 0
        for exchange_id, exchange in self._balance_exchanges.items():
            try:
                exchange_balance = await exchange.fetch_total_balance_usdt()
            except Exception as exc:
                logger.warning('balance_sync_error exchange=%s error=%s', exchange_id, exc)
                self._metrics.record_error('balance', exchange=exchange_id)
                continue
            self._metrics.set_exchange_balance(exchange_id, exchange_balance)
            total_balance_usdt += exchange_balance
            successful += 1
        if successful > 0:
            self._metrics.set_total_balance(total_balance_usdt)
            self._last_balance_sync_at = now

    async def _run_cycle(self) -> None:
        try:
            await self._refresh_deployment_state()
            result = await self._scanner.execute(self._scan_config)
            self._stats.scan_count += 1
            self._stats.last_scan_at = result.scanned_at
            self._stats.last_scan_duration_ms = result.duration_ms
            self._stats.total_opportunities_found += len(result.observed_opportunities)
            self._metrics.record_scan(ScanTelemetry(
                scanned_at=result.scanned_at,
                duration_ms=result.duration_ms,
                opportunities_count=len(result.observed_opportunities),
                errors_count=len(result.errors),
            ))
            self._metrics.set_open_positions(len(self._position_manager.open_positions))
            await self._sync_balances()

            reconciliation_issues = await self._maybe_reconcile_live_state(
                result.spot_prices,
                result.futures_prices,
            )
            if reconciliation_issues:
                for issue in reconciliation_issues:
                    self._activate_panic(issue)
                return

            if self._panic_reason:
                return

            if result.errors:
                self._stats.errors = result.errors[-10:]
                for e in result.errors:
                    self._metrics.record_error('scan')
                    if self._on_error:
                        self._on_error(e)

            if self._on_scan:
                self._on_scan(result.observed_opportunities, result.duration_ms)

            if self._mode == 'real':
                closed_positions = await self._position_manager.check_and_close_live(
                    result.spot_prices, result.futures_prices
                )
            else:
                closed_positions = await self._position_manager.check_and_close(
                    result.spot_prices, result.futures_prices
                )
            for pos, trade in closed_positions:
                self._stats.total_trades_executed += 1
                self._metrics.record_trade(self._build_trade_telemetry(
                    strategy=trade.strategy,
                    symbol=trade.symbol,
                    expected_profit_usdt=trade.expected_profit_usdt,
                    expected_profit_percent=trade.expected_profit_percent,
                    realized_profit_usdt=trade.actual_profit_usdt or 0.0,
                    position_usdt=trade.position_size_usdt,
                    details=trade.details,
                ))
                self._metrics.set_open_positions(len(self._position_manager.open_positions))
                if self._on_position_closed:
                    self._on_position_closed(pos, trade)
                if self._alert_service:
                    if isinstance(pos, FuturesFundingPosition):
                        entry_spot_price = pos.entry_long_price
                        entry_futures_price = pos.entry_short_price
                        entry_basis_percent = pos.entry_spread_percent
                        exit_spot_price = pos.exit_long_price
                        exit_futures_price = pos.exit_short_price
                        exit_basis_percent = pos.exit_spread_percent
                    else:
                        entry_spot_price = pos.entry_spot_price
                        entry_futures_price = pos.entry_futures_price
                        entry_basis_percent = pos.entry_basis_percent
                        exit_spot_price = pos.exit_spot_price
                        exit_futures_price = pos.exit_futures_price
                        exit_basis_percent = pos.exit_basis_percent
                    alert = TradeAlert(
                        strategy=trade.strategy,
                        symbol=pos.symbol,
                        mode=self._mode,
                        profit_percent=trade.expected_profit_percent,
                        profit_usdt=trade.actual_profit_usdt or 0.0,
                        position_usdt=pos.position_usdt,
                        details='',
                        workflow=[],
                        profit_last_hour=self._portfolio.profit_last_hour(),
                        profit_last_24h=self._portfolio.profit_last_24h(),
                        timestamp=datetime.now(),
                        alert_type='closed',
                        hours_held=pos.hours_open(),
                        close_reason=pos.close_reason,
                        entry_spot_price=entry_spot_price,
                        entry_futures_price=entry_futures_price,
                        entry_basis_percent=entry_basis_percent,
                        exit_spot_price=exit_spot_price,
                        exit_futures_price=exit_futures_price,
                        exit_basis_percent=exit_basis_percent,
                    )
                    asyncio.create_task(self._alert_service.send_trade_alert(alert))

            for opp in result.observed_opportunities:
                if not opp.is_profitable(self._scan_config.min_profit_percent):
                    continue
                self._metrics.record_signal(self._build_signal_telemetry(opp))

            for opp in result.opportunities:
                if not opp.is_profitable(self._scan_config.min_profit_percent):
                    continue
                if not await self._allows_new_trades():
                    logger.info(
                        'deployment drain active, skip new trades open_positions=%s pending_opportunities=%s',
                        len(self._position_manager.open_positions),
                        len(result.opportunities),
                    )
                    break
                try:
                    if opp.strategy in {'futures_spot', 'futures_funding'}:
                        if self._position_manager.has_open_position(opp.symbol):
                            continue
                        if len(self._position_manager.open_positions) >= self._max_open_positions:
                            logger.info(
                                'max_open_positions reached open_positions=%s limit=%s skip_symbol=%s',
                                len(self._position_manager.open_positions),
                                self._max_open_positions,
                                opp.symbol,
                            )
                            break
                        if self._mode == 'demo':
                            await self._position_manager.open_position(opp)
                            self._metrics.set_open_positions(len(self._position_manager.open_positions))
                            if self._on_opportunity:
                                self._on_opportunity(opp, None)
                            if self._alert_service:
                                alert = TradeAlert(
                                    strategy=opp.strategy,
                                    symbol=opp.symbol,
                                    mode=self._mode,
                                    profit_percent=opp.profit_percent,
                                    profit_usdt=opp.profit_usdt,
                                    position_usdt=opp.position_size_usdt,
                                    details=self._build_alert_details(opp),
                                    workflow=self._build_workflow(opp),
                                    profit_last_hour=self._portfolio.profit_last_hour(),
                                    profit_last_24h=self._portfolio.profit_last_24h(),
                                    timestamp=datetime.now(),
                                    alert_type='opened',
                                )
                                asyncio.create_task(self._alert_service.send_trade_alert(alert))
                        elif self._can_execute_live(opp):
                            await self._position_manager.open_live_position(opp)
                            self._metrics.set_open_positions(len(self._position_manager.open_positions))
                            if self._on_opportunity:
                                self._on_opportunity(opp, None)
                            if self._alert_service:
                                alert = TradeAlert(
                                    strategy=opp.strategy,
                                    symbol=opp.symbol,
                                    mode=self._mode,
                                    profit_percent=opp.profit_percent,
                                    profit_usdt=opp.profit_usdt,
                                    position_usdt=opp.position_size_usdt,
                                    details=self._build_alert_details(opp),
                                    workflow=self._build_workflow(opp),
                                    profit_last_hour=self._portfolio.profit_last_hour(),
                                    profit_last_24h=self._portfolio.profit_last_24h(),
                                    timestamp=datetime.now(),
                                    alert_type='opened',
                                )
                                asyncio.create_task(self._alert_service.send_trade_alert(alert))
                    else:
                        if self._mode == 'demo':
                            trade = await self._executor.execute(opp)
                            self._stats.total_trades_executed += 1
                            self._metrics.record_trade(self._build_trade_telemetry(
                                strategy=trade.strategy,
                                symbol=trade.symbol,
                                expected_profit_usdt=trade.expected_profit_usdt,
                                expected_profit_percent=trade.expected_profit_percent,
                                realized_profit_usdt=trade.actual_profit_usdt or 0.0,
                                position_usdt=trade.position_size_usdt,
                                details=trade.details,
                            ))
                            if self._on_opportunity:
                                self._on_opportunity(opp, trade)
                            if self._alert_service:
                                alert = TradeAlert(
                                    strategy=opp.strategy,
                                    symbol=opp.symbol,
                                    mode=self._mode,
                                    profit_percent=opp.profit_percent,
                                    profit_usdt=trade.actual_profit_usdt or opp.profit_usdt,
                                    position_usdt=opp.position_size_usdt,
                                    details=self._build_alert_details(opp),
                                    workflow=self._build_workflow(opp),
                                    profit_last_hour=self._portfolio.profit_last_hour(),
                                    profit_last_24h=self._portfolio.profit_last_24h(),
                                    timestamp=datetime.now(),
                                    alert_type='opened',
                                )
                                asyncio.create_task(self._alert_service.send_trade_alert(alert))
                except LiveExecutionError as e:
                    msg = f'Live execution error: {e}'
                    self._stats.errors.append(msg)
                    self._stats.errors = self._stats.errors[-10:]
                    logger.exception(msg)
                    self._metrics.record_error('live_execution')
                    if isinstance(e, SafetyViolationError):
                        self._activate_panic(str(e))
                        if self._on_error:
                            self._on_error(msg)
                        break
                    if self._on_error:
                        self._on_error(msg)
                    continue

        except LiveExecutionError as e:
            msg = f'Live execution error: {e}'
            self._stats.errors.append(msg)
            logger.exception(msg)
            self._metrics.record_error('live_execution')
            if self._on_error:
                self._on_error(msg)
        except Exception as e:
            msg = f'Bot cycle error: {e}'
            self._stats.errors.append(msg)
            logger.exception(msg)
            self._metrics.record_error('run_cycle')
            if self._on_error:
                self._on_error(msg)
