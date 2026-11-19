"""FastAPI Analytics and Performance Monitoring Router.

Exposes REST API endpoints for:
- Sourcing agent latency KPIs (P50, P95, P99, average, min, max)
- Stage-level component timings (LLM understanding, vehicle clarification,
  supplier wall-clock, product extraction, verification, relevance, ranking, response)
- Supplier-wise performance comparison
- Search reliability and product discovery quality metrics
- Paginated search request history and detailed inspection
- On-demand benchmark execution and real-time monitoring
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from parts_agent import metrics_db
from scripts.benchmark_runner import BenchmarkRunner, STANDARD_BENCHMARK_QUERIES

log = logging.getLogger("metrics_api")

router = APIRouter(prefix="/api/metrics", tags=["Performance Metrics & Analytics"])
benchmark_router = APIRouter(prefix="/api/benchmark", tags=["Benchmarking & Load Testing"])

# Active benchmark runner tracking
ACTIVE_BENCHMARK_RUNNER: Optional[BenchmarkRunner] = None
LATEST_BENCHMARK_RESULT: Optional[Dict[str, Any]] = None
BENCHMARK_LOCK = asyncio.Lock()


# -----------------------------------------------------------------
# Analytics Endpoints
# -----------------------------------------------------------------

@router.get("/overview")
async def get_overview(
    time_range: str = Query("24h", description="Time range: '24h', '7d', '30d', 'all'"),
    include_benchmark: bool = Query(False, description="Whether to include benchmark traffic"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD) for custom range"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD) for custom range"),
):
    """Retrieve top-level KPI metrics including latency percentiles, success rate, and discovery rate."""
    try:
        return metrics_db.get_overview_metrics(
            time_range=time_range,
            include_benchmark=include_benchmark,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        log.exception("Error in /api/metrics/overview")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/latency")
async def get_latency(
    time_range: str = Query("24h", description="Time range: '24h', '7d', '30d', 'all'"),
    include_benchmark: bool = Query(False, description="Whether to include benchmark traffic"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD) for custom range"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD) for custom range"),
):
    """Retrieve latency distribution histogram, stage breakdowns, and percentiles (P50/P95/P99)."""
    try:
        return metrics_db.get_latency_analytics(
            time_range=time_range,
            include_benchmark=include_benchmark,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        log.exception("Error in /api/metrics/latency")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suppliers")
async def get_suppliers_metrics(
    time_range: str = Query("24h", description="Time range: '24h', '7d', '30d', 'all'"),
    include_benchmark: bool = Query(False, description="Whether to include benchmark traffic"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD) for custom range"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD) for custom range"),
):
    """Retrieve per-supplier latency, success rate, product counts, and availability stats."""
    try:
        return metrics_db.get_supplier_analytics(
            time_range=time_range,
            include_benchmark=include_benchmark,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        log.exception("Error in /api/metrics/suppliers")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quality")
async def get_quality(
    time_range: str = Query("24h", description="Time range: '24h', '7d', '30d', 'all'"),
    include_benchmark: bool = Query(False, description="Whether to include benchmark traffic"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD) for custom range"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD) for custom range"),
):
    """Retrieve product discovery rates, extraction completeness, and verification coverage."""
    try:
        return metrics_db.get_search_quality_metrics(
            time_range=time_range,
            include_benchmark=include_benchmark,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        log.exception("Error in /api/metrics/quality")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_history(
    time_range: str = Query("24h", description="Time range: '24h', '7d', '30d', 'all'"),
    granularity: str = Query("hour", description="'hour' or 'day'"),
    include_benchmark: bool = Query(False, description="Whether to include benchmark traffic"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD) for custom range"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD) for custom range"),
):
    """Retrieve time-series trend of search volumes, latencies, and failure trends."""
    try:
        return metrics_db.get_history_trends(
            time_range=time_range,
            granularity=granularity,
            include_benchmark=include_benchmark,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        log.exception("Error in /api/metrics/history")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/requests")
async def get_requests(
    limit: int = Query(50, ge=1, le=200, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
    query: Optional[str] = Query(None, description="Filter by query or vehicle text"),
    status: Optional[str] = Query(None, description="Filter by status ('all', 'success', 'failed', etc.)"),
    time_range: str = Query("24h", description="Time range"),
    include_benchmark: bool = Query(False, description="Include benchmark requests"),
):
    """Retrieve paginated search request records for searchable history table."""
    try:
        return metrics_db.get_search_requests_history(
            limit=limit,
            offset=offset,
            query=query,
            status=status,
            time_range=time_range,
            include_benchmark=include_benchmark,
        )
    except Exception as e:
        log.exception("Error in /api/metrics/requests")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/requests/{request_id}")
async def get_request_by_id(request_id: str):
    """Retrieve full request detail including stage-level timings and supplier execution breakdown."""
    detail = metrics_db.get_request_detail(request_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Search request record not found")
    return detail


@router.post("/clear")
@router.delete("/clear")
async def clear_metrics_data(
    include_benchmark: bool = Query(True, description="Whether to clear benchmark metrics as well"),
):
    """Clear performance monitoring and analytics dashboard records."""
    global LATEST_BENCHMARK_RESULT
    try:
        counts = metrics_db.clear_metrics(include_benchmarks=include_benchmark)
        if include_benchmark:
            LATEST_BENCHMARK_RESULT = None
        return {
            "status": "cleared",
            "deleted": counts,
            "include_benchmark": include_benchmark,
        }
    except Exception as e:
        log.exception("Error clearing metrics data")
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------------------------------------------
# Benchmark Endpoints
# -----------------------------------------------------------------

class BenchmarkRequest(BaseModel):
    concurrency: int = Field(1, ge=1, le=20, description="Concurrency level: 1, 5, 10, or 20")
    mode: str = Field("sequential", description="'sequential' or 'concurrent'")
    queries: Optional[List[str]] = Field(None, description="Custom queries or defaults if omitted")
    timeout_seconds: float = Field(35.0, ge=5.0, le=120.0, description="Timeout per query")


@benchmark_router.post("/run")
async def run_benchmark_endpoint(payload: BenchmarkRequest):
    """Trigger a controlled benchmark evaluation run under configured workload."""
    global ACTIVE_BENCHMARK_RUNNER, LATEST_BENCHMARK_RESULT

    async with BENCHMARK_LOCK:
        if ACTIVE_BENCHMARK_RUNNER and not ACTIVE_BENCHMARK_RUNNER._cancelled:
            raise HTTPException(status_code=409, detail="A benchmark run is already in progress.")

        queries = payload.queries or list(STANDARD_BENCHMARK_QUERIES)
        runner = BenchmarkRunner(
            queries=queries,
            concurrency=payload.concurrency,
            mode=payload.mode,
            timeout_seconds=payload.timeout_seconds,
        )
        ACTIVE_BENCHMARK_RUNNER = runner

    try:
        summary = await runner.run()
        res_dict = asdict(summary)
        LATEST_BENCHMARK_RESULT = res_dict
        return {
            "status": "completed",
            "summary": res_dict,
        }
    finally:
        ACTIVE_BENCHMARK_RUNNER = None


@benchmark_router.get("/status")
async def get_benchmark_status():
    """Check current benchmark execution state and retrieve the latest summary."""
    is_running = ACTIVE_BENCHMARK_RUNNER is not None and not ACTIVE_BENCHMARK_RUNNER._cancelled
    return {
        "is_running": is_running,
        "latest_result": LATEST_BENCHMARK_RESULT,
        "default_queries": STANDARD_BENCHMARK_QUERIES,
    }


@benchmark_router.post("/cancel")
async def cancel_benchmark():
    """Cancel any active benchmark run."""
    global ACTIVE_BENCHMARK_RUNNER
    if ACTIVE_BENCHMARK_RUNNER:
        ACTIVE_BENCHMARK_RUNNER.cancel()
        ACTIVE_BENCHMARK_RUNNER = None
        return {"status": "ok", "message": "Benchmark cancelled"}
    return {"status": "ok", "message": "No active benchmark"}
