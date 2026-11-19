# Performance Monitoring & Analytics System

Comprehensive documentation for the performance monitoring, benchmarking, and analytics dashboard integrated into the Automotive Parts Sourcing Agent.

---

## 1. Architecture Overview

The Performance Monitoring & Analytics System provides continuous, zero-overhead latency, reliability, component-level stage tracking, and quality benchmarking without modifying the core sourcing agent logic or compromising user workflows.

```
                    ┌──────────────────────────────────────────────┐
                    │               User Sourcing Query            │
                    └──────────────────────┬───────────────────────┘
                                           │
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                      FastAPI Request Handler (`server.py`)                      │
│            • Generates monotonic `request_id` (e.g. `req_1794993217...`)         │
│            • Initializes `RequestMetricsTracker` in `perf_tracker.py`             │
└──────────────────────────────────────────┬───────────────────────────────────────┘
                                           │
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                   LangGraph Execution Workflow (`graph.py`)                      │
│                                                                                  │
│  [Stage 1] Query Understanding (LLM / fallback) ──────────────────────────────── │
│  [Stage 2] Vehicle Clarification / Dialogue ──────────────────────────────────── │
│  [Stage 3] Concurrent Supplier Search Execution:                                 │
│      ├── Boodmo Adapter Search & HTML Fetching                                   │
│      └── AutoDukan Adapter Search & HTML Fetching                                │
│      (Tracked as true wall-clock duration via monotonic timer)                    │
│  [Stage 4] HTML Fetching & Rendering                                             │
│  [Stage 5] Product Extraction (Title, Price, Part#, Brand, Stock, URL)            │
│  [Stage 6] Product Verification & Compatibility Checks                            │
│  [Stage 7] Relevance Filtering                                                   │
│  [Stage 8] Ranking & Sorting                                                     │
│  [Stage 9] Final Response Generation                                             │
│  [Stage 10] Total End-to-End Search Completion                                   │
└──────────────────────────────────────────┬───────────────────────────────────────┘
                                           │
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│             Persistent SQLite Database (`backend/data/parts_cache.db`)           │
│  • WAL Mode (Write-Ahead Logging) for high concurrency                           │
│  • Tables: `metrics_search_requests`, `metrics_supplier_executions`,              │
│            `metrics_stage_timings`, `metrics_product_quality`                    │
└──────────────────────────────────────────┬───────────────────────────────────────┘
                                           │
                     ┌─────────────────────┴─────────────────────┐
                     ▼                                           ▼
┌─────────────────────────────────────────┐ ┌──────────────────────────────────────┐
│       FastAPI Analytics Endpoints       │ │       Benchmark Runner CLI & UI      │
│     `GET /api/metrics/overview`         │ │   `backend/scripts/benchmark_runner` │
│     `GET /api/metrics/latency`          │ │   • Concurrency: 1, 5, 10, 20        │
│     `GET /api/metrics/suppliers`        │ │   • Realistic automotive test pack   │
│     `GET /api/metrics/quality`          │ │   • Monotonic latency & throughput   │
│     `GET /api/metrics/history`          │ │   • Tagged with `is_benchmark = 1`   │
│     `GET /api/metrics/requests`         │ └──────────────────────────────────────┘
└────────────────────┬────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│              Frontend React SaaS Performance Dashboard (`Header.jsx`)            │
│  • KPI Overview Cards (Total Searches, Success Rate, P50/P95/P99, Discovery)      │
│  • Latency Distribution Histogram & Historical SVG Trend Line                    │
│  • Supplier-wise Comparison Matrix & Availability Analytics                      │
│  • Search Quality & 7-Field Extraction Completeness vs Verification Coverage     │
│  • Searchable Request History Table & Stage Drill-Down Inspection Modal          │
│  • Live Load Testing & Benchmark Execution Panel                                 │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Latency Measurement & Mathematical Formulations

### Monotonic Timing
All timing measurements use Python's high-resolution monotonic timer:
```python
import time
t_start = time.perf_counter()
# ... executed work ...
latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
```
- **Monotonic Guarantee:** `time.perf_counter()` is unaffected by system clock adjustments, NTP drifts, or daylight savings.
- **Storage vs Presentation:** Latency values are stored internally in **milliseconds (`ms`)** with 2 decimal places in SQLite and converted to **seconds (`s`)** in the dashboard presentation layer (`(latency_ms / 1000.0).toFixed(3) + "s"`).

### Percentile Calculation Method (NIST Type 7 Linear Interpolation)
Percentiles (P50, P95, P99) are computed deterministically using standard linear interpolation on sorted data points:
Given an ordered list of $N$ latencies $x_0 \le x_1 \le \dots \le x_{N-1}$:
1. Compute the fractional rank $r$:
   $$r = \frac{p}{100} \times (N - 1)$$
2. Let $k = \lfloor r \rfloor$ (the integer floor) and $f = r - k$ (the fractional part).
3. The interpolated percentile value $P_p$ is:
   $$P_p = x_k + f \times (x_{k+1} - x_k)$$

#### Edge Cases Handled:
- **$N = 0$ (No requests recorded):** Returns `0.0` for all percentiles, average, min, and max.
- **$N = 1$ (Single request):** Returns $x_0$ for all percentiles (P50 = P95 = P99 = min = max = avg).
- **Outliers:** Sorting and rank-based interpolation prevents single high outliers from skewing P50 while faithfully capturing them in P95 and P99.

---

## 3. Component-Level 10-Stage Performance Tracking

The sourcing pipeline measures the 10 critical operational stages:

| # | Stage Identifier | Monitored Operation | Description |
|---|---|---|---|
| 1 | `query_understanding` | LLM Parsing & Extraction | Query intent extraction via Gemini or regex fallback |
| 2 | `vehicle_clarification` | Vehicle Dialogue & Identification | Model/variant resolution and conversational clarification turns |
| 3 | `supplier_search_wall_clock` | Supplier Searches Wall-Clock | Monotonic wall-clock time spanning concurrent supplier requests |
| 4 | `html_fetching_rendering` | Browser / Network Navigation | Dynamic DOM rendering and Playwright navigation duration |
| 5 | `product_extraction` | Attribute Scraping & Parsing | Parsing raw product cards, price, part numbers, and specs |
| 6 | `product_verification` | Verification & Fitment Checks | Supplier fitment verification and catalogue match checks |
| 7 | `relevance_filtering` | Relevance Scoring | Cross-checking query criteria against part title and tags |
| 8 | `ranking_sorting` | Results Ranking | Sorting listings by relevance, in-stock status, and price |
| 9 | `response_generation` | Final Response Assembly | Formatting listings, summary response, and audit log generation |
| 10 | `total_e2e_latency` | End-to-End Duration | Monotonic time from request receipt to final response dispatch |

### Concurrent Supplier Timing
In accordance with system requirements, **wall-clock duration of concurrent supplier searches is strictly measured using monotonic time across the concurrent execution block, NOT by summing individual supplier durations**:
```python
t_wall_start = time.perf_counter()
# Supplier searches execute concurrently via asyncio.as_completed or gather:
supplier_results = await asyncio.gather(*supplier_tasks, return_exceptions=True)
wall_clock_ms = round((time.perf_counter() - t_wall_start) * 1000.0, 2)
```
Each supplier's individual execution duration is tracked separately in `metrics_supplier_executions`.

---

## 4. Supplier-Wise Analytics & Future Supplier Extensibility

The system maintains separate statistics for each supplier adapter without hardcoding supplier identities. Any new supplier adapter registered in `session.adapters` will automatically be tracked and reflected in analytics.

For every supplier, the system computes:
- **Total Requests:** Count of searches delegated to this supplier.
- **Successful Searches:** Searches completing with status `ok` or `completed`.
- **Failed Searches:** Searches encountering errors or network failures.
- **Timeout Count:** Searches exceeding supplier deadline thresholds.
- **Availability / Response Rate (%):**
  $$\text{Response Rate} = \frac{\text{Successful Searches}}{\text{Total Requests}} \times 100$$
- **Latency Percentiles:** P50, P95, P99, and Average latency in milliseconds and seconds.
- **Products Discovered & Extracted:** Cumulative and per-search count of listings yielded.

---

## 5. Reliability & Search Quality Metrics

### Reliability Rates
- **Success Rate (%):** $\frac{\text{Successful Searches}}{\text{Total Searches}} \times 100$
- **Failure Rate (%):** $\frac{\text{Failed Searches}}{\text{Total Searches}} \times 100$
- **Timeout Rate (%):** $\frac{\text{Timed Out Searches}}{\text{Total Searches}} \times 100$

### Product Discovery Rates
A clear distinction is maintained between finding any product vs. finding a verified relevant product:
- **Any-Product Discovery Rate (%):** Searches returning at least one product listing.
- **Relevant Product Discovery Rate (%):** Searches returning at least one product with `relevance_score >= 0.45` and compatible attributes.
- **Zero-Result Searches:** Total count of completed searches yielding 0 listings.

### Extraction Completeness vs. Verification Coverage
> **CRITICAL METHODOLOGY NOTE:**
> Extraction success is never equated to 100% accuracy.
> - **Extraction Completeness:** Measures whether key structured attributes were successfully extracted from supplier listings. Evaluated across 7 standard automotive fields:
>   1. Product Title (`has_title`)
>   2. Price (`has_price`)
>   3. Part Number / OEM Number (`has_part_number`)
>   4. Brand / Manufacturer (`has_brand`)
>   5. Vehicle Compatibility (`has_compatibility`)
>   6. Stock Availability (`has_stock`)
>   7. Product URL (`has_url`)
> - **Verification Coverage:** Measures the percentage of extracted products that were independently cross-verified against fitment or catalogue databases (e.g. Boodmo vehicle fitment filters).

---

## 6. Persistent Database Schema (SQLite with WAL)

All metrics persist across backend restarts in `backend/data/parts_cache.db` (configured with WAL mode for non-blocking concurrent reads and writes).

```sql
-- 1. Search Request Records
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
    status TEXT NOT NULL,                -- success, partial_success, completed_no_results, failed, timeout
    total_latency_ms REAL NOT NULL,
    supplier_wall_clock_ms REAL DEFAULT 0.0,
    total_products_discovered INTEGER DEFAULT 0,
    relevant_products_discovered INTEGER DEFAULT 0,
    zero_results INTEGER DEFAULT 0,
    error_message TEXT,
    is_benchmark INTEGER DEFAULT 0       -- 0 = live customer query, 1 = benchmark run
);

