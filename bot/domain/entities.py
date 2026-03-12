from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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


@dataclass
class FuturesFundingDetails:
    long_exchange: str
    short_exchange: str
    symbol: str
    long_price: float
    short_price: float
    long_funding_rate: float
    short_funding_rate: float
    funding_rate_delta: float
    entry_spread_percent: float
    exit_spread_percent: float
    target_funding_time: int = 0
    long_taker_fee: float = 0.0
    short_taker_fee: float = 0.0


StrategyDetails = Union[CrossExchangeDetails, TriangularDetails, FuturesSpotDetails, FuturesFundingDetails]


@dataclass(frozen=True)
class OpenPositionSnapshot:
    position_id: str
    symbol: str
    spot_exchange: str
    futures_exchange: str
    entry_spot_price: float
    entry_futures_price: float
    entry_basis_percent: float
    funding_rate: float
    position_usdt: float
    spot_taker_fee: float
    futures_taker_fee: float
    opened_at: datetime
    strategy: str = 'futures_spot'
    funding_rate_secondary: float = 0.0
    target_close_at: Optional[datetime] = None
    spot_base_quantity: float = 0.0
    futures_base_quantity: float = 0.0
    spot_order_amount: float = 0.0
    futures_order_amount: float = 0.0


@dataclass(frozen=True)
class ClosedTradeAnalytics:
    trade_id: str
    closed_day: date
    strategy: str
    route_type: str
    symbol: str
    position_usdt: float
    expected_profit_usdt: float
    expected_profit_percent: float
    realized_profit_usdt: float
    exchange: str = ''
    buy_exchange: str = ''
    sell_exchange: str = ''
    spot_exchange: str = ''
    futures_exchange: str = ''
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


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
        spot_base_quantity: float = 0.0,
        futures_base_quantity: float = 0.0,
        spot_order_amount: float = 0.0,
        futures_order_amount: float = 0.0,
        position_id: Optional[str] = None,
        opened_at: Optional[datetime] = None,
    ):
        now = datetime.now()
        implied_qty = position_usdt / entry_spot_price if entry_spot_price > 0 else 0.0
        self.id = position_id or f'pos-{symbol}-{int(now.timestamp() * 1000)}'
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
        self.spot_base_quantity = spot_base_quantity or implied_qty
        self.futures_base_quantity = futures_base_quantity or implied_qty
        self.spot_order_amount = spot_order_amount or self.spot_base_quantity
        self.futures_order_amount = futures_order_amount or self.futures_base_quantity
        self.opened_at = opened_at or now
        self.status = 'open'
        self.exit_spot_price: float = 0.0
        self.exit_futures_price: float = 0.0
        self.exit_basis_percent: float = 0.0
        self.actual_profit_usdt: Optional[float] = None
        self.closed_at: Optional[datetime] = None
        self.close_reason: str = ''

    def close(self, exit_spot: float, exit_futures: float, reason: str) -> float:
        spot_pnl = self.spot_base_quantity * (exit_spot - self.entry_spot_price)
        futures_pnl = self.futures_base_quantity * (self.entry_futures_price - exit_futures)
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

    def to_snapshot(self) -> OpenPositionSnapshot:
        return OpenPositionSnapshot(
            position_id=self.id,
            symbol=self.symbol,
            strategy='futures_spot',
            spot_exchange=self.spot_exchange,
            futures_exchange=self.futures_exchange,
            entry_spot_price=self.entry_spot_price,
            entry_futures_price=self.entry_futures_price,
            entry_basis_percent=self.entry_basis_percent,
            funding_rate=self.funding_rate,
            position_usdt=self.position_usdt,
            spot_taker_fee=self.spot_taker_fee,
            futures_taker_fee=self.futures_taker_fee,
            target_close_at=None,
            spot_base_quantity=self.spot_base_quantity,
            futures_base_quantity=self.futures_base_quantity,
            spot_order_amount=self.spot_order_amount,
            futures_order_amount=self.futures_order_amount,
            opened_at=self.opened_at,
        )

    @classmethod
    def from_snapshot(cls, snapshot: OpenPositionSnapshot) -> 'FuturesSpotPosition':
        return cls(
            symbol=snapshot.symbol,
            spot_exchange=snapshot.spot_exchange,
            futures_exchange=snapshot.futures_exchange,
            entry_spot_price=snapshot.entry_spot_price,
            entry_futures_price=snapshot.entry_futures_price,
            entry_basis_percent=snapshot.entry_basis_percent,
            funding_rate=snapshot.funding_rate,
            position_usdt=snapshot.position_usdt,
            spot_taker_fee=snapshot.spot_taker_fee,
            futures_taker_fee=snapshot.futures_taker_fee,
            spot_base_quantity=snapshot.spot_base_quantity,
            futures_base_quantity=snapshot.futures_base_quantity,
            spot_order_amount=snapshot.spot_order_amount,
            futures_order_amount=snapshot.futures_order_amount,
            position_id=snapshot.position_id,
            opened_at=snapshot.opened_at,
        )


