from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .value_objects import Fee, OrderBook


@dataclass
class Ticker:
    symbol: str
    exchange_id: str
    bid: float
    ask: float
    last: float
    volume: float
    timestamp: int


@dataclass
class FuturesTicker(Ticker):
    funding_rate: float = 0.0
    next_funding_time: int = 0
    mark_price: float = 0.0
    index_price: float = 0.0


@dataclass
class ExchangeInfo:
    id: str
    name: str
    fee: Fee
    supports_spot: bool = True
    supports_futures: bool = False


class IExchange(abc.ABC):
    info: ExchangeInfo

    @abc.abstractmethod
    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        ...

    @abc.abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker:
        ...

    @abc.abstractmethod
    async def fetch_tickers(self, symbols: list[str]) -> list[Ticker]:
        ...

    @abc.abstractmethod
    async def fetch_futures_ticker(self, symbol: str) -> Optional[FuturesTicker]:
        ...

    @abc.abstractmethod
    async def is_available(self) -> bool:
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        ...


class ITradeRepository(abc.ABC):
    @abc.abstractmethod
    async def save(self, trade: 'VirtualTrade') -> None:  # type: ignore[name-defined]
        ...

    @abc.abstractmethod
    async def get_all(self) -> list[dict]:
        ...


@dataclass
class TradeAlert:
    strategy: str
    symbol: str
    profit_percent: float
    profit_usdt: float
    position_usdt: float
    details: str
    workflow: list[str]
    profit_last_hour: float
    profit_last_24h: float
    timestamp: 'datetime'  # type: ignore[name-defined]
    alert_type: str = 'opened'
    hours_held: Optional[float] = None
    close_reason: Optional[str] = None
    entry_spot_price: Optional[float] = None
    entry_futures_price: Optional[float] = None
    entry_basis_percent: Optional[float] = None
    exit_spot_price: Optional[float] = None
    exit_futures_price: Optional[float] = None
    exit_basis_percent: Optional[float] = None


class IAlertService(abc.ABC):
    @abc.abstractmethod
    async def send_trade_alert(self, alert: TradeAlert) -> None:
        ...


@dataclass(frozen=True)
class ScanTelemetry:
    scanned_at: datetime
    duration_ms: int
    opportunities_count: int
    errors_count: int


@dataclass(frozen=True)
class SignalTelemetry:
    strategy: str
    symbol: str
    route_type: str
    expected_profit_usdt: float
    expected_profit_percent: float
    position_usdt: float
    exchange: str = ''
    buy_exchange: str = ''
    sell_exchange: str = ''
    spot_exchange: str = ''
    futures_exchange: str = ''


@dataclass(frozen=True)
class TradeTelemetry:
    strategy: str
    symbol: str
    route_type: str
    expected_profit_usdt: float
    expected_profit_percent: float
    realized_profit_usdt: float
    position_usdt: float
    exchange: str = ''
    buy_exchange: str = ''
    sell_exchange: str = ''
    spot_exchange: str = ''
    futures_exchange: str = ''


class IMetricsService(abc.ABC):
    @abc.abstractmethod
    def start(self) -> None:
        ...

    @abc.abstractmethod
    def set_bot_running(self, is_running: bool) -> None:
        ...

    @abc.abstractmethod
    def set_open_positions(self, total: int) -> None:
        ...

    @abc.abstractmethod
    def record_scan(self, telemetry: ScanTelemetry) -> None:
        ...

    @abc.abstractmethod
    def record_signal(self, telemetry: SignalTelemetry) -> None:
        ...

    @abc.abstractmethod
    def record_trade(self, telemetry: TradeTelemetry) -> None:
        ...

    @abc.abstractmethod
    def record_error(self, stage: str, exchange: str = '', symbol: str = '') -> None:
        ...
