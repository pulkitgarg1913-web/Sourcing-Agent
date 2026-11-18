"""Gemini helpers: understand the request, and interpret answers that simple
matching can't. Every call walks a fallback chain because flash models
regularly return 503 under load."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
log = logging.getLogger(__name__)

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        # No SDK-level retries: on a 503 we'd rather move to the next model in the
        # chain than sit in exponential backoff while the mechanic waits.
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                               http_options=types.HttpOptions(
                                   timeout=int(os.environ.get("GEMINI_TIMEOUT_MS", "12000")),
                                   retry_options=types.HttpRetryOptions(attempts=1)))
    return _client


def model_chain() -> list[str]:
    primary = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    fallbacks = os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite")
    return [primary] + [m.strip() for m in fallbacks.split(",") if m.strip() and m.strip() != primary]


def generate(contents, config: types.GenerateContentConfig | None = None,
             models: list[str] | None = None):
    last = None
    for model in models or model_chain():
        if model in _unavailable and time.time() - _unavailable[model] < 120:
            continue   # skip a model that just returned 503 for a couple of minutes
        t0 = time.time()
        try:
            resp = client().models.generate_content(model=model, contents=contents, config=config)
            log.info("gemini %s ok in %.1fs", model, time.time() - t0)
            return resp
        except Exception as e:  # 503 / 429 / timeout
            last = e
            log.info("gemini %s failed in %.1fs: %s", model, time.time() - t0, str(e)[:120])
            if any(k in str(e) for k in ("503", "504", "429", "DEADLINE", "timed out", "Timeout")):
                _unavailable[model] = time.time()
    raise RuntimeError(f"All Gemini models failed: {last}")


async def generate_async(contents, config: types.GenerateContentConfig | None = None,
                         models: list[str] | None = None):
    """Run the synchronous Gemini SDK call without blocking the event loop."""
    return await asyncio.to_thread(generate, contents, config, models)


_unavailable: dict[str, float] = {}


# ---------------------------------------------------------------- request parsing

class ParsedRequest(BaseModel):
    part: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[str] = None
    fuel: Optional[str] = None
    transmission: Optional[str] = None
    trim: Optional[str] = None
    engine: Optional[str] = None


PARSE_PROMPT = """You extract a car-parts request from what an Indian mechanic said.
The text may be English, Hindi or Hinglish, and may come from speech-to-text.
Return only what was actually said. Do not guess missing details, with one exception:
if only a model is named and its manufacturer is unambiguous (e.g. Harrier -> Tata,
Creta -> Hyundai, Swift -> Maruti), fill `make`.
- part: the part in plain English, e.g. "oil filter", "front brake pads"
- year: 4-digit model year only if said
- fuel: Diesel / Petrol / CNG / Electric only if said
- transmission: "manual" or "automatic" only if said
- trim: variant/trim name only if said, e.g. "XZ+", "SX(O)", "VXI"
- engine: displacement like "2.0" only if said

