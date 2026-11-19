"""Migration script: Migrate SQLite parts_cache.db records to PostgreSQL.

Usage:
    python3 backend/scripts/migrate_to_postgres.py [--sqlite PATH] [--postgres-url URL]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("Error: psycopg2 is required for PostgreSQL migration. Run 'pip3 install psycopg2-binary'")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("migrate_to_postgres")

DEFAULT_SQLITE_PATH = Path(__file__).resolve().parent.parent / "data" / "parts_cache.db"
DEFAULT_PG_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@/parts_sourcing?host=/tmp")


def migrate_search_cache(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    cur_sql = sqlite_conn.cursor()
    cur_sql.execute("SELECT key, vehicle_label, part_name, data, created_at, expires_at FROM search_cache")
    rows = cur_sql.fetchall()
    if not rows:
        return 0

    with pg_conn.cursor() as cur_pg:
        psycopg2.extras.execute_values(
            cur_pg,
            """
            INSERT INTO search_cache (key, vehicle_label, part_name, data, created_at, expires_at)
            VALUES %s
            ON CONFLICT (key) DO UPDATE SET
                vehicle_label = EXCLUDED.vehicle_label,
                part_name = EXCLUDED.part_name,
                data = EXCLUDED.data,
                created_at = EXCLUDED.created_at,
                expires_at = EXCLUDED.expires_at
            """,
            [
                (
                    r[0],
                    r[1],
                    r[2],
                    r[3] if isinstance(r[3], str) else json.dumps(r[3]),
                    float(r[4] or 0),
                    float(r[5] or 0),
                )
                for r in rows
            ],
        )
    pg_conn.commit()
    return len(rows)


def migrate_url_cache(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    cur_sql = sqlite_conn.cursor()
    cur_sql.execute("SELECT vendor, vehicle_key, category, direct_url, created_at FROM url_cache")
    rows = cur_sql.fetchall()
    if not rows:
        return 0

    with pg_conn.cursor() as cur_pg:
        psycopg2.extras.execute_values(
            cur_pg,
            """
            INSERT INTO url_cache (vendor, vehicle_key, category, direct_url, created_at)
            VALUES %s
            ON CONFLICT (vendor, vehicle_key, category) DO UPDATE SET
                direct_url = EXCLUDED.direct_url,
                created_at = EXCLUDED.created_at
            """,
            [(r[0], r[1], r[2], r[3], float(r[4] or 0)) for r in rows],
        )
    pg_conn.commit()
    return len(rows)


def migrate_search_requests(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    cur_sql = sqlite_conn.cursor()
    cur_sql.execute("""
        SELECT
            request_id, thread_id, timestamp, created_at, customer_query,
            part_name, vehicle_maker, vehicle_model, vehicle_year, vehicle_variant,
            vehicle_label, status, total_latency_ms, supplier_wall_clock_ms,
            total_products_discovered, relevant_products_discovered,
            irrelevant_products_filtered, suppliers_count, suppliers_succeeded,
            suppliers_failed, suppliers_timed_out, error_message, is_cached,
            is_benchmark
        FROM metrics_search_requests
    """)
    rows = cur_sql.fetchall()
    if not rows:
        return 0

    with pg_conn.cursor() as cur_pg:
        psycopg2.extras.execute_values(
            cur_pg,
            """
            INSERT INTO metrics_search_requests (
                request_id, thread_id, timestamp, created_at, customer_query,
                part_name, vehicle_maker, vehicle_model, vehicle_year, vehicle_variant,
                vehicle_label, status, total_latency_ms, supplier_wall_clock_ms,
                total_products_discovered, relevant_products_discovered,
                irrelevant_products_filtered, suppliers_count, suppliers_succeeded,
                suppliers_failed, suppliers_timed_out, error_message, is_cached,
                is_benchmark
            ) VALUES %s
            ON CONFLICT (request_id) DO NOTHING
            """,
            [
                (
                    r[0], r[1], float(r[2]), r[3], r[4],
                    r[5], r[6], r[7], r[8], r[9],
                    r[10], r[11], float(r[12] or 0), float(r[13] or 0),
                    int(r[14] or 0), int(r[15] or 0), int(r[16] or 0),
                    int(r[17] or 0), int(r[18] or 0), int(r[19] or 0),
                    int(r[20] or 0), r[21], int(r[22] or 0), int(r[23] or 0),
                )
                for r in rows
            ],
        )
    pg_conn.commit()
    return len(rows)


def migrate_supplier_executions(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    cur_sql = sqlite_conn.cursor()
    cur_sql.execute("""
        SELECT
            request_id, supplier_id, supplier_name, timestamp, status,
            latency_ms, products_discovered, products_extracted, browser_init_ms,
            navigation_ms, extraction_ms, verification_ms, error_category,
            error_message, source_url, is_benchmark
        FROM metrics_supplier_executions
    """)
    rows = cur_sql.fetchall()
    if not rows:
        return 0

    with pg_conn.cursor() as cur_pg:
        psycopg2.extras.execute_values(
            cur_pg,
            """
            INSERT INTO metrics_supplier_executions (
                request_id, supplier_id, supplier_name, timestamp, status,
                latency_ms, products_discovered, products_extracted, browser_init_ms,
                navigation_ms, extraction_ms, verification_ms, error_category,
                error_message, source_url, is_benchmark
            ) VALUES %s
            """,
            [
                (
                    r[0], r[1], r[2], float(r[3] or 0), r[4],
                    float(r[5] or 0), int(r[6] or 0), int(r[7] or 0), float(r[8] or 0),
                    float(r[9] or 0), float(r[10] or 0), float(r[11] or 0), r[12],
                    r[13], r[14], int(r[15] or 0),
                )
                for r in rows
            ],
        )
    pg_conn.commit()
    return len(rows)


def migrate_stage_timings(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    cur_sql = sqlite_conn.cursor()
    cur_sql.execute("""
        SELECT request_id, stage_name, duration_ms, details
        FROM metrics_stage_timings
    """)
    rows = cur_sql.fetchall()
    if not rows:
        return 0

    with pg_conn.cursor() as cur_pg:
        psycopg2.extras.execute_values(
            cur_pg,
            """
            INSERT INTO metrics_stage_timings (request_id, stage_name, duration_ms, details)
            VALUES %s
            """,
            [(r[0], r[1], float(r[2] or 0), r[3]) for r in rows],
        )
    pg_conn.commit()
    return len(rows)


def migrate_product_quality(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    cur_sql = sqlite_conn.cursor()
    cur_sql.execute("""
        SELECT
            request_id, total_extracted, has_title_count, has_price_count,
            has_part_number_count, has_brand_count, has_compatibility_count,
            has_stock_count, has_url_count, verified_fitment_count,
            verified_stock_count, relevant_count, irrelevant_count,
            completeness_score, verification_coverage
        FROM metrics_product_quality
    """)
    rows = cur_sql.fetchall()
    if not rows:
        return 0

    with pg_conn.cursor() as cur_pg:
        psycopg2.extras.execute_values(
            cur_pg,
            """
            INSERT INTO metrics_product_quality (
                request_id, total_extracted, has_title_count, has_price_count,
                has_part_number_count, has_brand_count, has_compatibility_count,
                has_stock_count, has_url_count, verified_fitment_count,
                verified_stock_count, relevant_count, irrelevant_count,
                completeness_score, verification_coverage
            ) VALUES %s
            ON CONFLICT (request_id) DO NOTHING
            """,
            [
                (
                    r[0], int(r[1] or 0), int(r[2] or 0), int(r[3] or 0),
                    int(r[4] or 0), int(r[5] or 0), int(r[6] or 0),
                    int(r[7] or 0), int(r[8] or 0), int(r[9] or 0),
                    int(r[10] or 0), int(r[11] or 0), int(r[12] or 0),
                    float(r[13] or 0), float(r[14] or 0),
                )
                for r in rows
            ],
        )
    pg_conn.commit()
    return len(rows)


def run_migration(sqlite_path: Path, pg_url: str):
    if not sqlite_path.exists():
        log.error("SQLite database not found at: %s", sqlite_path)
        sys.exit(1)

    log.info("Connecting to SQLite database at: %s", sqlite_path)
    sqlite_conn = sqlite3.connect(str(sqlite_path))

    log.info("Connecting to PostgreSQL database at: %s", pg_url)
    try:
        pg_conn = psycopg2.connect(pg_url)
    except Exception as e:
        log.error("Failed to connect to PostgreSQL: %s", e)
        sys.exit(1)

    # Initialize schema first
    schema_sql_path = Path(__file__).resolve().parent.parent / "sql" / "postgres_schema.sql"
    if schema_sql_path.exists():
        log.info("Applying PostgreSQL schema from: %s", schema_sql_path)
        with pg_conn.cursor() as cur:
            cur.execute(schema_sql_path.read_text())
        pg_conn.commit()

    log.info("Starting table migration from SQLite -> PostgreSQL...")
    n_sc = migrate_search_cache(sqlite_conn, pg_conn)
    log.info("  • search_cache: %d records migrated", n_sc)

    n_uc = migrate_url_cache(sqlite_conn, pg_conn)
    log.info("  • url_cache: %d records migrated", n_uc)

    n_sr = migrate_search_requests(sqlite_conn, pg_conn)
    log.info("  • metrics_search_requests: %d records migrated", n_sr)

    n_se = migrate_supplier_executions(sqlite_conn, pg_conn)
    log.info("  • metrics_supplier_executions: %d records migrated", n_se)

    n_st = migrate_stage_timings(sqlite_conn, pg_conn)
    log.info("  • metrics_stage_timings: %d records migrated", n_st)

    n_pq = migrate_product_quality(sqlite_conn, pg_conn)
    log.info("  • metrics_product_quality: %d records migrated", n_pq)

    sqlite_conn.close()
    pg_conn.close()

    log.info("PostgreSQL database migration completed successfully!")


def main():
    parser = argparse.ArgumentParser(description="Migrate parts_cache.db to PostgreSQL")
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE_PATH, help="Path to SQLite database")
    parser.add_argument("--postgres-url", default=DEFAULT_PG_URL, help="PostgreSQL connection URI")
    args = parser.parse_args()

    run_migration(args.sqlite, args.postgres_url)


if __name__ == "__main__":
    main()
