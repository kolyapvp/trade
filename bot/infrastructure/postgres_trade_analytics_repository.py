from __future__ import annotations

from asyncpg import Connection, Pool

from ..domain.entities import ClosedTradeAnalytics
from ..domain.ports import ITradeAnalyticsRepository


class PostgresTradeAnalyticsRepository(ITradeAnalyticsRepository):
    def __init__(
        self,
        pool: Pool,
        trades_table: str = 'trade_closures',
        daily_table: str = 'trade_daily_profit',
    ):
        self._pool = pool
        self._trades_table = trades_table
        self._daily_table = daily_table

    async def initialize(self) -> None:
        await self._pool.execute(
            f'''
            CREATE TABLE IF NOT EXISTS {self._trades_table} (
                trade_id TEXT PRIMARY KEY,
                closed_day DATE NOT NULL,
                mode TEXT NOT NULL DEFAULT 'demo',
                strategy TEXT NOT NULL,
                route_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT '',
                buy_exchange TEXT NOT NULL DEFAULT '',
                sell_exchange TEXT NOT NULL DEFAULT '',
                spot_exchange TEXT NOT NULL DEFAULT '',
                futures_exchange TEXT NOT NULL DEFAULT '',
                position_usdt DOUBLE PRECISION NOT NULL,
                expected_profit_usdt DOUBLE PRECISION NOT NULL,
                expected_profit_percent DOUBLE PRECISION NOT NULL,
                realized_profit_usdt DOUBLE PRECISION NOT NULL,
                opened_at TIMESTAMP,
                closed_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            '''
        )
        await self._pool.execute(
            f"ALTER TABLE {self._trades_table} ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'demo'"
        )
        await self._pool.execute(
            f'''
            CREATE INDEX IF NOT EXISTS idx_{self._trades_table}_closed_day
            ON {self._trades_table} (closed_day)
            '''
        )
        await self._pool.execute(
            f'''
            CREATE INDEX IF NOT EXISTS idx_{self._trades_table}_symbol_closed_at
            ON {self._trades_table} (symbol, closed_at DESC)
            '''
        )
        await self._pool.execute(
            f'''
            CREATE INDEX IF NOT EXISTS idx_{self._trades_table}_route_closed_at
            ON {self._trades_table} (strategy, route_type, closed_at DESC)
            '''
        )
        await self._pool.execute(
            f'''
            CREATE TABLE IF NOT EXISTS {self._daily_table} (
                stat_date DATE NOT NULL,
                mode TEXT NOT NULL DEFAULT 'demo',
                strategy TEXT NOT NULL,
                route_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT '',
                buy_exchange TEXT NOT NULL DEFAULT '',
                sell_exchange TEXT NOT NULL DEFAULT '',
                spot_exchange TEXT NOT NULL DEFAULT '',
                futures_exchange TEXT NOT NULL DEFAULT '',
                profit_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,
                expected_profit_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,
                position_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,
                trades_count INTEGER NOT NULL DEFAULT 0,
                win_count INTEGER NOT NULL DEFAULT 0,
                loss_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (
                    stat_date,
                    mode,
                    strategy,
                    route_type,
                    symbol,
                    exchange,
                    buy_exchange,
                    sell_exchange,
                    spot_exchange,
                    futures_exchange
                )
            )
            '''
        )
        await self._ensure_daily_table_mode_dimension()
        await self._pool.execute(
            f'''
            CREATE INDEX IF NOT EXISTS idx_{self._daily_table}_stat_date
            ON {self._daily_table} (stat_date DESC)
            '''
        )
        await self._pool.execute(
            f'''
            CREATE INDEX IF NOT EXISTS idx_{self._daily_table}_symbol_date
            ON {self._daily_table} (symbol, stat_date DESC)
            '''
        )

    async def record_closed_trade(self, trade: ClosedTradeAnalytics) -> bool:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    f'''
                    INSERT INTO {self._trades_table} (
                        trade_id,
                        closed_day,
                        mode,
                        strategy,
                        route_type,
                        symbol,
                        exchange,
                        buy_exchange,
                        sell_exchange,
                        spot_exchange,
                        futures_exchange,
                        position_usdt,
                        expected_profit_usdt,
                        expected_profit_percent,
                        realized_profit_usdt,
                        opened_at,
                        closed_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                    ON CONFLICT (trade_id) DO NOTHING
                    RETURNING trade_id
                    ''',
                    trade.trade_id,
                    trade.closed_day,
                    trade.mode,
                    trade.strategy,
                    trade.route_type,
                    trade.symbol,
                    trade.exchange,
                    trade.buy_exchange,
                    trade.sell_exchange,
                    trade.spot_exchange,
                    trade.futures_exchange,
                    trade.position_usdt,
                    trade.expected_profit_usdt,
                    trade.expected_profit_percent,
                    trade.realized_profit_usdt,
                    trade.opened_at,
                    trade.closed_at,
                )
                if inserted is None:
                    return False
                await self._upsert_daily(conn, trade)
                return True

    async def backfill_closed_trades(self, trades: list[ClosedTradeAnalytics]) -> int:
        inserted = 0
        for trade in trades:
            if await self.record_closed_trade(trade):
                inserted += 1
        return inserted

    async def _upsert_daily(self, conn: Connection, trade: ClosedTradeAnalytics) -> None:
        await conn.execute(
            f'''
            INSERT INTO {self._daily_table} (
                stat_date,
                mode,
                strategy,
                route_type,
                symbol,
                exchange,
                buy_exchange,
                sell_exchange,
                spot_exchange,
                futures_exchange,
                profit_usdt,
                expected_profit_usdt,
                position_usdt,
                trades_count,
                win_count,
                loss_count,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 1, $14, $15, NOW())
            ON CONFLICT (
                stat_date,
                mode,
                strategy,
                route_type,
                symbol,
                exchange,
                buy_exchange,
                sell_exchange,
                spot_exchange,
                futures_exchange
            )
            DO UPDATE SET
                profit_usdt = {self._daily_table}.profit_usdt + EXCLUDED.profit_usdt,
                expected_profit_usdt = {self._daily_table}.expected_profit_usdt + EXCLUDED.expected_profit_usdt,
                position_usdt = {self._daily_table}.position_usdt + EXCLUDED.position_usdt,
                trades_count = {self._daily_table}.trades_count + 1,
                win_count = {self._daily_table}.win_count + EXCLUDED.win_count,
                loss_count = {self._daily_table}.loss_count + EXCLUDED.loss_count,
                updated_at = NOW()
            ''',
            trade.closed_day,
            trade.mode,
            trade.strategy,
            trade.route_type,
            trade.symbol,
            trade.exchange,
            trade.buy_exchange,
            trade.sell_exchange,
            trade.spot_exchange,
            trade.futures_exchange,
            trade.realized_profit_usdt,
            trade.expected_profit_usdt,
            trade.position_usdt,
            1 if trade.realized_profit_usdt > 0 else 0,
            1 if trade.realized_profit_usdt <= 0 else 0,
        )

    async def _ensure_daily_table_mode_dimension(self) -> None:
        mode_exists = await self._pool.fetchval(
            '''
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = $1
                  AND column_name = 'mode'
            )
            ''',
            self._daily_table,
        )
        if mode_exists:
            return

        temp_table = f'{self._daily_table}__mode_migration'
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f'''
                    CREATE TABLE {temp_table} (
                        stat_date DATE NOT NULL,
                        mode TEXT NOT NULL DEFAULT 'demo',
                        strategy TEXT NOT NULL,
                        route_type TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        exchange TEXT NOT NULL DEFAULT '',
                        buy_exchange TEXT NOT NULL DEFAULT '',
                        sell_exchange TEXT NOT NULL DEFAULT '',
                        spot_exchange TEXT NOT NULL DEFAULT '',
                        futures_exchange TEXT NOT NULL DEFAULT '',
                        profit_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,
                        expected_profit_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,
                        position_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,
                        trades_count INTEGER NOT NULL DEFAULT 0,
                        win_count INTEGER NOT NULL DEFAULT 0,
                        loss_count INTEGER NOT NULL DEFAULT 0,
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (
                            stat_date,
                            mode,
                            strategy,
                            route_type,
                            symbol,
                            exchange,
                            buy_exchange,
                            sell_exchange,
                            spot_exchange,
                            futures_exchange
                        )
                    )
                    '''
                )
                await conn.execute(
                    f'''
                    INSERT INTO {temp_table} (
                        stat_date,
                        mode,
                        strategy,
                        route_type,
                        symbol,
                        exchange,
                        buy_exchange,
                        sell_exchange,
                        spot_exchange,
                        futures_exchange,
                        profit_usdt,
                        expected_profit_usdt,
                        position_usdt,
                        trades_count,
                        win_count,
                        loss_count,
                        updated_at
                    )
                    SELECT
                        stat_date,
                        'demo' AS mode,
                        strategy,
                        route_type,
                        symbol,
                        exchange,
                        buy_exchange,
                        sell_exchange,
                        spot_exchange,
                        futures_exchange,
                        SUM(profit_usdt) AS profit_usdt,
                        SUM(expected_profit_usdt) AS expected_profit_usdt,
                        SUM(position_usdt) AS position_usdt,
                        SUM(trades_count) AS trades_count,
                        SUM(win_count) AS win_count,
                        SUM(loss_count) AS loss_count,
                        MAX(updated_at) AS updated_at
                    FROM {self._daily_table}
                    GROUP BY
                        stat_date,
                        strategy,
                        route_type,
                        symbol,
                        exchange,
                        buy_exchange,
                        sell_exchange,
                        spot_exchange,
                        futures_exchange
                    '''
                )
                await conn.execute(f'DROP TABLE {self._daily_table}')
                await conn.execute(f'ALTER TABLE {temp_table} RENAME TO {self._daily_table}')
