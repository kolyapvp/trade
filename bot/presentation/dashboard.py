from __future__ import annotations

from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.columns import Columns

from ..domain.entities import ArbitrageOpportunity, VirtualTrade, CrossExchangeDetails, TriangularDetails, FuturesSpotDetails, FuturesSpotPosition
from ..application.bot_service import BotStats
from ..application.use_cases import SessionStats

console = Console()


class Dashboard:
    def __init__(self):
        self._start_time = datetime.now()

    def print_header(self, mode: str) -> None:
        console.clear()
        mode_text = Text()
        if mode == 'demo':
            mode_text.append('● DEMO MODE (виртуальные сделки)', style='bold yellow')
        else:
            mode_text.append('● LIVE MODE (реальные сделки)', style='bold red')

        console.print(Panel(
            f'[bold cyan]CRYPTO ARBITRAGE BOT[/bold cyan]   {mode_text}\n'
            f'[dim]Запущен: {self._start_time.strftime("%d.%m.%Y %H:%M:%S")}[/dim]',
            border_style='cyan',
        ))

    def print_bot_stats(self, stats: BotStats, profit_hour: float, profit_24h: float) -> None:
        uptime = datetime.now() - self._start_time
        total_sec = int(uptime.total_seconds())
        m, s = divmod(total_sec, 60)

        status = '[green]Работает[/green]' if stats.is_running else '[red]Остановлен[/red]'
        console.print()
        console.rule('[bold]Статус бота[/bold]', style='dim')
        console.print(
            f'  Статус: {status}  |  '
            f'Uptime: [white]{m}м {s}с[/white]  |  '
            f'Сканирований: [white]{stats.scan_count}[/white]  |  '
            f'Скан: [white]{stats.last_scan_duration_ms}мс[/white]'
        )
        console.print(
            f'  Возможностей: [yellow]{stats.total_opportunities_found}[/yellow]  |  '
            f'Сделок: [green]{stats.total_trades_executed}[/green]  |  '
            f'Откр. позиций: [cyan]{stats.open_positions_count}[/cyan]  |  '
            f'Прибыль 1ч: [bold green]+${profit_hour:.4f}[/bold green]  |  '
            f'Прибыль 24ч: [bold green]+${profit_24h:.4f}[/bold green]'
        )
        if stats.errors:
            console.print(f'  [red][ERR] {stats.errors[-1][:100]}[/red]')

    def print_opportunity(self, opp: ArbitrageOpportunity, trade: VirtualTrade) -> None:
        icons = {'cross_exchange': '⇄', 'triangular': '△', 'futures_spot': '◈'}
        labels = {'cross_exchange': 'МЕЖБИРЖ', 'triangular': 'ТРЕУГОЛ', 'futures_spot': 'ФЬЮ-СПОТ'}
        icon = icons.get(opp.strategy, '●')
        label = labels.get(opp.strategy, opp.strategy)

        profit_style = 'bold green' if opp.profit_percent >= 0.5 else 'yellow'
        console.print()
        console.print(
            f'[bold white]{icon} [{label}][/bold white] '
            f'[white]{opp.symbol}[/white]  '
            f'[{profit_style}]+{opp.profit_percent:.4f}%[/{profit_style}]  '
            f'[{profit_style}]+${opp.profit_usdt:.4f}[/{profit_style}]'
        )

        if opp.strategy == 'cross_exchange':
            d = opp.details
            assert isinstance(d, CrossExchangeDetails)
            console.print(
                f'  [dim]Купить на {d.buy_exchange} по ${d.buy_price:.2f} → '
                f'Продать на {d.sell_exchange} по ${d.sell_price:.2f} | '
                f'Объём: {d.max_qty:.6f} {d.symbol.split("/")[0]}[/dim]'
            )
        elif opp.strategy == 'triangular':
            d = opp.details
            assert isinstance(d, TriangularDetails)
            console.print(
                f'  [dim]Путь: {" → ".join(d.path)} | '
                f'{d.start_amount:.2f} → {d.end_amount:.2f} USDT[/dim]'
            )
        else:
            d = opp.details
            assert isinstance(d, FuturesSpotDetails)
            console.print(
                f'  [dim]Спот: ${d.spot_price:.2f} | Фьюч: ${d.futures_price:.2f} | '
                f'Базис: {d.basis_percent:.4f}% | Ставка: {d.funding_rate * 100:.4f}%[/dim]'
            )

        actual = trade.actual_profit_usdt or 0
        console.print(
            f'  [dim]Позиция: ${opp.position_size_usdt:.0f} | '
            f'[green]Виртуальная прибыль: +${actual:.4f}[/green][/dim]'
        )

    def print_position_closed(self, pos: FuturesSpotPosition, trade: VirtualTrade) -> None:
        profit = trade.actual_profit_usdt or 0
        profit_style = 'bold green' if profit >= 0 else 'bold red'
        profit_sign = '+' if profit >= 0 else ''
        h = int(pos.hours_open())
        m = int((pos.hours_open() - h) * 60)
        console.print()
        console.print(
            f'[bold white]◈ [ЗАКРЫТА][/bold white] '
            f'[white]{pos.symbol}[/white]  '
            f'[{profit_style}]{profit_sign}${profit:.4f}[/{profit_style}]  '
            f'[dim]держалась {h}ч {m}мин | {pos.close_reason}[/dim]'
        )

    def print_scan_result(self, opportunities: list[ArbitrageOpportunity], duration_ms: int) -> None:
        now = datetime.now().strftime('%H:%M:%S')
        if not opportunities:
            print(f'\r  [{now}] Скан завершён за {duration_ms}мс | Возможностей не найдено   ', end='', flush=True)
        else:
            console.print()
            console.print(
                f'\n  [{now}] Найдено [yellow]{len(opportunities)}[/yellow] возможностей за {duration_ms}мс'
            )

    def print_report(self, stats: SessionStats) -> None:
        console.print()
        console.rule('[bold cyan]ОТЧЁТ СЕССИИ[/bold cyan]', style='cyan')

        table = Table(show_header=True, header_style='bold white', border_style='dim')
        table.add_column('Метрика', width=35)
        table.add_column('Значение', width=25)

        table.add_row('Всего сделок', str(stats.total_trades))
        table.add_row('Закрытых', str(stats.closed_trades))
        table.add_row('Прибыльных', f'[green]{stats.winning_trades}[/green]')
        table.add_row('Убыточных', f'[red]{stats.losing_trades}[/red]')
        table.add_row('Винрейт', f'[yellow]{stats.win_rate:.2f}%[/yellow]')
        table.add_row('Прибыль за 1ч', f'[bold green]+${stats.profit_last_hour:.4f}[/bold green]')
        table.add_row('Прибыль за 24ч', f'[bold green]+${stats.profit_last_24h:.4f}[/bold green]')
        table.add_row('Итоговая прибыль (demo)', f'[bold green]+${stats.total_profit_usdt:.4f}[/bold green]')
        table.add_row('Ожидаемая прибыль', f'[yellow]${stats.total_expected_profit_usdt:.4f}[/yellow]')
        table.add_row('Средний % прибыли', f'{stats.average_profit_percent:.4f}%')
        table.add_row('ROI (от 10k USDT)', f'{stats.roi:.4f}%')

        console.print(table)

        if stats.by_strategy:
            console.print('\n[bold]По стратегиям:[/bold]')
            st = Table(show_header=True, header_style='bold white', border_style='dim')
            st.add_column('Стратегия')
            st.add_column('Сделок')
            st.add_column('Прибыль')
            for strategy, data in stats.by_strategy.items():
                st.add_row(strategy, str(data['count']), f'[green]+${data["profit"]:.4f}[/green]')
            console.print(st)

        if stats.best_trade:
            console.print(
                f'\n[green]Лучшая сделка: {stats.best_trade["symbol"]} '
                f'+${stats.best_trade["profit"]:.4f} [{stats.best_trade["strategy"]}][/green]'
            )

    def print_error(self, msg: str) -> None:
        console.print(f'  [red][ERR] {msg[:100]}[/red]')

    def print_info(self, msg: str) -> None:
        console.print(f'  [dim]{msg}[/dim]')

    def print_success(self, msg: str) -> None:
        console.print(f'  [green]✓ {msg}[/green]')
