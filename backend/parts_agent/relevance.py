"""Product Relevance Classification and Intelligent Ranking Engine.

Performs semantic classification between the customer's query/intent and supplier
product listings. Prevents minor components (such as a ₹32 drain hose or grommet)
from masquerading as complete assemblies (such as a sunroof or bumper) or outranking
them during price sorting.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

log = logging.getLogger(__name__)

# Relevance category tiers
EXACT_MATCH = "EXACT_MATCH"
CLOSE_MATCH = "CLOSE_MATCH"
COMPONENT = "COMPONENT"
ACCESSORY = "ACCESSORY"
NEEDS_REVIEW = "NEEDS_REVIEW"
INCOMPATIBLE = "INCOMPATIBLE"
IRRELEVANT = "IRRELEVANT"

RELEVANCE_TIERS = {
    EXACT_MATCH: 1,
    CLOSE_MATCH: 2,
    COMPONENT: 3,
    ACCESSORY: 4,
    NEEDS_REVIEW: 5,
    INCOMPATIBLE: 6,
    IRRELEVANT: 7,
}

# Known automotive vehicle manufacturers and their common aliases
VEHICLE_MAKERS: Dict[str, Tuple[str, ...]] = {
    "tata": ("tata", "tata motors"),
    "maruti": ("maruti", "maruti suzuki", "suzuki"),
    "hyundai": ("hyundai",),
    "mahindra": ("mahindra",),
    "honda": ("honda",),
    "toyota": ("toyota",),
    "kia": ("kia",),
    "volkswagen": ("volkswagen", "vw"),
    "skoda": ("skoda",),
    "audi": ("audi",),
    "bmw": ("bmw",),
    "mercedes": ("mercedes", "mercedes benz", "mercedes-benz", "benz"),
    "ford": ("ford",),
    "renault": ("renault",),
    "nissan": ("nissan", "datsun"),
    "mg": ("mg", "mg motor", "morris garages"),
    "fiat": ("fiat", "abarth"),
    "chevrolet": ("chevrolet", "chevy"),
    "jeep": ("jeep",),
    "volvo": ("volvo",),
    "jaguar": ("jaguar",),
    "land rover": ("land rover", "range rover"),
    "porsche": ("porsche",),
    "peugeot": ("peugeot",),
    "citroen": ("citroen",),
}

# Distinctive vehicle models for major manufacturers
MAKER_MODELS: Dict[str, Tuple[str, ...]] = {
    "tata": ("safari", "harrier", "nexon", "altroz", "tiago", "tigor", "punch", "hexa", "indica", "indigo", "sumo", "sierra", "aria", "zest", "bolt", "nano", "curvv"),
    "audi": ("a3", "a4", "a6", "a8", "q2", "q3", "q5", "q7", "q8", "tt", "rs3", "rs4", "rs5", "rs6", "rs7", "etron", "e-tron"),
    "bmw": ("3 series", "5 series", "7 series", "x1", "x3", "x5", "x7", "m3", "m5"),
    "mercedes": ("c-class", "e-class", "s-class", "gla", "glc", "gle", "gls", "a-class", "cla"),
    "maruti": ("swift", "baleno", "dzire", "brezza", "ertiga", "alto", "wagonr", "wagon r", "celerio", "ignis", "ciaz", "xl6", "invicto", "fronx", "jimny", "grand vitara", "zen", "omni", "eeco", "s-cross", "scross"),
    "hyundai": ("creta", "i20", "i10", "grand i10", "venue", "verna", "tucson", "santro", "alcazar", "aura", "elantra", "exter", "kona", "ioniq"),
    "mahindra": ("scorpio", "scorpio-n", "scorpio n", "scorpio classic", "xuv700", "xuv 700", "xuv500", "xuv 500", "xuv300", "xuv 300", "xuv3xo", "thar", "bolero", "bolero neo", "marazzo", "xylo", "kuv100"),
    "toyota": ("innova", "innova crysta", "innova hycross", "fortuner", "glanza", "urban cruiser", "hyryder", "camry", "hilux", "corolla", "yaris", "etios"),
    "honda": ("city", "amaze", "elevate", "civic", "accord", "cr-v", "crv", "wr-v", "wrv", "jazz", "brio"),
    "volkswagen": ("polo", "vento", "virtus", "taigun", "tiguan", "passat", "jetta", "ameo"),
    "skoda": ("slavia", "kushaq", "octavia", "superb", "kodiaq", "rapid", "laura", "fabia"),
    "kia": ("seltos", "sonet", "carens", "carnival", "ev6"),
    "ford": ("ecosport", "endeavour", "figo", "aspire", "freestyle", "fiesta"),
    "renault": ("kwid", "triber", "kiger", "duster", "lodgy"),
    "nissan": ("magnite", "kicks", "terrano", "micra", "sunny"),
    "mg": ("hector", "astor", "gloster", "zs ev", "comet"),
}

# Common automotive hardware, consumable, and minor accessory words
ACCESSORY_TERMS = {
    "grommet", "hose", "pipe", "tube", "seal", "gasket", "o-ring", "oring",
    "washer", "nut", "bolt", "screw", "stud", "rivet", "fastener", "clip",
    "clamp", "bracket", "holder", "support", "mount", "mounting", "damper",
    "cap", "cover", "lid", "plug", "badge", "emblem", "sticker", "roller blind",
    "roll blind", "blind", "slide shoe", "shoe", "wire", "harness", "fuse",
    "relay", "bush", "bushing", "shim", "spacer", "strap", "retaining ring",
    "snap ring", "collar", "weatherstrip", "beading", "strip", "drain hose",
    "drain pipe", "nozzle", "hose assy", "drain", "bracket assy", "retainer",
    "return spring", "pedal spring"
}

# Major functional components that belong to an assembly
COMPONENT_TERMS = {
    "glass", "mechanism", "frame", "motor", "regulator", "actuator", "rail",
    "track", "panel", "housing", "unit", "switch", "sensor", "handle", "mirror",
    "caliper", "disc", "rotor", "drum", "pad", "pads", "strut", "shock",
    "shock absorber", "spring", "arm", "control arm", "link", "stabilizer link",
    "rod", "tie rod", "rack", "pinion", "pump", "core", "pulley", "belt",
    "chain", "gear", "shaft", "bearing", "hub", "valve", "piston", "tank",
    "reservoir", "cooler", "intercooler", "radiator", "condenser", "compressor",
    "alternator", "starter", "wiper arm", "wiper blade", "wiper motor", "bulb",
    "light bulb", "lens", "reflector", "grille", "grill", "diffuser", "spoiler",
    "crossmember", "knuckle", "subframe", "slider", "element", "cable", "cylinder",
    "flywheel", "fork", "pressure plate"
}

# Common assembly synonyms and families
SYNONYM_FAMILIES = {
    "sunroof": ["sunroof", "panorama roof", "panoramic roof", "panoramicroof", "moonroof", "panoramaroof", "sun roof", "panorama"],
    "oil filter": ["oil filter", "filter element", "oil filter element", "engine oil filter"],
    "headlight": ["headlight", "headlamp", "front light", "head lamp"],
    "taillight": ["taillight", "taillamp", "rear light", "tail lamp"],
    "brake pad": ["brake pad", "brake pads", "disc brake pad", "front brake pad", "rear brake pad"],
    "bumper": ["bumper", "front bumper", "rear bumper", "bumper bar"],
    "clutch plate": [
        "clutch plate", "clutch plates", "clutch disc", "clutch disk", "friction plate",
        "disc assy clutch", "clutch disc assy", "clutch set", "clutch kit", "clutch assy", "clutch"
    ],
    "clutch set": ["clutch set", "clutch kit", "clutch plate", "clutch disc", "clutch assembly"],
    "clutch": ["clutch", "clutch plate", "clutch disc", "clutch set", "clutch kit", "clutch assembly"],
    "cooling coil": [
        "cooling coil", "cooling coils", "evaporator", "evaporator coil", "ac cooling coil",
        "ac coil", "evaporator assy", "evaporator unit", "cooling unit"
    ],
    "evaporator": [
        "evaporator", "cooling coil", "cooling coils", "evaporator coil", "ac cooling coil",
        "ac coil", "evaporator assy", "evaporator unit", "cooling unit"
    ],
    "transmission": [
        "transmission", "gearbox", "gear box", "manual transmission", "trans assy"
    ],
    "gearbox": [
        "gearbox", "gear box", "transmission", "manual transmission", "trans assy"
    ],
}


def _clean_text(text: Optional[str]) -> str:
    """Normalize text by converting to lowercase and stripping punctuation."""
    if not text:
        return ""
    # Normalize hyphens and commas to spaces for matching
    cleaned = re.sub(r"[-_,/\\()]+", " ", str(text).lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def get_canonical_maker(maker: Optional[str]) -> str:
    """Normalize vehicle manufacturer to canonical identifier."""
    if not maker:
        return ""
    m_clean = _clean_text(maker)
    for canon, aliases in VEHICLE_MAKERS.items():
        if m_clean == canon or any(re.search(rf"\b{re.escape(a)}\b", m_clean) for a in aliases):
            return canon
    return m_clean


def check_vehicle_compatibility(
    product_title: str,
    product_brand: str = "",
    target_maker: str = "",
    target_model: str = ""
) -> Tuple[str, str]:
    """Verify vehicle maker and model compatibility.

    Returns:
        (status, reason) where status is:
        - "incompatible": Candidate belongs to a different vehicle manufacturer or incompatible model.
        - "verified": Candidate explicitly matches target maker/model.
        - "unverified": Candidate has no contradictory vehicle brand (e.g. generic part or OEM).
    """
    canon_target = get_canonical_maker(target_maker)
    if not canon_target:
        return ("unverified", "No target vehicle maker specified.")

    text = f"{product_title} {product_brand}".lower()
    target_aliases = VEHICLE_MAKERS.get(canon_target, (canon_target,))
    target_models = MAKER_MODELS.get(canon_target, ())

    matches_target_maker = any(re.search(rf"\b{re.escape(a)}\b", text) for a in target_aliases)
    matches_target_model = False
    if target_model:
        t_mod = target_model.lower().strip()
        matches_target_model = bool(re.search(rf"\b{re.escape(t_mod)}\b", text))
    elif target_models:
        matches_target_model = any(re.search(rf"\b{re.escape(m)}\b", text) for m in target_models)

    matches_target = matches_target_maker or matches_target_model

    # 1. Check for contradictory vehicle makers
    for other_canon, other_aliases in VEHICLE_MAKERS.items():
        if other_canon == canon_target:
            continue
        if any(re.search(rf"\b{re.escape(a)}\b", text) for a in other_aliases):
            if not matches_target:
                return (
                    "incompatible",
                    f"Incompatible vehicle brand: requested {canon_target.title()}, but product is for {other_canon.title()}."
                )

    # 2. Check for contradictory vehicle models
    for other_canon, other_models in MAKER_MODELS.items():
        if other_canon == canon_target:
            continue
        if any(re.search(rf"\b{re.escape(m)}\b", text) for m in other_models):
            if not matches_target:
                return (
                    "incompatible",
                    f"Incompatible vehicle model: product is for {other_canon.title()} model, requested {canon_target.title()}."
                )

    if matches_target:
        if target_model and matches_target_model:
            return ("verified", f"Verified match for {canon_target.title()} {target_model.title()}.")
        return ("verified", f"Verified match for {canon_target.title()}.")

    return ("unverified", "Generic automotive product - vehicle compatibility unconfirmed.")


def parse_query_intent(query: str, requested_part: str = "") -> Dict[str, Any]:
    """Analyze the customer's request to identify target product and desired granularity."""
    target_raw = requested_part or query or ""
    # Detect maker and model from query or requested part
    text_full = f"{query} {requested_part}".lower()
    inferred_maker = ""
    for canon, aliases in VEHICLE_MAKERS.items():
        if any(re.search(rf"\b{re.escape(a)}\b", text_full) for a in aliases):
            inferred_maker = canon
            break
    inferred_model = ""
    if inferred_maker and inferred_maker in MAKER_MODELS:
        for m in MAKER_MODELS[inferred_maker]:
            if re.search(rf"\b{re.escape(m)}\b", text_full):
                inferred_model = m
                break
    elif not inferred_maker:
        for canon, models in MAKER_MODELS.items():
            for m in models:
                if re.search(rf"\b{re.escape(m)}\b", text_full):
                    inferred_maker = canon
                    inferred_model = m
                    break
            if inferred_maker:
                break

    # Remove maker/model/year/filler words
    stopwords = {
        "find", "need", "looking", "for", "a", "an", "the", "car", "vehicle", "automobile",
        "spare", "part", "parts", "genuine", "oem", "chahiye", "ka", "ki", "ke", "hai",
        "tata", "hyundai", "maruti", "mahindra", "honda", "toyota", "kia", "creta",
        "harrier", "nexon", "swift", "baleno", "i20", "seltos", "scorpio", "xuv700"
    }

    raw_tokens = [w for w in _clean_text(target_raw).split() if w not in stopwords and not w.isdigit()]
    # Normalize common automotive plurals (plates -> plate, pads -> pad, filters -> filter)
    norm_tokens = []
    for w in raw_tokens:
        if w.endswith("ies") and len(w) > 4:
            norm_tokens.append(w[:-3] + "y")
        elif any(w.endswith(suf) for suf in ("ches", "shes", "xes", "zes", "sses")):
            norm_tokens.append(w[:-2])
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            norm_tokens.append(w[:-1])
        else:
            norm_tokens.append(w)
    target_clean = " ".join(norm_tokens)

    # Check if customer explicitly requested an accessory
    specified_accessory = None
    for acc in sorted(ACCESSORY_TERMS, key=lambda x: -len(x)):
        if acc in target_clean:
            specified_accessory = acc
            break

    # Check if customer explicitly requested an individual component
    specified_component = None
    for comp in sorted(COMPONENT_TERMS, key=lambda x: -len(x)):
        if comp in target_clean:
            specified_component = comp
            break

    # Determine desired product type
    if specified_accessory:
        desired_type = "accessory"
    elif specified_component:
        desired_type = "component"
    else:
        desired_type = "complete_assembly"

    # Identify product family
    product_family = target_clean
    for family, aliases in SYNONYM_FAMILIES.items():
        if any(alias in target_clean for alias in aliases):
            product_family = family
            break

    return {
        "target_raw": target_raw,
        "target_clean": target_clean,
        "desired_type": desired_type,
        "specified_accessory": specified_accessory,
        "specified_component": specified_component,
        "product_family": product_family,
        "inferred_maker": inferred_maker,
        "inferred_model": inferred_model,
    }