-- 2. Supplier Execution Records
CREATE TABLE IF NOT EXISTS metrics_supplier_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    supplier_name TEXT NOT NULL,
    status TEXT NOT NULL,                -- ok, no_results, failed, timeout, blocked
    latency_ms REAL NOT NULL,
    products_discovered INTEGER DEFAULT 0,
    products_extracted INTEGER DEFAULT 0,
    cache_hit INTEGER DEFAULT 0,
    error_category TEXT,
    error_message TEXT,
    timestamp REAL NOT NULL,
    FOREIGN KEY(request_id) REFERENCES metrics_search_requests(request_id)
);

-- 3. Component Stage Timings
CREATE TABLE IF NOT EXISTS metrics_stage_timings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    timestamp REAL NOT NULL,
    FOREIGN KEY(request_id) REFERENCES metrics_search_requests(request_id)
);

-- 4. Product Quality & Extraction Attributes
CREATE TABLE IF NOT EXISTS metrics_product_quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    supplier_name TEXT NOT NULL,
    product_title TEXT,
    has_title INTEGER DEFAULT 0,
    has_price INTEGER DEFAULT 0,
    has_part_number INTEGER DEFAULT 0,
    has_brand INTEGER DEFAULT 0,
    has_compatibility INTEGER DEFAULT 0,
    has_stock INTEGER DEFAULT 0,
    has_url INTEGER DEFAULT 0,
    is_verified INTEGER DEFAULT 0,
    is_relevant INTEGER DEFAULT 1,
    relevance_score REAL DEFAULT 1.0,
    timestamp REAL NOT NULL,
    FOREIGN KEY(request_id) REFERENCES metrics_search_requests(request_id)
);
```

---

## 7. Backend API Endpoints

All endpoints are mounted on FastAPI under `/api/metrics/` and `/api/benchmark/`.

### Query Parameters Supported Across Analytics Endpoints:
- `time_filter`: `24h` (default), `7d`, `30d`, `all`, or `custom`.
- `start_date`: ISO timestamp string (e.g. `2026-11-01T00:00:00Z`) when `time_filter=custom`.
- `end_date`: ISO timestamp string when `time_filter=custom`.
- `include_benchmark`: `false` (default: only live customer traffic) or `true` (includes synthetic benchmark traffic).

### 1. `GET /api/metrics/overview`
Returns high-level KPI cards data.
```json
{
  "total_searches": 42,
  "successful_searches": 39,
  "failed_searches": 2,
  "timed_out_searches": 1,
  "success_rate": 92.9,
  "failure_rate": 4.8,
  "timeout_rate": 2.4,
  "avg_latency_ms": 2850.5,
  "avg_latency_seconds": 2.851,
  "p50_latency_ms": 2420.0,
  "p50_latency_seconds": 2.420,
  "p95_latency_ms": 5210.0,
  "p95_latency_seconds": 5.210,
  "p99_latency_ms": 8450.0,
  "p99_latency_seconds": 8.450,
  "min_latency_seconds": 0.812,
  "max_latency_seconds": 9.120,
  "total_products_discovered": 184,
  "product_discovery_rate": 88.1,
  "relevant_discovery_rate": 81.0,
  "zero_result_searches": 4
}
```

### 2. `GET /api/metrics/latency`
Returns latency percentiles, histogram distribution buckets, historical time-series trends, and stage duration breakdown.
```json
{
  "percentiles": {
    "count": 42,
    "p50_seconds": 2.42,
    "p95_seconds": 5.21,
    "p99_seconds": 8.45,
    "avg_seconds": 2.851,
    "min_seconds": 0.812,
    "max_seconds": 9.12
  },
  "distribution_histogram": [
    { "label": "< 2s", "count": 18, "percentage": 42.9 },
    { "label": "2s - 5s", "count": 19, "percentage": 45.2 },
    { "label": "5s - 10s", "count": 4, "percentage": 9.5 },
    { "label": "10s - 20s", "count": 1, "percentage": 2.4 },
    { "label": ">= 20s", "count": 0, "percentage": 0.0 }
  ],
  "historical_trends": [
    { "bucket": "2026-11-18 10:00", "p50_seconds": 2.3, "p95_seconds": 4.8, "count": 12 }
  ],
  "stage_durations": [
    { "stage": "query_understanding", "avg_duration_ms": 120.4, "avg_duration_seconds": 0.120 },
    { "stage": "supplier_search_wall_clock", "avg_duration_ms": 2150.8, "avg_duration_seconds": 2.151 }
  ]
}
```

### 3. `GET /api/metrics/suppliers`
Returns comparative supplier performance analytics.
```json
[
  {
    "supplier": "Boodmo",
    "total_requests": 42,
    "successful_searches": 40,
    "failed_searches": 1,
    "timed_out_searches": 1,
    "success_rate": 95.2,
    "response_rate": 95.2,
    "avg_latency_seconds": 2.15,
    "p50_latency_seconds": 1.95,
    "p95_latency_seconds": 4.10,
    "p99_latency_seconds": 6.80,
    "total_products_discovered": 114,
    "total_products_extracted": 114
  },
  {
    "supplier": "AutoDukan",
    "total_requests": 42,
    "successful_searches": 38,
    "failed_searches": 3,
    "timed_out_searches": 1,
    "success_rate": 90.5,
    "response_rate": 90.5,
    "avg_latency_seconds": 2.45,
    "p50_latency_seconds": 2.20,
    "p95_latency_seconds": 4.80,
    "p99_latency_seconds": 7.10,
    "total_products_discovered": 70,
    "total_products_extracted": 70
  }
]
```

### 4. `GET /api/metrics/quality`
Returns search quality breakdown and extraction completeness percentages across the 7 fields.
```json
{
  "total_products": 184,
  "relevant_products": 158,
  "irrelevant_products": 26,
  "zero_result_searches": 4,
  "extraction_completeness": {
    "total_records": 184,
    "title_extracted_pct": 100.0,
    "price_extracted_pct": 98.4,
    "part_number_extracted_pct": 91.3,
    "brand_extracted_pct": 96.7,
    "compatibility_extracted_pct": 89.1,
    "stock_extracted_pct": 94.6,
    "url_extracted_pct": 100.0,
    "average_completeness_pct": 95.7
  },
  "verification_coverage": {
    "total_records": 184,
    "verified_products_count": 114,
    "unverified_products_count": 70,
    "verification_coverage_pct": 62.0
  }
}
```

### 5. `GET /api/metrics/history`
Returns aggregated time-series trends (search volume, success vs failure, latency) grouped by hour or day.

### 6. `GET /api/metrics/requests`
Returns paginated list of search requests with query, status, latency, supplier breakdown, and products discovered.
- Query parameters: `page`, `page_size`, `query_filter`, `status_filter`.

### 7. `GET /api/metrics/requests/{request_id}`
Returns granular execution details for a single search request, including:
- Top-level request summary and status.
- Stage-by-stage timings breakdown.
- Supplier-by-supplier execution status, latency, and product counts.
- Extracted product quality records.

### 8. Benchmark Endpoints
- `POST /api/benchmark/start`: Triggers asynchronous benchmark execution. Accepts `{"concurrency": 5, "mode": "concurrent", "queries_count": 7}`.
- `GET /api/benchmark/status`: Returns current state of active benchmark runner or summary of last run.
- `POST /api/benchmark/cancel`: Safely cancels currently running benchmark.

---

## 8. Frontend Performance Analytics Dashboard

The dashboard provides a SaaS-style interface designed for executive presentations:

### Accessing the Dashboard
1. Open the application at `http://localhost:5173`.
2. In the top navigation header, click the **"Performance Analytics"** tab (adjacent to the **"Sourcing Agent"** tab).
3. The dashboard loads instantaneously without refreshing the page or interrupting active agent conversations.

