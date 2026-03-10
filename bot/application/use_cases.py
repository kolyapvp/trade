from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..domain.entities import ArbitrageOpportunity, VirtualTrade, Portfolio
from ..domain.ports import IExchange, ITradeRepository, Ticker, FuturesTicker
from ..domain.services import ArbitrageDetector
from ..domain.value_objects import OrderBook


@dataclass
class TriangularPathConfig:
    exchange: str
    pairs: list[str]
    coins: list[str]


@dataclass
class ScanConfig:
    symbols: list[str]
    position_size_usdt: float
    min_profit_percent: float
    triangular_paths: list[TriangularPathConfig]
    enable_cross_exchange: bool = True
    enable_triangular: bool = True
    enable_futures_spot: bool = True
    futures_spot_long_only: bool = True


@dataclass
class ScanResult:
    opportunities: list[ArbitrageOpportunity]
    scanned_at: datetime
    duration_ms: int
    errors: list[str]


class ScanOpportunitiesUseCase:
    def __init__(self, spot_exchanges: list[IExchange], futures_exchanges: list[IExchange]):
        self._spot = spot_exchanges
        self._futures = futures_exchanges
        self._detector = ArbitrageDetector()

    async def execute(self, cfg: ScanConfig) -> ScanResult:
        start = datetime.now()
        opportunities: list[ArbitrageOpportunity] = []
        errors: list[str] = []
        exchange_data: list[dict] = []

        async def load_exchange(exchange: IExchange) -> None:
            books: dict[str, OrderBook] = {}
            tickers: dict[str, Ticker] = {}

            async def load_symbol(symbol: str) -> None:
                try:
                    book, ticker = await asyncio.gather(
                        exchange.fetch_order_book(symbol, 20),
                        exchange.fetch_ticker(symbol),
                    )
                    books[symbol] = book
                    tickers[symbol] = ticker
                except Exception as e:
                    errors.append(f'{exchange.info.id} {symbol}: {e}')

            await asyncio.gather(*[load_symbol(s) for s in cfg.symbols], return_exceptions=True)

            if books:
                exchange_data.append({
                    'exchange_id': exchange.info.id,
                    'fee': exchange.info.fee,
                    'books': books,
                    'tickers': tickers,
                })

        await asyncio.gather(*[load_exchange(ex) for ex in self._spot], return_exceptions=True)

        if cfg.enable_cross_exchange and len(exchange_data) >= 2:
            for symbol in cfg.symbols:
                found = self._detector.detect_cross_exchange(
                    exchange_data, symbol, cfg.position_size_usdt, cfg.min_profit_percent
                )
                opportunities.extend(found)

        if cfg.enable_triangular:
            for ex_data in exchange_data:
                path_cfgs = [
                    {'exchange': p.exchange, 'pairs': p.pairs, 'coins': p.coins}
                    for p in cfg.triangular_paths
                ]
                found = self._detector.detect_triangular(
                    ex_data['exchange_id'],
                    ex_data['fee'],
                    ex_data['tickers'],
                    path_cfgs,
                    cfg.position_size_usdt,
                    cfg.min_profit_percent,
                )
                opportunities.extend(found)

        if cfg.enable_futures_spot and self._futures:
            futures_tickers_cache: dict[str, dict[str, FuturesTicker]] = {}
            for futures_ex in self._futures:
                cache: dict[str, FuturesTicker] = {}
                for symbol in cfg.symbols:
                    try:
                        ft = await futures_ex.fetch_futures_ticker(symbol)
                        if ft:
                            cache[symbol] = ft
                    except Exception as e:
                        errors.append(f'futures {futures_ex.info.id} {symbol}: {e}')
                if cache:
                    futures_tickers_cache[futures_ex.info.id] = cache

            best_per_symbol: dict[str, ArbitrageOpportunity] = {}

            for spot_ex in self._spot:
                spot_data = next((d for d in exchange_data if d['exchange_id'] == spot_ex.info.id), None)
                if not spot_data:
                    continue

                for futures_ex in self._futures:
                    ftickers = futures_tickers_cache.get(futures_ex.info.id, {})

                    for symbol in cfg.symbols:
                        spot_ticker = spot_data['tickers'].get(symbol)
                        futures_ticker = ftickers.get(symbol)
                        if not spot_ticker or not futures_ticker:
                            continue
                        try:
                            opp = self._detector.detect_futures_spot(
                                spot_ex.info.id,
                                futures_ex.info.id,
                                symbol,
                                spot_ticker,
                                futures_ticker,
                                spot_ex.info.fee,
                                futures_ex.info.fee,
                                cfg.position_size_usdt,
                                cfg.min_profit_percent,
                                long_only=cfg.futures_spot_long_only,
                            )
                            if opp:
                                prev = best_per_symbol.get(symbol)
                                if prev is None or opp.profit_percent > prev.profit_percent:
                                    best_per_symbol[symbol] = opp
                        except Exception as e:
                            errors.append(f'futures-spot {spot_ex.info.id}×{futures_ex.info.id} {symbol}: {e}')

            opportunities.extend(best_per_symbol.values())

        opportunities.sort(key=lambda o: o.profit_percent, reverse=True)

        duration = int((datetime.now() - start).total_seconds() * 1000)
        return ScanResult(
            opportunities=opportunities,
            scanned_at=datetime.now(),
            duration_ms=duration,
            errors=errors,
        )