def classify_product(
    product_title: str,
    query: str = "",
    requested_part: str = "",
    category: str = "",
    part_number: str = "",
    target_maker: str = "",
    target_model: str = "",
    product_brand: str = "",
) -> Dict[str, Any]:
    """Deterministically classify an extracted product against customer query intent.

    Returns:
        {
            "relevance": EXACT_MATCH | CLOSE_MATCH | COMPONENT | ACCESSORY | NEEDS_REVIEW | INCOMPATIBLE | IRRELEVANT,
            "relevance_score": float (0.0 to 1.0),
            "relevance_reason": str,
            "product_type": "complete_assembly" | "component" | "accessory" | "incompatible" | "irrelevant"
        }
    """
    intent = parse_query_intent(query, requested_part)
    title_norm = _clean_text(product_title)
    cat_norm = _clean_text(category)

    # 0. Check for non-product website navigation, policy pages, marketing text, or seller links
    NON_PART_PATTERNS = re.compile(
        r"\b(policy|policies|terms\s+of\s+use|terms|disclaimer|privacy|seller|become\s+a\s+seller|"
        r"become\s+an\s+autodukan\s+seller|services|customer\s+care|contact\s+us|about\s+us|faq|login|"
        r"sign\s*in|register|cart|checkout|track\s*order|download\s+app|oes\s+brands|oem\s+brands|"
        r"we'd\s+love\s+to\s+hear|more\s+than\s+parts|all\s+rights\s+reserved|feedback|refund|cancellation)\b",
        re.IGNORECASE
    )
    if NON_PART_PATTERNS.search(product_title) or NON_PART_PATTERNS.search(title_norm):
        return {
            "relevance": IRRELEVANT,
            "relevance_score": 0.0,
            "relevance_reason": f"Website navigation or policy link ('{product_title}'), not an automotive part.",
            "product_type": "irrelevant",
        }

    # 0b. Vehicle Brand & Model Compatibility Validation
    eff_maker = target_maker or intent.get("inferred_maker") or ""
    eff_model = target_model or intent.get("inferred_model") or ""
    if eff_maker:
        compat_status, compat_reason = check_vehicle_compatibility(
            product_title=product_title,
            product_brand=product_brand,
            target_maker=eff_maker,
            target_model=eff_model,
        )
        if compat_status == "incompatible":
            return {
                "relevance": INCOMPATIBLE,
                "relevance_score": 0.0,
                "relevance_reason": compat_reason,
                "product_type": "incompatible",
            }

    # 1. Detect if the product itself is an accessory or hardware item
    detected_accessory = None
    for acc in sorted(ACCESSORY_TERMS, key=lambda x: -len(x)):
        # Match word boundaries or isolated phrase
        pattern = rf"\b{re.escape(acc)}\b"
        if re.search(pattern, title_norm):
            detected_accessory = acc
            break

    # 2. Detect if the product itself is an individual component
    detected_component = None
    for comp in sorted(COMPONENT_TERMS, key=lambda x: -len(x)):
        pattern = rf"\b{re.escape(comp)}\b"
        if re.search(pattern, title_norm):
            detected_component = comp
            break

    # Domain adjustments:
    # 1. In clutch context, "cover" (e.g. Clutch Cover / Cover Assy) is the pressure plate, NOT a minor accessory.
    #    Also, "clutch set" / "clutch kit" containing a cover plate is not an accessory.
    if "clutch" in title_norm or intent.get("product_family") in ("clutch plate", "clutch", "clutch set"):
        if detected_accessory == "cover":
            detected_accessory = None
        # 2. "disc" is the primary synonym for clutch plate, NOT an unwanted sub-component
        if detected_component in ("disc", "rotor"):
            detected_component = None

    # 3. Check for complete assembly signifiers
    is_complete_assembly = bool(re.search(r"\b(assembly|assy|kit|set|complete|unit|system)\b", title_norm))

    # Determine product's inherent type
    if detected_accessory:
        item_product_type = "accessory"
    elif detected_component and not (intent["product_family"] in title_norm and is_complete_assembly and not detected_accessory):
        item_product_type = "component"
    else:
        item_product_type = "complete_assembly"

    desired_type = intent["desired_type"]
    family = intent["product_family"]
    target_clean = intent["target_clean"]

    # -------------------------------------------------------------
    # CASE 1: Customer explicitly requested an ACCESSORY
    # (e.g. "sunroof drain hose", "oil filter gasket", "caliper clip")
    # -------------------------------------------------------------
    if desired_type == "accessory":
        spec_acc = intent["specified_accessory"]
        if spec_acc and spec_acc in title_norm:
            return {
                "relevance": EXACT_MATCH,
                "relevance_score": 0.96,
                "relevance_reason": f"Exact match for requested accessory: '{spec_acc}'.",
                "product_type": "accessory",
            }
        if detected_accessory:
            return {
                "relevance": CLOSE_MATCH,
                "relevance_score": 0.85,
                "relevance_reason": f"Related accessory ({detected_accessory}) in the requested category.",
                "product_type": "accessory",
            }
        return {
            "relevance": COMPONENT,
            "relevance_score": 0.70,
            "relevance_reason": "Complete assembly/component returned for specific accessory query.",
            "product_type": item_product_type,
        }

    # -------------------------------------------------------------
    # CASE 2: Customer explicitly requested a COMPONENT
    # (e.g. "sunroof glass", "sunroof mechanism", "brake rotor")
    # -------------------------------------------------------------
    if desired_type == "component":
        spec_comp = intent["specified_component"]
        if spec_comp and spec_comp in title_norm:
            # e.g. Customer asked for "sunroof glass", and item is "GLASS ASSY-PANORAMAROOF"
            return {
                "relevance": EXACT_MATCH,
                "relevance_score": 0.98,
                "relevance_reason": f"Exact match for requested component: '{spec_comp}'.",
                "product_type": "component",
            }
        if detected_component:
            return {
                "relevance": CLOSE_MATCH,
                "relevance_score": 0.82,
                "relevance_reason": f"Alternative component ({detected_component}) in the requested assembly.",
                "product_type": "component",
            }
        if detected_accessory:
            return {
                "relevance": ACCESSORY,
                "relevance_score": 0.75,
                "relevance_reason": f"Minor hardware/accessory ({detected_accessory}) related to component.",
                "product_type": "accessory",
            }
        return {
            "relevance": COMPONENT,
            "relevance_score": 0.75,
            "relevance_reason": "Broader assembly returned for specific component query.",
            "product_type": item_product_type,
        }

    # -------------------------------------------------------------
    # CASE 3: Customer requested a GENERIC / COMPLETE ASSEMBLY
    # (e.g. "Sunroof", "Headlight", "Bumper", "Oil Filter")
    # -------------------------------------------------------------
    # If the product is an accessory/hardware piece:
    if detected_accessory and detected_accessory not in target_clean:
        return {
            "relevance": ACCESSORY,
            "relevance_score": 0.95,
            "relevance_reason": f"Component/accessory ({detected_accessory}) of the requested {family}, not a complete assembly.",
            "product_type": "accessory",
        }

    # If the product contains an individual sub-component (e.g. glass, mechanism, frame, motor):
    # (Skip if the product is explicitly a complete kit or set representing the family, e.g. "Clutch Kit (Disc+Pressure Plate)")
    if (
        detected_component
        and detected_component not in target_clean
        and not (is_complete_assembly and any(k in title_norm for k in ("kit", "set", "complete kit", "complete set")))
    ):
        return {
            "relevance": COMPONENT,
            "relevance_score": 0.92,
            "relevance_reason": f"Sub-assembly component ({detected_component}) of the {family}, not a complete replacement assembly.",
            "product_type": "component",
        }

    # Check if the product represents the primary complete assembly
    # Check for direct or synonym family presence
    aliases = SYNONYM_FAMILIES.get(family, [family, target_clean])
    family_match = any(alias in title_norm or (alias in cat_norm and "assy" in title_norm) for alias in aliases)

    if family_match:
        if is_complete_assembly or any(term in title_norm for term in ("roof", "filter", "pad", "light", "clutch", "disc", "plate", "bumper")):
            return {
                "relevance": EXACT_MATCH,
                "relevance_score": 0.97,
                "relevance_reason": f"Direct complete assembly match for requested '{target_clean or family}'.",
                "product_type": "complete_assembly",
            }
        return {
            "relevance": CLOSE_MATCH,
            "relevance_score": 0.88,
            "relevance_reason": f"Close match for requested '{target_clean or family}'.",
            "product_type": "complete_assembly",
        }

    # Check if title at least contains tokens of the target
    target_tokens = set(target_clean.split())
    title_tokens = set(title_norm.split())
    if target_tokens and target_tokens.issubset(title_tokens):
        return {
            "relevance": CLOSE_MATCH,
            "relevance_score": 0.85,
            "relevance_reason": f"Matches query terms: '{target_clean}'.",
            "product_type": item_product_type,
        }

    # If it is in the category but missing direct title matches
    if cat_norm and any(alias in cat_norm for alias in aliases):
        if detected_component:
            return {
                "relevance": COMPONENT,
                "relevance_score": 0.80,
                "relevance_reason": f"Listed under {category} category as a component.",
                "product_type": "component",
            }
        return {
            "relevance": NEEDS_REVIEW,
            "relevance_score": 0.65,
            "relevance_reason": f"Listed under {category} category but specific product intent is unverified.",
            "product_type": item_product_type,
        }

    # Default fallback
    return {
        "relevance": NEEDS_REVIEW,
        "relevance_score": 0.50,
        "relevance_reason": "Product title does not conclusively match requested product intent.",
        "product_type": item_product_type,
    }


