from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ExchangeCredentials:
    api_key: str
    secret: str
    passphrase: str = ''


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass
class AppConfig:
    mode: str
    scan_interval_ms: int
    scan_request_timeout_ms: int
    scan_bulk_ticker_batch_size: int
    exchange_error_cooldown_seconds: int
    exchange_error_threshold: int
    min_profit_percent: float
    max_position_usdt: float
    max_open_positions: int
    max_daily_loss_usdt: float
    max_close_failures: int
    futures_spot_book_depth_limit: int
    futures_spot_min_top_level_notional_usdt: float
    futures_spot_min_depth_ratio: float
    futures_spot_max_spread_percent: float
    futures_spot_close_reserve_scale: float
    futures_spot_basis_history_window: int
    futures_spot_basis_min_samples: int
    futures_spot_min_basis_zscore: float
    futures_spot_min_funding_rate: float
    futures_spot_max_mark_price_deviation_percent: float
    futures_spot_max_index_price_deviation_percent: float
    futures_spot_route_history_size: int
    futures_spot_route_min_closed_trades: int
    futures_spot_route_min_win_rate: float
    futures_spot_route_max_median_underperformance_usdt: float
    futures_spot_route_max_p95_underperformance_usdt: float
    futures_spot_prefilter_profit_floor_percent: float
    futures_spot_prefilter_max_routes_per_symbol: int
    live_reconcile_interval_seconds: int
    live_orphan_notional_threshold_usdt: float
    spot_scan_concurrency: int
    futures_scan_concurrency: int
    futures_leverage: int
    futures_margin_mode: str
    metrics_enabled: bool
    metrics_port: int
    log_file: str
    log_dir: str
    log_retention_days: int
    analytics_timezone: str
    redis_url: str
    postgres_dsn: str
    exchanges: dict[str, ExchangeCredentials]
    telegram: TelegramConfig
    pairs: list[str]
    symbol_universe_mode: str
    symbol_universe_quote_currency: str
    symbol_universe_max_symbols: int
    symbol_universe_min_spot_exchanges: int
    symbol_universe_min_futures_exchanges: int
    symbol_universe_min_funding_exchanges: int
    symbol_universe_include: list[str]
    symbol_universe_exclude: list[str]
    spot_exchange_allowlist: list[str]
    futures_exchange_allowlist: list[str]
    strategies: dict[str, bool]
    futures_spot_long_only: bool


def _mode_from_args() -> str | None:
    for arg in sys.argv[1:]:
        if arg.startswith('--mode='):
            return arg.split('=', 1)[1]
    return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() not in {'0', 'false', 'no', 'off'}


