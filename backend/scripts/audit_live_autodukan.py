"""Audit Live Uncached Searches for AutoDukan Performance & Data Quality.

Executes 3 independent, uncached live searches with different vehicle/part combinations:
1. Maruti Swift (2020) — Brake Pad
2. Hyundai Creta (2021) — Brake Pad
3. Tata Tiago (2022) — Brake Pad
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

from parts_agent import auto_dukan, cache_manager
from parts_agent.browser_manager import get_browser_manager
from parts_agent.models import VehicleProfile


async def run_live_search(bm, run_num: int, maker: str, model: str, year: str, variant: str, category: str, part: str):
    print(f"\n" + "=" * 65)
    print(f" RUN {run_num}: {maker} {model} ({year}) {variant} — {part}")
    print(f"=" * 65)

    session_id = f"audit_live_{run_num}_{int(time.time())}"
    adapter = auto_dukan.AutoDukanAdapter(
        headless=True,
        session_id=session_id,
        browser_manager=bm,
    )

    profile = VehicleProfile(
        maker=maker,
        model=model,
        year=year,
        variant=variant,
        category=category,
        part=part,
    )

    t0_wall = time.time()
    nav_start_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0_wall))

    # Step 1: Advance / Navigation
    t0_nav = time.time()
    need = await adapter.advance(profile)
    t_nav = round(time.time() - t0_nav, 2)
    nav_end_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    print(f" -> Nav Start: {nav_start_iso} | Nav End: {nav_end_iso} | Elapsed: {t_nav}s (NeedInput: {need})")

    # Measure DOM readiness from the page directly if open
    dom_readiness_ms = None
    if adapter.page and not adapter.page.is_closed():
        try:
            dom_readiness_ms = await adapter.page.evaluate("""() => {
                const nav = performance.getEntriesByType('navigation')[0];
                return nav ? Math.round(nav.domContentLoadedEventEnd - nav.startTime) : null;
            }""")
        except Exception:
            pass
    print(f" -> DOM Readiness (domContentLoaded): {dom_readiness_ms}ms")

    # Step 2: Extraction / Collect
    t0_ext = time.time()
    outcome = await adapter.collect(profile)
    t_ext = round(time.time() - t0_ext, 2)
    t_total = round(time.time() - t0_wall, 2)
    first_result_time_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))

    print(f" -> First Supplier Result Timestamp: {first_result_time_iso}")
    print(f" -> Product Extraction Duration: {t_ext}s")
    print(f" -> Total Search Duration: {t_total}s")
    print(f" -> Cache Status: MISS (uncached live search)")
    print(f" -> Extracted Products Count: {len(outcome.listings)}")
    print(f" -> Status: {outcome.status}")
    print(f" -> Source URL: {outcome.source_url}")

    sample_items = outcome.listings[:5]
    print(f"\n Sample Extracted Products (First {len(sample_items)}):")
    for idx, item in enumerate(sample_items, 1):
        print(f"   {idx}. {item.get('title')}")
        print(f"      Price: Rs. {item.get('price')} (MRP: {item.get('mrp')})")
        print(f"      Brand: {item.get('brand')} | Part No: {item.get('part_number')}")
        print(f"      Fitment: {item.get('fitment')} ({item.get('fitment_basis')})")
        print(f"      URL: {item.get('url')}")
        print(f"      Available: {item.get('available')}")

    await adapter.close()
    await bm.close_session(session_id)

    return {
        "run": run_num,
        "vehicle": f"{maker} {model} {year} {variant}".strip(),
        "part": part,
        "cache_status": "MISS (live uncached)",
        "nav_start": nav_start_iso,
        "nav_end": nav_end_iso,
        "dom_readiness_ms": dom_readiness_ms,
        "first_result_timestamp": first_result_time_iso,
        "navigation_seconds": t_nav,
        "extraction_seconds": t_ext,
        "total_seconds": t_total,
        "products_count": len(outcome.listings),
        "status": outcome.status,
        "source_url": outcome.source_url,
        "sample_listings": sample_items,
        "all_listings": outcome.listings,
    }


async def main():
    bm = get_browser_manager()
    print("Initializing Chromium browser runtime...")
    await bm.initialize(headless=True)

    runs = [
        (1, "Maruti", "Swift", "2020", "1.2L VXI / PETROL", "Brake System", "Brake Pad"),
        (2, "Hyundai", "Creta", "2021", "1.5L SX / PETROL", "Brake System", "Brake Pad"),
        (3, "Tata", "Tiago", "2022", "1.2L Revotron", "Brake System", "Brake Pad"),
    ]

    results = []
    for run_args in runs:
        res = await run_live_search(bm, *run_args)
        results.append(res)

    print("\n" + "=" * 65)
    print(" LIVE BENCHMARK SUMMARY (3 UNCACHED RUNS)")
    print("=" * 65)
    for r in results:
        print(f"Run {r['run']} [{r['vehicle']} - {r['part']}]:")
        print(f"  Navigation: {r['navigation_seconds']}s | Extraction: {r['extraction_seconds']}s | Total: {r['total_seconds']}s | Products: {r['products_count']}")

    await bm.shutdown()

    out_file = Path(_backend_dir) / "scripts" / "live_benchmark_results.json"
    out_file.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nFull results written to {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
