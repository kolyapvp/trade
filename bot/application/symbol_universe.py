from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..domain.ports import IExchange, MarketDescriptor


@dataclass(frozen=True)
class SymbolUniverseConfig:
    mode: str = 'dynamic'
    quote_currency: str = 'USDT'
    max_symbols: int = 30
    min_spot_exchanges: int = 1
    min_futures_exchanges: int = 1
    min_funding_exchanges: int = 2
    include_symbols: list[str] = field(default_factory=list)
    exclude_symbols: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SymbolUniverseResult:
    symbols: list[str]
    spot_symbols_by_exchange: dict[str, list[str]]
    futures_symbols_by_exchange: dict[str, list[str]]
    spot_support: dict[str, int]
    futures_support: dict[str, int]
    errors: list[str] = field(default_factory=list)


class SymbolUniverseBuilder:
    def __init__(self, config: SymbolUniverseConfig):
        self._config = config

    async def build(
        self,
        spot_exchanges: list[IExchange],
        futures_exchanges: list[IExchange],
        static_symbols: list[str],
        enable_cross_exchange: bool,
        enable_futures_spot: bool,
        enable_futures_funding: bool,
    ) -> SymbolUniverseResult:
        static = self._normalize_symbols(static_symbols)
        include = self._normalize_symbols(self._config.include_symbols)
        exclude = set(self._normalize_symbols(self._config.exclude_symbols))

        if self._config.mode == 'static':
            selected = [symbol for symbol in static if symbol not in exclude]
            for symbol in include:
                if symbol not in selected:
                    selected.append(symbol)
            return SymbolUniverseResult(
                symbols=selected,
                spot_symbols_by_exchange={exchange.info.id: list(selected) for exchange in spot_exchanges},
                futures_symbols_by_exchange={exchange.info.id: list(selected) for exchange in futures_exchanges},
                spot_support={},
                futures_support={},
                errors=[],
            )

        spot_markets_by_exchange, spot_errors = await self._load_market_sets(spot_exchanges, is_futures=False)
        futures_markets_by_exchange, futures_errors = await self._load_market_sets(futures_exchanges, is_futures=True)

        spot_support = self._count_support(spot_markets_by_exchange)
        futures_support = self._count_support(futures_markets_by_exchange)

        candidates: set[str] = set()
        if enable_cross_exchange:
            candidates.update(
                symbol for symbol, count in spot_support.items()
                if count >= 2
            )
        if enable_futures_spot:
            candidates.update(
                symbol for symbol in set(spot_support) | set(futures_support)
                if spot_support.get(symbol, 0) >= self._config.min_spot_exchanges
                and futures_support.get(symbol, 0) >= self._config.min_futures_exchanges
            )
        if enable_futures_funding:
            candidates.update(
                symbol for symbol, count in futures_support.items()
                if count >= self._config.min_funding_exchanges
            )

        selected = self._rank_symbols(
            candidates=candidates,
            static=static,
            include=include,
            exclude=exclude,
            spot_support=spot_support,
            futures_support=futures_support,
        )
        if not selected:
            fallback = [symbol for symbol in static if symbol not in exclude]
            for symbol in include:
                if symbol not in fallback:
                    fallback.append(symbol)
            selected = fallback

        selected_set = set(selected)
        return SymbolUniverseResult(
            symbols=selected,
            spot_symbols_by_exchange={
                exchange_id: sorted(symbols & selected_set)
                for exchange_id, symbols in spot_markets_by_exchange.items()
            },
            futures_symbols_by_exchange={
                exchange_id: sorted(symbols & selected_set)
                for exchange_id, symbols in futures_markets_by_exchange.items()
            },
            spot_support=spot_support,
            futures_support=futures_support,
            errors=spot_errors + futures_errors,
        )

    async def _load_market_sets(
        self,
        exchanges: list[IExchange],
        is_futures: bool,
    ) -> tuple[dict[str, set[str]], list[str]]:
        results = await asyncio.gather(
            *[exchange.list_markets() for exchange in exchanges],
            return_exceptions=True,
        )
        symbols_by_exchange: dict[str, set[str]] = {}
        errors: list[str] = []
        for exchange, result in zip(exchanges, results):
            if isinstance(result, Exception):
                errors.append(f'universe {exchange.info.id}: {type(result).__name__}: {result}')
                continue
            filtered = {
                descriptor.symbol
                for descriptor in result
                if self._is_supported_market(descriptor, is_futures)
            }
            symbols_by_exchange[exchange.info.id] = filtered
        return symbols_by_exchange, errors

    def _is_supported_market(self, descriptor: MarketDescriptor, is_futures: bool) -> bool:
        if not descriptor.active:
            return False
        if descriptor.quote.upper() != self._config.quote_currency:
            return False
        if is_futures:
            if not descriptor.contract:
                return False
            if not descriptor.swap:
                return False
            if not descriptor.linear:
                return False
            if descriptor.settle and descriptor.settle.upper() != self._config.quote_currency:
                return False
            return True
        return descriptor.spot and not descriptor.contract

    def _count_support(self, symbols_by_exchange: dict[str, set[str]]) -> dict[str, int]:
        support: dict[str, int] = {}
        for symbols in symbols_by_exchange.values():
            for symbol in symbols:
                support[symbol] = support.get(symbol, 0) + 1
        return support

    def _rank_symbols(
        self,
        candidates: set[str],
        static: list[str],
        include: list[str],
        exclude: set[str],
        spot_support: dict[str, int],
        futures_support: dict[str, int],
    ) -> list[str]:
        scored_candidates = [symbol for symbol in candidates if symbol not in exclude]
        scored_candidates.sort(
            key=lambda symbol: (
                -self._score_symbol(symbol, static, include, spot_support, futures_support),
                symbol,
            )
        )
        max_symbols = max(self._config.max_symbols, 0)
        selected = scored_candidates[:max_symbols] if max_symbols > 0 else scored_candidates
        for symbol in include:
            if symbol in exclude or symbol in selected:
                continue
            if symbol in spot_support or symbol in futures_support:
                selected.append(symbol)
        return selected

    def _score_symbol(
        self,
        symbol: str,
        static: list[str],
        include: list[str],
        spot_support: dict[str, int],
        futures_support: dict[str, int],
    ) -> int:
        score = futures_support.get(symbol, 0) * 100 + spot_support.get(symbol, 0) * 10
        if symbol in static:
            score += 1000
        if symbol in include:
            score += 2000
        return score

    def _normalize_symbols(self, symbols: list[str]) -> list[str]:
        normalized: list[str] = []
        for symbol in symbols:
            value = symbol.strip().upper()
            if not value or value in normalized:
                continue
            normalized.append(value)
        return normalized
