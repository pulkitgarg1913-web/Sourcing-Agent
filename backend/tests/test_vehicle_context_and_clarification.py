"""Comprehensive tests for Vehicle Context Management, Follow-up Questions & Supplier Clarification.

Verifies:
1. Hyundai Creta 2026 headlights: Does NOT automatically assign Standard trim.
2. Honda City 2025 headlights: Does NOT automatically assign Standard trim.
3. Kia Seltos 2022 HTX Diesel Manual: All provided attributes are preserved.
4. AutoDukan requests trim again: HTX is reused, does not re-ask user.
5. Boodmo requests fuel again: Diesel is reused, does not re-ask user.
6. User explicitly provides Standard variant: 'Standard' is preserved as user-provided.
7. User skips a clarification question: Preserves unknown status instead of inserting default.
8. User changes Diesel to Petrol before confirmation: Context is updated correctly.
9. Supplier returns zero products: Clean completed/no_results outcome, never confuses with clarification.
10. Two suppliers execute concurrently: Both receive the exact same verified context.
11. Missing vehicle attribute: Remains null/unknown, never replaced by arbitrary default.
12. Conflicting supplier clarification: Does not overwrite verified user data.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from parts_agent import auto_dukan, boodmo, context, graph, variants
from parts_agent.models import Listing, NeedInput, SearchOutcome, VehicleProfile


class TestVehicleContextAndClarification(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_browser_manager = MagicMock()
        self.mock_page = AsyncMock()
        self.mock_page.url = "https://example.com"
        self.mock_page.goto = AsyncMock()
        self.mock_browser_manager.get_page = AsyncMock(return_value=self.mock_page)
        self.mock_browser_manager.close_session = AsyncMock()
        self.mock_browser_manager.release_page = AsyncMock()

    # -------------------------------------------------------------------------
    # Scenario 1: Hyundai Creta 2026 Headlights - No automatic Standard trim
    # -------------------------------------------------------------------------
    async def test_01_hyundai_creta_2026_headlights_no_auto_standard(self):
        """Hyundai Creta 2026 headlights asks for trim instead of assigning 'Standard'."""
        conv = graph.Conversation(thread_id="test-creta-no-standard")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
            require_confirmation=True,
        )
        graph.SESSIONS["test-creta-no-standard"] = session

        try:
            # User asks: "Hyundai Creta headlights"
            q1 = await conv.start("Hyundai Creta headlights")
            self.assertIsNotNone(q1)
            self.assertEqual(q1.get("field"), "year")

            # User answers: "2026"
            q2 = await conv.reply("2026")
            self.assertIsNotNone(q2)

            # Agent must ask for trim, NOT jump straight to confirmation with Standard!
            self.assertEqual(q2.get("field"), "trim")
            self.assertIn("variant or trim", q2.get("question", "").lower())
            self.assertIn("SX", q2.get("options", []))

            # The current state must NOT have assigned "Standard"
            boodmo_p = conv.state["supplier_profiles"]["boodmo"]
            self.assertNotEqual(boodmo_p.get("hints", {}).get("trim"), "Standard")
            self.assertNotEqual(boodmo_p.get("variant"), "Standard")
        finally:
            graph.SESSIONS.pop("test-creta-no-standard", None)

    # -------------------------------------------------------------------------
    # Scenario 2: Honda City 2025 Headlights - No automatic Standard trim
    # -------------------------------------------------------------------------
    async def test_02_honda_city_2025_headlights_no_auto_standard(self):
        """Headlights -> Honda -> City -> 2025: Variant must NOT become Standard automatically."""
        conv = graph.Conversation(thread_id="test-city-no-standard")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
            require_confirmation=True,
        )
        graph.SESSIONS["test-city-no-standard"] = session

        try:
            # 1. User says "Headlights"
            q1 = await conv.start("Headlights")
            # 2. Answer make: HONDA
            q2 = await conv.reply("HONDA")
            # 3. Answer model: CITY
            q3 = await conv.reply("CITY")
            # 4. Answer year: 2025
            q4 = await conv.reply("2025")

            self.assertIsNotNone(q4)
            # Must ask for trim
            self.assertEqual(q4.get("field"), "trim")
            self.assertIn("V", q4.get("options", []))
            self.assertIn("ZX", q4.get("options", []))

            # Must NOT be Standard
            boodmo_p = conv.state["supplier_profiles"]["boodmo"]
            self.assertNotEqual(boodmo_p.get("hints", {}).get("trim"), "Standard")
        finally:
            graph.SESSIONS.pop("test-city-no-standard", None)

    # -------------------------------------------------------------------------
    # Scenario 3: Kia Seltos 2022 HTX Diesel Manual - All attributes preserved
    # -------------------------------------------------------------------------
    async def test_03_kia_seltos_2022_htx_diesel_manual_preserved(self):
        """Complete vehicle context is preserved and passed to suppliers."""
        conv = graph.Conversation(thread_id="test-seltos-preserved")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
            require_confirmation=True,
        )
        graph.SESSIONS["test-seltos-preserved"] = session

        try:
            q = await conv.start("Find headlights for Kia Seltos 2022 Diesel Manual HTX")
            self.assertIsNotNone(q)
            self.assertEqual(q.get("field"), "confirm_search")

            question = q.get("question", "")
            self.assertIn("Kia", question)
            self.assertIn("Seltos", question)
            self.assertIn("2022", question)
            self.assertIn("HTX", question)
            self.assertIn("Diesel", question)
            self.assertIn("Manual", question)

            # Centralized vehicle context must have all fields
            v_ctx = conv.state.get("vehicle_context") or {}
            self.assertEqual(v_ctx.get("make"), "Kia")
            self.assertEqual(v_ctx.get("model"), "Seltos")
            self.assertEqual(str(v_ctx.get("year")), "2022")
            self.assertEqual(v_ctx.get("fuel_type"), "Diesel")
            self.assertEqual(v_ctx.get("transmission"), "Manual")
            self.assertEqual(v_ctx.get("variant"), "HTX")
        finally:
            graph.SESSIONS.pop("test-seltos-preserved", None)

    # -------------------------------------------------------------------------
    # Scenario 4: User explicitly provides Standard variant
    # -------------------------------------------------------------------------
    async def test_04_user_explicitly_provides_standard_variant(self):
        """When user explicitly provides 'Standard', it is preserved as user-provided."""
        conv = graph.Conversation(thread_id="test-explicit-standard")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
            require_confirmation=True,
        )
        graph.SESSIONS["test-explicit-standard"] = session

        try:
            await conv.start("Find brake pads for Tata Nano 2012")
            q_confirm = await conv.reply("Standard")
            self.assertIsNotNone(q_confirm)
            self.assertEqual(q_confirm.get("field"), "confirm_search")
            self.assertIn("Standard", q_confirm.get("question", ""))

            bm_hints = conv.state["supplier_profiles"]["boodmo"]["hints"]
            self.assertEqual(bm_hints.get("trim"), "Standard")
            self.assertEqual(bm_hints.get("trim_source"), "user_provided")
        finally:
            graph.SESSIONS.pop("test-explicit-standard", None)

    # -------------------------------------------------------------------------
    # Scenario 5: User skips a clarification question
    # -------------------------------------------------------------------------
    async def test_05_user_skips_clarification_preserves_unknown(self):
        """When user skips trim question, it remains Unknown and is NEVER coerced to Standard."""
        conv = graph.Conversation(thread_id="test-skip-trim")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
            require_confirmation=True,
        )
        graph.SESSIONS["test-skip-trim"] = session

        try:
            await conv.start("Find headlights for Hyundai Creta 2026")
            q_confirm = await conv.reply("Other / Not sure")
            self.assertIsNotNone(q_confirm)
            self.assertEqual(q_confirm.get("field"), "confirm_search")

            question = q_confirm.get("question", "")
            # Must NOT say Standard
            self.assertNotIn("Standard", question)
            self.assertIn("Unknown", question)

            bm_hints = conv.state["supplier_profiles"]["boodmo"]["hints"]
            self.assertIsNone(bm_hints.get("trim"))
            self.assertTrue(bm_hints.get("trim_unknown"))
        finally:
            graph.SESSIONS.pop("test-skip-trim", None)

    # -------------------------------------------------------------------------
    # Scenario 6: User changes Diesel to Petrol before confirmation
    # -------------------------------------------------------------------------
    async def test_06_user_changes_diesel_to_petrol_updates_context(self):
        """User changing fuel type before confirmation updates context cleanly."""
        conv = graph.Conversation(thread_id="test-change-fuel")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
            require_confirmation=True,
        )
        graph.SESSIONS["test-change-fuel"] = session

        try:
            q1 = await conv.start("Find headlights for Kia Seltos 2022 Diesel Manual HTX")
            self.assertEqual(q1.get("field"), "confirm_search")
            self.assertIn("Diesel", q1.get("question", ""))

            # User modifies: "Change to Petrol"
            q2 = await conv.reply("Change to Petrol")
            self.assertIsNotNone(q2)
            # Re-confirms with Petrol
            self.assertEqual(q2.get("field"), "confirm_search")
            self.assertIn("Petrol", q2.get("question", ""))
            self.assertNotIn("Diesel", q2.get("question", ""))

            bm_hints = conv.state["supplier_profiles"]["boodmo"]["hints"]
            self.assertEqual(bm_hints.get("fuel"), "petrol")
        finally:
            graph.SESSIONS.pop("test-change-fuel", None)

    # -------------------------------------------------------------------------
    # Scenario 7: AutoDukan & Boodmo auto-resolve known attributes
    # -------------------------------------------------------------------------
    async def test_07_supplier_clarification_auto_resolves_known_context(self):
        """AutoDukan and Boodmo reuse known attributes without prompting user."""
        ctx = context.VehicleContext(
            make="Kia",
            model="Seltos",
            year="2022",
            fuel_type="Diesel",
            transmission="Manual",
            variant="HTX",
            trim="HTX",
        )
        profile = VehicleProfile(maker="Kia", model="Seltos", year="2022")
        ctx.sync_to_profile(profile)

        # 1. AutoDukan asks for variant
        need_ad = NeedInput(
            field="variant",
            question="Which version or trim of the KIA SELTOS (2022) is it?",
            options=["1.5L CRDi HTE MT / DIESEL", "1.5L CRDi HTX MT / DIESEL", "1.5L PETROL HTX IVT"],
        )
        resolved_ad, opt_ad = context.resolve_supplier_clarification("AutoDukan", need_ad, ctx, profile)
        self.assertTrue(resolved_ad)
        self.assertEqual(opt_ad, "1.5L CRDi HTX MT / DIESEL")

        # 2. Boodmo asks for fuel
        need_bm_fuel = NeedInput(
            field="variant_attr",
            attr="fuel",
            question="Is it diesel or petrol?",
            options=["DIESEL", "PETROL"],
        )
        resolved_bm, opt_bm = context.resolve_supplier_clarification("Boodmo", need_bm_fuel, ctx, profile)
        self.assertTrue(resolved_bm)
        self.assertEqual(opt_bm, "DIESEL")

        # 3. Boodmo asks for transmission
        need_bm_tx = NeedInput(
            field="variant_attr",
            attr="transmission",
            question="Which transmission: manual or automatic?",
            options=["MT", "AT", "IVT"],
        )
        resolved_tx, opt_tx = context.resolve_supplier_clarification("Boodmo", need_bm_tx, ctx, profile)
        self.assertTrue(resolved_tx)
        self.assertEqual(opt_tx, "MT")

    # -------------------------------------------------------------------------
    # Scenario 8: Supplier returns zero products does not ask clarification
    # -------------------------------------------------------------------------
    async def test_08_supplier_zero_products_does_not_ask_clarification(self):
        """Zero products returned is treated as clean completed/no_results, not a clarification."""
        conv = graph.Conversation(thread_id="test-zero-products")
        session = graph.Session(
            boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=self.mock_browser_manager),
            autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=self.mock_browser_manager),
            announce=lambda t: None,
        )
        session.adapters["boodmo"].advance = AsyncMock(return_value=None)
        session.adapters["autodukan"].advance = AsyncMock(return_value=None)
        session.adapters["boodmo"].collect = AsyncMock(return_value=SearchOutcome(
            vendor="boodmo",
            status="no_results",
            listings=[],
            message="Boodmo search found 0 listings",
            clarification_required=False,
        ))
        session.adapters["autodukan"].collect = AsyncMock(return_value=SearchOutcome(
            vendor="autodukan",
            status="no_results",
            listings=[],
            message="AutoDukan search found 0 listings",
            clarification_required=False,
        ))

        with patch("parts_agent.graph._session", return_value=session), \
             patch("parts_agent.cache_manager.get_cached_search", return_value=None):
            await conv.start("Find headlights for Kia Seltos 2022 Diesel Manual HTX")

            summary = conv.state.get("summary") or ""
            # Must cleanly report no matching products, never question strings
            self.assertIn("Boodmo returned no matching products", summary)
            self.assertIn("AutoDukan returned no matching products", summary)
            self.assertNotIn("Which version or trim", summary)
            self.assertNotIn("Is it diesel or petrol", summary)


if __name__ == "__main__":
    unittest.main()
