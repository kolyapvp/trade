from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class Fee:
    def __init__(self, maker: float, taker: float):
        self.maker = maker
        self.taker = taker

    def get_taker_percent(self) -> float:
        return self.taker * 100

    def calculate(self, amount: float, fee_type: Literal['maker', 'taker'] = 'taker') -> float:
        return amount * (self.taker if fee_type == 'taker' else self.maker)

    @staticmethod
    def binance() -> 'Fee':
        return Fee(0.001, 0.001)

    @staticmethod
    def bybit() -> 'Fee':
        return Fee(0.001, 0.001)

    @staticmethod
    def okx() -> 'Fee':
        return Fee(0.0008, 0.001)

    @staticmethod
    def kucoin() -> 'Fee':
        return Fee(0.001, 0.001)

    @staticmethod
    def gateio() -> 'Fee':
        return Fee(0.002, 0.002)

    @staticmethod
    def mexc() -> 'Fee':
        return Fee(0.0, 0.002)

    @staticmethod
    def bitget() -> 'Fee':
        return Fee(0.001, 0.001)

    @staticmethod
    def htx() -> 'Fee':
        return Fee(0.002, 0.002)

    @staticmethod
    def default() -> 'Fee':
        return Fee(0.001, 0.001)


@dataclass
class OrderBookLevel:
    price: float
    quantity: float


class OrderBook:
    def __init__(
        self,
        symbol: str,
        exchange_id: str,
        bids: list[OrderBookLevel],
        asks: list[OrderBookLevel],
        timestamp: int,
    ):
        self.symbol = symbol
        self.exchange_id = exchange_id
        self.bids = bids
        self.asks = asks
        self.timestamp = timestamp

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def best_ask_notional(self) -> float:
        if not self.asks:
            return 0.0
        return self.asks[0].price * self.asks[0].quantity

    @property
    def best_bid_notional(self) -> float:
        if not self.bids:
            return 0.0
        return self.bids[0].price * self.bids[0].quantity

    @property
    def spread(self) -> float:
        if not self.asks or not self.bids:
            return 0.0
        return max(self.best_ask - self.best_bid, 0.0)

    @property
    def spread_percent(self) -> float:
        if self.best_ask <= 0:
            return 0.0
        return self.spread / self.best_ask * 100

    def fill_buy_order(self, usdt_amount: float) -> dict:
        remaining = usdt_amount
        filled_qty = 0.0
        total_cost = 0.0
        for level in self.asks:
            if remaining <= 0:
                break
            level_cost = level.price * level.quantity
            if level_cost <= remaining:
                filled_qty += level.quantity
                total_cost += level_cost
                remaining -= level_cost
            else:
                qty = remaining / level.price
                filled_qty += qty
                total_cost += remaining
                remaining = 0.0
        avg_price = total_cost / filled_qty if filled_qty > 0 else 0.0
        return {'filled_qty': filled_qty, 'avg_price': avg_price, 'total_cost': total_cost}

    def fill_buy_quantity(self, qty: float) -> dict:
        remaining = qty
        filled_qty = 0.0
        total_cost = 0.0
        for level in self.asks:
            if remaining <= 0:
                break
            level_qty = min(level.quantity, remaining)
            filled_qty += level_qty
            total_cost += level.price * level_qty
            remaining -= level_qty
        avg_price = total_cost / filled_qty if filled_qty > 0 else 0.0
        return {'filled_qty': filled_qty, 'avg_price': avg_price, 'total_cost': total_cost}

    def fill_sell_order(self, qty: float) -> dict:
        remaining = qty
        filled_qty = 0.0
        total_revenue = 0.0
        for level in self.bids:
            if remaining <= 0:
                break
            if level.quantity <= remaining:
                filled_qty += level.quantity
                total_revenue += level.price * level.quantity
                remaining -= level.quantity
            else:
                filled_qty += remaining
                total_revenue += level.price * remaining
                remaining = 0.0
        avg_price = total_revenue / filled_qty if filled_qty > 0 else 0.0
        return {'filled_qty': filled_qty, 'avg_price': avg_price, 'total_revenue': total_revenue}
