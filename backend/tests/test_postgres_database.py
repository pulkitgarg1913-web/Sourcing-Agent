"""Unit tests verifying PostgreSQL database integration and psql connectivity."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
import unittest

from backend.parts_agent.db import get_db, get_db_status, is_postgres_configured, get_postgres_url
from backend.parts_agent import metrics_db, cache_manager


class PostgresDatabaseTests(unittest.TestCase):
    """Test suite validating PostgreSQL database integration."""

    def setUp(self):
        self.skip_if_no_pg()

    def skip_if_no_pg(self):
        if not is_postgres_configured():
            self.skipTest("PostgreSQL is not configured or psycopg2 is not installed.")

    def test_postgres_connection_and_version(self):
        """Verify get_db_status reports PostgreSQL engine and active connection."""
        status = get_db_status()
        self.assertEqual(status["engine"], "postgresql")
        self.assertEqual(status["status"], "connected")
        self.assertIn("PostgreSQL", status.get("version", ""))

    def test_postgres_crud_search_requests(self):
        """Test recording and retrieving search requests from PostgreSQL."""
        req_id = f"test_req_pg_{int(time.time() * 1000)}"
        metrics_db.record_search_request(
            request_id=req_id,
            thread_id="test_pg_thread",
            customer_query="Front bumper for Hyundai Creta",
            status="success",
            total_latency_ms=2450.0,
            supplier_wall_clock_ms=2100.0,
            total_products_discovered=5,
            relevant_products_discovered=4,
            irrelevant_products_filtered=1,
            suppliers_count=2,
            suppliers_succeeded=2,
            suppliers_failed=0,
            suppliers_timed_out=0,
            is_cached=False,
            is_benchmark=False,
        )

        detail = metrics_db.get_request_detail(req_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["request_id"], req_id)
        self.assertEqual(detail["customer_query"], "Front bumper for Hyundai Creta")
        self.assertEqual(detail["status"], "success")
        self.assertAlmostEqual(detail["total_latency_ms"], 2450.0, places=1)

    def test_postgres_supplier_and_stage_timings(self):
        """Verify supplier executions and stage timings record into PostgreSQL."""
        req_id = f"test_req_stage_{int(time.time() * 1000)}"

        # 1. Record stage timing
        metrics_db.record_stage_timing(
            request_id=req_id,
            stage_name="query_understanding",
            duration_ms=125.0,
            details={"model": "gemini-3.5-flash-lite"},
        )

        # 2. Record supplier execution
        metrics_db.record_supplier_execution(
            request_id=req_id,
            supplier_id="boodmo",
            supplier_name="Boodmo",
            status="ok",
            latency_ms=1200.0,
            products_discovered=6,
            products_extracted=6,
        )

        detail = metrics_db.get_request_detail(req_id)
        self.assertIsNotNone(detail)
        stage_names = [s["stage"] for s in detail.get("stages", [])]
        self.assertIn("query_understanding", stage_names)
        sup_names = [s["supplier_name"] for s in detail.get("suppliers", [])]
        self.assertIn("Boodmo", sup_names)

    def test_postgres_product_quality_metrics(self):
        """Verify product quality records and completeness calculations in PostgreSQL."""
        req_id = f"test_req_qual_{int(time.time() * 1000)}"
        sample_listings = [
            {
                "title": "Front Bumper Assembly",
                "price": 4500.0,
                "part_number": "86511M0000",
                "brand": "Hyundai Mobis",
                "fitment": "verified",
                "available": True,
                "url": "https://boodmo.com/catalog/part-bumper-1/",
                "relevance_score": 0.95,
            }
        ]
        metrics_db.record_product_quality(req_id, sample_listings)
        detail = metrics_db.get_request_detail(req_id)
        self.assertIsNotNone(detail["quality"])
        self.assertEqual(detail["quality"]["total_extracted"], 1)
        self.assertEqual(detail["quality"]["has_title_count"], 1)
        self.assertEqual(detail["quality"]["has_price_count"], 1)

    def test_psql_cli_connectivity(self):
        """Verify connectivity directly using the psql command line tool."""
        db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/sourcing_agent")
        res = subprocess.run(
            ["psql", db_url, "-c", "SELECT count(*) FROM search_cache;"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"psql CLI failed: {res.stderr}")
        self.assertIn("count", res.stdout.lower())

    def test_sqlite_fallback_on_demand(self):
        """Verify that passing force_sqlite=True returns an SQLite connection."""
        with get_db(force_sqlite=True) as conn:
            self.assertFalse(conn.is_pg)
            res = conn.execute("SELECT 1 AS val;").fetchone()
            self.assertEqual(res["val"], 1)


if __name__ == "__main__":
    unittest.main()