### Key Sections:
- **Header Filters:** Time filters (`Last 24 Hours`, `Last 7 Days`, `Last 30 Days`, `All Time`, `Custom Range`), `Include Benchmark Data` toggle, and manual refresh button.
- **KPI Cards:** Overview of total searches, success rate, average latency, P50, P95, P99, and total products discovered.
- **Latency Distribution & Stage Breakdown:** Visualizes latency histogram, historical percentile trend chart, and a duration bar breakdown of all 10 stages.
- **Supplier Performance Comparison:** Side-by-side performance cards and comparison matrix detailing supplier requests, success rates, P95 latencies, and discovered products.
- **Search Quality & Extraction Completeness:** 7-attribute extraction completeness progress meters and verification coverage breakdown.
- **Searchable Request History & Drill-Down Modal:** Click any search request to open a modal displaying all stage timings, supplier executions, and error traces.
- **Interactive Load Testing & Benchmark Panel:** Execute controlled load tests directly from the browser with configurable concurrency (1, 5, 10, 20).

---

## 9. Benchmark & Load Testing Runner

The standalone benchmark runner (`backend/scripts/benchmark_runner.py`) enables automated performance evaluation under controlled workloads.

### Key Characteristics:
- **No Mock Latencies:** Measures actual wall-clock execution using `time.perf_counter()`.
- **Benchmark Tagging:** Persisted records are tagged with `is_benchmark=1` so real customer analytics remain pristine unless the user toggles benchmark visibility.
- **Rate-Limit Friendly:** Implements sensible inter-request delays and concurrency controls to prevent overloading supplier websites.

