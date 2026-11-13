"""FastAPI backend server for Conversational Automotive Parts Sourcing Dashboard.

Integrates with the existing LangGraph parts_agent orchestration, Boodmo, and
AutoDukan adapters without modifying existing agent logic or data models.
Provides real-time SSE streaming for concurrent supplier progress, logs, and
incremental product listings.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

# Ensure backend directory is in sys.path
_backend_dir = str(Path(__file__).resolve().parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from parts_agent import voice
from parts_agent.graph import (
    Conversation,
    SESSIONS,
    Session,
    close_session,
    open_session,
    save_run,
    _profiles_from_state,
)
from parts_agent.models import Listing, NeedInput, SearchOutcome, VehicleProfile

# Load .env from backend directory or project root
for env_path in [
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
]:
    if env_path.exists():
        load_dotenv(env_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("server")

app = FastAPI(title="Conversational AI Parts Sourcing Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------
# Event Bus for SSE Streams
# -----------------------------------------------------------------

class SSEManager:
    """Manages Server-Sent Events subscribers per conversation thread."""

    def __init__(self):
        self._queues: Dict[str, List[asyncio.Queue]] = {}
        self._history: Dict[str, List[Dict[str, Any]]] = {}

    def subscribe(self, thread_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.setdefault(thread_id, []).append(q)
        return q

    def unsubscribe(self, thread_id: str, q: asyncio.Queue):
        if thread_id in self._queues and q in self._queues[thread_id]:
            self._queues[thread_id].remove(q)
            if not self._queues[thread_id]:
                self._queues.pop(thread_id, None)

    def publish(self, thread_id: str, event_data: Dict[str, Any]):
        event_data.setdefault("timestamp", time.strftime("%H:%M:%S"))
        # Save to history for late subscribers
        self._history.setdefault(thread_id, []).append(event_data)
        if thread_id in self._queues:
            for q in self._queues[thread_id]:
                q.put_nowait(event_data)

    def get_history(self, thread_id: str) -> List[Dict[str, Any]]:
        return list(self._history.get(thread_id, []))

    def clear_history(self, thread_id: str):
        self._history.pop(thread_id, None)

    def clear(self, thread_id: str):
        self._queues.pop(thread_id, None)
        self._history.pop(thread_id, None)


sse_manager = SSEManager()

# Active Conversation Registry
CONVERSATIONS: Dict[str, Conversation] = {}
SESSION_LOCKS: Dict[str, asyncio.Lock] = {}
COLLECT_STARTED: Dict[str, bool] = {}


def get_lock(thread_id: str) -> asyncio.Lock:
    if thread_id not in SESSION_LOCKS:
        SESSION_LOCKS[thread_id] = asyncio.Lock()
    return SESSION_LOCKS[thread_id]


# -----------------------------------------------------------------
# Progress Estimation Helpers
# -----------------------------------------------------------------

def parse_progress_percentage(message: str) -> tuple[str, int]:
    """Map adapter progress message to (status_label, percentage)."""
    m = message.lower()
    if "finding the" in m or "opening the category" in m:
        return "Navigating to category", 20
    if "selecting maker" in m:
        return "Selecting vehicle", 35
    if "selecting model" in m:
        return "Selecting vehicle", 45
    if "selecting year" in m:
        return "Selecting vehicle", 55
    if "variant" in m or "using what you said" in m:
        return "Selecting vehicle", 65
    if "reading" in m and "listings" in m:
        return "Extracting listings", 75
    if "checking sellers" in m or "enriching" in m:
        return "Verifying product details", 88
    if "falling back to search" in m:
        return "Searching products", 40
    return "Searching products", 50


# -----------------------------------------------------------------
# Adapter Instrumentation
# -----------------------------------------------------------------

def instrument_session(thread_id: str, session: Session):
    """Attach SSE event emitters to session adapters without altering logic."""
    for vendor_name, adapter in session.adapters.items():
        supplier_title = "Boodmo" if vendor_name == "boodmo" else "AutoDukan"

        # 1. Custom progress callback
        def make_progress_handler(v_name: str, s_title: str):
            def handler(msg: str):
                status_label, pct = parse_progress_percentage(msg)
                sse_manager.publish(
                    thread_id,
                    {
                        "type": "adapter_progress",
                        "supplier": s_title,
                        "vendor": v_name,
                        "status": status_label,
                        "progress": pct,
                        "message": msg,
                    },
                )
                sse_manager.publish(
                    thread_id,
                    {
                        "type": "log_event",
                        "message": f"[{s_title}] {msg}",
                        "level": "info",
                    },
                )

            return handler

        adapter.progress = make_progress_handler(vendor_name, supplier_title)

        # 2. Wrap collect to emit adapter_completed immediately upon return!
        orig_collect = adapter.collect

        def make_collect_wrapper(orig_fn, v_name: str, s_title: str):
            async def wrapped_collect(vendor_profile: VehicleProfile):
                t0 = time.time()
                sse_manager.publish(
                    thread_id,
                    {
                        "type": "adapter_progress",
                        "supplier": s_title,
                        "vendor": v_name,
                        "status": "Searching products",
                        "progress": 30,
                        "message": f"Opening {s_title} search...",
                    },
                )
                sse_manager.publish(
                    thread_id,
                    {
                        "type": "log_event",
                        "message": f"Starting product search on {s_title} for {vendor_profile.part}",
                        "level": "info",
                    },
                )

                try:
                    outcome: SearchOutcome = await orig_fn(vendor_profile)
                    elapsed = round(time.time() - t0, 1)

                    listings_dict = []
                    for l in outcome.listings:
                        item = l if isinstance(l, dict) else l.to_dict()
                        item["vendor"] = v_name
                        item["supplier_name"] = s_title
                        listings_dict.append(item)

                    # Classify and intelligently rank listings before publishing to UI
                    from parts_agent import relevance
                    query_text = vendor_profile.hints.get("raw_request") or vendor_profile.part or ""
                    listings_dict = relevance.classify_listings(
                        listings_dict,
                        query=query_text,
                        requested_part=vendor_profile.part or "",
                        category=vendor_profile.category or ""
                    )
                    listings_dict = relevance.rank_listings(listings_dict, price_order="asc")

                    if outcome.status in ("failed", "extraction_failed"):
                        err_msg = outcome.message or f"{s_title} search failed."
                        sse_manager.publish(
                            thread_id,
                            {
                                "type": "adapter_failed",
                                "supplier": s_title,
                                "vendor": v_name,
                                "status": "Failed",
                                "error": err_msg,
                                "elapsed_seconds": elapsed,
                            },
                        )
                        sse_manager.publish(
                            thread_id,
                            {
                                "type": "log_event",
                                "message": f"{s_title} search failed: {err_msg}",
                                "level": "error",
                            },
                        )
                    else:
                        sse_manager.publish(
                            thread_id,
                            {
                                "type": "adapter_completed",
                                "supplier": s_title,
                                "vendor": v_name,
                                "status": "Completed",
                                "products_found": len(listings_dict),
                                "results": listings_dict,
                                "source_url": outcome.source_url,
                                "message": outcome.message,
                                "elapsed_seconds": elapsed,
                            },
                        )
                        sse_manager.publish(
                            thread_id,
                            {
                                "type": "log_event",
                                "message": f"{s_title} extracted {len(listings_dict)} listings ({elapsed}s)",
                                "level": "success",
                            },
                        )
                    return outcome

                except Exception as exc:
                    elapsed = round(time.time() - t0, 1)
                    log.exception("Error in %s wrapped collect", s_title)
                    sse_manager.publish(
                        thread_id,
                        {
                            "type": "adapter_failed",
                            "supplier": s_title,
                            "vendor": v_name,
                            "status": "Failed",
                            "error": str(exc),
                            "elapsed_seconds": elapsed,
                        },
                    )
                    sse_manager.publish(
                        thread_id,
                        {
                            "type": "log_event",
                            "message": f"Exception on {s_title}: {exc}",
                            "level": "error",
                        },
                    )
                    raise

            return wrapped_collect

        adapter.collect = make_collect_wrapper(orig_collect, vendor_name, supplier_title)


# -----------------------------------------------------------------
# API Models
# -----------------------------------------------------------------

class StartRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None
    headless: bool = True


class ReplyRequest(BaseModel):
    thread_id: str
    answer: str


# -----------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "suppliers": ["boodmo", "autodukan"],
    }


@app.get("/api/suppliers")
async def get_suppliers():
    return [
        {
            "id": "boodmo",
            "name": "Boodmo",
            "description": "India's largest online marketplace for car spare parts with fitment verification",
            "capabilities": ["Fitment verification", "Seller offer enrichment", "Stock checking"],
        },
        {
            "id": "autodukan",
            "name": "AutoDukan",
            "description": "Multi-brand automotive parts retailer & distributor",
            "capabilities": ["Vehicle catalog search", "Direct pricing", "Multi-page extraction"],
        },
    ]


@app.get("/api/session/{thread_id}/events")
async def session_events(thread_id: str, request: Request):
    """Server-Sent Events endpoint streaming real-time logs, progress, and results."""
    queue = sse_manager.subscribe(thread_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        # Send 2KB comment padding to bypass proxy buffering (Cloudflare/Render/Nginx)
        yield f": {' ' * 2048}\n\n"
        # Send initial confirmation event immediately so frontend connection status turns active
        yield f"data: {json.dumps({'type': 'connection_established', 'thread_id': thread_id, 'timestamp': time.strftime('%H:%M:%S')})}\n\n"

        # Send history events first so UI synchronizes if reconnected
        history = sse_manager.get_history(thread_id)
        for h in history:
            yield f"data: {json.dumps(h)}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive ping
                    yield f": ping\n\n"
        finally:
            sse_manager.unsubscribe(thread_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/start")
async def start_chat(payload: StartRequest):
    thread_id = payload.thread_id or str(uuid.uuid4())
    lock = get_lock(thread_id)

    async with lock:
        # Close any previous session on this thread
        await close_session(thread_id)
        # Clear past event history but preserve active subscriber queues for this thread!
        sse_manager.clear_history(thread_id)

        sse_manager.publish(
            thread_id,
            {"type": "log_event", "message": "Search session initiated.", "level": "info"},
        )
        sse_manager.publish(
            thread_id,
            {"type": "log_event", "message": f'Customer: "{payload.message}"', "level": "info"},
        )

        def announce_cb(text: str):
            sse_manager.publish(
                thread_id,
                {"type": "agent_message", "message": text, "role": "agent"},
            )
            sse_manager.publish(
                thread_id,
                {"type": "log_event", "message": f"Agent: {text}", "level": "info"},
            )

        # Initialize session with adapters (force headless on servers without a display)
        use_headless = payload.headless if os.environ.get("DISPLAY") else True
        session = open_session(
            thread_id,
            headless=use_headless,
            announce=announce_cb,
            require_confirmation=True,
        )
        instrument_session(thread_id, session)

        # Notify initial supplier status
        for v_name, title in [("boodmo", "Boodmo"), ("autodukan", "AutoDukan")]:
            sse_manager.publish(
                thread_id,
                {
                    "type": "adapter_progress",
                    "supplier": title,
                    "vendor": v_name,
                    "status": "Waiting",
                    "progress": 0,
                    "message": "Waiting for vehicle specification",
                },
            )

        convo = Conversation(thread_id)
        CONVERSATIONS[thread_id] = convo

        # Run conversation start in background task so SSE connection can begin immediately
        async def run_start():
            try:
                sse_manager.publish(
                    thread_id,
                    {"type": "log_event", "message": "Analyzing request with Gemini...", "level": "info"},
                )
                q = await convo.start(payload.message)
                state = convo.state
                profile_dict = state.get("profile") or {}

                # Send vehicle profile update
                sse_manager.publish(
                    thread_id,
                    {
                        "type": "vehicle_profile_update",
                        "profile": profile_dict,
                        "supplier_profiles": state.get("supplier_profiles") or {},
                    },
                )

                if q:
                    # An adapter needs information
                    sse_manager.publish(
                        thread_id,
                        {
                            "type": "need_input",
                            "pending": q,
                            "profile": profile_dict,
                        },
                    )
                else:
                    # No questions needed; collection and presentation finished!
                    save_run(thread_id, state)
                    outcome = state.get("outcome") or {}
                    summary = state.get("summary") or "Search completed."
                    sse_manager.publish(
                        thread_id,
                        {
                            "type": "search_summary",
                            "summary": summary,
                            "outcome": outcome,
                            "profile": profile_dict,
                        },
                    )
            except Exception as e:
                log.exception("Error in convo.start")
                sse_manager.publish(
                    thread_id,
                    {"type": "log_event", "message": f"Error: {e}", "level": "error"},
                )
                sse_manager.publish(
                    thread_id,
                    {"type": "error", "error": str(e)},
                )

        asyncio.create_task(run_start())

    return {
        "status": "ok",
        "thread_id": thread_id,
        "message": "Session started",
    }


@app.post("/api/chat/reply")
async def reply_chat(payload: ReplyRequest):
    thread_id = payload.thread_id
    if thread_id not in CONVERSATIONS:
        raise HTTPException(status_code=404, detail="Conversation session not found")

    lock = get_lock(thread_id)
    async with lock:
        convo = CONVERSATIONS[thread_id]

        sse_manager.publish(
            thread_id,
            {"type": "log_event", "message": f'Customer reply: "{payload.answer}"', "level": "info"},
        )

        async def run_reply():
            try:
                q = await convo.reply(payload.answer)
                state = convo.state
                profile_dict = state.get("profile") or {}

                sse_manager.publish(
                    thread_id,
                    {
                        "type": "vehicle_profile_update",
                        "profile": profile_dict,
                        "supplier_profiles": state.get("supplier_profiles") or {},
                    },
                )

                if q:
                    sse_manager.publish(
                        thread_id,
                        {
                            "type": "need_input",
                            "pending": q,
                            "profile": profile_dict,
                        },
                    )
                else:
                    # Collection and presentation completed!
                    save_run(thread_id, state)
                    outcome = state.get("outcome") or {}
                    summary = state.get("summary") or "Search completed."
                    sse_manager.publish(
                        thread_id,
                        {
                            "type": "search_summary",
                            "summary": summary,
                            "outcome": outcome,
                            "profile": profile_dict,
                        },
                    )
            except Exception as e:
                log.exception("Error in convo.reply")
                sse_manager.publish(
                    thread_id,
                    {"type": "log_event", "message": f"Error: {e}", "level": "error"},
                )
                sse_manager.publish(
                    thread_id,
                    {"type": "error", "error": str(e)},
                )

        asyncio.create_task(run_reply())

    return {"status": "ok", "thread_id": thread_id}


@app.post("/api/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    context: str = Form(""),
):
    """Transcribe user audio input using Gemini STT via parts_agent.voice."""
    temp_in = None
    temp_wav = None
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty audio file")

        # Save incoming audio to a temporary file
        suffix = Path(file.filename or "audio.webm").suffix or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f_in:
            f_in.write(content)
            temp_in = f_in.name

        # Convert to 16kHz mono WAV using ffmpeg
        temp_wav = temp_in + ".wav"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            temp_in,
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            temp_wav,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if proc.returncode != 0 or not Path(temp_wav).exists():
            # If conversion failed, try reading raw content if it's already wav
            wav_bytes = content
        else:
            wav_bytes = Path(temp_wav).read_bytes()

        # Call existing voice transcription logic
        transcript = voice.transcribe(wav_bytes, context=context)
        return {"text": transcript.strip()}

    except Exception as e:
        log.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
    finally:
        if temp_in and os.path.exists(temp_in):
            try:
                os.remove(temp_in)
            except Exception:
                pass
        if temp_wav and os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except Exception:
                pass


@app.post("/api/reset")
async def reset_session(payload: Dict[str, str]):
    thread_id = payload.get("thread_id")
    if thread_id:
        await close_session(thread_id)
        CONVERSATIONS.pop(thread_id, None)
        sse_manager.clear(thread_id)
        SESSION_LOCKS.pop(thread_id, None)
    return {"status": "ok", "message": "Session reset"}


# -----------------------------------------------------------------
# Static files serving for frontend build (if built)
# -----------------------------------------------------------------
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if not FRONTEND_DIST.exists():
    FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    app_target = "server:app" if Path("server.py").exists() else "backend.server:app"
    uvicorn.run(app_target, host="0.0.0.0", port=8000, reload=True)
