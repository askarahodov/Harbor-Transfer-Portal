from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./data/harbor-transfer-portal.db"


def create_db_engine(database_url: str = DEFAULT_DATABASE_URL) -> Engine:
    if database_url.startswith("sqlite:///"):
        path = database_url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, future=True)

    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
