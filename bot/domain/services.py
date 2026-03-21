from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Optional

from .value_objects import OrderBook, Fee
from .ports import Ticker, FuturesTicker
from .entities import (
    ArbitrageOpportunity,
    CrossExchangeDetails,
    TriangularDetails,
    FuturesSpotDetails,
    FuturesFundingDetails,
    VirtualTrade,
)


@dataclass(frozen=True)
class FuturesSpotRiskConfig:
    book_depth_limit: int = 20
    min_top_level_notional_usdt: float = 150.0
    min_depth_ratio: float = 1.0
    max_spread_percent: float = 0.12
    close_reserve_scale: float = 1.0
    basis_history_window: int = 240
    basis_min_samples: int = 30
    min_basis_zscore: float = 1.2
    min_funding_rate: float = 0.0
    max_mark_price_deviation_percent: float = 0.25
    max_index_price_deviation_percent: float = 0.35
    route_history_size: int = 50
    route_min_closed_trades: int = 5
    route_min_win_rate: float = 0.4
    route_max_median_underperformance_usdt: float = 0.15
    route_max_p95_underperformance_usdt: float = 0.35


@dataclass(frozen=True)
class FuturesSpotBasisSnapshot:
    samples: int = 0
    mean: float = 0.0
    stddev: float = 0.0
    zscore: float = 0.0


@dataclass(frozen=True)
class FuturesSpotRouteQuality:
    trades_count: int = 0
    win_rate: float = 1.0
    median_underperformance_usdt: float = 0.0
    p95_underperformance_usdt: float = 0.0

    @property
    def has_history(self) -> bool:
        return self.trades_count > 0


class FuturesSpotBasisMonitor:
    def __init__(self, window_size: int):
        self._window_size = max(window_size, 1)
        self._history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self._window_size))

    def observe(self, route_key: str, basis_percent: float) -> FuturesSpotBasisSnapshot:
        history = self._history[route_key]
        snapshot = self._snapshot(list(history), basis_percent)
        history.append(basis_percent)
        return snapshot

    def _snapshot(self, values: list[float], current: float) -> FuturesSpotBasisSnapshot:
        if len(values) < 2:
            return FuturesSpotBasisSnapshot(samples=len(values))
        avg = mean(values)
        variance = mean([(value - avg) ** 2 for value in values])
        stddev = variance ** 0.5
        if stddev <= 1e-12:
            return FuturesSpotBasisSnapshot(samples=len(values), mean=avg, stddev=stddev, zscore=0.0)
        zscore = (current - avg) / stddev
        return FuturesSpotBasisSnapshot(samples=len(values), mean=avg, stddev=stddev, zscore=zscore)


class FuturesSpotRouteQualityMonitor:
    def __init__(self, history_size: int):
        self._history_size = max(history_size, 1)
        self._underperformance: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self._history_size))
        self._wins: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=self._history_size))

    @staticmethod
    def route_key(spot_exchange: str, futures_exchange: str, symbol: str) -> str:
        return f'{spot_exchange}->{futures_exchange}:{symbol}'

    def record_trade(self, trade: VirtualTrade) -> None:
        if trade.strategy != 'futures_spot':
            return
        details = trade.details
        assert isinstance(details, FuturesSpotDetails)
        actual_profit = trade.actual_profit_usdt
        if actual_profit is None:
            return
        route_key = self.route_key(details.spot_exchange, details.futures_exchange, trade.symbol)
        underperformance = max(trade.expected_profit_usdt - actual_profit, 0.0)
        self._underperformance[route_key].append(underperformance)
        self._wins[route_key].append(1 if actual_profit > 0 else 0)

    def bootstrap(self, trades: list[VirtualTrade]) -> None:
        for trade in trades:
            self.record_trade(trade)

    def get_quality(self, spot_exchange: str, futures_exchange: str, symbol: str) -> FuturesSpotRouteQuality:
        route_key = self.route_key(spot_exchange, futures_exchange, symbol)
        underperformance = list(self._underperformance.get(route_key, ()))
        wins = list(self._wins.get(route_key, ()))
        if not underperformance or not wins:
            return FuturesSpotRouteQuality(trades_count=max(len(underperformance), len(wins)))
        sorted_underperformance = sorted(underperformance)
        p95_index = min(max(int(len(sorted_underperformance) * 0.95) - 1, 0), len(sorted_underperformance) - 1)
        return FuturesSpotRouteQuality(
            trades_count=len(sorted_underperformance),
            win_rate=sum(wins) / len(wins),
            median_underperformance_usdt=median(sorted_underperformance),
            p95_underperformance_usdt=sorted_underperformance[p95_index],
        )


