"""Audit Verification Script for Fuel Pump Clarification & Safari Oil Filter Relevance."""
from __future__ import annotations

import asyncio
import json
import sys
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
    INCOMPATIBLE,
    IRRELEVANT,
    NEEDS_REVIEW,
    check_vehicle_compatibility,
    classify_listings,
    classify_product,
    rank_listings,
)


async def test_fuel_pump_verification():
    print("\n" + "=" * 60)
    print(" AUDIT 1: FUEL PUMP FOR TATA SAFARI")
    print("=" * 60)

    # 1. Test AutoDukan category resolution
    ad_adapter = auto_dukan.AutoDukanAdapter()
    p_ad = VehicleProfile(part="fuel pump", maker="Tata", model="Safari")
    need_ad = await ad_adapter.resolve_category(p_ad)
    print(f"1. AutoDukan Fuel Pump Resolution:")
    print(f"   NeedInput: {need_ad}")
    print(f"   Profile Category: {p_ad.category}")
    print(f"   Category Path: {ad_adapter._category_paths.get(p_ad.category.lower() if p_ad.category else '')}")
    assert need_ad is None, "AutoDukan must resolve 'fuel pump' directly without clarification!"
    assert p_ad.category == "FUEL PUMP", f"Expected category 'FUEL PUMP', got '{p_ad.category}'"

    # 2. Test Boodmo category resolution
    bm_adapter = boodmo.BoodmoAdapter()
    p_bm = VehicleProfile(part="fuel pump", maker="Tata", model="Safari")
    need_bm = await bm_adapter.resolve_category(p_bm)
    print(f"\n2. Boodmo Fuel Pump Resolution:")
    print(f"   NeedInput: {need_bm}")
    print(f"   Profile Category: {p_bm.category}")
    print(f"   Category Href: {bm_adapter.category_href}")
    assert need_bm is None, "Boodmo must resolve 'fuel pump' directly without clarification!"
    assert p_bm.category == "Fuel Pump", f"Expected category 'Fuel Pump', got '{p_bm.category}'"

    # 3. Test Escape Options ("None of these") Handling
    print(f"\n3. Escape Options & Loop Prevention:")
    mock_bm = MagicMock()
    mock_bm.get_page = AsyncMock()
    mock_bm.close_session = AsyncMock()

    conv = graph.Conversation(thread_id="audit-fuel-pump-flow")
    session = graph.Session(
        boodmo_adapter=boodmo.BoodmoAdapter(browser_manager=mock_bm),
        autodukan_adapter=auto_dukan.AutoDukanAdapter(browser_manager=mock_bm),
        announce=lambda t: None,
    )
    graph.SESSIONS["audit-fuel-pump-flow"] = session

    try:
        # Simulate user requesting ambiguous part where category question is asked
        pending_need = {
            "field": "category",
            "question": "Which AutoDukan category best fits fuel pump?",
            "options": ["Lighting", "Brake System", "Body Parts", "None of these", "Search by part name instead"],
            "vendor": "autodukan",
        }
        state: graph.State = {
            "request": "Find fuel pump for Tata Safari",
            "pending": pending_need,
            "answer": "None of these",
            "supplier_profiles": {
                "boodmo": p_bm.to_dict(),
                "autodukan": p_ad.to_dict(),
            },
            "profile": p_bm.to_dict(),
        }
        config = {"configurable": {"thread_id": "audit-fuel-pump-flow"}}

        # Interpret the "None of these" answer
        new_state = await graph.interpret(state, config)
        print(f"   Interpreted 'None of these':")
        print(f"   - Pending cleared: {new_state.get('pending') is None}")
        print(f"   - Category fallback flagged: {new_state['supplier_profiles']['autodukan']['hints'].get('category_fallback')}")
        print(f"   - Preserved part: {new_state['supplier_profiles']['autodukan'].get('part')}")
        assert new_state.get("pending") is None, "Pending must be cleared after selecting 'None of these'"
        assert new_state["supplier_profiles"]["autodukan"].get("part") == "fuel pump", "Part name must survive!"

        # Advance navigation
        session.boodmo_adapter.advance = AsyncMock(return_value=None)
        session.autodukan_adapter.advance = AsyncMock(return_value=None)
        nav_state = await graph.navigate(new_state, config)
        print(f"   - Navigation next pending: {nav_state.get('pending')}")
        assert nav_state.get("pending") is None or nav_state["pending"]["field"] != "category", "Must NOT repeat category clarification!"
        print("   -> Result: PASSED (Zero Loop, Fallback Successful, Context Preserved)")
    finally:
        graph.SESSIONS.pop("audit-fuel-pump-flow", None)


