"""Comprehensive test suite for Kia Seltos Model Year Range and Trim Clarification.

Scenarios verified:
1. Kia Seltos 2026: Accepts 2026, offers 2026 in year selection, does not truncate to 2023.
2. Kia Seltos without trim: Asks year -> fuel -> transmission -> trim, does not jump straight to search.
3. Kia Seltos with complete details: Detects all details, does not re-ask, moves directly to confirmation/search.
4. Different model years: 2023, 2024, 2025, 2026 all supported and preserved.
5. Supplier catalogue mismatch: Preserves correct year (2026), reports limitations without silent downgrade.
6. Unknown trim: Allows manual entry (e.g. "Anniversary Edition"), preserves in search hints and fitment validation.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from parts_agent import auto_dukan, boodmo, graph, llm, relevance, variants
from parts_agent.models import Listing, NeedInput, SearchOutcome, VehicleProfile


class TestKiaSeltosModelYearAndTrim(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_browser_manager = MagicMock()
        self.mock_page = MagicMock()
        self.mock_page.url = "https://example.com"
        self.mock_browser_manager.get_page = AsyncMock(return_value=self.mock_page)

    # -------------------------------------------------------------------------
    # Test 1: Kia Seltos 2026 year support
    # -------------------------------------------------------------------------
    async def test_01_kia_seltos_year_options_not_truncated_to_2023(self):
        """When year is not specified, agent offers 2019-2026 and does NOT truncate to 2023."""
        conv = graph.Conversation(thread_id="test-seltos-year-options")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        graph.SESSIONS["test-seltos-year-options"] = session

        try:
            q = await conv.start("Find headlights for Kia Seltos")
            self.assertIsNotNone(q)
            self.assertEqual(q.get("field"), "year")
            options = q.get("options", [])
            # Must contain 2024, 2025, 2026
            self.assertIn("2024", options)
            self.assertIn("2025", options)
            self.assertIn("2026", options)
            self.assertNotIn("2027", options)
            # Question asks for model year
            self.assertIn("model year", q.get("question", "").lower())
        finally:
            graph.SESSIONS.pop("test-seltos-year-options", None)

    async def test_01b_kia_seltos_2026_explicit_query_accepted(self):
        """Explicit query 'Find headlights for Kia Seltos 2026' accepts 2026 and does not re-ask year."""
        conv = graph.Conversation(thread_id="test-seltos-2026-explicit")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        graph.SESSIONS["test-seltos-2026-explicit"] = session

        try:
            q = await conv.start("Find headlights for Kia Seltos 2026")
            self.assertIsNotNone(q)
            # Year is already known (2026), so agent must NOT ask for year
            self.assertNotEqual(q.get("field"), "year")
            # Profile has year=2026
            boodmo_p = conv.state["supplier_profiles"]["boodmo"]
            self.assertEqual(boodmo_p.get("year"), "2026")
        finally:
            graph.SESSIONS.pop("test-seltos-2026-explicit", None)

    # -------------------------------------------------------------------------
    # Test 2: Kia Seltos without trim: progressive conversational flow
    # -------------------------------------------------------------------------
    async def test_02_kia_seltos_without_trim_asks_year_fuel_transmission_trim(self):
        """Query without trim progresses through Year -> Fuel -> Transmission -> Trim -> Confirm."""
        conv = graph.Conversation(thread_id="test-seltos-multi-step")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
            require_confirmation=True,
        )
        graph.SESSIONS["test-seltos-multi-step"] = session

        try:
            # Step 1: Start without details
            q1 = await conv.start("Find headlights for Kia Seltos")
            self.assertIsNotNone(q1)
            self.assertEqual(q1.get("field"), "year")
            self.assertIn("2026", q1.get("options", []))

            # Step 2: Answer year
            q2 = await conv.reply("2024")
            self.assertIsNotNone(q2)
            self.assertEqual(q2.get("field"), "variant_attr")
            self.assertEqual(q2.get("attr"), "fuel")

            # Step 3: Answer fuel
            q3 = await conv.reply("Diesel")
            self.assertIsNotNone(q3)
            self.assertEqual(q3.get("field"), "variant_attr")
            self.assertEqual(q3.get("attr"), "transmission")

            # Step 3b: Answer transmission
            q4 = await conv.reply("Automatic")
            self.assertIsNotNone(q4)
            self.assertEqual(q4.get("field"), "trim")
            self.assertIn("GTX+", q4.get("options", []))
            self.assertIn("HTE", q4.get("options", []))
            self.assertIn("X-Line", q4.get("options", []))

            # Step 4: Answer trim
            q5 = await conv.reply("GTX+")
            self.assertIsNotNone(q5)
            self.assertEqual(q5.get("field"), "confirm_search")
            self.assertIn("GTX+", q5.get("question", ""))
            self.assertIn("2024", q5.get("question", ""))
            self.assertIn("Diesel", q5.get("question", ""))
        finally:
            graph.SESSIONS.pop("test-seltos-multi-step", None)

    # -------------------------------------------------------------------------
    # Test 3: Kia Seltos with complete details: does not re-ask
    # -------------------------------------------------------------------------
    async def test_03_kia_seltos_with_complete_details_does_not_reask(self):
        """Query with complete details detects all attributes and proceeds directly to confirmation/search."""
        conv = graph.Conversation(thread_id="test-seltos-complete")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
            require_confirmation=True,
        )
        graph.SESSIONS["test-seltos-complete"] = session

        try:
            q = await conv.start("Find headlights for Kia Seltos 2024 1.5L Diesel AT GTX+")
            self.assertIsNotNone(q)
            # Moves directly to confirmation without asking year, fuel, transmission, or trim
            self.assertEqual(q.get("field"), "confirm_search")
            question = q.get("question", "")
            self.assertIn("Kia", question)
            self.assertIn("Seltos", question)
            self.assertIn("2024", question)
            self.assertIn("GTX+", question)
            self.assertIn("Diesel", question)

            # Confirm and proceed
            session.boodmo_adapter.advance = AsyncMock(return_value=None)
            session.autodukan_adapter.advance = AsyncMock(return_value=None)
            session.boodmo_adapter.collect = AsyncMock(return_value=SearchOutcome(
                vendor="boodmo", status="ok", vehicle_label="Kia Seltos 2024 GTX+", listings=[], source_url=""
            ))
            session.autodukan_adapter.collect = AsyncMock(return_value=SearchOutcome(
                vendor="autodukan", status="ok", vehicle_label="Kia Seltos 2024 GTX+", listings=[], source_url=""
            ))

            final_q = await conv.reply("Confirm & Search")
            self.assertIsNone(final_q)
            self.assertIn("outcome", conv.state)
        finally:
            graph.SESSIONS.pop("test-seltos-complete", None)

    # -------------------------------------------------------------------------
    # Test 4: Different model years (2023, 2024, 2025, 2026)
    # -------------------------------------------------------------------------
    async def test_04_different_model_years_supported(self):
        """Verify that 2023, 2024, 2025, and 2026 are all supported and correctly recorded."""
        for yr in ("2023", "2024", "2025", "2026"):
            with self.subTest(year=yr):
                thread_id = f"test-seltos-year-{yr}"
                conv = graph.Conversation(thread_id=thread_id)
                session = graph.Session(
                    boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
                    autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
                    announce=lambda t: None,
                )
                graph.SESSIONS[thread_id] = session
                try:
                    q = await conv.start(f"Find headlights for Kia Seltos {yr}")
                    self.assertIsNotNone(q)
                    # Year must NOT be re-asked
                    self.assertNotEqual(q.get("field"), "year")
                    self.assertEqual(conv.state["supplier_profiles"]["boodmo"]["year"], yr)
                    self.assertEqual(conv.state["supplier_profiles"]["autodukan"]["year"], yr)
                finally:
                    graph.SESSIONS.pop(thread_id, None)

    # -------------------------------------------------------------------------
    # Test 5: Supplier catalogue mismatch handling
    # -------------------------------------------------------------------------
    async def test_05_supplier_catalogue_mismatch_fallback_without_downgrade(self):
        """If a supplier dropdown lacks 2026, it falls back to direct search and never downgrades year."""
        bm = boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager)
        bm.page = self.mock_page
        bm._ensure_category_page = AsyncMock()
        # Mock dropdown returning only up to 2023
        bm._options = AsyncMock(side_effect=[
            {"options": ["KIA"], "selected": "KIA"},
            {"options": ["SELTOS"], "selected": "SELTOS"},
            {"options": ["2019", "2020", "2021", "2022", "2023"], "selected": None},
        ])
        bm._search_fallback = AsyncMock()

        profile = VehicleProfile(maker="Kia", model="Seltos", year="2026", part="headlight")
        need = await bm.advance(profile)

        # Advance should complete without raising RuntimeError
        self.assertIsNone(need)
        # Year must remain 2026 (never downgraded to 2023)
        self.assertEqual(profile.year, "2026")
        # Search fallback was invoked with 2026
        bm._search_fallback.assert_awaited_once_with(profile)

    # -------------------------------------------------------------------------
    # Test 6: Unknown / custom trim manual entry
    # -------------------------------------------------------------------------
    async def test_06_unknown_trim_manual_entry_preserved(self):
        """User entering an unlisted/custom trim (e.g. 'Anniversary Edition') has it preserved."""
        conv = graph.Conversation(thread_id="test-seltos-custom-trim")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
            require_confirmation=True,
        )
        graph.SESSIONS["test-seltos-custom-trim"] = session

        try:
            # Start and provide year, fuel, transmission
            await conv.start("Find headlights for Kia Seltos 2024")
            await conv.reply("Diesel")
            await conv.reply("Automatic")

            # At trim step, enter a custom trim
            q_confirm = await conv.reply("Anniversary Edition")
            self.assertIsNotNone(q_confirm)
            self.assertEqual(q_confirm.get("field"), "confirm_search")
            self.assertIn("Anniversary Edition", q_confirm.get("question", ""))

            # Hints must preserve Anniversary Edition
            bm_hints = conv.state["supplier_profiles"]["boodmo"]["hints"]
            self.assertEqual(bm_hints.get("trim"), "Anniversary Edition")
            self.assertTrue(bm_hints.get("trim_unverified"))
        finally:
            graph.SESSIONS.pop("test-seltos-custom-trim", None)


if __name__ == "__main__":
    unittest.main()
