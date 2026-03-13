from __future__ import annotations

from datetime import datetime, timedelta

from .value_objects import OrderBook, Fee
from .ports import Ticker, FuturesTicker
from .entities import (
    ArbitrageOpportunity,
    CrossExchangeDetails,
    TriangularDetails,
    FuturesSpotDetails,
    FuturesFundingDetails,
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
        spot_price: float,
        futures_price: float,
        position_usdt: float,
        spot_fee: Fee,
        futures_fee: Fee,
    ) -> dict:
        if spot_price <= 0 or futures_price <= 0:
            return self._empty()
        basis = futures_price - spot_price
        basis_percent = (basis / spot_price * 100) if spot_price > 0 else 0.0

        qty = position_usdt / spot_price if spot_price > 0 else 0.0
        spot_fee_usdt = spot_fee.calculate(position_usdt)
        futures_fee_usdt = futures_fee.calculate(position_usdt)

        basis_profit = qty * basis
        total_fees = (spot_fee_usdt + futures_fee_usdt) * 2

        profit_usdt = basis_profit - total_fees
        profit_percent = (profit_usdt / position_usdt * 100) if position_usdt > 0 else 0.0

        return {
            'is_profitable': profit_usdt > 0,
            'profit_usdt': profit_usdt,
            'profit_percent': profit_percent,
            'basis': basis,
            'basis_percent': basis_percent,
            'basis_profit_usdt': basis_profit,
            'total_fees_usdt': total_fees,
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
        entry_spread_percent = (short_entry - long_entry) / long_entry * 100 if long_entry > 0 else 0.0
        exit_spread_percent = (short_exit - long_exit) / long_exit * 100 if long_exit > 0 else 0.0

        return {
            'is_profitable': profit_usdt > 0,
            'profit_usdt': profit_usdt,
            'profit_percent': profit_percent,
            'long_price': long_entry,
            'short_price': short_entry,
            'funding_rate_delta': funding_delta,
            'entry_spread_percent': entry_spread_percent,
            'exit_spread_percent': exit_spread_percent,
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
            'buy_price': 0.0,
            'sell_price': 0.0,
            'effective_qty': 0.0,
            'buy_fee_usdt': 0.0,
            'sell_fee_usdt': 0.0,
        }


class ArbitrageDetector:
    def __init__(self):
        self._calc = ProfitCalculator()

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
        spot_fee: 'Fee',
        futures_fee: 'Fee',
        position_usdt: float,
        min_profit_percent: float,
        long_only: bool = True,
    ) -> ArbitrageOpportunity | None:
        spot_entry = spot_ticker.ask or spot_ticker.last or spot_ticker.bid
        futures_entry = futures_ticker.bid or futures_ticker.last or futures_ticker.ask
        result = self._calc.calculate_futures_spot(
            spot_entry,
            futures_entry,
            position_usdt,
            spot_fee,
            futures_fee,
        )

        basis = result['basis']
        if long_only and basis < 0:
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
                spot_price=spot_entry,
                futures_price=futures_entry,
                funding_rate=futures_ticker.funding_rate,
                basis=result['basis'],
                basis_percent=result['basis_percent'],
                spot_taker_fee=spot_fee.taker,
                futures_taker_fee=futures_fee.taker,
            ),
        )

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
            ),
        )
