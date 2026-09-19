"""Read-only connection to the existing assignment portal database.

Deliberately separate from `database.py`:

* its own engine and session factory, so portal queries never share a
  transaction with the bot's own writes;
* its own declarative base, so `init_db()` can never try to create portal
  tables inside the bot's database;
* every connection is put into a READ ONLY session by the server, so a write
  is refused by MariaDB itself rather than by convention.
"""

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.PORTAL_DATABASE_URL,
    pool_pre_ping=True,     # portal connections idle between questions
    pool_recycle=3600,      # stay under MySQL's wait_timeout
    echo=False,             # never echo portal SQL: it carries student data
)


@event.listens_for(engine, "connect")
def _enforce_read_only(dbapi_connection, connection_record) -> None:
    """Make every pooled connection refuse writes.

    Belt and braces alongside the database account's own privileges: an
    INSERT/UPDATE/DELETE that slips through fails with MariaDB error 1792
    ("Cannot execute statement in a READ ONLY transaction") instead of
    touching live portal data.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")

        # Bound how long one statement may run, the way MAX_RESULTS bounds how
        # many rows come back. Some portal tables hold millions of rows, and a
        # question that turns into a full scan would otherwise leave a chat
        # reply hanging until the client gives up. MariaDB kills the statement
        # and the caller gets an error it can report.
        timeout = settings.PORTAL_STATEMENT_TIMEOUT_SECONDS
        if timeout and timeout > 0:
            try:
                cursor.execute("SET SESSION max_statement_time=%s" % float(timeout))
            except Exception:  # noqa: BLE001
                # MySQL spells this max_execution_time, and older servers have
                # neither. The read-only guard above is the one that must hold.
                logger.debug("portal server has no max_statement_time; no query cap set")
    finally:
        cursor.close()


PortalSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class PortalBase(DeclarativeBase):
    """Base for portal models. Never passed to `create_all()`."""


def get_portal_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a portal session.

    Rolls back on the way out: nothing here is meant to write, and leaving a
    transaction open would hold a read view on a live table.
    """
    db = PortalSessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def check_portal_connection() -> bool:
    """True if the portal database answers. For diagnostics, not startup."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
