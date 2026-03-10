from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Union


@dataclass
class CrossExchangeDetails:
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    buy_fee: float
    sell_fee: float
    max_qty: float
    symbol: str


@dataclass
class TriangularDetails:
    exchange: str
    path: list[str]
    start_amount: float
    end_amount: float
    fees: float


@dataclass
class FuturesSpotDetails:
    spot_exchange: str
    futures_exchange: str
    symbol: str
    spot_price: float
    futures_price: float
    funding_rate: float
    basis: float
    basis_percent: float
    spot_taker_fee: float = 0.0
    futures_taker_fee: float = 0.0


StrategyDetails = Union[CrossExchangeDetails, TriangularDetails, FuturesSpotDetails]


class FuturesSpotPosition:
    CLOSE_THRESHOLD_PERCENT = 0.05
    MAX_HOLD_HOURS = 48

    def __init__(
        self,
        symbol: str,
        spot_exchange: str,
        futures_exchange: str,
        entry_spot_price: float,
        entry_futures_price: float,
        entry_basis_percent: float,
        funding_rate: float,
        position_usdt: float,
        spot_taker_fee: float,
        futures_taker_fee: float,
    ):
        self.id = f'pos-{symbol}-{int(datetime.now().timestamp() * 1000)}'
        self.symbol = symbol
        self.spot_exchange = spot_exchange
        self.futures_exchange = futures_exchange
        self.entry_spot_price = entry_spot_price
        self.entry_futures_price = entry_futures_price
        self.entry_basis_percent = entry_basis_percent
        self.funding_rate = funding_rate
        self.position_usdt = position_usdt
        self.spot_taker_fee = spot_taker_fee
        self.futures_taker_fee = futures_taker_fee
        self.opened_at = datetime.now()
        self.status = 'open'
        self.exit_spot_price: float = 0.0
        self.exit_futures_price: float = 0.0
        self.exit_basis_percent: float = 0.0
        self.actual_profit_usdt: Optional[float] = None
        self.closed_at: Optional[datetime] = None
        self.close_reason: str = ''

    def close(self, exit_spot: float, exit_futures: float, reason: str) -> float:
        qty = self.position_usdt / self.entry_spot_price
        spot_pnl = qty * (exit_spot - self.entry_spot_price)
        futures_pnl = qty * (self.entry_futures_price - exit_futures)
        hours_held = (datetime.now() - self.opened_at).total_seconds() / 3600
        funding_periods = int(hours_held / 8)
        funding_income = self.position_usdt * self.funding_rate * funding_periods
        total_fees = (self.spot_taker_fee + self.futures_taker_fee) * self.position_usdt * 2
        profit = spot_pnl + futures_pnl + funding_income - total_fees
        self.exit_spot_price = exit_spot
        self.exit_futures_price = exit_futures
        self.exit_basis_percent = ((exit_futures - exit_spot) / exit_spot * 100) if exit_spot > 0 else 0.0
        self.actual_profit_usdt = profit
        self.status = 'closed'
        self.closed_at = datetime.now()
        self.close_reason = reason
        return profit

    def hours_open(self) -> float:
        return (datetime.now() - self.opened_at).total_seconds() / 3600
ArbitrageStrategy = str


class ArbitrageOpportunity:
    def __init__(
        self,
        strategy: ArbitrageStrategy,
        symbol: str,
        profit_usdt: float,
        profit_percent: float,
        position_size_usdt: float,
        details: StrategyDetails,
    ):
        self.id = f'{strategy}-{symbol}-{int(datetime.now().timestamp() * 1000)}'
        self.strategy = strategy
        self.symbol = symbol
        self.profit_usdt = profit_usdt
        self.profit_percent = profit_percent
        self.position_size_usdt = position_size_usdt
        self.details = details
        self.detected_at = datetime.now()

    def is_profitable(self, min_profit_percent: float) -> bool:
        return self.profit_percent >= min_profit_percent and self.profit_usdt > 0

    def __str__(self) -> str:
        return f'[{self.strategy.upper()}] {self.symbol} | +{self.profit_percent:.4f}% | ${self.profit_usdt:.4f}'


