from sqlalchemy import create_engine, inspect, text

from app.db.migrations import apply_additive_migrations
from app.db.session import Base


def test_legacy_sqlite_auth_migration_is_additive_and_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users ("
                "id VARCHAR(36) PRIMARY KEY, full_name VARCHAR(255) NOT NULL, "
                "email VARCHAR(255) NOT NULL UNIQUE, created_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE cases ("
                "id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36), title VARCHAR(255))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE chat_case_sessions ("
                "case_id VARCHAR(36) PRIMARY KEY, profile_data JSON NOT NULL, "
                "messages_data JSON NOT NULL, created_at DATETIME, updated_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO chat_case_sessions "
                "(case_id, profile_data, messages_data) VALUES "
                "('demo-existing', '{}', '[]'), ('legacy-existing', '{}', '[]')"
            )
        )

    # Application startup calls create_all first; ensure it safely skips the
    # existing old-version tables before the explicit additive upgrade runs.
    Base.metadata.create_all(bind=engine)
    apply_additive_migrations(engine)
    apply_additive_migrations(engine)

    db_inspector = inspect(engine)
    assert "auth_sessions" in db_inspector.get_table_names()
    assert "password_hash" in {
        column["name"] for column in db_inspector.get_columns("users")
    }
    assert {"user_id", "is_demo"}.issubset(
        {column["name"] for column in db_inspector.get_columns("chat_case_sessions")}
    )
    with engine.connect() as connection:
        rows = dict(
            connection.execute(
                text("SELECT case_id, is_demo FROM chat_case_sessions ORDER BY case_id")
            ).all()
        )
        assert bool(rows["demo-existing"]) is True
        assert bool(rows["legacy-existing"]) is False
        assert connection.execute(text("SELECT count(*) FROM chat_case_sessions")).scalar_one() == 2
