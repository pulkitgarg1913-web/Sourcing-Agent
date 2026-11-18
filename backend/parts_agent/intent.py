"""Strict Automotive-Only Intent Validation Module.

Ensures the agent only processes automobile spare parts sourcing queries,
and cleanly rejects out-of-scope requests (programming, general shopping,
weather, general knowledge, etc.) before any supplier adapters or sourcing
workflows are started.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class IntentCategory(str, Enum):
    AUTOMOTIVE_PART_SEARCH = "AUTOMOTIVE_PART_SEARCH"
    AUTOMOTIVE_PART_FOLLOWUP = "AUTOMOTIVE_PART_FOLLOWUP"
    AUTOMOTIVE_GENERAL_CLARIFICATION = "AUTOMOTIVE_GENERAL_CLARIFICATION"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"


# Standard user-facing messages
DEFAULT_OUT_OF_SCOPE_MESSAGE = (
    "I'm specifically designed to help with automobile spare parts sourcing. "
    "I can search for vehicle parts, compare supplier prices and help identify the correct part. "
    "I'm unable to assist with other types of questions."
)

PROGRAMMING_OUT_OF_SCOPE_MESSAGE = (
    "I'm specifically designed to help with automobile spare parts sourcing. "
    "I can help you find automobile parts, but I can't answer general programming questions."
)

LAPTOP_OUT_OF_SCOPE_MESSAGE = (
    "I'm specifically designed for automobile spare parts sourcing, so I can't help search for laptops. "
    "I can help you find spare parts for your vehicle."
)

AMBIGUOUS_CLARIFICATION_MESSAGE = (
    "Could you please specify which automobile spare part and vehicle you are looking for? "
    "For example: 'Headlights for Hyundai Creta' or 'Oil filter for Tata Harrier'."
)


@dataclass
class IntentResult:
    category: IntentCategory
    confidence: float
    reason: str
    part_detected: Optional[str] = None
    vehicle_detected: Optional[str] = None
    suggested_reply: Optional[str] = None


# -----------------------------------------------------------------------------
# Automotive Knowledge Base & Vocabulary
# -----------------------------------------------------------------------------

AUTO_PARTS = {
    # Lighting & Electrical
    "headlight", "headlights", "headlamp", "headlamps", "tail light", "tail lights", "taillight", "taillights",
    "fog light", "fog lights", "fog lamp", "fog lamps", "indicator", "blinker", "drl", "bulb", "bulbs",
    "alternator", "starter motor", "battery", "horn", "fuse", "relay", "wiring harness", "spark plug", "spark plugs",
    "glow plug", "glow plugs", "ignition coil", "oxygen sensor", "o2 sensor", "abs sensor", "sensor",
    # Braking & Wheels
    "brake pad", "brake pads", "brake disc", "brake discs", "brake rotor", "brake rotors", "brake shoe", "brake shoes",
    "brake caliper", "brake calipers", "brake master cylinder", "brake booster", "caliper pin", "brake fluid",
    "wheel bearing", "wheel hub", "alloy wheel", "rim", "tyre", "tire",
    # Engine & Drivetrain
    "air filter", "air cleaner", "oil filter", "fuel filter", "cabin filter", "ac filter", "pollen filter",
    "engine oil", "coolant", "radiator", "intercooler", "condenser", "thermostat", "water pump",
    "clutch", "clutch plate", "clutch disc", "pressure plate", "flywheel", "clutch set", "clutch kit",
    "timing belt", "timing chain", "serpentine belt", "drive belt", "fan belt", "fuel pump", "fuel injector", "injector",
    "turbo", "turbocharger", "cylinder head", "head gasket", "piston", "piston ring", "valves", "crankshaft",
    "camshaft", "exhaust", "silencer", "catalytic converter", "muffler",
    # Suspension & Steering
    "shock absorber", "shockers", "shocks", "strut", "struts", "suspension", "coil spring", "leaf spring",
    "control arm", "lower arm", "upper arm", "tie rod", "tie rod end", "rack end", "ball joint", "stabilizer link",
    "sway bar", "steering rack", "steering column", "power steering pump", "cv joint", "drive shaft", "axle",
    # Body & Interior
    "bumper", "front bumper", "rear bumper", "fender", "bonnet", "hood", "grille", "grill", "door", "door handle",
    "window regulator", "windshield", "windscreen", "side mirror", "rear view mirror", "orvm", "wiper", "wiper blade",
    "wipers", "mud flap", "mudguard", "spoiler", "boot lid", "tailgate", "dashboard", "seat belt",
}

AUTO_MAKES = {
    "tata", "hyundai", "kia", "maruti", "suzuki", "maruti suzuki", "toyota", "honda", "mahindra",
    "volkswagen", "vw", "skoda", "renault", "ford", "nissan", "mg", "morris garages", "chevrolet",
    "chevy", "fiat", "bmw", "audi", "mercedes", "mercedes-benz", "benz", "jeep", "isuzu", "citroen",
    "land rover", "jaguar", "volvo", "porsche", "lexus", "mitsubishi", "ashok leyland",
}

AUTO_MODELS = {
    "creta", "harrier", "safari", "seltos", "sonet", "nexon", "punch", "altroz", "tiago", "tigor",
    "swift", "baleno", "dzire", "alto", "brezza", "ertiga", "wagon r", "wagonr", "innova", "crysta",
    "fortuner", "city", "amaze", "verna", "venue", "i20", "i10", "grand i10", "scorpio", "thar",
    "xuv700", "xuv500", "xuv300", "carens", "carnival", "bolero", "compass", "kwid", "triber", "kiger",
    "magnite", "kushaq", "slavia", "taigun", "virtus", "polo", "vento", "octavia", "superb", "duster",
    "ecosport", "endeavour", "figo", "aspire", "hector", "astor", "gloster", "ev6", "ioniq", "ace", "dost",
}

# Follow-up attribute keywords for ongoing active sessions
VALID_FUELS = {"diesel", "petrol", "cng", "electric", "ev", "hybrid", "lpg"}
VALID_TRANSMISSIONS = {"manual", "automatic", "mt", "at", "amt", "imt", "cvt", "ivt", "dct", "dsg"}
VALID_CONFIRMATIONS = {"yes", "no", "y", "n", "ok", "okay", "confirm", "confirmed", "proceed", "correct", "sure", "haan", "sahi hai"}


# -----------------------------------------------------------------------------
# Regex Rules for Deterministic Out-Of-Scope Fast-Path
# -----------------------------------------------------------------------------

PROGRAMMING_PATTERNS = [
    r"\b(?:python|java|javascript|typescript|c\+\+|golang|rust|php|ruby|html|css|sql|bash|powershell)\b",
    r"\b(?:reverse\s+string|binary\s+search|fibonacci|bubble\s+sort|quick\s+sort)\b",
    r"\b(?:code|coding|program|programming|function|script|algorithm|regex|compiler)\b",
    r"\b(?:write\s+a\s+(?:python|code|program|function|script)|explain\s+code)\b",
    r"\b(?:print\(|def\s+\w+\(|class\s+\w+|import\s+\w+|return\s+\w+)\b",
    r"\b(?:npm\s+install|pip\s+install|git\s+commit|git\s+push|docker\s+run)\b",
    r"\b(?:syntax\s+error|nullpointerexception|typeerror|indexerror)\b",
]

SHOPPING_NON_AUTO_PATTERNS = [
    (r"\b(?:laptop|notebook|macbook|thinkpad|chromebook)\b", "laptops"),
    (r"\b(?:smartphone|iphone|android\s+phone|mobile\s+phone|samsung\s+galaxy|oneplus|pixel)\b", "smartphones"),
    (r"\b(?:tablet|ipad)\b", "tablets"),
    (r"\b(?:television|tv|smart\s+tv|oled|qled)\b", "televisions"),
    (r"\b(?:refrigerator|fridge|washing\s+machine|microwave|air\s+conditioner|ac\s+under)\b", "home appliances"),
    (r"\b(?:shirt|t-shirt|jeans|trousers|dress|shoes|sneakers|watch|perfume|clothing)\b", "apparel and fashion products"),
    (r"\b(?:grocery|vegetables|fruits|milk|bread|pizza|burger|food\s+delivery)\b", "groceries and food"),
    (r"\b(?:flight\s+ticket|hotel\s+room|train\s+ticket|movie\s+ticket)\b", "travel and bookings"),
    (r"\b(?:computer|pc|laptop|desktop|server)\s+(?:hardware|part|parts|component|components)\b", "computer parts"),
    (r"\b(?:airplane|aeroplane|aircraft|aviation|drone|helicopter|boat|ship|yacht|rocket)\b", "non-automotive equipment"),
]

GENERAL_KNOWLEDGE_PATTERNS = [
    r"\b(?:what\s+is\s+the\s+weather|temperature\s+today|will\s+it\s+rain|weather\s+forecast)\b",
    r"\b(?:who\s+is\s+(?:the\s+)?president|prime\s+minister|ceo\s+of|founder\s+of)\b",
    r"\b(?:who\s+was|who\s+won\s+the|capital\s+of|population\s+of|distance\s+between)\b",
    r"\b(?:tell\s+me\s+a\s+joke|write\s+a\s+poem|write\s+an\s+essay|how\s+to\s+cook|recipe\s+for)\b",
    r"\b(?:stock\s+price|share\s+price|bitcoin|cryptocurrency|horoscope|astrology)\b",
    r"\b(?:translate\s+(?:this|to)|how\s+old\s+is\s+the\s+earth|meaning\s+of\s+life)\b",
    r"\b(?:who\s+directed|movie\s+review|box\s+office)\b",
]


class PydanticIntentClassification(BaseModel):
    category: str = Field(description="One of: AUTOMOTIVE_PART_SEARCH, AUTOMOTIVE_PART_FOLLOWUP, AUTOMOTIVE_GENERAL_CLARIFICATION, OUT_OF_SCOPE, AMBIGUOUS")
    confidence: float = Field(default=0.9, description="Confidence score from 0.0 to 1.0")
    reason: str = Field(description="Explanation of classification")
    detected_part: Optional[str] = Field(default=None, description="Automotive spare part name if found")
    detected_vehicle: Optional[str] = Field(default=None, description="Vehicle make/model if found")


# -----------------------------------------------------------------------------
# Core Classifier
# -----------------------------------------------------------------------------

def is_active_session_followup(text: str, pending_field: Optional[str] = None) -> bool:
    """Check if text is a plausible follow-up answer during an active automotive session."""
    t = text.strip().lower()

    # 1. Single word or short phrase confirmation
    if t in VALID_CONFIRMATIONS:
        return True

    # 2. Model year: 4 digits between 1980 and 2035
    if re.fullmatch(r"(?:19[89]\d|20[0-3]\d)", t):
        return True

    # 3. Fuel type
    if t in VALID_FUELS:
        return True

    # 4. Transmission type
    if t in VALID_TRANSMISSIONS:
        return True

    # 5. Combined vehicle attributes, e.g. "2025 Diesel Manual", "HTX Petrol AT", "1.5L Diesel MT"
    parts = t.split()
    if 2 <= len(parts) <= 5:
        has_year = any(re.fullmatch(r"(?:19[89]\d|20[0-3]\d)", p) for p in parts)
        has_fuel = any(p in VALID_FUELS for p in parts)
        has_trans = any(p in VALID_TRANSMISSIONS for p in parts)
        has_engine = any(re.match(r"^\d\.\d[a-z]?$", p) for p in parts)
        has_known_term = has_year or has_fuel or has_trans or has_engine
        if has_known_term:
            return True

    # 6. Common trim identifiers: e.g. "HTX", "GTX", "SX", "SX(O)", "VXI", "ZXI", "Fearless", "Adventure"
    if re.fullmatch(r"[a-z0-9\+\(\)\-]{2,15}", t):
        known_trims = {"htx", "gtx", "hte", "htk", "gtx+", "sx", "sxo", "sx(o)", "ex", "vxi", "zxi", "lxi",
                       "fearless", "adventure", "creative", "smart", "pure", "base", "top", "standard"}
        if t in known_trims or any(m in t for m in AUTO_MODELS) or any(m in t for m in AUTO_MAKES):
            return True

    # 7. Check if it directly matches a make or model
    if t in AUTO_MAKES or t in AUTO_MODELS:
        return True

    # 8. Unsure / Don't know / Skip answers during clarification
    if re.search(r"\b(?:don'?t know|not sure|no idea|pata nahi|maloom nahi|skip|unknown|any|none|na|n/a|neither)\b", t):
        return True

    # 9. Numeric answers: e.g. year attempts ("1890", "2024") or option numbers ("1", "2", "option 1")
    if pending_field == "year" and re.fullmatch(r"\d{2,4}", t):
        return True
    if re.fullmatch(r"(?:option\s*)?\d{1,2}", t):
        return True

    # 10. General short replies to pending questions (not matching any out-of-scope patterns)
    if pending_field and len(t.split()) <= 4:
        return True

    return False


def classify_deterministic(text: str, has_active_session: bool = False, pending_field: Optional[str] = None) -> Optional[IntentResult]:
    """Fast deterministic classification using strict domain boundaries.
    
    Returns IntentResult if high confidence match found, or None to fall back to LLM.
    """
    clean_text = text.strip()
    lower_text = clean_text.lower()

    if not clean_text:
        return IntentResult(
            category=IntentCategory.AMBIGUOUS,
            confidence=1.0,
            reason="Empty request",
            suggested_reply=AMBIGUOUS_CLARIFICATION_MESSAGE,
        )

    # -------------------------------------------------------------------------
    # 1. Check for Explicit OUT_OF_SCOPE Domains (Higher Priority than Followup)
    # Even if an active session is waiting for input, programming, laptop, or weather
    # questions MUST be rejected as OUT_OF_SCOPE.
    # -------------------------------------------------------------------------

    # 1a. Programming / Coding questions
    for pat in PROGRAMMING_PATTERNS:
        if re.search(pat, lower_text):
            return IntentResult(
                category=IntentCategory.OUT_OF_SCOPE,
                confidence=1.0,
                reason="Detected programming / coding request",
                suggested_reply=PROGRAMMING_OUT_OF_SCOPE_MESSAGE,
            )

    # 1b. Unrelated shopping / Non-automotive products
    for pat, item_name in SHOPPING_NON_AUTO_PATTERNS:
        if re.search(pat, lower_text):
            # Special case: don't trigger if user explicitly says "laptop charger for Creta car socket"
            if "laptop" in lower_text:
                reply = LAPTOP_OUT_OF_SCOPE_MESSAGE
            else:
                reply = f"I'm specifically designed for automobile spare parts sourcing, so I can't help search for {item_name}. I can help you find spare parts for your vehicle."
            return IntentResult(
                category=IntentCategory.OUT_OF_SCOPE,
                confidence=1.0,
                reason=f"Detected unrelated shopping request: {item_name}",
                suggested_reply=reply,
            )

    # 1c. General knowledge / Weather / Chit-chat
    for pat in GENERAL_KNOWLEDGE_PATTERNS:
        if re.search(pat, lower_text):
            return IntentResult(
                category=IntentCategory.OUT_OF_SCOPE,
                confidence=1.0,
                reason="Detected general knowledge / weather / non-automotive chit-chat query",
                suggested_reply=DEFAULT_OUT_OF_SCOPE_MESSAGE,
            )

    # -------------------------------------------------------------------------
    # 2. Check for Follow-Up in Active Session or Standalone Spec
    # -------------------------------------------------------------------------
    if has_active_session and is_active_session_followup(clean_text, pending_field):
        return IntentResult(
            category=IntentCategory.AUTOMOTIVE_PART_FOLLOWUP,
            confidence=0.95,
            reason="Provided vehicle attribute or answer during active automotive sourcing session",
        )

    # Standalone vehicle specification without active session (e.g. "2025 Diesel Manual", "Diesel", "Manual")
    if not has_active_session:
        parts = lower_text.split()
        has_fuel = any(p in VALID_FUELS for p in parts)
        has_trans = any(p in VALID_TRANSMISSIONS for p in parts)
        has_year = any(re.fullmatch(r"(?:19[89]\d|20[0-3]\d)", p) for p in parts)
        if (has_fuel or has_trans or (has_year and (has_fuel or has_trans))) and len(parts) <= 5:
            return IntentResult(
                category=IntentCategory.AMBIGUOUS,
                confidence=0.85,
                reason="Vehicle specification provided without an active sourcing session or part request",
                suggested_reply=AMBIGUOUS_CLARIFICATION_MESSAGE,
            )

    # -------------------------------------------------------------------------
    # 3. Detect Automotive Part and Vehicle
    # -------------------------------------------------------------------------
    detected_part = None
    for part in AUTO_PARTS:
        if re.search(rf"\b{re.escape(part)}\b", lower_text):
            detected_part = part
            break

    detected_make = None
    for make in AUTO_MAKES:
        if re.search(rf"\b{re.escape(make)}\b", lower_text):
            detected_make = make
            break

    detected_model = None
    for model in AUTO_MODELS:
        if re.search(rf"\b{re.escape(model)}\b", lower_text):
            detected_model = model
            break

    detected_vehicle = " ".join(filter(None, [detected_make, detected_model])) or None

    # -------------------------------------------------------------------------
    # 4. Automotive Part Search
    # -------------------------------------------------------------------------
    if detected_part:
        # Part detected! E.g. "Find headlights for Hyundai Creta", "Air filter for Harrier", "Brake pads"
        return IntentResult(
            category=IntentCategory.AUTOMOTIVE_PART_SEARCH,
            confidence=0.95,
            reason=f"Detected automobile spare part request: '{detected_part}'",
            part_detected=detected_part,
            vehicle_detected=detected_vehicle,
        )

    # Check for general part indicators + vehicle or search intent
    part_query_indicators = ["part", "parts", "spare", "spares", "component", "components", "accessory", "accessories"]
    has_part_query_word = any(re.search(rf"\b{re.escape(w)}\b", lower_text) for w in part_query_indicators)
    has_search_verb = any(w in lower_text for w in ["find", "search", "buy", "price", "need", "get", "looking for", "cost"])

    if detected_vehicle and (has_part_query_word or has_search_verb):
        return IntentResult(
            category=IntentCategory.AUTOMOTIVE_PART_SEARCH,
            confidence=0.90,
            reason=f"Detected automotive part search intent for vehicle '{detected_vehicle}'",
            vehicle_detected=detected_vehicle,
        )

    # -------------------------------------------------------------------------
    # 5. Standalone or Ambiguous Queries
    # -------------------------------------------------------------------------
    generic_auto_indicators = ["car", "cars", "vehicle", "vehicles", "auto", "automobile", "automotive", "gadi", "gaadi"]
    has_generic_auto = any(re.search(rf"\b{re.escape(w)}\b", lower_text) for w in generic_auto_indicators)

    # If the user only says a vehicle name (e.g. "Hyundai", "Creta"), says "parts", or says "car",
    # that is ambiguous without knowing which part or vehicle.
    if (detected_vehicle or has_part_query_word or has_generic_auto) and len(clean_text.split()) <= 3:
        return IntentResult(
            category=IntentCategory.AMBIGUOUS,
            confidence=0.85,
            reason="Ambiguous request without complete part or vehicle specification",
            vehicle_detected=detected_vehicle,
            suggested_reply=AMBIGUOUS_CLARIFICATION_MESSAGE,
        )

    # -------------------------------------------------------------------------
    # 6. Default Fallback for Unrecognized Content
    # If a message has no automotive part or vehicle indicators, it must NOT default
    # to automotive search!
    # -------------------------------------------------------------------------
    if not detected_part and not detected_vehicle:
        return IntentResult(
            category=IntentCategory.OUT_OF_SCOPE,
            confidence=0.85,
            reason="Query contains no recognized automobile spare parts or vehicle references",
            suggested_reply=DEFAULT_OUT_OF_SCOPE_MESSAGE,
        )

    return None


INTENT_PROMPT = """You are an intent classifier for an AI Automotive Spare Parts Sourcing Agent.
Your sole job is to categorize the user's message into one of these strict categories:

