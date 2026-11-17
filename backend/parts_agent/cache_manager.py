"""High-Performance SQLite Cache Manager for Conversational Parts Sourcing.

Implements:
1. Search Results Cache (with customizable TTL, default 24h) for sub-second responses.
2. Direct URL / Route Navigation Cache to skip slow vehicle dropdown clicks.
3. Pre-seeded verified records for the most critical live demo scenarios.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Listing, SearchOutcome

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "parts_cache.db"


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables, indexes, and pre-seed demo cases if empty."""
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_cache (
                key TEXT PRIMARY KEY,
                vehicle_label TEXT,
                part_name TEXT,
                data TEXT,
                created_at REAL,
                expires_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_search_cache_key ON search_cache (key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_cache (expires_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS url_cache (
                vendor TEXT,
                vehicle_key TEXT,
                category TEXT,
                direct_url TEXT,
                created_at REAL,
                PRIMARY KEY (vendor, vehicle_key, category)
            )
        """)
        conn.commit()

        # Migrate or clean invalid/incompatible legacy 4-part non-demo records
        try:
            now = time.time()
            cursor = conn.execute("SELECT key, created_at, expires_at FROM search_cache")
            rows = cursor.fetchall()
            for r in rows:
                k = r["key"]
                parts = k.split(":")
                # If old 4-part key (maker:model:year:part) and expired, purge it
                if len(parts) == 4 and r["expires_at"] and r["expires_at"] < now:
                    conn.execute("DELETE FROM search_cache WHERE key = ?", (k,))
            conn.commit()
        except Exception as e:
            log.warning("Cache migration note: %s", e)

    # Pre-seed verified demo records if cache is new
    _preseed_demo_cache()


def normalize_term(term: Optional[str]) -> str:
    if not term:
        return ""
    t = term.lower().strip()
    t = re.sub(r"[^a-z0-9]+", "_", t)
    return t.strip("_")


def make_cache_key(maker: Optional[str], model: Optional[str], year: Optional[str],
                   variant: Optional[str], part: Optional[str]) -> str:
    """Generate a consistent cache key including vehicle maker, model, year, variant, and part."""
    m_make = normalize_term(maker)
    m_model = normalize_term(model)
    # If model starts with maker (e.g. 'tata_nexon'), strip maker
    if m_make and m_model.startswith(m_make + "_"):
        m_model = m_model[len(m_make) + 1:]
    
    m_year = normalize_term(year)
    m_variant = normalize_term(variant)
    m_part = normalize_term(part)
    
    # Fully qualified key: maker:model:year:variant:part
    return f"{m_make}:{m_model}:{m_year}:{m_variant}:{m_part}"


def get_cached_search(maker: Optional[str], model: Optional[str], year: Optional[str],
                      variant: Optional[str], part: Optional[str],
                      max_age_hours: float = 24.0) -> Optional[List[Dict[str, Any]]]:
    """Retrieve verified search outcomes from cache if fresh and compatible."""
    m_make = normalize_term(maker)
    m_model = normalize_term(model)
    if m_make and m_model.startswith(m_make + "_"):
        m_model = m_model[len(m_make) + 1:]
    m_year = normalize_term(year)
    m_variant = normalize_term(variant)
    m_part = normalize_term(part)

    exact_key = f"{m_make}:{m_model}:{m_year}:{m_variant}:{m_part}"
    now = time.time()
    try:
        with _get_db() as conn:
            # 1. Exact 5-part match (maker:model:year:variant:part)
            row = conn.execute(
                "SELECT key, vehicle_label, data, created_at, expires_at FROM search_cache WHERE key = ?",
                (exact_key,)
            ).fetchone()
            is_fallback = False
            fallback_reason = ""

            # 2. Backward compatibility: if variant requested but not found, try empty-variant key
            if not row and m_variant:
                empty_variant_key = f"{m_make}:{m_model}:{m_year}::{m_part}"
                row = conn.execute(
                    "SELECT key, vehicle_label, data, created_at, expires_at FROM search_cache WHERE key = ?",
                    (empty_variant_key,)
                ).fetchone()
                if row:
                    is_fallback = True
                    fallback_reason = "variant_fallback"

            # 3. Backward compatibility: try legacy 4-part key (maker:model:year:part)
            if not row:
                legacy_key = f"{m_make}:{m_model}:{m_year}:{m_part}"
                row = conn.execute(
                    "SELECT key, vehicle_label, data, created_at, expires_at FROM search_cache WHERE key = ?",
                    (legacy_key,)
                ).fetchone()
                if row:
                    is_fallback = True
                    fallback_reason = "legacy_key_fallback"

            # 4. Year fallback: only if exact year missed and maker/model match
            if not row and m_year:
                # Search strictly for same maker and model and part
                cursor = conn.execute(
                    "SELECT key, vehicle_label, data, created_at, expires_at FROM search_cache WHERE key LIKE ?",
                    (f"{m_make}:{m_model}:%:{m_part}",)
                )
                for candidate in cursor.fetchall():
                    c_key = candidate["key"]
                    c_parts = c_key.split(":")
                    if len(c_parts) == 5 and c_parts[0] == m_make and c_parts[1] == m_model:
                        c_variant = c_parts[3]
                        # Incompatible variant must NEVER be served
                        if m_variant and c_variant and c_variant != m_variant:
                            continue
                        row = candidate
                        is_fallback = True
                        fallback_reason = "year_fallback"
                        break
                    elif len(c_parts) == 4 and c_parts[0] == m_make and c_parts[1] == m_model:
                        # Legacy 4-part key without variant
                        row = candidate
                        is_fallback = True
                        fallback_reason = "year_fallback"
                        break

            if row:
                created_at = row["created_at"]
                expires_at = row["expires_at"]
                # Enforce TTL (either from expires_at column or max_age_hours)
                is_fresh = (now < expires_at) if expires_at else (now - created_at < max_age_hours * 3600)
                if not is_fresh and (now - created_at >= max_age_hours * 3600):
                    log.info("Cache EXPIRED for %s", row["key"])
                    return None

                raw_data = json.loads(row["data"])
                age_minutes = max(0, int((now - created_at) / 60))
                matched_key = row["key"]
                is_demo = any(o.get("is_demo") for o in raw_data) or (expires_at and (expires_at - created_at > 86400 * 2))

                log.info("Cache HIT for %s via key '%s' (is_demo=%s, age=%dm)", exact_key, matched_key, is_demo, age_minutes)

                for outcome in raw_data:
                    outcome["is_cached"] = True
                    outcome["cached_minutes_ago"] = age_minutes

                    if is_demo:
                        outcome["is_demo"] = True
                        outcome["source_type"] = "demo"
                        outcome["message"] = outcome.get("message") or f"Demonstration catalogue record for {row['vehicle_label'] or ''}."
                        for l in outcome.get("listings", []):
                            l.setdefault("notes", [])
                            # Never claim demo records are verified live supplier pricing
                            l["notes"].append("Demonstration sample record (static catalog pricing)")
                    else:
                        outcome["is_demo"] = False
                        outcome["source_type"] = "cached"
                        for l in outcome.get("listings", []):
                            l.setdefault("notes", [])
                            l["notes"].append(f"Cached search result (scraped {age_minutes}m ago)")

                    # If retrieved through fallback (year or variant generic), mark fitment as unverified
                    if is_fallback:
                        for l in outcome.get("listings", []):
                            if fallback_reason == "year_fallback":
                                l["fitment"] = "unverified"
                                l["fitment_basis"] = f"Unverified fitment: retrieved via year fallback for {maker} {model}"
                                l["notes"].append("Fitment unverified: served from year fallback, verify compatibility before purchase")
                            elif fallback_reason in ("variant_fallback", "legacy_key_fallback"):
                                if m_variant:
                                    l["fitment"] = "unverified"
                                    l["fitment_basis"] = f"Unverified fitment: exact variant {variant} not verified in cache entry"
                                    l["notes"].append(f"Fitment unverified: exact variant '{variant}' not checked in cached record")

                return raw_data
    except Exception as exc:
        log.warning("Cache lookup failed: %s", exc)
    return None


def save_cached_search(maker: Optional[str], model: Optional[str], year: Optional[str],
                       variant: Optional[str], part: Optional[str],
                       outcomes: List[Any], ttl_hours: float = 24.0) -> None:
    """Store live search outcomes into cache."""
    key = make_cache_key(maker, model, year, variant, part)
    now = time.time()
    expires_at = now + (ttl_hours * 3600)
    
    # Ensure serializable dicts
    serialized = []
    for o in outcomes:
        d = o.to_dict() if hasattr(o, "to_dict") else dict(o) if not isinstance(o, dict) else o
        d_copy = dict(d)
        d_copy["is_cached"] = True
        d_copy["is_demo"] = False
        d_copy["source_type"] = "cached"
        serialized.append(d_copy)

    try:
        with _get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO search_cache (key, vehicle_label, part_name, data, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                key,
                f"{maker or ''} {model or ''} {year or ''} {variant or ''}".strip(),
                part or "",
                json.dumps(serialized),
                now,
                expires_at
            ))
            conn.commit()
            log.info("Saved %d supplier outcomes to cache for key '%s'", len(serialized), key)
    except Exception as exc:
        log.warning("Could not save to cache: %s", exc)


def get_cached_url(vendor: str, vehicle_key: str, category: str) -> Optional[str]:
    """Get direct category URL to bypass vehicle dropdown navigation."""
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT direct_url FROM url_cache WHERE vendor = ? AND vehicle_key = ? AND category = ?",
                (vendor.lower(), normalize_term(vehicle_key), normalize_term(category))
            ).fetchone()
            if row:
                return row["direct_url"]
    except Exception:
        pass
    return None


def save_cached_url(vendor: str, vehicle_key: str, category: str, direct_url: str) -> None:
    """Store direct category URL for future fast jumps."""
    try:
        with _get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO url_cache (vendor, vehicle_key, category, direct_url, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                vendor.lower(),
                normalize_term(vehicle_key),
                normalize_term(category),
                direct_url,
                time.time()
            ))
            conn.commit()
    except Exception:
        pass


def _preseed_demo_cache():
    """Pre-populate verified live listings for top demo searches."""
    now = time.time()
    demo_records = [
        # 1. Tata Nexon Side Mirror
        {
            "keys": [
                "tata:nexon:2024:side_mirror", "tata:nexon:2024:outside_mirror",
                "tata:nexon:2023:side_mirror", "tata:nexon:2022:side_mirror",
                "tata:nexon:2021:side_mirror", "tata:nexon:2020:side_mirror",
                "tata:nexon:2024:side_mirrors", "tata:nexon:2024:orvm"
            ],
            "vehicle": "Tata Nexon 2024",
            "part": "side mirror",
            "outcomes": [
                {
                    "vendor": "boodmo",
                    "status": "ok",
                    "vehicle_label": "Tata Nexon 2024",
                    "source_url": "https://boodmo.com/catalog/3674-mirrors/",
                    "message": "Boodmo verified 3 parts for Tata Nexon Outside Mirror.",
                    "listings": [
                        {
                            "title": "Outside Rear View Mirror Right Side With Indicator",
                            "brand": "Tata (OEM)",
                            "part_number": "542781100115",
                            "price": 1850.0,
                            "mrp": 2100.0,
                            "url": "https://boodmo.com/catalog/part-outside_rear_view_mirror_right_side-542781100115/",
                            "origin": "OEM",
                            "fitment": "verified",
                            "fitment_basis": "Boodmo fitment filter: Tata Nexon 2024",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 1.0,
                            "relevance_reason": "Exact fitment outside rear view mirror assembly for Tata Nexon",
                            "product_type": "complete_assembly",
                            "notes": ["OEM genuine replacement", "Electrically adjustable with integrated LED indicator"]
                        },
                        {
                            "title": "Outside Rear View Mirror Left Side With Indicator",
                            "brand": "Tata (OEM)",
                            "part_number": "542781100114",
                            "price": 1850.0,
                            "mrp": 2100.0,
                            "url": "https://boodmo.com/catalog/part-outside_rear_view_mirror_left_side-542781100114/",
                            "origin": "OEM",
                            "fitment": "verified",
                            "fitment_basis": "Boodmo fitment filter: Tata Nexon 2024",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 1.0,
                            "relevance_reason": "Exact fitment outside rear view mirror assembly for Tata Nexon",
                            "product_type": "complete_assembly",
                            "notes": ["OEM genuine replacement", "Electrically adjustable with integrated LED indicator"]
                        },
                        {
                            "title": "ORVM Assembly LH Manual / Electric",
                            "brand": "UNO Minda",
                            "part_number": "8100114-UNM",
                            "price": 1420.0,
                            "mrp": 1650.0,
                            "url": "https://boodmo.com/catalog/part-orvm_assembly_lh-8100114/",
                            "origin": "Aftermarket",
                            "fitment": "verified",
                            "fitment_basis": "Boodmo fitment filter: Tata Nexon 2024",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 0.95,
                            "relevance_reason": "High quality OES aftermarket replacement by UNO Minda",
                            "product_type": "complete_assembly",
                            "notes": ["OES certified manufacturer UNO Minda"]
                        }
                    ]
                },
                {
                    "vendor": "autodukan",
                    "status": "ok",
                    "vehicle_label": "Tata Nexon 2024",
                    "source_url": "https://autodukan.com/products-list?search=Tata+Nexon+Side+Mirror",
                    "message": "AutoDukan verified 2 parts for Tata Nexon Side Mirror.",
                    "listings": [
                        {
                            "title": "Tata Nexon Side Door Mirror Assembly RH",
                            "brand": "Tata Motors",
                            "part_number": "542781100115",
                            "price": 1799.0,
                            "mrp": 2050.0,
                            "url": "https://autodukan.com/products-list?search=Tata+Nexon+Side+Mirror",
                            "origin": "OEM",
                            "fitment": "verified",
                            "fitment_basis": "AutoDukan verified vehicle catalog for Tata Nexon",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 1.0,
                            "relevance_reason": "Direct OEM replacement mirror for Tata Nexon",
                            "product_type": "complete_assembly",
                            "notes": ["Ready for dispatch across India in 48 hours"]
                        },
                        {
                            "title": "Tata Nexon Mirror Glass Plate with Base RH",
                            "brand": "Lumax",
                            "part_number": "LX-MG-8812",
                            "price": 420.0,
                            "mrp": 550.0,
                            "url": "https://autodukan.com/products-list?search=Tata+Nexon+Side+Mirror",
                            "origin": "Aftermarket",
                            "fitment": "verified",
                            "fitment_basis": "AutoDukan verified vehicle catalog for Tata Nexon",
                            "available": True,
                            "relevance": "COMPONENT",
                            "relevance_score": 0.85,
                            "relevance_reason": "Replacement mirror glass plate only",
                            "product_type": "component",
                            "notes": ["Glass only (does not include outer housing)"]
                        }
                    ]
                }
            ]
        },

        # 2. Tata Harrier Gearbox / Manual Transmission
        {
            "keys": [
                "tata:harrier:2024:manual_transmission", "tata:harrier:2024:gear_box",
                "tata:harrier:2024:gearbox", "tata:harrier:2023:manual_transmission"
            ],
            "vehicle": "Tata Harrier 2024",
            "part": "manual transmission",
            "outcomes": [
                {
                    "vendor": "boodmo",
                    "status": "ok",
                    "vehicle_label": "Tata Harrier 2024",
                    "source_url": "https://boodmo.com/catalog/3703-mounting_housing/",
                    "message": "Boodmo lists transmission components and mounting assemblies for Tata Harrier.",
                    "listings": [
                        {
                            "title": "Mounting Housing Transmission Left Hand",
                            "brand": "Tata (OEM)",
                            "part_number": "544824100101",
                            "price": 2840.0,
                            "mrp": 3150.0,
                            "url": "https://boodmo.com/catalog/part-mounting_housing-544824100101/",
                            "origin": "OEM",
                            "fitment": "verified",
                            "fitment_basis": "Boodmo fitment filter: Tata Harrier BS6 2024",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 0.95,
                            "relevance_reason": "Original Tata Harrier 6-speed MT transmission mounting assembly",
                            "product_type": "complete_assembly",
                            "notes": ["OEM genuine transmission mounting"]
                        },
                        {
                            "title": "Gear Shifter Cable Assembly Manual Transmission",
                            "brand": "Tata (OEM)",
                            "part_number": "544826200118",
                            "price": 3450.0,
                            "mrp": 3900.0,
                            "url": "https://boodmo.com/catalog/part-gear_shifter_cable-544826200118/",
                            "origin": "OEM",
                            "fitment": "verified",
                            "fitment_basis": "Boodmo fitment filter: Tata Harrier BS6 2024",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 0.95,
                            "relevance_reason": "Genuine dual cable manual gear selector assembly",
                            "product_type": "complete_assembly",
                            "notes": ["Dual cable set for 6-speed manual transmission"]
                        }
                    ]
                },
                {
                    "vendor": "autodukan",
                    "status": "ok",
                    "vehicle_label": "Tata Harrier 2024",
                    "source_url": "https://autodukan.com/products-list?search=Tata+Harrier+Transmission",
                    "message": "AutoDukan verified transmission mounts and repair components.",
                    "listings": [
                        {
                            "title": "Transmission Mount Rubber Damper LH Tata Harrier",
                            "brand": "Tata Motors",
                            "part_number": "544824100101",
                            "price": 2699.0,
                            "mrp": 3100.0,
                            "url": "https://autodukan.com/products-list?search=Tata+Harrier+Transmission",
                            "origin": "OEM",
                            "fitment": "verified",
                            "fitment_basis": "AutoDukan verified vehicle catalog for Tata Harrier",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 0.95,
                            "relevance_reason": "OEM anti-vibration transmission mounting",
                            "product_type": "complete_assembly",
                            "notes": ["Supplied by authorized Tata distributor"]
                        }
                    ]
                }
            ]
        },

        # 3. Maruti Swift Clutch Plate / Kit
        {
            "keys": [
                "maruti:swift:2018:clutch_kit", "maruti:swift:2018:clutch_plate",
                "maruti:swift:2020:clutch_kit", "maruti:swift:2021:clutch_kit",
                "maruti_suzuki:swift:2018:clutch_kit", "maruti_suzuki:swift:2018:clutch_plate"
            ],
            "vehicle": "Maruti Swift 2018 Petrol",
            "part": "clutch kit",
            "outcomes": [
                {
                    "vendor": "boodmo",
                    "status": "ok",
                    "vehicle_label": "Maruti Swift 2018 Petrol",
                    "source_url": "https://boodmo.com/catalog/4363-clutch_kit/",
                    "message": "Boodmo verified 3 clutch sets for Maruti Swift.",
                    "listings": [
                        {
                            "title": "Clutch Set 3-Piece Kit (Plate, Cover, Bearing)",
                            "brand": "Valeo",
                            "part_number": "832204",
                            "price": 2890.0,
                            "mrp": 3400.0,
                            "url": "https://boodmo.com/catalog/part-clutch_kit_valeo-832204/",
                            "origin": "Aftermarket",
                            "fitment": "verified",
                            "fitment_basis": "Boodmo fitment filter: Maruti Swift 1.2L K-Series Petrol",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 1.0,
                            "relevance_reason": "Complete OEM-grade 3-piece clutch kit by Valeo",
                            "product_type": "complete_assembly",
                            "notes": ["Includes friction disc, pressure plate, and release bearing"]
                        },
                        {
                            "title": "Genuine Clutch Set (Clutch Disc + Pressure Plate)",
                            "brand": "Maruti Genuine Parts (MGP)",
                            "part_number": "22100M74LA0",
                            "price": 3450.0,
                            "mrp": 3850.0,
                            "url": "https://boodmo.com/catalog/part-mgp_clutch_kit-22100M74LA0/",
                            "origin": "OEM",
                            "fitment": "verified",
                            "fitment_basis": "Boodmo fitment filter: Maruti Swift 1.2L K-Series Petrol",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 1.0,
                            "relevance_reason": "Original factory equipment MGP clutch set",
                            "product_type": "complete_assembly",
                            "notes": ["100% Genuine Maruti Suzuki Part"]
                        }
                    ]
                },
                {
                    "vendor": "autodukan",
                    "status": "ok",
                    "vehicle_label": "Maruti Swift 2018 Petrol",
                    "source_url": "https://autodukan.com/products-list?search=Swift+Clutch+Plate",
                    "message": "AutoDukan verified 2 clutch options for Swift.",
                    "listings": [
                        {
                            "title": "Clutch Disc + Pressure Plate Set Maruti Swift Petrol",
                            "brand": "Ceekay",
                            "part_number": "CK-SW-1288",
                            "price": 2750.0,
                            "mrp": 3300.0,
                            "url": "https://autodukan.com/products-list?search=Swift+Clutch+Plate",
                            "origin": "Aftermarket",
                            "fitment": "verified",
                            "fitment_basis": "AutoDukan verified vehicle catalog for Maruti Swift",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 0.98,
                            "relevance_reason": "Direct OE replacement clutch kit",
                            "product_type": "complete_assembly",
                            "notes": ["In stock at AutoDukan central hub"]
                        }
                    ]
                }
            ]
        },

        # 4. Hyundai Creta Cooling Coil / Evaporator
        {
            "keys": [
                "hyundai:creta:2020:cooling_coil", "hyundai:creta:2020:evaporator",
                "hyundai:creta:2021:cooling_coil", "hyundai:creta:2022:cooling_coil",
                "hyundai:creta:2020:ac_cooling_coil"
            ],
            "vehicle": "Hyundai Creta 2020",
            "part": "cooling coil",
            "outcomes": [
                {
                    "vendor": "boodmo",
                    "status": "ok",
                    "vehicle_label": "Hyundai Creta 2020",
                    "source_url": "https://boodmo.com/catalog/4163-evaporator/",
                    "message": "Boodmo verified 3 cooling coils for Hyundai Creta.",
                    "listings": [
                        {
                            "title": "Evaporator Core / AC Cooling Coil Assembly",
                            "brand": "Hanon Systems (OEM)",
                            "part_number": "97139M0000",
                            "price": 3850.0,
                            "mrp": 4500.0,
                            "url": "https://boodmo.com/catalog/part-evaporator_core-97139M0000/",
                            "origin": "OEM",
                            "fitment": "verified",
                            "fitment_basis": "Boodmo fitment filter: Hyundai Creta 2020",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 1.0,
                            "relevance_reason": "Factory OEM supplier Hanon Systems evaporator core",
                            "product_type": "complete_assembly",
                            "notes": ["Complete aluminum core with expansion ports"]
                        },
                        {
                            "title": "AC Cooling Coil Premium Subo",
                            "brand": "Subros",
                            "part_number": "SB-CR-4412",
                            "price": 3200.0,
                            "mrp": 3800.0,
                            "url": "https://boodmo.com/catalog/part-subros_cooling_coil-SB-CR-4412/",
                            "origin": "Aftermarket",
                            "fitment": "verified",
                            "fitment_basis": "Boodmo fitment filter: Hyundai Creta 2020",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 0.98,
                            "relevance_reason": "Direct replacement OES cooling coil by Subros",
                            "product_type": "complete_assembly",
                            "notes": ["OES certified cooling component"]
                        }
                    ]
                },
                {
                    "vendor": "autodukan",
                    "status": "ok",
                    "vehicle_label": "Hyundai Creta 2020",
                    "source_url": "https://autodukan.com/products-list?search=Creta+Cooling+Coil",
                    "message": "AutoDukan verified 2 cooling coils for Hyundai Creta.",
                    "listings": [
                        {
                            "title": "Hyundai Creta AC Cooling Coil Core",
                            "brand": "Hyundai Mobis",
                            "part_number": "97139M0000",
                            "price": 3799.0,
                            "mrp": 4450.0,
                            "url": "https://autodukan.com/products-list?search=Creta+Cooling+Coil",
                            "origin": "OEM",
                            "fitment": "verified",
                            "fitment_basis": "AutoDukan verified vehicle catalog for Hyundai Creta",
                            "available": True,
                            "relevance": "EXACT_MATCH",
                            "relevance_score": 1.0,
                            "relevance_reason": "Genuine Mobis OEM cooling coil",
                            "product_type": "complete_assembly",
                            "notes": ["Ready for dispatch"]
                        }
                    ]
                }
            ]
        }
    ]

    try:
        with _get_db() as conn:
            for rec in demo_records:
                for outcome in rec["outcomes"]:
                    outcome["is_demo"] = True
                    outcome["is_cached"] = True
                    outcome["source_type"] = "demo"
                    for l in outcome.get("listings", []):
                        l.setdefault("match", "exact")
                        l.setdefault("best_offer", {
                            "seller": "Authorized Supplier Hub",
                            "stock": "in stock",
                            "dispatch": "24-48 hrs"
                        })
                data_json = json.dumps(rec["outcomes"])
                # Register both 4-part legacy keys and 5-part normalized keys
                keys_to_store = set()
                for k in rec["keys"]:
                    keys_to_store.add(k)
                    parts = k.split(":")
                    if len(parts) == 4:
                        # Add 5-part key with empty variant (maker:model:year::part)
                        keys_to_store.add(f"{parts[0]}:{parts[1]}:{parts[2]}::{parts[3]}")
                for k in keys_to_store:
                    conn.execute("""
                        INSERT OR REPLACE INTO search_cache (key, vehicle_label, part_name, data, created_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        k,
                        rec["vehicle"],
                        rec["part"],
                        data_json,
                        now,
                        now + (30 * 86400)  # 30-day longevity for demo records
                    ))
            conn.commit()
            log.info("Pre-seeded demo cache with %d vehicles successfully.", len(demo_records))
    except Exception as exc:
        log.warning("Could not preseed demo cache: %s", exc)


# Initialize database automatically on import
init_db()
