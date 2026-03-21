from __future__ import annotations

import asyncio
import signal
import sys
from contextlib import suppress

import asyncpg
import redis.asyncio as redis

from bot.application.bot_service import ArbitrageBotService
from bot.application.use_cases import (
    ExecuteDemoTradeUseCase,
    FuturesSpotPositionManager,
    GenerateReportUseCase,
    ScanConfig,
    ScanOpportunitiesUseCase,
    TriangularPathConfig,
    build_closed_trade_analytics,
)
from bot.application.symbol_universe import SymbolUniverseBuilder, SymbolUniverseConfig
from bot.config import config
from bot.domain.entities import Portfolio, VirtualTrade
from bot.domain.services import (
    FuturesSpotBasisMonitor,
    FuturesSpotRiskConfig,
    FuturesSpotRouteQualityMonitor,
)
from bot.infrastructure.exchange_factory import ExchangeFactory
from bot.infrastructure.logging_setup import configure_file_logging
from bot.infrastructure.metrics_service import NullMetricsService, PrometheusMetricsService
from bot.infrastructure.postgres_position_repository import PostgresOpenPositionSnapshotRepository
from bot.infrastructure.postgres_trade_analytics_repository import PostgresTradeAnalyticsRepository
from bot.infrastructure.redis_deployment_repository import RedisDeploymentStateRepository
from bot.infrastructure.redis_trade_repository import RedisOpenPositionStore, RedisTradeRepository
from bot.infrastructure.telegram_service import TelegramAlertService
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


async def _restore_portfolio(repository: RedisTradeRepository, portfolio: Portfolio) -> int:
    restored = 0
    for item in await repository.get_all():
        try:
            portfolio.add_trade(VirtualTrade.from_dict(item))
            restored += 1
        except Exception:
            continue
    return restored


