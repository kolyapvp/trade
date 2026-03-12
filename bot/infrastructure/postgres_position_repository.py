from __future__ import annotations

from asyncpg import Pool

from ..domain.entities import OpenPositionSnapshot
from ..domain.ports import IOpenPositionSnapshotRepository


class PostgresOpenPositionSnapshotRepository(IOpenPositionSnapshotRepository):
    def __init__(self, pool: Pool, table_name: str = 'open_positions'):
        self._pool = pool
        self._table_name = table_name

    async def initialize(self) -> None:
        await self._pool.execute(
            f'''
            CREATE TABLE IF NOT EXISTS {self._table_name} (
                symbol TEXT PRIMARY KEY,
                position_id TEXT NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'futures_spot',
                spot_exchange TEXT NOT NULL,
                futures_exchange TEXT NOT NULL,
                entry_spot_price DOUBLE PRECISION NOT NULL,
                entry_futures_price DOUBLE PRECISION NOT NULL,
                entry_basis_percent DOUBLE PRECISION NOT NULL,
                funding_rate DOUBLE PRECISION NOT NULL,
                funding_rate_secondary DOUBLE PRECISION NOT NULL DEFAULT 0,
                position_usdt DOUBLE PRECISION NOT NULL,
                spot_taker_fee DOUBLE PRECISION NOT NULL,
                futures_taker_fee DOUBLE PRECISION NOT NULL,
                spot_base_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                futures_base_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                spot_order_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                futures_order_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                opened_at TIMESTAMP NOT NULL,
                target_close_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            '''
        )
        await self._pool.execute(
            f"ALTER TABLE {self._table_name} ADD COLUMN IF NOT EXISTS strategy TEXT NOT NULL DEFAULT 'futures_spot'"
        )
        await self._pool.execute(
            f'ALTER TABLE {self._table_name} ADD COLUMN IF NOT EXISTS funding_rate_secondary DOUBLE PRECISION NOT NULL DEFAULT 0'
        )
        await self._pool.execute(
            f'ALTER TABLE {self._table_name} ADD COLUMN IF NOT EXISTS spot_base_quantity DOUBLE PRECISION NOT NULL DEFAULT 0'
        )
        await self._pool.execute(
            f'ALTER TABLE {self._table_name} ADD COLUMN IF NOT EXISTS futures_base_quantity DOUBLE PRECISION NOT NULL DEFAULT 0'
        )
        await self._pool.execute(
            f'ALTER TABLE {self._table_name} ADD COLUMN IF NOT EXISTS spot_order_amount DOUBLE PRECISION NOT NULL DEFAULT 0'
        )
        await self._pool.execute(
            f'ALTER TABLE {self._table_name} ADD COLUMN IF NOT EXISTS futures_order_amount DOUBLE PRECISION NOT NULL DEFAULT 0'
        )
        await self._pool.execute(
            f'ALTER TABLE {self._table_name} ADD COLUMN IF NOT EXISTS target_close_at TIMESTAMP'
        )

    async def upsert(self, snapshot: OpenPositionSnapshot) -> None:
        await self._pool.execute(
            f'''
            INSERT INTO {self._table_name} (
                symbol,
                position_id,
                strategy,
                spot_exchange,
                futures_exchange,
                entry_spot_price,
                entry_futures_price,
                entry_basis_percent,
                funding_rate,
                funding_rate_secondary,
                position_usdt,
                spot_taker_fee,
                futures_taker_fee,
                spot_base_quantity,
                futures_base_quantity,
                spot_order_amount,
                futures_order_amount,
                opened_at,
                target_close_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, NOW())
            ON CONFLICT (symbol) DO UPDATE SET
                position_id = EXCLUDED.position_id,
                strategy = EXCLUDED.strategy,
                spot_exchange = EXCLUDED.spot_exchange,
                futures_exchange = EXCLUDED.futures_exchange,
                entry_spot_price = EXCLUDED.entry_spot_price,
                entry_futures_price = EXCLUDED.entry_futures_price,
                entry_basis_percent = EXCLUDED.entry_basis_percent,
                funding_rate = EXCLUDED.funding_rate,
                funding_rate_secondary = EXCLUDED.funding_rate_secondary,
                position_usdt = EXCLUDED.position_usdt,
                spot_taker_fee = EXCLUDED.spot_taker_fee,
                futures_taker_fee = EXCLUDED.futures_taker_fee,
                spot_base_quantity = EXCLUDED.spot_base_quantity,
                futures_base_quantity = EXCLUDED.futures_base_quantity,
                spot_order_amount = EXCLUDED.spot_order_amount,
                futures_order_amount = EXCLUDED.futures_order_amount,
                opened_at = EXCLUDED.opened_at,
                target_close_at = EXCLUDED.target_close_at,
                updated_at = NOW()
            ''',
            snapshot.symbol,
            snapshot.position_id,
            snapshot.strategy,
            snapshot.spot_exchange,
            snapshot.futures_exchange,
            snapshot.entry_spot_price,
            snapshot.entry_futures_price,
            snapshot.entry_basis_percent,
            snapshot.funding_rate,
            snapshot.funding_rate_secondary,
            snapshot.position_usdt,
            snapshot.spot_taker_fee,
            snapshot.futures_taker_fee,
            snapshot.spot_base_quantity,
            snapshot.futures_base_quantity,
            snapshot.spot_order_amount,
            snapshot.futures_order_amount,
            snapshot.opened_at,
            snapshot.target_close_at,
        )

    async def replace_all(self, snapshots: list[OpenPositionSnapshot]) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f'DELETE FROM {self._table_name}')
                if not snapshots:
                    return
                await conn.executemany(
                    f'''
                    INSERT INTO {self._table_name} (
                        symbol,
                        position_id,
                        strategy,
                        spot_exchange,
                        futures_exchange,
                        entry_spot_price,
                        entry_futures_price,
                        entry_basis_percent,
                        funding_rate,
                        funding_rate_secondary,
                        position_usdt,
                        spot_taker_fee,
                        futures_taker_fee,
                        spot_base_quantity,
                        futures_base_quantity,
                        spot_order_amount,
                        futures_order_amount,
                        opened_at,
                        target_close_at,
                        updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, NOW())
                    ''',
                    [
                        (
                            snapshot.symbol,
                            snapshot.position_id,
                            snapshot.strategy,
                            snapshot.spot_exchange,
                            snapshot.futures_exchange,
                            snapshot.entry_spot_price,
                            snapshot.entry_futures_price,
                            snapshot.entry_basis_percent,
                            snapshot.funding_rate,
                            snapshot.funding_rate_secondary,
                            snapshot.position_usdt,
                            snapshot.spot_taker_fee,
                            snapshot.futures_taker_fee,
                            snapshot.spot_base_quantity,
                            snapshot.futures_base_quantity,
                            snapshot.spot_order_amount,
                            snapshot.futures_order_amount,
                            snapshot.opened_at,
                            snapshot.target_close_at,
                        )
                        for snapshot in snapshots
                    ],
                )

    async def delete(self, symbol: str) -> None:
        await self._pool.execute(
            f'DELETE FROM {self._table_name} WHERE symbol = $1',
            symbol,
        )

    async def get_all(self) -> list[OpenPositionSnapshot]:
        rows = await self._pool.fetch(
            f'''
            SELECT
                position_id,
                symbol,
                strategy,
                spot_exchange,
                futures_exchange,
                entry_spot_price,
                entry_futures_price,
                entry_basis_percent,
                funding_rate,
                funding_rate_secondary,
                position_usdt,
                spot_taker_fee,
                futures_taker_fee,
                spot_base_quantity,
                futures_base_quantity,
                spot_order_amount,
                futures_order_amount,
                opened_at,
                target_close_at
            FROM {self._table_name}
            ORDER BY opened_at ASC
            '''
        )
        return [
                OpenPositionSnapshot(
                    position_id=row['position_id'],
                    symbol=row['symbol'],
                    strategy=row['strategy'],
                    spot_exchange=row['spot_exchange'],
                    futures_exchange=row['futures_exchange'],
                    entry_spot_price=float(row['entry_spot_price']),
                    entry_futures_price=float(row['entry_futures_price']),
                    entry_basis_percent=float(row['entry_basis_percent']),
                    funding_rate=float(row['funding_rate']),
                    funding_rate_secondary=float(row['funding_rate_secondary']),
                    position_usdt=float(row['position_usdt']),
                    spot_taker_fee=float(row['spot_taker_fee']),
                    futures_taker_fee=float(row['futures_taker_fee']),
                    target_close_at=row['target_close_at'],
                    spot_base_quantity=float(row['spot_base_quantity']),
                    futures_base_quantity=float(row['futures_base_quantity']),
                    spot_order_amount=float(row['spot_order_amount']),
                    futures_order_amount=float(row['futures_order_amount']),
                opened_at=row['opened_at'],
            )
            for row in rows
        ]

    async def close(self) -> None:
        await self._pool.close()
