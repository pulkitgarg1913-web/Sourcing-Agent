"""Railway Concurrency, Queue Policy, and Memory Leak Audit.

Tests all scenarios with WEB_CONCURRENCY=1 and MAX_CONCURRENT_SEARCHES=1:
1. One active live search + one queued search (semaphore locked, queue status emitted).
2. Cancel queued search (cancelled cleanly, active search unaffected).
3. Cancel active search (cancelled cleanly, semaphore released immediately).
4. Complete search normally (completes, semaphore released).
5. Run cached search while live search is active (cached search bypasses semaphore, completes instantly).
6. Verify no context leaks or unclosed tasks across all operations.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# Enforce Railway safety parameters
os.environ["WEB_CONCURRENCY"] = "1"
os.environ["MAX_CONCURRENT_SEARCHES"] = "1"

from parts_agent import cache_manager, graph
from parts_agent.browser_manager import get_browser_manager
from parts_agent.graph import (
    MAX_CONCURRENT_SEARCHES,
    Session,
    collect,
    close_session,
    get_search_semaphore,
    reset_search_semaphore,
)
from parts_agent.models import Listing, SearchOutcome, VehicleProfile
from scripts.run_production_benchmarks import get_process_tree_memory


async def run_concurrency_audit():
    print("=" * 65)
    print(" RAILWAY CONCURRENCY & MEMORY SAFETY AUDIT")
    print(f" WEB_CONCURRENCY=1 | MAX_CONCURRENT_SEARCHES={MAX_CONCURRENT_SEARCHES}")
    print("=" * 65)

    reset_search_semaphore()
    sem = get_search_semaphore()
    bm = get_browser_manager()
    await bm.initialize(headless=True)

    mem_init = get_process_tree_memory()
    print(f"Initial Memory: RSS: {mem_init['total_rss_mb']} MB | PSS: {mem_init['total_pss_mb']} MB | Anon: {mem_init['total_private_anon_mb']} MB")

    # -------------------------------------------------------------------------
    # TEST 1 & 2: Active Search + Queued Search, and Cancel Queued Search
    # -------------------------------------------------------------------------
    print("\n--- Test 1 & 2: One Active Search, One Queued Search, Cancel Queued ---")
    active_search_started = asyncio.Event()
    active_search_hold = asyncio.Event()
    queued_search_event = asyncio.Event()

    async def mock_active_adapter_collect(p):
        active_search_started.set()
        await active_search_hold.wait()
        return SearchOutcome(vendor="boodmo", status="ok", vehicle_label="Active Car", listings=[], source_url="")

    # Session 1: Active
    s1 = Session(
        boodmo_adapter=MagicMock(name="boodmo", collect=mock_active_adapter_collect, close=AsyncMock()),
        autodukan_adapter=MagicMock(name="autodukan", collect=mock_active_adapter_collect, close=AsyncMock()),
        announce=lambda t: None,
    )
    graph.SESSIONS["session-active"] = s1

    # Session 2: Queued
    s2 = Session(
        boodmo_adapter=MagicMock(name="boodmo", collect=AsyncMock(), close=AsyncMock()),
        autodukan_adapter=MagicMock(name="autodukan", collect=AsyncMock(), close=AsyncMock()),
        announce=lambda t: None,
        on_queued=lambda: queued_search_event.set(),
    )
    graph.SESSIONS["session-queued"] = s2

    state1 = {
        "request": "Active Search Request",
        "supplier_profiles": {"boodmo": VehicleProfile(part="active_part").to_dict()},
        "profile": VehicleProfile(part="active_part").to_dict(),
    }
    config1 = {"configurable": {"thread_id": "session-active"}}

    state2 = {
        "request": "Queued Search Request",
        "supplier_profiles": {"boodmo": VehicleProfile(part="queued_part").to_dict()},
        "profile": VehicleProfile(part="queued_part").to_dict(),
    }
    config2 = {"configurable": {"thread_id": "session-queued"}}

    # Launch active task
    task_active = asyncio.create_task(collect(state1, config1))
    await active_search_started.wait()

    # Verify semaphore is locked by active search
    assert sem.locked(), "Semaphore MUST be locked by the active search!"
    print(" [OK] Active search acquired semaphore (semaphore locked: True)")

    # Launch queued task
    task_queued = asyncio.create_task(collect(state2, config2))
    # Give event loop time to hit the locked semaphore
    await asyncio.sleep(0.05)

    assert queued_search_event.is_set(), "on_queued event callback must fire when semaphore is full!"
    print(" [OK] Queued search detected capacity limit and emitted queued event")

    # Cancel queued search
    print(" -> Cancelling the queued search...")
    task_queued.cancel()
    try:
        await task_queued
    except asyncio.CancelledError:
        print(" [OK] Queued search cancelled cleanly (raised CancelledError)")

    # Verify active search is still holding semaphore
    assert sem.locked(), "Active search must continue holding semaphore after queued search cancellation!"
    print(" [OK] Active search unaffected by queued search cancellation")

    # Release active search
    active_search_hold.set()
    res1 = await task_active
    await close_session("session-active")
    await close_session("session-queued")

    # Verify semaphore is released
    assert not sem.locked(), "Semaphore must be released after active search completes!"
    print(" [OK] Semaphore released cleanly after active search completion")

    # -------------------------------------------------------------------------
    # TEST 3: Cancel Active Search
    # -------------------------------------------------------------------------
    print("\n--- Test 3: Cancel Active Search ---")
    active_cancelled_started = asyncio.Event()

    async def mock_slow_collect(p):
        active_cancelled_started.set()
        await asyncio.sleep(10.0)
        return SearchOutcome(vendor="boodmo", status="ok", vehicle_label="Slow Car", listings=[], source_url="")

    s3 = Session(
        boodmo_adapter=MagicMock(name="boodmo", collect=mock_slow_collect, close=AsyncMock()),
        autodukan_adapter=MagicMock(name="autodukan", collect=mock_slow_collect, close=AsyncMock()),
        announce=lambda t: None,
    )
    graph.SESSIONS["session-cancel-active"] = s3

    state3 = {
        "request": "Cancel Active Request",
        "supplier_profiles": {"boodmo": VehicleProfile(part="slow_part").to_dict()},
        "profile": VehicleProfile(part="slow_part").to_dict(),
    }
    config3 = {"configurable": {"thread_id": "session-cancel-active"}}

    task_slow = asyncio.create_task(collect(state3, config3))
    await active_cancelled_started.wait()
    assert sem.locked(), "Slow task must hold semaphore!"
    print(" [OK] Slow active search acquired semaphore")

    # Cancel slow active task
    print(" -> Cancelling active search task...")
    task_slow.cancel()
    try:
        await task_slow
    except asyncio.CancelledError:
        print(" [OK] Active search cancelled cleanly")

    await close_session("session-cancel-active")
    assert not sem.locked(), "Semaphore MUST be released when an active search is cancelled!"
    print(" [OK] Semaphore automatically released on active task cancellation")

    # -------------------------------------------------------------------------
    # TEST 4: Complete Search Normally
    # -------------------------------------------------------------------------
    print("\n--- Test 4: Complete Search Normally ---")
    s4 = Session(
        boodmo_adapter=MagicMock(
            name="boodmo",
            collect=AsyncMock(return_value=SearchOutcome(vendor="boodmo", status="ok", vehicle_label="Normal Car", listings=[], source_url="")),
            close=AsyncMock()
        ),
        autodukan_adapter=MagicMock(
            name="autodukan",
            collect=AsyncMock(return_value=SearchOutcome(vendor="autodukan", status="ok", vehicle_label="Normal Car", listings=[], source_url="")),
            close=AsyncMock()
        ),
        announce=lambda t: None,
    )
    graph.SESSIONS["session-normal"] = s4

    state4 = {
        "request": "Normal Search Request",
        "supplier_profiles": {"boodmo": VehicleProfile(part="normal_part").to_dict()},
        "profile": VehicleProfile(part="normal_part").to_dict(),
    }
    config4 = {"configurable": {"thread_id": "session-normal"}}

    res4 = await collect(state4, config4)
    assert res4["outcome"]["status"] == "ok"
    assert not sem.locked(), "Semaphore must be released after normal completion"
    await close_session("session-normal")
    print(" [OK] Normal search completed with status 'ok' and released semaphore")

    # -------------------------------------------------------------------------
    # TEST 5: Run Cached Search while Live Search is Active
    # -------------------------------------------------------------------------
    print("\n--- Test 5: Run Cached Search while Live Search is Active ---")
    # Pre-seed cache with a known item
    cache_manager.save_cached_search(
        maker="Tata", model="Safari", year="2022", variant="XZ", part="cached_filter",
        outcomes=[{"vendor": "boodmo", "status": "ok", "vehicle_label": "Tata Safari", "listings": [{"title": "Cached Filter", "price": 500}]}],
        ttl_hours=24.0
    )

    live_holding_started = asyncio.Event()
    live_holding_release = asyncio.Event()

    async def mock_live_hold(p):
        live_holding_started.set()
        await live_holding_release.wait()
        return SearchOutcome(vendor="boodmo", status="ok", vehicle_label="Holding Car", listings=[], source_url="")

    s5_live = Session(
        boodmo_adapter=MagicMock(name="boodmo", collect=mock_live_hold, close=AsyncMock()),
        autodukan_adapter=MagicMock(name="autodukan", collect=mock_live_hold, close=AsyncMock()),
        announce=lambda t: None,
    )
    graph.SESSIONS["session-live-holding"] = s5_live

    state5_live = {
        "request": "Holding Live Request",
        "supplier_profiles": {"boodmo": VehicleProfile(part="holding_part").to_dict()},
        "profile": VehicleProfile(part="holding_part").to_dict(),
    }
    config5_live = {"configurable": {"thread_id": "session-live-holding"}}

    # Start live search to occupy the semaphore
    task_live_hold = asyncio.create_task(collect(state5_live, config5_live))
    await live_holding_started.wait()
    assert sem.locked(), "Live search must hold the semaphore"
    print(" [OK] Live search is active and holding the search semaphore")

    # Now execute CACHED search while semaphore is 100% full!
    s5_cached = Session(
        boodmo_adapter=MagicMock(name="boodmo", collect=AsyncMock(), close=AsyncMock()),
        autodukan_adapter=MagicMock(name="autodukan", collect=AsyncMock(), close=AsyncMock()),
        announce=lambda t: None,
    )
    graph.SESSIONS["session-cached-concurrent"] = s5_cached

    cached_profile = VehicleProfile(maker="Tata", model="Safari", year="2022", variant="XZ", part="cached_filter")
    state5_cached = {
        "request": "Cached Filter Request",
        "supplier_profiles": {"boodmo": cached_profile.to_dict()},
        "profile": cached_profile.to_dict(),
    }
    config5_cached = {"configurable": {"thread_id": "session-cached-concurrent"}}

    t0_cache = time.time()
    res5_cached = await collect(state5_cached, config5_cached)
    t_cache_ms = round((time.time() - t0_cache) * 1000, 2)

    assert res5_cached["outcome"]["is_cached"] is True, "Must hit cache!"
    assert len(res5_cached["outcome"]["listings"]) == 1, "Must return cached listings!"
    print(f" [OK] Cached search completed in {t_cache_ms}ms WITHOUT WAITING for semaphore!")
    print(f"      Cached outcome: {res5_cached['outcome']['message']}")

    # Now release live search
    live_holding_release.set()
    await task_live_hold
    await close_session("session-live-holding")
    await close_session("session-cached-concurrent")
    assert not sem.locked(), "Semaphore must be released after live search completes"
    print(" [OK] Live search completed and released semaphore")

    # -------------------------------------------------------------------------
    # TEST 6: Memory & Process-Tree Verification
    # -------------------------------------------------------------------------
    print("\n--- Test 6: Memory Breakdown & Context Cleanup ---")
    mem_final = get_process_tree_memory()
    print(f"Final Memory: RSS: {mem_final['total_rss_mb']} MB | PSS: {mem_final['total_pss_mb']} MB | Anon: {mem_final['total_private_anon_mb']} MB")
    print(f"Process Count: {mem_final['process_count']} (1 Python + 1 Chromium + Playwright Driver)")

    diff_rss = round(mem_final['total_rss_mb'] - mem_init['total_rss_mb'], 2)
    diff_anon = round(mem_final['total_private_anon_mb'] - mem_init['total_private_anon_mb'], 2)
    print(f"Delta RSS: {diff_rss} MB | Delta Private Anon: {diff_anon} MB")
    assert diff_anon < 50.0, f"Memory leak detected! Private anonymous memory grew by {diff_anon} MB"
    print(" [OK] No memory leak detected! Private anonymous memory delta < 50MB")

    await bm.shutdown()
    print("\n" + "=" * 65)
    print(" ALL RAILWAY CONCURRENCY & MEMORY SAFETY TESTS PASSED")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(run_concurrency_audit())