class FuturesFundingPosition:
    MAX_HOLD_HOURS = 12

    def __init__(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
        entry_long_price: float,
        entry_short_price: float,
        long_funding_rate: float,
        short_funding_rate: float,
        position_usdt: float,
        long_taker_fee: float,
        short_taker_fee: float,
        target_close_at: Optional[datetime],
        long_base_quantity: float = 0.0,
        short_base_quantity: float = 0.0,
        long_order_amount: float = 0.0,
        short_order_amount: float = 0.0,
        position_id: Optional[str] = None,
        opened_at: Optional[datetime] = None,
    ):
        now = datetime.now()
        implied_qty = position_usdt / entry_long_price if entry_long_price > 0 else 0.0
        self.id = position_id or f'funding-{symbol}-{int(now.timestamp() * 1000)}'
        self.symbol = symbol
        self.long_exchange = long_exchange
        self.short_exchange = short_exchange
        self.entry_long_price = entry_long_price
        self.entry_short_price = entry_short_price
        self.long_funding_rate = long_funding_rate
        self.short_funding_rate = short_funding_rate
        self.position_usdt = position_usdt
        self.long_taker_fee = long_taker_fee
        self.short_taker_fee = short_taker_fee
        self.target_close_at = target_close_at
        self.long_base_quantity = long_base_quantity or implied_qty
        self.short_base_quantity = short_base_quantity or implied_qty
        self.long_order_amount = long_order_amount or self.long_base_quantity
        self.short_order_amount = short_order_amount or self.short_base_quantity
        self.opened_at = opened_at or now
        self.status = 'open'
        self.exit_long_price: float = 0.0
        self.exit_short_price: float = 0.0
        self.exit_spread_percent: float = 0.0
        self.actual_profit_usdt: Optional[float] = None
        self.closed_at: Optional[datetime] = None
        self.close_reason: str = ''

    @property
    def funding_rate_delta(self) -> float:
        return self.short_funding_rate - self.long_funding_rate

    @property
    def entry_spread_percent(self) -> float:
        if self.entry_long_price <= 0:
            return 0.0
        return (self.entry_short_price - self.entry_long_price) / self.entry_long_price * 100

    def close(self, exit_long: float, exit_short: float, reason: str) -> float:
        long_pnl = self.long_base_quantity * (exit_long - self.entry_long_price)
        short_pnl = self.short_base_quantity * (self.entry_short_price - exit_short)
        funding_income = self.position_usdt * self.funding_rate_delta if self._funding_applied() else 0.0
        total_fees = (self.long_taker_fee + self.short_taker_fee) * self.position_usdt * 2
        profit = long_pnl + short_pnl + funding_income - total_fees
        self.exit_long_price = exit_long
        self.exit_short_price = exit_short
        self.exit_spread_percent = ((exit_short - exit_long) / exit_long * 100) if exit_long > 0 else 0.0
        self.actual_profit_usdt = profit
        self.status = 'closed'
        self.closed_at = datetime.now()
        self.close_reason = reason
        return profit

    def hours_open(self) -> float:
        return (datetime.now() - self.opened_at).total_seconds() / 3600

    def to_snapshot(self) -> OpenPositionSnapshot:
        return OpenPositionSnapshot(
            position_id=self.id,
            symbol=self.symbol,
            strategy='futures_funding',
            spot_exchange=self.long_exchange,
            futures_exchange=self.short_exchange,
            entry_spot_price=self.entry_long_price,
            entry_futures_price=self.entry_short_price,
            entry_basis_percent=self.entry_spread_percent,
            funding_rate=self.long_funding_rate,
            funding_rate_secondary=self.short_funding_rate,
            position_usdt=self.position_usdt,
            spot_taker_fee=self.long_taker_fee,
            futures_taker_fee=self.short_taker_fee,
            target_close_at=self.target_close_at,
            spot_base_quantity=self.long_base_quantity,
            futures_base_quantity=self.short_base_quantity,
            spot_order_amount=self.long_order_amount,
            futures_order_amount=self.short_order_amount,
            opened_at=self.opened_at,
        )

    @classmethod
    def from_snapshot(cls, snapshot: OpenPositionSnapshot) -> 'FuturesFundingPosition':
        return cls(
            symbol=snapshot.symbol,
            long_exchange=snapshot.spot_exchange,
            short_exchange=snapshot.futures_exchange,
            entry_long_price=snapshot.entry_spot_price,
            entry_short_price=snapshot.entry_futures_price,
            long_funding_rate=snapshot.funding_rate,
            short_funding_rate=snapshot.funding_rate_secondary,
            position_usdt=snapshot.position_usdt,
            long_taker_fee=snapshot.spot_taker_fee,
            short_taker_fee=snapshot.futures_taker_fee,
            target_close_at=snapshot.target_close_at,
            long_base_quantity=snapshot.spot_base_quantity,
            short_base_quantity=snapshot.futures_base_quantity,
            long_order_amount=snapshot.spot_order_amount,
            short_order_amount=snapshot.futures_order_amount,
            position_id=snapshot.position_id,
            opened_at=snapshot.opened_at,
        )

    def _funding_applied(self) -> bool:
        if self.target_close_at is None:
            return self.hours_open() >= 8
        return datetime.now() >= self.target_close_at


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

    @classmethod
    def from_dict(cls, data: dict) -> 'VirtualTrade':
        trade = cls(
            strategy=data['strategy'],
            symbol=data['symbol'],
            position_size_usdt=float(data['position_size_usdt']),
            expected_profit_usdt=float(data['expected_profit_usdt']),
            expected_profit_percent=float(data['expected_profit_percent']),
            details=_parse_strategy_details(data['strategy'], data.get('details') or {}),
        )
        trade.id = data['id']
        trade.actual_profit_usdt = (
            float(data['actual_profit_usdt']) if data.get('actual_profit_usdt') is not None else None
        )
        trade.status = data.get('status', 'open')
        trade.notes = data.get('notes')
        trade.opened_at = datetime.fromisoformat(data['opened_at'])
        closed_at = data.get('closed_at')
        trade.closed_at = datetime.fromisoformat(closed_at) if closed_at else None
        return trade


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