### Realistic Query Pack:
1. `Hyundai Creta Headlight`
2. `Kia Seltos Front Bumper`
3. `Tata Nexon Gearbox`
4. `Maruti Baleno Air Filter`
5. `Toyota Fortuner Side Mirror`
6. `Honda City Brake Pads`
7. `Tata Harrier Oil Filter`

### CLI Usage:
```bash
# Run 7 realistic queries sequentially:
python3 backend/scripts/benchmark_runner.py --concurrency 1 --queries 7 --mode sequential

# Run concurrent load test with concurrency level 5:
python3 backend/scripts/benchmark_runner.py --concurrency 5 --queries 7 --mode concurrent

# Run high-concurrency stress test (concurrency 10 or 20):
python3 backend/scripts/benchmark_runner.py --concurrency 10 --queries 7 --mode concurrent
python3 backend/scripts/benchmark_runner.py --concurrency 20 --queries 7 --mode concurrent
```

### CLI Output Example:
```
======================================================================
 AUTOMOTIVE PARTS SOURCING AGENT - PERFORMANCE BENCHMARK RUNNER
======================================================================
Mode:        CONCURRENT
Concurrency: 5
Test Queries (7):
  1. Hyundai Creta Headlight
  2. Kia Seltos Front Bumper
  3. Tata Nexon Gearbox
  4. Maruti Baleno Air Filter
  5. Toyota Fortuner Side Mirror
  6. Honda City Brake Pads
  7. Tata Harrier Oil Filter
----------------------------------------------------------------------
Starting benchmark run (measuring actual latencies with time.perf_counter())...

======================================================================
 BENCHMARK RESULTS SUMMARY
======================================================================
Total Queries Executed:       7
Successful Requests:          7
Failed Requests:              0
Timeouts:                     0
Total Execution Duration:     8.42 s
System Throughput:            0.83 req/s
Error Rate:                   0.0%
Timeout Rate:                 0.0%
Product Discovery Rate:       100.0%
Relevant Discovery Rate:      100.0%
----------------------------------------------------------------------
 LATENCY DISTRIBUTION (Monotonic time.perf_counter)
Average Latency:              3.120 s (3120.4 ms)
P50 Latency (50th percentile): 2.940 s (2940.1 ms)
P95 Latency (95th percentile): 4.810 s (4810.0 ms)
P99 Latency (99th percentile): 5.120 s (5120.0 ms)
Minimum Latency:              1.820 s
Maximum Latency:              5.210 s
======================================================================
Detailed benchmark output saved to: backend/scripts/live_benchmark_results.json
```

