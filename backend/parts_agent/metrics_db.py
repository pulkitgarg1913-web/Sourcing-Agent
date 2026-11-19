"""Persistent Performance Monitoring and Analytics Database.

SQLite-backed storage for request-level latencies, stage-level timings,
supplier executions, product discovery, and data extraction quality metrics.
Designed for sub-millisecond query performance and seamless management reporting.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("metrics_db")

from .db import get_db, is_postgres_configured

# Default database path: same database as parts_cache.db for unified SQLite persistence
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "parts_cache.db"
DB_PATH = Path(os.environ.get("METRICS_DB_PATH", str(DEFAULT_DB_PATH)))


def init_metrics_db(db_path: Optional[Path] = None):
    """Initialize metrics tables and indexes if they do not exist."""
    path = db_path or DB_PATH
    if is_postgres_configured() and db_path is None:
        schema_file = Path(__file__).resolve().parent.parent / "sql" / "postgres_schema.sql"
        if schema_file.exists():
            with get_db() as conn:
                conn.execute(schema_file.read_text())
            return

    with get_db(path) as conn:
        # 1. Search Request Records
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_search_requests (
                request_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                created_at TEXT NOT NULL,
                customer_query TEXT NOT NULL,
                part_name TEXT,
                vehicle_maker TEXT,
                vehicle_model TEXT,
                vehicle_year TEXT,
                vehicle_variant TEXT,
                vehicle_label TEXT,
                status TEXT NOT NULL,
                total_latency_ms REAL NOT NULL,
                supplier_wall_clock_ms REAL DEFAULT 0.0,
                total_products_discovered INTEGER DEFAULT 0,
                relevant_products_discovered INTEGER DEFAULT 0,
                irrelevant_products_filtered INTEGER DEFAULT 0,
                suppliers_count INTEGER DEFAULT 0,
                suppliers_succeeded INTEGER DEFAULT 0,
                suppliers_failed INTEGER DEFAULT 0,
                suppliers_timed_out INTEGER DEFAULT 0,
                error_message TEXT,
                is_cached INTEGER DEFAULT 0,
                is_benchmark INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_req_timestamp ON metrics_search_requests (timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_req_status ON metrics_search_requests (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_req_benchmark ON metrics_search_requests (is_benchmark)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_req_thread ON metrics_search_requests (thread_id)")

        # 2. Supplier Execution Records
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_supplier_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                supplier_id TEXT NOT NULL,
                supplier_name TEXT NOT NULL,
                timestamp REAL NOT NULL,
                status TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                products_discovered INTEGER DEFAULT 0,
                products_extracted INTEGER DEFAULT 0,
                browser_init_ms REAL DEFAULT 0.0,
                navigation_ms REAL DEFAULT 0.0,
                extraction_ms REAL DEFAULT 0.0,
                verification_ms REAL DEFAULT 0.0,
                error_category TEXT,
                error_message TEXT,
                source_url TEXT,
                is_benchmark INTEGER DEFAULT 0,
                FOREIGN KEY (request_id) REFERENCES metrics_search_requests (request_id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sup_request_id ON metrics_supplier_executions (request_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sup_supplier_id ON metrics_supplier_executions (supplier_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sup_timestamp ON metrics_supplier_executions (timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sup_status ON metrics_supplier_executions (status)")

        # 3. Stage-Level Timing Records
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_stage_timings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                stage_name TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                details TEXT,
                FOREIGN KEY (request_id) REFERENCES metrics_search_requests (request_id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stage_req ON metrics_stage_timings (request_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stage_name ON metrics_stage_timings (stage_name)")

        # 4. Product Quality & Extraction Statistics
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_product_quality (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                total_extracted INTEGER DEFAULT 0,
                has_title_count INTEGER DEFAULT 0,
                has_price_count INTEGER DEFAULT 0,
                has_part_number_count INTEGER DEFAULT 0,
                has_brand_count INTEGER DEFAULT 0,
                has_compatibility_count INTEGER DEFAULT 0,
                has_stock_count INTEGER DEFAULT 0,
                has_url_count INTEGER DEFAULT 0,
                verified_fitment_count INTEGER DEFAULT 0,
                verified_stock_count INTEGER DEFAULT 0,
                relevant_count INTEGER DEFAULT 0,
                irrelevant_count INTEGER DEFAULT 0,
                completeness_score REAL DEFAULT 0.0,
                verification_coverage REAL DEFAULT 0.0,
                FOREIGN KEY (request_id) REFERENCES metrics_search_requests (request_id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_quality_req ON metrics_product_quality (request_id)")

        conn.commit()


# Ensure tables are initialized upon import
try:
    init_metrics_db()
except Exception as e:
    log.warning("Could not auto-initialize metrics database: %s", e)


# -----------------------------------------------------------------
# Percentile Calculation (Mathematical Foundation & Documentation)
# -----------------------------------------------------------------

def calculate_percentiles(values: List[float]) -> Dict[str, float]:
    """Calculate P50, P95, P99, average, minimum, and maximum latency.

    Percentile Calculation Method:
    -------------------------------
    Uses standard linear interpolation (consistent with NIST / NumPy default method 7).
    For a list of N non-negative numbers sorted in ascending order v_0, v_1, ..., v_{N-1}:
    - The rank index for percentile p in [0, 100] is:
        r = (p / 100.0) * (N - 1)
    - Let integer index k = floor(r) and fractional distance d = r - k.
    - If k >= N - 1:
        percentile = v_{N-1}
    - Otherwise:
        percentile = v_k + d * (v_{k+1} - v_k)

    Properties:
    - Guaranteed monotonic.
    - For N = 1, P50 = P95 = P99 = v_0.
    - Preserves sub-millisecond precision.
    - Fully deterministic and pure Python.
    """
    if not values:
        return {
            "count": 0,
            "avg": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "min": 0.0,
            "max": 0.0,
        }

    sorted_v = sorted(values)
    n = len(sorted_v)

    def _pct(p: float) -> float:
        if n == 1:
            return float(sorted_v[0])
        r = (p / 100.0) * (n - 1)
        k = int(r)
        d = r - k
        if k >= n - 1:
            return float(sorted_v[-1])
        return float(sorted_v[k] + d * (sorted_v[k + 1] - sorted_v[k]))

    return {
        "count": n,
        "avg": round(sum(sorted_v) / n, 2),
        "p50": round(_pct(50.0), 2),
        "p95": round(_pct(95.0), 2),
        "p99": round(_pct(99.0), 2),
        "min": round(sorted_v[0], 2),
        "max": round(sorted_v[-1], 2),
    }


# -----------------------------------------------------------------
# Record Ingestion Functions
# -----------------------------------------------------------------

def record_search_request(
    request_id: str,
    thread_id: str,
    customer_query: str,
    status: str,
    total_latency_ms: float,
    part_name: Optional[str] = None,
    vehicle_maker: Optional[str] = None,
    vehicle_model: Optional[str] = None,
    vehicle_year: Optional[str] = None,
    vehicle_variant: Optional[str] = None,
    vehicle_label: Optional[str] = None,
    supplier_wall_clock_ms: float = 0.0,
    total_products_discovered: int = 0,
    relevant_products_discovered: int = 0,
    irrelevant_products_filtered: int = 0,
    suppliers_count: int = 0,
    suppliers_succeeded: int = 0,
    suppliers_failed: int = 0,
    suppliers_timed_out: int = 0,
    error_message: Optional[str] = None,
    is_cached: bool = False,
    is_benchmark: bool = False,
    timestamp: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> None:
    """Persist a top-level search request record to SQLite."""
    ts = timestamp if timestamp is not None else time.time()
    created_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO metrics_search_requests (
                request_id, thread_id, timestamp, created_at, customer_query,
                part_name, vehicle_maker, vehicle_model, vehicle_year,
                vehicle_variant, vehicle_label, status, total_latency_ms,
                supplier_wall_clock_ms, total_products_discovered,
                relevant_products_discovered, irrelevant_products_filtered,
                suppliers_count, suppliers_succeeded, suppliers_failed,
                suppliers_timed_out, error_message, is_cached, is_benchmark
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                thread_id,
                ts,
                created_at,
                customer_query,
                part_name,
                vehicle_maker,
                vehicle_model,
                vehicle_year,
                vehicle_variant,
                vehicle_label,
                status,
                total_latency_ms,
                supplier_wall_clock_ms,
                total_products_discovered,
                relevant_products_discovered,
                irrelevant_products_filtered,
                suppliers_count,
                suppliers_succeeded,
                suppliers_failed,
                suppliers_timed_out,
                error_message,
                1 if is_cached else 0,
                1 if is_benchmark else 0,
            ),
        )
        conn.commit()


def _ensure_parent_request(conn, request_id: str, ts: Optional[float] = None) -> None:
    t = ts if ts is not None else time.time()
    conn.execute(
        """
        INSERT INTO metrics_search_requests (
            request_id, thread_id, timestamp, created_at, customer_query, status, total_latency_ms
        ) VALUES (?, ?, ?, datetime('now'), ?, 'in_progress', 0.0)
        ON CONFLICT (request_id) DO NOTHING
        """,
        (request_id, f"thread_{request_id}", t, "Search Request"),
    )


def record_supplier_execution(
    request_id: str,
    supplier_id: str,
    supplier_name: str,
    status: str,
    latency_ms: float,
    products_discovered: int = 0,
    products_extracted: int = 0,
    browser_init_ms: float = 0.0,
    navigation_ms: float = 0.0,
    extraction_ms: float = 0.0,
    verification_ms: float = 0.0,
    error_category: Optional[str] = None,
    error_message: Optional[str] = None,
    source_url: Optional[str] = None,
    is_benchmark: bool = False,
    timestamp: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> None:
    """Persist an individual supplier execution record."""
    ts = timestamp if timestamp is not None else time.time()
    with get_db(db_path) as conn:
        _ensure_parent_request(conn, request_id, ts)
        conn.execute(
            """
            INSERT INTO metrics_supplier_executions (
                request_id, supplier_id, supplier_name, timestamp, status,
                latency_ms, products_discovered, products_extracted,
                browser_init_ms, navigation_ms, extraction_ms, verification_ms,
                error_category, error_message, source_url, is_benchmark
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                supplier_id,
                supplier_name,
                ts,
                status,
                latency_ms,
                products_discovered,
                products_extracted,
                browser_init_ms,
                navigation_ms,
                extraction_ms,
                verification_ms,
                error_category,
                error_message,
                source_url,
                1 if is_benchmark else 0,
            ),
        )
        conn.commit()


