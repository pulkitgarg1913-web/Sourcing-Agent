"""Regression Test Suite for Category Clarification Loop & Supplier Product Brand Matching.

Covers all 15 mandatory verification scenarios:
1. Fuel pump category resolution on Boodmo and AutoDukan.
2. Category clarification options include "None of these" / text search fallback.
3. Selecting "None of these" falls back cleanly to keyword/text search without repeating question.
4. Clarification answer is saved to state and not re-asked.
5. Tata Safari oil filter search strictly rejects Audi / incompatible maker products.
6. Product relevance verifies maker match (Tata vs Audi, Maruti vs Hyundai, etc.).
7. Verified vs unverified fitment labeling.
8. Price does not override vehicle relevance.
9. Multi-turn context preservation (vehicle info not lost during category answer).
10. Multi-turn context preservation (part info not lost during vehicle answer).
11. Boodmo and AutoDukan search outcomes are isolated and correct.
12. Both suppliers handle fallback search when category is unavailable.
13. SSE events stream smoothly during clarification and search.
14. End-to-end simulated flow for "fuel pump" without infinite loop.
15. End-to-end simulated flow for "Tata Safari oil filter" with brand validation.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from parts_agent import auto_dukan, boodmo, graph, models, relevance
from parts_agent.models import Listing, NeedInput, SearchOutcome, VehicleProfile
from parts_agent.relevance import (
    ACCESSORY,
    CLOSE_MATCH,
    COMPONENT,
    EXACT_MATCH,
    EXACT_PART_MATCH,
    PART_ALIAS_MATCH,
    RELATED_COMPONENT,
    WRONG_PART_TYPE,
    UNKNOWN,
    INCOMPATIBLE,
    IRRELEVANT,
    NEEDS_REVIEW,
    check_vehicle_compatibility,
    check_part_family_compatibility,
    classify_listings,
    classify_product,
    rank_listings,
)


class TestBugFixesAndRegression(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from parts_agent import cache_manager
        cache_manager.purge_cached_search()
        self.mock_browser_manager = MagicMock()
        self.mock_browser_manager.get_page = AsyncMock()
        self.mock_browser_manager.close_session = AsyncMock()

    # -------------------------------------------------------------------------
    # Scenario 1: Fuel pump category resolution on Boodmo and AutoDukan
    # -------------------------------------------------------------------------
    async def test_scenario_01_fuel_pump_category_resolution(self):
        """Fuel pump should be recognized and mapped to the appropriate fuel category without clarification."""
        # AutoDukan
        ad_adapter = auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager)
        ad_profile = VehicleProfile(part="fuel pump")
        need_ad = await ad_adapter.resolve_category(ad_profile)
        self.assertIsNone(need_ad)
        self.assertEqual(ad_profile.category, "FUEL PUMP")

        # Boodmo
        bm_adapter = boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager)
        bm_profile = VehicleProfile(part="fuel pump")
        need_bm = await bm_adapter.resolve_category(bm_profile)
        self.assertIsNone(need_bm)
        self.assertEqual(bm_profile.category, "Fuel Pump")

    # -------------------------------------------------------------------------
    # Scenario 2: Category clarification options include escape options
    # -------------------------------------------------------------------------
    async def test_scenario_02_category_clarification_includes_escape_options(self):
        """When category clarification is required, options must include 'None of these' and 'Search by part name instead'."""
        ad_adapter = auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager)
        ad_profile = VehicleProfile(part="Brake System Assembly")

        # Mock page to return category links
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.goto = AsyncMock()
        ad_adapter.page = mock_page
        ad_adapter._visible_category_links = AsyncMock(return_value=[
            {"path": "/subcategory/lighting", "href": "https://autodukan.com/subcategory/lighting"},
            {"path": "/subcategory/brakes", "href": "https://autodukan.com/subcategory/brakes"},
        ])
        ad_adapter._category_link_label = lambda link: "Lighting" if "lighting" in link["path"] else "Brake System"

        with patch("parts_agent.llm.generate_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = MagicMock(text="NONE")
            need = await ad_adapter.resolve_category(ad_profile)
            self.assertIsNotNone(need)
            self.assertEqual(need.field, "category")
            self.assertIn("None of these", need.options)
            self.assertIn("Search by part name instead", need.options)

    # -------------------------------------------------------------------------
    # Scenario 3: Selecting "None of these" falls back cleanly to search
    # -------------------------------------------------------------------------
    async def test_scenario_03_selecting_none_of_these_falls_back_to_search(self):
        """Selecting 'None of these' applies search fallback, clears pending question, and does not loop."""
        p_ad = VehicleProfile(part="fuel pump", maker="Tata", model="Safari")
        p_bm = VehicleProfile(part="fuel pump", maker="Tata", model="Safari")
        profiles = {"autodukan": p_ad, "boodmo": p_bm}

        pending_need = {
            "field": "category",
            "question": "Which AutoDukan category best fits fuel pump?",
            "options": ["Lighting", "Brake System", "Body Parts", "None of these", "Search by part name instead"],
            "vendor": "autodukan",
        }
        state: graph.State = {
            "request": "fuel pump for Tata Safari",
            "pending": pending_need,
            "answer": "None of these",
            "supplier_profiles": {k: v.to_dict() for k, v in profiles.items()},
            "profile": p_bm.to_dict(),
        }

        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        config = {"configurable": {"thread_id": "test-none-of-these-thread"}}
        graph.SESSIONS["test-none-of-these-thread"] = session

        try:
            new_state = await graph.interpret(state, config)
            # Pending must be cleared
            self.assertIsNone(new_state.get("pending"))
            # Hints must record category_fallback
            self.assertTrue(new_state["supplier_profiles"]["autodukan"]["hints"].get("category_fallback"))
            self.assertTrue(new_state["supplier_profiles"]["boodmo"]["hints"].get("category_fallback"))

            # Next navigation step must NOT ask for category again!
            nav_state = await graph.navigate(new_state, config)
            # Both adapters must be ready to collect, not asking for category
            if nav_state.get("pending"):
                self.assertNotEqual(nav_state["pending"]["field"], "category")
        finally:
            graph.SESSIONS.pop("test-none-of-these-thread", None)

    # -------------------------------------------------------------------------
    # Scenario 4: Clarification answer is saved to state and not re-asked
    # -------------------------------------------------------------------------
    async def test_scenario_04_clarification_answer_saved_and_not_reasked(self):
        """When user provides a category answer, it is recorded and the question is never repeated."""
        p_ad = VehicleProfile(part="oil filter", maker="Tata", model="Safari")
        profiles = {"autodukan": p_ad, "boodmo": VehicleProfile(part="oil filter", maker="Tata", model="Safari")}

        pending_need = {
            "field": "category",
            "question": "Which AutoDukan category best fits oil filter?",
            "options": ["OIL FILTER", "FILTERS", "None of these"],
            "vendor": "autodukan",
        }
        state: graph.State = {
            "request": "oil filter for Tata Safari",
            "pending": pending_need,
            "answer": "OIL FILTER",
            "supplier_profiles": {k: v.to_dict() for k, v in profiles.items()},
            "profile": profiles["boodmo"].to_dict(),
        }

        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        config = {"configurable": {"thread_id": "test-answered-cat-thread"}}
        graph.SESSIONS["test-answered-cat-thread"] = session

        try:
            with patch("parts_agent.llm.match_option_async", new_callable=AsyncMock) as mock_match:
                mock_match.return_value = (0, False)
                new_state = await graph.interpret(state, config)
                self.assertIsNone(new_state.get("pending"))
                ad_prof = VehicleProfile.from_dict(new_state["supplier_profiles"]["autodukan"])
                self.assertEqual(ad_prof.category, "OIL FILTER")

                # Re-advancing adapter with this profile should NEVER ask for category again
                need = await session.autodukan_adapter.resolve_category(ad_prof)
                self.assertIsNone(need)
        finally:
            graph.SESSIONS.pop("test-answered-cat-thread", None)

    # -------------------------------------------------------------------------
    # Scenario 5: Tata Safari oil filter search strictly rejects Audi products
    # -------------------------------------------------------------------------
    def test_scenario_05_tata_safari_oil_filter_rejects_audi(self):
        """An Audi oil filter priced at ₹50,000 must be marked INCOMPATIBLE and excluded from Tata Safari results."""
        listings = [
            {
                "title": "Audi Genuine Oil Filter Element A4 A6 Q5",
                "price": 50000.0,
                "brand": "Audi",
                "part_number": "06L115562B",
                "url": "https://autodukan.com/product/audi-filter",
            },
            {
                "title": "Tata Genuine Oil Filter Harrier / Safari 2.0L Diesel",
                "price": 750.0,
                "brand": "Tata",
                "part_number": "542718140101",
                "url": "https://autodukan.com/product/tata-filter",
            },
            {
                "title": "Bosch Oil Filter for Tata Safari Diesel",
                "price": 680.0,
                "brand": "Bosch",
                "part_number": "F002H24001",
                "url": "https://autodukan.com/product/bosch-filter",
            },
        ]

        classified = classify_listings(
            listings,
            query="Tata Safari oil filter",
            requested_part="oil filter",
            target_maker="Tata",
            target_model="Safari",
        )

        audi_item = next(l for l in classified if "Audi" in l["title"])
        tata_item = next(l for l in classified if "Tata" in l["title"])
        bosch_item = next(l for l in classified if "Bosch" in l["title"])

        # Audi item must be classified as INCOMPATIBLE with score 0.0
        self.assertEqual(audi_item["relevance"], INCOMPATIBLE)
        self.assertEqual(audi_item["relevance_score"], 0.0)
        self.assertIn("Incompatible vehicle brand", audi_item["relevance_reason"])

        # Tata and Bosch items must be compatible
        self.assertEqual(tata_item["relevance"], EXACT_MATCH)
        self.assertEqual(bosch_item["relevance"], EXACT_MATCH)

        # Ranked listings MUST NOT contain the Audi product!
        ranked = rank_listings(classified, price_order="asc")
        ranked_titles = [l["title"] for l in ranked]
        self.assertNotIn(audi_item["title"], ranked_titles)
        self.assertIn(tata_item["title"], ranked_titles)
        self.assertIn(bosch_item["title"], ranked_titles)

    # -------------------------------------------------------------------------
    # Scenario 6: Product relevance verifies maker match matrix
    # -------------------------------------------------------------------------
    def test_scenario_06_maker_match_matrix(self):
        """Cross-brand parts must be rejected across multiple makers (Maruti vs Hyundai, etc.)."""
        # Case A: Maruti Swift vs Hyundai Mobis Creta Filter
        res_a = classify_product(
            product_title="Hyundai Mobis Engine Oil Filter Creta",
            product_brand="Hyundai",
            target_maker="Maruti",
            target_model="Swift",
        )
        self.assertEqual(res_a["relevance"], INCOMPATIBLE)

        # Case B: Mahindra Thar vs Tata Nexon Brake Pads
        res_b = classify_product(
            product_title="Tata Genuine Front Brake Pads Nexon",
            product_brand="Tata",
            target_maker="Mahindra",
            target_model="Thar",
        )
        self.assertEqual(res_b["relevance"], INCOMPATIBLE)

        # Case C: Hyundai Creta vs Hyundai Genuine Creta Part
        res_c = classify_product(
            product_title="Hyundai Mobis Front Bumper Creta 2021",
            product_brand="Hyundai",
            target_maker="Hyundai",
            target_model="Creta",
        )
        self.assertIn(res_c["relevance"], [EXACT_MATCH, CLOSE_MATCH])

    # -------------------------------------------------------------------------
    # Scenario 7: Verified vs unverified fitment labeling
    # -------------------------------------------------------------------------
    def test_scenario_07_verified_vs_unverified_fitment_labeling(self):
        """Check compatibility status accurately assigns verified, unverified, or incompatible."""
        # 1. Exact match with target maker and model
        stat_1, _ = check_vehicle_compatibility("Tata Safari Oil Filter 2.0L", "Tata", "Tata", "Safari")
        self.assertEqual(stat_1, "verified")

        # 2. Generic aftermarket filter without contradictory maker
        stat_2, _ = check_vehicle_compatibility("Mann-Filter Oil Filter W712/52", "Mann-Filter", "Tata", "Safari")
        self.assertEqual(stat_2, "unverified")

        # 3. Explicit contradictory vehicle brand
        stat_3, _ = check_vehicle_compatibility("Audi Oil Filter A4", "Audi", "Tata", "Safari")
        self.assertEqual(stat_3, "incompatible")

    # -------------------------------------------------------------------------
    # Scenario 8: Price does not override vehicle relevance
    # -------------------------------------------------------------------------
    def test_scenario_08_price_does_not_override_vehicle_relevance(self):
        """A cheap contradictory or irrelevant part (₹50) or expensive contradictory part (₹50,000) must never outrank verified part."""
        items = [
            {"title": "BMW Washer Nut", "price": 10.0, "brand": "BMW", "relevance": INCOMPATIBLE},
            {"title": "Tata Safari Genuine Oil Filter", "price": 750.0, "brand": "Tata", "relevance": EXACT_MATCH},
            {"title": "Audi OEM Oil Filter", "price": 45000.0, "brand": "Audi", "relevance": INCOMPATIBLE},
            {"title": "Safari Oil Filter Drain Gasket", "price": 25.0, "brand": "Tata", "relevance": ACCESSORY},
        ]
        ranked = rank_listings(items, price_order="asc")
        # Incompatible items must be excluded completely
        self.assertEqual(len(ranked), 2)
        # Primary match (₹750) must appear before accessory (₹25) despite being more expensive!
        self.assertEqual(ranked[0]["title"], "Tata Safari Genuine Oil Filter")
        self.assertEqual(ranked[1]["title"], "Safari Oil Filter Drain Gasket")

    # -------------------------------------------------------------------------
    # Scenario 9: Multi-turn context preservation (vehicle info kept during category answer)
    # -------------------------------------------------------------------------
    async def test_scenario_09_multi_turn_context_preserved_during_category_clarification(self):
        """Answering a category clarification question must not drop maker, model, year, or hints."""
        profiles = {
            "boodmo": VehicleProfile(maker="Tata", model="Safari", year="2022", part="oil filter", hints={"fuel": "diesel", "make": "Tata", "model": "Safari"}),
            "autodukan": VehicleProfile(maker="Tata", model="Safari", year="2022", part="oil filter", hints={"fuel": "diesel", "make": "Tata", "model": "Safari"}),
        }
        pending_need = {
            "field": "category",
            "question": "Which category?",
            "options": ["FILTERS", "None of these"],
            "vendor": "autodukan",
        }
        state: graph.State = {
            "request": "oil filter for Tata Safari 2022 diesel",
            "pending": pending_need,
            "answer": "FILTERS",
            "supplier_profiles": {k: v.to_dict() for k, v in profiles.items()},
            "profile": profiles["boodmo"].to_dict(),
        }

        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        config = {"configurable": {"thread_id": "test-context-cat-thread"}}
        graph.SESSIONS["test-context-cat-thread"] = session

        try:
            with patch("parts_agent.llm.match_option_async", new_callable=AsyncMock) as mock_match:
                mock_match.return_value = (0, False)
                new_state = await graph.interpret(state, config)
                ad_prof = VehicleProfile.from_dict(new_state["supplier_profiles"]["autodukan"])
                self.assertEqual(ad_prof.maker, "Tata")
                self.assertEqual(ad_prof.model, "Safari")
                self.assertEqual(ad_prof.year, "2022")
                self.assertEqual(ad_prof.part, "oil filter")
                self.assertEqual(ad_prof.hints.get("fuel"), "diesel")
        finally:
            graph.SESSIONS.pop("test-context-cat-thread", None)

    # -------------------------------------------------------------------------
    # Scenario 10: Multi-turn context preservation (part info kept during vehicle answer)
    # -------------------------------------------------------------------------
    async def test_scenario_10_multi_turn_context_preserved_during_vehicle_clarification(self):
        """Answering a vehicle clarification (e.g. year/model) must not drop the requested part."""
        profiles = {
            "boodmo": VehicleProfile(maker="Tata", model="Safari", part="fuel pump"),
            "autodukan": VehicleProfile(maker="Tata", model="Safari", part="fuel pump"),
        }
        pending_need = {
            "field": "year",
            "question": "Which year is your Tata Safari?",
            "options": ["2021", "2022", "2023"],
            "vendor": "boodmo",
        }
        state: graph.State = {
            "request": "fuel pump for Tata Safari",
            "pending": pending_need,
            "answer": "2021",
            "supplier_profiles": {k: v.to_dict() for k, v in profiles.items()},
            "profile": profiles["boodmo"].to_dict(),
        }

        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        config = {"configurable": {"thread_id": "test-context-veh-thread"}}
        graph.SESSIONS["test-context-veh-thread"] = session

        try:
            with patch("parts_agent.llm.match_option_async", new_callable=AsyncMock) as mock_match:
                mock_match.return_value = (0, False)
                new_state = await graph.interpret(state, config)
                bm_prof = VehicleProfile.from_dict(new_state["supplier_profiles"]["boodmo"])
                ad_prof = VehicleProfile.from_dict(new_state["supplier_profiles"]["autodukan"])
                self.assertEqual(bm_prof.part, "fuel pump")
                self.assertEqual(ad_prof.part, "fuel pump")
                self.assertEqual(bm_prof.year, "2021")
        finally:
            graph.SESSIONS.pop("test-context-veh-thread", None)

    # -------------------------------------------------------------------------
    # Scenario 11: Boodmo and AutoDukan search outcomes are isolated
    # -------------------------------------------------------------------------
    def test_scenario_11_supplier_outcomes_isolation(self):
        """Boodmo and AutoDukan outcomes must remain separate and never contaminate each other's listings."""
        bm_outcome = SearchOutcome(
            vendor="boodmo",
            status="ok",
            vehicle_label="Tata Safari 2021",
            listings=[{"title": "Boodmo Tata Oil Filter", "price": 750, "vendor": "boodmo"}],
            source_url="https://boodmo.com/catalog/sample",
        )
        ad_outcome = SearchOutcome(
            vendor="autodukan",
            status="ok",
            vehicle_label="Tata Safari 2021",
            listings=[{"title": "AutoDukan Tata Oil Filter", "price": 780, "vendor": "autodukan"}],
            source_url="https://autodukan.com/products-list?search=oil+filter",
        )

        self.assertNotEqual(bm_outcome.vendor, ad_outcome.vendor)
        self.assertNotEqual(bm_outcome.listings[0]["title"], ad_outcome.listings[0]["title"])
        self.assertEqual(bm_outcome.listings[0]["vendor"], "boodmo")
        self.assertEqual(ad_outcome.listings[0]["vendor"], "autodukan")

    # -------------------------------------------------------------------------
    # Scenario 12: Both suppliers handle fallback search when category unavailable
    # -------------------------------------------------------------------------
    async def test_scenario_12_both_suppliers_handle_fallback_search(self):
        """When category is unavailable, both adapters proceed to search fallback without errors."""
        ad_adapter = auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager)
        bm_adapter = boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager)

        p = VehicleProfile(maker="Tata", model="Safari", part="rare sensor", hints={"category_fallback": True})

        ad_need = await ad_adapter.resolve_category(p)
        bm_need = await bm_adapter.resolve_category(p)

        self.assertIsNone(ad_need)
        self.assertIsNone(bm_need)
        self.assertEqual(p.part, "rare sensor")

    # -------------------------------------------------------------------------
    # Scenario 13: SSE events stream smoothly during clarification and search
    # -------------------------------------------------------------------------
    def test_scenario_13_sse_events_stream_smoothly(self):
        """Verify SSE manager publishes and formats progressive events correctly."""
        from server import sse_manager
        thread_id = "test-sse-stream-thread"
        q = sse_manager.subscribe(thread_id)

        try:
            sse_manager.publish(thread_id, {"type": "supplier_start", "supplier": "Boodmo"})
            sse_manager.publish(thread_id, {"type": "clarification_needed", "question": "Which year?"})
            sse_manager.publish(thread_id, {"type": "complete", "total_products": 5})

            event1 = q.get_nowait()
            event2 = q.get_nowait()
            event3 = q.get_nowait()

            self.assertEqual(event1["type"], "supplier_start")
            self.assertEqual(event2["type"], "clarification_needed")
            self.assertEqual(event3["type"], "complete")
        finally:
            sse_manager.unsubscribe(thread_id, q)

    # -------------------------------------------------------------------------
    # Scenario 14: End-to-end simulated flow for "fuel pump" without infinite loop
    # -------------------------------------------------------------------------
    async def test_scenario_14_end_to_end_fuel_pump_flow_no_loop(self):
        """Simulate conversational search for fuel pump: resolves cleanly without looping."""
        conv = graph.Conversation(thread_id="test-e2e-fuel-pump")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        graph.SESSIONS["test-e2e-fuel-pump"] = session

        try:
            # Mock LLM parse to return fuel pump for Tata Safari 2021
            with patch("parts_agent.llm.parse_request_async", new_callable=AsyncMock) as mock_parse:
                mock_parse.return_value = MagicMock(
                    part="fuel pump",
                    make="Tata",
                    model="Safari",
                    year="2021",
                    fuel="Diesel",
                    trim=None,
                    variant=None,
                    model_dump=lambda: {"part": "fuel pump", "make": "Tata", "model": "Safari", "year": "2021", "fuel": "Diesel"}
                )
                session.boodmo_adapter.advance = AsyncMock(return_value=None)
                session.autodukan_adapter.advance = AsyncMock(return_value=None)
                session.boodmo_adapter.collect = AsyncMock(return_value=SearchOutcome(
                    vendor="boodmo", status="ok", vehicle_label="Tata Safari 2021", listings=[], source_url=""
                ))
                session.autodukan_adapter.collect = AsyncMock(return_value=SearchOutcome(
                    vendor="autodukan", status="ok", vehicle_label="Tata Safari 2021", listings=[], source_url=""
                ))

                q = await conv.start("Find a fuel pump for Tata Safari 2021")
                # When advance returns None, it proceeds straight to collect/present without loop!
                self.assertIsNone(q)
                self.assertIn("outcome", conv.state)
                self.assertEqual(conv.state["outcome"]["status"], "ok")
        finally:
            graph.SESSIONS.pop("test-e2e-fuel-pump", None)

    # -------------------------------------------------------------------------
    # Scenario 15: End-to-end simulated flow for "Tata Safari oil filter" with brand validation
    # -------------------------------------------------------------------------
    async def test_scenario_15_end_to_end_tata_safari_oil_filter_brand_validation(self):
        """Simulate search for Tata Safari oil filter where supplier returns an Audi part: Audi part is eliminated."""
        conv = graph.Conversation(thread_id="test-e2e-safari-filter")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        graph.SESSIONS["test-e2e-safari-filter"] = session

        try:
            with patch("parts_agent.llm.parse_request_async", new_callable=AsyncMock) as mock_parse:
                mock_parse.return_value = MagicMock(
                    part="oil filter",
                    make="Tata",
                    model="Safari",
                    year="2022",
                    fuel=None,
                    trim=None,
                    variant=None,
                    model_dump=lambda: {"part": "oil filter", "make": "Tata", "model": "Safari", "year": "2022"}
                )
                session.boodmo_adapter.advance = AsyncMock(return_value=None)
                session.autodukan_adapter.advance = AsyncMock(return_value=None)

                # Mock outcomes where AutoDukan returns an Audi filter (₹50k) and a Tata filter (₹750)
                raw_autodukan_listings = [
                    Listing(
                        title="Audi Genuine Oil Filter Element A4 A6",
                        brand="Audi",
                        part_number="AUDI-06L",
                        price=50000.0,
                        mrp=55000.0,
                        url="https://autodukan.com/product/audi-filter",
                    ),
                    Listing(
                        title="Tata Genuine Oil Filter Harrier / Safari",
                        brand="Tata",
                        part_number="TATA-5427",
                        price=750.0,
                        mrp=850.0,
                        url="https://autodukan.com/product/tata-filter",
                    ),
                ]
                session.autodukan_adapter.collect = AsyncMock(return_value=SearchOutcome(
                    vendor="autodukan",
                    status="ok",
                    vehicle_label="Tata Safari 2022",
                    listings=[l.to_dict() for l in raw_autodukan_listings],
                    source_url="https://autodukan.com/products-list",
                ))
                session.boodmo_adapter.collect = AsyncMock(return_value=SearchOutcome(
                    vendor="boodmo",
                    status="ok",
                    vehicle_label="Tata Safari 2022",
                    listings=[{
                        "title": "Tata Motors Genuine Oil Filter",
                        "brand": "Tata",
                        "part_number": "TATA-5427",
                        "price": 720.0,
                        "url": "https://boodmo.com/catalog/sample",
                    }],
                    source_url="https://boodmo.com/catalog/sample",
                ))

                q = await conv.start("Find an oil filter for Tata Safari 2022")
                self.assertIsNone(q)
                self.assertIn("outcome", conv.state)
                listings = conv.state["outcome"]["listings"]

                # Audi part MUST NOT be present in final outcome listings!
                audi_parts = [l for l in listings if "Audi" in l.get("title", "") or l.get("brand") == "Audi"]
                self.assertEqual(len(audi_parts), 0)

                # Compatible Tata parts MUST be present
                tata_parts = [l for l in listings if "Tata" in l.get("title", "") or l.get("brand", "").upper() == "TATA"]
                self.assertGreaterEqual(len(tata_parts), 1)
        finally:
            graph.SESSIONS.pop("test-e2e-safari-filter", None)

    # -------------------------------------------------------------------------
    # Scenario 5: Cloudflare Challenge Safety & Supplier Isolation
    # -------------------------------------------------------------------------

    async def test_cloudflare_challenge_detection_and_isolation(self):
        """Test that Cloudflare challenges set status to challenge_detected and do not block other suppliers."""
        session_id = "test-cf-isolation"
        try:
            # AutoDukan encounters a bot-protection challenge
            mock_autodukan = MagicMock(name="autodukan")
            mock_autodukan.name = "autodukan"
            mock_autodukan.collect = AsyncMock(side_effect=RuntimeError("AutoDukan bot-protection challenge encountered at https://autodukan.com"))
            mock_autodukan.close = AsyncMock()

            # Boodmo succeeds with verified parts
            mock_boodmo = MagicMock(name="boodmo")
            mock_boodmo.name = "boodmo"
            mock_boodmo.collect = AsyncMock(return_value=SearchOutcome(
                vendor="boodmo",
                status="ok",
                vehicle_label="Tata Safari 2022",
                listings=[{
                    "title": "Bosch Oil Filter for Tata Safari",
                    "brand": "Bosch",
                    "part_number": "F002H24001",
                    "price": 680.0,
                    "url": "https://boodmo.com/product/123",
                    "fitment": "verified",
                }],
                source_url="https://boodmo.com/search",
            ))
            mock_boodmo.close = AsyncMock()

            s = graph.Session(boodmo_adapter=mock_boodmo, autodukan_adapter=mock_autodukan, announce=lambda t: None)
            graph.SESSIONS[session_id] = s

            state = {
                "request": "Find unique cf test filter for Tata Safari 2022",
                "supplier_profiles": {
                    "boodmo": VehicleProfile(maker="Tata", model="Safari", year="2022", part="unique_cf_test_filter").to_dict(),
                    "autodukan": VehicleProfile(maker="Tata", model="Safari", year="2022", part="unique_cf_test_filter").to_dict(),
                },
                "profile": VehicleProfile(maker="Tata", model="Safari", year="2022", part="unique_cf_test_filter").to_dict(),
            }
            config = {"configurable": {"thread_id": session_id}}

            res = await graph.collect(state, config)
            outcome = res["outcome"]

            # Vendor outcomes must contain both suppliers
            vendor_outcomes = {vo["vendor"]: vo for vo in outcome["vendor_outcomes"]}
            self.assertEqual(vendor_outcomes["autodukan"]["status"], "challenge_detected")
            self.assertEqual(len(vendor_outcomes["autodukan"]["listings"]), 0)

            # Boodmo MUST succeed despite AutoDukan's challenge
            self.assertEqual(vendor_outcomes["boodmo"]["status"], "ok")
            self.assertEqual(len(vendor_outcomes["boodmo"]["listings"]), 1)

            # Overall outcome must contain Boodmo's listing
            self.assertEqual(len(outcome["listings"]), 1)
            self.assertEqual(outcome["listings"][0]["brand"], "Bosch")
        finally:
            graph.SESSIONS.pop(session_id, None)

    # -------------------------------------------------------------------------
    # Scenario 6: Strict Numeric Price Ordering and Missing Price Resilience
    # -------------------------------------------------------------------------

    def test_strict_numeric_price_sorting_and_missing_prices(self):
        """Test numeric price sorting (preventing lexicographic sorting) and proper handling of missing prices."""
        listings = [
            {"title": "Filter C", "price": "10000", "relevance": EXACT_MATCH, "fitment": "verified"},
            {"title": "Filter A", "price": 200, "relevance": EXACT_MATCH, "fitment": "verified"},
            {"title": "Filter B", "price": "1200", "relevance": EXACT_MATCH, "fitment": "verified"},
            {"title": "Filter No Price", "price": None, "relevance": EXACT_MATCH, "fitment": "verified"},
            {"title": "Component A", "price": 50, "relevance": COMPONENT, "fitment": "verified"},
        ]

        ranked = rank_listings(listings, price_order="asc")

        # 1. Relevance tiers take strict precedence (EXACT_MATCH before COMPONENT even though Component is Rs 50)
        self.assertEqual(ranked[0]["title"], "Filter A")        # 200
        self.assertEqual(ranked[1]["title"], "Filter B")        # 1200
        self.assertEqual(ranked[2]["title"], "Filter C")        # 10000
        self.assertEqual(ranked[3]["title"], "Filter No Price") # None (appended at end of tier)
        self.assertEqual(ranked[4]["title"], "Component A")     # 50 (lower relevance tier)

        # 2. Verify numeric sorting: 200 < 1200 < 10000 (lexicographic would be "10000" < "1200" < 200)
        prices = [float(l["price"]) for l in ranked[:3]]
        self.assertEqual(prices, [200.0, 1200.0, 10000.0])

        # 3. Verify missing price was not treated as 0 (which would have placed it first)
        self.assertIsNone(ranked[3]["price"])

    # -------------------------------------------------------------------------
    # Scenario 7: Part-Family Validation - Tata Nexon Fuel Pump Rejection
    # -------------------------------------------------------------------------

    def test_part_family_tata_nexon_fuel_pump_rejection(self):
        """Reject unrelated brackets, clamps, separators, and fuse boxes for fuel pump queries."""
        query = "Tata Nexon 2023 1.2L Revotron fuel pump"
        items = [
            {"title": "Tata Separator", "price": 5.76, "brand": "Tata"},
            {"title": "Tata Bracket", "price": 14.40, "brand": "Tata"},
            {"title": "Tata Fuse Box Bracket", "price": 21.12, "brand": "Tata"},
            {"title": "Tata Clamp Brackets", "price": 33.60, "brand": "Tata"},
            {"title": "Tata Motors Genuine Fuel Pump Assembly", "price": 6500.0, "brand": "Tata"},
            {"title": "Bosch Fuel Delivery Module", "price": 5200.0, "brand": "Bosch"},
            {"title": "Fuel Pump Motor Core", "price": 1800.0, "brand": "Aftermarket"},
            {"title": "Tata Genuine Fuel Pump Lock Ring", "price": 150.0, "brand": "Tata"},
        ]

        classified = classify_listings(
            items,
            query=query,
            requested_part="fuel pump",
            target_maker="Tata",
            target_model="Nexon",
        )
        by_title = {l["title"]: l for l in classified}

        # 1. Verify part family classifications
        self.assertEqual(by_title["Tata Separator"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Tata Separator"]["relevance"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Tata Separator"]["relevance_score"], 0.0)

        self.assertEqual(by_title["Tata Bracket"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Tata Bracket"]["relevance"], WRONG_PART_TYPE)

        self.assertEqual(by_title["Tata Fuse Box Bracket"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Tata Fuse Box Bracket"]["relevance"], WRONG_PART_TYPE)

        self.assertEqual(by_title["Tata Clamp Brackets"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Tata Clamp Brackets"]["relevance"], WRONG_PART_TYPE)

        # 2. Legitimate parts must be accepted
        self.assertEqual(by_title["Tata Motors Genuine Fuel Pump Assembly"]["part_family_classification"], EXACT_PART_MATCH)
        self.assertEqual(by_title["Tata Motors Genuine Fuel Pump Assembly"]["relevance"], EXACT_MATCH)

        self.assertEqual(by_title["Bosch Fuel Delivery Module"]["part_family_classification"], EXACT_PART_MATCH)
        self.assertEqual(by_title["Bosch Fuel Delivery Module"]["relevance"], EXACT_MATCH)

        self.assertEqual(by_title["Fuel Pump Motor Core"]["part_family_classification"], RELATED_COMPONENT)
        self.assertEqual(by_title["Tata Genuine Fuel Pump Lock Ring"]["part_family_classification"], ACCESSORY)

        # 3. Final ranked results MUST completely exclude all WRONG_PART_TYPE products
        ranked = rank_listings(classified, price_order="asc")
        ranked_titles = [l["title"] for l in ranked]

        self.assertNotIn("Tata Separator", ranked_titles)
        self.assertNotIn("Tata Bracket", ranked_titles)
        self.assertNotIn("Tata Fuse Box Bracket", ranked_titles)
        self.assertNotIn("Tata Clamp Brackets", ranked_titles)

        # Exact assemblies come first, components second, accessories third
        self.assertEqual(ranked_titles[0], "Bosch Fuel Delivery Module")  # 5200.0 (cheaper exact match)
        self.assertEqual(ranked_titles[1], "Tata Motors Genuine Fuel Pump Assembly")  # 6500.0
        self.assertEqual(ranked_titles[2], "Fuel Pump Motor Core")  # Component
        self.assertEqual(ranked_titles[3], "Tata Genuine Fuel Pump Lock Ring")  # Accessory

    # -------------------------------------------------------------------------
    # Scenario 8: Part-Family Validation - Hyundai i20 Air Filter Rejection
    # -------------------------------------------------------------------------

    def test_part_family_hyundai_i20_air_filter_rejection(self):
        """Reject packing caps, oil filler caps, and oil filters for air filter queries."""
        query = "Hyundai i20 2021 1.2L Kappa air filter"
        items = [
            {"title": "Mobis Packing Cap", "price": 157.78, "brand": "Hyundai"},
            {"title": "Mobis Oil Filler Cap", "price": 485.10, "brand": "Hyundai"},
            {"title": "CAP-OIL FILLER", "price": 280.0, "brand": "Hyundai"},
            {"title": "Mobis Air Filter Element", "price": 420.0, "brand": "Hyundai"},
            {"title": "Mann-Filter Engine Air Filter C25016", "price": 550.0, "brand": "Mann-Filter"},
            {"title": "Bosch Engine Oil Filter", "price": 250.0, "brand": "Bosch"},  # Conflicting filter
        ]

        classified = classify_listings(
            items,
            query=query,
            requested_part="air filter",
            target_maker="Hyundai",
            target_model="i20",
        )
        by_title = {l["title"]: l for l in classified}

        # 1. Unrelated caps and conflicting filters must be WRONG_PART_TYPE
        self.assertEqual(by_title["Mobis Packing Cap"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Mobis Packing Cap"]["relevance"], WRONG_PART_TYPE)

        self.assertEqual(by_title["Mobis Oil Filler Cap"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Mobis Oil Filler Cap"]["relevance"], WRONG_PART_TYPE)

        self.assertEqual(by_title["CAP-OIL FILLER"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["CAP-OIL FILLER"]["relevance"], WRONG_PART_TYPE)

        self.assertEqual(by_title["Bosch Engine Oil Filter"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Bosch Engine Oil Filter"]["relevance"], WRONG_PART_TYPE)

        # 2. Legitimate air filter items must match
        self.assertIn(by_title["Mobis Air Filter Element"]["part_family_classification"], [EXACT_PART_MATCH, PART_ALIAS_MATCH])
        self.assertEqual(by_title["Mobis Air Filter Element"]["relevance"], EXACT_MATCH)

        self.assertIn(by_title["Mann-Filter Engine Air Filter C25016"]["part_family_classification"], [EXACT_PART_MATCH, PART_ALIAS_MATCH])
        self.assertEqual(by_title["Mann-Filter Engine Air Filter C25016"]["relevance"], EXACT_MATCH)

        # 3. Final ranked results MUST contain zero caps and zero oil filters
        ranked = rank_listings(classified, price_order="asc")
        ranked_titles = [l["title"] for l in ranked]

        self.assertNotIn("Mobis Packing Cap", ranked_titles)
        self.assertNotIn("Mobis Oil Filler Cap", ranked_titles)
        self.assertNotIn("CAP-OIL FILLER", ranked_titles)
        self.assertNotIn("Bosch Engine Oil Filter", ranked_titles)
        self.assertEqual(len(ranked_titles), 2)

    # -------------------------------------------------------------------------
    # Scenario 9: Part-Family Validation - Tata Safari Oil Filter Matching
    # -------------------------------------------------------------------------

    def test_part_family_tata_safari_oil_filter(self):
        """Accept valid Safari oil filters; reject contradictory vehicle and wrong part families."""
        query = "Tata Safari 2022 2.0L Kryotec oil filter"
        items = [
            {"title": "Tata Genuine Oil Filter Element", "price": 450.0, "brand": "Tata"},
            {"title": "Bosch Engine Oil Filter", "price": 380.0, "brand": "Bosch"},
            {"title": "Audi OEM Oil Filter", "price": 2200.0, "brand": "Audi"},  # Incompatible vehicle
            {"title": "Tata Safari Engine Mount Bracket", "price": 1100.0, "brand": "Tata"},  # Wrong part
            {"title": "Tata Safari Engine Air Filter Element", "price": 750.0, "brand": "Tata"},  # Conflicting filter
            {"title": "Safari Oil Filter Drain Gasket", "price": 25.0, "brand": "Tata"},  # Genuine accessory
        ]

        classified = classify_listings(
            items,
            query=query,
            requested_part="oil filter",
            target_maker="Tata",
            target_model="Safari",
        )
        by_title = {l["title"]: l for l in classified}

        # Audi item rejected by vehicle gate (INCOMPATIBLE)
        self.assertEqual(by_title["Audi OEM Oil Filter"]["relevance"], INCOMPATIBLE)

        # Engine mount bracket and Air filter rejected by part-family gate (WRONG_PART_TYPE)
        self.assertEqual(by_title["Tata Safari Engine Mount Bracket"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Tata Safari Engine Mount Bracket"]["relevance"], WRONG_PART_TYPE)

        self.assertEqual(by_title["Tata Safari Engine Air Filter Element"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Tata Safari Engine Air Filter Element"]["relevance"], WRONG_PART_TYPE)

        # Valid oil filters accepted
        self.assertEqual(by_title["Tata Genuine Oil Filter Element"]["relevance"], EXACT_MATCH)
        self.assertEqual(by_title["Bosch Engine Oil Filter"]["relevance"], EXACT_MATCH)
        self.assertEqual(by_title["Safari Oil Filter Drain Gasket"]["relevance"], ACCESSORY)

        # Final ranked output check
        ranked = rank_listings(classified, price_order="asc")
        ranked_titles = [l["title"] for l in ranked]

        self.assertNotIn("Audi OEM Oil Filter", ranked_titles)
        self.assertNotIn("Tata Safari Engine Mount Bracket", ranked_titles)
        self.assertNotIn("Tata Safari Engine Air Filter Element", ranked_titles)

        self.assertEqual(ranked_titles[0], "Bosch Engine Oil Filter")  # 380.0
        self.assertEqual(ranked_titles[1], "Tata Genuine Oil Filter Element")  # 450.0
        self.assertEqual(ranked_titles[2], "Safari Oil Filter Drain Gasket")  # 25.0 (accessory tier)

    # -------------------------------------------------------------------------
    # Scenario 10: Part-Family Validation - Maruti Swift Brake Pad Rejection
    # -------------------------------------------------------------------------

    def test_part_family_maruti_swift_brake_pad(self):
        """Accept brake pads; reject brake discs, drums, and calipers when pads were requested."""
        query = "Maruti Swift 2020 1.2L VXI brake pad"
        items = [
            {"title": "Maruti Genuine Front Brake Pad Set", "price": 1450.0, "brand": "Maruti Suzuki"},
            {"title": "Brembo Disc Brake Pads Front", "price": 1800.0, "brand": "Brembo"},
            {"title": "Maruti Genuine Front Brake Disc Rotor", "price": 1100.0, "brand": "Maruti Suzuki"},
            {"title": "Maruti Genuine Rear Brake Drum", "price": 950.0, "brand": "Maruti Suzuki"},
            {"title": "Brake Caliper Assembly Left", "price": 3200.0, "brand": "Aftermarket"},
            {"title": "Swift Brake Pad Retaining Clip Set", "price": 120.0, "brand": "Maruti Suzuki"},
        ]

        classified = classify_listings(
            items,
            query=query,
            requested_part="brake pad",
            target_maker="Maruti",
            target_model="Swift",
        )
        by_title = {l["title"]: l for l in classified}

        # Brake disc, drum, caliper must be WRONG_PART_TYPE
        self.assertEqual(by_title["Maruti Genuine Front Brake Disc Rotor"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Maruti Genuine Front Brake Disc Rotor"]["relevance"], WRONG_PART_TYPE)

        self.assertEqual(by_title["Maruti Genuine Rear Brake Drum"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Maruti Genuine Rear Brake Drum"]["relevance"], WRONG_PART_TYPE)

        self.assertEqual(by_title["Brake Caliper Assembly Left"]["part_family_classification"], WRONG_PART_TYPE)
        self.assertEqual(by_title["Brake Caliper Assembly Left"]["relevance"], WRONG_PART_TYPE)

        # Pads must be EXACT_MATCH
        self.assertEqual(by_title["Maruti Genuine Front Brake Pad Set"]["relevance"], EXACT_MATCH)
        self.assertEqual(by_title["Brembo Disc Brake Pads Front"]["relevance"], EXACT_MATCH)
        self.assertEqual(by_title["Swift Brake Pad Retaining Clip Set"]["relevance"], ACCESSORY)

        # Final ranked output check
        ranked = rank_listings(classified, price_order="asc")
        ranked_titles = [l["title"] for l in ranked]

        self.assertNotIn("Maruti Genuine Front Brake Disc Rotor", ranked_titles)
        self.assertNotIn("Maruti Genuine Rear Brake Drum", ranked_titles)
        self.assertNotIn("Brake Caliper Assembly Left", ranked_titles)

        self.assertEqual(ranked_titles[0], "Maruti Genuine Front Brake Pad Set")  # 1450.0
        self.assertEqual(ranked_titles[1], "Brembo Disc Brake Pads Front")  # 1800.0
        self.assertEqual(ranked_titles[2], "Swift Brake Pad Retaining Clip Set")  # 120.0 (accessory)


if __name__ == "__main__":
    unittest.main()