def classify_listings(
    listings: List[Union[Dict[str, Any], Any]],
    query: str = "",
    requested_part: str = "",
    category: str = "",
    target_maker: str = "",
    target_model: str = "",
) -> List[Dict[str, Any]]:
    """Classify an entire list of listings and enrich each with relevance fields."""
    classified = []
    for item in listings:
        if hasattr(item, "to_dict"):
            d = item.to_dict()
        elif isinstance(item, dict):
            d = dict(item)
        else:
            continue

        title = d.get("title") or ""
        brand = d.get("brand") or ""
        item_cat = d.get("part_class") or category or ""
        part_no = d.get("part_number") or ""

        res = classify_product(
            product_title=title,
            query=query,
            requested_part=requested_part,
            category=item_cat,
            part_number=part_no,
            target_maker=target_maker,
            target_model=target_model,
            product_brand=brand,
        )

        d["relevance"] = res["relevance"]
        d["relevance_score"] = res["relevance_score"]
        d["relevance_reason"] = res["relevance_reason"]
        d["product_type"] = res["product_type"]

        if res["relevance"] == INCOMPATIBLE:
            d["fitment"] = "incompatible"
            d["match"] = "incompatible"
        else:
            # Keep legacy match field aligned
            d["match"] = "exact" if res["relevance"] in (EXACT_MATCH, CLOSE_MATCH) else "related"

        classified.append(d)

    return classified


