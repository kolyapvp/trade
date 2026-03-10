from __future__ import annotations

import json
from pathlib import Path

from ..domain.ports import ITradeRepository
from ..domain.entities import VirtualTrade


class FileTradeRepository(ITradeRepository):
    def __init__(self, file_path: str):
        self._path = Path(file_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def save(self, trade: VirtualTrade) -> None:
        trades = await self.get_all()
        trades = [t for t in trades if t.get('id') != trade.id]
        trades.append(trade.to_dict())
        self._path.write_text(json.dumps(trades, indent=2, ensure_ascii=False))

    async def get_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return []
