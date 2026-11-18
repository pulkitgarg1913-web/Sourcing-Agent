"""Unit and Integration Tests for Strict Automotive-Only Intent Validation.

Covers:
1. Python coding question -> OUT_OF_SCOPE
2. General knowledge question -> OUT_OF_SCOPE
3. Weather question -> OUT_OF_SCOPE
4. Laptop search -> OUT_OF_SCOPE
5. Automobile spare-part search -> AUTOMOTIVE_PART_SEARCH
6. Vehicle details supplied during an active session -> AUTOMOTIVE_PART_FOLLOWUP
7. Unrelated question during an active automotive session -> OUT_OF_SCOPE
8. New automotive request after an out-of-scope response -> AUTOMOTIVE_PART_SEARCH
9. Ambiguous request -> Ask for clarification without starting supplier searches
10. Mock supplier adapters and verify they are NEVER called for OUT_OF_SCOPE requests
11. Server endpoints (/api/chat/start and /api/chat/reply) reject out-of-scope requests immediately
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from parts_agent import graph
from parts_agent.intent import (
    DEFAULT_OUT_OF_SCOPE_MESSAGE,
    LAPTOP_OUT_OF_SCOPE_MESSAGE,
    PROGRAMMING_OUT_OF_SCOPE_MESSAGE,
    AMBIGUOUS_CLARIFICATION_MESSAGE,
    IntentCategory,
    classify_intent,
    classify_intent_async,
)
from parts_agent.models import NeedInput, VehicleProfile


class TestStrictIntentValidation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_bm = MagicMock()
        self.mock_page = AsyncMock()
        self.mock_page.url = "https://example.com"
        self.mock_page.is_closed = MagicMock(return_value=False)
        self.mock_bm.get_page = AsyncMock(return_value=self.mock_page)
        self.mock_bm.close_adapter = AsyncMock()
        self.mock_bm.close_session = AsyncMock()

    # -------------------------------------------------------------------------
    # 1. Python coding question -> OUT_OF_SCOPE
    # -------------------------------------------------------------------------
    async def test_01_python_coding_question_out_of_scope(self):
        query = "What is reverse string Python code?"
        result = await classify_intent_async(query)
        self.assertEqual(result.category, IntentCategory.OUT_OF_SCOPE)
        self.assertIn("programming", result.suggested_reply.lower())
        self.assertEqual(result.suggested_reply, PROGRAMMING_OUT_OF_SCOPE_MESSAGE)

    # -------------------------------------------------------------------------
    # 2. General knowledge question -> OUT_OF_SCOPE
    # -------------------------------------------------------------------------
    async def test_02_general_knowledge_question_out_of_scope(self):
        query = "Who was the first president of India?"
        result = await classify_intent_async(query)
        self.assertEqual(result.category, IntentCategory.OUT_OF_SCOPE)
        self.assertIn("automobile spare parts sourcing", result.suggested_reply)

    # -------------------------------------------------------------------------
    # 3. Weather question -> OUT_OF_SCOPE
    # -------------------------------------------------------------------------
    async def test_03_weather_question_out_of_scope(self):
        query = "What is the weather today?"
        result = await classify_intent_async(query)
        self.assertEqual(result.category, IntentCategory.OUT_OF_SCOPE)
        self.assertEqual(result.suggested_reply, DEFAULT_OUT_OF_SCOPE_MESSAGE)

    # -------------------------------------------------------------------------
    # 4. Laptop search -> OUT_OF_SCOPE
    # -------------------------------------------------------------------------
    async def test_04_laptop_search_out_of_scope(self):
        query = "Find me a laptop under ₹50,000."
        result = await classify_intent_async(query)
        self.assertEqual(result.category, IntentCategory.OUT_OF_SCOPE)
        self.assertIn("laptops", result.suggested_reply.lower())
        self.assertEqual(result.suggested_reply, LAPTOP_OUT_OF_SCOPE_MESSAGE)

    # -------------------------------------------------------------------------
    # 5. Automobile spare-part search -> AUTOMOTIVE_PART_SEARCH
    # -------------------------------------------------------------------------
    async def test_05_automobile_spare_part_search(self):
        queries = [
            "Find headlights for Hyundai Creta",
            "Find an air filter for Tata Harrier",
            "Front brake pads for Swift",
            "Clutch plate for Kia Seltos 2022",
        ]
        for q in queries:
            result = await classify_intent_async(q)
            self.assertEqual(
                result.category,
                IntentCategory.AUTOMOTIVE_PART_SEARCH,
                f"Failed for query '{q}': got {result.category}",
            )

    # -------------------------------------------------------------------------
    # 6. Vehicle details supplied during an active session -> AUTOMOTIVE_PART_FOLLOWUP
    # -------------------------------------------------------------------------
    async def test_06_vehicle_details_during_active_session_is_followup(self):
        followups = [
            ("2025", "year"),
            ("Diesel", "fuel"),
            ("Manual", "transmission"),
            ("HTX", "variant"),
            ("2025 Diesel Manual", "variant_attr"),
            ("Tata", "maker"),
            ("Creta", "model"),
            ("yes", "confirm_search"),
        ]
        for answer, field in followups:
            result = await classify_intent_async(
                answer,
                has_active_session=True,
                pending_field=field,
            )
            self.assertEqual(
                result.category,
                IntentCategory.AUTOMOTIVE_PART_FOLLOWUP,
                f"Failed for follow-up '{answer}' on field '{field}': got {result.category}",
            )

    # -------------------------------------------------------------------------
    # 7. Unrelated question during an active automotive session -> OUT_OF_SCOPE
    # -------------------------------------------------------------------------
    async def test_07_unrelated_question_during_active_session_is_out_of_scope(self):
        unrelated_replies = [
            "What is reverse string Python code?",
            "What is the weather today?",
            "Find me a laptop under ₹50,000",
            "Who won the cricket match?",
        ]
        for reply in unrelated_replies:
            result = await classify_intent_async(
                reply,
                has_active_session=True,
                pending_field="year",
            )
            self.assertEqual(
                result.category,
                IntentCategory.OUT_OF_SCOPE,
                f"Failed to reject unrelated reply '{reply}' during active session",
            )

    # -------------------------------------------------------------------------
    # 8. New automotive request after an out-of-scope response -> AUTOMOTIVE_PART_SEARCH
    # -------------------------------------------------------------------------
    async def test_08_new_automotive_request_after_out_of_scope(self):
        # First query: Out of scope
        res1 = await classify_intent_async("What is reverse string Python code?", has_active_session=False)
        self.assertEqual(res1.category, IntentCategory.OUT_OF_SCOPE)

        # Subsequent query: New automotive request
        res2 = await classify_intent_async("Find headlights for Hyundai Creta", has_active_session=False)
        self.assertEqual(res2.category, IntentCategory.AUTOMOTIVE_PART_SEARCH)

    # -------------------------------------------------------------------------
    # 9. Ambiguous request -> Ask for clarification without starting supplier searches
    # -------------------------------------------------------------------------
    async def test_09_ambiguous_request_clarification(self):
        ambiguous_queries = ["Hyundai", "parts", "car"]
        for q in ambiguous_queries:
            result = await classify_intent_async(q, has_active_session=False)
            self.assertEqual(
                result.category,
                IntentCategory.AMBIGUOUS,
                f"Expected AMBIGUOUS for '{q}', got {result.category}",
            )
            self.assertEqual(result.suggested_reply, AMBIGUOUS_CLARIFICATION_MESSAGE)

    # -------------------------------------------------------------------------
    # 10. Mock supplier adapters and verify they are NEVER called for OUT_OF_SCOPE requests
    # -------------------------------------------------------------------------
    async def test_10_supplier_adapters_never_called_for_out_of_scope(self):
        mock_boodmo = AsyncMock()
        mock_autodukan = AsyncMock()

        with patch("parts_agent.graph.BoodmoAdapter", return_value=mock_boodmo), \
             patch("parts_agent.graph.AutoDukanAdapter", return_value=mock_autodukan):

            convo = graph.Conversation(thread_id="test-intent-out-of-scope")

            # Start conversation with a coding question
            next_q = await convo.start("What is reverse string Python code?")

            # 1. Must return None (no vehicle questions asked)
            self.assertIsNone(next_q)

            # 2. State must record out_of_scope
            self.assertTrue(convo.state.get("out_of_scope"))
            self.assertEqual(convo.state.get("intent"), "OUT_OF_SCOPE")
            self.assertIn("programming", convo.state.get("summary", "").lower())

            # 3. Supplier collect() must NEVER be called
            mock_boodmo.collect.assert_not_called()
            mock_autodukan.collect.assert_not_called()
            mock_boodmo.advance.assert_not_called()
            mock_autodukan.advance.assert_not_called()

    # -------------------------------------------------------------------------
    # 11. End-to-End Server API rejection test for OUT_OF_SCOPE
    # -------------------------------------------------------------------------
    async def test_11_server_api_rejects_out_of_scope_without_adapter_init(self):
        from server import start_chat, StartRequest, open_session

        mock_open_session = MagicMock()
        with patch("server.open_session", mock_open_session):
            req = StartRequest(message="What is the weather today?", thread_id="test-srv-oos")
            resp = await start_chat(req)

            # API returns out_of_scope
            self.assertEqual(resp.get("status"), "out_of_scope")
            self.assertIn("automobile spare parts sourcing", resp.get("message"))

            # open_session (browser adapter allocation) MUST NOT be called!
            mock_open_session.assert_not_called()


    # -------------------------------------------------------------------------
    # 12. Non-automotive uses of brand or part words -> OUT_OF_SCOPE
    # -------------------------------------------------------------------------
    async def test_12_brand_or_part_word_alone_does_not_make_it_automotive(self):
        non_auto_brand_queries = [
            "What is the share price of Tata Motors?",
            "Who directed the movie Ford v Ferrari?",
            "Write a python script to parse car parts",
            "Computer spare parts for sale",
            "Airplane engine parts manufacturer",
        ]
        for q in non_auto_brand_queries:
            result = await classify_intent_async(q)
            self.assertEqual(
                result.category,
                IntentCategory.OUT_OF_SCOPE,
                f"Failed to reject query with brand/part keyword: '{q}'",
            )

    # -------------------------------------------------------------------------
    # 13. Scenarios A through E from User Specification
    # -------------------------------------------------------------------------
    async def test_13_scenarios_a_through_e(self):
        # Scenario A: "Find headlights for Hyundai Creta." -> Starts automotive workflow
        res_a = await classify_intent_async("Find headlights for Hyundai Creta.")
        self.assertEqual(res_a.category, IntentCategory.AUTOMOTIVE_PART_SEARCH)

        # Scenario B: "What is reverse string Python code?" -> Must reject even after automotive session
        res_b = await classify_intent_async("What is reverse string Python code?", has_active_session=True)
        self.assertEqual(res_b.category, IntentCategory.OUT_OF_SCOPE)

        # Scenario C: "2025 Diesel Manual." -> Valid follow-up only if active sourcing workflow is waiting
        res_c_active = await classify_intent_async("2025 Diesel Manual", has_active_session=True, pending_field="variant_attr")
        self.assertEqual(res_c_active.category, IntentCategory.AUTOMOTIVE_PART_FOLLOWUP)

        # Standalone "2025 Diesel Manual" without active session is ambiguous
        res_c_standalone = await classify_intent_async("2025 Diesel Manual", has_active_session=False)
        self.assertEqual(res_c_standalone.category, IntentCategory.AMBIGUOUS)

        # Scenario D: "Find a laptop." -> Reject as out of scope
        res_d = await classify_intent_async("Find a laptop.")
        self.assertEqual(res_d.category, IntentCategory.OUT_OF_SCOPE)

        # Scenario E: "Find an air filter for Tata Harrier." -> Start the automotive sourcing workflow
        res_e = await classify_intent_async("Find an air filter for Tata Harrier.")
        self.assertEqual(res_e.category, IntentCategory.AUTOMOTIVE_PART_SEARCH)

    # -------------------------------------------------------------------------
    # 14. Server /api/chat/reply rejects OUT_OF_SCOPE replies cleanly
    # -------------------------------------------------------------------------
    async def test_14_reply_api_rejects_out_of_scope_during_active_session(self):
        from server import reply_chat, ReplyRequest, CONVERSATIONS

        # Setup mock active conversation
        mock_convo = MagicMock()
        mock_convo.state = {"pending": {"field": "year", "question": "Which year?"}}
        CONVERSATIONS["test-active-thread"] = mock_convo

        req = ReplyRequest(thread_id="test-active-thread", answer="What is reverse string Python code?")
        resp = await reply_chat(req)

        self.assertEqual(resp.get("status"), "out_of_scope")
        self.assertIn("programming", resp.get("message").lower())

        # Convo reply MUST NOT have been called with the out-of-scope question
        mock_convo.reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