class ProfitCalculator:
    def calculate_cross_exchange(
        self,
        buy_book: OrderBook,
        sell_book: OrderBook,
        buy_fee: Fee,
        sell_fee: Fee,
        position_usdt: float,
    ) -> dict:
        buy = buy_book.fill_buy_order(position_usdt)
        if buy['filled_qty'] == 0:
            return self._empty()

        sell = sell_book.fill_sell_order(buy['filled_qty'])
        if sell['filled_qty'] == 0:
            return self._empty()

        qty = min(buy['filled_qty'], sell['filled_qty'])
        total_cost = qty * buy['avg_price']
        total_revenue = qty * sell['avg_price']

        buy_fee_usdt = buy_fee.calculate(total_cost)
        sell_fee_usdt = sell_fee.calculate(total_revenue)

        net_profit = total_revenue - total_cost - buy_fee_usdt - sell_fee_usdt
        profit_percent = (net_profit / total_cost * 100) if total_cost > 0 else 0.0

        return {
            'is_profitable': net_profit > 0,
            'profit_usdt': net_profit,
            'profit_percent': profit_percent,
            'buy_price': buy['avg_price'],
            'sell_price': sell['avg_price'],
            'effective_qty': qty,
            'buy_fee_usdt': buy_fee_usdt,
            'sell_fee_usdt': sell_fee_usdt,
        }

    def calculate_triangular(self, start_amount: float, rates: list[dict]) -> dict:
        path = [r['from'] for r in rates] + [rates[-1]['to']]
        amount = start_amount
        total_fees = 0.0

        for step in rates:
            fee = amount * (step['fee_percent'] / 100)
            total_fees += fee
            amount = (amount - fee) * step['rate']

        profit_usdt = amount - start_amount
        profit_percent = ((amount - start_amount) / start_amount * 100) if start_amount > 0 else 0.0

        return {
            'is_profitable': profit_usdt > 0,
            'profit_usdt': profit_usdt,
            'profit_percent': profit_percent,
            'path': path,
            'start_amount': start_amount,
            'end_amount': amount,
            'total_fees': total_fees,
        }

    def calculate_futures_spot(
        self,
        spot_book: OrderBook,
        futures_book: OrderBook,
        position_usdt: float,
        spot_fee: Fee,
        futures_fee: Fee,
        close_reserve_scale: float = 1.0,
    ) -> dict:
        if position_usdt <= 0:
            return self._empty()
        target_qty = position_usdt / spot_book.best_ask if spot_book.best_ask > 0 else 0.0
        if target_qty <= 0:
            return self._empty()

        spot_entry_budget = spot_book.fill_buy_order(position_usdt)
        if spot_entry_budget['filled_qty'] <= 0:
            return self._empty()

        futures_entry_budget = futures_book.fill_sell_order(target_qty)
        if futures_entry_budget['filled_qty'] <= 0:
            return self._empty()

        executable_qty = min(
            target_qty,
            spot_entry_budget['filled_qty'],
            futures_entry_budget['filled_qty'],
        )
        if executable_qty <= 0:
            return self._empty()

        spot_entry = spot_book.fill_buy_quantity(executable_qty)
        futures_entry = futures_book.fill_sell_order(executable_qty)
        if spot_entry['filled_qty'] <= 0 or futures_entry['filled_qty'] <= 0:
            return self._empty()

        spot_entry_price = spot_entry['avg_price']
        futures_entry_price = futures_entry['avg_price']
        if spot_entry_price <= 0 or futures_entry_price <= 0:
            return self._empty()

        basis = futures_entry_price - spot_entry_price
        basis_percent = (basis / spot_entry_price * 100) if spot_entry_price > 0 else 0.0

        entry_edge_usdt = futures_entry['total_revenue'] - spot_entry['total_cost']

        expected_spot_exit = executable_qty * spot_book.best_bid
        spot_exit = spot_book.fill_sell_order(executable_qty)
        expected_futures_exit = executable_qty * futures_book.best_ask
        futures_exit = futures_book.fill_buy_quantity(executable_qty)

        spot_exit_depth_impact = max(expected_spot_exit - spot_exit['total_revenue'], 0.0)
        futures_exit_depth_impact = max(futures_exit['total_cost'] - expected_futures_exit, 0.0)
        close_reserve_usdt = (
            (spot_book.spread + futures_book.spread) * executable_qty
            + spot_exit_depth_impact
            + futures_exit_depth_impact
        ) * max(close_reserve_scale, 0.0)

        entry_fees = (
            spot_fee.calculate(spot_entry['total_cost'])
            + futures_fee.calculate(futures_entry['total_revenue'])
        )
        exit_fees = (
            spot_fee.calculate(expected_spot_exit)
            + futures_fee.calculate(expected_futures_exit)
        )
        total_fees = entry_fees + exit_fees

        liquidity_ratio = min(
            spot_entry_budget['total_cost'] / position_usdt if position_usdt > 0 else 0.0,
            futures_entry_budget['filled_qty'] / target_qty if target_qty > 0 else 0.0,
        )
        profit_usdt = entry_edge_usdt - total_fees - close_reserve_usdt
        profit_percent = (profit_usdt / position_usdt * 100) if position_usdt > 0 else 0.0

        return {
            'is_profitable': profit_usdt > 0,
            'profit_usdt': profit_usdt,
            'profit_percent': profit_percent,
            'basis': basis,
            'basis_percent': basis_percent,
            'basis_profit_usdt': entry_edge_usdt,
            'total_fees_usdt': total_fees,
            'entry_quantity': executable_qty,
            'spot_price': spot_entry_price,
            'futures_price': futures_entry_price,
            'spot_spread_percent': spot_book.spread_percent,
            'futures_spread_percent': futures_book.spread_percent,
            'entry_edge_usdt': entry_edge_usdt,
            'close_reserve_usdt': close_reserve_usdt,
            'liquidity_ratio': liquidity_ratio,
        }

    def calculate_futures_funding(
        self,
        long_ticker: FuturesTicker,
        short_ticker: FuturesTicker,
        position_usdt: float,
        long_fee: Fee,
        short_fee: Fee,
    ) -> dict:
        long_entry = long_ticker.ask or long_ticker.last
        short_entry = short_ticker.bid or short_ticker.last
        long_exit = long_ticker.bid or long_ticker.last
        short_exit = short_ticker.ask or short_ticker.last
        if long_entry <= 0 or short_entry <= 0:
            return self._empty()

        funding_delta = short_ticker.funding_rate - long_ticker.funding_rate
        funding_income = position_usdt * funding_delta
        total_fees = (long_fee.calculate(position_usdt) + short_fee.calculate(position_usdt)) * 2
        profit_usdt = funding_income - total_fees
        profit_percent = (profit_usdt / position_usdt * 100) if position_usdt > 0 else 0.0
        total_taker_fee_percent = (total_fees / position_usdt * 100) if position_usdt > 0 else 0.0
        entry_spread_percent = (short_entry - long_entry) / long_entry * 100 if long_entry > 0 else 0.0
        exit_spread_percent = (short_exit - long_exit) / long_exit * 100 if long_exit > 0 else 0.0
        long_volume_usdt_24h = max(long_ticker.volume, 0.0) * max(long_ticker.last, 0.0)
        short_volume_usdt_24h = max(short_ticker.volume, 0.0) * max(short_ticker.last, 0.0)

        return {
            'is_profitable': profit_usdt > 0,
            'profit_usdt': profit_usdt,
            'profit_percent': profit_percent,
            'long_price': long_entry,
            'short_price': short_entry,
            'funding_rate_delta': funding_delta,
            'entry_spread_percent': entry_spread_percent,
            'exit_spread_percent': exit_spread_percent,
            'long_bid': long_ticker.bid or long_ticker.last,
            'long_ask': long_ticker.ask or long_ticker.last,
            'short_bid': short_ticker.bid or short_ticker.last,
            'short_ask': short_ticker.ask or short_ticker.last,
            'long_volume_usdt_24h': long_volume_usdt_24h,
            'short_volume_usdt_24h': short_volume_usdt_24h,
            'funding_income_usdt': funding_income,
            'total_fees_usdt': total_fees,
            'total_taker_fee_percent': total_taker_fee_percent,
        }

    def _empty(self) -> dict:
        return {
            'is_profitable': False,
            'profit_usdt': 0.0,
            'profit_percent': 0.0,
            'basis': 0.0,
            'basis_percent': 0.0,
            'basis_profit_usdt': 0.0,
            'total_fees_usdt': 0.0,
            'entry_quantity': 0.0,
            'spot_price': 0.0,
            'futures_price': 0.0,
            'spot_spread_percent': 0.0,
            'futures_spread_percent': 0.0,
            'entry_edge_usdt': 0.0,
            'close_reserve_usdt': 0.0,
            'liquidity_ratio': 0.0,
            'buy_price': 0.0,
            'sell_price': 0.0,
            'effective_qty': 0.0,
            'buy_fee_usdt': 0.0,
            'sell_fee_usdt': 0.0,
        }


