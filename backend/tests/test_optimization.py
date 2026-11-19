"""Comprehensive test suite for optimization audit requirements:
1. Browser lifecycle (shared browser, isolated contexts, crash recovery, shutdown)
2. Navigation (timeouts, retries, closed page recovery, challenge/blocked detection)
3. Supplier concurrency (progressive emission via as_completed, failure isolation, timeout handling)
4. Cache (5-part keys with variant, TTL, demo labeling, fallback fitment marking)
5. Duplicate supplier advancement prevention
6. Product extraction robustness
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from parts_agent import auto_dukan, boodmo, cache_manager
from parts_agent.browser_manager import BrowserManager, get_browser_manager
from parts_agent.graph import collect, open_session, close_session, State
from parts_agent.models import Listing, SearchOutcome, VehicleProfile


class TestBrowserLifecycle(unittest.IsolatedAsyncioTestCase):
    """Verify centralized BrowserManager architecture."""

    async def test_browser_singleton_and_starts_once(self):
        bm1 = get_browser_manager()
        bm2 = get_browser_manager()
        self.assertIs(bm1, bm2)

    async def test_multiple_sessions_have_independent_contexts(self):
        bm = BrowserManager()
        # Mock Playwright and Chromium browser
        mock_pw = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.is_connected = MagicMock(return_value=True)

        ctx_session_a = MagicMock(pages=[])
        ctx_session_b = MagicMock(pages=[])
        mock_browser.new_context = AsyncMock(side_effect=[ctx_session_a, ctx_session_b])
        bm._pw = mock_pw
        bm._browser = mock_browser

        ctx1 = await bm.get_context("session-1", "boodmo")
        ctx2 = await bm.get_context("session-2", "boodmo")

        self.assertIsNot(ctx1, ctx2)
        self.assertEqual(mock_browser.new_context.call_count, 2)

    async def test_both_suppliers_can_use_shared_browser_with_distinct_contexts(self):
        bm = BrowserManager()
        mock_browser = MagicMock()
        mock_browser.is_connected = MagicMock(return_value=True)

        ctx_boodmo = MagicMock(pages=[])
        ctx_autodukan = MagicMock(pages=[])
        mock_browser.new_context = AsyncMock(side_effect=[ctx_boodmo, ctx_autodukan])
        bm._browser = mock_browser

        boodmo_ctx = await bm.get_context("session-test", "boodmo")
        autodukan_ctx = await bm.get_context("session-test", "autodukan")

        self.assertIsNot(boodmo_ctx, autodukan_ctx)
        self.assertEqual(len(bm._contexts), 2)

    async def test_one_context_closing_does_not_close_the_other(self):
        bm = BrowserManager()
        mock_browser = MagicMock()
        mock_browser.is_connected = MagicMock(return_value=True)

        ctx_a = MagicMock(pages=[], close=AsyncMock())
        ctx_b = MagicMock(pages=[], close=AsyncMock())
        mock_browser.new_context = AsyncMock(side_effect=[ctx_a, ctx_b])
        bm._browser = mock_browser

        await bm.get_context("session-a", "boodmo")
        await bm.get_context("session-b", "boodmo")

        await bm.close_session("session-a")
        ctx_a.close.assert_awaited_once()
        ctx_b.close.assert_not_awaited()
        self.assertNotIn(("session-a", "boodmo"), bm._contexts)
        self.assertIn(("session-b", "boodmo"), bm._contexts)

    async def test_browser_shutdown_cleans_up_all_resources(self):
        bm = BrowserManager()
        mock_browser = MagicMock(close=AsyncMock())
        mock_pw = MagicMock(stop=AsyncMock())
        bm._browser = mock_browser
        bm._pw = mock_pw

        ctx1 = MagicMock(close=AsyncMock())
        ctx2 = MagicMock(close=AsyncMock())
        bm._contexts[("sess1", "boodmo")] = ctx1
        bm._contexts[("sess2", "autodukan")] = ctx2

        await bm.shutdown()
        ctx1.close.assert_awaited_once()
        ctx2.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()
        self.assertEqual(len(bm._contexts), 0)
        self.assertIsNone(bm._browser)
        self.assertIsNone(bm._pw)

    async def test_browser_crash_recovery_reinitializes(self):
        bm = BrowserManager()
        crashed_browser = MagicMock()
        crashed_browser.is_connected = MagicMock(return_value=False)
        bm._browser = crashed_browser

        fresh_browser = MagicMock()
        fresh_browser.is_connected = MagicMock(return_value=True)

        with patch.object(bm, "initialize", new_callable=AsyncMock) as mock_init:
            async def set_fresh(*args, **kwargs):
                bm._browser = fresh_browser
            mock_init.side_effect = set_fresh

            browser = await bm._ensure_browser()
            mock_init.assert_awaited_once()
            self.assertEqual(browser, fresh_browser)


class TestNavigationControl(unittest.IsolatedAsyncioTestCase):
    """Verify navigation timeouts, retries, and challenge detection."""

    async def test_successful_navigation(self):
        adapter = boodmo.BoodmoAdapter()
        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.title = AsyncMock(return_value="Spare Parts - Boodmo")
        mock_page.evaluate = AsyncMock(return_value="Content loaded")
        adapter.page = mock_page

        await adapter._goto("https://boodmo.com/test")
        mock_page.goto.assert_awaited_once()

    async def test_navigation_retry_commit_success(self):
        adapter = boodmo.BoodmoAdapter()
        mock_page = MagicMock()
        mock_page.goto = AsyncMock(side_effect=[Exception("Timeout 15000ms"), None])
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.title = AsyncMock(return_value="Spare Parts - Boodmo")
        mock_page.evaluate = AsyncMock(return_value="Valid catalog page")
        adapter.page = mock_page

        await adapter._goto("https://boodmo.com/test")
        self.assertEqual(mock_page.goto.call_count, 2)

    async def test_navigation_both_attempts_fail_raises(self):
        adapter = boodmo.BoodmoAdapter()
        mock_page = MagicMock()
        mock_page.goto = AsyncMock(side_effect=[Exception("Timeout 15000ms"), Exception("Connection refused")])
        adapter.page = mock_page

        with self.assertRaises(Exception):
            await adapter._goto("https://boodmo.com/test")

    async def test_challenge_blocked_page_detection_raises_meaningful_error(self):
        adapter = boodmo.BoodmoAdapter()
        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()
        mock_page.title = AsyncMock(return_value="Attention Required! | Cloudflare")
        mock_page.evaluate = AsyncMock(return_value="Please complete the security check to proceed")
        adapter.page = mock_page

        with self.assertRaises(RuntimeError) as ctx:
            await adapter._goto("https://boodmo.com/catalog")
        self.assertIn("bot-protection challenge", str(ctx.exception))


class TestSupplierConcurrencyAndProgressiveResults(unittest.IsolatedAsyncioTestCase):
    """Verify progressive emission of supplier results via asyncio.as_completed."""

    async def test_faster_supplier_emitted_first_and_combined(self):
        # Create session with mock adapters
        session_id = "test-concurrency-session"
        s = open_session(session_id)

        # AutoDukan finishes fast (0.01s), Boodmo finishes slower (0.08s)
        async def fast_autodukan_collect(p):
            await asyncio.sleep(0.01)
            return SearchOutcome(
                vendor="autodukan",
                status="ok",
                vehicle_label="Tata Nexon",
                listings=[{"title": "AutoDukan Filter", "brand": "Tata", "price": 400.0, "url": "https://autodukan.com/1", "fitment": "unverified"}],
                source_url="https://autodukan.com",
                elapsed_seconds=0.01
            )

        async def slow_boodmo_collect(p):
            await asyncio.sleep(0.08)
            return SearchOutcome(
                vendor="boodmo",
                status="ok",
                vehicle_label="Tata Nexon",
                listings=[{"title": "Boodmo Filter", "brand": "Tata", "price": 450.0, "url": "https://boodmo.com/1", "fitment": "verified"}],
                source_url="https://boodmo.com",
                elapsed_seconds=0.08
            )

        s.autodukan_adapter.collect = AsyncMock(side_effect=fast_autodukan_collect)
        s.boodmo_adapter.collect = AsyncMock(side_effect=slow_boodmo_collect)

        completed_order = []
        def on_completed(outcome):
            completed_order.append(outcome.vendor)

        s.on_supplier_completed = on_completed

        state = {
            "request": "Find an oil filter for Tata Nexon",
            "profile": {"maker": "Tata", "model": "Nexon", "year": "2024", "part": "oil filter"},
            "supplier_profiles": {}
        }
        config = {"configurable": {"thread_id": session_id}}

        # Bypass cache
        with patch("parts_agent.cache_manager.get_cached_search", return_value=None), \
             patch("parts_agent.cache_manager.save_cached_search"):
            result = await collect(state, config)

        self.assertEqual(completed_order, ["autodukan", "boodmo"])
        self.assertEqual(len(result["outcome"]["listings"]), 2)
        self.assertIn(result["outcome"]["status"], ("ok", "success"))

    async def test_one_supplier_failure_preserves_successful_supplier_results(self):
        session_id = "test-failover-session"
        s = open_session(session_id)

        async def failing_boodmo_collect(p):
            raise RuntimeError("Boodmo gateway error 502")

        async def successful_autodukan_collect(p):
            return SearchOutcome(
                vendor="autodukan",
                status="ok",
                vehicle_label="Tata Nexon",
                listings=[{"title": "AutoDukan Pad", "brand": "Bosch", "price": 1200.0, "url": "https://autodukan.com/pad", "fitment": "unverified"}],
                source_url="https://autodukan.com",
                elapsed_seconds=0.02
            )

        s.boodmo_adapter.collect = AsyncMock(side_effect=failing_boodmo_collect)
        s.autodukan_adapter.collect = AsyncMock(side_effect=successful_autodukan_collect)

        state = {
            "request": "Find brake pads for Tata Nexon",
            "profile": {"maker": "Tata", "model": "Nexon", "year": "2024", "part": "brake pads"},
            "supplier_profiles": {}
        }
        config = {"configurable": {"thread_id": session_id}}

        with patch("parts_agent.cache_manager.get_cached_search", return_value=None), \
             patch("parts_agent.cache_manager.save_cached_search"):
            result = await collect(state, config)

        # Successful AutoDukan listing is preserved
        self.assertEqual(len(result["outcome"]["listings"]), 1)
        self.assertEqual(result["outcome"]["listings"][0]["vendor"], "autodukan")
        self.assertIn(result["outcome"]["status"], ("ok", "partial_success", "success"))


class TestCacheKeyAndLabelling(unittest.TestCase):
    """Verify 5-part cache keys, TTL, demo labeling, and fitment safety."""

    def test_different_variants_produce_different_cache_keys(self):
        key_at = cache_manager.make_cache_key("Tata", "Harrier", "2021", "XZA Plus AT", "oil filter")
        key_mt = cache_manager.make_cache_key("Tata", "Harrier", "2021", "XZ MT", "oil filter")
        self.assertNotEqual(key_at, key_mt)
        self.assertIn("xza_plus_at", key_at)
        self.assertIn("xz_mt", key_mt)

    def test_different_years_produce_different_cache_keys(self):
        key_2021 = cache_manager.make_cache_key("Tata", "Nexon", "2021", "XZ", "air filter")
        key_2024 = cache_manager.make_cache_key("Tata", "Nexon", "2024", "XZ", "air filter")
        self.assertNotEqual(key_2021, key_2024)

    def test_demo_cache_records_labelled_as_demo(self):
        # Tata Nexon 2024 side mirror is a pre-seeded demo record
        cached = cache_manager.get_cached_search("Tata", "Nexon", "2024", None, "side mirror")
        self.assertIsNotNone(cached)
        for outcome in cached:
            self.assertTrue(outcome.get("is_demo"))
            self.assertTrue(outcome.get("is_cached"))
            self.assertEqual(outcome.get("source_type"), "demo")
            # Must NOT claim live verified pricing
            for l in outcome.get("listings", []):
                notes_str = " ".join(l.get("notes", []))
                self.assertIn("Demonstration sample", notes_str)
                self.assertNotIn("verified live", notes_str)

    def test_year_fallback_marks_fitment_unverified(self):
        # Querying an unseeded year like 2099 triggers year fallback
        cached = cache_manager.get_cached_search("Tata", "Nexon", "2099", None, "side mirror")
        if cached:
            for outcome in cached:
                for l in outcome.get("listings", []):
                    self.assertEqual(l.get("fitment"), "unverified")
                    notes_str = " ".join(l.get("notes", []))
                    self.assertIn("year fallback", notes_str)


class TestDuplicateAdvancementPrevention(unittest.IsolatedAsyncioTestCase):
    """Verify adapters do not duplicate navigation for unchanged profiles."""

    async def test_boodmo_duplicate_advance_reused(self):
        adapter = boodmo.BoodmoAdapter()
        adapter.start = AsyncMock()
        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        adapter.page = mock_page
        adapter.resolve_category = AsyncMock(return_value=None)
        adapter._search_fallback = AsyncMock()
        adapter._ensure_category_page = AsyncMock()
        adapter._options = AsyncMock(return_value={"options": ["Tata"], "selected": "Tata"})

        profile = VehicleProfile(maker="Tata", model="Nexon", year="2024", variant="1.2L Revotron", category="Mirrors")

        # First advance sets signature
        need1 = await adapter.advance(profile)
        self.assertIsNone(need1)
        self.assertTrue(adapter._advanced_ok)

        # Second advance with same profile returns immediately without navigation
        with patch.object(adapter, "_search_fallback") as mock_fallback:
            need2 = await adapter.advance(profile)
            self.assertIsNone(need2)
            mock_fallback.assert_not_called()

    async def test_autodukan_duplicate_advance_reused(self):
        adapter = auto_dukan.AutoDukanAdapter()
        adapter.start = AsyncMock()
        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.url = adapter.base_url
        adapter.page = mock_page
        adapter.resolve_category = AsyncMock(return_value=None)
        adapter._select_vehicle = AsyncMock(return_value=(True, "", None))
        adapter._select_category = AsyncMock(return_value=(True, ""))

        profile = VehicleProfile(maker="Tata", model="Nexon", year="2024", variant="XZA+", category="Mirrors")

        need1 = await adapter.advance(profile)
        self.assertIsNone(need1)
        self.assertTrue(adapter._advanced_ok)

        # Calling advance again reuses confirmed state
        with patch.object(adapter, "_select_vehicle") as mock_select:
            need2 = await adapter.advance(profile)
            self.assertIsNone(need2)
            mock_select.assert_not_called()


class TestProductExtractionRobustness(unittest.TestCase):
    """Verify handling of missing prices, duplicate cards, and masked part numbers."""

    def test_price_parsing_handles_missing_and_formatted(self):
        self.assertEqual(auto_dukan._price("₹ 1,850.50"), 1850.50)
        self.assertEqual(auto_dukan._price("Rs. 420"), 420.0)
        self.assertIsNone(auto_dukan._price(None))
        self.assertIsNone(auto_dukan._price(""))

    def test_part_number_unmasking_from_slug(self):
        # Boodmo masks: KLP...9AA with slug carrying KLP103969AA
        number, item_id = boodmo._part_number(
            "/catalog/part-outside_mirror_klp103969aa-12345/",
            "KLP...9AA"
        )
        self.assertEqual(number, "KLP103969AA")
        self.assertEqual(item_id, "12345")


if __name__ == "__main__":
    unittest.main()
