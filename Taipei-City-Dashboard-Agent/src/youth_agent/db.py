"""Async database layer backed by asyncpg connection pools.

Two pools are maintained as module-level singletons:

* **data_pool** — read-only access to *postgres-data* (youth_* tables).
* **manager_pool** — read-write access to *postgres-manager*
  (components, query_charts, component_charts).

Both databases live on the ``br_dashboard`` Docker network
(192.168.128.0/24).
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
data_pool: asyncpg.Pool | None = None
manager_pool: asyncpg.Pool | None = None


class DatabaseConnectionError(Exception):
    """Raised when a connection to a database cannot be established."""


class DatabaseQueryError(Exception):
    """Raised when a query fails after the connection was established."""


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def init_pools(config: object) -> None:
    """Create both connection pools from *config*.

    ``config`` must expose ``DB_DATA_DSN`` and ``DB_MANAGER_DSN`` as
    string attributes (e.g. the ``youth_agent.config`` module itself).
    """
    global data_pool, manager_pool

    dsn_data: str = getattr(config, "DB_DATA_DSN")
    dsn_manager: str = getattr(config, "DB_MANAGER_DSN")

    try:
        data_pool = await asyncpg.create_pool(
            dsn=dsn_data,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
        logger.info("data_pool connected to postgres-data")
    except (asyncpg.PostgresError, OSError) as exc:
        raise DatabaseConnectionError(
            f"Failed to connect to postgres-data: {exc}"
        ) from exc

    try:
        manager_pool = await asyncpg.create_pool(
            dsn=dsn_manager,
            min_size=1,
            max_size=5,
            command_timeout=60,
        )
        logger.info("manager_pool connected to postgres-manager")
    except (asyncpg.PostgresError, OSError) as exc:
        # Clean up the already-opened data pool before propagating.
        if data_pool is not None:
            await data_pool.close()
            data_pool = None
        raise DatabaseConnectionError(
            f"Failed to connect to postgres-manager: {exc}"
        ) from exc


async def close_pools() -> None:
    """Gracefully close both connection pools."""
    global data_pool, manager_pool

    for name, pool in [("data_pool", data_pool), ("manager_pool", manager_pool)]:
        if pool is not None:
            try:
                await pool.close()
                logger.info("%s closed", name)
            except Exception:
                logger.exception("Error closing %s", name)

    data_pool = None
    manager_pool = None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

async def execute_readonly(
    sql: str,
    params: list[Any] | None = None,
    timeout: str | None = None,
) -> list[asyncpg.Record]:
    """Run a read-only query on *data_pool* with an optional statement timeout.

    Parameters
    ----------
    sql:
        The SQL statement to execute.
    params:
        Positional parameters (``$1``, ``$2``, …).
    timeout:
        A PostgreSQL interval string such as ``'30s'`` or ``'5000ms'``.
        Applied via ``SET LOCAL statement_timeout`` so it only affects
        this transaction.

    Returns
    -------
    list[asyncpg.Record]
    """
    if data_pool is None:
        raise DatabaseConnectionError(
            "data_pool is not initialised — call init_pools() first"
        )

    try:
        async with data_pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                if timeout:
                    await conn.execute(
                        f"SET LOCAL statement_timeout = '{timeout}'"
                    )
                if params:
                    rows = await conn.fetch(sql, *params)
                else:
                    rows = await conn.fetch(sql)
                return rows
    except asyncpg.PostgresConnectionError as exc:
        raise DatabaseConnectionError(
            f"Connection lost during read query: {exc}"
        ) from exc
    except asyncpg.PostgresError as exc:
        raise DatabaseQueryError(
            f"Read query failed: {exc}"
        ) from exc


async def execute_write(
    sql: str,
    params: list[Any] | None = None,
) -> str:
    """Run a write query on *manager_pool* and return the command status.

    Parameters
    ----------
    sql:
        An INSERT / UPDATE / DELETE statement.
    params:
        Positional parameters (``$1``, ``$2``, …).

    Returns
    -------
    str
        The PostgreSQL command status tag, e.g. ``'INSERT 0 1'``.
    """
    if manager_pool is None:
        raise DatabaseConnectionError(
            "manager_pool is not initialised — call init_pools() first"
        )

    try:
        async with manager_pool.acquire() as conn:
            if params:
                status = await conn.execute(sql, *params)
            else:
                status = await conn.execute(sql)
            return status
    except asyncpg.PostgresConnectionError as exc:
        raise DatabaseConnectionError(
            f"Connection lost during write query: {exc}"
        ) from exc
    except asyncpg.PostgresError as exc:
        raise DatabaseQueryError(
            f"Write query failed: {exc}"
        ) from exc
