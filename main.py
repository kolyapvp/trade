from __future__ import annotations

import asyncio
import signal
import sys

from bot.config import config
from bot.domain.entities import Portfolio
from bot.infrastructure.exchange_factory import ExchangeFactory
from bot.infrastructure.file_repository import FileTradeRepository
from bot.infrastructure.telegram_service import TelegramAlertService
from bot.application.use_cases import (
    ScanOpportunitiesUseCase,
    ExecuteDemoTradeUseCase,
    GenerateReportUseCase,
    FuturesSpotPositionManager,
    ScanConfig,
    TriangularPathConfig,
)
from bot.application.bot_service import ArbitrageBotService
from bot.presentation.dashboard import Dashboard

TRIANGULAR_PATHS = [
    TriangularPathConfig('binance', ['ETH/BTC', 'ETH/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'ETH', 'USDT']),
    TriangularPathConfig('binance', ['BNB/BTC', 'BNB/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'BNB', 'USDT']),
    TriangularPathConfig('binance', ['SOL/BTC', 'SOL/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'SOL', 'USDT']),
    TriangularPathConfig('binance', ['LTC/BTC', 'LTC/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'LTC', 'USDT']),
    TriangularPathConfig('binance', ['XRP/BTC', 'XRP/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'XRP', 'USDT']),
    TriangularPathConfig('binance', ['ADA/BTC', 'ADA/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'ADA', 'USDT']),
    TriangularPathConfig('bybit', ['ETH/BTC', 'ETH/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'ETH', 'USDT']),
    TriangularPathConfig('bybit', ['SOL/BTC', 'SOL/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'SOL', 'USDT']),
    TriangularPathConfig('kucoin', ['ETH/BTC', 'ETH/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'ETH', 'USDT']),
    TriangularPathConfig('kucoin', ['SOL/BTC', 'SOL/USDT', 'BTC/USDT'], ['USDT', 'BTC', 'SOL', 'USDT']),
]


async def bootstrap() -> None:
    dashboard = Dashboard()
    dashboard.print_header(config.mode)
    dashboard.print_info(f'Режим: {config.mode.upper()}')
    dashboard.print_info(f'Интервал сканирования: {config.scan_interval_ms}мс')
    dashboard.print_info(f'Мин. прибыль: {config.min_profit_percent}%')
    dashboard.print_info(f'Макс. позиция: ${config.max_position_usdt}')

    factory = ExchangeFactory()
    cx = config.exchanges

    def has_creds(name: str) -> bool:
        c = cx.get(name)
        return bool(c and c.api_key and c.secret)

    creds_status = {name: has_creds(name) for name in cx}
    labels = ' | '.join(f'{n}{"(api)" if v else "(pub)"}' for n, v in creds_status.items())
    dashboard.print_info(f'Биржи: {labels}')

    def creds_or_none(name: str):
        return cx[name] if has_creds(name) else None

    spot_exchanges = [
        factory.create_binance_spot(creds_or_none('binance')),
        factory.create_bybit(creds_or_none('bybit')),
        factory.create_okx(creds_or_none('okx')),
        factory.create_kucoin(creds_or_none('kucoin')),
        factory.create_gateio(creds_or_none('gateio')),
        factory.create_mexc(creds_or_none('mexc')),
        factory.create_bitget(creds_or_none('bitget')),
        factory.create_htx(creds_or_none('htx')),
    ]

    futures_exchanges = [
        factory.create_binance_futures(creds_or_none('binance')),
        factory.create_bybit_futures(creds_or_none('bybit')),
    ]

    all_exchanges = spot_exchanges + futures_exchanges

    dashboard.print_info('Проверка доступности бирж...')
    avail = await asyncio.gather(*[ex.is_available() for ex in spot_exchanges], return_exceptions=True)

    active_spot = []
    for ex, ok in zip(spot_exchanges, avail):
        if ok is True:
            dashboard.print_success(f'{ex.info.id} доступен')
            active_spot.append(ex)
        else:
            dashboard.print_error(f'{ex.info.id} недоступен, пропускаем')

    if not active_spot:
        dashboard.print_error('Ни одна биржа не доступна. Проверьте интернет-соединение.')
        await asyncio.gather(*[ex.close() for ex in all_exchanges], return_exceptions=True)
        sys.exit(1)

    alert_service = None
    if config.telegram.bot_token and config.telegram.chat_id:
        alert_service = TelegramAlertService(config.telegram.bot_token, config.telegram.chat_id)
        dashboard.print_success('Telegram-алерты подключены')
    else:
        dashboard.print_info('Telegram-алерты отключены (TELEGRAM_BOT_TOKEN не задан)')

    repository = FileTradeRepository(config.log_file)
    portfolio = Portfolio(initial_capital=10_000.0)

    scan_cfg = ScanConfig(
        symbols=config.pairs,
        position_size_usdt=config.max_position_usdt,
        min_profit_percent=config.min_profit_percent,
        triangular_paths=TRIANGULAR_PATHS,
        enable_cross_exchange=config.strategies.get('cross_exchange', True),
        enable_triangular=config.strategies.get('triangular', True),
        enable_futures_spot=config.strategies.get('futures_spot', True),
        futures_spot_long_only=config.futures_spot_long_only,
    )

    scanner = ScanOpportunitiesUseCase(active_spot, futures_exchanges)
    executor = ExecuteDemoTradeUseCase(repository, portfolio)
    position_manager = FuturesSpotPositionManager(repository, portfolio)
    reporter = GenerateReportUseCase(repository, portfolio)
    reporter.set_position_manager(position_manager)

    bot = ArbitrageBotService(
        scanner=scanner,
        executor=executor,
        reporter=reporter,
        portfolio=portfolio,
        scan_config=scan_cfg,
        mode=config.mode,
        scan_interval_ms=config.scan_interval_ms,
        position_manager=position_manager,
        alert_service=alert_service,
    )

    bot.set_scan_handler(lambda opps, dur: (
        dashboard.print_bot_stats(bot.get_stats(), portfolio.profit_last_hour(), portfolio.profit_last_24h()),
        dashboard.print_scan_result(opps, dur),
    ))
    bot.set_opportunity_handler(lambda opp, trade: dashboard.print_opportunity(opp, trade))
    bot.set_position_closed_handler(lambda pos, trade: dashboard.print_position_closed(pos, trade))
    bot.set_error_handler(lambda err: dashboard.print_error(err))

    async def shutdown() -> None:
        bot.stop()
        print()
        dashboard.print_info('Остановка бота...')
        report = await reporter.execute()
        dashboard.print_report(report)
        await asyncio.gather(*[ex.close() for ex in all_exchanges], return_exceptions=True)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown_and_exit()))

    async def shutdown_and_exit() -> None:
        await shutdown()
        sys.exit(0)

    if config.mode == 'report':
        report = await reporter.execute()
        dashboard.print_report(report)
        await asyncio.gather(*[ex.close() for ex in all_exchanges], return_exceptions=True)
        return

    dashboard.print_success('Бот запущен. Нажмите Ctrl+C для остановки и просмотра отчёта.')
    print()
    await bot.start()


if __name__ == '__main__':
    asyncio.run(bootstrap())