---

## 10. Automated Test Suite

A comprehensive unit test suite with 18 dedicated tests is located at `backend/tests/test_performance_monitoring.py`.

### Test Coverage:
1. `test_percentiles_calculation_nist`: Validates linear interpolation percentiles against exact mathematical rank.
2. `test_percentiles_edge_cases`: Validates empty sets ($N=0$), single items ($N=1$), and identical values.
3. `test_latency_outlier_resilience`: Verifies P50 remains stable while P99 captures extreme latency spikes.
4. `test_request_tracker_monotonic_timing`: Confirms latency is measured monotonically in milliseconds.
5. `test_stage_timing_recording`: Verifies all 10 component stages are properly tracked.
6. `test_concurrent_supplier_timing_not_additive`: Verifies wall-clock timing is non-additive across concurrent suppliers.
7. `test_database_persistence_across_reconnect`: Verifies records persist across database reconnection / restarts.
8. `test_metrics_overview_api`: Validates schema and values of `/api/metrics/overview`.
9. `test_metrics_latency_api`: Validates latency histogram and percentiles API.
10. `test_metrics_suppliers_api`: Validates supplier statistics calculation.
11. `test_metrics_quality_api`: Validates completeness across all 7 fields and verification coverage.
12. `test_metrics_history_api`: Validates time-series trend aggregation.
13. `test_metrics_requests_api`: Validates request search, pagination, and detail inspection.
14. `test_benchmark_isolation`: Confirms benchmark queries are tagged with `is_benchmark=1`.
15. `test_error_and_timeout_tracking`: Verifies failed and timed out queries are accurately recorded.
16. `test_discovery_rates_distinction`: Verifies distinction between any-product and relevant-product discovery rates.
17. `test_failsafe_monitoring_non_crashing`: Confirms monitoring errors never crash main sourcing workflow.
18. `test_benchmark_runner_summary`: Validates benchmark runner calculations for throughput, error rates, and percentiles.

