from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from bot.db.models import Base


def create_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url)
    if url.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_fk(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


NEW_COLUMNS = [
    ("products", "production_date", "DATE"),
    ("warehouses", "invite_token", "VARCHAR(32)"),
]


def _add_missing_columns(sync_conn) -> None:
    """create_all не меняет существующие таблицы — досоздаём новые колонки в старой базе."""
    insp = inspect(sync_conn)
    for table, column, sql_type in NEW_COLUMNS:
        if column not in {c["name"] for c in insp.get_columns(table)}:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
    sync_conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_warehouses_invite_token ON warehouses (invite_token)"
    ))


async def init_db(engine: AsyncEngine) -> async_sessionmaker:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    return async_sessionmaker(engine, expire_on_commit=False)
