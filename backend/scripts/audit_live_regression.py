"""Final Live Regression & Deployment Readiness Audit Script.

Executes 5 live uncached searches against both suppliers (Boodmo & AutoDukan):
1. Tata Safari oil filter
2. Tata Nexon fuel pump
3. Maruti Swift brake pad
4. Hyundai i20 air filter
5. Bugatti Chiron brake pad (query that returns no results)

Records:
- Supplier status per vendor
- First-result time
- Total search duration
- Product count (per vendor & aggregate)
- Sample listings with fitment evidence
- Rejected incompatible product count
- Process tree memory (RSS, PSS, Private Anon) and cgroup metrics
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# Enforce Railway single-worker safety parameters
os.environ["WEB_CONCURRENCY"] = "1"
os.environ["MAX_CONCURRENT_SEARCHES"] = "1"

from parts_agent import cache_manager, graph, relevance
from parts_agent.browser_manager import get_browser_manager
from parts_agent.models import VehicleProfile
from scripts.run_production_benchmarks import get_process_tree_memory


def get_cgroup_memory() -> dict:
    """Read container cgroup memory metrics if in container, else host slice."""
    candidates = [
        Path("/sys/fs/cgroup/memory.current"),
        Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
        Path("/sys/fs/cgroup/user.slice/memory.current"),
    ]
    peak_candidates = [
        Path("/sys/fs/cgroup/memory.peak"),
        Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes"),
    ]

    curr_mb = None
    peak_mb = None
    source = "not_available"

    for p in candidates:
        if p.exists():
            try:
                curr_mb = round(int(p.read_text().strip()) / (1024 * 1024), 2)
                source = str(p)
                break
            except Exception:
                pass

    for p in peak_candidates:
        if p.exists():
            try:
                peak_mb = round(int(p.read_text().strip()) / (1024 * 1024), 2)
                break
            except Exception:
                pass

    return {
        "source": source,
        "memory_current_mb": curr_mb,
        "memory_peak_mb": peak_mb,
    }


def get_memory_snapshot() -> dict:
    proc = get_process_tree_memory()
    cg = get_cgroup_memory()
    return {
        "rss_mb": proc["total_rss_mb"],
        "pss_mb": proc["total_pss_mb"],
        "private_anon_mb": proc["total_private_anon_mb"],
        "process_count": proc["process_count"],
        "cgroup_current_mb": cg["memory_current_mb"],
        "cgroup_peak_mb": cg["memory_peak_mb"],
        "cgroup_source": cg["source"],
    }


async def run_live_test(bm, test_id: int, query: str, maker: str, model: str, year: str, variant: str, category: str, part: str):
    print("\n" + "=" * 70)
    print(f" LIVE TEST {test_id}: \"{query}\"")
    print(f" Vehicle: {maker} {model} ({year}) {variant} | Part: {part}")
    print("=" * 70)

    # Force cache MISS by purging existing entry for this vehicle/part
    try:
        cache_manager.purge_cached_search(maker=maker, model=model, year=year, variant=variant, part=part)
    except Exception:
        pass

    session_id = f"audit_reg_{test_id}_{int(time.time())}"
    first_result_timestamp = None
    first_supplier_name = None

    def on_supplier_completed(outcome):
        nonlocal first_result_timestamp, first_supplier_name
        now_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
        if first_result_timestamp is None:
            first_result_timestamp = now_iso
            first_supplier_name = outcome.vendor
        print(f" -> [{now_iso}] Supplier '{outcome.vendor}' finished with status '{outcome.status}' ({len(outcome.listings)} items)")

    session = graph.open_session(
        session_id,
        headless=True,
        on_supplier_completed=on_supplier_completed,
    )

    profile = VehicleProfile(
        maker=maker,
        model=model,
        year=year,
        variant=variant,
        category=category,
        part=part,
    )

    state = {
        "request": query,
        "force_refresh": True,
        "profile": profile.to_dict(),
        "supplier_profiles": {
            "boodmo": profile.to_dict(),
            "autodukan": profile.to_dict(),
        },
    }
    config = {"configurable": {"thread_id": session_id}}

    t0 = time.time()
    res = await graph.collect(state, config)
    total_duration = round(time.time() - t0, 2)

    outcome = res["outcome"]
    vendor_outcomes = outcome.get("vendor_outcomes", [])
    all_listings = outcome.get("listings", [])

    # Count rejected incompatible products from raw listings
    # We inspect how many raw listings were rejected across all suppliers
    rejected_count = 0
    for vo in vendor_outcomes:
        for item in vo.get("listings", []):
            compat_status, _ = relevance.check_vehicle_compatibility(
                product_title=item.get("title", ""),
                product_brand=item.get("brand", ""),
                target_maker=maker,
                target_model=model,
            )
            if compat_status == "incompatible":
                rejected_count += 1

    sample_items = all_listings[:5]
    print(f"\n Search Completed in {total_duration}s")
    print(f" First Supplier Result: {first_supplier_name} at {first_result_timestamp}")
    print(f" Total Extracted Products (after relevance/compatibility filtering): {len(all_listings)}")
    print(f" Rejected Incompatible Products: {rejected_count}")

    print("\n Supplier Outcomes:")
    for vo in vendor_outcomes:
        print(f"   - {vo['vendor'].title()}: status={vo['status']}, products={len(vo.get('listings', []))}, elapsed={vo.get('elapsed_seconds')}s")

    print(f"\n Sample Products (First {len(sample_items)}):")
    for idx, item in enumerate(sample_items, 1):
        print(f"   {idx}. {item.get('title')}")
        print(f"      Vendor: {item.get('vendor')} | Price: Rs. {item.get('price')} (MRP: {item.get('mrp')})")
        print(f"      Brand: {item.get('brand')} | Part No: {item.get('part_number')}")
        print(f"      Fitment: {item.get('fitment')} ({item.get('fitment_basis')})")
        print(f"      Relevance: {item.get('relevance')} (Score: {item.get('relevance_score')})")
        print(f"      URL: {item.get('url')}")

    await graph.close_session(session_id)

    return {
        "test_id": test_id,
        "query": query,
        "vehicle": f"{maker} {model} {year} {variant}".strip(),
        "part": part,
        "cache_status": "MISS (forced uncached)",
        "first_result_time": first_result_timestamp,
        "first_supplier": first_supplier_name,
        "total_duration_seconds": total_duration,
        "total_products_count": len(all_listings),
        "rejected_incompatible_count": rejected_count,
        "vendor_outcomes": [
            {
                "vendor": vo["vendor"],
                "status": vo["status"],
                "count": len(vo.get("listings", [])),
                "elapsed": vo.get("elapsed_seconds"),
            }
            for vo in vendor_outcomes
        ],
        "sample_listings": sample_items,
    }


async def main():
    print("=" * 70)
    print(" FINAL RAILWAY DEPLOYMENT READINESS AUDIT — LIVE RUNS")
    print(f" Config: WEB_CONCURRENCY={os.environ.get('WEB_CONCURRENCY')} | MAX_CONCURRENT_SEARCHES={graph.MAX_CONCURRENT_SEARCHES}")
    print("=" * 70)

    bm = get_browser_manager()
    print("Purging cached live searches to guarantee 100% uncached runs...")
    purged = cache_manager.purge_cached_search()
    print(f"Purged {purged} previous cache records.")
    print("Initializing central Chromium browser instance...")
    await bm.initialize(headless=True)

    mem_idle = get_memory_snapshot()
    print(f"\n[MEMORY IDLE] RSS: {mem_idle['rss_mb']} MB | PSS: {mem_idle['pss_mb']} MB | Anon: {mem_idle['private_anon_mb']} MB | Processes: {mem_idle['process_count']}")

    test_cases = [
        (1, "Tata Safari oil filter", "Tata", "Safari", "2022", "2.0L Kryotec", "Filters", "Oil Filter"),
        (2, "Tata Nexon fuel pump", "Tata", "Nexon", "2023", "1.2L Revotron", "Fuel System", "Fuel Pump"),
        (3, "Maruti Swift brake pad", "Maruti", "Swift", "2020", "1.2L VXI / PETROL", "Brake System", "Brake Pad"),
        (4, "Hyundai i20 air filter", "Hyundai", "i20", "2021", "1.2L Kappa", "Filters", "Air Filter"),
        (5, "Bugatti Chiron brake pad", "Bugatti", "Chiron", "2020", "8.0L W16", "Brake System", "Brake Pad"),
    ]

    results = []
    active_memory_snapshots = []

    for tc in test_cases:
        res = await run_live_test(bm, *tc)
        results.append(res)
        snap = get_memory_snapshot()
        active_memory_snapshots.append(snap)
        print(f" -> Memory after test {tc[0]}: RSS: {snap['rss_mb']} MB | PSS: {snap['pss_mb']} MB | Anon: {snap['private_anon_mb']} MB")

    mem_post = get_memory_snapshot()
    await bm.shutdown()

    summary = {
        "test_results": results,
        "memory_profile": {
            "idle": mem_idle,
            "post_tests": mem_post,
            "intermediate_snapshots": active_memory_snapshots,
        },
    }

    out_file = Path(_backend_dir) / "scripts" / "final_audit_results.json"
    out_file.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n" + "=" * 70)
    print(" AUDIT EXECUTION COMPLETED")
    print(f" Summary saved to {out_file}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
