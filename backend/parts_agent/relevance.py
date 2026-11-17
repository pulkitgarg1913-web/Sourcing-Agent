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
WRONG_PART_TYPE = "WRONG_PART_TYPE"

# Part-Family Specific Classifications
EXACT_PART_MATCH = "EXACT_PART_MATCH"
PART_ALIAS_MATCH = "PART_ALIAS_MATCH"
RELATED_COMPONENT = "RELATED_COMPONENT"
UNKNOWN = "UNKNOWN"

RELEVANCE_TIERS = {
    EXACT_PART_MATCH: 1,
    EXACT_MATCH: 1,
    PART_ALIAS_MATCH: 2,
    CLOSE_MATCH: 2,
    RELATED_COMPONENT: 3,
    COMPONENT: 3,
    ACCESSORY: 4,
    NEEDS_REVIEW: 5,
    UNKNOWN: 5,
    INCOMPATIBLE: 6,
    WRONG_PART_TYPE: 7,
    IRRELEVANT: 8,
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
    target_model: str = "",
    target_year: str = "",
    target_variant: str = "",
    catalog_vehicle: str = "",
    catalog_year: str = "",
) -> Tuple[str, str]:
    """Verify vehicle maker, model, and year compatibility.

    Returns:
        (status, reason) where status is:
        - "incompatible": Candidate belongs to a different vehicle manufacturer or incompatible model.
        - "partial": Candidate is for the compatible model but different year or variant generation.
        - "verified": Candidate explicitly matches target maker, model, and year.
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
        # Check year granularity if target year and catalog/title year exist
        eff_cat_year = catalog_year
        if not eff_cat_year and catalog_vehicle:
            y_m = re.search(r"\b(19\d\d|20\d\d)\b", catalog_vehicle)
            if y_m:
                eff_cat_year = y_m.group(1)
        if not eff_cat_year:
            y_m = re.search(r"\b(19\d\d|20\d\d)\b", text)
            if y_m:
                eff_cat_year = y_m.group(1)

        if target_year and eff_cat_year and str(target_year) != str(eff_cat_year):
            cat_name = f"{canon_target.title()} {target_model.title() if target_model else ''} {eff_cat_year}".strip()
            return (
                "partial",
                f"Verified for {cat_name} catalog; verify {target_year} compatibility before purchase"
            )

        if target_model and matches_target_model:
            yr_str = f" {target_year}" if target_year else ""
            return ("verified", f"Verified match for {canon_target.title()} {target_model.title()}{yr_str}.")
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

    # Domain adjustments for requested intent:
    # In fuel pump context, "pump" is the primary assembly requested, NOT a sub-component.
    if any(p in target_clean for p in ("fuel pump", "petrol pump", "diesel pump", "hpfp", "feed pump")):
        if specified_component in ("pump",):
            specified_component = None

    # In brake pad context, "pad" is the primary assembly requested, NOT a sub-component.
    if any(p in target_clean for p in ("brake pad", "disc pad", "brake pads", "disc pads")):
        if specified_component in ("pad", "pads"):
            specified_component = None

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


# Comprehensive Automotive Part Family Definitions
PART_FAMILY_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "fuel_pump": {
        "name": "fuel pump",
        "match_queries": (
            "fuel pump", "fuel delivery pump", "fuel feed pump", "petrol pump",
            "diesel pump", "fuel module", "fuel sender", "high pressure fuel pump",
            "hpfp", "feed pump", "fuel delivery module", "fuel sending unit",
        ),
        "family_keywords": ("fuel", "pump", "hpfp", "sender"),
        "exact_patterns": (
            r"\b(fuel\s+pump|fuel\s+delivery\s+(?:module|unit)|petrol\s+pump|diesel\s+pump|hpfp|fuel\s+feed\s+pump|feed\s+pump|high\s+pressure\s+(?:fuel\s+)?pump)\b",
        ),
        "alias_patterns": (
            r"\b(fuel\s+(?:sender|sending\s+unit|pump\s+module|sub\s*assy\s+fuel\s+pump)|module\s+assy\s+fuel)\b",
        ),
        "related_component_patterns": (
            r"\b(fuel\s+pump\s+(?:motor|core)|pump\s+motor|fuel\s+level\s+sensor|fuel\s+gauge\s+sensor|float\s+fuel\s+pump|fuel\s+strainer|fuel\s+pump\s+filter)\b",
        ),
        "accessory_patterns": (
            r"\b(fuel\s+pump\s+.*?(?:bracket|clamp|seal|gasket|o-ring|oring|relay|harness|connector|wiring|retainer|lock\s*ring|lockring|mounting))\b",
        ),
        "conflicting_patterns": (
            r"\b(separator|fuse\s*box|oil\s+filler|filler\s+cap|oil\s+filter|air\s+filter|cabin\s+filter|brake|spark\s*plug|clutch|bumper|headlight|wiper|strut|shock|radiator|intercooler|exhaust|muffler|silencer|fuel\s+tank\b(?!.*pump)|fuel\s+pipe\b(?!.*pump)|fuel\s+hose\b(?!.*pump)|fuel\s+line\b(?!.*pump)|fuel\s+rail\b(?!.*pump)|fuel\s+filter\b(?!.*pump)|injector\b(?!.*pump)|bracket\b(?!.*fuel\s*pump)|clamp\b(?!.*fuel\s*pump)|cap\b(?!.*fuel\s*pump)|packing\b(?!.*fuel\s*pump))\b",
        ),
    },
    "air_filter": {
        "name": "air filter",
        "match_queries": (
            "air filter", "engine air filter", "air cleaner", "air cleaner element",
            "filter air", "air intake filter",
        ),
        "family_keywords": ("air", "filter", "cleaner"),
        "exact_patterns": (
            r"\b(air\s+filter|air\s+cleaner\s+element|filter\s+element\s+air|engine\s+air\s+filter|filter\s+air\s+cleaner|element\s+air\s+cleaner)\b",
        ),
        "alias_patterns": (
            r"\b(air\s+cleaner|filter\s+assy\s+air|filter\s+sub\s*assy\s+air|air\s+filter\s+insert|air\s+intake\s+filter)\b",
        ),
        "related_component_patterns": (
            r"\b(air\s+filter\s+box|air\s+cleaner\s+case|air\s+box|air\s+duct|intake\s+hose)\b",
        ),
        "accessory_patterns": (
            r"\b(air\s+filter\s+(?:clamp|clip|bracket|seal|gasket))\b",
        ),
        "conflicting_patterns": (
            r"\b(oil\s+filler|packing\s+cap|oil\s+cap|oil\s+filter|fuel\s+filter|cabin\s+filter|ac\s+filter|pollen\s+filter|spark\s*plug|brake|clutch|separator|fuse\s*box|bumper|headlight|wiper|shock|fuel\s+pump|cap\b(?!.*air\s*(?:filter|cleaner))|bracket\b(?!.*air\s*(?:filter|cleaner))|clamp\b(?!.*air\s*(?:filter|cleaner))|cover\b(?!.*air\s*(?:filter|cleaner))|hose\b(?!.*air\s*(?:filter|cleaner))|packing\b(?!.*air\s*(?:filter|cleaner)))\b",
        ),
    },
    "oil_filter": {
        "name": "oil filter",
        "match_queries": (
            "oil filter", "engine oil filter", "lube filter", "lube oil filter",
            "oil filter element", "filter oil", "oil cartridge",
        ),
        "family_keywords": ("oil", "filter", "lube"),
        "exact_patterns": (
            r"\b(oil\s+filter|engine\s+oil\s+filter|lube\s+filter|lube\s+oil\s+filter|oil\s+filter\s+element|filter\s+cartridge\s+oil|filter\s+oil|filter\s+element\s+oil)\b",
        ),
        "alias_patterns": (
            r"\b(filter\s+assy\s+oil|filter\s+sub\s*assy\s+oil|spin-on\s+oil\s+filter)\b",
        ),
        "related_component_patterns": (
            r"\b(oil\s+filter\s+housing|oil\s+filter\s+cap|oil\s+cooler|oil\s+filter\s+bracket|filter\s+head)\b",
        ),
        "accessory_patterns": (
            r"\b(oil\s+filter\s+.*?(?:gasket|o-ring|oring|seal|washer|drain\s+plug|drain\s+gasket))\b",
        ),
        "conflicting_patterns": (
            r"\b(air\s+filter|cabin\s+filter|fuel\s+filter|fuel\s+pump|brake|spark\s*plug|clutch|oil\s+filler\s+cap|oil\s+pan|oil\s+sump|oil\s+pump\b(?!.*filter)|dipstick|separator|fuse\s*box|bracket\b(?!.*oil\s*filter)|clamp\b(?!.*oil\s*filter)|cap\b(?!.*oil\s*filter)|packing\b(?!.*oil\s*filter))\b",
        ),
    },
    "brake_pad": {
        "name": "brake pad",
        "match_queries": (
            "brake pad", "brake pads", "disc brake pad", "front brake pad",
            "rear brake pad", "brake pad set", "disc pad", "front pad set", "rear pad set",
        ),
        "family_keywords": ("pad", "pads"),
        "exact_patterns": (
            r"\b(brake\s+pads?|disc\s+pads?|disc\s+brake\s+pads?|pad\s+set\s+(?:disc\s+)?brake|brake\s+pad\s+set|pads?,\s*brake)\b",
        ),
        "alias_patterns": (
            r"\b(front\s+pad\s+set|rear\s+pad\s+set|brake\s+lining\s+pad|friction\s+pads?|pad\s+kit\s+disc\s+brake)\b",
        ),
        "related_component_patterns": (
            r"\b(brake\s+pad\s+wear\s+sensor|brake\s+wear\s+indicator|pad\s+sensor)\b",
        ),
        "accessory_patterns": (
            r"\b(brake\s+pad\s+.*?(?:clip|shim|pin|spring|retaining\s+clip|hardware\s+kit))\b",
        ),
        "conflicting_patterns": (
            r"\b(brake\s+disc|brake\s+rotor|disc\s+rotor|brake\s+drum|brake\s+shoe|brake\s+caliper|caliper\s+assy|caliper\s+bracket|brake\s+booster|master\s+cylinder|brake\s+hose|brake\s+pipe|brake\s+fluid|handbrake\s+cable|abs\s+sensor|clutch|oil\s+filter|air\s+filter|fuel\s+pump|spark\s*plug)\b",
        ),
    },
    "spark_plug": {
        "name": "spark plug",
        "match_queries": ("spark plug", "spark plugs", "sparkplug", "glow plug"),
        "family_keywords": ("plug", "plugs"),
        "exact_patterns": (
            r"\b(spark\s*plugs?|iridium\s+(?:spark\s*)?plugs?|platinum\s+(?:spark\s*)?plugs?|glow\s*plugs?)\b",
        ),
        "alias_patterns": (
            r"\b(ignition\s+plugs?)\b",
        ),
        "related_component_patterns": (
            r"\b(spark\s*plug\s+(?:wire|cable|boot|lead))\b",
        ),
        "accessory_patterns": (
            r"\b(spark\s*plug\s+(?:gap|gasket|seal|tool))\b",
        ),
        "conflicting_patterns": (
            r"\b(ignition\s+coil|coil\s+pack|distributor|plug\s+cap\b(?!.*spark\s*plug)|oil\s+drain\s+plug|drain\s+plug|air\s+filter|oil\s+filter|fuel\s+pump|brake|clutch)\b",
        ),
    },
    "cabin_filter": {
        "name": "cabin filter",
        "match_queries": ("cabin filter", "cabin air filter", "ac filter", "pollen filter"),
        "family_keywords": ("cabin", "ac", "pollen"),
        "exact_patterns": (
            r"\b(cabin\s+filter|cabin\s+air\s+filter|pollen\s+filter|ac\s+filter|a/c\s+filter)\b",
        ),
        "conflicting_patterns": (
            r"\b(engine\s+air\s+filter|oil\s+filter|fuel\s+filter|fuel\s+pump|brake|spark\s*plug)\b",
        ),
    },
    "clutch_plate": {
        "name": "clutch plate",
        "match_queries": ("clutch plate", "clutch disc", "clutch set", "clutch kit", "clutch assembly", "clutch"),
        "family_keywords": ("clutch",),
        "exact_patterns": (
            r"\b(clutch\s+plate|clutch\s+disc|disc\s+assy\s*clutch|clutch\s+set|clutch\s+kit|clutch\s+assy|friction\s+plate|cover\s+assy\s*clutch|clutch\s+cover|pressure\s+plate)\b",
        ),
        "conflicting_patterns": (
            r"\b(brake|oil\s+filter|air\s+filter|fuel\s+pump|spark\s*plug)\b",
        ),
    },
    "headlight": {
        "name": "headlight",
        "match_queries": ("headlight", "head lamp", "head light", "headlamp"),
        "family_keywords": ("headlight", "headlamp", "front light"),
        "exact_patterns": (
            r"\b(head\s*lights?|head\s*lamps?|front\s+light)\b",
        ),
        "conflicting_patterns": (
            r"\b(tail\s*light|tail\s*lamp|fog\s*light|fog\s*lamp|indicator|mirror|bumper|brake|oil\s+filter|air\s+filter|fuel\s+pump)\b",
        ),
    },
    "taillight": {
        "name": "taillight",
        "match_queries": ("taillight", "tail light", "tail lamp", "taillamp", "rear light", "rear lamp"),
        "family_keywords": ("taillight", "taillamp", "rear light"),
        "exact_patterns": (
            r"\b(tail\s*lights?|tail\s*lamps?|rear\s+combination\s+lamp)\b",
        ),
        "conflicting_patterns": (
            r"\b(head\s*light|head\s*lamp|fog\s*lamp|fog\s*light|bumper|mirror|brake|oil\s+filter|air\s+filter|fuel\s+pump)\b",
        ),
    },
    "wiper_blade": {
        "name": "wiper blade",
        "match_queries": ("wiper blade", "wiper blades", "windshield wiper", "wiper"),
        "family_keywords": ("wiper",),
        "exact_patterns": (
            r"\b(wiper\s+blades?|windshield\s+wiper|wiper\s+blade\s+set)\b",
        ),
        "conflicting_patterns": (
            r"\b(wiper\s+motor|wiper\s+linkage|wiper\s+arm\b(?!.*blade)|washer\s+pump|washer\s+nozzle|headlight|brake|oil\s+filter)\b",
        ),
    },
    "shock_absorber": {
        "name": "shock absorber",
        "match_queries": ("shock absorber", "strut", "damper", "shocker"),
        "family_keywords": ("shock", "strut", "damper"),
        "exact_patterns": (
            r"\b(shock\s+absorbers?|struts?|front\s+shock|rear\s+shock|damper)\b",
        ),
        "conflicting_patterns": (
            r"\b(spring\b(?!.*shock)|coil\s+spring|strut\s+mount\b(?!.*strut\s+assembly)|bump\s+stop|control\s+arm|tie\s+rod|brake|oil\s+filter)\b",
        ),
    },
    "sunroof": {
        "name": "sunroof",
        "match_queries": ("sunroof", "panorama roof", "panoramic roof", "moonroof", "sun roof"),
        "family_keywords": ("roof", "sunroof", "panorama", "panoramic"),
        "exact_patterns": (
            r"\b(sunroof|panorama\s*roof|panoramic\s*roof|moonroof|sun\s*roof)\b",
        ),
        "related_component_patterns": (
            r"\b(glass|mechanism|frame|motor|roller\s+blind|slide\s+shoe)\b",
        ),
        "accessory_patterns": (
            r"\b(drain\s+hose|grommet|drain\s+pipe)\b",
        ),
        "conflicting_patterns": (
            r"\b(oil\s+filter|air\s+filter|fuel\s+pump|brake|clutch|spark\s*plug)\b",
        ),
    },
    "side_mirror": {
        "name": "side mirror",
        "match_queries": (
            "side mirror", "side mirrors", "outside mirror", "outside mirrors",
            "orvm", "rear view mirror", "rearview mirror", "door mirror",
            "outside rear view mirror", "outside rearview mirror", "mirror",
        ),
        "family_keywords": ("mirror", "mirrors", "orvm", "glass"),
        "exact_patterns": (
            r"\b(outside\s+(?:rear\s*view\s+)?mirrors?|side\s+(?:door\s+)?mirrors?|door\s+mirrors?|orvm\s+assembly|orvm|rear\s*view\s+mirrors?)\b",
        ),
        "alias_patterns": (
            r"\b(mirror\s+assy|side\s+mirror\s+assy|orvm\s+lh|orvm\s+rh)\b",
        ),
        "related_component_patterns": (
            r"\b(mirror\s+glass(?:\s+plate)?|mirror\s+cover|mirror\s+housing|mirror\s+motor|mirror\s+switch|mirror\s+indicator|mirror\s+cap|mirror\s+base)\b",
        ),
        "accessory_patterns": (
            r"\b(mirror\s+(?:bracket|clamp|gasket|bezel|trim))\b",
        ),
        "conflicting_patterns": (
            r"\b(headlight|taillight|fog\s*light|bumper|brake|clutch|oil\s+filter|air\s+filter|fuel\s+pump|spark\s*plug)\b",
        ),
    },
}


def check_part_family_compatibility(
    product_title: str,
    requested_part: str = "",
    query: str = "",
    category: str = "",
) -> Tuple[str, str]:
    """Validate that the product belongs to the requested automotive part family.

    Returns:
        (classification, reason) where classification is one of:
        - EXACT_PART_MATCH: Direct, primary match for the requested part.
        - PART_ALIAS_MATCH: Acceptable automotive synonym or alias.
        - RELATED_COMPONENT: Legitimate sub-component of the requested part assembly.
        - ACCESSORY: Genuine accessory/mounting hardware of the requested part.
        - WRONG_PART_TYPE: Unrelated, conflicting, or non-matching part family.
        - UNKNOWN: Ambiguous or unclassifiable product.
    """
    title_clean = _clean_text(product_title)
    if not title_clean:
        return (UNKNOWN, "Empty product title.")

    # Infer intent and requested targets
    intent = parse_query_intent(query, requested_part)
    target_clean = intent.get("target_clean") or _clean_text(requested_part or query or category)
    desired_type = intent.get("desired_type", "complete_assembly")
    spec_acc = intent.get("specified_accessory")
    spec_comp = intent.get("specified_component")

    # If customer explicitly asked for an accessory (e.g. "sunroof drain hose")
    if desired_type == "accessory" and spec_acc:
        if re.search(rf"\b{re.escape(spec_acc)}\b", title_clean):
            return (EXACT_PART_MATCH, f"Exact match for requested accessory: '{spec_acc}'.")

    # If customer explicitly asked for a component (e.g. "sunroof glass")
    if desired_type == "component" and spec_comp:
        if re.search(rf"\b{re.escape(spec_comp)}\b", title_clean):
            return (EXACT_PART_MATCH, f"Exact match for requested component: '{spec_comp}'.")

    # Detect matching family definition
    matched_family_id = None
    matched_family = None

    # Check query/requested_part against match_queries
    text_to_match = f"{target_clean} {_clean_text(requested_part)} {_clean_text(query)}".lower()
    for fam_id, fam_def in PART_FAMILY_DEFINITIONS.items():
        # Sort queries by length descending
        for mq in sorted(fam_def.get("match_queries", ()), key=lambda x: -len(x)):
            if re.search(rf"\b{re.escape(mq)}\b", text_to_match):
                matched_family_id = fam_id
                matched_family = fam_def
                break
        if matched_family:
            break

    if not matched_family and intent.get("product_family") in PART_FAMILY_DEFINITIONS:
        matched_family_id = intent["product_family"]
        matched_family = PART_FAMILY_DEFINITIONS[matched_family_id]

    if matched_family:
        fam_name = matched_family["name"]

        # 1. Check positive patterns
        # Accessories
        for pat in matched_family.get("accessory_patterns", ()):
            if re.search(pat, title_clean):
                return (ACCESSORY, f"Genuine accessory/hardware for {fam_name}: '{product_title}'.")

        # Related sub-components
        for pat in matched_family.get("related_component_patterns", ()):
            if re.search(pat, title_clean):
                return (RELATED_COMPONENT, f"Related sub-component of {fam_name}: '{product_title}'.")

        # Exact match
        for pat in matched_family.get("exact_patterns", ()):
            if re.search(pat, title_clean):
                return (EXACT_PART_MATCH, f"Exact part match for {fam_name}: '{product_title}'.")

        # Alias match
        for pat in matched_family.get("alias_patterns", ()):
            if re.search(pat, title_clean):
                return (PART_ALIAS_MATCH, f"Part alias match for {fam_name}: '{product_title}'.")

        # 2. Check conflicting patterns
        for pat in matched_family.get("conflicting_patterns", ()):
            if re.search(pat, title_clean):
                return (
                    WRONG_PART_TYPE,
                    f"Conflicting part type: product is '{product_title}', but requested part family is '{fam_name}'."
                )

        # 3. Check family keywords presence
        keywords = matched_family.get("family_keywords", ())
        if keywords and not any(re.search(rf"\b{re.escape(kw)}\b", title_clean) for kw in keywords):
            return (
                WRONG_PART_TYPE,
                f"Unrelated part type: product '{product_title}' does not contain required {fam_name} keywords."
            )

        # If some keywords were present
        return (RELATED_COMPONENT, f"Related component in {fam_name} system: '{product_title}'.")

    # Fallback when requested part is not in predefined dictionary
    stopwords = {
        "find", "need", "looking", "for", "a", "an", "the", "car", "vehicle", "automobile",
        "spare", "part", "parts", "genuine", "oem", "chahiye", "ka", "ki", "ke", "hai",
    }
    target_words = [w for w in target_clean.split() if w not in stopwords and len(w) > 2]
    if not target_words:
        return (UNKNOWN, f"Ambiguous requested part: '{target_clean}'.")

    has_any = any(re.search(rf"\b{re.escape(w)}\b", title_clean) for w in target_words)
    has_all = all(re.search(rf"\b{re.escape(w)}\b", title_clean) for w in target_words)

    if not has_any:
        # Check if product is generic hardware or component
        if any(re.search(rf"\b{re.escape(t)}\b", title_clean) for t in ACCESSORY_TERMS | COMPONENT_TERMS | {"separator"}):
            return (WRONG_PART_TYPE, f"Unrelated part: '{product_title}' does not match requested '{target_clean}'.")
        return (WRONG_PART_TYPE, f"Unrelated part: '{product_title}' does not match requested '{target_clean}'.")

    if has_all:
        return (EXACT_PART_MATCH, f"Matches requested part '{target_clean}': '{product_title}'.")

    return (PART_ALIAS_MATCH, f"Partial match for requested part '{target_clean}': '{product_title}'.")


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
            "part_family_classification": WRONG_PART_TYPE,
            "part_family_reason": "Website navigation or policy link.",
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
                "part_family_classification": WRONG_PART_TYPE,
                "part_family_reason": compat_reason,
            }

    # 0c. Strict Part-Family Compatibility Validation
    target_part_str = requested_part or intent.get("target_raw") or query or ""
    part_classification, part_reason = check_part_family_compatibility(
        product_title=product_title,
        requested_part=target_part_str,
        query=query,
        category=category,
    )
    if part_classification == WRONG_PART_TYPE:
        return {
            "relevance": WRONG_PART_TYPE,
            "relevance_score": 0.0,
            "relevance_reason": part_reason,
            "product_type": "wrong_part_type",
            "part_family_classification": WRONG_PART_TYPE,
            "part_family_reason": part_reason,
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
                "part_family_classification": EXACT_PART_MATCH,
                "part_family_reason": part_reason,
            }
        if detected_accessory or part_classification == ACCESSORY:
            return {
                "relevance": CLOSE_MATCH,
                "relevance_score": 0.85,
                "relevance_reason": f"Related accessory ({detected_accessory or 'hardware'}) in the requested category.",
                "product_type": "accessory",
                "part_family_classification": ACCESSORY,
                "part_family_reason": part_reason,
            }
        return {
            "relevance": COMPONENT,
            "relevance_score": 0.70,
            "relevance_reason": "Complete assembly/component returned for specific accessory query.",
            "product_type": item_product_type,
            "part_family_classification": part_classification,
            "part_family_reason": part_reason,
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
                "part_family_classification": EXACT_PART_MATCH,
                "part_family_reason": part_reason,
            }
        if detected_component or part_classification == RELATED_COMPONENT:
            return {
                "relevance": CLOSE_MATCH,
                "relevance_score": 0.82,
                "relevance_reason": f"Alternative component ({detected_component or 'sub-assembly'}) in the requested assembly.",
                "product_type": "component",
                "part_family_classification": RELATED_COMPONENT,
                "part_family_reason": part_reason,
            }
        if detected_accessory or part_classification == ACCESSORY:
            return {
                "relevance": ACCESSORY,
                "relevance_score": 0.75,
                "relevance_reason": f"Minor hardware/accessory ({detected_accessory or 'hardware'}) related to component.",
                "product_type": "accessory",
                "part_family_classification": ACCESSORY,
                "part_family_reason": part_reason,
            }
        return {
            "relevance": COMPONENT,
            "relevance_score": 0.75,
            "relevance_reason": "Broader assembly returned for specific component query.",
            "product_type": item_product_type,
            "part_family_classification": part_classification,
            "part_family_reason": part_reason,
        }

    # -------------------------------------------------------------
    # CASE 3: Customer requested a GENERIC / COMPLETE ASSEMBLY
    # (e.g. "Sunroof", "Headlight", "Bumper", "Oil Filter")
    # -------------------------------------------------------------
    # 1. Accessory matches (genuine accessory of the assembly)
    if part_classification == ACCESSORY or (
        detected_accessory
        and detected_accessory not in target_clean
        and part_classification not in (EXACT_PART_MATCH, PART_ALIAS_MATCH)
    ):
        return {
            "relevance": ACCESSORY,
            "relevance_score": 0.95,
            "relevance_reason": f"Component/accessory ({detected_accessory or 'hardware'}) of the requested {family}, not a complete assembly.",
            "product_type": "accessory",
            "part_family_classification": ACCESSORY,
            "part_family_reason": part_reason,
        }

    # 2. Exact part matches
    if part_classification == EXACT_PART_MATCH:
        return {
            "relevance": EXACT_MATCH,
            "relevance_score": 0.98,
            "relevance_reason": f"Direct complete assembly match for requested '{target_clean or family}'.",
            "product_type": "complete_assembly",
            "part_family_classification": EXACT_PART_MATCH,
            "part_family_reason": part_reason,
        }

    # 3. Part alias matches
    if part_classification == PART_ALIAS_MATCH:
        return {
            "relevance": CLOSE_MATCH,
            "relevance_score": 0.90,
            "relevance_reason": f"Part alias match for requested '{target_clean or family}'.",
            "product_type": "complete_assembly",
            "part_family_classification": PART_ALIAS_MATCH,
            "part_family_reason": part_reason,
        }

    # 4. Related sub-components
    if (
        part_classification == RELATED_COMPONENT
        or (
            detected_component
            and detected_component not in target_clean
            and not (is_complete_assembly and any(k in title_norm for k in ("kit", "set", "complete kit", "complete set")))
        )
    ):
        return {
            "relevance": COMPONENT,
            "relevance_score": 0.92,
            "relevance_reason": f"Sub-assembly component ({detected_component or 'component'}) of the {family}, not a complete replacement assembly.",
            "product_type": "component",
            "part_family_classification": RELATED_COMPONENT,
            "part_family_reason": part_reason,
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
                "part_family_classification": EXACT_PART_MATCH,
                "part_family_reason": f"Matched synonym family '{family}'.",
            }
        return {
            "relevance": CLOSE_MATCH,
            "relevance_score": 0.88,
            "relevance_reason": f"Close match for requested '{target_clean or family}'.",
            "product_type": "complete_assembly",
            "part_family_classification": PART_ALIAS_MATCH,
            "part_family_reason": f"Matched synonym family '{family}'.",
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
            "part_family_classification": PART_ALIAS_MATCH,
            "part_family_reason": "Matches all query tokens.",
        }

    # If it is in the category but missing direct title matches
    if cat_norm and any(alias in cat_norm for alias in aliases):
        if detected_component:
            return {
                "relevance": COMPONENT,
                "relevance_score": 0.80,
                "relevance_reason": f"Listed under {category} category as a component.",
                "product_type": "component",
                "part_family_classification": RELATED_COMPONENT,
                "part_family_reason": f"Category component match for {category}.",
            }
        return {
            "relevance": NEEDS_REVIEW,
            "relevance_score": 0.65,
            "relevance_reason": f"Listed under {category} category but specific product intent is unverified.",
            "product_type": item_product_type,
            "part_family_classification": UNKNOWN,
            "part_family_reason": "Category match without direct title confirmation.",
        }

    # Default fallback
    return {
        "relevance": NEEDS_REVIEW,
        "relevance_score": 0.50,
        "relevance_reason": "Product title does not conclusively match requested product intent.",
        "product_type": item_product_type,
        "part_family_classification": UNKNOWN,
        "part_family_reason": "Ambiguous product intent.",
    }


def classify_listings(
    listings: List[Union[Dict[str, Any], Any]],
    query: str = "",
    requested_part: str = "",
    category: str = "",
    target_maker: str = "",
    target_model: str = "",
    target_year: str = "",
    target_variant: str = "",
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
        d["part_family_classification"] = res.get("part_family_classification", UNKNOWN)
        d["part_family_reason"] = res.get("part_family_reason", "")

        # Check vehicle fitment granularity
        existing_fitment = d.get("fitment") or "unverified"
        existing_basis = d.get("fitment_basis") or ""
        catalog_vehicle = d.get("catalog_vehicle") or d.get("vehicle_label") or ""

        # Extract catalog year if present
        eff_cat_year = ""
        if catalog_vehicle:
            y_m = re.search(r"\b(19\d\d|20\d\d)\b", catalog_vehicle)
            if y_m:
                eff_cat_year = y_m.group(1)
        if not eff_cat_year and existing_basis:
            y_m = re.search(r"\b(19\d\d|20\d\d)\b", existing_basis)
            if y_m:
                eff_cat_year = y_m.group(1)
        if not eff_cat_year and title:
            y_m = re.search(r"\b(19\d\d|20\d\d)\b", title)
            if y_m:
                eff_cat_year = y_m.group(1)

        if res["relevance"] == INCOMPATIBLE:
            d["fitment"] = "incompatible"
            d["match"] = "incompatible"
        elif res["relevance"] == WRONG_PART_TYPE or res.get("part_family_classification") == WRONG_PART_TYPE:
            d["fitment"] = "wrong_part_type"
            d["match"] = "wrong_part_type"
        else:
            # Check year match when target_year is specified
            if target_year and eff_cat_year and str(eff_cat_year) != str(target_year):
                # Compatible model, different year/variant
                cat_name = f"{target_maker.title()} {target_model.title()} {eff_cat_year}".strip() if target_maker else eff_cat_year
                d["fitment"] = "partial"
                d["fitment_basis"] = f"Verified for {cat_name} catalog; verify {target_year} compatibility before purchase"
                d.setdefault("notes", [])
                if not any("verify" in str(n).lower() and str(target_year) in str(n) for n in d["notes"]):
                    d["notes"].append(f"Catalogue verified for {eff_cat_year} model; verify {target_year} compatibility before purchase")
            elif existing_fitment in ("verified", "partial"):
                d["fitment"] = existing_fitment
                d["fitment_basis"] = existing_basis or (
                    f"Verified for {target_maker.title()} {target_model.title()}" + (f" {target_year}" if target_year else "") + " variant"
                )
            else:
                d["fitment"] = existing_fitment
                d["fitment_basis"] = existing_basis or "Compatibility unverified: verify fitment before purchase"

            # Keep legacy match field aligned
            d["match"] = "exact" if res["relevance"] in (EXACT_MATCH, CLOSE_MATCH, EXACT_PART_MATCH, PART_ALIAS_MATCH) else "related"

        # Ensure vendor and supplier normalization
        v_val = (d.get("vendor") or "").lower()
        if not v_val:
            if "boodmo" in (d.get("url") or "") or "boodmo" in existing_basis.lower():
                v_val = "boodmo"
            elif "autodukan" in (d.get("url") or "") or "autodukan" in existing_basis.lower():
                v_val = "autodukan"
        if v_val:
            d["vendor"] = v_val
            d["supplier"] = d.get("supplier") or ("Boodmo" if v_val == "boodmo" else "AutoDukan")
            d["supplier_name"] = d.get("supplier_name") or d["supplier"]

        # Ensure seller and dispatch fallbacks
        best_off = d.get("best_offer") or {}
        if not d.get("seller"):
            d["seller"] = best_off.get("seller") or (
                "Authorized Supplier Hub" if d.get("vendor") == "boodmo" else "AutoDukan Distributor" if d.get("vendor") == "autodukan" else None
            )
        if not d.get("dispatch"):
            d["dispatch"] = best_off.get("dispatch") or "24–48 hours"

        classified.append(d)

    return classified


def rank_listings(
    listings: List[Dict[str, Any]],
    price_order: Optional[str] = "asc"  # "asc" | "desc" | None
) -> List[Dict[str, Any]]:
    """Sort listings strictly preserving relevance tiers so price never overrides relevance.

    Priority:
        1. EXACT_MATCH / EXACT_PART_MATCH
        2. CLOSE_MATCH / PART_ALIAS_MATCH
        3. COMPONENT / RELATED_COMPONENT
        4. ACCESSORY
        5. NEEDS_REVIEW / UNKNOWN
        (IRRELEVANT, INCOMPATIBLE, and WRONG_PART_TYPE products are filtered out)

    Within each tier, products are sorted by price (asc or desc).
    """
    # Exclude IRRELEVANT, INCOMPATIBLE, and WRONG_PART_TYPE products
    valid = [
        l for l in listings
        if l.get("relevance") not in (IRRELEVANT, INCOMPATIBLE, WRONG_PART_TYPE)
        and l.get("fitment") not in ("incompatible", "wrong_part_type")
        and l.get("part_family_classification") != WRONG_PART_TYPE
        and l.get("product_type") not in ("incompatible", "irrelevant", "wrong_part_type")
    ]

    # Group by relevance tier
    tiers: Dict[str, List[Dict[str, Any]]] = {
        EXACT_PART_MATCH: [],
        EXACT_MATCH: [],
        PART_ALIAS_MATCH: [],
        CLOSE_MATCH: [],
        RELATED_COMPONENT: [],
        COMPONENT: [],
        ACCESSORY: [],
        NEEDS_REVIEW: [],
        UNKNOWN: [],
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
    # Primary matches
    ranked.extend(sort_tier(tiers[EXACT_PART_MATCH] + tiers[EXACT_MATCH]))
    ranked.extend(sort_tier(tiers[PART_ALIAS_MATCH] + tiers[CLOSE_MATCH]))
    # Component and accessory tiers
    ranked.extend(sort_tier(tiers[RELATED_COMPONENT] + tiers[COMPONENT]))
    ranked.extend(sort_tier(tiers[ACCESSORY]))
    # Review / Unknown tier
    ranked.extend(sort_tier(tiers[NEEDS_REVIEW] + tiers[UNKNOWN]))

    return ranked
