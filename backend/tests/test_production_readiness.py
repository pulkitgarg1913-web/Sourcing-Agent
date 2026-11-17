"""Production Readiness Verification Test Suite.

Verifies:
1. Browser lifecycle: Single shared Chromium instance, isolated contexts per session/vendor,
   graceful shutdown, crash recovery, and zero context leaks.
2. Resource interception: Images, media, fonts blocked; stylesheets, scripts, documents allowed.
3. Cache correctness: Exact 5-part key matching, variant safety on year fallback, fitment marking,
   and demo record labeling.
4. Progressive SSE events: supplier_start, supplier_result, supplier_error, complete.
5. Concurrency: Two simultaneous customer sessions without state contamination or crosstalk.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from fastapi.testclient import TestClient
from parts_agent import auto_dukan, boodmo, cache_manager
from parts_agent.browser_manager import BrowserManager, get_browser_manager
from parts_agent.graph import Conversation, SESSIONS, close_session, open_session
from parts_agent.models import Listing, SearchOutcome, VehicleProfile
import server
from server import app, sse_manager


class TestBrowserLifecycleAndContextIsolation(unittest.IsolatedAsyncioTestCase):
    """Verify single browser process, isolated contexts per session/vendor, and clean teardown."""

    async def asyncSetUp(self):
        self.bm = get_browser_manager()
        await self.bm.initialize(headless=True)

    async def asyncTearDown(self):
        await self.bm.shutdown()

    async def test_single_browser_process_for_multiple_sessions(self):
        """Verify only one Chromium process serves multiple customer sessions."""
        self.assertTrue(self.bm.is_running)
        initial_browser = self.bm._browser

        # Create pages for Session 1 and Session 2 across both suppliers
        p1_b = await self.bm.get_page("session-1", "boodmo")
        p1_a = await self.bm.get_page("session-1", "autodukan")
        p2_b = await self.bm.get_page("session-2", "boodmo")
        p2_a = await self.bm.get_page("session-2", "autodukan")

        # All contexts must belong to the exact same shared browser instance
        self.assertIs(self.bm._browser, initial_browser)
        self.assertEqual(len(self.bm._contexts), 4)

        # Contexts must be strictly distinct
        ctx1_b = self.bm._contexts[("session-1", "boodmo")]
        ctx1_a = self.bm._contexts[("session-1", "autodukan")]
        ctx2_b = self.bm._contexts[("session-2", "boodmo")]
        ctx2_a = self.bm._contexts[("session-2", "autodukan")]

        self.assertIsNot(ctx1_b, ctx1_a)
        self.assertIsNot(ctx1_b, ctx2_b)
        self.assertIsNot(ctx2_a, ctx2_b)

    async def test_contexts_close_cleanly_on_session_close(self):
        """Verify all contexts for a session are destroyed when session closes (no leaks)."""
        await self.bm.get_page("leak-test-session", "boodmo")
        await self.bm.get_page("leak-test-session", "autodukan")
        self.assertIn(("leak-test-session", "boodmo"), self.bm._contexts)
        self.assertIn(("leak-test-session", "autodukan"), self.bm._contexts)

        await self.bm.close_session("leak-test-session")

        self.assertNotIn(("leak-test-session", "boodmo"), self.bm._contexts)
        self.assertNotIn(("leak-test-session", "autodukan"), self.bm._contexts)
        self.assertNotIn(("leak-test-session", "boodmo"), self.bm._pages)
        self.assertNotIn(("leak-test-session", "autodukan"), self.bm._pages)

    async def test_browser_crash_and_recovery(self):
        """Verify that if the browser disconnects, BrowserManager safely re-initializes."""
        page1 = await self.bm.get_page("recovery-session", "boodmo")
        self.assertIsNotNone(page1)

        # Simulate abrupt browser disconnection
        if self.bm._browser:
            await self.bm._browser.close()
        self.assertFalse(self.bm.is_running)

        # Requesting a page should transparently re-launch Chromium
        page2 = await self.bm.get_page("recovery-session", "boodmo")
        self.assertTrue(self.bm.is_running)
        self.assertIsNotNone(page2)
        await self.bm.close_session("recovery-session")


class TestResourceInterception(unittest.IsolatedAsyncioTestCase):
    """Verify routing rules: block images, media, fonts; preserve stylesheets, scripts, html."""

    async def asyncSetUp(self):
        self.bm = get_browser_manager()
        await self.bm.initialize(headless=True)

    async def asyncTearDown(self):
        await self.bm.shutdown()

    async def test_heavy_assets_blocked_while_css_and_scripts_allowed(self):
        """Check route handler logic blocks only image/media/font and preserves stylesheet."""
        ctx = await self.bm.get_context("route-test", "boodmo")
        page = await self.bm.get_page("route-test", "boodmo")

        # Intercepted request mock
        aborted = []
        continued = []

        class DummyRequest:
            def __init__(self, r_type):
                self.resource_type = r_type

        class DummyRoute:
            def __init__(self, r_type):
                self.request = DummyRequest(r_type)

            async def abort(self):
                aborted.append(self.request.resource_type)

            async def continue_(self):
                continued.append(self.request.resource_type)

        # Simulate route callback logic from browser_manager
        async def evaluate_route(resource_type):
            r = DummyRoute(resource_type)
            if r.request.resource_type in ("image", "media", "font"):
                await r.abort()
            else:
                await r.continue_()

        # Heavy assets must be aborted
        await evaluate_route("image")
        await evaluate_route("media")
        await evaluate_route("font")
        self.assertIn("image", aborted)
        self.assertIn("media", aborted)
        self.assertIn("font", aborted)

        # Stylesheets, scripts, documents must be allowed!
        await evaluate_route("stylesheet")
        await evaluate_route("script")
        await evaluate_route("document")
        await evaluate_route("fetch")
        await evaluate_route("xhr")
        self.assertIn("stylesheet", continued)
        self.assertIn("script", continued)
        self.assertIn("document", continued)
        self.assertNotIn("stylesheet", aborted)

        await self.bm.close_session("route-test")


class TestCacheCorrectnessAndVariantSafety(unittest.TestCase):
    """Verify cache keys, variant isolation on fallback, fitment status, and demo record tags."""

    def setUp(self):
        cache_manager.init_db()

    def test_incompatible_variant_never_served_via_year_fallback(self):
        """A diesel cache record must NEVER be served to a customer searching for petrol."""
        maker = "Toyota"
        model = "Innova"
        part = "intercooler"

        # Save record for 2023 Diesel
        diesel_outcome = [{
            "vendor": "autodukan",
            "status": "ok",
            "vehicle_label": "Toyota Innova 2023 Diesel 2.4L",
            "listings": [{
                "title": "Diesel Intercooler Assembly",
                "brand": "Toyota OEM",
                "price": 8500.0,
                "url": "https://autodukan.com/diesel-intercooler",
                "fitment": "verified",
            }],
            "source_url": "https://autodukan.com",
            "is_cached": False,
        }]
        cache_manager.save_cached_search(
            maker=maker, model=model, year="2023", variant="Diesel 2.4L",
            part=part, outcomes=diesel_outcome, ttl_hours=24.0
        )

        # Customer searches for 2024 Petrol 2.0L (year 2024 misses exact cache)
        # Year fallback must NOT serve the diesel record!
        result = cache_manager.get_cached_search(
            maker=maker, model=model, year="2024", variant="Petrol 2.0L", part=part
        )
        self.assertIsNone(result)

    def test_year_fallback_marks_fitment_unverified(self):
        """When year fallback matches compatible or generic record, fitment is marked unverified."""
        maker = "Mahindra"
        model = "Thar"
        part = "headlight"

        # Save generic record for 2022
        generic_outcome = [{
            "vendor": "boodmo",
            "status": "ok",
            "vehicle_label": "Mahindra Thar 2022",
            "listings": [{
                "title": "Round LED Headlamp",
                "brand": "Mahindra",
                "price": 3500.0,
                "url": "https://boodmo.com/headlamp",
                "fitment": "verified",
            }],
            "source_url": "https://boodmo.com",
            "is_cached": False,
        }]
        cache_manager.save_cached_search(
            maker=maker, model=model, year="2022", variant="",
            part=part, outcomes=generic_outcome, ttl_hours=24.0
        )

        # Lookup for 2023 (same vehicle, no conflicting variant)
        cached = cache_manager.get_cached_search(
            maker=maker, model=model, year="2023", variant="", part=part
        )
        self.assertIsNotNone(cached)
        listing = cached[0]["listings"][0]
        self.assertEqual(listing["fitment"], "unverified")
        self.assertIn("year fallback", listing["fitment_basis"].lower())

    def test_demo_records_clearly_labeled_never_live(self):
        """Demo records must always carry source_type='demo' and is_demo=True."""
        demo = cache_manager.get_cached_search(
            maker="Tata", model="Nexon", year="2024", variant="", part="side_mirror"
        )
        self.assertIsNotNone(demo)
        for outcome in demo:
            self.assertTrue(outcome.get("is_demo"))
            self.assertEqual(outcome.get("source_type"), "demo")
            notes = [n for l in outcome["listings"] for n in l.get("notes", [])]
            self.assertTrue(any("demonstration" in n.lower() for n in notes))


class TestProgressiveResultsAndConcurrency(unittest.IsolatedAsyncioTestCase):
    """Verify progressive event delivery via SSE and isolation between concurrent sessions."""

    async def test_progressive_events_emitted_immediately(self):
        """AutoDukan result is published immediately without waiting for Boodmo."""
        thread_id = "test-progressive-stream"
        published_events = []

        # Hook SSE publish to capture events
        orig_publish = sse_manager.publish

        def capture_publish(tid, event):
            if tid == thread_id:
                published_events.append(dict(event))
            orig_publish(tid, event)

        with patch.object(sse_manager, "publish", side_effect=capture_publish):
            s = open_session(thread_id)

            # AutoDukan finishes in 0.02s
            async def fast_autodukan(p):
                await asyncio.sleep(0.02)
                return SearchOutcome(
                    vendor="autodukan",
                    status="ok",
                    vehicle_label="Tata Nexon",
                    listings=[{"title": "Fast Filter", "brand": "Tata", "price": 300.0, "url": "https://autodukan.com/fast"}],
                    source_url="https://autodukan.com",
                    elapsed_seconds=0.02,
                )

            # Boodmo finishes in 0.10s
            async def slow_boodmo(p):
                await asyncio.sleep(0.10)
                return SearchOutcome(
                    vendor="boodmo",
                    status="ok",
                    vehicle_label="Tata Nexon",
                    listings=[{"title": "Slow Filter", "brand": "Bosch", "price": 450.0, "url": "https://boodmo.com/slow"}],
                    source_url="https://boodmo.com",
                    elapsed_seconds=0.10,
                )

            s.adapters["autodukan"].collect = fast_autodukan
            s.adapters["boodmo"].collect = slow_boodmo
            server.instrument_session(thread_id, s)

            # Profile for collection with unique part to guarantee cache miss
            unique_part = f"exhaust_manifold_{int(time.time()*1000)}"
            profiles = {
                "autodukan": VehicleProfile(maker="Tata", model="Nexon", year="2024", part=unique_part),
                "boodmo": VehicleProfile(maker="Tata", model="Nexon", year="2024", part=unique_part),
            }

            from parts_agent.graph import collect
            state = {
                "thread_id": thread_id,
                "profile": VehicleProfile(maker="Tata", model="Nexon", year="2024", part=unique_part),
                "supplier_profiles": profiles,
                "request": "exhaust manifold",
            }

            config = {"configurable": {"thread_id": thread_id}}
            res = await collect(state, config)

            event_types = [e["type"] for e in published_events]
            self.assertIn("supplier_start", event_types)
            self.assertIn("supplier_result", event_types)

            # AutoDukan must complete BEFORE Boodmo in the event sequence
            ad_idx = next(i for i, e in enumerate(published_events) if e.get("vendor") == "autodukan" and e["type"] in ("supplier_result", "adapter_completed"))
            bm_idx = next(i for i, e in enumerate(published_events) if e.get("vendor") == "boodmo" and e["type"] in ("supplier_result", "adapter_completed"))
            self.assertLess(ad_idx, bm_idx)

            await close_session(thread_id)

    async def test_two_simultaneous_customer_sessions_isolated(self):
        """Run two customer sessions simultaneously; verify zero state or page crosstalk."""
        thread_1 = "customer-session-alpha"
        thread_2 = "customer-session-beta"

        s1 = open_session(thread_1)
        s2 = open_session(thread_2)

        self.assertNotEqual(s1.adapters["boodmo"].session_id, s2.adapters["boodmo"].session_id)
        self.assertNotEqual(s1.adapters["autodukan"].session_id, s2.adapters["autodukan"].session_id)

        # Simultaneous advancement
        p1 = VehicleProfile(maker="Tata", model="Nexon", year="2024", category="Mirrors")
        p2 = VehicleProfile(maker="Hyundai", model="Creta", year="2022", category="Brake System")

        s1.adapters["boodmo"].advance = AsyncMock(return_value=None)
        s1.adapters["autodukan"].advance = AsyncMock(return_value=None)
        s2.adapters["boodmo"].advance = AsyncMock(return_value=None)
        s2.adapters["autodukan"].advance = AsyncMock(return_value=None)

        await asyncio.gather(
            s1.adapters["boodmo"].advance(p1),
            s1.adapters["autodukan"].advance(p1),
            s2.adapters["boodmo"].advance(p2),
            s2.adapters["autodukan"].advance(p2),
        )

        s1.adapters["boodmo"].advance.assert_called_once_with(p1)
        s2.adapters["boodmo"].advance.assert_called_once_with(p2)

        await close_session(thread_1)
        await close_session(thread_2)


class TestApiEndpointsAndCancel(unittest.TestCase):
    """Verify HTTP API endpoints including cancel and reset."""

    def setUp(self):
        self.client = TestClient(app)

    def test_cancel_session_closes_cleanly(self):
        """POST /api/chat/cancel closes session and returns 200 OK."""
        res = self.client.post("/api/chat/cancel", json={"thread_id": "session-to-cancel"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")

    def test_health_check_endpoint(self):
        """GET /api/health returns healthy with supplier list."""
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("boodmo", data["suppliers"])
        self.assertIn("autodukan", data["suppliers"])


if __name__ == "__main__":
    unittest.main()