def _parse_strategy_details(strategy: str, details: dict) -> StrategyDetails:
    if strategy == 'cross_exchange':
        return CrossExchangeDetails(
            buy_exchange=details.get('buy_exchange', ''),
            sell_exchange=details.get('sell_exchange', ''),
            buy_price=float(details.get('buy_price', 0.0)),
            sell_price=float(details.get('sell_price', 0.0)),
            buy_fee=float(details.get('buy_fee', 0.0)),
            sell_fee=float(details.get('sell_fee', 0.0)),
            max_qty=float(details.get('max_qty', 0.0)),
            symbol=details.get('symbol', ''),
        )
    if strategy == 'triangular':
        return TriangularDetails(
            exchange=details.get('exchange', ''),
            path=list(details.get('path', [])),
            start_amount=float(details.get('start_amount', 0.0)),
            end_amount=float(details.get('end_amount', 0.0)),
            fees=float(details.get('fees', 0.0)),
        )
    if strategy == 'futures_funding':
        return FuturesFundingDetails(
            long_exchange=details.get('long_exchange', ''),
            short_exchange=details.get('short_exchange', ''),
            symbol=details.get('symbol', ''),
            long_price=float(details.get('long_price', 0.0)),
            short_price=float(details.get('short_price', 0.0)),
            long_funding_rate=float(details.get('long_funding_rate', 0.0)),
            short_funding_rate=float(details.get('short_funding_rate', 0.0)),
            funding_rate_delta=float(details.get('funding_rate_delta', 0.0)),
            entry_spread_percent=float(details.get('entry_spread_percent', 0.0)),
            exit_spread_percent=float(details.get('exit_spread_percent', 0.0)),
            target_funding_time=int(details.get('target_funding_time', 0)),
            long_taker_fee=float(details.get('long_taker_fee', 0.0)),
            short_taker_fee=float(details.get('short_taker_fee', 0.0)),
        )
    return FuturesSpotDetails(
        spot_exchange=details.get('spot_exchange', ''),
        futures_exchange=details.get('futures_exchange', ''),
        symbol=details.get('symbol', ''),
        spot_price=float(details.get('spot_price', 0.0)),
        futures_price=float(details.get('futures_price', 0.0)),
        funding_rate=float(details.get('funding_rate', 0.0)),
        basis=float(details.get('basis', 0.0)),
        basis_percent=float(details.get('basis_percent', 0.0)),
        spot_taker_fee=float(details.get('spot_taker_fee', 0.0)),
        futures_taker_fee=float(details.get('futures_taker_fee', 0.0)),
    )