1. AUTOMOTIVE_PART_SEARCH: The user wants to find, buy, price-check, or search for an automobile spare part (e.g., "headlights for Creta", "air filter for Tata Harrier", "clutch plate for Swift").
2. AUTOMOTIVE_PART_FOLLOWUP: The user is answering a question about vehicle details (e.g., year "2024", fuel "Diesel", transmission "Manual", trim "HTX") in an ongoing parts sourcing session.
3. AUTOMOTIVE_GENERAL_CLARIFICATION: The user is asking a technical or compatibility question specifically about automobile spare parts or vehicle fitment.
4. OUT_OF_SCOPE: Any request that is NOT about automobile spare parts sourcing (e.g., programming/coding questions, general shopping like laptops/phones, weather, general knowledge, non-automotive products).
5. AMBIGUOUS: Vague requests where intent is unclear (e.g., just "Hyundai", "parts").

IMPORTANT:
- A query is NOT automotive just because it mentions a brand name (e.g., "Tata steel price", "Ford movie", "Python code for car parts" are OUT_OF_SCOPE).
- Any programming, coding, math, weather, or unrelated product request is strictly OUT_OF_SCOPE.

Active session waiting for vehicle specification: {has_active_session}
Pending question: {pending_field}
User message: "{text}"
"""


async def classify_intent_async(
    text: str,
    has_active_session: bool = False,
    pending_field: Optional[str] = None
) -> IntentResult:
    """Classify user intent with strict domain enforcement.
    
    Uses deterministic fast-path first; if inconclusive, uses Gemini structured JSON.
    Never silently defaults unknown queries to automotive search.
    """
    # 1. Deterministic fast path
    res = classify_deterministic(text, has_active_session=has_active_session, pending_field=pending_field)
    if res is not None:
        log.info("Deterministic intent classification for '%s': %s (reason: %s)", text[:40], res.category.value, res.reason)
        return res

    # 2. Structured LLM classification fallback
    try:
        from . import llm
        prompt = INTENT_PROMPT.format(
            has_active_session=has_active_session,
            pending_field=pending_field or "None",
            text=text,
        )
        resp = await llm.generate_async(
            contents=prompt,
            config=llm.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PydanticIntentClassification,
                temperature=0.0,
            ),
        )
        parsed = resp.parsed
        if parsed and parsed.category:
            cat_str = parsed.category.strip().upper()
            try:
                category = IntentCategory(cat_str)
            except ValueError:
                category = IntentCategory.OUT_OF_SCOPE

            reply = None
            if category == IntentCategory.OUT_OF_SCOPE:
                reply = DEFAULT_OUT_OF_SCOPE_MESSAGE
            elif category == IntentCategory.AMBIGUOUS:
                reply = AMBIGUOUS_CLARIFICATION_MESSAGE

            return IntentResult(
                category=category,
                confidence=parsed.confidence,
                reason=parsed.reason,
                part_detected=parsed.detected_part,
                vehicle_detected=parsed.detected_vehicle,
                suggested_reply=reply,
            )
    except Exception as e:
        log.warning("LLM intent classification failed or offline: %s; using safe strict fallback", e)

    # 3. Safe fallback: never default unknown requests to AUTOMOTIVE_PART_SEARCH
    return IntentResult(
        category=IntentCategory.OUT_OF_SCOPE,
        confidence=0.8,
        reason="Safe fallback: query could not be verified as automotive spare parts sourcing",
        suggested_reply=DEFAULT_OUT_OF_SCOPE_MESSAGE,
    )


def classify_intent(
    text: str,
    has_active_session: bool = False,
    pending_field: Optional[str] = None
) -> IntentResult:
    """Synchronous intent classification (deterministic fast-path or safe default)."""
    res = classify_deterministic(text, has_active_session=has_active_session, pending_field=pending_field)
    if res is not None:
        return res
    return IntentResult(
        category=IntentCategory.OUT_OF_SCOPE,
        confidence=0.8,
        reason="Safe fallback: query could not be verified as automotive spare parts sourcing",
        suggested_reply=DEFAULT_OUT_OF_SCOPE_MESSAGE,
    )
