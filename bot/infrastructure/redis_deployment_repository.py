from __future__ import annotations

from datetime import datetime

from redis.asyncio import Redis

from ..domain.ports import DeploymentState, IDeploymentStateRepository


class RedisDeploymentStateRepository(IDeploymentStateRepository):
    def __init__(self, client: Redis, key: str = 'tradebot:deployment'):
        self._client = client
        self._key = key

    async def get_state(self) -> DeploymentState:
        raw = await self._client.hgetall(self._key)
        requested_at_raw = raw.get('requested_at')
        requested_at = None
        if requested_at_raw:
            try:
                requested_at = datetime.fromisoformat(requested_at_raw)
            except ValueError:
                requested_at = None
        status = (raw.get('status') or 'active').strip().lower()
        if status not in {'active', 'draining'}:
            status = 'active'
        return DeploymentState(
            status=status,
            target_sha=raw.get('target_sha', ''),
            requested_at=requested_at,
            requested_by=raw.get('requested_by', ''),
        )
