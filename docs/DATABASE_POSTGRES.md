# PostgreSQL Database Integration & psql Connection Guide

This guide documents the PostgreSQL database connection, schema setup, migration tools, and interactive `psql` command access for the Conversational Automotive Parts Sourcing Agent.

---

## 1. Quick Connection Details

| Parameter | Value |
|---|---|
| **Database Engine** | PostgreSQL 14.24 |
| **Database Name** | `parts_sourcing` |
| **Username** | `postgres` |
| **Host / Socket** | `/tmp` |
| **Port** | `5432` |
| **Full URI (`DATABASE_URL`)** | `postgresql://postgres@/parts_sourcing?host=/tmp` |

---

## 2. Connect via `psql` CLI

### Option A: Direct Socket Connection (Recommended)
```bash
psql -h /tmp -U postgres -d parts_sourcing
```

### Option B: Connection URI String
```bash
psql "postgresql://postgres@/parts_sourcing?host=/tmp"
```

### Option C: Using Standard PostgreSQL Environment Variables
```bash
PGHOST=/tmp PGUSER=postgres PGDATABASE=parts_sourcing psql
```

### Common Useful `psql` Inspection Queries:
```sql
-- List all tables in the database
\dt

-- Inspect recent search requests and their latencies
SELECT request_id, customer_query, status, total_latency_ms, created_at 
FROM metrics_search_requests 
ORDER BY timestamp DESC 
LIMIT 5;

-- Check supplier performance stats
SELECT supplier_name, status, latency_ms, products_discovered 
FROM metrics_supplier_executions 
ORDER BY id DESC 
LIMIT 10;

-- Check cached parts catalogue records
SELECT key, vehicle_label, part_name, to_timestamp(created_at) as cached_time 
FROM search_cache 
LIMIT 5;
```

---

## 3. Architecture & Unified Database Adapter

The backend utilizes a unified database layer ([`backend/parts_agent/db.py`](file:///home/habilelabs/Development/CAS/backend/parts_agent/db.py)) that seamlessly bridges PostgreSQL and SQLite:

```
                  ┌──────────────────────────────────────────────┐
                  │          FastAPI / Sourcing Agent            │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │    Unified Database Layer (`db.py`)          │
                  │  • Auto-detects `DATABASE_URL`               │
                  │  • psycopg2 with RealDictCursor              │
                  │  • Auto-adapts `?` placeholders to `%s`      │
                  │  • Translates time formatting to PostgreSQL  │
                  └──────────────────────┬───────────────────────┘
                                         │
                  ┌──────────────────────┴──────────────────────┐
                  ▼                                             ▼
       PostgreSQL (`psql`)                             SQLite Fallback
    `parts_sourcing` database                     `backend/data/parts_cache.db`
    (Production & High Concurrency)               (Zero-Dependency Local Dev)
```

- **PostgreSQL First:** When `DATABASE_URL` is set to a valid PostgreSQL URI (configured in `backend/.env`), all search metrics, component stage timings, quality metrics, and catalogue cache automatically query and persist to PostgreSQL.
- **Fail-Safe SQLite Fallback:** If PostgreSQL is unreachable or `DATABASE_URL` is unset, the application automatically falls back to SQLite (`backend/data/parts_cache.db`) without throwing unhandled exceptions.

---

## 4. PostgreSQL DDL Schema

The complete PostgreSQL DDL is defined in [`backend/sql/postgres_schema.sql`](file:///home/habilelabs/Development/CAS/backend/sql/postgres_schema.sql):

- **`search_cache`**: Caches scraped product outcomes with TTL for sub-second retrieval (`key`, `vehicle_label`, `part_name`, `data JSONB`, `created_at`, `expires_at`).
- **`url_cache`**: Caches direct vendor category navigation links.
- **`metrics_search_requests`**: Stores request-level latency, customer query, vehicle resolution, and status (`request_id PRIMARY KEY`, `total_latency_ms`, `supplier_wall_clock_ms`, `is_benchmark`).
- **`metrics_supplier_executions`**: Tracks per-supplier response times, availability, and error traces (`id BIGSERIAL`, `supplier_name`, `latency_ms`, `status`).
- **`metrics_stage_timings`**: Tracks component-level duration across all 10 stages (`id BIGSERIAL`, `stage_name`, `duration_ms`, `details JSONB`).
- **`metrics_product_quality`**: Tracks extraction completeness across 7 key fields and fitment verification coverage (`request_id UNIQUE REFERENCES metrics_search_requests`).

---

## 5. Migration Script (SQLite to PostgreSQL)

To sync or migrate records from SQLite to PostgreSQL at any time:

```bash
# Run migration script
python3 backend/scripts/migrate_to_postgres.py

# Optional: specify custom SQLite file or PostgreSQL URI
python3 backend/scripts/migrate_to_postgres.py \
    --sqlite backend/data/parts_cache.db \
    --postgres-url "postgresql://postgres@/parts_sourcing?host=/tmp"
```

---

## 6. Verifying the Connection

Run the dedicated test suite to verify PostgreSQL connectivity, schema validity, and `psql` command execution:

```bash
python3 -m unittest backend/tests/test_postgres_database.py
```