async def test_safari_oil_filter_verification():
    print("\n" + "=" * 60)
    print(" AUDIT 2: TATA SAFARI OIL FILTER RELEVANCE & BRAND REJECTION")
    print("=" * 60)

    listings = [
        {
            "title": "Audi Genuine Oil Filter Element A4 A6 Q5",
            "price": 50000.0,
            "brand": "Audi",
            "part_number": "06L115562B",
            "url": "https://autodukan.com/product/audi-filter",
            "fitment": "unverified",
        },
        {
            "title": "Tata Genuine Oil Filter Safari / Harrier 2.0L Diesel",
            "price": 750.0,
            "brand": "Tata",
            "part_number": "542718140101",
            "url": "https://autodukan.com/product/tata-filter",
            "fitment": "verified",
        },
        {
            "title": "Bosch Oil Filter for Tata Safari Diesel",
            "price": 680.0,
            "brand": "Bosch",
            "part_number": "F002H24001",
            "url": "https://autodukan.com/product/bosch-filter",
            "fitment": "verified",
        },
        {
            "title": "Mann-Filter Oil Filter Element 2.0 TDI",
            "price": 850.0,
            "brand": "Mann-Filter",
            "part_number": "HU71151Z",
            "url": "https://autodukan.com/product/mann-filter",
            "fitment": "unverified",
        },
        {
            "title": "BMW Engine Oil Filter Cartridge 320d",
            "price": 4200.0,
            "brand": "BMW",
            "part_number": "11428507683",
            "url": "https://autodukan.com/product/bmw-filter",
            "fitment": "unverified",
        },
    ]

    print("Candidate listings submitted for classification:")
    for l in listings:
        print(f" - {l['title']} | Brand: {l['brand']} | Price: Rs. {l['price']}")

    classified = classify_listings(
        listings,
        query="Tata Safari oil filter",
        requested_part="oil filter",
        target_maker="Tata",
        target_model="Safari",
    )

    print("\nClassification Results:")
    for c in classified:
        print(f" * {c['title']}: relevance={c['relevance']} (score={c['relevance_score']}) reason='{c['relevance_reason']}' fitment={c['fitment']}")

    audi_item = next(l for l in classified if "Audi" in l["title"])
    bmw_item = next(l for l in classified if "BMW" in l["title"])
    tata_item = next(l for l in classified if "Tata Genuine" in l["title"])
    bosch_item = next(l for l in classified if "Bosch" in l["title"])
    mann_item = next(l for l in classified if "Mann-Filter" in l["title"])

    assert audi_item["relevance"] == INCOMPATIBLE, f"Audi item must be INCOMPATIBLE, got {audi_item['relevance']}"
    assert bmw_item["relevance"] == INCOMPATIBLE, f"BMW item must be INCOMPATIBLE, got {bmw_item['relevance']}"
    assert tata_item["relevance"] == EXACT_MATCH, f"Tata item must be EXACT_MATCH, got {tata_item['relevance']}"
    assert bosch_item["relevance"] == EXACT_MATCH, f"Bosch item must be EXACT_MATCH, got {bosch_item['relevance']}"

    ranked = rank_listings(classified, price_order="asc")
    print(f"\nFinal Ranked Products (price asc within relevance tiers):")
    for idx, r in enumerate(ranked, 1):
        print(f" {idx}. {r['title']} | Price: Rs. {r['price']} | Relevance: {r['relevance']}")

    ranked_titles = [r["title"] for r in ranked]
    assert "Audi Genuine Oil Filter Element A4 A6 Q5" not in ranked_titles, "Audi filter MUST NOT be in ranked results!"
    assert "BMW Engine Oil Filter Cartridge 320d" not in ranked_titles, "BMW filter MUST NOT be in ranked results!"
    assert ranked[0]["title"] == "Bosch Oil Filter for Tata Safari Diesel", f"Expected cheapest compatible filter first (Bosch Rs. 680), got {ranked[0]['title']}"
    assert ranked[1]["title"] == "Tata Genuine Oil Filter Safari / Harrier 2.0L Diesel", f"Expected Tata Rs. 750 second, got {ranked[1]['title']}"

    print("   -> Result: PASSED (Audi ₹50k part strictly rejected, price never overrides relevance)")


async def main():
    await test_fuel_pump_verification()
    await test_safari_oil_filter_verification()
    print("\n" + "=" * 60)
    print(" ALL FUNCTIONAL AUDIT CHECKS COMPLETED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
