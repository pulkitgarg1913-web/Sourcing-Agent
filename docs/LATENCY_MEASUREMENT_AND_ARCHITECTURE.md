# Performance Latency Measurement & Architecture Specification

## Executive Overview

This document specifies the latency measurement architecture, mathematical formulas, component-level stage tracking, and persistent analytics implemented for the **Conversational Automobile Parts Sourcing Agent**.

Because graphic/mermaid diagrams may not render across all environments, this document uses **clear ASCII text diagrams, mathematical formulas, numerical examples, and structured reference tables**.

---

## 1. High-Level Latency Architecture (Text Diagram)

```
[ Customer / Benchmark Ingress ]
             |
             v  <--- RECORD START TIME: t0 = time.perf_counter()
+-----------------------------------------------------------------------------------------+
|                                    SEARCH PIPELINE                                      |
|                                                                                         |
|  [ Stage 1: LLM Query Understanding ]                                                   |
|      Duration: t_query_understanding                                                    |
|             |                                                                           |
|             v                                                                           |
|  [ Stage 2: Vehicle Clarification Dialogue (Multi-Turn) ]                               |
|      Duration: t_vehicle_clarification                                                  |
|             |                                                                           |
|             v                                                                           |
|  [ Stage 3: Concurrent Supplier Search ] <--- t_wall_start = time.perf_counter()         |
|      +-------------------------------------------------------------------------------+  |
|      |  PARALLEL EXECUTION (asyncio.gather)                                          |  |
|      |                                                                               |  |
|      |  [ Supplier A: Boodmo ]       [ Supplier B: AutoDukan ]                       |  |
|      |    Duration: t_boodmo              Duration: t_autodukan                      |  |
|      |    (e.g., 3.8s)                    (e.g., 4.5s)                               |  |
|      |                                                                               |  |
|      |  WALL-CLOCK DURATION = max(t_boodmo, t_autodukan) = 4.5s                      |  |
|      |  (NOTE: NEVER summed as 3.8s + 4.5s = 8.3s)                                   |  |
|      +-------------------------------------------------------------------------------+  |
|      <--- t_wall_clock = time.perf_counter() - t_wall_start                             |
|             |                                                                           |
|             v                                                                           |
|  [ Stage 4: HTML Fetching & Playwright DOM Rendering ]                                  |
|             |                                                                           |
|             v                                                                           |
|  [ Stage 5: Product Extraction (Scraping title, price, brand, part #) ]                 |
|             |                                                                           |
|             v                                                                           |
|  [ Stage 6: Product Verification (Ground-truth & catalog fitment check) ]               |
|             |                                                                           |
|             v                                                                           |
|  [ Stage 7: Relevance Filtering (Multi-factor scoring, threshold = 0.45) ]              |
|             |                                                                           |
|             v                                                                           |
|  [ Stage 8: Result Ranking & Sorting (Relevance, price, supplier priority) ]            |
|             |                                                                           |
|             v                                                                           |
|  [ Stage 9: Final Response Generation ]                                                 |
|             |                                                                           |
|             v  <--- RECORD STOP TIME: t_end = time.perf_counter()                       |
|  TOTAL END-TO-END LATENCY = (t_end - t0) * 1000.0 ms                                    |
+-----------------------------------------------------------------------------------------+
             |
             v
[ Async Persistence Layer (Zero-Downtime Non-Blocking) ]
             |
             +---> PostgreSQL (`sourcing_agent` on Port 5433)
             +---> Fallback SQLite (`parts_cache.db`)
             |
             v
[ REST API (`/api/metrics/*`) ] ---> [ SaaS Analytics Dashboard UI ]
```

---

## 2. High-Resolution Timer Mechanics (`time.perf_counter()`)

### Why Monotonic Time is Mandatory
Latency measurements must be strictly immune to clock drift, daylight saving transitions, NTP time synchronization shifts, or manual system time changes.

| Clock Type | Behavior During NTP Drift / Leap Seconds | Suitability for Latency |
|---|---|---|
| `time.time()` | Can jump backward or forward unexpectedly | **Unsuitable** (can produce negative or inflated latency) |
| `time.perf_counter()` | **Monotonic guarantee**: always strictly increments | **Industry Standard** (sub-microsecond precision) |

### Implementation Detail
In `backend/parts_agent/perf_tracker.py`:
```python
self._t0_perf = time.perf_counter()  # At start of request
...
total_latency_ms = (time.perf_counter() - self._t0_perf) * 1000.0  # At end of request
```

---

## 3. Storage vs. Presentation Units

1. **Storage Unit (Internal Database):**
   - Stored in **milliseconds** (`float` / `REAL`) in PostgreSQL and SQLite.
   - Preserves high numerical resolution without truncation or rounding loss across mathematical percentile calculations.
2. **Display Unit (Dashboard & API):**
   - Displayed in **seconds** formatted to 3 decimal places (e.g., `1.425 s` or `0.045 s`) for clarity in SaaS management dashboards.

---

## 4. Component-Level Stage Tracking

Every request is broken down into 10 component stages via `tracker.track_stage(stage_name)`:

| Stage Identifier | Pipeline Component | What is Measured |
|---|---|---|
| `query_understanding` | LLM Intent & Entity Extraction | Latency of Gemini API call extracting intent, maker, model, part |
| `vehicle_clarification` | Vehicle Dialogue Resolution | Wall-clock time across clarification questions (year, fuel, trim) |
| `supplier_search_wall_clock` | Concurrent Supplier Retrieval | True wall-clock duration of parallel supplier execution |
| `html_fetching_rendering` | Headless Chromium Browser | Navigation, page rendering, and DOM hydration time |
| `product_extraction` | Data Scraping & Normalization | Parsing HTML tables, cards, pricing, and part numbers |
| `product_verification` | Fitment & Verification | OEM number cross-referencing and catalog fitment check |
| `relevance_filtering` | Scoring & Outlier Filtering | Evaluating titles against keywords; filtering out irrelevant parts |
| `ranking_sorting` | Prioritization Engine | Ordering items by relevance score, in-stock status, and price |
| `response_generation` | Natural Language Synthesis | Final conversational answer and structured payload generation |
| `total_e2e` | End-to-End Latency | Total duration from request ingress to HTTP dispatch |

---

## 5. Concurrent Supplier Wall-Clock Latency (Text Diagram)

### The Concurrent Latency Rule
When multiple suppliers are searched simultaneously using `asyncio.gather()`, individual latencies **must never be added together**.

```
SERIAL EXECUTION (WRONG):
Boodmo:   |================ 3.8s ================|
AutoDukan:                                        |================ 4.5s ================|
Reported (Sum): 3.8s + 4.5s = 8.3s  <--- ARTIFICIALLY HIGH & INCORRECT

CONCURRENT EXECUTION (CORRECT & IMPLEMENTED):
Boodmo:   |================ 3.8s ================|
AutoDukan: |==================== 4.5s ====================|
Real Wall-Clock Duration: max(3.8s, 4.5s) = 4.5s  <--- TRUE USER LATENCY
```

### Data Captured per Supplier
For every supplier, separate metrics are recorded:
- Total requests executed
- Successful searches
- Failed searches
- Timeout count
- Blocked / bot-challenge count
- Individual supplier latency (P50, P95, P99, Average)
- Products discovered & extracted
- Availability / response rate (%)

---

## 6. Mathematical Calculation of Percentiles (P50, P95, P99)

### Algorithm: NIST Type 7 Linear Interpolation
The system implements **NIST Type 7 Linear Interpolation** (the default method in NumPy, SciPy, Python 3.8+ `statistics.quantiles(..., method='inclusive')`, and Excel `PERCENTILE.INC`).

#### Formula
Given a sorted list of $N$ non-negative latencies:
$$v_0 \le v_1 \le v_2 \le \dots \le v_{N-1}$$

To calculate the percentile for percentage $p \in [0, 100]$:

1. **Calculate the Rank Index $r$:**
   $$r = \frac{p}{100} \times (N - 1)$$

2. **Split $r$ into Integer and Fractional Components:**
   $$k = \lfloor r \rfloor \quad (\text{integer part})$$
   $$d = r - k \quad (\text{fractional part, } 0 \le d < 1)$$

3. **Interpolate Between Adjacent Ordered Ranks:**
   $$\text{Percentile}(p) = \begin{cases} 
   v_{N-1} & \text{if } k \ge N - 1 \\ 
   v_k + d \times (v_{k+1} - v_k) & \text{otherwise} 
   \end{cases}$$

4. **Average (Mean) Latency:**
   $$\bar{v} = \frac{1}{N} \sum_{i=0}^{N-1} v_i$$

---

### Step-by-Step Numerical Example

Suppose a test run yields $N = 5$ latencies (in milliseconds):
$$\text{Sorted Values: } [120, 450, 780, 1200, 5400]$$

#### 1. Calculating P50 (Median, $p = 50$):
- Rank index: $r = \frac{50}{100} \times (5 - 1) = 0.50 \times 4 = 2.0$
- Integer index: $k = 2$, fraction: $d = 0.0$
- Value: $v_2 + 0.0 \times (v_3 - v_2) = v_2 = \mathbf{780\text{ ms}}$

#### 2. Calculating P95 ($p = 95$):
- Rank index: $r = \frac{95}{100} \times (5 - 1) = 0.95 \times 4 = 3.8$
- Integer index: $k = 3$, fraction: $d = 0.8$
- Interpolation:
  $$\text{P95} = v_3 + 0.8 \times (v_4 - v_3)$$
  $$\text{P95} = 1200 + 0.8 \times (5400 - 1200) = 1200 + 0.8 \times 4200 = 1200 + 3360 = \mathbf{4560\text{ ms}}$$

#### 3. Calculating P99 ($p = 99$):
- Rank index: $r = \frac{99}{100} \times (5 - 1) = 0.99 \times 4 = 3.96$
- Integer index: $k = 3$, fraction: $d = 0.96$
- Interpolation:
  $$\text{P99} = 1200 + 0.96 \times 4200 = 1200 + 4032 = \mathbf{5232\text{ ms}}$$

#### 4. Calculating Average:
- $\text{Average} = \frac{120 + 450 + 780 + 1200 + 5400}{5} = \frac{7950}{5} = \mathbf{1590\text{ ms}}$

---

### Edge-Case Matrix

| Scenario | Input | Algorithm Behavior |
|---|---|---|
| **Zero Requests** | `[]` | Returns `0.0` for all metrics (no division by zero) |
| **Single Request** | `[250]` | $N=1 \implies \text{P50} = \text{P95} = \text{P99} = \text{Avg} = \text{Min} = \text{Max} = \mathbf{250}$ |
| **Identical Values** | `[500, 500, 500]` | Interpolation evaluates cleanly to `500.0` |
| **Extreme Outliers** | `[10, 15, 20, 60000]` | P50 (median) remains resistant; P99 and Max capture outlier |

---

## 7. Database Persistence Schema (PostgreSQL DDL)

Metrics persist independently across backend restarts in PostgreSQL (`sourcing_agent`):

```sql
-- 1. Master Search Request Records
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
);

-- 2. Component Stage Timing Breakdown
CREATE TABLE IF NOT EXISTS metrics_stage_timings (
    id SERIAL PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES metrics_search_requests (request_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    details JSONB
);

-- 3. Per-Supplier Execution Records
CREATE TABLE IF NOT EXISTS metrics_supplier_executions (
    id SERIAL PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES metrics_search_requests (request_id) ON DELETE CASCADE,
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
    is_benchmark INTEGER DEFAULT 0
);
```

---

## 8. REST API Endpoints Reference

| Endpoint | Method | Purpose | Key Query Parameters |
|---|---|---|---|
| `/api/metrics/overview` | `GET` | Overall KPIs, percentiles, discovery rate | `time_range`, `include_benchmark` |
| `/api/metrics/latency` | `GET` | Latency histogram & stage breakdowns | `time_range`, `include_benchmark` |
| `/api/metrics/suppliers` | `GET` | Per-supplier latency and availability | `time_range`, `include_benchmark` |
| `/api/metrics/quality` | `GET` | Discovery rate, extraction completeness | `time_range`, `include_benchmark` |
| `/api/metrics/requests` | `GET` | Paginated request explorer history | `limit`, `offset`, `query`, `status` |
| `/api/metrics/requests/{id}` | `GET` | Full inspection of single request timings | Path parameter: `request_id` |
| `/api/metrics/clear` | `POST / DELETE` | Reset dashboard metrics | `include_benchmark` |
| `/api/benchmark/run` | `POST` | Trigger background benchmark suite | `concurrency`, `mode`, `queries` |
| `/api/benchmark/status` | `GET` | Benchmark status and latest results | None |

---

## 9. File Implementation Map

- **Measurement & Timing Tracker:** [`backend/parts_agent/perf_tracker.py`](file:///home/habilelabs/Development/CAS/backend/parts_agent/perf_tracker.py)
- **Database & NIST Percentile Calculations:** [`backend/parts_agent/metrics_db.py`](file:///home/habilelabs/Development/CAS/backend/parts_agent/metrics_db.py)
- **Unified DB Layer (Postgres + SQLite):** [`backend/parts_agent/db.py`](file:///home/habilelabs/Development/CAS/backend/parts_agent/db.py)
- **FastAPI Endpoints:** [`backend/metrics_api.py`](file:///home/habilelabs/Development/CAS/backend/metrics_api.py)
- **Benchmark Runner:** [`backend/scripts/benchmark_runner.py`](file:///home/habilelabs/Development/CAS/backend/scripts/benchmark_runner.py)
- **Frontend Dashboard:** [`frontend/src/components/analytics/AnalyticsDashboard.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/analytics/AnalyticsDashboard.jsx)
- **Frontend Latency Charts:** [`frontend/src/components/analytics/LatencyCharts.jsx`](file:///home/habilelabs/Development/CAS/frontend/src/components/analytics/LatencyCharts.jsx)