class VirtualTrade:
    def __init__(
        self,
        strategy: ArbitrageStrategy,
        symbol: str,
        position_size_usdt: float,
        expected_profit_usdt: float,
        expected_profit_percent: float,
        details: StrategyDetails,
    ):
        self.id = f'vtrade-{strategy}-{uuid.uuid4().hex[:8]}'
        self.strategy = strategy
        self.symbol = symbol
        self.position_size_usdt = position_size_usdt
        self.expected_profit_usdt = expected_profit_usdt
        self.expected_profit_percent = expected_profit_percent
        self.details = details
        self.status = 'open'
        self.actual_profit_usdt: Optional[float] = None
        self.notes: Optional[str] = None
        self.opened_at = datetime.now()
        self.closed_at: Optional[datetime] = None

    def close(self, actual_profit: float, notes: Optional[str] = None) -> None:
        self.actual_profit_usdt = actual_profit
        self.notes = notes
        self.status = 'closed'
        self.closed_at = datetime.now()

    def to_dict(self) -> dict:
        details = self.details.__dict__ if hasattr(self.details, '__dict__') else {}
        return {
            'id': self.id,
            'strategy': self.strategy,
            'symbol': self.symbol,
            'position_size_usdt': self.position_size_usdt,
            'expected_profit_usdt': self.expected_profit_usdt,
            'expected_profit_percent': self.expected_profit_percent,
            'actual_profit_usdt': self.actual_profit_usdt,
            'status': self.status,
            'notes': self.notes,
            'opened_at': self.opened_at.isoformat(),
            'closed_at': self.closed_at.isoformat() if self.closed_at else None,
            'details': details,
        }


class Portfolio:
    def __init__(self, initial_capital: float = 10_000.0):
        self._initial_capital = initial_capital
        self._trades: list[VirtualTrade] = []

    def add_trade(self, trade: VirtualTrade) -> None:
        self._trades.append(trade)

    @property
    def total_trades(self) -> int:
        return len(self._trades)

    @property
    def closed_trades(self) -> list[VirtualTrade]:
        return [t for t in self._trades if t.status == 'closed']

    @property
    def winning_trades(self) -> list[VirtualTrade]:
        return [t for t in self.closed_trades if (t.actual_profit_usdt or 0) > 0]

    @property
    def losing_trades(self) -> list[VirtualTrade]:
        return [t for t in self.closed_trades if (t.actual_profit_usdt or 0) <= 0]

    @property
    def total_profit_usdt(self) -> float:
        return sum(t.actual_profit_usdt or 0 for t in self.closed_trades)

    @property
    def total_expected_profit_usdt(self) -> float:
        return sum(t.expected_profit_usdt for t in self._trades)

    @property
    def win_rate(self) -> float:
        if not self.closed_trades:
            return 0.0
        return (len(self.winning_trades) / len(self.closed_trades)) * 100

    @property
    def average_profit_percent(self) -> float:
        if not self.closed_trades:
            return 0.0
        return sum(t.expected_profit_percent for t in self.closed_trades) / len(self.closed_trades)

    @property
    def roi(self) -> float:
        if self._initial_capital == 0:
            return 0.0
        return (self.total_profit_usdt / self._initial_capital) * 100

    def profit_last_hour(self) -> float:
        cutoff = datetime.now() - timedelta(hours=1)
        return sum(
            t.actual_profit_usdt or 0
            for t in self.closed_trades
            if t.closed_at and t.closed_at >= cutoff
        )

    def profit_last_24h(self) -> float:
        cutoff = datetime.now() - timedelta(hours=24)
        return sum(
            t.actual_profit_usdt or 0
            for t in self.closed_trades
            if t.closed_at and t.closed_at >= cutoff
        )

    def get_stats_by_strategy(self) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        for trade in self.closed_trades:
            if trade.strategy not in stats:
                stats[trade.strategy] = {'count': 0, 'profit': 0.0}
            stats[trade.strategy]['count'] += 1
            stats[trade.strategy]['profit'] += trade.actual_profit_usdt or 0
        return stats
