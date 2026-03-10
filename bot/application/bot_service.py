from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from .use_cases import ScanOpportunitiesUseCase, ExecuteDemoTradeUseCase, GenerateReportUseCase, ScanConfig
from ..domain.entities import ArbitrageOpportunity, VirtualTrade, Portfolio
from ..domain.entities import CrossExchangeDetails, TriangularDetails, FuturesSpotDetails
from ..domain.ports import IAlertService, TradeAlert


@dataclass
class BotStats:
    is_running: bool = False
    scan_count: int = 0
    last_scan_at: Optional[datetime] = None
    last_scan_duration_ms: int = 0
    total_opportunities_found: int = 0
    total_trades_executed: int = 0
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
        alert_service: Optional[IAlertService] = None,
    ):
        self._scanner = scanner
        self._executor = executor
        self._reporter = reporter
        self._portfolio = portfolio
        self._scan_config = scan_config
        self._mode = mode
        self._scan_interval_ms = scan_interval_ms
        self._alert_service = alert_service
        self._running = False
        self._stats = BotStats()
        self._on_opportunity: Optional[Callable] = None
        self._on_scan: Optional[Callable] = None
        self._on_error: Optional[Callable] = None

    def set_opportunity_handler(self, handler: Callable) -> None:
        self._on_opportunity = handler

    def set_scan_handler(self, handler: Callable) -> None:
        self._on_scan = handler

    def set_error_handler(self, handler: Callable) -> None:
        self._on_error = handler

    def get_stats(self) -> BotStats:
        return BotStats(
            is_running=self._stats.is_running,
            scan_count=self._stats.scan_count,
            last_scan_at=self._stats.last_scan_at,
            last_scan_duration_ms=self._stats.last_scan_duration_ms,
            total_opportunities_found=self._stats.total_opportunities_found,
            total_trades_executed=self._stats.total_trades_executed,
            errors=list(self._stats.errors),
        )

    async def start(self) -> None:
        self._running = True
        self._stats.is_running = True
        while self._running:
            await self._run_cycle()
            if self._running:
                await asyncio.sleep(self._scan_interval_ms / 1000)

    def stop(self) -> None:
        self._running = False
        self._stats.is_running = False

    async def get_report(self):
        return await self._reporter.execute()

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

        d = opp.details
        assert isinstance(d, FuturesSpotDetails)
        coin = d.symbol.split('/')[0]
        rate_pct = d.funding_rate * 100
        rate_sign = '+' if rate_pct >= 0 else ''

        if d.basis < 0:
            direction = 'Обратный кэш-энд-кэрри'
            who_receives = 'лонги получают' if rate_pct < 0 else 'шорты получают'
            return [
                f'📌 <b>{direction}</b> (фьюч дешевле спота)',
                f'1️⃣ Продать спот <b>{coin}</b> по <b>${d.spot_price:.4f}</b>',
                f'2️⃣ Купить фьюч <b>{coin} LONG</b> по <b>${d.futures_price:.4f}</b>',
                f'3️⃣ Ставка фин-я: {rate_sign}{rate_pct:.4f}%/8ч → <b>{who_receives}</b>',
                f'4️⃣ Закрыть обе позиции при схождении базиса к 0',
            ]
        else:
            direction = 'Кэш-энд-кэрри'
            who_receives = 'шорты получают' if rate_pct > 0 else 'лонги получают'
            return [
                f'📌 <b>{direction}</b> (фьюч дороже спота)',
                f'1️⃣ Купить спот <b>{coin}</b> по <b>${d.spot_price:.4f}</b>',
                f'2️⃣ Открыть фьюч <b>{coin} SHORT</b> по <b>${d.futures_price:.4f}</b>',
                f'3️⃣ Ставка фин-я: {rate_sign}{rate_pct:.4f}%/8ч → <b>{who_receives}</b>',
                f'4️⃣ Закрыть обе позиции при схождении базиса к 0',
            ]

    async def _run_cycle(self) -> None:
        try:
            result = await self._scanner.execute(self._scan_config)
            self._stats.scan_count += 1
            self._stats.last_scan_at = result.scanned_at
            self._stats.last_scan_duration_ms = result.duration_ms
            self._stats.total_opportunities_found += len(result.opportunities)

            if result.errors:
                self._stats.errors = result.errors[-10:]
                for e in result.errors:
                    if self._on_error:
                        self._on_error(e)

            if self._on_scan:
                self._on_scan(result.opportunities, result.duration_ms)

            for opp in result.opportunities:
                if not opp.is_profitable(self._scan_config.min_profit_percent):
                    continue
                if self._mode == 'demo':
                    trade = await self._executor.execute(opp)
                    self._stats.total_trades_executed += 1
                    if self._on_opportunity:
                        self._on_opportunity(opp, trade)

                    if self._alert_service:
                        alert = TradeAlert(
                            strategy=opp.strategy,
                            symbol=opp.symbol,
                            profit_percent=opp.profit_percent,
                            profit_usdt=trade.actual_profit_usdt or opp.profit_usdt,
                            position_usdt=opp.position_size_usdt,
                            details=self._build_alert_details(opp),
                            workflow=self._build_workflow(opp),
                            profit_last_hour=self._portfolio.profit_last_hour(),
                            profit_last_24h=self._portfolio.profit_last_24h(),
                            timestamp=datetime.now(),
                        )
                        asyncio.create_task(self._alert_service.send_trade_alert(alert))

        except Exception as e:
            msg = f'Bot cycle error: {e}'
            self._stats.errors.append(msg)
            if self._on_error:
                self._on_error(msg)
