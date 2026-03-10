from __future__ import annotations

from .value_objects import OrderBook, Fee
from .ports import Ticker, FuturesTicker
from .entities import (
    ArbitrageOpportunity,
    CrossExchangeDetails,
    TriangularDetails,
    FuturesSpotDetails,
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
        funding_rate: float,
        position_usdt: float,
        spot_fee: Fee,
        futures_fee: Fee,
    ) -> dict:
        basis = futures_price - spot_price
        basis_percent = (basis / spot_price * 100) if spot_price > 0 else 0.0

        qty = position_usdt / spot_price if spot_price > 0 else 0.0
        spot_fee_usdt = spot_fee.calculate(position_usdt)
        futures_fee_usdt = futures_fee.calculate(position_usdt)

        funding_income = position_usdt * abs(funding_rate)
        basis_profit = qty * abs(basis)
        total_fees = spot_fee_usdt + futures_fee_usdt

        profit_usdt = basis_profit + funding_income - total_fees
        profit_percent = (profit_usdt / position_usdt * 100) if position_usdt > 0 else 0.0

        return {
            'profit_usdt': profit_usdt,
            'profit_percent': profit_percent,
            'basis': basis,
            'basis_percent': basis_percent,
        }

    def _empty(self) -> dict:
        return {
            'is_profitable': False,
            'profit_usdt': 0.0,
            'profit_percent': 0.0,
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
        exchange_id: str,
        symbol: str,
        spot_ticker: Ticker,
        futures_ticker: FuturesTicker,
        fee: 'Fee',
        position_usdt: float,
        min_profit_percent: float,
    ) -> ArbitrageOpportunity | None:
        result = self._calc.calculate_futures_spot(
            spot_ticker.last,
            futures_ticker.last,
            futures_ticker.funding_rate,
            position_usdt,
            fee,
            fee,
        )
        if result['profit_percent'] < min_profit_percent:
            return None

        return ArbitrageOpportunity(
            strategy='futures_spot',
            symbol=symbol,
            profit_usdt=result['profit_usdt'],
            profit_percent=result['profit_percent'],
            position_size_usdt=position_usdt,
            details=FuturesSpotDetails(
                exchange=exchange_id,
                symbol=symbol,
                spot_price=spot_ticker.last,
                futures_price=futures_ticker.last,
                funding_rate=futures_ticker.funding_rate,
                basis=result['basis'],
                basis_percent=result['basis_percent'],
            ),
        )
