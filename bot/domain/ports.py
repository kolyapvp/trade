from __future__ import annotations

import abc
from dataclasses import dataclass
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


class IAlertService(abc.ABC):
    @abc.abstractmethod
    async def send_trade_alert(self, alert: TradeAlert) -> None:
        ...
