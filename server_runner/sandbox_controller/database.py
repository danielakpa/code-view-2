"""Database connection for the CodeView Sandbox Controller."""

import os

from collections.abc import Generator
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


# Locate CodeView's project directory.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment variables.
load_dotenv(BASE_DIR / ".env")


@lru_cache(maxsize=1)
def create_controller_session_factory():
    """
    Create one reusable database session factory.

    SQLAlchemy manages a connection pool behind the engine.

    PostgreSQL timeouts protect the Controller against:
    1. Long-running SQL statements.
    2. Excessive database lock waits.
    3. Sessions left idle inside transactions.
    """

    # Read the Controller's dedicated database URL.
    database_url = os.getenv(
        "CODEVIEW_CONTROLLER_DATABASE_URL"
    )

    # Reject missing configuration.
    if not database_url:
        raise RuntimeError(
            "CODEVIEW_CONTROLLER_DATABASE_URL is not configured."
        )

    # Configure PostgreSQL safety timeouts.
    #
    # statement_timeout:
    # Cancel statements running longer than 30 seconds.
    #
    # lock_timeout:
    # Cancel statements waiting more than 5 seconds
    # to acquire a database lock.
    #
    # idle_in_transaction_session_timeout:
    # Disconnect sessions that remain idle inside
    # a transaction for more than 60 seconds.
    postgres_options = (
        "-c statement_timeout=30000 "
        "-c lock_timeout=5000 "
        "-c idle_in_transaction_session_timeout=60000"
    )

    # Create the engine once per application process.
    engine = create_engine(
        database_url,

        # Check whether pooled connections are still alive.
        pool_pre_ping=True,

        # Maintain up to 5 regular connections.
        pool_size=5,

        # Allow up to 5 additional temporary connections.
        max_overflow=5,

        # Do not wait indefinitely for a pooled connection.
        pool_timeout=10,

        # Apply PostgreSQL timeout settings
        # whenever a new connection is established.
        connect_args={
            "options": postgres_options,
        },
    )

    # Create the reusable session factory.
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )


def get_controller_db() -> Generator[Session, None, None]:
    """Provide a database session for one Controller request."""

    # Reuse the existing session factory.
    session_factory = create_controller_session_factory()

    # Create a separate session for this request.
    db = session_factory()

    try:
        # Give the session to the endpoint.
        yield db

    finally:
        # Always close the session.
        db.close()