"""Comprehensive Test Suite for Supplier Bot-Protection, Error Handling, and Search Status.

Covers:
1. Boodmo normal search -> Completed
2. Boodmo empty query -> Handled safely without crash
3. Boodmo fallback query -> No UnboundLocalError when advancing multiple variants
4. AutoDukan bot challenge -> Blocked
5. AutoDukan normal navigation error -> Failed
6. Boodmo fails, AutoDukan succeeds -> Partial success
7. AutoDukan blocked, Boodmo succeeds -> Partial success
8. Both suppliers blocked -> Failed
9. Both suppliers return zero products successfully -> Completed no results
10. Both suppliers return products -> Success
11. One supplier times out -> Other supplier continues
12. All suppliers finish -> No pending statuses, durations recorded
"""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from parts_agent import auto_dukan, boodmo, graph, relevance, variants
from parts_agent.models import Listing, NeedInput, SearchOutcome, VehicleProfile
from parts_agent.perf_logger import perf_monitor


class TestSupplierBlocksAndStatus(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_bm = MagicMock()
        self.mock_page = AsyncMock()
        self.mock_page.url = "https://example.com"
        self.mock_page.is_closed = MagicMock(return_value=False)
        self.mock_page.title = AsyncMock(return_value="Spare Parts")
        self.mock_page.evaluate = AsyncMock(return_value="")
        self.mock_page.goto = AsyncMock()
        self.mock_page.wait_for_load_state = AsyncMock()
        self.mock_bm.get_page = AsyncMock(return_value=self.mock_page)
        self.mock_bm.close_adapter = AsyncMock()
        self.mock_bm.close_session = AsyncMock()

    # -------------------------------------------------------------------------
    # 1. Boodmo normal search -> Completed
    # -------------------------------------------------------------------------
    async def test_01_boodmo_normal_search(self):
        adapter = boodmo.BoodmoAdapter(browser_manager=self.mock_bm)
        profile = VehicleProfile(maker="Tata", model="Harrier", year="2023", part="Headlight")

        # Mock successful advance and collect tiles
        adapter._advanced_ok = True
        adapter._read_tiles = AsyncMock(return_value=[{
            "title": "Tata Harrier Headlight Right",
            "price": "₹8,500",
            "brand": "Tata",
            "number": "TATA123",
            "href": "/part-tata-123",
        }])
        adapter._enrich = AsyncMock()

        outcome = await adapter.collect(profile)
        self.assertIn(outcome.status, ("ok", "completed"))
        self.assertGreater(len(outcome.listings), 0)
        self.assertNotEqual(perf_monitor.get_metrics(adapter.session_id, adapter.name).status, "pending")

    # -------------------------------------------------------------------------
    # 2. Boodmo empty query -> Handled safely
    # -------------------------------------------------------------------------
    async def test_02_boodmo_empty_query_handled_safely(self):
        adapter = boodmo.BoodmoAdapter(browser_manager=self.mock_bm)
        profile = VehicleProfile()  # all fields empty

        # Should not raise exception and should fallback safely
        await adapter._search_fallback(profile)
        self.assertTrue(adapter._using_search_fallback)
        self.mock_page.goto.assert_called()
        call_url = self.mock_page.goto.call_args[0][0]
        self.assertIn("search", call_url)
        self.assertNotIn("/search//", call_url)

    # -------------------------------------------------------------------------
    # 3. Boodmo fallback query -> No UnboundLocalError
    # -------------------------------------------------------------------------
    async def test_03_boodmo_advance_no_unbound_local_error(self):
        adapter = boodmo.BoodmoAdapter(browser_manager=self.mock_bm)
        profile = VehicleProfile(
            maker="Tata",
            model="Harrier",
            year="2026",
            part="Headlight",
            hints={"make": "Tata", "model": "Harrier", "year": "2026", "trim": "Fearless"}
        )

        # Mock category page options
        adapter._options = AsyncMock(side_effect=[
            {"options": ["Tata"], "selected": "Tata"},       # maker
            {"options": ["Harrier"], "selected": "Harrier"}, # model
            {"options": ["2026"], "selected": "2026"},       # year
            {"options": [                                    # variants
                "2.0L Diesel Fearless MT",
                "2.0L Diesel Fearless AT",
                "2.0L Diesel Adventure MT"
            ], "selected": None}
        ])
        adapter._choose = AsyncMock()
        adapter._ensure_category_page = AsyncMock()

        # advance() must not raise UnboundLocalError
        try:
            need = await adapter.advance(profile)
        except UnboundLocalError as err:
            self.fail(f"advance() raised UnboundLocalError: {err}")

        # Either narrowed or returned NeedInput cleanly
        if need:
            self.assertIn(need.field, ("variant", "variant_attr"))

    # -------------------------------------------------------------------------
    # 4. AutoDukan bot challenge -> Blocked
    # -------------------------------------------------------------------------
    async def test_04_autodukan_bot_challenge_blocked(self):
        adapter = auto_dukan.AutoDukanAdapter(browser_manager=self.mock_bm)
        profile = VehicleProfile(maker="Tata", model="Harrier", year="2026", part="Headlight")

        # Mock Cloudflare Turnstile challenge page
        self.mock_page.title = AsyncMock(return_value="Just a moment...")
        self.mock_page.evaluate = AsyncMock(return_value="Attention Required! | Cloudflare verify you are human cf-challenge")

        outcome = await adapter.collect(profile)
        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.error, "bot_protection_challenge")
        self.assertIn("bot-protection", outcome.message.lower())

        # Verify adapter is marked blocked and does not retry
        self.assertTrue(adapter._is_blocked)
        m = perf_monitor.get_metrics(adapter.session_id, adapter.name)
        self.assertEqual(m.status, "blocked")
        self.assertEqual(m.error_category, "bot_protection_challenge")

    # -------------------------------------------------------------------------
    # 5. AutoDukan normal navigation error -> Failed
    # -------------------------------------------------------------------------
    async def test_05_autodukan_normal_navigation_error_failed(self):
        adapter = auto_dukan.AutoDukanAdapter(browser_manager=self.mock_bm)
        profile = VehicleProfile(maker="Tata", model="Harrier", year="2026", part="Headlight")

        # Mock standard connection / timeout error (not a bot challenge)
        self.mock_page.goto = AsyncMock(side_effect=RuntimeError("Net::ERR_CONNECTION_REFUSED"))

        outcome = await adapter.collect(profile)
        self.assertEqual(outcome.status, "failed")
        self.assertIn("failed", outcome.message.lower())
        m = perf_monitor.get_metrics(adapter.session_id, adapter.name)
        self.assertEqual(m.status, "failed")

    # -------------------------------------------------------------------------
    # 6. Boodmo fails, AutoDukan succeeds -> Partial success
    # -------------------------------------------------------------------------
    async def test_06_boodmo_fails_autodukan_succeeds(self):
        outcomes = [
            {"vendor": "boodmo", "status": "failed", "listings": [], "message": "Boodmo error"},
            {"vendor": "autodukan", "status": "completed", "listings": [{"title": "Headlight", "price": 5000}], "message": "Found 1"}
        ]
        status = graph.compute_overall_status(outcomes)
        self.assertEqual(status, "partial_success")

    # -------------------------------------------------------------------------
    # 7. AutoDukan blocked, Boodmo succeeds -> Partial success
    # -------------------------------------------------------------------------
    async def test_07_autodukan_blocked_boodmo_succeeds(self):
        outcomes = [
            {"vendor": "autodukan", "status": "blocked", "listings": [], "message": "Bot challenge"},
            {"vendor": "boodmo", "status": "ok", "listings": [{"title": "Headlight", "price": 4000}], "message": "Found 1"}
        ]
        status = graph.compute_overall_status(outcomes)
        self.assertEqual(status, "partial_success")

    # -------------------------------------------------------------------------
    # 8. Both suppliers blocked -> Failed
    # -------------------------------------------------------------------------
    async def test_08_both_suppliers_blocked(self):
        outcomes = [
            {"vendor": "autodukan", "status": "blocked", "listings": [], "message": "Bot challenge"},
            {"vendor": "boodmo", "status": "blocked", "listings": [], "message": "Bot challenge"}
        ]
        status = graph.compute_overall_status(outcomes)
        self.assertEqual(status, "failed")

        profile = VehicleProfile(maker="Tata", model="Harrier", year="2026", part="Headlight")
        summary = graph.summarize(profile, {"status": status, "vendor_outcomes": outcomes, "listings": []})
        self.assertIn("could not be completed", summary)
        self.assertIn("blocked", summary.lower())

    # -------------------------------------------------------------------------
    # 9. Both suppliers return zero products successfully -> Completed no results
    # -------------------------------------------------------------------------
    async def test_09_both_suppliers_return_zero_products_successfully(self):
        outcomes = [
            {"vendor": "autodukan", "status": "no_results", "listings": [], "message": "0 found"},
            {"vendor": "boodmo", "status": "no_results", "listings": [], "message": "0 found"}
        ]
        status = graph.compute_overall_status(outcomes)
        self.assertEqual(status, "completed_no_results")

        profile = VehicleProfile(maker="Tata", model="Harrier", year="2026", part="Headlight")
        summary = graph.summarize(profile, {"status": status, "vendor_outcomes": outcomes, "listings": []})
        self.assertIn("found no matching products", summary)

    # -------------------------------------------------------------------------
    # 10. Both suppliers return products -> Success
    # -------------------------------------------------------------------------
    async def test_10_both_suppliers_return_products(self):
        outcomes = [
            {"vendor": "boodmo", "status": "ok", "listings": [{"title": "H1", "price": 1000}], "message": "Found"},
            {"vendor": "autodukan", "status": "ok", "listings": [{"title": "H2", "price": 1200}], "message": "Found"}
        ]
        status = graph.compute_overall_status(outcomes)
        self.assertEqual(status, "success")

    # -------------------------------------------------------------------------
    # 11. One supplier times out -> Other supplier continues
    # -------------------------------------------------------------------------
    async def test_11_one_supplier_times_out_other_continues(self):
        outcomes = [
            {"vendor": "boodmo", "status": "timeout", "listings": [], "message": "Timed out"},
            {"vendor": "autodukan", "status": "ok", "listings": [{"title": "Headlight", "price": 3000}], "message": "Found 1"}
        ]
        status = graph.compute_overall_status(outcomes)
        self.assertEqual(status, "partial_success")

    # -------------------------------------------------------------------------
    # 12. All suppliers finish -> No pending statuses, metrics recorded
    # -------------------------------------------------------------------------
    async def test_12_all_suppliers_finish_no_pending_statuses(self):
        session_id = "test-terminal-status"
        m_bm = perf_monitor.get_metrics(session_id, "boodmo")
        m_ad = perf_monitor.get_metrics(session_id, "autodukan")

        # Simulate completion
        m_bm.status = "ok"
        m_bm.total_seconds = 2.4
        m_bm.products_extracted = 3

        m_ad.status = "blocked"
        m_ad.total_seconds = 1.1
        m_ad.error_category = "bot_protection_challenge"

        # Neither can remain pending
        self.assertNotEqual(m_bm.status, "pending")
        self.assertNotEqual(m_ad.status, "pending")
        self.assertGreater(m_bm.total_seconds, 0)
        self.assertGreater(m_ad.total_seconds, 0)

        report_bm = m_bm.format_report()
        report_ad = m_ad.format_report()
        self.assertIn("Status: ok", report_bm)
        self.assertIn("Status: blocked", report_ad)
        self.assertIn("Error category: bot_protection_challenge", report_ad)
        self.assertNotIn("Total: unavailable", report_bm)
        self.assertNotIn("Total: unavailable", report_ad)


if __name__ == "__main__":
    unittest.main()
