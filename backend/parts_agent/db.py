"""Unified Database Access Layer supporting PostgreSQL and SQLite.

Supports:
- PostgreSQL via psycopg2 when DATABASE_URL or POSTGRES_URL is configured.
- Graceful, zero-downtime fallback to SQLite (parts_cache.db) if PostgreSQL is not configured or unavailable.
- Auto-adaptation of SQL parameter placeholders (? for SQLite, %s for PostgreSQL).
- Dict-like row access for both database engines.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

try:
    from dotenv import load_dotenv
    for _env_p in [
        Path(__file__).resolve().parent.parent / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]:
        if _env_p.exists():
            load_dotenv(_env_p, override=False)
except Exception:
    pass

log = logging.getLogger("db")

DEFAULT_SQLITE_PATH = Path(__file__).resolve().parent.parent / "data" / "parts_cache.db"
SQLITE_DB_PATH = Path(os.environ.get("METRICS_DB_PATH", str(DEFAULT_SQLITE_PATH)))

# Environment variable check for PostgreSQL
DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL")
    or os.environ.get("PG_URL")
)

# Optional flag to force postgres or sqlite
USE_POSTGRES_ENV = os.environ.get("USE_POSTGRES", "").lower() in ("1", "true", "yes")

_HAS_PSYCOPG2 = False
try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    pass


def is_postgres_configured() -> bool:
    """Check if PostgreSQL connection is configured and psycopg2 is available."""
    if not _HAS_PSYCOPG2:
        return False
    if os.environ.get("USE_POSTGRES", "").lower() in ("1", "true", "yes"):
        return True
    url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("PG_URL")
    )
    if url and ("postgres" in url or "psql" in url):
        return True
    return False


def get_postgres_url() -> str:
    """Retrieve normalized PostgreSQL connection URL or default unix-socket URL."""
    url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("PG_URL")
        or "postgresql://postgres@/parts_sourcing?host=/tmp"
    )
    # Convert postgres:// to postgresql:// for compatibility with psycopg2
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class DatabaseCursorWrapper:
    """Cursor wrapper providing consistent fetchall/fetchone behavior across drivers."""

    def __init__(self, raw_cursor, is_pg: bool = False):
        self.cursor = raw_cursor
        self.is_pg = is_pg

    def fetchone(self) -> Optional[Dict[str, Any]]:
        row = self.cursor.fetchone()
        if row is None:
            return None
        if self.is_pg:
            return dict(row)
        # SQLite Row
        return dict(row)

    def fetchall(self) -> List[Dict[str, Any]]:
        rows = self.cursor.fetchall()
        if not rows:
            return []
        return [dict(r) for r in rows]

    @property
    def rowcount(self) -> int:
        return self.cursor.rowcount

    @property
    def lastrowid(self) -> Optional[int]:
        if hasattr(self.cursor, "lastrowid"):
            return self.cursor.lastrowid
        return None


class DatabaseConnectionWrapper:
    """Connection wrapper providing uniform execute() and commit() methods."""

    def __init__(self, raw_conn, is_pg: bool = False):
        self.conn = raw_conn
        self.is_pg = is_pg

    def _adapt_sql(self, sql: str) -> str:
        if not self.is_pg:
            return sql
        # Adapt SQL for PostgreSQL:
        # 1. Convert SQLite strftime to PostgreSQL to_char
        sql = re.sub(
            r"strftime\('%Y-%m-%d %H:00',\s*datetime\((\w+),\s*'unixepoch'\)\)",
            r"to_char(to_timestamp(\1), 'YYYY-MM-DD HH24:00')",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"strftime\('%Y-%m-%d',\s*datetime\((\w+),\s*'unixepoch'\)\)",
            r"to_char(to_timestamp(\1), 'YYYY-MM-DD')",
            sql,
            flags=re.IGNORECASE,
        )

        # 2. Convert INSERT OR REPLACE for search_cache and url_cache
        if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+search_cache", sql, re.IGNORECASE):
            sql = re.sub(
                r"INSERT\s+OR\s+REPLACE\s+INTO\s+search_cache\s*\((.*?)\)\s*VALUES\s*\((.*?)\)",
                r"INSERT INTO search_cache (\1) VALUES (\2) ON CONFLICT (key) DO UPDATE SET vehicle_label = EXCLUDED.vehicle_label, part_name = EXCLUDED.part_name, data = EXCLUDED.data, created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at",
                sql,
                flags=re.IGNORECASE | re.DOTALL,
            )
        elif re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+url_cache", sql, re.IGNORECASE):
            sql = re.sub(
                r"INSERT\s+OR\s+REPLACE\s+INTO\s+url_cache\s*\((.*?)\)\s*VALUES\s*\((.*?)\)",
                r"INSERT INTO url_cache (\1) VALUES (\2) ON CONFLICT (vendor, vehicle_key, category) DO UPDATE SET direct_url = EXCLUDED.direct_url, created_at = EXCLUDED.created_at",
                sql,
                flags=re.IGNORECASE | re.DOTALL,
            )
        elif re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+metrics_search_requests", sql, re.IGNORECASE):
            sql = re.sub(
                r"INSERT\s+OR\s+REPLACE\s+INTO\s+metrics_search_requests\s*\((.*?)\)\s*VALUES\s*\((.*?)\)",
                r"INSERT INTO metrics_search_requests (\1) VALUES (\2) ON CONFLICT (request_id) DO UPDATE SET "
                r"status = EXCLUDED.status, total_latency_ms = EXCLUDED.total_latency_ms, "
                r"supplier_wall_clock_ms = EXCLUDED.supplier_wall_clock_ms, "
                r"total_products_discovered = EXCLUDED.total_products_discovered, "
                r"relevant_products_discovered = EXCLUDED.relevant_products_discovered, "
                r"irrelevant_products_filtered = EXCLUDED.irrelevant_products_filtered, "
                r"suppliers_count = EXCLUDED.suppliers_count, suppliers_succeeded = EXCLUDED.suppliers_succeeded, "
                r"suppliers_failed = EXCLUDED.suppliers_failed, suppliers_timed_out = EXCLUDED.suppliers_timed_out, "
                r"error_message = EXCLUDED.error_message",
                sql,
                flags=re.IGNORECASE | re.DOTALL,
            )
        elif re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+metrics_product_quality", sql, re.IGNORECASE):
            sql = re.sub(
                r"INSERT\s+OR\s+REPLACE\s+INTO\s+metrics_product_quality\s*\((.*?)\)\s*VALUES\s*\((.*?)\)",
                r"INSERT INTO metrics_product_quality (\1) VALUES (\2) ON CONFLICT (request_id) DO UPDATE SET "
                r"total_extracted = EXCLUDED.total_extracted, has_title_count = EXCLUDED.has_title_count, "
                r"has_price_count = EXCLUDED.has_price_count, has_part_number_count = EXCLUDED.has_part_number_count, "
                r"has_brand_count = EXCLUDED.has_brand_count, has_compatibility_count = EXCLUDED.has_compatibility_count, "
                r"has_stock_count = EXCLUDED.has_stock_count, has_url_count = EXCLUDED.has_url_count, "
                r"verified_fitment_count = EXCLUDED.verified_fitment_count, verified_stock_count = EXCLUDED.verified_stock_count, "
                r"relevant_count = EXCLUDED.relevant_count, irrelevant_count = EXCLUDED.irrelevant_count, "
                r"completeness_score = EXCLUDED.completeness_score, verification_coverage = EXCLUDED.verification_coverage",
                sql,
                flags=re.IGNORECASE | re.DOTALL,
            )
        elif re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", sql, re.IGNORECASE):
            sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.IGNORECASE)
            sql = sql.rstrip("; ") + " ON CONFLICT DO NOTHING"

        # 3. Handle duplicate quality metric insertions
        if re.search(r"INSERT\s+INTO\s+metrics_product_quality", sql, re.IGNORECASE) and "ON CONFLICT" not in sql:
            sql = sql.rstrip("; ") + " ON CONFLICT (request_id) DO UPDATE SET total_extracted = EXCLUDED.total_extracted, completeness_score = EXCLUDED.completeness_score, verification_coverage = EXCLUDED.verification_coverage"

        # 4. Convert datetime('now') to NOW()
        sql = re.sub(r"datetime\('now'\)", "NOW()", sql, flags=re.IGNORECASE)

        # 5. Convert ? parameter placeholders to %s for psycopg2
        sql = sql.replace("?", "%s")
        return sql

    def execute(self, sql: str, params: Optional[Union[List[Any], Tuple[Any, ...]]] = None) -> DatabaseCursorWrapper:
        adapted_sql = self._adapt_sql(sql)
        if self.is_pg:
            cur = self.conn.cursor()
            cur.execute(adapted_sql, params or ())
            return DatabaseCursorWrapper(cur, is_pg=True)
        else:
            if ";" in adapted_sql.strip().rstrip(";") and params is None:
                cur = self.conn.executescript(adapted_sql)
                return DatabaseCursorWrapper(cur, is_pg=False)
            cur = self.conn.execute(adapted_sql, params or ())
            return DatabaseCursorWrapper(cur, is_pg=False)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


@contextmanager
def get_db(db_path: Optional[Path] = None, force_sqlite: bool = False) -> Generator[DatabaseConnectionWrapper, None, None]:
    """Yield a wrapped database connection for PostgreSQL (if configured) or SQLite."""
    use_pg = is_postgres_configured() and not force_sqlite and db_path is None

    if use_pg:
        pg_url = get_postgres_url()
        pg_conn = None
        try:
            pg_conn = psycopg2.connect(
                pg_url,
                cursor_factory=psycopg2.extras.RealDictCursor,
                connect_timeout=10,
            )
        except Exception as e:
            log.warning("PostgreSQL connection to '%s' failed: %s. Falling back to SQLite.", pg_url, e)

        if pg_conn is not None:
            wrapper = DatabaseConnectionWrapper(pg_conn, is_pg=True)
            try:
                yield wrapper
                wrapper.commit()
            except Exception:
                wrapper.rollback()
                raise
            finally:
                wrapper.close()
            return

    # SQLite fallback / default
    path = db_path or SQLITE_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_conn = sqlite3.connect(str(path), timeout=20.0)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_conn.execute("PRAGMA journal_mode=WAL;")
    sqlite_conn.execute("PRAGMA synchronous=NORMAL;")
    wrapper = DatabaseConnectionWrapper(sqlite_conn, is_pg=False)
    try:
        yield wrapper
        wrapper.commit()
    except Exception:
        wrapper.rollback()
        raise
    finally:
        wrapper.close()


def get_db_status() -> Dict[str, Any]:
    """Return runtime diagnostic status about active database connection."""
    is_pg = is_postgres_configured()
    if is_pg:
        try:
            with get_db() as conn:
                res = conn.execute("SELECT version();").fetchone()
                return {
                    "engine": "postgresql",
                    "status": "connected",
                    "url": get_postgres_url().split("@")[-1],  # mask password
                    "version": list(res.values())[0] if res else "unknown",
                }
        except Exception as e:
            return {
                "engine": "postgresql",
                "status": "error",
                "error": str(e),
                "fallback": "sqlite",
            }
    return {
        "engine": "sqlite",
        "status": "connected",
        "path": str(SQLITE_DB_PATH),
    }
