"""Deterministic integration tests for FastAPI endpoints and event streaming."""
import sys
from pathlib import Path
_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from server import app, parse_progress_percentage, sse_manager


class APIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_check(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("boodmo", data["suppliers"])
        self.assertIn("autodukan", data["suppliers"])

    def test_suppliers_endpoint(self):
        resp = self.client.get("/api/suppliers")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        supplier_ids = [s["id"] for s in data]
        self.assertIn("boodmo", supplier_ids)
        self.assertIn("autodukan", supplier_ids)

    def test_progress_percentage_mapping(self):
        status, pct = parse_progress_percentage("Finding the 'oil filter' category on Boodmo")
        self.assertEqual(status, "Navigating to category")
        self.assertEqual(pct, 20)

        status, pct = parse_progress_percentage("Selecting maker Tata")
        self.assertEqual(status, "Selecting vehicle")
        self.assertEqual(pct, 35)

        status, pct = parse_progress_percentage("Selecting model Harrier")
        self.assertEqual(status, "Selecting vehicle")
        self.assertEqual(pct, 45)

        status, pct = parse_progress_percentage("Selecting year 2021")
        self.assertEqual(status, "Selecting vehicle")
        self.assertEqual(pct, 55)

        status, pct = parse_progress_percentage("Reading Oil Filter listings for Tata Harrier")
        self.assertEqual(status, "Extracting listings")
        self.assertEqual(pct, 75)

        status, pct = parse_progress_percentage("Checking sellers and stock on 12 product pages")
        self.assertEqual(status, "Verifying product details")
        self.assertEqual(pct, 88)

    def test_sse_manager_pub_sub(self):
        thread_id = "test-sse-thread"
        q = sse_manager.subscribe(thread_id)
        self.assertIsNotNone(q)

        event = {"type": "log_event", "message": "Test event"}
        sse_manager.publish(thread_id, event)

        history = sse_manager.get_history(thread_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["message"], "Test event")

        received = q.get_nowait()
        self.assertEqual(received["message"], "Test event")
        self.assertIn("timestamp", received)

        sse_manager.unsubscribe(thread_id, q)
        sse_manager.clear(thread_id)
        self.assertEqual(len(sse_manager.get_history(thread_id)), 0)

    @patch("server.Conversation")
    @patch("server.open_session")
    def test_chat_start_and_reset(self, mock_open_session, mock_conversation_cls):
        mock_convo = MagicMock()
        mock_convo.start = AsyncMock(return_value=None)
        mock_convo.state = {"profile": {"maker": "Tata", "part": "oil filter"}}
        mock_conversation_cls.return_value = mock_convo
        mock_open_session.return_value = MagicMock(adapters={})

        thread_id = "test-chat-thread-1"
        resp = self.client.post("/api/chat/start", json={
            "message": "Find an oil filter for a Tata Harrier 2021",
            "thread_id": thread_id,
            "headless": True
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["thread_id"], thread_id)

        # Reset session
        reset_resp = self.client.post("/api/reset", json={"thread_id": thread_id})
        self.assertEqual(reset_resp.status_code, 200)
        self.assertEqual(reset_resp.json()["status"], "ok")

    def test_chat_reply(self):
        thread_id = "test-chat-thread-reply"
        from server import CONVERSATIONS
        mock_convo = MagicMock()
        mock_convo.reply = AsyncMock(return_value=None)
        mock_convo.state = {"profile": {"year": "2021"}, "pending": {"field": "year"}}
        CONVERSATIONS[thread_id] = mock_convo

        try:
            resp = self.client.post("/api/chat/reply", json={
                "thread_id": thread_id,
                "answer": "2021"
            })
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
            mock_convo.reply.assert_called_once_with("2021")
        finally:
            CONVERSATIONS.pop(thread_id, None)

    @patch("server.voice.transcribe")
    @patch("server.subprocess.run")
    def test_transcribe_audio_endpoint(self, mock_subproc, mock_voice_transcribe):
        mock_voice_transcribe.return_value = "oil filter for Harrier"
        mock_subproc.return_value = MagicMock(returncode=0)

        fake_audio = b"FAKE_AUDIO_DATA_FOR_TESTING"
        files = {"file": ("test.webm", fake_audio, "audio/webm")}
        resp = self.client.post("/api/transcribe", files=files, data={"context": "Tata Harrier"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["text"], "oil filter for Harrier")
        mock_voice_transcribe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