Mechanic said: {text}"""


def parse_request(text: str) -> ParsedRequest:
    resp = generate(PARSE_PROMPT.format(text=text),
                    types.GenerateContentConfig(response_mime_type="application/json",
                                                response_schema=ParsedRequest,
                                                temperature=0))
    return resp.parsed or ParsedRequest()


async def parse_request_async(text: str) -> ParsedRequest:
    try:
        parsed = await asyncio.to_thread(parse_request, text)
    except Exception as exc:
        # Keep the supplier search usable during Gemini outages. This fallback
        # only extracts explicit text and a small set of unambiguous aliases.
        log.warning("request parsing failed; using local extraction: %s", exc)
        return fallback_parse_request(text)
    if any(parsed.model_dump().values()):
        return parsed
    log.warning("request parser returned no fields; using local extraction")
    return fallback_parse_request(text)


_MAKE_ALIASES = {
    "maruti suzuki": "Maruti Suzuki", "maruti": "Maruti",
    "tata": "Tata", "hyundai": "Hyundai", "kia": "Kia",
    "honda": "Honda", "toyota": "Toyota", "mahindra": "Mahindra",
    "renault": "Renault", "volkswagen": "Volkswagen", "vw": "VW",
    "skoda": "Skoda", "ford": "Ford", "nissan": "Nissan",
    "mg": "MG", "morris garages": "MG", "citroen": "Citroen", "jeep": "Jeep", "isuzu": "Isuzu",
    "chevrolet": "Chevrolet", "chevy": "Chevrolet", "fiat": "Fiat", "bmw": "BMW", "audi": "Audi",
    "mercedes benz": "Mercedes-Benz", "mercedes": "Mercedes-Benz", "benz": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz", "land rover": "Land Rover",
}

# These aliases cover common Indian models and the models used by this agent.
# A model is only filled when the name is explicit in the mechanic's text.
_MODEL_ALIASES = {
    "nexon ev": ("NEXON EV", "Tata"), "nexon": ("NEXON", "Tata"),
    "harrier ev": ("HARRIER EV", "Tata"), "harrier": ("HARRIER", "Tata"),
    "safari": ("SAFARI", "Tata"), "punch ev": ("PUNCH EV", "Tata"),
    "punch": ("PUNCH", "Tata"), "altroz": ("ALTROZ", "Tata"),
    "tiago": ("TIAGO", "Tata"), "tigor": ("TIGOR", "Tata"),
    "seltos": ("SELTOS", "Kia"), "sonet": ("SONET", "Kia"),
    "carens": ("CARENS", "Kia"), "city": ("CITY", "Honda"),
    "amaze": ("AMAZE", "Honda"), "jazz": ("JAZZ", "Honda"),
    "elevate": ("ELEVATE", "Honda"), "verna": ("VERNA", "Hyundai"),
    "creta": ("CRETA", "Hyundai"), "venue": ("VENUE", "Hyundai"),
    "i20": ("i20", "Hyundai"), "i10": ("i10", "Hyundai"),
    "swift": ("SWIFT", "Maruti Suzuki"), "baleno": ("BALENO", "Maruti Suzuki"),
    "dzire": ("DZIRE", "Maruti Suzuki"), "alto": ("ALTO", "Maruti Suzuki"),
    "brezza": ("BREZZA", "Maruti Suzuki"), "ertiga": ("ERTIGA", "Maruti Suzuki"),
    "wagon r": ("WAGON R", "Maruti Suzuki"), "innova": ("INNOVA", "Toyota"),
    "fortuner": ("FORTUNER", "Toyota"), "glanza": ("GLANZA", "Toyota"),
    "scorpio": ("SCORPIO", "Mahindra"), "thar": ("THAR", "Mahindra"),
    "xuv700": ("XUV700", "Mahindra"), "xuv500": ("XUV500", "Mahindra"),
    "ace": ("ACE", "Tata"), "dost": ("DOST", "Ashok Leyland"),
}

COMMERCIAL_VEHICLES: list[tuple[str, str]] = [
    (r"\bace\b", "Tata Ace"),
    (r"\bdost\b", "Ashok Leyland Dost"),
    (r"\bbolero\s+maxi\s*truck\b", "Mahindra Bolero Maxi Truck"),
    (r"\bbolero\s+maxitruck\b", "Mahindra Bolero Maxi Truck"),
    (r"\bbolero\s+pik\s*up\b", "Mahindra Bolero Pikup"),
    (r"\bbolero\s+pickup\b", "Mahindra Bolero Pikup"),
    (r"\bpik\s*up\b", "Pickup Truck"),
    (r"\bpickup\b", "Pickup Truck"),
    (r"\bintra\b", "Tata Intra"),
    (r"\bsupro\b", "Mahindra Supro"),
    (r"\bjeeto\b", "Mahindra Jeeto"),
    (r"\bcanter\b", "Eicher Canter"),
    (r"\beicher\b", "Eicher Commercial Vehicle"),
]


def check_commercial_vehicle(text: str = "", model: Optional[str] = None) -> Optional[str]:
    """Check if the vehicle requested is a known commercial vehicle (SCV / LCV / Truck)."""
    target = f"{text} {model or ''}".lower()
    for pattern, name in COMMERCIAL_VEHICLES:
        if re.search(pattern, target, re.I):
            return f"{name} is a commercial vehicle. AutoDukan and Boodmo only list passenger cars and SUVs."
    return None


MAJOR_ASSEMBLIES: dict[str, dict] = {
    "gearbox": {
        "name": "gearbox",
        "explanation": "Boodmo and AutoDukan do not sell complete gearbox assemblies online; they only stock individual gearbox components and repair parts.",
        "prompt": "Boodmo lists 12 replacement categories for a gearbox/transmission. Which one do you need?",
        "components": [
            "Manual Transmission",
            "Automatic Transmission",
            "Transmission Bracket (Mounting)",
            "Gear Shift Cable",
            "Clutch Kit",
            "Gear Shifter Repair Kit",
            "Transmission Gear",
            "Transmission Housing",
            "Transmission Shaft",
            "Gear Shift Fork",
            "Synchronizer Ring",
            "Manual Transmission Fluid",
        ],
    },
    "engine": {
        "name": "engine",
        "explanation": "Boodmo and AutoDukan do not sell complete crate engine assemblies online; they only stock engine components and service parts.",
        "prompt": "Boodmo and AutoDukan don't sell complete engine assemblies online, only service parts. Which engine part do you need: Engine Mounting, Timing Belt Kit, Cylinder Head Gasket, Piston Rings, or Oil Filter?",
        "components": [
            "Engine Mounting",
            "Timing Belt Kit",
            "Cylinder Head Gasket",
            "Piston Ring Set",
            "Oil Filter",
        ],
    },
}


def check_major_assembly(part_text: str = "") -> Optional[dict]:
    """Check if the requested part is a major vehicle assembly not sold as a whole unit online."""
    lowered = (part_text or "").lower().strip()
    if not lowered:
        return None

    # Check for Gearbox / Transmission assembly
    if re.search(r"\b(?:gear\s*box|gearbox|transmission)\b", lowered):
        # Exclude specific subcomponents if already stated in the request
        subcomponents = (
            "mount", "bracket", "cable", "fluid", "oil", "bush", "seal", "gasket",
            "kit", "clutch", "lever", "fork", "bearing", "sensor", "filter", "synchronizer",
            "bellow", "knob", "plate", "disc", "shaft", "sleeve", "rod", "link",
            "manual transmission", "automatic transmission"
        )
        if not any(sc in lowered for sc in subcomponents):
            return MAJOR_ASSEMBLIES["gearbox"]

    # Check for Engine assembly
    if re.search(r"\b(?:engine|motor)\b", lowered):
        subcomponents = (
            "mount", "bracket", "belt", "chain", "oil", "filter", "gasket", "piston",
            "valve", "spark", "plug", "coolant", "radiator", "hose", "head", "sensor"
        )
        if not any(sc in lowered for sc in subcomponents):
            return MAJOR_ASSEMBLIES["engine"]

    return None


_PART_ALIASES = (
    (r"\b(?:tail\s*lights?|tail\s*lamps?|taillights?|taillamps?|rear\s+lamps?)\b", "Tail Light"),
    (r"\b(?:head\s*lights?|head\s*lamps?|headlights?|headlamps?)\b", "Headlight"),
    (r"\bsteering\s+wheel\s+switch(?:es)?\b", "Steering Wheel Switch"),
    (r"\b(?:gear\s*box\s+mount(?:ing)?|transmission\s+mount(?:ing)?)\b", "Gearbox Mounting"),
    (r"\b(?:gear\s*shift\s+cables?|gear\s+cables?)\b", "Gear Shift Cable"),
    (r"\b(?:gear\s*shifter\s+repair\s+kit|gear\s+lever\s+bush(?:ing)?)\b", "Gear Shifter Repair Kit"),
    (r"\b(?:gear\s*oil|transmission\s+oil|transmission\s+fluid|atf)\b", "Transmission Fluid"),
    (r"\b(?:gear\s*box|gearbox)\b", "Gearbox"),
    (r"\b(?:brake\s+pads?|disc\s+pads?)\b", "Brake Pads"),
    (r"\b(?:oil\s+filters?)\b", "Oil Filter"),
    (r"\b(?:air\s+filters?)\b", "Air Filter"),
    (r"\b(?:spark\s*plugs?)\b", "Spark Plug"),
    (r"\b(?:wiper\s*blades?)\b", "Wiper Blade"),
    (r"\b(?:wipers?|windscreen\s+wipers?)\b", "Wiper"),
    (r"\b(?:shock\s+absorbers?|shocker(?:s)?)\b", "Shock Absorber"),
    (r"\b(?:tail\s+gate|tailgate)\b", "Tailgate"),
    (r"\b(?:side\s+mirrors?|door\s+mirrors?)\b", "Side Mirror"),
    (r"\b(?:clutch\s*sets?|clutch\s*kits?)\b", "Clutch Set"),
    (r"\b(?:clutch\s+plates?)\b", "Clutch Plate"),
    (r"\b(?:radiators?)\b", "Radiator"),
    (r"\b(?:horns?)\b", "Horn"),
    (r"\b(?:batter(?:y|ies))\b", "Battery"),
    (r"\b(?:bumpers?)\b", "Bumper"),
    (r"\b(?:alternators?)\b", "Alternator"),
    (r"\b(?:steering)\b", "Steering"),
)


def fallback_parse_request(text: str) -> ParsedRequest:
    """Extract clear vehicle and part names without an LLM call."""
    raw = (text or "").strip()
    lowered = raw.lower()
    result = ParsedRequest()

    make_alias = next((alias for alias in sorted(_MAKE_ALIASES, key=len, reverse=True)
                       if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered)), None)
    if make_alias:
        result.make = _MAKE_ALIASES[make_alias]

    model_alias = next((alias for alias in sorted(_MODEL_ALIASES, key=len, reverse=True)
                        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered)), None)
    if model_alias:
        model_value, model_make = _MODEL_ALIASES[model_alias]
        if not result.make or result.make.casefold() == model_make.casefold():
            result.model = model_value
        if not result.make:
            result.make = model_make

    if year := re.search(r"\b(19[89]\d|20[0-4]\d)\b", raw):
        result.year = year.group(1)
    for fuel in ("diesel", "petrol", "cng", "electric"):
        if re.search(rf"\b{fuel}\b", lowered):
            result.fuel = fuel.title()
            break
    if re.search(r"\b(?:automatic|auto|cvt|dct|amt|dsg)\b", lowered):
        result.transmission = "automatic"
    elif re.search(r"\bmanual\b", lowered):
        result.transmission = "manual"

    for pattern, canonical in _PART_ALIASES:
        if re.search(pattern, lowered):
            result.part = canonical
            break
    if not result.part:
        # Preserve a remaining phrase as the requested part after removing
        # only details recognized above. Unknown part names stay searchable.
        remainder = raw
        for alias in sorted(_MAKE_ALIASES, key=len, reverse=True):
            remainder = re.sub(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", " ", remainder, flags=re.I)
        for alias in sorted(_MODEL_ALIASES, key=len, reverse=True):
            remainder = re.sub(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", " ", remainder, flags=re.I)
        remainder = re.sub(r"\b(19[89]\d|20[0-4]\d|diesel|petrol|cng|electric|automatic|auto|manual)\b", " ", remainder, flags=re.I)
        result.part = re.sub(r"\s+", " ", remainder).strip(" ,.-") or None
    return result


# ---------------------------------------------------------------- answer matching

WORD_NUMBERS = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3,
                "four": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "sixth": 6,
                "last": -1, "pehla": 1, "doosra": 2, "dusra": 2, "teesra": 3, "tisra": 3}

ALIASES = {"manual": "MT", "automatic": "AT", "auto": "AT", "plus": "+"}

EQUIVALENT_NAMES = {
    "mercedes": {"MERCEDES", "MERCEDESBENZ", "BENZ"},
    "mercedes benz": {"MERCEDES", "MERCEDESBENZ", "BENZ"},
    "mercedes-benz": {"MERCEDES", "MERCEDESBENZ", "BENZ"},
    "benz": {"MERCEDES", "MERCEDESBENZ", "BENZ"},
    "maruti": {"MARUTI", "MARUTISUZUKI", "SUZUKI"},
    "maruti suzuki": {"MARUTI", "MARUTISUZUKI", "SUZUKI"},
    "suzuki": {"MARUTI", "MARUTISUZUKI", "SUZUKI"},
    "vw": {"VW", "VOLKSWAGEN"},
    "volkswagen": {"VW", "VOLKSWAGEN"},
    "mg": {"MG", "MORRISGARAGES"},
    "morris garages": {"MG", "MORRISGARAGES"},
    "chevy": {"CHEVY", "CHEVROLET"},
    "chevrolet": {"CHEVY", "CHEVROLET"},
}


def _norm(s: str) -> str:
    s = s.upper().replace(" PLUS", "+").replace("PLUS", "+")
    return re.sub(r"[^A-Z0-9+.]", "", s)


def match_option_locally(answer: str, options: list[str], spoken: list[str] | None = None) -> Optional[int]:
    """Cheap deterministic matching: ordinal, exact, unique containment, close fuzzy."""
    a = answer.strip().lower()
    a = re.sub(r"[.!?]$", "", a)
    if not a:
        return None
    # Year-like answers must match a year option exactly.
    m = re.fullmatch(r"(?:option\s*|number\s*)?(\d{1,2})", a)
    if m and not all(re.fullmatch(r"\d{4}", o) for o in options):
        i = int(m.group(1))
        if 1 <= i <= len(options):
            return i - 1
    for w, i in WORD_NUMBERS.items():
        if re.fullmatch(rf"(the\s+)?{w}(\s+(one|option|wala|waala|vala|number))?", a):
            return (len(options) - 1) if i == -1 else (i - 1 if i <= len(options) else None)

    # Every word they said appears in exactly one option ("automatic" -> "AGS automatic").
    filler = {"the", "one", "wala", "waala", "vala", "it", "is", "its", "it's", "a", "hai", "vo", "woh"}
    words = [w for w in re.findall(r"[a-z0-9+.()]+", a.replace(" plus", "+")) if w not in filler]
    if words:
        pools = [set(re.findall(r"[a-z0-9+.()]+", o.lower().replace(" plus", "+"))) for o in (spoken or options)]
        hits = [i for i, pool in enumerate(pools) if all(w in pool for w in words)]
        if len(hits) == 1:
            return hits[0]

    na = _norm(a)
    for alias, val in ALIASES.items():
        if a == alias:
            na = val
    labels = [(_norm(o), i) for i, o in enumerate(options)]
    if spoken:
        labels += [(_norm(s), i) for i, s in enumerate(spoken)]
    exact = {i for n, i in labels if n == na}
    if len(exact) == 1:
        return exact.pop()

    # Brand and common abbreviation equivalents
    cleaned_a = a.replace("-", " ").strip()
    eq = EQUIVALENT_NAMES.get(a, set()) | EQUIVALENT_NAMES.get(cleaned_a, set()) | EQUIVALENT_NAMES.get(na.lower(), set())
    if eq:
        eq_hits = {i for n, i in labels if n in eq}
        if len(eq_hits) == 1:
            return eq_hits.pop()

    contains = {i for n, i in labels if na and (na in n.split() or (len(na) >= 4 and na in n) or (len(n) >= 4 and n in na))}
    if len(contains) == 1:
        return contains.pop()
    scored = sorted(((SequenceMatcher(None, na, n).ratio(), i) for n, i in labels), reverse=True)
    if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1):
        if not any(s[1] != scored[0][1] and s[0] == scored[0][0] for s in scored):
            return scored[0][1]
    return None


class OptionChoice(BaseModel):
    index: Optional[int] = None   # 1-based; null if unclear or none fit
    unsure: bool = False


CHOICE_PROMPT = """A mechanic is answering a question while we search a parts website.
Question: {question}
The website's options (use only these):
{options}
Mechanic's answer: "{answer}"