async def bootstrap() -> None:
    configure_file_logging(config.log_dir, config.log_retention_days)

    dashboard = Dashboard()
    dashboard.print_header(config.mode)
    dashboard.print_info(f'Режим: {config.mode.upper()}')
    dashboard.print_info(f'Интервал сканирования: {config.scan_interval_ms}мс')
    dashboard.print_info(f'Мин. прибыль: {config.min_profit_percent}%')
    dashboard.print_info(f'Макс. позиция: ${config.max_position_usdt}')
    dashboard.print_info(
        f'Параллельность сканирования: spot x{config.spot_scan_concurrency} | '
        f'futures x{config.futures_scan_concurrency}'
    )
    dashboard.print_info(f'Spot allowlist: {", ".join(config.spot_exchange_allowlist)}')
    dashboard.print_info(f'Futures allowlist: {", ".join(config.futures_exchange_allowlist)}')
    dashboard.print_info(f'Фьючерсы: {config.futures_margin_mode} | плечо {config.futures_leverage}x')
    dashboard.print_info(
        f'Метрики: {"включены" if config.metrics_enabled else "выключены"}'
        f'{" на порту " + str(config.metrics_port) if config.metrics_enabled else ""}'
    )
    dashboard.print_info('Redis: configured')
    dashboard.print_info('Postgres: configured')
    dashboard.print_info(f'Логи: {config.log_dir} ({config.log_retention_days} дн.)')

    redis_client = None
    postgres_pool = None
    all_exchanges = []
    try:
        redis_client = redis.from_url(
            config.redis_url,
            encoding='utf-8',
            decode_responses=True,
            health_check_interval=30,
        )
        postgres_pool = await asyncpg.create_pool(
            dsn=config.postgres_dsn,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        trade_repository = RedisTradeRepository(redis_client)
        open_position_store = RedisOpenPositionStore(redis_client)
        deployment_state_repository = RedisDeploymentStateRepository(redis_client)
        snapshot_repository = PostgresOpenPositionSnapshotRepository(postgres_pool)
        analytics_repository = PostgresTradeAnalyticsRepository(postgres_pool)
        await redis_client.ping()
        await snapshot_repository.initialize()
        await analytics_repository.initialize()

        factory = ExchangeFactory(config.exchange_http_timeout_ms)
        cx = config.exchanges
        use_private_api = config.mode == 'real'

        def has_private_api(name: str) -> bool:
            if not use_private_api:
                return False
            credentials = cx.get(name)
            if not credentials or not credentials.api_key or not credentials.secret:
                return False
            if name in {'okx', 'kucoin', 'bitget'} and not credentials.passphrase:
                return False
            return True

        creds_status = {name: has_private_api(name) for name in cx}
        labels = ' | '.join(f'{name}{"(api)" if value else "(pub)"}' for name, value in creds_status.items())
        dashboard.print_info(f'Биржи: {labels}')

        def creds_or_none(name: str):
            return cx[name] if has_private_api(name) else None

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
        spot_exchanges = [
            exchange for exchange in spot_exchanges
            if exchange.info.id in config.spot_exchange_allowlist
        ]

        futures_exchanges = [
            factory.create_binance_futures(creds_or_none('binance')),
            factory.create_bybit_futures(creds_or_none('bybit')),
            factory.create_okx_futures(creds_or_none('okx')),
            factory.create_kucoin_futures(creds_or_none('kucoin')),
            factory.create_gateio_futures(creds_or_none('gateio')),
            factory.create_mexc_futures(creds_or_none('mexc')),
            factory.create_bitget_futures(creds_or_none('bitget')),
            factory.create_htx_futures(creds_or_none('htx')),
        ]
        futures_exchanges = [
            exchange for exchange in futures_exchanges
            if exchange.info.id in config.futures_exchange_allowlist
        ]

        all_exchanges = spot_exchanges + futures_exchanges
        portfolio = Portfolio(initial_capital=10_000.0)
        metrics_service = (
            PrometheusMetricsService(config.metrics_port, config.mode)
            if config.metrics_enabled else NullMetricsService()
        )

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
            sys.exit(1)

        dashboard.print_info('Проверка доступности фьючерсных рынков...')
        futures_avail = await asyncio.gather(*[ex.is_available() for ex in futures_exchanges], return_exceptions=True)

        active_futures = []
        for ex, ok in zip(futures_exchanges, futures_avail):
            if ok is True:
                dashboard.print_success(f'{ex.info.id} futures доступны')
                active_futures.append(ex)
            else:
                dashboard.print_error(f'{ex.info.id} futures недоступны, пропускаем')

        universe_builder = SymbolUniverseBuilder(SymbolUniverseConfig(
            mode=config.symbol_universe_mode,
            quote_currency=config.symbol_universe_quote_currency,
            max_symbols=config.symbol_universe_max_symbols,
            min_spot_exchanges=config.symbol_universe_min_spot_exchanges,
            min_futures_exchanges=config.symbol_universe_min_futures_exchanges,
            min_funding_exchanges=config.symbol_universe_min_funding_exchanges,
            include_symbols=config.symbol_universe_include,
            exclude_symbols=config.symbol_universe_exclude,
        ))
        universe = await universe_builder.build(
            active_spot,
            active_futures,
            config.pairs,
            enable_cross_exchange=config.strategies.get('cross_exchange', True),
            enable_futures_spot=config.strategies.get('futures_spot', True),
            enable_futures_funding=config.strategies.get('futures_funding', True),
        )
        if universe.errors:
            for error in universe.errors:
                dashboard.print_error(error)
        selected_symbols = universe.symbols or config.pairs
        dashboard.print_info(
            f'Universe ({config.symbol_universe_mode}): {len(selected_symbols)} symbols'
        )
        dashboard.print_info(
            f'Symbols: {", ".join(selected_symbols[:20])}'
            f'{" ..." if len(selected_symbols) > 20 else ""}'
        )

        live_spot_exchange_map = {
            exchange.info.id: exchange
            for exchange in active_spot
            if has_private_api(exchange.info.id)
        }
        live_futures_exchange_map = {
            exchange.info.id: exchange
            for exchange in active_futures
            if has_private_api(exchange.info.id)
        }
        balance_exchange_map: dict[str, list] = {}
        for exchange_id, exchange in live_spot_exchange_map.items():
            balance_exchange_map.setdefault(exchange_id, []).append(exchange)
        for exchange_id, exchange in live_futures_exchange_map.items():
            balance_exchange_map.setdefault(exchange_id, []).append(exchange)
        if config.mode == 'real':
            live_spot_labels = ', '.join(sorted(live_spot_exchange_map)) or 'нет'
            live_futures_labels = ', '.join(sorted(live_futures_exchange_map)) or 'нет'
            dashboard.print_info(f'Live spot API: {live_spot_labels}')
            dashboard.print_info(f'Live futures API: {live_futures_labels}')
            missing_spot_live = [
                exchange_id for exchange_id in config.spot_exchange_allowlist
                if exchange_id not in live_spot_exchange_map
            ]
            missing_futures_live = [
                exchange_id for exchange_id in config.futures_exchange_allowlist
                if exchange_id not in live_futures_exchange_map
            ]
            if missing_spot_live:
                dashboard.print_error(
                    f'Для live spot не хватает API-ключей: {", ".join(missing_spot_live)}'
                )
            if missing_futures_live:
                dashboard.print_error(
                    f'Для live futures не хватает API-ключей: {", ".join(missing_futures_live)}'
                )

        alert_service = None
        if config.telegram.bot_token and config.telegram.chat_id:
            alert_service = TelegramAlertService(config.telegram.bot_token, config.telegram.chat_id)
            dashboard.print_success('Telegram-алерты подключены')
        else:
            dashboard.print_info('Telegram-алерты отключены (TELEGRAM_BOT_TOKEN не задан)')

        restored_trades = await _restore_portfolio(trade_repository, portfolio)
        if restored_trades:
            dashboard.print_info(f'Из Redis восстановлено сделок: {restored_trades}')
        backfilled_trades = await analytics_repository.backfill_closed_trades([
            build_closed_trade_analytics(trade, config.analytics_timezone)
            for trade in portfolio.closed_trades
        ])
        if backfilled_trades:
            dashboard.print_info(f'В Postgres backfill закрытых сделок: {backfilled_trades}')

        scan_cfg = ScanConfig(
            symbols=selected_symbols,
            position_size_usdt=config.max_position_usdt,
            min_profit_percent=config.min_profit_percent,
            triangular_paths=TRIANGULAR_PATHS,
            scan_request_timeout_ms=config.scan_request_timeout_ms,
            scan_bulk_ticker_batch_size=config.scan_bulk_ticker_batch_size,
            exchange_error_cooldown_seconds=config.exchange_error_cooldown_seconds,
            exchange_error_threshold=config.exchange_error_threshold,
            spot_scan_concurrency=config.spot_scan_concurrency,
            futures_scan_concurrency=config.futures_scan_concurrency,
            enable_cross_exchange=config.strategies.get('cross_exchange', True),
            enable_triangular=config.strategies.get('triangular', True),
            enable_futures_spot=config.strategies.get('futures_spot', True),
            enable_futures_funding=config.strategies.get('futures_funding', True),
            futures_spot_long_only=config.futures_spot_long_only,
            futures_spot_book_depth_limit=config.futures_spot_book_depth_limit,
            futures_spot_prefilter_profit_floor_percent=config.futures_spot_prefilter_profit_floor_percent,
            futures_spot_prefilter_max_routes_per_symbol=config.futures_spot_prefilter_max_routes_per_symbol,
            spot_symbols_by_exchange=universe.spot_symbols_by_exchange,
            futures_symbols_by_exchange=universe.futures_symbols_by_exchange,
        )

        futures_spot_risk = FuturesSpotRiskConfig(
            book_depth_limit=config.futures_spot_book_depth_limit,
            min_top_level_notional_usdt=config.futures_spot_min_top_level_notional_usdt,
            min_depth_ratio=config.futures_spot_min_depth_ratio,
            max_spread_percent=config.futures_spot_max_spread_percent,
            close_reserve_scale=config.futures_spot_close_reserve_scale,
            basis_history_window=config.futures_spot_basis_history_window,
            basis_min_samples=config.futures_spot_basis_min_samples,
            min_basis_zscore=config.futures_spot_min_basis_zscore,
            min_funding_rate=config.futures_spot_min_funding_rate,
            max_mark_price_deviation_percent=config.futures_spot_max_mark_price_deviation_percent,
            max_index_price_deviation_percent=config.futures_spot_max_index_price_deviation_percent,
            route_history_size=config.futures_spot_route_history_size,
            route_min_closed_trades=config.futures_spot_route_min_closed_trades,
            route_min_win_rate=config.futures_spot_route_min_win_rate,
            route_max_median_underperformance_usdt=config.futures_spot_route_max_median_underperformance_usdt,
            route_max_p95_underperformance_usdt=config.futures_spot_route_max_p95_underperformance_usdt,
        )
        futures_spot_basis_monitor = FuturesSpotBasisMonitor(futures_spot_risk.basis_history_window)
        futures_spot_route_quality_monitor = FuturesSpotRouteQualityMonitor(futures_spot_risk.route_history_size)
        futures_spot_route_quality_monitor.bootstrap([
            trade for trade in portfolio.closed_trades
            if trade.strategy == 'futures_spot'
        ])
        scanner = ScanOpportunitiesUseCase(
            active_spot,
            active_futures,
            futures_spot_risk=futures_spot_risk,
            futures_spot_basis_monitor=futures_spot_basis_monitor,
            futures_spot_route_quality_monitor=futures_spot_route_quality_monitor,
        )
        executor = ExecuteDemoTradeUseCase(
            trade_repository,
            portfolio,
            analytics_repository,
            config.analytics_timezone,
        )
        position_manager = FuturesSpotPositionManager(
            trade_repository,
            portfolio,
            open_position_store,
            snapshot_repository,
            analytics_repository,
            config.analytics_timezone,
            config.mode,
            futures_leverage=config.futures_leverage,
            futures_margin_mode=config.futures_margin_mode,
            spot_execution_exchanges=live_spot_exchange_map,
            futures_execution_exchanges=live_futures_exchange_map,
            max_close_failures=config.max_close_failures,
        )
        reporter = GenerateReportUseCase(trade_repository, portfolio)
        reporter.set_position_manager(position_manager)

        restored_positions = await position_manager.restore_positions(await snapshot_repository.get_all())
        if restored_positions:
            dashboard.print_success(f'Из Postgres восстановлено открытых позиций: {len(restored_positions)}')

        bot = ArbitrageBotService(
            scanner=scanner,
            executor=executor,
            reporter=reporter,
            portfolio=portfolio,
            scan_config=scan_cfg,
            mode=config.mode,
            scan_interval_ms=config.scan_interval_ms,
            position_manager=position_manager,
            metrics_service=metrics_service,
            deployment_state_repository=deployment_state_repository,
            alert_service=alert_service,
            live_spot_exchange_ids=set(live_spot_exchange_map),
            live_futures_exchange_ids=set(live_futures_exchange_map),
            balance_exchanges=balance_exchange_map,
            max_open_positions=config.max_open_positions,
            max_daily_loss_usdt=config.max_daily_loss_usdt,
            live_reconcile_interval_seconds=config.live_reconcile_interval_seconds,
            live_orphan_notional_threshold_usdt=config.live_orphan_notional_threshold_usdt,
        )

        bot.set_scan_handler(lambda opps, dur: (
            dashboard.print_bot_stats(bot.get_stats(), portfolio.profit_last_hour(), portfolio.profit_last_24h()),
            dashboard.print_scan_result(opps, dur),
        ))
        bot.set_opportunity_handler(lambda opp, trade: dashboard.print_opportunity(opp, trade))
        bot.set_position_closed_handler(lambda pos, trade: dashboard.print_position_closed(pos, trade))
        bot.set_error_handler(lambda err: dashboard.print_error(err))

        if config.mode == 'report':
            report = await reporter.execute()
            dashboard.print_report(report)
            return

        shutdown_requested = asyncio.Event()

        def request_shutdown() -> None:
            if shutdown_requested.is_set():
                return
            shutdown_requested.set()
            dashboard.print_info('Получен сигнал остановки, ожидаю завершение текущего цикла...')
            bot.stop()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, request_shutdown)

        dashboard.print_success('Бот запущен. Нажмите Ctrl+C для остановки и просмотра отчёта.')
        print()

        bot_task = asyncio.create_task(bot.start())
        try:
            await bot_task
        finally:
            bot.stop()
            await position_manager.flush_open_positions()
            if shutdown_requested.is_set():
                print()
                dashboard.print_info('Остановка бота...')
            report = await reporter.execute()
            dashboard.print_report(report)

    finally:
        await asyncio.gather(*[ex.close() for ex in all_exchanges], return_exceptions=True)
        if postgres_pool is not None:
            await postgres_pool.close()
        if redis_client is not None:
            await redis_client.aclose()


if __name__ == '__main__':
    asyncio.run(bootstrap())