class ExecuteDemoTradeUseCase:
    def __init__(self, repository: ITradeRepository, portfolio: Portfolio):
        self._repo = repository
        self._portfolio = portfolio

    async def execute(self, opp: ArbitrageOpportunity) -> VirtualTrade:
        trade = VirtualTrade(
            strategy=opp.strategy,
            symbol=opp.symbol,
            position_size_usdt=opp.position_size_usdt,
            expected_profit_usdt=opp.profit_usdt,
            expected_profit_percent=opp.profit_percent,
            details=opp.details,
        )
        await self._repo.save(trade)
        self._portfolio.add_trade(trade)

        slippage = 0.0002
        actual_profit = opp.profit_usdt * (1 - slippage)
        trade.close(actual_profit, 'Demo: closed with 0.02% slippage')
        await self._repo.save(trade)
        return trade


@dataclass
class SessionStats:
    total_trades: int
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_profit_usdt: float
    total_expected_profit_usdt: float
    average_profit_percent: float
    roi: float
    profit_last_hour: float
    profit_last_24h: float
    by_strategy: dict[str, dict]
    best_trade: Optional[dict]
    worst_trade: Optional[dict]


class GenerateReportUseCase:
    def __init__(self, repository: ITradeRepository, portfolio: Portfolio):
        self._repo = repository
        self._portfolio = portfolio

    async def execute(self) -> SessionStats:
        closed = self._portfolio.closed_trades
        best = worst = None
        if closed:
            best_t = max(closed, key=lambda t: t.actual_profit_usdt or 0)
            worst_t = min(closed, key=lambda t: t.actual_profit_usdt or 0)
            best = {'symbol': best_t.symbol, 'profit': best_t.actual_profit_usdt or 0, 'strategy': best_t.strategy}
            worst = {'symbol': worst_t.symbol, 'profit': worst_t.actual_profit_usdt or 0, 'strategy': worst_t.strategy}

        return SessionStats(
            total_trades=self._portfolio.total_trades,
            closed_trades=len(closed),
            winning_trades=len(self._portfolio.winning_trades),
            losing_trades=len(self._portfolio.losing_trades),
            win_rate=self._portfolio.win_rate,
            total_profit_usdt=self._portfolio.total_profit_usdt,
            total_expected_profit_usdt=self._portfolio.total_expected_profit_usdt,
            average_profit_percent=self._portfolio.average_profit_percent,
            roi=self._portfolio.roi,
            profit_last_hour=self._portfolio.profit_last_hour(),
            profit_last_24h=self._portfolio.profit_last_24h(),
            by_strategy=self._portfolio.get_stats_by_strategy(),
            best_trade=best,
            worst_trade=worst,
        )
