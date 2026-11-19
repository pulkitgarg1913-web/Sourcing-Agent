"""High-Resolution Performance & Stage Timing Tracker.

Uses monotonic `time.perf_counter()` to measure:
- End-to-end request latency
- Individual component-level stage durations (query understanding, vehicle clarification,
  concurrent supplier search wall-clock, product extraction, verification,
  relevance filtering, ranking, response generation)
- Concurrent supplier search durations without invalid additive summation
- Product extraction and verification quality metrics
Ensures all timing measurements are non-blocking and fail-safe.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from . import metrics_db
from .models import SearchOutcome, VehicleProfile

log = logging.getLogger("perf_tracker")


class RequestMetricsTracker:
    """Accurately records monotonic timestamps and stages for a single search request."""

    def __init__(
        self,
        request_id: str,
        thread_id: str,
        customer_query: str,
        is_benchmark: bool = False,
    ):
        self.request_id = request_id
        self.thread_id = thread_id
        self.customer_query = customer_query
        self.is_benchmark = is_benchmark

        # Monotonic timer for end-to-end latency measurement
        self._t0_perf = time.perf_counter()
        self.timestamp = time.time()

        # Component stage timings: stage_name -> duration_ms
        self.stage_timings: Dict[str, float] = {}
        self.stage_details: Dict[str, Dict[str, Any]] = {}
        self._active_stages: Dict[str, float] = {}

        # Supplier execution results
        self.supplier_executions: List[Dict[str, Any]] = []

        # Product listings extracted
        self.extracted_listings: List[Dict[str, Any]] = []

        # Metadata
        self.supplier_wall_clock_ms: float = 0.0
        self.is_cached: bool = False
        self.status: str = "in_progress"

        self.error_message: Optional[str] = None
        self._completed: bool = False

    def start_stage(self, stage_name: str) -> None:
        """Mark the beginning of an individual stage using monotonic timer."""
        self._active_stages[stage_name] = time.perf_counter()

    def end_stage(self, stage_name: str, details: Optional[Dict[str, Any]] = None) -> float:
        """Mark the completion of a stage, calculate monotonic duration in milliseconds, and record."""
        t_start = self._active_stages.pop(stage_name, None)
        if t_start is None:
            return 0.0
        elapsed_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
        # Accumulate if stage occurred across multiple steps (e.g. multi-turn clarification)
        self.stage_timings[stage_name] = round(self.stage_timings.get(stage_name, 0.0) + elapsed_ms, 2)
        if details:
            self.stage_details.setdefault(stage_name, {}).update(details)
        return elapsed_ms

    def record_stage(self, stage_name: str, duration_ms: float, details: Optional[Dict[str, Any]] = None) -> None:
        """Directly record a pre-computed stage duration in milliseconds."""
        self.stage_timings[stage_name] = round(self.stage_timings.get(stage_name, 0.0) + duration_ms, 2)
        if details:
            self.stage_details.setdefault(stage_name, {}).update(details)

    def record_supplier_search(
        self,
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
    ) -> None:
        """Record the performance of an individual supplier search."""
        self.supplier_executions.append({
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "products_discovered": products_discovered,
            "products_extracted": products_extracted,
            "browser_init_ms": round(browser_init_ms, 2),
            "navigation_ms": round(navigation_ms, 2),
            "extraction_ms": round(extraction_ms, 2),
            "verification_ms": round(verification_ms, 2),
            "error_category": error_category,
            "error_message": error_message,
            "source_url": source_url,
        })

    def finish(
        self,
        status: str,
        outcome: Optional[Dict[str, Any]] = None,
        profile: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Finalize the request latency, record product quality metrics, and persist to SQLite."""
        if self._completed:
            return {"request_id": self.request_id, "status": self.status}

        self._completed = True
        self.status = status
        self.error_message = error_message

        # Calculate exact total end-to-end latency via monotonic clock
        total_latency_ms = round((time.perf_counter() - self._t0_perf) * 1000.0, 2)
        self.record_stage("total_e2e", total_latency_ms)

        outcome = outcome or {}
        profile = profile or {}

        # Check cached / demo status
        self.is_cached = bool(outcome.get("is_cached") or outcome.get("is_demo"))

        # Wall clock supplier duration
        if "supplier_search_wall_clock" in self.stage_timings:
            self.supplier_wall_clock_ms = self.stage_timings["supplier_search_wall_clock"]
        elif outcome.get("total_elapsed"):
            self.supplier_wall_clock_ms = round(float(outcome["total_elapsed"]) * 1000.0, 2)

        # Products
        listings = outcome.get("listings") or []
        self.extracted_listings = listings
        total_products = len(listings)

        # Relevant vs Irrelevant count
        relevant_count = sum(
            1 for l in listings
            if (l.get("is_relevant", True) is not False and l.get("relevance_score", 1.0) >= 0.45)
        )
        irrelevant_count = total_products - relevant_count

        # Supplier counts
        suppliers_count = len(self.supplier_executions)
        succeeded_suppliers = sum(
            1 for s in self.supplier_executions
            if s["status"] in ("completed", "ok", "no_results")
        )
        failed_suppliers = sum(
            1 for s in self.supplier_executions
            if s["status"] in ("failed", "extraction_failed", "blocked")
        )
        timed_out_suppliers = sum(
            1 for s in self.supplier_executions
            if s["status"] == "timeout"
        )

        vehicle_maker = profile.get("maker") or (profile.get("hints") or {}).get("make")
        vehicle_model = profile.get("model") or (profile.get("hints") or {}).get("model")
        vehicle_year = profile.get("year") or str((profile.get("hints") or {}).get("year") or "")
        vehicle_variant = profile.get("variant") or (profile.get("hints") or {}).get("variant")
        vehicle_label = outcome.get("vehicle_label") or " ".join(
            x for x in (vehicle_maker, vehicle_model, vehicle_year, vehicle_variant) if x
        )
        part_name = profile.get("part") or profile.get("category") or ""

        # Safe asynchronous-compatible persistence: failure MUST NEVER crash main workflow
        try:
            metrics_db.record_search_request(
                request_id=self.request_id,
                thread_id=self.thread_id,
                customer_query=self.customer_query,
                status=self.status,
                total_latency_ms=total_latency_ms,
                part_name=part_name,
                vehicle_maker=vehicle_maker,
                vehicle_model=vehicle_model,
                vehicle_year=vehicle_year,
                vehicle_variant=vehicle_variant,
                vehicle_label=vehicle_label,
                supplier_wall_clock_ms=self.supplier_wall_clock_ms,
                total_products_discovered=total_products,
                relevant_products_discovered=relevant_count,
                irrelevant_products_filtered=irrelevant_count,
                suppliers_count=suppliers_count,
                suppliers_succeeded=succeeded_suppliers,
                suppliers_failed=failed_suppliers,
                suppliers_timed_out=timed_out_suppliers,
                error_message=self.error_message,
                is_cached=self.is_cached,
                is_benchmark=self.is_benchmark,
                timestamp=self.timestamp,
            )

            # Record per-supplier executions
            for sup in self.supplier_executions:
                metrics_db.record_supplier_execution(
                    request_id=self.request_id,
                    supplier_id=sup["supplier_id"],
                    supplier_name=sup["supplier_name"],
                    status=sup["status"],
                    latency_ms=sup["latency_ms"],
                    products_discovered=sup["products_discovered"],
                    products_extracted=sup["products_extracted"],
                    browser_init_ms=sup.get("browser_init_ms", 0.0),
                    navigation_ms=sup.get("navigation_ms", 0.0),
                    extraction_ms=sup.get("extraction_ms", 0.0),
                    verification_ms=sup.get("verification_ms", 0.0),
                    error_category=sup.get("error_category"),
                    error_message=sup.get("error_message"),
                    source_url=sup.get("source_url"),
                    is_benchmark=self.is_benchmark,
                    timestamp=self.timestamp,
                )

            # Record stage timings
            for stg_name, duration_ms in self.stage_timings.items():
                metrics_db.record_stage_timing(
                    request_id=self.request_id,
                    stage_name=stg_name,
                    duration_ms=duration_ms,
                    details=self.stage_details.get(stg_name),
                )

            # Record product quality & extraction statistics
            metrics_db.record_product_quality(
                request_id=self.request_id,
                listings=self.extracted_listings,
            )

        except Exception as exc:
            log.exception("Safe metric recording caught error (main workflow unaffected): %s", exc)

        return {
            "request_id": self.request_id,
            "total_latency_ms": total_latency_ms,
            "total_latency_seconds": round(total_latency_ms / 1000.0, 2),
            "status": self.status,
            "total_products": total_products,
            "relevant_products": relevant_count,
        }


# -----------------------------------------------------------------
# Active Trackers Registry
# -----------------------------------------------------------------

ACTIVE_TRACKERS: Dict[str, RequestMetricsTracker] = {}


def create_tracker(
    request_id: str,
    thread_id: str,
    customer_query: str,
    is_benchmark: bool = False,
) -> RequestMetricsTracker:
    """Create and register a new tracker for a search session."""
    tracker = RequestMetricsTracker(
        request_id=request_id,
        thread_id=thread_id,
        customer_query=customer_query,
        is_benchmark=is_benchmark,
    )
    ACTIVE_TRACKERS[thread_id] = tracker
    ACTIVE_TRACKERS[request_id] = tracker
    return tracker


def get_tracker(key: str) -> Optional[RequestMetricsTracker]:
    """Retrieve an active tracker by thread_id or request_id."""
    return ACTIVE_TRACKERS.get(key)


def remove_tracker(key: str) -> Optional[RequestMetricsTracker]:
    """Remove a tracker from the active registry once completed."""
    tracker = ACTIVE_TRACKERS.pop(key, None)
    if tracker:
        ACTIVE_TRACKERS.pop(tracker.thread_id, None)
        ACTIVE_TRACKERS.pop(tracker.request_id, None)
    return tracker
