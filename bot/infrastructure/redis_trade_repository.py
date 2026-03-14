from __future__ import annotations

import json
from datetime import datetime

from redis.asyncio import Redis

from ..domain.entities import OpenPositionSnapshot, VirtualTrade
from ..domain.ports import IOpenPositionStore, ITradeRepository


class RedisTradeRepository(ITradeRepository):
    def __init__(self, client: Redis, key: str = 'tradebot:trades'):
        self._client = client
        self._key = key

    async def save(self, trade: VirtualTrade) -> None:
        await self._client.hset(
            self._key,
            trade.id,
            json.dumps(trade.to_dict(), ensure_ascii=False),
        )

    async def get_all(self) -> list[dict]:
        raw = await self._client.hgetall(self._key)
        trades: list[dict] = []
        for payload in raw.values():
            try:
                trades.append(json.loads(payload))
            except Exception:
                continue
        trades.sort(key=lambda item: item.get('opened_at', ''))
        return trades


class RedisOpenPositionStore(IOpenPositionStore):
    def __init__(self, client: Redis, key: str = 'tradebot:open_positions'):
        self._client = client
        self._key = key

    async def save(self, snapshot: OpenPositionSnapshot) -> None:
        await self._client.hset(
            self._key,
            snapshot.symbol,
            json.dumps(self._serialize(snapshot), ensure_ascii=False),
        )

    async def delete(self, symbol: str) -> None:
        await self._client.hdel(self._key, symbol)

    async def get_all(self) -> list[OpenPositionSnapshot]:
        raw = await self._client.hgetall(self._key)
        snapshots: list[OpenPositionSnapshot] = []
        for payload in raw.values():
            try:
                snapshots.append(self._deserialize(json.loads(payload)))
            except Exception:
                continue
        snapshots.sort(key=lambda item: item.opened_at)
        return snapshots

    async def close(self) -> None:
        await self._client.aclose()

    def _serialize(self, snapshot: OpenPositionSnapshot) -> dict:
        return {
            'position_id': snapshot.position_id,
            'symbol': snapshot.symbol,
            'strategy': snapshot.strategy,
            'spot_exchange': snapshot.spot_exchange,
            'futures_exchange': snapshot.futures_exchange,
            'entry_spot_price': snapshot.entry_spot_price,
            'entry_futures_price': snapshot.entry_futures_price,
            'entry_basis_percent': snapshot.entry_basis_percent,
            'funding_rate': snapshot.funding_rate,
            'funding_rate_secondary': snapshot.funding_rate_secondary,
            'position_usdt': snapshot.position_usdt,
            'spot_taker_fee': snapshot.spot_taker_fee,
            'futures_taker_fee': snapshot.futures_taker_fee,
            'opened_at': snapshot.opened_at.isoformat(),
            'target_close_at': snapshot.target_close_at.isoformat() if snapshot.target_close_at else None,
            'spot_base_quantity': snapshot.spot_base_quantity,
            'futures_base_quantity': snapshot.futures_base_quantity,
            'spot_order_amount': snapshot.spot_order_amount,
            'futures_order_amount': snapshot.futures_order_amount,
            'entry_spot_cost_usdt': snapshot.entry_spot_cost_usdt,
            'entry_spot_fee_usdt': snapshot.entry_spot_fee_usdt,
            'entry_futures_cost_usdt': snapshot.entry_futures_cost_usdt,
            'entry_futures_fee_usdt': snapshot.entry_futures_fee_usdt,
            'expected_profit_usdt': snapshot.expected_profit_usdt,
            'expected_profit_percent': snapshot.expected_profit_percent,
        }

    def _deserialize(self, data: dict) -> OpenPositionSnapshot:
        return OpenPositionSnapshot(
            position_id=data['position_id'],
            symbol=data['symbol'],
            strategy=data.get('strategy', 'futures_spot'),
            spot_exchange=data['spot_exchange'],
            futures_exchange=data['futures_exchange'],
            entry_spot_price=float(data['entry_spot_price']),
            entry_futures_price=float(data['entry_futures_price']),
            entry_basis_percent=float(data['entry_basis_percent']),
            funding_rate=float(data['funding_rate']),
            funding_rate_secondary=float(data.get('funding_rate_secondary', 0.0)),
            position_usdt=float(data['position_usdt']),
            spot_taker_fee=float(data['spot_taker_fee']),
            futures_taker_fee=float(data['futures_taker_fee']),
            opened_at=datetime.fromisoformat(data['opened_at']),
            target_close_at=datetime.fromisoformat(data['target_close_at']) if data.get('target_close_at') else None,
            spot_base_quantity=float(data.get('spot_base_quantity', 0.0)),
            futures_base_quantity=float(data.get('futures_base_quantity', 0.0)),
            spot_order_amount=float(data.get('spot_order_amount', 0.0)),
            futures_order_amount=float(data.get('futures_order_amount', 0.0)),
            entry_spot_cost_usdt=float(data.get('entry_spot_cost_usdt', 0.0)),
            entry_spot_fee_usdt=float(data.get('entry_spot_fee_usdt', 0.0)),
            entry_futures_cost_usdt=float(data.get('entry_futures_cost_usdt', 0.0)),
            entry_futures_fee_usdt=float(data.get('entry_futures_fee_usdt', 0.0)),
            expected_profit_usdt=float(data.get('expected_profit_usdt', 0.0)),
            expected_profit_percent=float(data.get('expected_profit_percent', 0.0)),
        )
