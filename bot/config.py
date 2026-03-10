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
    log_file: str
    exchanges: dict[str, ExchangeCredentials]
    telegram: TelegramConfig
    pairs: list[str]
    strategies: dict[str, bool]


def _mode_from_args() -> str | None:
    for arg in sys.argv[1:]:
        if arg.startswith('--mode='):
            return arg.split('=', 1)[1]
    return None


config = AppConfig(
    mode=_mode_from_args() or os.getenv('MODE', 'demo'),
    scan_interval_ms=int(os.getenv('SCAN_INTERVAL_MS', '3000')),
    min_profit_percent=float(os.getenv('MIN_PROFIT_PERCENT', '0.1')),
    max_position_usdt=float(os.getenv('MAX_POSITION_USDT', '100')),
    log_file=os.getenv('LOG_FILE', 'trades.json'),
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
        'ARB/USDT', 'OP/USDT', 'MATIC/USDT', 'FIL/USDT',
        'NEAR/USDT', 'INJ/USDT',
    ],
    strategies={
        'cross_exchange': True,
        'triangular': True,
        'futures_spot': True,
    },
)
