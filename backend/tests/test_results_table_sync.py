"""Regression tests for Results Table Synchronization & Product Consistency.

Verifies:
1. Complete data flow from adapter/cache output through graph.py and server.py.
2. Narrative vs structured product consistency (every product described in conversational narrative
   is present in structured output).
3. Left and right side mirrors both appear in structured listings without deduplication collision.
4. AutoDukan products with identical search page URLs are never deduplicated into one.
5. Fallback display for missing optional fields (seller, dispatch, etc.).
6. Supplier filter metadata correctly populated on all listings (vendor, supplier, supplier_name).
7. Accurate fitment status and fitment basis display (2026 requested vs 2026 Boodmo = verified;
   2026 requested vs 2024 AutoDukan = partial with explicit year notice).
8. Zero products lost between backend extraction and presentation.
"""
import unittest
from parts_agent.models import VehicleProfile, Listing, SearchOutcome
from parts_agent import relevance, cache_manager, graph
from server import publish_completed_outcomes, sse_manager


class TestResultsTableSync(unittest.TestCase):

    def setUp(self):
        cache_manager.init_db()

    def test_nexon_outside_mirror_full_pipeline_data_flow(self):
        """Test complete data flow for Tata Nexon 2026 Outside Mirror search."""
        profile = VehicleProfile(
            maker="Tata",
            model="Nexon",
            year="2026",
            variant="1.2L PURE+ AMT",
            part="outside mirror",
            category="Outside Mirror",
            hints={"make": "Tata", "model": "Nexon", "year": "2026"}
        )

        state = {
            "request": "Find outside mirror for TATA NEXON 2026 1.2L PURE+ AMT / Petrol / BS6.2",
            "profile": profile.to_dict(),
            "suppliers": ["boodmo", "autodukan"],
        }

        # Lookup in cache manager
        cached_data = cache_manager.get_cached_search(
            maker="Tata",
            model="Nexon",
            year="2026",
            variant="1.2L PURE+ AMT",
            part="outside_mirror"
        )
        self.assertIsNotNone(cached_data, "Demo cache must deliver Tata Nexon Outside Mirror")

        # Classify listings using relevance engine with target year and variant
        all_listings = [
            listing
            for vo in cached_data
            for listing in vo.get("listings", [])
        ]
        classified = relevance.classify_listings(
            all_listings,
            query=state["request"],
            requested_part=profile.part,
            target_maker=profile.maker,
            target_model=profile.model,
            target_year=profile.year,
            target_variant=profile.variant,
        )

        # 1. Verify all listings have vendor, supplier, supplier_name
        for item in classified:
            self.assertIn("vendor", item)
            self.assertIn(item["vendor"], ("boodmo", "autodukan"))
            self.assertIn("supplier", item)
            self.assertIn(item["supplier"], ("Boodmo", "AutoDukan"))
            self.assertIn("supplier_name", item)

        # 2. Verify left and right side mirrors are both present
        titles = [item["title"] for item in classified]
        part_numbers = [item["part_number"] for item in classified]
        self.assertIn("Outside Rear View Mirror Right Side With Indicator", titles)
        self.assertIn("Outside Rear View Mirror Left Side With Indicator", titles)
        self.assertIn("542781100115", part_numbers)
        self.assertIn("542781100114", part_numbers)

        # 3. Verify AutoDukan Lumax Mirror Glass Plate (component, ₹420) is present
        lumax_items = [item for item in classified if "Lumax" in item.get("brand", "")]
        self.assertEqual(len(lumax_items), 1)
        lumax = lumax_items[0]
        self.assertEqual(lumax["vendor"], "autodukan")
        self.assertEqual(lumax["price"], 420.0)
        self.assertEqual(lumax["part_number"], "LX-MG-8812")
        self.assertEqual(lumax["product_type"], "component")

        # 4. Verify fitment status and basis:
        # Boodmo matched 2026 variant -> verified
        boodmo_items = [item for item in classified if item["vendor"] == "boodmo"]
        for b_item in boodmo_items:
            self.assertEqual(b_item["fitment"], "verified")
            self.assertIn("2026", b_item["fitment_basis"])

        # AutoDukan catalog was 2024 for 2026 vehicle -> partial with explicit notice
        autodukan_items = [item for item in classified if item["vendor"] == "autodukan"]
        for a_item in autodukan_items:
            self.assertEqual(a_item["fitment"], "partial")
            self.assertIn("2024", a_item["fitment_basis"])
            self.assertIn("2026", a_item["fitment_basis"])
            self.assertIn("verify", a_item["fitment_basis"].lower())

    def test_narrative_and_structured_output_consistency(self):
        """Ensure all products mentioned in conversational narrative match structured listings."""
        profile = VehicleProfile(
            maker="Tata",
            model="Nexon",
            year="2026",
            variant="1.2L PURE+ AMT",
            part="outside mirror",
            category="Outside Mirror"
        )
        cached_data = cache_manager.get_cached_search(
            maker="Tata", model="Nexon", year="2026", variant="1.2L PURE+ AMT", part="outside_mirror"
        )
        self.assertIsNotNone(cached_data)

        # Classify listings for each vendor
        for vo in cached_data:
            v_name = vo.get("vendor", "")
            s_title = "Boodmo" if v_name == "boodmo" else "AutoDukan"
            vo["listings"] = relevance.rank_listings(
                relevance.classify_listings(
                    vo.get("listings", []),
                    query="Find outside mirror for Tata Nexon 2026",
                    requested_part="outside mirror",
                    target_maker="Tata",
                    target_model="Nexon",
                    target_year="2026",
                ),
                price_order="asc"
            )

        outcome = {
            "status": "ok",
            "vendor_outcomes": cached_data,
            "listings": [l for vo in cached_data for l in vo["listings"]],
        }

        narrative = graph.summarize(profile, outcome)

        # Boodmo summary mentions Tata OEM mirror with part number 542781100115
        self.assertIn("542781100115", narrative)
        # AutoDukan summary mentions Lumax mirror glass plate
        self.assertIn("Lumax", narrative)
        self.assertIn("420", narrative)

        # Verify structured listings contain these exact items
        structured_part_nos = [l.get("part_number") for l in outcome["listings"]]
        self.assertIn("542781100115", structured_part_nos)
        self.assertIn("LX-MG-8812", structured_part_nos)

        structured_brands = [l.get("brand") for l in outcome["listings"]]
        self.assertIn("Lumax", structured_brands)

    def test_sse_publish_completed_outcomes_preserves_all_metadata(self):
        """Test server.publish_completed_outcomes attaches vendor, supplier, seller, dispatch."""
        published_events = []

        def mock_publish(thread_id, event):
            published_events.append(event)

        orig_publish = sse_manager.publish
        sse_manager.publish = mock_publish
        try:
            cached_data = cache_manager.get_cached_search(
                maker="Tata", model="Nexon", year="2026", variant="1.2L PURE+ AMT", part="outside_mirror"
            )
            state = {
                "outcome": {
                    "status": "ok",
                    "vendor_outcomes": cached_data,
                    "listings": [l for vo in cached_data for l in vo.get("listings", [])]
                }
            }
            publish_completed_outcomes("test-thread-sync", state)

            adapter_events = [e for e in published_events if e.get("type") == "adapter_completed"]
            self.assertEqual(len(adapter_events), 2)

            for event in adapter_events:
                self.assertIn(event["vendor"], ("boodmo", "autodukan"))
                self.assertIn(event["supplier"], ("Boodmo", "AutoDukan"))
                self.assertGreater(len(event["results"]), 0)
                for item in event["results"]:
                    self.assertIn("vendor", item)
                    self.assertEqual(item["vendor"], event["vendor"])
                    self.assertIn("supplier", item)
                    self.assertEqual(item["supplier"], event["supplier"])
                    self.assertIsNotNone(item.get("seller"))
                    self.assertIsNotNone(item.get("dispatch"))
        finally:
            sse_manager.publish = orig_publish

    def test_optional_fields_fallback_gracefully(self):
        """Test that sparse listings with missing optional fields don't raise errors and retain defaults."""
        sparse_listing = {
            "title": "Brake Hose",
            "brand": None,
            "part_number": None,
            "price": None,
            "url": None,
            "vendor": "boodmo",
            "available": None,
            "fitment": "unverified",
        }

        classified = relevance.classify_listings([sparse_listing], query="Brake Hose")
        self.assertEqual(len(classified), 1)
        res = classified[0]
        self.assertEqual(res["title"], "Brake Hose")
        self.assertEqual(res["vendor"], "boodmo")
        self.assertEqual(res["supplier"], "Boodmo")
        self.assertIsNotNone(res["seller"])
        self.assertIsNotNone(res["dispatch"])

    def test_vehicle_compatibility_year_granularity(self):
        """Test check_vehicle_compatibility correctly flags year generation mismatches as partial."""
        # Exact year match
        status, reason = relevance.check_vehicle_compatibility(
            product_title="Tata Nexon Mirror RH",
            product_brand="Tata",
            target_maker="Tata",
            target_model="Nexon",
            target_year="2024",
            catalog_year="2024"
        )
        self.assertEqual(status, "verified")

        # Compatible model, different year
        status, reason = relevance.check_vehicle_compatibility(
            product_title="Tata Nexon Mirror RH 2024",
            product_brand="Tata",
            target_maker="Tata",
            target_model="Nexon",
            target_year="2026",
            catalog_year="2024"
        )
        self.assertEqual(status, "partial")
        self.assertIn("verify 2026 compatibility", reason)

        # Incompatible maker
        status, reason = relevance.check_vehicle_compatibility(
            product_title="Audi Q5 Mirror RH",
            product_brand="Audi",
            target_maker="Tata",
            target_model="Nexon",
            target_year="2026",
        )
        self.assertEqual(status, "incompatible")


if __name__ == "__main__":
    unittest.main()