def record_stage_timing(
    request_id: str,
    stage_name: str,
    duration_ms: float,
    details: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> None:
    """Persist a stage-level duration record."""
    details_str = json.dumps(details) if details else None
    with get_db(db_path) as conn:
        _ensure_parent_request(conn, request_id)
        conn.execute(
            """
            INSERT INTO metrics_stage_timings (request_id, stage_name, duration_ms, details)
            VALUES (?, ?, ?, ?)
            """,
            (request_id, stage_name, duration_ms, details_str),
        )
        conn.commit()


def record_product_quality(
    request_id: str,
    listings: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Analyze extracted listings and persist data extraction & verification quality metrics."""
    total = len(listings)
    if total == 0:
        data = {
            "total_extracted": 0,
            "has_title_count": 0,
            "has_price_count": 0,
            "has_part_number_count": 0,
            "has_brand_count": 0,
            "has_compatibility_count": 0,
            "has_stock_count": 0,
            "has_url_count": 0,
            "verified_fitment_count": 0,
            "verified_stock_count": 0,
            "relevant_count": 0,
            "irrelevant_count": 0,
            "completeness_score": 0.0,
            "verification_coverage": 0.0,
        }
    else:
        has_title = sum(1 for l in listings if bool(l.get("title")))
        has_price = sum(1 for l in listings if l.get("price") is not None)
        has_part_no = sum(1 for l in listings if bool(l.get("part_number") or l.get("partNumber") or l.get("oem_number")))
        has_brand = sum(1 for l in listings if bool(l.get("brand")))
        has_comp = sum(1 for l in listings if bool(l.get("fitment") or l.get("catalog_vehicle") or l.get("compatibility")))
        has_stock = sum(1 for l in listings if l.get("available") is not None)
        has_url = sum(1 for l in listings if bool(l.get("url")))

        verified_fitment = sum(1 for l in listings if l.get("fitment") in ("verified", "exact"))
        verified_stock = sum(1 for l in listings if l.get("available") is True)
        relevant = sum(1 for l in listings if l.get("is_relevant", True) is True and l.get("relevance_score", 1.0) >= 0.45)
        irrelevant = total - relevant

        # 7 key fields for completeness
        sum_fields = has_title + has_price + has_part_no + has_brand + has_comp + has_stock + has_url
        completeness_score = round((sum_fields / (7.0 * total)) * 100.0, 1)

        # Verification coverage = % with verified fitment or verified stock
        has_verification = sum(1 for l in listings if (l.get("fitment") in ("verified", "exact") or l.get("available") is True))
        verification_coverage = round((has_verification / total) * 100.0, 1)

        data = {
            "total_extracted": total,
            "has_title_count": has_title,
            "has_price_count": has_price,
            "has_part_number_count": has_part_no,
            "has_brand_count": has_brand,
            "has_compatibility_count": has_comp,
            "has_stock_count": has_stock,
            "has_url_count": has_url,
            "verified_fitment_count": verified_fitment,
            "verified_stock_count": verified_stock,
            "relevant_count": relevant,
            "irrelevant_count": irrelevant,
            "completeness_score": completeness_score,
            "verification_coverage": verification_coverage,
        }

    with get_db(db_path) as conn:
        _ensure_parent_request(conn, request_id)
        conn.execute(
            """
            INSERT OR REPLACE INTO metrics_product_quality (
                request_id, total_extracted, has_title_count, has_price_count,
                has_part_number_count, has_brand_count, has_compatibility_count,
                has_stock_count, has_url_count, verified_fitment_count,
                verified_stock_count, relevant_count, irrelevant_count,
                completeness_score, verification_coverage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                data["total_extracted"],
                data["has_title_count"],
                data["has_price_count"],
                data["has_part_number_count"],
                data["has_brand_count"],
                data["has_compatibility_count"],
                data["has_stock_count"],
                data["has_url_count"],
                data["verified_fitment_count"],
                data["verified_stock_count"],
                data["relevant_count"],
                data["irrelevant_count"],
                data["completeness_score"],
                data["verification_coverage"],
            ),
        )
        conn.commit()

    return data


# -----------------------------------------------------------------
# Query Filtering Helper
# -----------------------------------------------------------------

def _build_time_filter(
    time_range: str = "24h",
    include_benchmark: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    table_prefix: str = "",
) -> Tuple[str, List[Any]]:
    """Build WHERE clause condition and parameters based on time filters."""
    p = f"{table_prefix}." if table_prefix else ""
    clauses = []
    params: List[Any] = []

    if not include_benchmark:
        clauses.append(f"{p}is_benchmark = 0")

    now = time.time()
    if start_date and end_date:
        try:
            # Parse YYYY-MM-DD
            dt_start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            dt_end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).timestamp()
            clauses.append(f"{p}timestamp >= ? AND {p}timestamp <= ?")
            params.extend([dt_start, dt_end])
        except Exception:
            pass
    elif time_range == "24h":
        clauses.append(f"{p}timestamp >= ?")
        params.append(now - 86400)
    elif time_range == "7d":
        clauses.append(f"{p}timestamp >= ?")
        params.append(now - 7 * 86400)
    elif time_range == "30d":
        clauses.append(f"{p}timestamp >= ?")
        params.append(now - 30 * 86400)
    elif time_range == "all":
        pass  # no timestamp constraint

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


