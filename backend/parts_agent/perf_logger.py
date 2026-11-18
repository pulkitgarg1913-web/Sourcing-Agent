"""Structured Performance Logger for Conversational Business Sourcing Agent.

Accurately tracks, logs, and formats step-by-step metrics for every supplier search:
- Thread/session ID
- Supplier name
- Browser initialization time
- Navigation duration
- Vehicle selection duration
- Category selection duration
- Product extraction duration
- Product detail enrichment duration
- Total supplier search duration
- Number of extracted products
- Number of failed product detail requests
- Cache hit or miss
- Final supplier status
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

log = logging.getLogger("perf")


@dataclass
class SupplierMetrics:
    supplier: str
    session_id: str = ""
    browser_init_seconds: float = 0.0
    navigation_seconds: float = 0.0
    vehicle_selection_seconds: float = 0.0
    category_selection_seconds: float = 0.0
    extraction_seconds: float = 0.0
    enrichment_seconds: float = 0.0
    total_seconds: Optional[float] = None
    products_extracted: int = 0
    failed_details_count: int = 0
    cache_hit: bool = False
    status: str = "pending"
    error_category: Optional[str] = None
    error_message: Optional[str] = None

    def format_report(self) -> str:
        lines = [
            f"[{self.supplier.upper()}]",
            f"Status: {self.status}",
            f"Cache: {'HIT' if self.cache_hit else 'MISS'}",
        ]
        if self.browser_init_seconds > 0:
            lines.append(f"Browser initialization: {self.browser_init_seconds:.1f}s")
        if self.navigation_seconds > 0:
            lines.append(f"Navigation: {self.navigation_seconds:.1f}s")
        if self.vehicle_selection_seconds > 0:
            lines.append(f"Vehicle selection: {self.vehicle_selection_seconds:.1f}s")
        if self.category_selection_seconds > 0:
            lines.append(f"Category selection: {self.category_selection_seconds:.1f}s")
        if self.extraction_seconds > 0:
            lines.append(f"Extraction: {self.extraction_seconds:.1f}s")
        if self.enrichment_seconds > 0:
            lines.append(f"Product detail enrichment: {self.enrichment_seconds:.1f}s")
        lines.append(f"Products: {self.products_extracted}")
        if self.failed_details_count > 0:
            lines.append(f"Failed details: {self.failed_details_count}")
        if self.error_category:
            lines.append(f"Error category: {self.error_category}")
        if self.error_message:
            lines.append(f"Error message: {self.error_message}")
        if self.total_seconds is not None:
            lines.append(f"Total: {self.total_seconds:.1f}s")
        else:
            lines.append("Total: unavailable")
        return "\n".join(lines)


class PerformanceMonitor:
    """Manages active performance metrics across concurrent sessions and suppliers."""

    def __init__(self):
        self._metrics: Dict[str, Dict[str, SupplierMetrics]] = {}

    def get_metrics(self, session_id: str, supplier: str) -> SupplierMetrics:
        sess = self._metrics.setdefault(session_id, {})
        if supplier not in sess:
            sess[supplier] = SupplierMetrics(supplier=supplier, session_id=session_id)
        return sess[supplier]

    def log_search_started(self, session_id: str, search_query: str) -> None:
        log.info("\n[SEARCH STARTED] %s (session: %s)", search_query, session_id[:8] if session_id else "global")

    def log_supplier_completed(self, session_id: str, supplier: str) -> None:
        m = self.get_metrics(session_id, supplier)
        log.info("\n%s", m.format_report())

    def log_search_completed(self, session_id: str, total_elapsed: float) -> None:
        sess = self._metrics.get(session_id, {})
        reports = [m.format_report() for m in sess.values()]
        full_report = "\n\n".join(reports)
        log.info("\n%s\n\n[SEARCH COMPLETED]\nTotal elapsed: %.1fs", full_report, total_elapsed)
        self._metrics.pop(session_id, None)


perf_monitor = PerformanceMonitor()
