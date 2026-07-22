"""Azure SQL access. Uses Managed Identity (DefaultAzureCredential) when the
connection string requests AAD auth; otherwise connects with the connection
string as-is (e.g. SQL auth for local dev). No secrets/keys hardcoded.

Connections are reused per worker thread (not opened per request) and
transparently reconnected if the pooled connection has gone stale, so a busy
Function host isn't paying a full TCP+auth handshake on every query. All
queries are parameterized; query text (never parameter values, which carry
PHI) is logged for slow queries.
"""
import logging
import struct
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

import pyodbc
from azure.identity import DefaultAzureCredential

from . import config

# Driver-level connection pooling (this is pyodbc's default; set explicitly to
# document intent). Combined with the thread-local reuse below, connects are
# amortized rather than paid per request.
pyodbc.pooling = True

_SQL_COPT_SS_ACCESS_TOKEN = 1256
_AAD_TOKEN_SCOPE = "https://database.windows.net/.default"
_SLOW_QUERY_MS = 500
# SQLSTATE prefixes that mean "the connection itself is dead" — safe to drop
# the cached connection and retry once on a fresh one.
_CONNECTION_ERROR_SQLSTATES = ("08S01", "08001", "08003", "08004", "HYT00", "HY000")

_credential: DefaultAzureCredential | None = None
_local = threading.local()


class DatabaseError(Exception):
    """Wraps any pyodbc error so callers never see a raw driver exception
    (which can leak SQLSTATE/driver internals to an HTTP response)."""


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def _needs_aad_token(conn_str: str) -> bool:
    return "ActiveDirectoryMsi" in conn_str or "ActiveDirectoryDefault" in conn_str


def _new_connection() -> pyodbc.Connection:
    conn_str = config.SQL_CONNECTION_STRING
    if not conn_str:
        raise DatabaseError("SQL_CONNECTION_STRING is not configured")
    if _needs_aad_token(conn_str):
        token = _get_credential().get_token(_AAD_TOKEN_SCOPE).token.encode("utf-16-le")
        token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
        return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})
    return pyodbc.connect(conn_str)


def _thread_conn() -> pyodbc.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _new_connection()
        _local.conn = conn
    return conn


def _drop_thread_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def _is_connection_error(exc: pyodbc.Error) -> bool:
    sqlstate = exc.args[0] if exc.args else ""
    return any(str(sqlstate).startswith(s) for s in _CONNECTION_ERROR_SQLSTATES)


@contextmanager
def get_connection():
    """Yields the thread's reusable connection. Kept for internal use; callers
    should prefer query/execute/transaction."""
    yield _thread_conn()


def _log_slow(sql: str, elapsed_ms: float) -> None:
    if elapsed_ms >= _SLOW_QUERY_MS:
        # SQL text only — parameter values carry PHI and must never be logged.
        logging.warning("db: slow query %.0fms: %s", elapsed_ms, " ".join(sql.split())[:300])


def _run(sql: str, params: Iterable[Any], handler):
    """Executes on the thread connection with a single reconnect-and-retry if
    the connection has gone stale, wrapping driver errors as DatabaseError."""
    for attempt in (1, 2):
        conn = _thread_conn()
        started = time.perf_counter()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            result = handler(cursor, conn)
            _log_slow(sql, (time.perf_counter() - started) * 1000)
            return result
        except pyodbc.Error as exc:
            if attempt == 1 and _is_connection_error(exc):
                logging.info("db: stale connection, reconnecting and retrying once")
                _drop_thread_conn()
                continue
            # Log query text + SQLSTATE (no parameter values) then wrap.
            sqlstate = exc.args[0] if exc.args else "?"
            logging.error("db: query failed [%s]: %s", sqlstate, " ".join(sql.split())[:300])
            raise DatabaseError(f"Database operation failed ({sqlstate})") from exc


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    def handler(cursor, _conn):
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    return _run(sql, params, handler)


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Run an INSERT/UPDATE/DELETE. Returns rowcount."""

    def handler(cursor, conn):
        conn.commit()
        return cursor.rowcount

    return _run(sql, params, handler)


def execute_returning_id(sql: str, params: Iterable[Any] = ()) -> int:
    """Run an INSERT with OUTPUT INSERTED.id and return the new id."""

    def handler(cursor, conn):
        new_id = cursor.fetchone()[0]
        conn.commit()
        return new_id

    return _run(sql, params, handler)


class _Transaction:
    """Cursor wrapper for atomic multi-statement operations. All statements
    run on one connection and commit together at the end of the `with` block,
    or roll back entirely if the block raises."""

    def __init__(self, conn: pyodbc.Connection):
        self._conn = conn
        self._cursor = conn.cursor()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> "_Transaction":
        started = time.perf_counter()
        self._cursor.execute(sql, params)
        _log_slow(sql, (time.perf_counter() - started) * 1000)
        return self

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list:
        return self._cursor.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


@contextmanager
def transaction():
    """Usage:
        with db.transaction() as tx:
            tx.execute("UPDATE ...", (...))
            tx.execute("INSERT ...", (...))
    Commits on clean exit, rolls back on any exception."""
    conn = _thread_conn()
    tx = _Transaction(conn)
    try:
        yield tx
        conn.commit()
    except pyodbc.Error as exc:
        try:
            conn.rollback()
        except Exception:
            _drop_thread_conn()
        sqlstate = exc.args[0] if exc.args else "?"
        logging.error("db: transaction failed [%s], rolled back", sqlstate)
        raise DatabaseError(f"Transaction failed ({sqlstate})") from exc
    except Exception:
        try:
            conn.rollback()
        except Exception:
            _drop_thread_conn()
        raise