# -----------------------------------------------------------------
# Analytics & Aggregation Queries
# -----------------------------------------------------------------

def get_overview_metrics(
    time_range: str = "24h",
    include_benchmark: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compute high-level KPI overview across all requests."""
    where_sql, params = _build_time_filter(time_range, include_benchmark, start_date, end_date)

    with get_db(db_path) as conn:
        cursor = conn.execute(
            f"""
            SELECT
                COUNT(*) as total_searches,
                SUM(CASE WHEN status IN ('success', 'completed', 'completed_no_results') THEN 1 ELSE 0 END) as successful_searches,
                SUM(CASE WHEN status IN ('failed', 'partial_failure') THEN 1 ELSE 0 END) as failed_searches,
                SUM(CASE WHEN status = 'timeout' OR suppliers_timed_out > 0 THEN 1 ELSE 0 END) as timeout_searches,
                SUM(total_products_discovered) as total_products,
                SUM(relevant_products_discovered) as relevant_products,
                SUM(CASE WHEN total_products_discovered > 0 THEN 1 ELSE 0 END) as searches_with_products,
                SUM(CASE WHEN relevant_products_discovered > 0 THEN 1 ELSE 0 END) as searches_with_relevant,
                SUM(CASE WHEN total_products_discovered = 0 THEN 1 ELSE 0 END) as zero_result_searches,
                SUM(suppliers_count) as total_supplier_slots,
                SUM(suppliers_succeeded) as total_supplier_successes
            FROM metrics_search_requests
            {where_sql}
            """,
            params,
        )
        row = cursor.fetchone()

        total = row["total_searches"] or 0
        success = row["successful_searches"] or 0
        failed = row["failed_searches"] or 0
        timeouts = row["timeout_searches"] or 0
        total_products = row["total_products"] or 0
        relevant_products = row["relevant_products"] or 0
        searches_with_prods = row["searches_with_products"] or 0
        searches_with_rel = row["searches_with_relevant"] or 0
        zero_results = row["zero_result_searches"] or 0
        sup_slots = row["total_supplier_slots"] or 0
        sup_succ = row["total_supplier_successes"] or 0

        # Retrieve all latency values for accurate percentile computation
        cursor_lat = conn.execute(
            f"SELECT total_latency_ms FROM metrics_search_requests {where_sql}",
            params,
        )
        latencies = [r["total_latency_ms"] for r in cursor_lat.fetchall()]
        pct = calculate_percentiles(latencies)

    success_rate = round((success / total * 100.0), 1) if total > 0 else 0.0
    failure_rate = round((failed / total * 100.0), 1) if total > 0 else 0.0
    timeout_rate = round((timeouts / total * 100.0), 1) if total > 0 else 0.0
    supplier_response_rate = round((sup_succ / sup_slots * 100.0), 1) if sup_slots > 0 else 0.0

    # Distinguish product discovery rates clearly
    product_discovery_rate = round((searches_with_prods / total * 100.0), 1) if total > 0 else 0.0
    relevant_discovery_rate = round((searches_with_rel / total * 100.0), 1) if total > 0 else 0.0
    avg_relevant_per_search = round((relevant_products / total), 1) if total > 0 else 0.0

    return {
        "time_range": time_range,
        "include_benchmark": include_benchmark,
        "total_searches": total,
        "successful_searches": success,
        "failed_searches": failed,
        "timeout_searches": timeouts,
        "success_rate": success_rate,
        "failure_rate": failure_rate,
        "timeout_rate": timeout_rate,
        "supplier_response_rate": supplier_response_rate,
        "total_products_discovered": total_products,
        "relevant_products_discovered": relevant_products,
        "zero_result_searches": zero_results,
        "avg_relevant_products_per_search": avg_relevant_per_search,
        "product_discovery_rate": product_discovery_rate,
        "relevant_product_discovery_rate": relevant_discovery_rate,
        "latency": {
            "avg_ms": pct["avg"],
            "p50_ms": pct["p50"],
            "p95_ms": pct["p95"],
            "p99_ms": pct["p99"],
            "min_ms": pct["min"],
            "max_ms": pct["max"],
            # Values in seconds for dashboard display
            "avg_seconds": round(pct["avg"] / 1000.0, 3),
            "p50_seconds": round(pct["p50"] / 1000.0, 3),
            "p95_seconds": round(pct["p95"] / 1000.0, 3),
            "p99_seconds": round(pct["p99"] / 1000.0, 3),
            "min_seconds": round(pct["min"] / 1000.0, 3),
            "max_seconds": round(pct["max"] / 1000.0, 3),
        },
        "percentile_method": "linear_interpolation_nist_7",
    }


def get_latency_analytics(
    time_range: str = "24h",
    include_benchmark: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retrieve latency distributions, percentiles, stage breakdowns, and timeline trends."""
    where_sql, params = _build_time_filter(time_range, include_benchmark, start_date, end_date)

    with get_db(db_path) as conn:
        # Latency percentiles
        cursor = conn.execute(f"SELECT total_latency_ms FROM metrics_search_requests {where_sql}", params)
        latencies = [r["total_latency_ms"] for r in cursor.fetchall()]
        pct = calculate_percentiles(latencies)

        # Latency Distribution Histogram Buckets (in seconds)
        # Buckets: <1s, 1-3s, 3-5s, 5-10s, 10-20s, >20s
        buckets = [
            {"label": "< 1s", "min_ms": 0, "max_ms": 1000, "count": 0},
            {"label": "1s – 3s", "min_ms": 1000, "max_ms": 3000, "count": 0},
            {"label": "3s – 5s", "min_ms": 3000, "max_ms": 5000, "count": 0},
            {"label": "5s – 10s", "min_ms": 5000, "max_ms": 10000, "count": 0},
            {"label": "10s – 20s", "min_ms": 10000, "max_ms": 20000, "count": 0},
            {"label": "> 20s", "min_ms": 20000, "max_ms": None, "count": 0},
        ]
        for lat in latencies:
            for b in buckets:
                if lat >= b["min_ms"] and (b["max_ms"] is None or lat < b["max_ms"]):
                    b["count"] += 1
                    break

        total_pts = len(latencies)
        for b in buckets:
            b["percentage"] = round((b["count"] / total_pts * 100.0), 1) if total_pts > 0 else 0.0

        # Stage Timing Breakdown
        where_stg, params_stg = _build_time_filter(time_range, include_benchmark, start_date, end_date, table_prefix="r")
        cursor_stg = conn.execute(
            f"""
            SELECT s.stage_name, s.duration_ms
            FROM metrics_stage_timings s
            JOIN metrics_search_requests r ON s.request_id = r.request_id
            {where_stg}
            """,
            params_stg,
        )
        stage_map: Dict[str, List[float]] = {}
        for r in cursor_stg.fetchall():
            stage_map.setdefault(r["stage_name"], []).append(r["duration_ms"])

        stage_breakdown = []
        # Predefined stage order for logical presentation
        ordered_stages = [
            ("query_understanding", "Query Understanding / LLM"),
            ("vehicle_clarification", "Vehicle Identification & Clarification"),
            ("supplier_search_wall_clock", "Supplier Search Wall-Clock"),
            ("html_fetching_rendering", "HTML Fetching & Rendering"),
            ("product_extraction", "Product Extraction"),
            ("product_verification", "Product Verification & Enrichment"),
            ("relevance_filtering", "Relevance Filtering"),
            ("ranking_sorting", "Result Ranking & Sorting"),
            ("response_generation", "Final Response Generation"),
            ("total_e2e", "Total End-to-End"),
        ]
        for stg_id, stg_label in ordered_stages:
            v_list = stage_map.get(stg_id, [])
            stg_pct = calculate_percentiles(v_list)
            stage_breakdown.append({
                "stage": stg_id,
                "label": stg_label,
                "count": stg_pct["count"],
                "avg_ms": stg_pct["avg"],
                "p50_ms": stg_pct["p50"],
                "p95_ms": stg_pct["p95"],
                "avg_seconds": round(stg_pct["avg"] / 1000.0, 3),
                "p50_seconds": round(stg_pct["p50"] / 1000.0, 3),
                "p95_seconds": round(stg_pct["p95"] / 1000.0, 3),
            })

    return {
        "summary": {
            "avg_ms": pct["avg"],
            "p50_ms": pct["p50"],
            "p95_ms": pct["p95"],
            "p99_ms": pct["p99"],
            "min_ms": pct["min"],
            "max_ms": pct["max"],
            "avg_seconds": round(pct["avg"] / 1000.0, 3),
            "p50_seconds": round(pct["p50"] / 1000.0, 3),
            "p95_seconds": round(pct["p95"] / 1000.0, 3),
            "p99_seconds": round(pct["p99"] / 1000.0, 3),
            "min_seconds": round(pct["min"] / 1000.0, 3),
            "max_seconds": round(pct["max"] / 1000.0, 3),
        },
        "histogram": buckets,
        "stage_breakdown": stage_breakdown,
        "calculation_method": "NIST Type 7 Linear Interpolation",
    }


def get_supplier_analytics(
    time_range: str = "24h",
    include_benchmark: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Calculate granular performance statistics per configured supplier."""
    where_sql, params = _build_time_filter(time_range, include_benchmark, start_date, end_date)

    with get_db(db_path) as conn:
        cursor = conn.execute(
            f"""
            SELECT
                supplier_id,
                supplier_name,
                COUNT(*) as total_requests,
                SUM(CASE WHEN status IN ('completed', 'ok', 'no_results') THEN 1 ELSE 0 END) as successful_searches,
                SUM(CASE WHEN status IN ('failed', 'extraction_failed') THEN 1 ELSE 0 END) as failed_searches,
                SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) as timeout_count,
                SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) as blocked_count,
                SUM(products_discovered) as total_products_discovered,
                SUM(products_extracted) as total_products_extracted,
                AVG(browser_init_ms) as avg_browser_init_ms,
                AVG(navigation_ms) as avg_navigation_ms,
                AVG(extraction_ms) as avg_extraction_ms,
                AVG(verification_ms) as avg_verification_ms
            FROM metrics_supplier_executions
            {where_sql}
            GROUP BY supplier_id, supplier_name
            ORDER BY total_requests DESC
            """,
            params,
        )
        supplier_rows = cursor.fetchall()

        # Query per-supplier latencies for exact percentile computation
        cursor_lats = conn.execute(
            f"SELECT supplier_id, latency_ms FROM metrics_supplier_executions {where_sql}",
            params,
        )
        sup_lats: Dict[str, List[float]] = {}
        for r in cursor_lats.fetchall():
            sup_lats.setdefault(r["supplier_id"], []).append(r["latency_ms"])

    suppliers_res = []
    for row in supplier_rows:
        s_id = row["supplier_id"]
        total = row["total_requests"] or 0
        succ = row["successful_searches"] or 0
        failed = row["failed_searches"] or 0
        timeouts = row["timeout_count"] or 0
        blocked = row["blocked_count"] or 0
        discovered = row["total_products_discovered"] or 0
        extracted = row["total_products_extracted"] or 0

        lats = sup_lats.get(s_id, [])
        pct = calculate_percentiles(lats)

        success_rate = round((succ / total * 100.0), 1) if total > 0 else 0.0
        failure_rate = round((failed / total * 100.0), 1) if total > 0 else 0.0
        timeout_rate = round((timeouts / total * 100.0), 1) if total > 0 else 0.0
        # Availability rate = % non-blocked, non-timed out responses
        avail_rate = round(((total - (timeouts + blocked)) / total * 100.0), 1) if total > 0 else 0.0

        suppliers_res.append({
            "supplier_id": s_id,
            "supplier_name": row["supplier_name"] or s_id.title(),
            "requests": total,
            "successful_searches": succ,
            "failed_searches": failed,
            "timeout_count": timeouts,
            "blocked_count": blocked,
            "success_rate": success_rate,
            "failure_rate": failure_rate,
            "timeout_rate": timeout_rate,
            "availability_rate": avail_rate,
            "avg_latency_ms": pct["avg"],
            "p50_latency_ms": pct["p50"],
            "p95_latency_ms": pct["p95"],
            "p99_latency_ms": pct["p99"],
            "avg_latency_seconds": round(pct["avg"] / 1000.0, 2),
            "p50_latency_seconds": round(pct["p50"] / 1000.0, 2),
            "p95_latency_seconds": round(pct["p95"] / 1000.0, 2),
            "p99_latency_seconds": round(pct["p99"] / 1000.0, 2),
            "products_discovered": discovered,
            "products_extracted": extracted,
            "avg_products_per_search": round((discovered / total), 1) if total > 0 else 0.0,
            "stage_breakdown": {
                "browser_init_seconds": round((row["avg_browser_init_ms"] or 0.0) / 1000.0, 2),
                "navigation_seconds": round((row["avg_navigation_ms"] or 0.0) / 1000.0, 2),
                "extraction_seconds": round((row["avg_extraction_ms"] or 0.0) / 1000.0, 2),
                "verification_seconds": round((row["avg_verification_ms"] or 0.0) / 1000.0, 2),
            },
        })

    return {
        "suppliers": suppliers_res,
        "count": len(suppliers_res),
    }


def get_search_quality_metrics(
    time_range: str = "24h",
    include_benchmark: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retrieve product discovery, extraction completeness, and verification coverage metrics."""
    where_sql, params = _build_time_filter(time_range, include_benchmark, start_date, end_date)

    with get_db(db_path) as conn:
        cursor_req = conn.execute(
            f"""
            SELECT
                COUNT(*) as total_searches,
                SUM(total_products_discovered) as total_products,
                SUM(relevant_products_discovered) as relevant_products,
                SUM(irrelevant_products_filtered) as irrelevant_products,
                SUM(CASE WHEN total_products_discovered = 0 THEN 1 ELSE 0 END) as zero_result_searches,
                SUM(CASE WHEN total_products_discovered > 0 THEN 1 ELSE 0 END) as searches_with_any,
                SUM(CASE WHEN relevant_products_discovered > 0 THEN 1 ELSE 0 END) as searches_with_relevant
            FROM metrics_search_requests
            {where_sql}
            """,
            params,
        )
        row_req = cursor_req.fetchone()

        where_q, params_q = _build_time_filter(time_range, include_benchmark, start_date, end_date, table_prefix="r")
        cursor_q = conn.execute(
            f"""
            SELECT
                SUM(q.total_extracted) as total_extracted,
                SUM(q.has_title_count) as has_title,
                SUM(q.has_price_count) as has_price,
                SUM(q.has_part_number_count) as has_part_number,
                SUM(q.has_brand_count) as has_brand,
                SUM(q.has_compatibility_count) as has_compatibility,
                SUM(q.has_stock_count) as has_stock,
                SUM(q.has_url_count) as has_url,
                SUM(q.verified_fitment_count) as verified_fitment,
                SUM(q.verified_stock_count) as verified_stock,
                AVG(q.completeness_score) as avg_completeness,
                AVG(q.verification_coverage) as avg_coverage
            FROM metrics_product_quality q
            JOIN metrics_search_requests r ON q.request_id = r.request_id
            {where_q}
            """,
            params_q,
        )
        row_q = cursor_q.fetchone()

    total_searches = row_req["total_searches"] or 0
    total_prods = row_req["total_products"] or 0
    relevant_prods = row_req["relevant_products"] or 0
    irrelevant_prods = row_req["irrelevant_products"] or 0
    zero_results = row_req["zero_result_searches"] or 0
    searches_any = row_req["searches_with_any"] or 0
    searches_rel = row_req["searches_with_relevant"] or 0

    total_extracted = row_q["total_extracted"] or 0

    def _field_rate(count_val: Optional[int]) -> float:
        if not total_extracted:
            return 0.0
        return round(((count_val or 0) / total_extracted * 100.0), 1)

    return {
        "product_discovery": {
            "total_products_discovered": total_prods,
            "relevant_products_discovered": relevant_prods,
            "irrelevant_products_filtered": irrelevant_prods,
            "zero_result_searches": zero_results,
            "avg_relevant_products_per_search": round((relevant_prods / total_searches), 1) if total_searches > 0 else 0.0,
            "any_product_discovery_rate": round((searches_any / total_searches * 100.0), 1) if total_searches > 0 else 0.0,
            "relevant_product_discovery_rate": round((searches_rel / total_searches * 100.0), 1) if total_searches > 0 else 0.0,
        },
        "extraction_completeness": {
            "overall_completeness_rate": round(row_q["avg_completeness"] or 0.0, 1),
            "total_products_evaluated": total_extracted,
            "fields": {
                "title": _field_rate(row_q["has_title"]),
                "price": _field_rate(row_q["has_price"]),
                "part_number": _field_rate(row_q["has_part_number"]),
                "brand": _field_rate(row_q["has_brand"]),
                "vehicle_compatibility": _field_rate(row_q["has_compatibility"]),
                "stock_availability": _field_rate(row_q["has_stock"]),
                "product_url": _field_rate(row_q["has_url"]),
            },
            "metric_type": "completeness",
            "ground_truth_note": "Extraction completeness verifies presence and structure of critical catalog fields. Ground-truth accuracy requires comparison against verified OEM catalog specifications.",
        },
        "verification_coverage": {
            "overall_coverage_rate": round(row_q["avg_coverage"] or 0.0, 1),
            "fitment_verified_rate": _field_rate(row_q["verified_fitment"]),
            "stock_verified_rate": _field_rate(row_q["verified_stock"]),
            "verified_fitment_count": row_q["verified_fitment"] or 0,
            "verified_stock_count": row_q["verified_stock"] or 0,
        },
    }


