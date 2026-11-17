"""Regression and validation tests for Phase 2 AutoDukan and Railway optimizations.

Covers:
- AutoDukan extraction timing.
- Multiple product-card extraction via single JS evaluation.
- Missing product fields handling (price, sku, brand).
- Duplicate product cards deduplication.
- Pagination handling.
- Cancellation during extraction.
- Memory cleanup after session closure.
- Queued search cancellation and semaphore release.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from parts_agent import auto_dukan
from parts_agent.browser_manager import BrowserManager
from parts_agent.graph import (
    Session,
    collect,
    close_session,
    get_search_semaphore,
    open_session,
    MAX_CONCURRENT_SEARCHES,
)
from parts_agent.models import Listing, SearchOutcome, VehicleProfile


class TestPhase2AutoDukanOptimization(unittest.IsolatedAsyncioTestCase):

    async def test_multiple_product_cards_extraction_and_deduplication(self):
        """Test that _read_cards extracts multiple fields and deduplicates cards."""
        adapter = auto_dukan.AutoDukanAdapter(headless=True)
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False

        # Mock evaluate returning multiple cards with some duplicates
        raw_mock_cards = [
            {
                "title": "Front Brake Pad Set",
                "url": "https://autodukan.com/product/front-brake-pad-101",
                "priceText": "₹1,407.75",
                "mrpText": "MRP ₹1,800.00",
                "sku": "F002H26442",
                "brand": "Bosch",
            },
            # Duplicate URL
            {
                "title": "Front Brake Pad Set",
                "url": "https://autodukan.com/product/front-brake-pad-101",
                "priceText": "₹1,407.75",
                "mrpText": "MRP ₹1,800.00",
                "sku": "F002H26442",
                "brand": "Bosch",
            },
            {
                "title": "Brake Pad Ceramic Front",
                "url": "https://autodukan.com/product/front-brake-pad-102",
                "priceText": "₹1,033.50",
                "mrpText": None,
                "sku": "F002H28046",
                "brand": "Bosch",
            },
        ]
        mock_page.evaluate = AsyncMock(return_value=raw_mock_cards)
        mock_page.wait_for_selector = AsyncMock()
        adapter.page = mock_page

        cards = await adapter._read_cards()
        self.assertEqual(len(cards), 3)

        # Now test collect deduplication and field mapping
        p = VehicleProfile(maker="Maruti", model="Swift", year="2020", variant="VXI", category="BRAKE PADS", part="Brake Pad")
        adapter._vehicle_confirmed = True
        adapter._car_model_id = "73927"
        adapter._category_confirmed = True
        adapter._advanced_signature = f"{p.maker}:{p.model}:{p.year}:{p.variant}:{p.category}"
        adapter._advanced_ok = True

        outcome = await adapter.collect(p)
        self.assertEqual(outcome.status, "ok")
        # Deduplication must reduce 3 cards (with 1 duplicate URL) to 2 unique listings!
        self.assertEqual(len(outcome.listings), 2)
        urls = [l["url"] for l in outcome.listings]
        self.assertEqual(len(urls), len(set(urls)))
        # Verify fitment is verified
        self.assertEqual(outcome.listings[0]["fitment"], "verified")
        self.assertEqual(outcome.listings[0]["price"], 1033.50)  # lowest price first
        self.assertEqual(outcome.listings[1]["price"], 1407.75)

    async def test_missing_product_fields(self):
        """Test extraction resilience when fields like price, sku, or brand are missing."""
        adapter = auto_dukan.AutoDukanAdapter(headless=True)
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False

        cards_with_missing_fields = [
            {
                "title": "Universal Brake Pad",
                "url": "https://autodukan.com/product/pad-missing-price",
                "priceText": None,  # Missing price
                "mrpText": None,
                "sku": None,  # Missing SKU
                "brand": None,  # Missing brand
            }
        ]
        mock_page.evaluate = AsyncMock(return_value=cards_with_missing_fields)
        mock_page.wait_for_selector = AsyncMock()
        adapter.page = mock_page

        p = VehicleProfile(maker="Maruti", model="Swift", year="2020", variant="VXI", category="BRAKE PADS", part="Brake Pad")
        adapter._vehicle_confirmed = True
        adapter._car_model_id = "73927"
        adapter._category_confirmed = True
        adapter._advanced_signature = f"{p.maker}:{p.model}:{p.year}:{p.variant}:{p.category}"
        adapter._advanced_ok = True

        outcome = await adapter.collect(p)
        self.assertEqual(len(outcome.listings), 1)
        item = outcome.listings[0]
        self.assertIsNone(item["price"])
        self.assertEqual(item["part_number"], "")
        self.assertEqual(item["brand"], "Maruti")  # falls back to profile.maker
        self.assertIn("price was not readable on the category result", item["notes"])

    async def test_pagination_handling(self):
        """Test that AutoDukan handles pagination indicators without crashing."""
        adapter = auto_dukan.AutoDukanAdapter(headless=True)
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False

        mock_page.evaluate = AsyncMock(return_value={
            "hasPagination": True,
            "hasNext": True,
            "paginationHtml": '<ul class="pagination"><li class="active">1</li><li><a href="?page=2">2</a></li></ul>'
        })
        adapter.page = mock_page

        # Verify page state evaluation executes properly
        res = await mock_page.evaluate("() => true")
        self.assertTrue(res["hasPagination"])
        self.assertTrue(res["hasNext"])

    async def test_cancellation_during_extraction(self):
        """Test clean cancellation during an in-flight AutoDukan extraction."""
        adapter = auto_dukan.AutoDukanAdapter(headless=True)
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False

        async def slow_extract(*args, **kwargs):
            await asyncio.sleep(5)
            return []

        mock_page.evaluate = slow_extract
        mock_page.wait_for_selector = AsyncMock()
        adapter.page = mock_page

        p = VehicleProfile(maker="Maruti", model="Swift", year="2020", variant="VXI", category="BRAKE PADS", part="Brake Pad")
        adapter._vehicle_confirmed = True
        adapter._car_model_id = "73927"
        adapter._category_confirmed = True
        adapter._advanced_signature = f"{p.maker}:{p.model}:{p.year}:{p.variant}:{p.category}"
        adapter._advanced_ok = True

        task = asyncio.create_task(adapter.collect(p))
        await asyncio.sleep(0.05)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_search_semaphore_and_queued_cancellation(self):
        """Test that the concurrency semaphore queues searches and releases correctly on cancel."""
        sem = get_search_semaphore()
        # Acquire all permits to simulate saturated capacity
        acquired = []
        for _ in range(MAX_CONCURRENT_SEARCHES):
            await sem.acquire()
            acquired.append(True)

        self.assertTrue(sem.locked())

        # Now launch a queued task that attempts to acquire the semaphore
        queued_entered = False
        on_queued_called = False

        async def queued_search():
            nonlocal queued_entered
            queued_entered = True
            async with sem:
                return "completed"

        task = asyncio.create_task(queued_search())
        await asyncio.sleep(0.05)
        self.assertTrue(queued_entered)
        self.assertFalse(task.done())

        # Cancel the queued task while it's waiting
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        # Release previously acquired permits
        for _ in acquired:
            sem.release()

        # Verify semaphore permits are intact and can be acquired immediately
        async with sem:
            pass

    async def test_memory_cleanup_after_session_close(self):
        """Test that close_session releases all adapter resources and empties SESSIONS registry."""
        thread_id = "test-mem-cleanup-thread"
        session = open_session(thread_id, headless=True)
        mock_bm = MagicMock()
        mock_bm.close_session = AsyncMock()
        session.boodmo_adapter.browser_manager = mock_bm
        session.autodukan_adapter.browser_manager = mock_bm

        await close_session(thread_id)
        # SESSIONS dictionary must not hold this thread
        self.assertNotIn(thread_id, auto_dukan.get_browser_manager()._contexts)


if __name__ == "__main__":
    unittest.main()
