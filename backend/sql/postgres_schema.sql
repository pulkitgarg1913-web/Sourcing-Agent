-- =====================================================================
-- Conversational Parts Sourcing Agent - PostgreSQL Database Schema
-- =====================================================================

-- 1. Search Results Cache Table (Sub-second retrieval for cached parts)
CREATE TABLE IF NOT EXISTS search_cache (
    key VARCHAR(255) PRIMARY KEY,
    vehicle_label TEXT,
    part_name TEXT,
    data JSONB,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_cache (expires_at);

-- 2. URL Direct Navigation Route Cache
CREATE TABLE IF NOT EXISTS url_cache (
    vendor VARCHAR(64) NOT NULL,
    vehicle_key VARCHAR(255) NOT NULL,
    category VARCHAR(255) NOT NULL,
    direct_url TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (vendor, vehicle_key, category)
);

-- 3. Search Requests Performance & Reliability Records
CREATE TABLE IF NOT EXISTS metrics_search_requests (
    request_id VARCHAR(128) PRIMARY KEY,
    thread_id VARCHAR(128) NOT NULL,
    timestamp DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    customer_query TEXT NOT NULL,
    part_name TEXT,
    vehicle_maker TEXT,
    vehicle_model TEXT,
    vehicle_year TEXT,
    vehicle_variant TEXT,
    vehicle_label TEXT,
    status VARCHAR(64) NOT NULL,
    total_latency_ms DOUBLE PRECISION NOT NULL,
    supplier_wall_clock_ms DOUBLE PRECISION DEFAULT 0.0,
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

CREATE INDEX IF NOT EXISTS idx_pg_search_req_ts ON metrics_search_requests (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_pg_search_req_bench ON metrics_search_requests (is_benchmark);
CREATE INDEX IF NOT EXISTS idx_pg_search_req_status ON metrics_search_requests (status);

-- 4. Supplier Execution Records
CREATE TABLE IF NOT EXISTS metrics_supplier_executions (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(128) NOT NULL REFERENCES metrics_search_requests (request_id) ON DELETE CASCADE,
    supplier_id VARCHAR(64),
    supplier_name VARCHAR(128) NOT NULL,
    timestamp DOUBLE PRECISION NOT NULL,
    status VARCHAR(64) NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL,
    products_discovered INTEGER DEFAULT 0,
    products_extracted INTEGER DEFAULT 0,
    browser_init_ms DOUBLE PRECISION DEFAULT 0.0,
    navigation_ms DOUBLE PRECISION DEFAULT 0.0,
    extraction_ms DOUBLE PRECISION DEFAULT 0.0,
    verification_ms DOUBLE PRECISION DEFAULT 0.0,
    error_category VARCHAR(128),
    error_message TEXT,
    source_url TEXT,
    is_benchmark INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pg_supplier_exec_req ON metrics_supplier_executions (request_id);
CREATE INDEX IF NOT EXISTS idx_pg_supplier_exec_name ON metrics_supplier_executions (supplier_name);
CREATE INDEX IF NOT EXISTS idx_pg_supplier_exec_ts ON metrics_supplier_executions (timestamp);

-- 5. Component Stage-Level Durations
CREATE TABLE IF NOT EXISTS metrics_stage_timings (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(128) NOT NULL REFERENCES metrics_search_requests (request_id) ON DELETE CASCADE,
    stage_name VARCHAR(128) NOT NULL,
    duration_ms DOUBLE PRECISION NOT NULL,
    details JSONB,
    timestamp DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
);

CREATE INDEX IF NOT EXISTS idx_pg_stage_req ON metrics_stage_timings (request_id);
CREATE INDEX IF NOT EXISTS idx_pg_stage_name ON metrics_stage_timings (stage_name);

-- 6. Product Quality & Extraction Completeness
CREATE TABLE IF NOT EXISTS metrics_product_quality (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(128) NOT NULL UNIQUE REFERENCES metrics_search_requests (request_id) ON DELETE CASCADE,
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
    completeness_score DOUBLE PRECISION DEFAULT 0.0,
    verification_coverage DOUBLE PRECISION DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_pg_quality_req ON metrics_product_quality (request_id);