### Running the Tests:
```bash
# Run Performance Monitoring test suite:
python3 -m unittest backend/tests/test_performance_monitoring.py

# Run all backend unit tests:
python3 -m unittest discover -s backend/tests -p "test_*.py"

# Run frontend tests:
npm --prefix frontend test -- --run
```

---

## 11. Modified & Created Files Summary

### Newly Created Files:
- [`backend/parts_agent/metrics_db.py`](file:///home/habilelabs/Development/CAS/backend/parts_agent/metrics_db.py): SQLite schema, WAL configuration, NIST Type 7 percentile calculations, and aggregation queries.
- [`backend/parts_agent/perf_tracker.py`](file:///home/habilelabs/Development/CAS/backend/parts_agent/perf_tracker.py): High-resolution monotonic latency tracking, 10-stage component timing, and fail-safe persistence.
- [`backend/metrics_api.py`](file:///home/habilelabs/Development/CAS/backend/metrics_api.py): FastAPI endpoints for metrics overview, latency distribution, supplier analytics, quality metrics, history, requests, and benchmarks.
- [`backend/scripts/benchmark_runner.py`](file:///home/habilelabs/Development/CAS/backend/scripts/benchmark_runner.py): Standalone CLI & programmatic benchmark runner supporting sequential & concurrent execution (1, 5, 10, 20).
- [`backend/tests/test_performance_monitoring.py`](file:///home/habilelabs/Development/CAS/backend/tests/test_performance_monitoring.py): 18 comprehensive unit tests.
- [`frontend/src/components/analytics/AnalyticsDashboard.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/analytics/AnalyticsDashboard.jsx): Main dashboard container with time filters and benchmark toggle.
- [`frontend/src/components/analytics/OverviewCards.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/analytics/OverviewCards.jsx): 8 SaaS-style KPI metric cards.
- [`frontend/src/components/analytics/LatencyCharts.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/analytics/LatencyCharts.jsx): Latency distribution histogram, historical trend chart, and 10-stage breakdown.
- [`frontend/src/components/analytics/SupplierComparison.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/analytics/SupplierComparison.jsx): Comparative supplier table and response rate analytics.
- [`frontend/src/components/analytics/SearchQuality.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/analytics/SearchQuality.jsx): Product discovery rates, 7-field extraction completeness bars, and verification coverage.
- [`frontend/src/components/analytics/RequestHistory.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/analytics/RequestHistory.jsx): Searchable request table with stage-level inspection modal.
- [`frontend/src/components/analytics/BenchmarkPanel.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/analytics/BenchmarkPanel.jsx): Load testing interface with concurrency selectors and live progress.
- [`docs/PERFORMANCE_MONITORING.md`](file:///home/habilelabs/Development/CAS/docs/PERFORMANCE_MONITORING.md): Comprehensive system documentation.

### Modified Files:
- [`backend/parts_agent/graph.py`](file:///home/habilelabs/Development/CAS/backend/parts_agent/graph.py): Integrated stage-level timing instrumentation into the LangGraph loop and wall-clock supplier timing.
- [`backend/server.py`](file:///home/habilelabs/Development/CAS/backend/server.py): Mounted metrics routers and wired `RequestMetricsTracker` to session lifecycle.
- [`frontend/src/App.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/App.jsx): Added view switcher state and conditional rendering for `<AnalyticsDashboard />`.
- [`frontend/src/components/Header.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/Header.jsx): Added "Sourcing Agent" vs "Performance Analytics" toggle buttons.
- [`frontend/src/services/api.js`](file:///home/habilelabs/Development/CAS/frontend/src/services/api.js): Added REST client methods for metrics and benchmark endpoints.
- [`README.md`](file:///home/habilelabs/Development/CAS/README.md): Updated documentation with setup, benchmark commands, and dashboard instructions.
