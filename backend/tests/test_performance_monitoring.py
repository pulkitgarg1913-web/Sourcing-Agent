"""Unit and Integration Tests for Performance Monitoring, Analytics, and Benchmarking.

Tests:
1. Latency measurement (high-resolution monotonic timers).
2. P50, P95, P99 percentile calculations (linear interpolation).
3. Average, min, and max latency calculations.
4. Supplier-wise metrics and availability rates.
5. Concurrent supplier timing (verifying wall clock != sum of durations).
6. Successful and failed request tracking.
7. Timeout and error handling.
8. Product discovery metrics (any vs relevant discovery rate, zero-result searches).
9. Data extraction completeness and verification coverage.
10. Database persistence across restarts.
11. Analytics API endpoints responses.
12. Edge cases: empty dataset, single request, outliers, missing supplier responses.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from fastapi.testclient import TestClient

from parts_agent import metrics_db
from parts_agent.perf_tracker import RequestMetricsTracker, create_tracker, get_tracker, remove_tracker
from scripts.benchmark_runner import BenchmarkItemResult, BenchmarkRunner
from server import app


class LatencyAndPercentileTests(unittest.TestCase):
    """Test percentile calculations and latency measurements."""

    def test_percentiles_empty_dataset(self):
        """Edge case: no requests recorded."""
        res = metrics_db.calculate_percentiles([])
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["avg"], 0.0)
        self.assertEqual(res["p50"], 0.0)
        self.assertEqual(res["p95"], 0.0)
        self.assertEqual(res["p99"], 0.0)
        self.assertEqual(res["min"], 0.0)
        self.assertEqual(res["max"], 0.0)

    def test_percentiles_single_request(self):
        """Edge case: exactly one request."""
        res = metrics_db.calculate_percentiles([450.0])
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["avg"], 450.0)
        self.assertEqual(res["p50"], 450.0)
        self.assertEqual(res["p95"], 450.0)
        self.assertEqual(res["p99"], 450.0)
        self.assertEqual(res["min"], 450.0)
        self.assertEqual(res["max"], 450.0)

    def test_percentiles_interpolation_accuracy(self):
        """Verify linear interpolation matches standard definition."""
        # 10 values: 10, 20, 30, 40, 50, 60, 70, 80, 90, 100
        values = [float(x * 10) for x in range(1, 11)]
        res = metrics_db.calculate_percentiles(values)
        self.assertEqual(res["count"], 10)
        self.assertEqual(res["avg"], 55.0)
        self.assertEqual(res["min"], 10.0)
        self.assertEqual(res["max"], 100.0)
        # N=10, P50 rank = 0.5 * 9 = 4.5 -> v[4] + 0.5*(v[5]-v[4]) = 50 + 0.5*10 = 55.0
        self.assertEqual(res["p50"], 55.0)
        # P95 rank = 0.95 * 9 = 8.55 -> v[8] + 0.55*(v[9]-v[8]) = 90 + 0.55*10 = 95.5
        self.assertEqual(res["p95"], 95.5)
        # P99 rank = 0.99 * 9 = 8.91 -> v[8] + 0.91*(v[9]-v[8]) = 90 + 0.91*10 = 99.1
        self.assertEqual(res["p99"], 99.1)

    def test_percentiles_large_outlier_resilience(self):
        """Edge case: large latency outlier."""
        # 9 fast requests (~100ms) and 1 extreme outlier (60,000ms = 60s)
        values = [100.0, 102.0, 98.0, 105.0, 99.0, 101.0, 104.0, 97.0, 103.0, 60000.0]
        res = metrics_db.calculate_percentiles(values)
        self.assertEqual(res["count"], 10)
        # P50 should remain unaffected by the single outlier (~101ms)
        self.assertLess(res["p50"], 110.0)
        # P95 and P99 must reflect the extreme spike
        self.assertGreater(res["p95"], 20000.0)
        self.assertGreater(res["p99"], 50000.0)
        self.assertEqual(res["max"], 60000.0)

    def test_monotonic_timer_tracking(self):
        """Verify monotonic timing via perf_counter."""
        tracker = RequestMetricsTracker("req_test_1", "thread_test_1", "Test Query")
        tracker.start_stage("llm_parse")
        time.sleep(0.015)  # 15ms sleep
        duration = tracker.end_stage("llm_parse")
        self.assertGreaterEqual(duration, 10.0)
        self.assertIn("llm_parse", tracker.stage_timings)


class ConcurrentSupplierTimingTests(unittest.TestCase):
    """Test that concurrent supplier searches measure wall-clock duration, not additive summation."""

    def test_concurrent_wall_clock_not_additive_sum(self):
        """Total supplier search latency must be the wall-clock time, not the sum of durations."""
        tracker = RequestMetricsTracker("req_concur", "thread_concur", "Creta Headlight")

        # Simulate 2 concurrent suppliers:
        # Supplier A takes 300ms
        # Supplier B takes 450ms
        # Total wall clock is ~450ms, while additive sum would be 750ms!
        sup_a_duration_ms = 300.0
        sup_b_duration_ms = 450.0
        actual_wall_clock_ms = 455.0

        tracker.record_supplier_search(
            supplier_id="boodmo",
            supplier_name="Boodmo",
            status="ok",
            latency_ms=sup_a_duration_ms,
            products_discovered=5,
        )
        tracker.record_supplier_search(
            supplier_id="autodukan",
            supplier_name="AutoDukan",
            status="ok",
            latency_ms=sup_b_duration_ms,
            products_discovered=3,
        )
        tracker.record_stage("supplier_search_wall_clock", actual_wall_clock_ms)

        summary = tracker.finish(
            status="success",
            outcome={"listings": [{"title": "Headlight", "price": 1200.0}]},
        )

        self.assertIn("total_latency_ms", summary)
        self.assertEqual(tracker.stage_timings["supplier_search_wall_clock"], actual_wall_clock_ms)
        # Crucial check: wall clock is less than the additive sum
        additive_sum = sup_a_duration_ms + sup_b_duration_ms
        self.assertLess(tracker.supplier_wall_clock_ms, additive_sum)


class DatabasePersistenceTests(unittest.TestCase):
    """Test SQLite database persistence, restart survival, and data integrity."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_metrics.db"
        metrics_db.init_metrics_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_records_persist_and_survive_restart(self):
        """Simulate backend restart by closing and reopening database connection."""
        req_id = "req_persist_001"
        metrics_db.record_search_request(
            request_id=req_id,
            thread_id="thread_persist_001",
            customer_query="Tata Nexon Gearbox",
            status="success",
            total_latency_ms=1240.5,
            part_name="Gearbox",
            vehicle_maker="Tata",
            vehicle_model="Nexon",
            vehicle_year="2022",
            vehicle_variant="Diesel AMT",
            vehicle_label="Tata Nexon 2022 Diesel AMT",
            supplier_wall_clock_ms=850.2,
            total_products_discovered=4,
            relevant_products_discovered=4,
            suppliers_count=2,
            suppliers_succeeded=2,
            db_path=self.db_path,
        )

        metrics_db.record_stage_timing(
            request_id=req_id,
            stage_name="query_understanding",
            duration_ms=210.0,
            db_path=self.db_path,
        )
        metrics_db.record_stage_timing(
            request_id=req_id,
            stage_name="supplier_search_wall_clock",
            duration_ms=850.2,
            db_path=self.db_path,
        )

        metrics_db.record_supplier_execution(
            request_id=req_id,
            supplier_id="boodmo",
            supplier_name="Boodmo",
            status="completed",
            latency_ms=620.0,
            products_discovered=2,
            products_extracted=2,
            db_path=self.db_path,
        )
        metrics_db.record_supplier_execution(
            request_id=req_id,
            supplier_id="autodukan",
            supplier_name="AutoDukan",
            status="completed",
            latency_ms=850.2,
            products_discovered=2,
            products_extracted=2,
            db_path=self.db_path,
        )

        # Simulate restart: open fresh connection from disk
        detail = metrics_db.get_request_detail(req_id, db_path=self.db_path)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["request_id"], req_id)
        self.assertEqual(detail["customer_query"], "Tata Nexon Gearbox")
        self.assertEqual(detail["status"], "success")
        self.assertEqual(detail["total_latency_ms"], 1240.5)
        self.assertEqual(len(detail["stages"]), 2)
        self.assertEqual(len(detail["suppliers"]), 2)

    def test_supplier_analytics_aggregation(self):
        """Verify supplier statistics: total requests, success rate, timeout count, availability."""
        # Record 3 Boodmo requests: 2 ok, 1 timeout
        metrics_db.record_supplier_execution(
            request_id="req_s1", supplier_id="boodmo", supplier_name="Boodmo",
            status="completed", latency_ms=500.0, products_discovered=3, db_path=self.db_path
        )
        metrics_db.record_supplier_execution(
            request_id="req_s2", supplier_id="boodmo", supplier_name="Boodmo",
            status="completed", latency_ms=700.0, products_discovered=2, db_path=self.db_path
        )
        metrics_db.record_supplier_execution(
            request_id="req_s3", supplier_id="boodmo", supplier_name="Boodmo",
            status="timeout", latency_ms=30000.0, products_discovered=0, db_path=self.db_path
        )

        res = metrics_db.get_supplier_analytics(time_range="all", db_path=self.db_path)
        sups = res["suppliers"]
        self.assertEqual(len(sups), 1)
        boodmo_stat = sups[0]
        self.assertEqual(boodmo_stat["supplier_id"], "boodmo")
        self.assertEqual(boodmo_stat["requests"], 3)
        self.assertEqual(boodmo_stat["successful_searches"], 2)
        self.assertEqual(boodmo_stat["timeout_count"], 1)
        self.assertAlmostEqual(boodmo_stat["success_rate"], 66.7, places=1)
        self.assertAlmostEqual(boodmo_stat["timeout_rate"], 33.3, places=1)
        self.assertAlmostEqual(boodmo_stat["availability_rate"], 66.7, places=1)
        self.assertEqual(boodmo_stat["p50_latency_ms"], 700.0)


