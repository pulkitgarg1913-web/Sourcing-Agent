"""Dedicated tests for Part Category Mismatch, Contradictory Results and Product Counter Sync.

Verifies:
1. Search Hyundai Creta 2026 Headlights: requested category remains Headlights throughout.
2. Boodmo returns two valid headlights: two relevant products displayed and counted.
3. Boodmo returns two indicators instead of headlights: two raw listings may be recorded, but zero relevant headlight matches.
4. Boodmo returns two products and AutoDukan returns zero: Boodmo results remain visible; total extracted count is correct.
5. Both suppliers return relevant headlights: both supplier tables populate independently.
6. Refresh or repeat a search using cached results: correct category and counts are retained.
7. Final AI response generation uses structured supplier results as source of truth.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from parts_agent import auto_dukan, boodmo, cache_manager, graph, relevance
from parts_agent.models import Listing, SearchOutcome, VehicleProfile


class TestPartCategoryAndCounterSync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_browser_manager = MagicMock()
        self.mock_page = AsyncMock()
        self.mock_page.url = "https://example.com"
        self.mock_page.goto = AsyncMock()
        self.mock_browser_manager.get_page = AsyncMock(return_value=self.mock_page)
        self.mock_browser_manager.close_session = AsyncMock()
        self.mock_browser_manager.release_page = AsyncMock()

    # -------------------------------------------------------------------------
    # Test 1: Requested Part remains Headlights throughout session
    # -------------------------------------------------------------------------
    async def test_01_requested_category_remains_headlights_throughout(self):
        """User requests headlights for Hyundai Creta 2026; requested_part stays Headlights."""
        conv = graph.Conversation(thread_id="test-creta-headlight-preservation")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)
        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)
        session.adapters["boodmo"].collect = AsyncMock(return_value=SearchOutcome("boodmo", "ok", listings=[]))
        session.adapters["autodukan"].collect = AsyncMock(return_value=SearchOutcome("autodukan", "no_results", listings=[]))

        with patch("parts_agent.graph._session", return_value=session):
            state = await conv.start("Find headlights for Hyundai Creta 2026 1.5L SX Premium MT Diesel")

            # Check profile and hints across both adapters
            prof = conv.state.get("profile") or {}
            self.assertEqual(prof.get("requested_part", "").lower(), "headlights")
            self.assertEqual(prof.get("hints", {}).get("requested_part", "").lower(), "headlights")

            # Verify Boodmo category resolution does NOT select Indicator
            b_adapter = boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager)
            vp = VehicleProfile.from_dict(prof)
            await b_adapter.resolve_category(vp)
            self.assertEqual(vp.category, "Headlight")
            self.assertNotEqual(vp.category, "Indicator")
            # Alternate categories must NOT contain Indicator, Fog Lamp, or Tail Light
            alts = vp.hints.get("alternate_categories") or []
            self.assertNotIn("Indicator", alts)
            self.assertNotIn("Fog Lamp", alts)
            self.assertNotIn("Tail Light", alts)

    # -------------------------------------------------------------------------
    # Test 2: Boodmo returns two valid headlights
    # -------------------------------------------------------------------------
    async def test_02_boodmo_two_valid_headlights_displayed_and_counted(self):
        """Two valid headlights returned by Boodmo are both counted as relevant matches."""
        conv = graph.Conversation(thread_id="test-creta-two-valid-headlights")
        announcements = []
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: announcements.append(t),
        )

        boodmo_outcome = SearchOutcome(
            vendor="boodmo",
            status="ok",
            vehicle_label="Hyundai Creta 2026",
            listings=[
                Listing(
                    title="Headlamp Assembly Right Hand Creta 2024-2026",
                    brand="Hyundai Mobis",
                    part_number="92102-A9000",
                    price=4500.0,
                    mrp=5200.0,
                    url="https://boodmo.com/catalog/part-1",
                    origin="OEM",
                    fitment="verified",
                    available=True,
                    vendor="boodmo",
                    seller="Authorized Supplier Hub",
                    dispatch="24–48 hours",
                ),
                Listing(
                    title="Headlamp Assembly Left Hand Creta 2024-2026",
                    brand="Hyundai Mobis",
                    part_number="92101-A9000",
                    price=4500.0,
                    mrp=5200.0,
                    url="https://boodmo.com/catalog/part-2",
                    origin="OEM",
                    fitment="verified",
                    available=True,
                    vendor="boodmo",
                    seller="Authorized Supplier Hub",
                    dispatch="24–48 hours",
                ),
            ],
            source_url="https://boodmo.com/catalog/4647-headlight/",
            elapsed_seconds=12.5,
        )

        autodukan_outcome = SearchOutcome(
            vendor="autodukan",
            status="no_results",
            vehicle_label="Hyundai Creta 2026",
            listings=[],
            source_url="https://autodukan.com",
            elapsed_seconds=11.0,
        )

        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)
        session.adapters["boodmo"].collect = AsyncMock(return_value=boodmo_outcome)
        session.adapters["autodukan"].collect = AsyncMock(return_value=autodukan_outcome)

        with patch("parts_agent.graph._session", return_value=session), \
             patch("parts_agent.cache_manager.get_cached_search", return_value=None):
            await conv.start("Find headlights for Hyundai Creta 2026 1.5L SX Premium MT Diesel")

            outcome = conv.state.get("outcome") or {}
            listings = outcome.get("listings") or []
            self.assertEqual(len(listings), 2)
            self.assertIn(listings[0]["relevance"], (relevance.EXACT_MATCH, relevance.EXACT_PART_MATCH))
            self.assertEqual(listings[0].get("part_family_classification"), relevance.EXACT_PART_MATCH)
            self.assertIn(listings[1]["relevance"], (relevance.EXACT_MATCH, relevance.EXACT_PART_MATCH))
            self.assertEqual(listings[1].get("part_family_classification"), relevance.EXACT_PART_MATCH)

            # Check announcement: mentions 2 matching headlights
            boodmo_announces = [a for a in announcements if "Boodmo completed" in a]
            self.assertTrue(any("2 matching headlights found" in a for a in boodmo_announces))

            # Check final narrative summary
            summary = conv.state.get("summary") or ""
            self.assertIn("Boodmo returned 2 relevant products", summary)
            self.assertIn("AutoDukan returned no matching products", summary)
            self.assertNotIn("Indicator", summary)

    # -------------------------------------------------------------------------
    # Test 3: Boodmo returns two indicators instead of headlights
    # -------------------------------------------------------------------------
    async def test_03_boodmo_indicators_recorded_as_raw_but_zero_relevant_matches(self):
        """When Boodmo returns 2 indicator listings for a headlights search, relevant count is 0."""
        conv = graph.Conversation(thread_id="test-creta-indicators-rejected")
        announcements = []
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: announcements.append(t),
        )

        boodmo_indicator_outcome = SearchOutcome(
            vendor="boodmo",
            status="ok",
            vehicle_label="Hyundai Creta 2026",
            listings=[
                Listing(
                    title="Turn Signal Indicator Light RH Creta",
                    brand="Lumax",
                    part_number="IND-001",
                    price=650.0,
                    mrp=800.0,
                    url="https://boodmo.com/catalog/part-ind-1",
                    fitment="verified",
                    available=True,
                    vendor="boodmo",
                ),
                Listing(
                    title="Side Turn Indicator Lamp LH Creta",
                    brand="Lumax",
                    part_number="IND-002",
                    price=650.0,
                    mrp=800.0,
                    url="https://boodmo.com/catalog/part-ind-2",
                    fitment="verified",
                    available=True,
                    vendor="boodmo",
                ),
            ],
            source_url="https://boodmo.com/catalog/4204-turn_signal_light/",
            elapsed_seconds=13.1,
            raw_count=2,
        )

        autodukan_outcome = SearchOutcome(
            vendor="autodukan",
            status="no_results",
            vehicle_label="Hyundai Creta 2026",
            listings=[],
            source_url="https://autodukan.com",
            elapsed_seconds=10.5,
        )

        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)
        session.adapters["boodmo"].collect = AsyncMock(return_value=boodmo_indicator_outcome)
        session.adapters["autodukan"].collect = AsyncMock(return_value=autodukan_outcome)

        with patch("parts_agent.graph._session", return_value=session), \
             patch("parts_agent.cache_manager.get_cached_search", return_value=None):
            await conv.start("Find headlights for Hyundai Creta 2026 1.5L SX Premium MT Diesel")

            outcome = conv.state.get("outcome") or {}
            listings = outcome.get("listings") or []
            # Relevant matching listings must be 0
            self.assertEqual(len(listings), 0)

            # Raw count is preserved on vendor outcome
            vo_boodmo = next(v for v in outcome["vendor_outcomes"] if v["vendor"] == "boodmo")
            self.assertEqual(vo_boodmo.get("raw_count"), 2)
            self.assertEqual(len(vo_boodmo.get("listings", [])), 0)

            # Announcement does NOT report contradictory 2 parts found
            boodmo_announces = [a for a in announcements if "Boodmo completed" in a]
            self.assertTrue(any("2 catalogue listings extracted, 0 matching headlights" in a for a in boodmo_announces))

            # Final response explains raw listings did not match requested Headlights category
            summary = conv.state.get("summary") or ""
            self.assertIn("Boodmo extracted 2 catalogue listings, but neither matched the requested Headlights category", summary)
            self.assertIn("AutoDukan returned no matching products", summary)
            self.assertNotIn("under Indicator", summary)

    # -------------------------------------------------------------------------
    # Test 4: Boodmo returns 2 headlights, AutoDukan returns 0
    # -------------------------------------------------------------------------
    async def test_04_boodmo_two_autodukan_zero_boodmo_remains_visible(self):
        """Boodmo 2 products, AutoDukan 0: Boodmo products remain visible and total count is 2."""
        conv = graph.Conversation(thread_id="test-creta-boodmo-2-autodukan-0")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)

        boodmo_outcome = SearchOutcome(
            vendor="boodmo",
            status="ok",
            vehicle_label="Hyundai Creta 2026",
            listings=[
                Listing(
                    title="Headlamp RH Creta",
                    brand="Hyundai Mobis",
                    part_number="92102-A9000",
                    price=4500.0,
                    mrp=5200.0,
                    url="https://boodmo.com/part-1",
                    available=True,
                ),
                Listing(
                    title="Headlamp LH Creta",
                    brand="Hyundai Mobis",
                    part_number="92101-A9000",
                    price=4500.0,
                    mrp=5200.0,
                    url="https://boodmo.com/part-2",
                    available=True,
                ),
            ],
            source_url="https://boodmo.com",
        )

        autodukan_outcome = SearchOutcome(
            vendor="autodukan",
            status="no_results",
            vehicle_label="Hyundai Creta 2026",
            listings=[],
            source_url="https://autodukan.com",
        )

        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)
        session.adapters["boodmo"].collect = AsyncMock(return_value=boodmo_outcome)
        session.adapters["autodukan"].collect = AsyncMock(return_value=autodukan_outcome)

        with patch("parts_agent.graph._session", return_value=session), \
             patch("parts_agent.cache_manager.get_cached_search", return_value=None):
            await conv.start("Find headlights for Hyundai Creta 2026 1.5L SX Premium MT Diesel")

            outcome = conv.state.get("outcome") or {}
            self.assertEqual(len(outcome.get("listings", [])), 2)

            vo_boodmo = next(v for v in outcome["vendor_outcomes"] if v["vendor"] == "boodmo")
            vo_autodukan = next(v for v in outcome["vendor_outcomes"] if v["vendor"] == "autodukan")

            self.assertEqual(len(vo_boodmo["listings"]), 2)
            self.assertEqual(len(vo_autodukan["listings"]), 0)

            summary = conv.state.get("summary") or ""
            self.assertIn("Boodmo returned 2 relevant products", summary)
            self.assertIn("AutoDukan returned no matching products", summary)

    # -------------------------------------------------------------------------
    # Test 5: Both suppliers return relevant headlights
    # -------------------------------------------------------------------------
    async def test_05_both_suppliers_return_relevant_headlights(self):
        """Both suppliers return valid headlights; tables and counts populate independently."""
        conv = graph.Conversation(thread_id="test-creta-both-suppliers-headlights")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)

        boodmo_outcome = SearchOutcome(
            vendor="boodmo",
            status="ok",
            vehicle_label="Hyundai Creta 2026",
            listings=[
                Listing(
                    title="Headlamp RH Creta",
                    brand="Hyundai Mobis",
                    part_number="92102-A9000",
                    price=4500.0,
                    url="https://boodmo.com/part-1",
                    available=True,
                ),
            ],
            source_url="https://boodmo.com",
        )

        autodukan_outcome = SearchOutcome(
            vendor="autodukan",
            status="ok",
            vehicle_label="Hyundai Creta 2026",
            listings=[
                Listing(
                    title="Lumax Headlight Assembly Creta RH",
                    brand="Lumax",
                    part_number="216-HLA-RH",
                    price=3800.0,
                    url="https://autodukan.com/part-1",
                    available=True,
                ),
            ],
            source_url="https://autodukan.com",
        )

        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)
        session.adapters["boodmo"].collect = AsyncMock(return_value=boodmo_outcome)
        session.adapters["autodukan"].collect = AsyncMock(return_value=autodukan_outcome)

        with patch("parts_agent.graph._session", return_value=session), \
             patch("parts_agent.cache_manager.get_cached_search", return_value=None):
            await conv.start("Find headlights for Hyundai Creta 2026 1.5L SX Premium MT Diesel")

            outcome = conv.state.get("outcome") or {}
            self.assertEqual(len(outcome.get("listings", [])), 2)

            vo_boodmo = next(v for v in outcome["vendor_outcomes"] if v["vendor"] == "boodmo")
            vo_autodukan = next(v for v in outcome["vendor_outcomes"] if v["vendor"] == "autodukan")

            self.assertEqual(len(vo_boodmo["listings"]), 1)
            self.assertEqual(len(vo_autodukan["listings"]), 1)

            summary = conv.state.get("summary") or ""
            self.assertIn("Boodmo returned 1 relevant product", summary)
            self.assertIn("AutoDukan returned 1 relevant product", summary)

    # -------------------------------------------------------------------------
    # Test 6: Refresh or repeat search using cached results
    # -------------------------------------------------------------------------
    async def test_06_repeat_search_using_cached_results(self):
        """Repeat search using cached outcomes preserves category, supplier counts, and response."""
        cached_outcomes = [
            {
                "vendor": "boodmo",
                "supplier": "Boodmo",
                "supplier_name": "Boodmo",
                "status": "ok",
                "vehicle_label": "Hyundai Creta 2026 1.5L SX Premium MT",
                "source_url": "https://boodmo.com",
                "raw_count": 2,
                "listings": [
                    {
                        "title": "Headlamp Assembly Right Hand Creta",
                        "brand": "Hyundai Mobis",
                        "part_number": "92102-A9000",
                        "price": 4500.0,
                        "url": "https://boodmo.com/part-1",
                        "available": True,
                        "fitment": "verified",
                        "relevance": "EXACT_PART_MATCH",
                    },
                    {
                        "title": "Headlamp Assembly Left Hand Creta",
                        "brand": "Hyundai Mobis",
                        "part_number": "92101-A9000",
                        "price": 4500.0,
                        "url": "https://boodmo.com/part-2",
                        "available": True,
                        "fitment": "verified",
                        "relevance": "EXACT_PART_MATCH",
                    },
                ],
            },
            {
                "vendor": "autodukan",
                "supplier": "AutoDukan",
                "supplier_name": "AutoDukan",
                "status": "no_results",
                "vehicle_label": "Hyundai Creta 2026 1.5L SX Premium MT",
                "source_url": "https://autodukan.com",
                "raw_count": 0,
                "listings": [],
            },
        ]

        conv = graph.Conversation(thread_id="test-creta-cache-hit")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)

        with patch("parts_agent.graph._session", return_value=session), \
             patch("parts_agent.cache_manager.get_cached_search", return_value=cached_outcomes):
            await conv.start("Find headlights for Hyundai Creta 2026 1.5L SX Premium MT Diesel")

            outcome = conv.state.get("outcome") or {}
            self.assertTrue(outcome.get("is_cached"))
            self.assertEqual(len(outcome.get("listings", [])), 2)

            summary = conv.state.get("summary") or ""
            self.assertIn("Headlights", summary)
            self.assertIn("Boodmo returned 2 relevant products", summary)
            self.assertIn("AutoDukan returned no matching products", summary)
            self.assertNotIn("Indicator", summary)


if __name__ == "__main__":
    unittest.main()
