import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


logger = logging.getLogger("uvicorn.error")


def apply_additive_migrations(engine: Engine) -> None:
    """Upgrade pre-auth SQLite databases without deleting or reassigning data."""

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if engine.dialect.name != "sqlite":
        logger.warning(
            "event=schema_migration_skipped reason=unsupported_dialect dialect=%s",
            engine.dialect.name,
        )
        return

    with engine.begin() as connection:
        if "users" in tables:
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            if "password_hash" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(512)"))

        if "chat_case_sessions" in tables:
            chat_columns = {
                column["name"] for column in inspector.get_columns("chat_case_sessions")
            }
            if "user_id" not in chat_columns:
                connection.execute(
                    text("ALTER TABLE chat_case_sessions ADD COLUMN user_id VARCHAR(36)")
                )
            if "is_demo" not in chat_columns:
                connection.execute(
                    text(
                        "ALTER TABLE chat_case_sessions "
                        "ADD COLUMN is_demo BOOLEAN NOT NULL DEFAULT 0"
                    )
                )

            # Existing demo records stay available for a future explicit demo
            # mode, but never enter a real user's private case list.
            connection.execute(
                text(
                    "UPDATE chat_case_sessions SET is_demo = 1 "
                    "WHERE case_id LIKE 'demo-%' AND user_id IS NULL"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chat_case_sessions_user_id "
                    "ON chat_case_sessions (user_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chat_case_sessions_is_demo "
                    "ON chat_case_sessions (is_demo)"
                )
            )

        if "cases" in tables:
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_cases_user_id ON cases (user_id)")
            )

        if "users" in tables:
            duplicate = connection.execute(
                text(
                    "SELECT lower(trim(email)) FROM users "
                    "GROUP BY lower(trim(email)) HAVING count(*) > 1 LIMIT 1"
                )
            ).first()
            if duplicate is None:
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_normalized_email "
                        "ON users (lower(trim(email)))"
                    )
                )
            else:
                logger.warning(
                    "event=normalized_email_index_skipped reason=legacy_duplicates"
                )
