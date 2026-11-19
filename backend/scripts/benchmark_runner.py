"""High-Performance Benchmarking & Load Testing Utility.

Executes real, un-mocked automotive parts sourcing queries under controlled workloads:
- Configurable concurrency levels (1, 5, 10, 20)
- Sequential or concurrent request modes
- Realistic automotive test queries (Creta, Seltos, Nexon, Baleno, Fortuner, City, Harrier)
- Monotonic latency tracking using `time.perf_counter()`
- Calculation of Throughput (requests/sec), P50, P95, P99, Average, Min, Max
- Product discovery rate, error rate, and timeout rate
- Strictly tags all generated records with `is_benchmark = 1`
- Never runs automatically on startup
- Can be run as a CLI tool or triggered via REST API
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from parts_agent import cache_manager, metrics_db
from parts_agent.browser_manager import get_browser_manager
from parts_agent.graph import Conversation, close_session, open_session
from parts_agent.perf_tracker import create_tracker, remove_tracker

log = logging.getLogger("benchmark_runner")

# Controlled set of realistic automotive queries per requirements
STANDARD_BENCHMARK_QUERIES = [
    "Hyundai Creta Headlight",
    "Kia Seltos Front Bumper",
    "Tata Nexon Gearbox",
    "Maruti Baleno Air Filter",
    "Toyota Fortuner Side Mirror",
    "Honda City Brake Pads",
    "Tata Harrier Oil Filter",
]


@dataclass
class BenchmarkItemResult:
    query: str
    request_id: str
    thread_id: str
    latency_ms: float
    latency_seconds: float
    status: str
    products_found: int
    relevant_products: int
    error: Optional[str] = None


@dataclass
class BenchmarkSummary:
    mode: str
    concurrency: int
    total_queries: int
    successful_queries: int
    failed_queries: int
    timed_out_queries: int
    total_duration_seconds: float
    throughput_rps: float
    error_rate: float
    timeout_rate: float
    product_discovery_rate: float
    relevant_discovery_rate: float
    latency: Dict[str, float]
    queries_evaluated: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class BenchmarkRunner:
    """Manages execution of isolated benchmark suites against the sourcing agent."""

    def __init__(
        self,
        queries: Optional[List[str]] = None,
        concurrency: int = 1,
        mode: str = "sequential",  # "sequential" | "concurrent"
        timeout_seconds: float = 45.0,
        delay_between_requests: float = 0.5,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.queries = queries or list(STANDARD_BENCHMARK_QUERIES)
        self.concurrency = max(1, concurrency)
        self.mode = mode
        self.timeout_seconds = timeout_seconds
        self.delay_between_requests = delay_between_requests
        self.progress_callback = progress_callback
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def _execute_single_query(self, query: str, index: int) -> BenchmarkItemResult:
        """Execute a single query through the complete agent pipeline, measuring monotonic latency."""
        t_start = time.perf_counter()
        req_id = f"req_bench_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        thread_id = f"bench_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"

        tracker = create_tracker(
            request_id=req_id,
            thread_id=thread_id,
            customer_query=query,
            is_benchmark=True,
        )

        session = open_session(
            thread_id,
            headless=True,
            require_confirmation=False,
        )

        try:
            convo = Conversation(thread_id)
            # Execute search
            q = await asyncio.wait_for(
                convo.start(query, request_id=req_id, is_benchmark=True),
                timeout=self.timeout_seconds,
            )

            # If agent needs clarification during benchmark, reply automatically to resolve
            max_turns = 3
            turns = 0
            while q and turns < max_turns and not self._cancelled:
                turns += 1
                field_needed = q.get("field", "")
                options = q.get("options") or []

                if field_needed == "confirm_search":
                    ans = "yes"
                elif options:
                    # Pick first realistic option
                    ans = str(options[0])
                elif field_needed == "year":
                    ans = "2021"
                elif field_needed == "variant":
                    ans = "1.5 Diesel"
                else:
                    ans = "confirm"

                q = await asyncio.wait_for(
                    convo.reply(ans),
                    timeout=self.timeout_seconds,
                )

            # Sourcing complete
            state = convo.state
            outcome = state.get("outcome") or {}
            profile = state.get("profile") or {}
            status = outcome.get("status") or "completed"
            listings = outcome.get("listings") or []
            total_prods = len(listings)
            rel_prods = sum(1 for l in listings if l.get("is_relevant", True) is not False and l.get("relevance_score", 1.0) >= 0.45)

            elapsed_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
            tracker.finish(status=status, outcome=outcome, profile=profile)

            return BenchmarkItemResult(
                query=query,
                request_id=req_id,
                thread_id=thread_id,
                latency_ms=elapsed_ms,
                latency_seconds=round(elapsed_ms / 1000.0, 3),
                status=status,
                products_found=total_prods,
                relevant_products=rel_prods,
            )

        except asyncio.TimeoutError:
            elapsed_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
            tracker.finish(
                status="timeout",
                outcome={"status": "timeout", "listings": []},
                error_message=f"Benchmark query timed out after {self.timeout_seconds}s",
            )
            return BenchmarkItemResult(
                query=query,
                request_id=req_id,
                thread_id=thread_id,
                latency_ms=elapsed_ms,
                latency_seconds=round(elapsed_ms / 1000.0, 3),
                status="timeout",
                products_found=0,
                relevant_products=0,
                error="Timed out",
            )

        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
            tracker.finish(
                status="failed",
                outcome={"status": "failed", "listings": []},
                error_message=str(exc),
            )
            return BenchmarkItemResult(
                query=query,
                request_id=req_id,
                thread_id=thread_id,
                latency_ms=elapsed_ms,
                latency_seconds=round(elapsed_ms / 1000.0, 3),
                status="failed",
                products_found=0,
                relevant_products=0,
                error=str(exc),
            )

        finally:
            remove_tracker(thread_id)
            try:
                await close_session(thread_id)
            except Exception:
                pass

    async def run(self) -> BenchmarkSummary:
        """Run the benchmark suite according to the configured mode and concurrency."""
        t0_suite = time.perf_counter()
        results: List[BenchmarkItemResult] = []

        if self.mode == "concurrent" and self.concurrency > 1:
            sem = asyncio.Semaphore(self.concurrency)

            async def _worker(q: str, idx: int):
                async with sem:
                    if self._cancelled:
                        return None
                    res = await self._execute_single_query(q, idx)
                    if self.delay_between_requests > 0:
                        await asyncio.sleep(self.delay_between_requests)
                    if self.progress_callback:
                        self.progress_callback({
                            "type": "progress",
                            "index": idx + 1,
                            "total": len(self.queries),
                            "item": asdict(res),
                        })
                    return res

            tasks = [_worker(q, i) for i, q in enumerate(self.queries)]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in raw_results:
                if isinstance(r, BenchmarkItemResult):
                    results.append(r)
                elif isinstance(r, Exception):
                    log.error("Unhandled benchmark worker error: %s", r)
        else:
            # Sequential execution
            for idx, q in enumerate(self.queries):
                if self._cancelled:
                    break
                res = await self._execute_single_query(q, idx)
                results.append(res)
                if self.progress_callback:
                    self.progress_callback({
                        "type": "progress",
                        "index": idx + 1,
                        "total": len(self.queries),
                        "item": asdict(res),
                    })
                if self.delay_between_requests > 0 and idx < len(self.queries) - 1:
                    await asyncio.sleep(self.delay_between_requests)

        total_duration = round(time.perf_counter() - t0_suite, 2)
        total_q = len(results)
        succeeded = sum(1 for r in results if r.status in ("success", "completed", "completed_no_results", "ok"))
        failed = sum(1 for r in results if r.status in ("failed", "partial_failure"))
        timeouts = sum(1 for r in results if r.status == "timeout")

        # Monotonic Latencies
        lats_ms = [r.latency_ms for r in results]
        pct = metrics_db.calculate_percentiles(lats_ms)

        throughput = round((succeeded / total_duration), 2) if total_duration > 0 else 0.0
        error_rate = round((failed / total_q * 100.0), 1) if total_q > 0 else 0.0
        timeout_rate = round((timeouts / total_q * 100.0), 1) if total_q > 0 else 0.0

        searches_with_prods = sum(1 for r in results if r.products_found > 0)
        searches_with_rel = sum(1 for r in results if r.relevant_products > 0)
        prod_discovery_rate = round((searches_with_prods / total_q * 100.0), 1) if total_q > 0 else 0.0
        rel_discovery_rate = round((searches_with_rel / total_q * 100.0), 1) if total_q > 0 else 0.0

        summary = BenchmarkSummary(
            mode=self.mode,
            concurrency=self.concurrency,
            total_queries=total_q,
            successful_queries=succeeded,
            failed_queries=failed,
            timed_out_queries=timeouts,
            total_duration_seconds=total_duration,
            throughput_rps=throughput,
            error_rate=error_rate,
            timeout_rate=timeout_rate,
            product_discovery_rate=prod_discovery_rate,
            relevant_discovery_rate=rel_discovery_rate,
            latency={
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
            queries_evaluated=[asdict(r) for r in results],
        )

        return summary


# -----------------------------------------------------------------
# CLI Runner Entry Point
# -----------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Automotive Parts Sourcing Benchmark Runner")
    parser.add_argument("--concurrency", type=int, default=1, choices=[1, 5, 10, 20], help="Concurrency level")
    parser.add_argument("--mode", type=str, default="sequential", choices=["sequential", "concurrent"], help="Execution mode")
    parser.add_argument("--queries", type=int, default=7, help="Number of queries to run from test suite")
    parser.add_argument("--output", type=str, default="live_benchmark_results.json", help="Path to write JSON results")
    return parser.parse_args()


async def main():
    args = parse_args()
    selected_queries = STANDARD_BENCHMARK_QUERIES[: max(1, min(args.queries, len(STANDARD_BENCHMARK_QUERIES)))]

    print("=" * 70)
    print(" AUTOMOTIVE PARTS SOURCING AGENT - PERFORMANCE BENCHMARK RUNNER")
    print("=" * 70)
    print(f"Mode:        {args.mode.upper()}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Test Queries ({len(selected_queries)}):")
    for idx, q in enumerate(selected_queries, 1):
        print(f"  {idx}. {q}")
    print("-" * 70)

    runner = BenchmarkRunner(
        queries=selected_queries,
        concurrency=args.concurrency,
        mode=args.mode,
    )

    print("Starting benchmark run (measuring actual latencies with time.perf_counter())...\n")
    summary = await runner.run()

    print("=" * 70)
    print(" BENCHMARK RESULTS SUMMARY")
    print("=" * 70)
    print(f"Total Queries Executed:       {summary.total_queries}")
    print(f"Successful Requests:          {summary.successful_queries}")
    print(f"Failed Requests:              {summary.failed_queries}")
    print(f"Timeouts:                     {summary.timed_out_queries}")
    print(f"Total Execution Duration:     {summary.total_duration_seconds:.2f} s")
    print(f"System Throughput:            {summary.throughput_rps:.2f} req/s")
    print(f"Error Rate:                   {summary.error_rate:.1f}%")
    print(f"Timeout Rate:                 {summary.timeout_rate:.1f}%")
    print(f"Product Discovery Rate:       {summary.product_discovery_rate:.1f}%")
    print(f"Relevant Discovery Rate:      {summary.relevant_discovery_rate:.1f}%")
    print("-" * 70)
    print(" LATENCY DISTRIBUTION (Monotonic time.perf_counter)")
    print(f"Average Latency:              {summary.latency['avg_seconds']:.3f} s ({summary.latency['avg_ms']:.1f} ms)")
    print(f"P50 Latency (50th percentile): {summary.latency['p50_seconds']:.3f} s ({summary.latency['p50_ms']:.1f} ms)")
    print(f"P95 Latency (95th percentile): {summary.latency['p95_seconds']:.3f} s ({summary.latency['p95_ms']:.1f} ms)")
    print(f"P99 Latency (99th percentile): {summary.latency['p99_seconds']:.3f} s ({summary.latency['p99_ms']:.1f} ms)")
    print(f"Minimum Latency:              {summary.latency['min_seconds']:.3f} s")
    print(f"Maximum Latency:              {summary.latency['max_seconds']:.3f} s")
    print("=" * 70)

    # Save to output file
    out_path = Path(__file__).resolve().parent / args.output
    out_path.write_text(json.dumps(asdict(summary), indent=2, default=str))
    print(f"Detailed benchmark output saved to: {out_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
