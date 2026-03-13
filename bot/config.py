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
    min_profit_percent: float
    max_position_usdt: float
    max_open_positions: int
    max_daily_loss_usdt: float
    max_close_failures: int
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


config = AppConfig(
    mode=_mode_from_args() or os.getenv('MODE', 'demo'),
    scan_interval_ms=int(os.getenv('SCAN_INTERVAL_MS', '3000')),
    min_profit_percent=float(os.getenv('MIN_PROFIT_PERCENT', '0.1')),
    max_position_usdt=float(os.getenv('MAX_POSITION_USDT', '100')),
    max_open_positions=max(int(os.getenv('MAX_OPEN_POSITIONS', '1')), 1),
    max_daily_loss_usdt=max(float(os.getenv('MAX_DAILY_LOSS_USDT', '20')), 0.0),
    max_close_failures=max(int(os.getenv('MAX_CLOSE_FAILURES', '10')), 1),
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
    pairs=[
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT',
        'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT',
        'DOT/USDT', 'LINK/USDT', 'LTC/USDT', 'ATOM/USDT',
        'TRX/USDT', 'UNI/USDT', 'APT/USDT', 'SUI/USDT',
        'ARB/USDT', 'OP/USDT', 'POL/USDT', 'FIL/USDT',
        'NEAR/USDT', 'INJ/USDT',
    ],
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