def _env_csv(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = [item.strip().lower() for item in raw.split(',')]
    return [item for item in values if item]


def _env_symbols(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = [item.strip().upper() for item in raw.split(',')]
    return [item for item in values if item]


config = AppConfig(
    mode=_mode_from_args() or os.getenv('MODE', 'demo'),
    scan_interval_ms=int(os.getenv('SCAN_INTERVAL_MS', '3000')),
    scan_request_timeout_ms=max(int(os.getenv('SCAN_REQUEST_TIMEOUT_MS', '8000')), 1000),
    scan_bulk_ticker_batch_size=max(int(os.getenv('SCAN_BULK_TICKER_BATCH_SIZE', '8')), 1),
    exchange_error_cooldown_seconds=max(int(os.getenv('SCAN_EXCHANGE_ERROR_COOLDOWN_SECONDS', '1800')), 60),
    exchange_error_threshold=max(int(os.getenv('SCAN_EXCHANGE_ERROR_THRESHOLD', '3')), 1),
    min_profit_percent=float(os.getenv('MIN_PROFIT_PERCENT', '0.1')),
    max_position_usdt=float(os.getenv('MAX_POSITION_USDT', '100')),
    max_open_positions=max(int(os.getenv('MAX_OPEN_POSITIONS', '1')), 1),
    max_daily_loss_usdt=max(float(os.getenv('MAX_DAILY_LOSS_USDT', '20')), 0.0),
    max_close_failures=max(int(os.getenv('MAX_CLOSE_FAILURES', '10')), 1),
    futures_spot_book_depth_limit=max(int(os.getenv('FUTURES_SPOT_BOOK_DEPTH_LIMIT', '20')), 5),
    futures_spot_min_top_level_notional_usdt=max(
        float(os.getenv('FUTURES_SPOT_MIN_TOP_LEVEL_NOTIONAL_USDT', '150')),
        0.0,
    ),
    futures_spot_min_depth_ratio=max(float(os.getenv('FUTURES_SPOT_MIN_DEPTH_RATIO', '1.0')), 0.0),
    futures_spot_max_spread_percent=max(float(os.getenv('FUTURES_SPOT_MAX_SPREAD_PERCENT', '0.12')), 0.0),
    futures_spot_close_reserve_scale=max(float(os.getenv('FUTURES_SPOT_CLOSE_RESERVE_SCALE', '1.0')), 0.0),
    futures_spot_basis_history_window=max(int(os.getenv('FUTURES_SPOT_BASIS_HISTORY_WINDOW', '240')), 10),
    futures_spot_basis_min_samples=max(int(os.getenv('FUTURES_SPOT_BASIS_MIN_SAMPLES', '30')), 2),
    futures_spot_min_basis_zscore=float(os.getenv('FUTURES_SPOT_MIN_BASIS_ZSCORE', '1.2')),
    futures_spot_min_funding_rate=float(os.getenv('FUTURES_SPOT_MIN_FUNDING_RATE', '0.0')),
    futures_spot_max_mark_price_deviation_percent=max(
        float(os.getenv('FUTURES_SPOT_MAX_MARK_PRICE_DEVIATION_PERCENT', '0.25')),
        0.0,
    ),
    futures_spot_max_index_price_deviation_percent=max(
        float(os.getenv('FUTURES_SPOT_MAX_INDEX_PRICE_DEVIATION_PERCENT', '0.35')),
        0.0,
    ),
    futures_spot_route_history_size=max(int(os.getenv('FUTURES_SPOT_ROUTE_HISTORY_SIZE', '50')), 5),
    futures_spot_route_min_closed_trades=max(int(os.getenv('FUTURES_SPOT_ROUTE_MIN_CLOSED_TRADES', '5')), 1),
    futures_spot_route_min_win_rate=min(max(float(os.getenv('FUTURES_SPOT_ROUTE_MIN_WIN_RATE', '0.4')), 0.0), 1.0),
    futures_spot_route_max_median_underperformance_usdt=max(
        float(os.getenv('FUTURES_SPOT_ROUTE_MAX_MEDIAN_UNDERPERFORMANCE_USDT', '0.15')),
        0.0,
    ),
    futures_spot_route_max_p95_underperformance_usdt=max(
        float(os.getenv('FUTURES_SPOT_ROUTE_MAX_P95_UNDERPERFORMANCE_USDT', '0.35')),
        0.0,
    ),
    futures_spot_prefilter_profit_floor_percent=float(
        os.getenv('FUTURES_SPOT_PREFILTER_PROFIT_FLOOR_PERCENT', '-0.05')
    ),
    futures_spot_prefilter_max_routes_per_symbol=max(
        int(os.getenv('FUTURES_SPOT_PREFILTER_MAX_ROUTES_PER_SYMBOL', '5')),
        1,
    ),
    live_reconcile_interval_seconds=max(int(os.getenv('LIVE_RECONCILE_INTERVAL_SECONDS', '30')), 5),
    live_orphan_notional_threshold_usdt=float(os.getenv('LIVE_ORPHAN_NOTIONAL_THRESHOLD_USDT', '5')),
    spot_scan_concurrency=int(os.getenv('SPOT_SCAN_CONCURRENCY', '6')),
    futures_scan_concurrency=int(os.getenv('FUTURES_SCAN_CONCURRENCY', '4')),
    futures_leverage=int(os.getenv('FUTURES_LEVERAGE', '5')),
    futures_margin_mode=os.getenv('FUTURES_MARGIN_MODE', 'isolated').lower(),
    metrics_enabled=os.getenv('METRICS_ENABLED', 'true').lower() != 'false',
    metrics_port=int(os.getenv('METRICS_PORT', '9108')),
    log_file=os.getenv('LOG_FILE', 'trades.json'),
    log_dir=os.getenv('LOG_DIR', 'logs'),
    log_retention_days=int(os.getenv('LOG_RETENTION_DAYS', '7')),
    analytics_timezone=os.getenv('ANALYTICS_TIMEZONE', 'Europe/Moscow'),
    redis_url=os.getenv('REDIS_URL', 'redis://redis:6379/0'),
    postgres_dsn=os.getenv('POSTGRES_DSN', 'postgresql://trade:trade@postgres:5432/trade'),
    exchanges={
        'binance': ExchangeCredentials(
            api_key=os.getenv('BINANCE_API_KEY', ''),
            secret=os.getenv('BINANCE_SECRET', ''),
        ),
        'bybit': ExchangeCredentials(
            api_key=os.getenv('BYBIT_API_KEY', ''),
            secret=os.getenv('BYBIT_SECRET', ''),
        ),
        'okx': ExchangeCredentials(
            api_key=os.getenv('OKX_API_KEY', ''),
            secret=os.getenv('OKX_SECRET', ''),
            passphrase=os.getenv('OKX_PASSPHRASE', ''),
        ),
        'kucoin': ExchangeCredentials(
            api_key=os.getenv('KUCOIN_API_KEY', ''),
            secret=os.getenv('KUCOIN_SECRET', ''),
            passphrase=os.getenv('KUCOIN_PASSPHRASE', ''),
        ),
        'gateio': ExchangeCredentials(
            api_key=os.getenv('GATEIO_API_KEY', ''),
            secret=os.getenv('GATEIO_SECRET', ''),
        ),
        'mexc': ExchangeCredentials(
            api_key=os.getenv('MEXC_API_KEY', ''),
            secret=os.getenv('MEXC_SECRET', ''),
        ),
        'bitget': ExchangeCredentials(
            api_key=os.getenv('BITGET_API_KEY', ''),
            secret=os.getenv('BITGET_SECRET', ''),
            passphrase=os.getenv('BITGET_PASSPHRASE', ''),
        ),
        'htx': ExchangeCredentials(
            api_key=os.getenv('HTX_API_KEY', ''),
            secret=os.getenv('HTX_SECRET', ''),
        ),
    },
    telegram=TelegramConfig(
        bot_token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
        chat_id=os.getenv('TELEGRAM_CHAT_ID', ''),
    ),
    pairs=_env_symbols(
        'PAIRS',
        [
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT',
        'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT',
        'DOT/USDT', 'LINK/USDT', 'LTC/USDT', 'ATOM/USDT',
        'TRX/USDT', 'UNI/USDT', 'APT/USDT', 'SUI/USDT',
        'ARB/USDT', 'OP/USDT', 'POL/USDT', 'FIL/USDT',
        'NEAR/USDT', 'INJ/USDT',
        ],
    ),
    symbol_universe_mode=os.getenv('SYMBOL_UNIVERSE_MODE', 'dynamic').strip().lower(),
    symbol_universe_quote_currency=os.getenv('SYMBOL_UNIVERSE_QUOTE_CURRENCY', 'USDT').strip().upper(),
    symbol_universe_max_symbols=max(int(os.getenv('SYMBOL_UNIVERSE_MAX_SYMBOLS', '30')), 1),
    symbol_universe_min_spot_exchanges=max(int(os.getenv('SYMBOL_UNIVERSE_MIN_SPOT_EXCHANGES', '1')), 1),
    symbol_universe_min_futures_exchanges=max(int(os.getenv('SYMBOL_UNIVERSE_MIN_FUTURES_EXCHANGES', '1')), 1),
    symbol_universe_min_funding_exchanges=max(int(os.getenv('SYMBOL_UNIVERSE_MIN_FUNDING_EXCHANGES', '2')), 1),
    symbol_universe_include=_env_symbols('SYMBOL_UNIVERSE_INCLUDE', []),
    symbol_universe_exclude=_env_symbols('SYMBOL_UNIVERSE_EXCLUDE', []),
    spot_exchange_allowlist=_env_csv(
        'SPOT_EXCHANGE_ALLOWLIST',
        ['binance', 'bybit', 'okx', 'kucoin', 'gateio', 'mexc', 'bitget', 'htx'],
    ),
    futures_exchange_allowlist=_env_csv(
        'FUTURES_EXCHANGE_ALLOWLIST',
        ['binance', 'bybit', 'okx', 'kucoin', 'gateio', 'mexc', 'bitget', 'htx'],
    ),
    strategies={
        'cross_exchange': _env_bool('ENABLE_CROSS_EXCHANGE', False),
        'triangular': _env_bool('ENABLE_TRIANGULAR', False),
        'futures_spot': _env_bool('ENABLE_FUTURES_SPOT', True),
        'futures_funding': _env_bool('ENABLE_FUTURES_FUNDING', True),
    },
    futures_spot_long_only=os.getenv('FUTURES_SPOT_LONG_ONLY', 'true').lower() != 'false',
)