def rank_listings(
    listings: List[Dict[str, Any]],
    price_order: Optional[str] = "asc"  # "asc" | "desc" | None
) -> List[Dict[str, Any]]:
    """Sort listings strictly preserving relevance tiers so price never overrides relevance.

    Priority:
        1. EXACT_MATCH
        2. CLOSE_MATCH
        3. COMPONENT
        4. ACCESSORY
        5. NEEDS_REVIEW
        (IRRELEVANT and INCOMPATIBLE products are filtered out)

    Within each tier, products are sorted by price (asc or desc).
    """
    # Exclude IRRELEVANT and INCOMPATIBLE products
    valid = [
        l for l in listings
        if l.get("relevance") not in (IRRELEVANT, INCOMPATIBLE)
        and l.get("fitment") != "incompatible"
    ]

    # Group by relevance tier
    tiers: Dict[str, List[Dict[str, Any]]] = {
        EXACT_MATCH: [],
        CLOSE_MATCH: [],
        COMPONENT: [],
        ACCESSORY: [],
        NEEDS_REVIEW: [],
    }

    for item in valid:
        rel = item.get("relevance", NEEDS_REVIEW)
        if rel not in tiers:
            rel = NEEDS_REVIEW
        tiers[rel].append(item)

    # Sort each tier individually by price if requested
    def sort_tier(tier_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not price_order:
            return tier_items
        reverse = (price_order == "desc")

        def numeric_price(x):
            try:
                val = x.get("price")
                return float(val) if val is not None else float("inf")
            except (ValueError, TypeError):
                return float("inf")

        # Items with prices first (sorted numerically), items without price last
        priced = [x for x in tier_items if x.get("price") is not None]
        unpriced = [x for x in tier_items if x.get("price") is None]

        priced.sort(key=numeric_price, reverse=reverse)
        return priced + unpriced

    ranked: List[Dict[str, Any]] = []
    for tier_key in (EXACT_MATCH, CLOSE_MATCH, COMPONENT, ACCESSORY, NEEDS_REVIEW):
        ranked.extend(sort_tier(tiers[tier_key]))

    return ranked