class ArbitrageDetector:
    def __init__(
        self,
        futures_spot_risk: Optional[FuturesSpotRiskConfig] = None,
        futures_spot_basis_monitor: Optional[FuturesSpotBasisMonitor] = None,
        futures_spot_route_quality_monitor: Optional[FuturesSpotRouteQualityMonitor] = None,
    ):
        self._calc = ProfitCalculator()
        self._futures_spot_risk = futures_spot_risk or FuturesSpotRiskConfig()
        self._futures_spot_basis_monitor = (
            futures_spot_basis_monitor
            or FuturesSpotBasisMonitor(self._futures_spot_risk.basis_history_window)
        )
        self._futures_spot_route_quality_monitor = (
            futures_spot_route_quality_monitor
            or FuturesSpotRouteQualityMonitor(self._futures_spot_risk.route_history_size)
        )

    def detect_cross_exchange(
        self,
        exchanges: list[dict],
        symbol: str,
        position_usdt: float,
        min_profit_percent: float,
    ) -> list[ArbitrageOpportunity]:
        opportunities = []
        for i, buy_ex in enumerate(exchanges):
            for j, sell_ex in enumerate(exchanges):
                if i == j:
                    continue
                buy_book = buy_ex['books'].get(symbol)
                sell_book = sell_ex['books'].get(symbol)
                if not buy_book or not sell_book:
                    continue
                if buy_book.best_ask == 0 or sell_book.best_bid == 0:
                    continue
                if buy_book.best_ask >= sell_book.best_bid:
                    continue

                result = self._calc.calculate_cross_exchange(
                    buy_book, sell_book, buy_ex['fee'], sell_ex['fee'], position_usdt
                )
                if not result['is_profitable'] or result['profit_percent'] < min_profit_percent:
                    continue

                opportunities.append(ArbitrageOpportunity(
                    strategy='cross_exchange',
                    symbol=symbol,
                    profit_usdt=result['profit_usdt'],
                    profit_percent=result['profit_percent'],
                    position_size_usdt=position_usdt,
                    details=CrossExchangeDetails(
                        buy_exchange=buy_ex['exchange_id'],
                        sell_exchange=sell_ex['exchange_id'],
                        buy_price=result['buy_price'],
                        sell_price=result['sell_price'],
                        buy_fee=result['buy_fee_usdt'],
                        sell_fee=result['sell_fee_usdt'],
                        max_qty=result['effective_qty'],
                        symbol=symbol,
                    ),
                ))
        return opportunities

    def detect_triangular(
        self,
        exchange_id: str,
        fee: 'Fee',
        tickers: dict[str, Ticker],
        triangular_paths: list[dict],
        start_amount: float,
        min_profit_percent: float,
    ) -> list[ArbitrageOpportunity]:
        opportunities = []
        for path_cfg in triangular_paths:
            if path_cfg['exchange'] != exchange_id:
                continue

            rates = []
            valid = True
            for i, pair in enumerate(path_cfg['pairs']):
                ticker = tickers.get(pair)
                if not ticker or ticker.ask == 0:
                    valid = False
                    break
                from_coin = path_cfg['coins'][i]
                base_of_pair = pair.split('/')[0]
                rate = ticker.bid if from_coin == base_of_pair else 1.0 / ticker.ask
                rates.append({
                    'from': path_cfg['coins'][i],
                    'to': path_cfg['coins'][i + 1],
                    'rate': rate,
                    'fee_percent': fee.get_taker_percent(),
                })

            if not valid:
                continue

            result = self._calc.calculate_triangular(start_amount, rates)
            if not result['is_profitable'] or result['profit_percent'] < min_profit_percent:
                continue

            opportunities.append(ArbitrageOpportunity(
                strategy='triangular',
                symbol='→'.join(path_cfg['coins']),
                profit_usdt=result['profit_usdt'],
                profit_percent=result['profit_percent'],
                position_size_usdt=start_amount,
                details=TriangularDetails(
                    exchange=exchange_id,
                    path=result['path'],
                    start_amount=result['start_amount'],
                    end_amount=result['end_amount'],
                    fees=result['total_fees'],
                ),
            ))
        return opportunities

    def detect_futures_spot(
        self,
        spot_exchange_id: str,
        futures_exchange_id: str,
        symbol: str,
        spot_ticker: Ticker,
        futures_ticker: FuturesTicker,
        spot_book: OrderBook,
        futures_book: OrderBook,
        spot_fee: 'Fee',
        futures_fee: 'Fee',
        position_usdt: float,
        min_profit_percent: float,
        long_only: bool = True,
    ) -> ArbitrageOpportunity | None:
        if not self._passes_futures_spot_liquidity_filters(spot_book, futures_book):
            return None

        mark_price_deviation_percent = self._price_deviation_percent(
            futures_ticker.bid or futures_ticker.last,
            futures_ticker.mark_price,
        )
        if (
            futures_ticker.mark_price > 0
            and mark_price_deviation_percent > self._futures_spot_risk.max_mark_price_deviation_percent
        ):
            return None

        index_price_deviation_percent = self._price_deviation_percent(
            futures_ticker.bid or futures_ticker.last,
            futures_ticker.index_price,
        )
        if (
            futures_ticker.index_price > 0
            and index_price_deviation_percent > self._futures_spot_risk.max_index_price_deviation_percent
        ):
            return None

        result = self._calc.calculate_futures_spot(
            spot_book,
            futures_book,
            position_usdt,
            spot_fee,
            futures_fee,
            close_reserve_scale=self._futures_spot_risk.close_reserve_scale,
        )

        basis = result['basis']
        if long_only and basis < 0:
            return None
        if not self._passes_futures_spot_funding_filter(basis, futures_ticker.funding_rate, long_only):
            return None

        if result['liquidity_ratio'] < self._futures_spot_risk.min_depth_ratio:
            return None

        basis_snapshot = self._futures_spot_basis_monitor.observe(
            self._futures_spot_route_quality_monitor.route_key(spot_exchange_id, futures_exchange_id, symbol),
            result['basis_percent'],
        )
        zscore_value = basis_snapshot.zscore if long_only else abs(basis_snapshot.zscore)
        if (
            basis_snapshot.samples >= self._futures_spot_risk.basis_min_samples
            and zscore_value < self._futures_spot_risk.min_basis_zscore
        ):
            return None

        route_quality = self._futures_spot_route_quality_monitor.get_quality(
            spot_exchange_id,
            futures_exchange_id,
            symbol,
        )
        if self._is_low_quality_route(route_quality):
            return None

        if not result.get('is_profitable'):
            return None

        if result['profit_percent'] < min_profit_percent:
            return None

        return ArbitrageOpportunity(
            strategy='futures_spot',
            symbol=symbol,
            profit_usdt=result['profit_usdt'],
            profit_percent=result['profit_percent'],
            position_size_usdt=position_usdt,
            details=FuturesSpotDetails(
                spot_exchange=spot_exchange_id,
                futures_exchange=futures_exchange_id,
                symbol=symbol,
                spot_price=result['spot_price'],
                futures_price=result['futures_price'],
                funding_rate=futures_ticker.funding_rate,
                basis=result['basis'],
                basis_percent=result['basis_percent'],
                spot_taker_fee=spot_fee.taker,
                futures_taker_fee=futures_fee.taker,
                entry_quantity=result['entry_quantity'],
                spot_spread_percent=result['spot_spread_percent'],
                futures_spread_percent=result['futures_spread_percent'],
                entry_edge_usdt=result['entry_edge_usdt'],
                close_reserve_usdt=result['close_reserve_usdt'],
                basis_zscore=basis_snapshot.zscore,
                liquidity_ratio=result['liquidity_ratio'],
                mark_price_deviation_percent=mark_price_deviation_percent,
                index_price_deviation_percent=index_price_deviation_percent,
                route_win_rate=route_quality.win_rate,
                route_median_underperformance_usdt=route_quality.median_underperformance_usdt,
            ),
        )

    def record_futures_spot_trade(self, trade: VirtualTrade) -> None:
        self._futures_spot_route_quality_monitor.record_trade(trade)

    def bootstrap_futures_spot_trades(self, trades: list[VirtualTrade]) -> None:
        self._futures_spot_route_quality_monitor.bootstrap(trades)

    def _passes_futures_spot_liquidity_filters(self, spot_book: OrderBook, futures_book: OrderBook) -> bool:
        if spot_book.best_ask <= 0 or spot_book.best_bid <= 0:
            return False
        if futures_book.best_ask <= 0 or futures_book.best_bid <= 0:
            return False
        if spot_book.best_ask_notional < self._futures_spot_risk.min_top_level_notional_usdt:
            return False
        if spot_book.best_bid_notional < self._futures_spot_risk.min_top_level_notional_usdt:
            return False
        if futures_book.best_bid_notional < self._futures_spot_risk.min_top_level_notional_usdt:
            return False
        if futures_book.best_ask_notional < self._futures_spot_risk.min_top_level_notional_usdt:
            return False
        if spot_book.spread_percent > self._futures_spot_risk.max_spread_percent:
            return False
        if futures_book.spread_percent > self._futures_spot_risk.max_spread_percent:
            return False
        return True

    def _is_low_quality_route(self, route_quality: FuturesSpotRouteQuality) -> bool:
        if route_quality.trades_count < self._futures_spot_risk.route_min_closed_trades:
            return False
        if route_quality.win_rate < self._futures_spot_risk.route_min_win_rate:
            return True
        if (
            route_quality.median_underperformance_usdt
            > self._futures_spot_risk.route_max_median_underperformance_usdt
        ):
            return True
        if route_quality.p95_underperformance_usdt > self._futures_spot_risk.route_max_p95_underperformance_usdt:
            return True
        return False

    def _passes_futures_spot_funding_filter(
        self,
        basis: float,
        funding_rate: float,
        long_only: bool,
    ) -> bool:
        min_funding_rate = self._futures_spot_risk.min_funding_rate
        if min_funding_rate <= 0:
            return True
        if long_only or basis >= 0:
            return funding_rate >= min_funding_rate
        return funding_rate <= -min_funding_rate

    def _price_deviation_percent(self, execution_price: float, reference_price: float) -> float:
        if execution_price <= 0 or reference_price <= 0:
            return 0.0
        return abs(execution_price - reference_price) / reference_price * 100

    def detect_futures_funding(
        self,
        long_exchange_id: str,
        short_exchange_id: str,
        symbol: str,
        long_ticker: FuturesTicker,
        short_ticker: FuturesTicker,
        long_fee: Fee,
        short_fee: Fee,
        position_usdt: float,
        min_profit_percent: float,
    ) -> ArbitrageOpportunity | None:
        if long_exchange_id == short_exchange_id:
            return None
        result = self._calc.calculate_futures_funding(
            long_ticker,
            short_ticker,
            position_usdt,
            long_fee,
            short_fee,
        )
        if not result['is_profitable'] or result['profit_percent'] < min_profit_percent:
            return None
        if result['funding_rate_delta'] <= 0:
            return None
        if result['entry_spread_percent'] < 0:
            return None

        long_next = long_ticker.next_funding_time or 0
        short_next = short_ticker.next_funding_time or 0
        if long_next and short_next and abs(long_next - short_next) > 15 * 60 * 1000:
            return None
        target_funding_time = max(long_next, short_next)
        if target_funding_time == 0:
            target_funding_time = int((datetime.now() + timedelta(hours=8)).timestamp() * 1000)

        return ArbitrageOpportunity(
            strategy='futures_funding',
            symbol=symbol,
            profit_usdt=result['profit_usdt'],
            profit_percent=result['profit_percent'],
            position_size_usdt=position_usdt,
            details=FuturesFundingDetails(
                long_exchange=long_exchange_id,
                short_exchange=short_exchange_id,
                symbol=symbol,
                long_price=result['long_price'],
                short_price=result['short_price'],
                long_funding_rate=long_ticker.funding_rate,
                short_funding_rate=short_ticker.funding_rate,
                funding_rate_delta=result['funding_rate_delta'],
                entry_spread_percent=result['entry_spread_percent'],
                exit_spread_percent=result['exit_spread_percent'],
                target_funding_time=target_funding_time,
                long_taker_fee=long_fee.taker,
                short_taker_fee=short_fee.taker,
                long_bid=result['long_bid'],
                long_ask=result['long_ask'],
                short_bid=result['short_bid'],
                short_ask=result['short_ask'],
                long_volume_usdt_24h=result['long_volume_usdt_24h'],
                short_volume_usdt_24h=result['short_volume_usdt_24h'],
                funding_income_usdt=result['funding_income_usdt'],
                total_fees_usdt=result['total_fees_usdt'],
                total_taker_fee_percent=result['total_taker_fee_percent'],
            ),
        )