def get_history_trends(
    time_range: str = "24h",
    include_benchmark: bool = False,
    granularity: str = "hour",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retrieve time-bucketed historical latency and volume trends."""
    where_sql, params = _build_time_filter(time_range, include_benchmark, start_date, end_date)

    # In SQLite, strftime grouping
    time_fmt = "%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"

    with get_db(db_path) as conn:
        cursor = conn.execute(
            f"""
            SELECT
                strftime('{time_fmt}', datetime(timestamp, 'unixepoch')) as time_bucket,
                MIN(timestamp) as bucket_ts,
                COUNT(*) as search_count,
                SUM(CASE WHEN status IN ('success', 'completed', 'completed_no_results') THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status IN ('failed', 'partial_failure') THEN 1 ELSE 0 END) as failure_count,
                SUM(total_products_discovered) as products_discovered,
                AVG(total_latency_ms) as avg_latency_ms
            FROM metrics_search_requests
            {where_sql}
            GROUP BY time_bucket
            ORDER BY bucket_ts ASC
            """,
            params,
        )
        rows = cursor.fetchall()

    timeline = []
    for r in rows:
        timeline.append({
            "bucket": r["time_bucket"],
            "timestamp": r["bucket_ts"],
            "total_searches": r["search_count"],
            "successful_searches": r["success_count"],
            "failed_searches": r["failure_count"],
            "products_discovered": r["products_discovered"] or 0,
            "avg_latency_ms": round(r["avg_latency_ms"] or 0.0, 2),
            "avg_latency_seconds": round((r["avg_latency_ms"] or 0.0) / 1000.0, 2),
        })

    return {
        "granularity": granularity,
        "timeline": timeline,
    }


def get_search_requests_history(
    limit: int = 50,
    offset: int = 0,
    query: Optional[str] = None,
    status: Optional[str] = None,
    time_range: str = "24h",
    include_benchmark: bool = False,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retrieve paginated search request history records."""
    where_sql, params = _build_time_filter(time_range, include_benchmark)

    clauses = []
    if where_sql:
        clauses.append(where_sql.replace("WHERE ", ""))

    if query:
        clauses.append("(customer_query LIKE ? OR part_name LIKE ? OR vehicle_label LIKE ?)")
        q_wild = f"%{query.strip()}%"
        params.extend([q_wild, q_wild, q_wild])

    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)

    full_where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with get_db(db_path) as conn:
        cursor_count = conn.execute(
            f"SELECT COUNT(*) as total FROM metrics_search_requests {full_where}",
            params,
        )
        total_count = cursor_count.fetchone()["total"]

        cursor_rows = conn.execute(
            f"""
            SELECT
                request_id, thread_id, timestamp, created_at, customer_query,
                part_name, vehicle_label, status, total_latency_ms,
                supplier_wall_clock_ms, total_products_discovered,
                relevant_products_discovered, suppliers_count, suppliers_succeeded,
                suppliers_failed, suppliers_timed_out, error_message, is_cached,
                is_benchmark
            FROM metrics_search_requests
            {full_where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        requests = []
        for r in cursor_rows.fetchall():
            requests.append({
                "request_id": r["request_id"],
                "thread_id": r["thread_id"],
                "timestamp": r["timestamp"],
                "created_at": r["created_at"],
                "customer_query": r["customer_query"],
                "part_name": r["part_name"] or "",
                "vehicle_label": r["vehicle_label"] or "",
                "status": r["status"],
                "total_latency_ms": r["total_latency_ms"],
                "total_latency_seconds": round(r["total_latency_ms"] / 1000.0, 2),
                "supplier_wall_clock_seconds": round((r["supplier_wall_clock_ms"] or 0.0) / 1000.0, 2),
                "products_discovered": r["total_products_discovered"],
                "relevant_products": r["relevant_products_discovered"],
                "suppliers_count": r["suppliers_count"],
                "suppliers_succeeded": r["suppliers_succeeded"],
                "suppliers_failed": r["suppliers_failed"],
                "suppliers_timed_out": r["suppliers_timed_out"],
                "error_message": r["error_message"],
                "is_cached": bool(r["is_cached"]),
                "is_benchmark": bool(r["is_benchmark"]),
            })

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "requests": requests,
    }


def get_request_detail(request_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Retrieve full request detail including stage timings, supplier executions, and quality metrics."""
    with get_db(db_path) as conn:
        cursor_req = conn.execute(
            "SELECT * FROM metrics_search_requests WHERE request_id = ?",
            (request_id,),
        )
        req_row = cursor_req.fetchone()
        if not req_row:
            return None

        # Stage Timings
        cursor_stages = conn.execute(
            "SELECT stage_name, duration_ms, details FROM metrics_stage_timings WHERE request_id = ? ORDER BY id ASC",
            (request_id,),
        )
        stages = []
        for s in cursor_stages.fetchall():
            stages.append({
                "stage": s["stage_name"],
                "duration_ms": s["duration_ms"],
                "duration_seconds": round(s["duration_ms"] / 1000.0, 3),
                "details": json.loads(s["details"]) if isinstance(s["details"], str) else (s["details"] or None),
            })

        # Supplier Executions
        cursor_sups = conn.execute(
            "SELECT * FROM metrics_supplier_executions WHERE request_id = ? ORDER BY id ASC",
            (request_id,),
        )
        suppliers = []
        for su in cursor_sups.fetchall():
            suppliers.append({
                "supplier_id": su["supplier_id"],
                "supplier_name": su["supplier_name"],
                "status": su["status"],
                "latency_ms": su["latency_ms"],
                "latency_seconds": round(su["latency_ms"] / 1000.0, 2),
                "products_discovered": su["products_discovered"],
                "products_extracted": su["products_extracted"],
                "browser_init_seconds": round((su["browser_init_ms"] or 0.0) / 1000.0, 2),
                "navigation_seconds": round((su["navigation_ms"] or 0.0) / 1000.0, 2),
                "extraction_seconds": round((su["extraction_ms"] or 0.0) / 1000.0, 2),
                "verification_seconds": round((su["verification_ms"] or 0.0) / 1000.0, 2),
                "error_category": su["error_category"],
                "error_message": su["error_message"],
                "source_url": su["source_url"],
            })

        # Product Quality
        cursor_q = conn.execute(
            "SELECT * FROM metrics_product_quality WHERE request_id = ?",
            (request_id,),
        )
        q_row = cursor_q.fetchone()
        quality_data = dict(q_row) if q_row else None

    req_dict = dict(req_row)
    req_dict["total_latency_seconds"] = round(req_dict["total_latency_ms"] / 1000.0, 2)
    req_dict["supplier_wall_clock_seconds"] = round((req_dict.get("supplier_wall_clock_ms") or 0.0) / 1000.0, 2)
    req_dict["stages"] = stages
    req_dict["suppliers"] = suppliers
    req_dict["quality"] = quality_data

    return req_dict


def clear_metrics(include_benchmarks: bool = True, db_path: Optional[Path] = None) -> Dict[str, int]:
    """Clear performance monitoring and analytics dashboard records.
    
    Clears metrics_product_quality, metrics_stage_timings,
    metrics_supplier_executions, and metrics_search_requests.
    Returns counts of deleted records.
    """
    path = db_path or DB_PATH
    deleted_counts = {
        "requests": 0,
        "suppliers": 0,
        "stages": 0,
        "quality": 0,
    }

    with get_db(path) as conn:
        try:
            q_row = conn.execute("SELECT count(*) AS cnt FROM metrics_product_quality").fetchone()
            deleted_counts["quality"] = (q_row["cnt"] if q_row else 0) or 0
            st_row = conn.execute("SELECT count(*) AS cnt FROM metrics_stage_timings").fetchone()
            deleted_counts["stages"] = (st_row["cnt"] if st_row else 0) or 0
            sup_row = conn.execute("SELECT count(*) AS cnt FROM metrics_supplier_executions").fetchone()
            deleted_counts["suppliers"] = (sup_row["cnt"] if sup_row else 0) or 0
            req_row = conn.execute("SELECT count(*) AS cnt FROM metrics_search_requests").fetchone()
            deleted_counts["requests"] = (req_row["cnt"] if req_row else 0) or 0
        except Exception:
            pass

        if include_benchmarks:
            if getattr(conn, "is_pg", False):
                conn.execute("""
                    TRUNCATE TABLE 
                        metrics_product_quality,
                        metrics_stage_timings,
                        metrics_supplier_executions,
                        metrics_search_requests
                    CASCADE;
                """)
            else:
                conn.execute("DELETE FROM metrics_product_quality;")
                conn.execute("DELETE FROM metrics_stage_timings;")
                conn.execute("DELETE FROM metrics_supplier_executions;")
                conn.execute("DELETE FROM metrics_search_requests;")
        else:
            if getattr(conn, "is_pg", False):
                conn.execute("DELETE FROM metrics_search_requests WHERE is_benchmark = 0;")
            else:
                conn.execute("DELETE FROM metrics_product_quality WHERE request_id IN (SELECT request_id FROM metrics_search_requests WHERE is_benchmark = 0);")
                conn.execute("DELETE FROM metrics_stage_timings WHERE request_id IN (SELECT request_id FROM metrics_search_requests WHERE is_benchmark = 0);")
                conn.execute("DELETE FROM metrics_supplier_executions WHERE request_id IN (SELECT request_id FROM metrics_search_requests WHERE is_benchmark = 0);")
                conn.execute("DELETE FROM metrics_search_requests WHERE is_benchmark = 0;")
        conn.commit()

    # If PostgreSQL is configured and default path was used, also sync local SQLite fallback
    if is_postgres_configured() and db_path is None and DEFAULT_DB_PATH.exists():
        try:
            with get_db(force_sqlite=True) as s_conn:
                if include_benchmarks:
                    s_conn.execute("DELETE FROM metrics_product_quality;")
                    s_conn.execute("DELETE FROM metrics_stage_timings;")
                    s_conn.execute("DELETE FROM metrics_supplier_executions;")
                    s_conn.execute("DELETE FROM metrics_search_requests;")
                else:
                    s_conn.execute("DELETE FROM metrics_product_quality WHERE request_id IN (SELECT request_id FROM metrics_search_requests WHERE is_benchmark = 0);")
                    s_conn.execute("DELETE FROM metrics_stage_timings WHERE request_id IN (SELECT request_id FROM metrics_search_requests WHERE is_benchmark = 0);")
                    s_conn.execute("DELETE FROM metrics_supplier_executions WHERE request_id IN (SELECT request_id FROM metrics_search_requests WHERE is_benchmark = 0);")
                    s_conn.execute("DELETE FROM metrics_search_requests WHERE is_benchmark = 0;")
                s_conn.commit()
        except Exception as e:
            log.warning("Could not sync clear with local SQLite fallback: %s", e)

    log.info("Cleared dashboard metrics: %s (include_benchmarks=%s)", deleted_counts, include_benchmarks)
    return deleted_counts