Pick the single option the mechanic means. Return its 1-based index.
If the answer is ambiguous, matches several options, or none of them, return index null.
Set unsure=true if you had to make any leap (e.g. sound-alike words from speech-to-text)."""


def match_option(answer: str, question: str, options: list[str], spoken: list[str] | None = None) -> tuple[Optional[int], bool]:
    """Returns (index, needs_confirmation)."""
    i = match_option_locally(answer, options, spoken)
    if i is not None:
        return i, False
    listing = "\n".join(f"{n + 1}. {o}" for n, o in enumerate(options))
    try:
        resp = generate(CHOICE_PROMPT.format(question=question, options=listing, answer=answer),
                        types.GenerateContentConfig(response_mime_type="application/json",
                                                    response_schema=OptionChoice, temperature=0))
        c = resp.parsed
    except Exception as e:
        log.warning("option matching via Gemini failed: %s", e)
        return None, False
    if c and c.index and 1 <= c.index <= len(options):
        i = c.index - 1
        # If the answer doesn't literally contain the option, the model inferred it
        # (e.g. "XZA plus" -> automatic). Plausible, but confirm rather than assume.
        said = _norm(answer)
        literal = _norm(options[i]) in said or (spoken and _norm(spoken[i]) and _norm(spoken[i]) in said)
        return i, bool(c.unsure) or not literal
    return None, False


async def match_option_async(answer: str, question: str, options: list[str],
                             spoken: list[str] | None = None) -> tuple[Optional[int], bool]:
    return await asyncio.to_thread(match_option, answer, question, options, spoken)


def closest(value: str, options: list[str], n: int = 4) -> list[str]:
    nv = _norm(value)
    scored = sorted(options, key=lambda o: SequenceMatcher(None, nv, _norm(o)).ratio(), reverse=True)
    return scored[:n]


def is_yes(answer: str) -> Optional[bool]:
    a = answer.strip().lower()
    if re.match(r"^(yes|yeah|yep|haan|ha|han|ji|correct|right|sahi|ok|okay|sure|confirm)\b", a):
        return True
    if re.match(r"^(no|nope|nahi|nahin|na|wrong|galat)\b", a):
        return False
    return None