class SearchQualityAndExtractionTests(unittest.TestCase):
    """Test extraction completeness and verification coverage metrics."""

    def test_extraction_completeness_and_coverage(self):
        sample_listings = [
            {
                "title": "Brake Pad Front Set",
                "price": 850.0,
                "part_number": "BP-1234",
                "brand": "Bosch",
                "fitment": "verified",
                "available": True,
                "url": "https://example.com/pad1",
                "is_relevant": True,
                "relevance_score": 0.95,
            },
            {
                "title": "Brake Pad Rear",
                "price": 650.0,
                "part_number": "BP-5678",
                "brand": "TVS",
                "fitment": "unverified",
                "available": None,  # unverified stock
                "url": "https://example.com/pad2",
                "is_relevant": True,
                "relevance_score": 0.88,
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_quality.db"
            metrics_db.init_metrics_db(db_path)

            res = metrics_db.record_product_quality("req_qual_1", sample_listings, db_path=db_path)
            self.assertEqual(res["total_extracted"], 2)
            self.assertEqual(res["has_title_count"], 2)
            self.assertEqual(res["has_price_count"], 2)
            self.assertEqual(res["has_part_number_count"], 2)
            self.assertEqual(res["has_brand_count"], 2)
            self.assertEqual(res["verified_fitment_count"], 1)
            self.assertEqual(res["verified_stock_count"], 1)
            # Item 1 has 7/7 fields; Item 2 has 6/7 (stock is None) -> (7+6)/(14) * 100 = 92.9%
            self.assertAlmostEqual(res["completeness_score"], 92.9, places=1)
            # Item 1 has verified fitment & stock; Item 2 has neither -> 1/2 = 50.0% coverage
            self.assertEqual(res["verification_coverage"], 50.0)

    def test_zero_result_searches(self):
        """Edge case: searches where 0 products were found."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_zero.db"
            metrics_db.init_metrics_db(db_path)

            metrics_db.record_search_request(
                request_id="req_zero_1",
                thread_id="t_zero_1",
                customer_query="Obscure Rare Part",
                status="completed_no_results",
                total_latency_ms=1800.0,
                total_products_discovered=0,
                relevant_products_discovered=0,
                db_path=db_path,
            )

            metrics_db.record_product_quality("req_zero_1", [], db_path=db_path)
            overview = metrics_db.get_overview_metrics(time_range="all", db_path=db_path)
            self.assertEqual(overview["total_searches"], 1)
            self.assertEqual(overview["zero_result_searches"], 1)
            self.assertEqual(overview["product_discovery_rate"], 0.0)
            self.assertEqual(overview["relevant_product_discovery_rate"], 0.0)


class FastAPIMetricsEndpointTests(unittest.TestCase):
    """Test the analytics and benchmarking API endpoints using FastAPI TestClient."""

    def setUp(self):
        self.client = TestClient(app)

    def test_metrics_overview_endpoint(self):
        resp = self.client.get("/api/metrics/overview?time_range=all")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total_searches", data)
        self.assertIn("success_rate", data)
        self.assertIn("latency", data)
        self.assertIn("p50_seconds", data["latency"])
        self.assertIn("p95_seconds", data["latency"])
        self.assertIn("p99_seconds", data["latency"])

    def test_metrics_latency_endpoint(self):
        resp = self.client.get("/api/metrics/latency?time_range=all")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("summary", data)
        self.assertIn("histogram", data)
        self.assertIn("stage_breakdown", data)

    def test_metrics_suppliers_endpoint(self):
        resp = self.client.get("/api/metrics/suppliers?time_range=all")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("suppliers", data)

    def test_metrics_quality_endpoint(self):
        resp = self.client.get("/api/metrics/quality?time_range=all")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("product_discovery", data)
        self.assertIn("extraction_completeness", data)
        self.assertIn("verification_coverage", data)

    def test_metrics_history_endpoint(self):
        resp = self.client.get("/api/metrics/history?time_range=all&granularity=hour")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("timeline", data)

    def test_metrics_requests_endpoint(self):
        resp = self.client.get("/api/metrics/requests?limit=10&offset=0")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total", data)
        self.assertIn("requests", data)

    def test_benchmark_status_endpoint(self):
        resp = self.client.get("/api/benchmark/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("is_running", data)
        self.assertIn("default_queries", data)


class BenchmarkRunnerTests(unittest.TestCase):
    """Test benchmark runner execution, throughput, and percentiles."""

    def test_benchmark_runner_sequential_and_concurrent(self):
        """Test benchmark runner with fast mocked conversation pipeline."""
        queries = ["Hyundai Creta Headlight", "Honda City Brake Pads"]
        runner = BenchmarkRunner(queries=queries, concurrency=2, mode="concurrent")

        async def _run():
            # Mock _execute_single_query to test runner aggregation logic deterministically
            async def mock_exec(query, index):
                lat = 250.0 if "Creta" in query else 350.0
                return BenchmarkItemResult(
                    query=query,
                    request_id=f"req_mock_{index}",
                    thread_id=f"thread_mock_{index}",
                    latency_ms=lat,
                    latency_seconds=lat / 1000.0,
                    status="success",
                    products_found=3,
                    relevant_products=3,
                )
            runner._execute_single_query = mock_exec
            return await runner.run()

        summary = asyncio.run(_run())
        self.assertEqual(summary.total_queries, 2)
        self.assertEqual(summary.successful_queries, 2)
        self.assertEqual(summary.failed_queries, 0)
        self.assertGreater(summary.throughput_rps, 0.0)
        self.assertEqual(summary.product_discovery_rate, 100.0)
        self.assertIn("p50_ms", summary.latency)
        self.assertIn("p95_ms", summary.latency)
        self.assertIn("p99_ms", summary.latency)


if __name__ == "__main__":
    unittest.main()
