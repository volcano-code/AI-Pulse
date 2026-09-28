from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import Settings

class Base(DeclarativeBase):
    pass

def make_database(settings: Settings):
    settings.prepare_sqlite_dir()
    kwargs = {"check_same_thread": False, "timeout": 20} if settings.database_url.startswith("sqlite") else {}
    engine = create_engine(settings.database_url, connect_args=kwargs, pool_pre_ping=True)
    if settings.database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_sqlite(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=20000")
            cursor.close()
    return engine, sessionmaker(engine, expire_on_commit=False)
