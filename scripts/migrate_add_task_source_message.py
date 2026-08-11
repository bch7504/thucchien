"""One-off migration: add tasks.source_message_id.

Why a hand-written script instead of Alembic: this project doesn't use a migration tool -
Base.metadata.create_all() (src/db/session.py::init_db, run on every app startup) only creates
tables that don't exist yet, it never ALTERs an existing table. A new column on an already-live
`tasks` table (dev DB, and Supabase in production if deployed) therefore has to be applied by hand,
exactly once. Idempotent (IF NOT EXISTS everywhere) so running it again is a no-op, not an error.

Test DB is unaffected: tests/conftest.py's _test_database fixture drops and recreates the whole
schema from the current models every session, so it always gets this column for free.

Usage:
    python scripts/migrate_add_task_source_message.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from src.db.session import engine  # noqa: E402

# Two separate statements, not one multi-statement string - asyncpg (the driver src/db/session.py
# uses for the app's async engine) doesn't reliably support several commands in a single execute().
DDL_STATEMENTS = [
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_message_id VARCHAR NULL",
    "CREATE INDEX IF NOT EXISTS ix_tasks_source_message_id ON tasks (source_message_id)",
]


async def main() -> None:
    async with engine.begin() as conn:
        for statement in DDL_STATEMENTS:
            await conn.execute(text(statement))
    print("tasks.source_message_id is present.")


if __name__ == "__main__":
    asyncio.run(main())
