"""LangGraph conversation loop.

understand -> navigate -(site needs input)-> [pause] interpret -> navigate ...
                       -(vehicle fully selected)-> collect -> present

The graph pauses before `interpret` (a static breakpoint rather than
`interrupt()`, which needs Python 3.11+ inside async nodes). `Conversation`
writes the mechanic's answer into state and resumes.

The browser session lives outside the graph state (it can't be checkpointed),
in a per-thread session registry, so it stays on the same page while we wait
for the mechanic to answer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import operator
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Callable, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from . import llm, variants
from .auto_dukan import AutoDukanAdapter
from .boodmo import BoodmoAdapter
from .browser_manager import get_browser_manager
from .models import Listing, NeedInput, SearchOutcome, VehicleProfile
from .perf_logger import perf_monitor

log = logging.getLogger(__name__)
RUNS = Path(__file__).resolve().parent.parent / "runs"
if not RUNS.exists():
    _alt_runs = Path(__file__).resolve().parent.parent.parent / "runs"
    if _alt_runs.exists():
        RUNS = _alt_runs


# ---------------------------------------------------------------- session registry

@dataclass
class Session:
    boodmo_adapter: BoodmoAdapter
    autodukan_adapter: AutoDukanAdapter
    announce: Callable[[str], None]   # short spoken/printed status lines
    require_confirmation: bool = False
    on_supplier_completed: Optional[Callable[[SearchOutcome], None]] = None
    on_queued: Optional[Callable[[], None]] = None

    @property
    def adapters(self) -> dict[str, object]:
        return {
            "boodmo": self.boodmo_adapter,
            "autodukan": self.autodukan_adapter,
        }


SESSIONS: dict[str, Session] = {}
MAX_CONCURRENT_SEARCHES = int(os.environ.get("MAX_CONCURRENT_SEARCHES", "1"))
_SEARCH_SEMAPHORE: Optional[asyncio.Semaphore] = None


def get_search_semaphore() -> asyncio.Semaphore:
    global _SEARCH_SEMAPHORE
    if _SEARCH_SEMAPHORE is None:
        _SEARCH_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
    return _SEARCH_SEMAPHORE


def reset_search_semaphore(new_limit: Optional[int] = None) -> asyncio.Semaphore:
    global _SEARCH_SEMAPHORE, MAX_CONCURRENT_SEARCHES
    if new_limit is not None:
        MAX_CONCURRENT_SEARCHES = new_limit
    _SEARCH_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
    return _SEARCH_SEMAPHORE


def open_session(thread_id: str, *, headless: bool = True,
                 progress: Callable[[str], None] | None = None,
                 announce: Callable[[str], None] | None = None,
                 record_video_dir: Optional[str] = None,
                 require_confirmation: bool = False,
                 on_supplier_completed: Optional[Callable[[SearchOutcome], None]] = None,
                 on_queued: Optional[Callable[[], None]] = None) -> Session:
    bm = get_browser_manager()
    s = Session(boodmo_adapter=BoodmoAdapter(headless=headless, progress=progress, record_video_dir=record_video_dir, session_id=thread_id, browser_manager=bm),
                autodukan_adapter=AutoDukanAdapter(headless=headless, progress=progress, record_video_dir=record_video_dir, session_id=thread_id, browser_manager=bm),
                announce=announce or (lambda t: None),
                require_confirmation=require_confirmation,
                on_supplier_completed=on_supplier_completed,
                on_queued=on_queued)
    SESSIONS[thread_id] = s
    return s


def reap_zombie_processes():
    try:
        while True:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid <= 0:
                break
    except Exception:
        pass


async def close_session(thread_id: str):
    s = SESSIONS.pop(thread_id, None)
    if s:
        adapters = list(s.adapters.values())
        results = await asyncio.gather(*(adapter.close() for adapter in adapters),
                                       return_exceptions=True)
        for adapter, result in zip(adapters, results):
            if isinstance(result, Exception):
                log.warning("Could not close %s browser session: %s", adapter.name, result)
    try:
        await get_browser_manager().close_session(thread_id)
    except Exception as e:
        log.warning("Error closing session %s in BrowserManager: %s", thread_id, e)
    reap_zombie_processes()
    import gc
    gc.collect()


async def close_all_stale_sessions(active_thread_id: Optional[str] = None):
    stale_threads = [tid for tid in list(SESSIONS.keys()) if tid != active_thread_id]
    for tid in stale_threads:
        try:
            await close_session(tid)
        except Exception as e:
            log.warning("Error closing stale session %s: %s", tid, e)
    reap_zombie_processes()
    import gc
    gc.collect()


def _session(config) -> Session:
    return SESSIONS[config["configurable"]["thread_id"]]


# ---------------------------------------------------------------- state

class State(TypedDict, total=False):
    request: str
    profile: dict                  # Boodmo profile, retained for summary compatibility
    supplier_profiles: dict        # independent selections for each supplier
    pending: Optional[dict]      # NeedInput (or a confirmation) waiting for an answer
    answer: Optional[str]
    outcome: Optional[dict]
    summary: Optional[str]
    error: Optional[str]
    cancelled: bool
    confirmed: Optional[bool]
    turns: Annotated[list, operator.add]   # audit trail of questions and answers


CANCEL = re.compile(r"^\s*(cancel|stop|exit|quit|band karo|rehne do|chhodo)\b", re.I)
DONT_KNOW = re.compile(r"(don'?t know|not sure|no idea|pata nahi|maloom nahi|skip)", re.I)

# Downstream fields that become invalid when an upstream answer changes.
RESETS = {
    "maker": ("model", "year", "variant"),
    "model": ("year", "variant"),
    "year": ("variant",),
}


def _quick_hints(text: str, profile: VehicleProfile):
    """Pick up details mentioned in passing ("2021, diesel automatic") without an LLM call."""
    t = text.lower()
    raw = text.strip()
    if not profile.year and (m := re.search(r"\b(19[89]\d|20[0-4]\d)\b", t)):
        profile.hints.setdefault("year", m.group(1))
    for fuel in ("diesel", "petrol", "cng", "electric"):
        if re.search(rf"\b{fuel}\b", t):
            profile.hints.setdefault("fuel", fuel.lower())
            break
    if re.search(r"\b(?:imt)\b", t):
        profile.hints.setdefault("transmission", "iMT")
    elif re.search(r"\b(?:ivt)\b", t):
        profile.hints.setdefault("transmission", "IVT")
    elif re.search(r"\b(?:dct)\b", t):
        profile.hints.setdefault("transmission", "DCT")
    elif re.search(r"\b(?:dsg)\b", t):
        profile.hints.setdefault("transmission", "DSG")
    elif re.search(r"\b(?:amt|ags)\b", t):
        profile.hints.setdefault("transmission", "AMT")
    elif re.search(r"\b(?:automatic|auto|\bat\b)\b", t):
        profile.hints.setdefault("transmission", "automatic")
    elif re.search(r"\b(?:manual|\bmt\b)\b", t):
        profile.hints.setdefault("transmission", "manual")
    if eng := re.search(r"(?<![a-z0-9])([0-5]\.\d|[0-5]\d?\.\d|\b[1-5]\b)\s*(?:l|litre|liter)\b", raw, re.I):
        profile.hints.setdefault("engine", f"{eng.group(1)}L")
    elif eng := re.search(r"(?<![a-z0-9])([0-5]\.\d)(?![a-z0-9])", raw):
        profile.hints.setdefault("engine", f"{eng.group(1)}L")
    if tm := re.search(r"(?<![a-z0-9])(GTX\+|GTX\s*PLUS|GTX|HTX\+|HTX\s*PLUS|HTX|HTK\+|HTK\s*PLUS|HTK|HTE|X[- ]?LINE|XZA?\+|XZA|XZ\+|XZ|XT|XM|XE|CREATIVE\+|CREATIVE|FEARLESS\+|FEARLESS|PURE\+|PURE|SMART\+|SMART|SX\s*\(?O\)?|SX\s*PLUS|SX\s*TECH|SX|EX|ZXI\+|ZXI|VXI|LXI|ALPHA|ZETA|DELTA|SIGMA|AX7\s*L?|AX5|AX3|LX|AX\s*OPT|AX)(?![a-z0-9])", raw, re.I):
        trim_str = tm.group(1).upper().replace(" PLUS", "+").replace("PLUS", "+")
        if trim_str in ("X LINE", "X-LINE"):
            trim_str = "X-Line"
        profile.hints.setdefault("trim", trim_str)


def _profiles_from_state(state: State) -> dict[str, VehicleProfile]:
    stored = state.get("supplier_profiles") or {}
    legacy = VehicleProfile.from_dict(state.get("profile"))
    boodmo_p = VehicleProfile.from_dict(stored.get("boodmo") or legacy.to_dict())

    # Shared mechanic-relevant facts
    shared_hints = dict(legacy.hints)
    for key, value in (
        ("make", boodmo_p.maker or legacy.maker),
        ("model", boodmo_p.model or legacy.model),
        ("year", boodmo_p.year or legacy.year),
    ):
        if value:
            shared_hints.setdefault(key, value)
    for key, value in boodmo_p.variant_filters.items():
        if value and value != variants.SKIP:
            shared_hints.setdefault(key, value)
    if boodmo_p.variant:
        shared_hints["boodmo_variant"] = boodmo_p.variant
        shared_hints.setdefault("variant", boodmo_p.variant)

    profiles = {"boodmo": boodmo_p}
    for vendor in ("autodukan",):
        saved = stored.get(vendor)
        p = VehicleProfile.from_dict(saved) if saved else VehicleProfile(part=boodmo_p.part or legacy.part, hints=dict(shared_hints))
        for k, v in shared_hints.items():
            if k not in p.hints or not p.hints[k]:
                p.hints[k] = v
        if not p.maker and (boodmo_p.maker or shared_hints.get("make")):
            p.maker = boodmo_p.maker or shared_hints.get("make")
        if not p.model and (boodmo_p.model or shared_hints.get("model")):
            p.model = boodmo_p.model or shared_hints.get("model")
        if not p.year and (boodmo_p.year or shared_hints.get("year")):
            p.year = boodmo_p.year or shared_hints.get("year")
        if boodmo_p.variant:
            p.hints["boodmo_variant"] = boodmo_p.variant
        if not p.part and boodmo_p.part:
            p.part = boodmo_p.part
        profiles[vendor] = p

    # Ensure bidirectional synchronization so vehicle identity is never lost
    common_maker = boodmo_p.maker or profiles["autodukan"].maker or shared_hints.get("make")
    common_model = boodmo_p.model or profiles["autodukan"].model or shared_hints.get("model")
    common_year = boodmo_p.year or profiles["autodukan"].year or shared_hints.get("year")
    common_part = boodmo_p.part or profiles["autodukan"].part or legacy.part
    fallback_flag = bool(boodmo_p.hints.get("category_fallback") or profiles["autodukan"].hints.get("category_fallback"))
    for p in profiles.values():
        if not p.maker and common_maker:
            p.maker = common_maker
        if not p.model and common_model:
            p.model = common_model
        if not p.year and common_year:
            p.year = common_year
        if not p.part and common_part:
            p.part = common_part
        if fallback_flag:
            p.hints["category_fallback"] = True
            p.hints["use_search_fallback"] = True
    return profiles


def _profile_updates(profiles: dict[str, VehicleProfile]) -> dict:
    return {
        "profile": profiles["boodmo"].to_dict(),
        "supplier_profiles": {name: profile.to_dict() for name, profile in profiles.items()},
    }


def _need_for_vendor(need: NeedInput, vendor: str) -> dict:
    d = need.to_dict()
    q = d.get("question", "")
    q = re.sub(r"\s+on\s+AutoDukan\b", "", q, flags=re.I)
    q = re.sub(r"\s+on\s+Boodmo\b", "", q, flags=re.I)
    q = re.sub(r"\bWhich AutoDukan\b", "Which", q, flags=re.I)
    q = re.sub(r"\bWhich Boodmo\b", "Which", q, flags=re.I)
    d["question"] = q
    return d | {"vendor": vendor}


def _other_profiles(profiles: dict[str, VehicleProfile], vendor: str) -> list[VehicleProfile]:
    return [profile for name, profile in profiles.items() if name != vendor]


# ---------------------------------------------------------------- nodes

async def understand(state: State, config) -> State:
    text = state["request"]
    profile = VehicleProfile()
    try:
        parsed = await llm.parse_request_async(text)
    except Exception as e:
        log.warning("request parsing failed: %s", e)
        parsed = llm.ParsedRequest(part=text)
    req_part = parsed.part or text
    if req_part and text:
        m = re.search(rf"\b({re.escape(req_part)}s?)\b", text, re.I)
        if m:
            req_part = m.group(1).capitalize()
    profile.part = req_part or parsed.part
    profile.requested_part = req_part or parsed.part or text
    profile.hints = {k: v for k, v in parsed.model_dump().items()
                     if v and k not in ("part",)}
    profile.hints["raw_request"] = text
    profile.hints["requested_part"] = profile.requested_part
    _quick_hints(text, profile)
    # All adapters start from the mechanic's request. Supplier-specific
    # categories and selected variant labels are resolved and stored separately.
    comm_warning = llm.check_commercial_vehicle(text, parsed.model)
    if comm_warning:
        profile.hints["commercial_warning"] = comm_warning

    profiles = {
        "boodmo": VehicleProfile.from_dict(profile.to_dict()),
        "autodukan": VehicleProfile.from_dict(profile.to_dict()),
    }
    turns = [{"t": time.time(), "role": "mechanic", "text": text, "parsed": parsed.model_dump()}]
    if not profile.part:
        need = NeedInput(field="part", question="Which part do you need?", options=[])
        return _profile_updates(profiles) | {"pending": need.to_dict(), "turns": turns}

    assembly_info = llm.check_major_assembly(profile.part)
    if assembly_info:
        need = NeedInput(
            field="part",
            question=assembly_info["prompt"],
            options=assembly_info["components"]
        )
        if comm_warning:
            _session(config).announce(comm_warning)
        _session(config).announce(assembly_info["explanation"])
        return _profile_updates(profiles) | {"pending": need.to_dict(), "turns": turns}

    car = " ".join(x for x in (parsed.make, parsed.model) if x)
    if comm_warning:
        _session(config).announce(comm_warning)
    _session(config).announce(f"Okay, looking for {_a(profile.part)}{profile.part}"
                              + (f" for a {car}" if car else "")
                              + " on Boodmo and AutoDukan.")
    return _profile_updates(profiles) | {"pending": None, "turns": turns}


async def navigate(state: State, config) -> State:
    profiles = _profiles_from_state(state)
    session = _session(config)

    boodmo_p = profiles.get("boodmo")
    autodukan_p = profiles.get("autodukan")
    active_p = boodmo_p or autodukan_p

    maker = (boodmo_p and boodmo_p.maker) or (autodukan_p and autodukan_p.maker) or (active_p and active_p.hints.get("make")) or ""
    model = (boodmo_p and boodmo_p.model) or (autodukan_p and autodukan_p.model) or (active_p and active_p.hints.get("model")) or ""
    year = (boodmo_p and boodmo_p.year) or (autodukan_p and autodukan_p.year) or (active_p and active_p.hints.get("year")) or ""
    fuel = (active_p and active_p.hints.get("fuel")) or ""
    transmission = (active_p and active_p.hints.get("transmission")) or ""
    trim = (active_p and active_p.hints.get("trim")) or ""
    engine = (active_p and active_p.hints.get("engine")) or ""
    part = (active_p and (active_p.part or active_p.category)) or "parts"

    # Conversational Clarification Flow (before supplier search)
    if not state.get("confirmed"):
        if maker and model:
            # Step 2: Ask model year if missing
            if not year:
                catalog_years = variants.get_model_catalog_years(maker, model)
                need = NeedInput(
                    field="year",
                    question=f"Which model year is your {maker.title()} {model.title()}?",
                    options=catalog_years,
                )
                return _profile_updates(profiles) | {"pending": need.to_dict()}

            is_seltos = (maker.lower(), model.lower()) == ("kia", "seltos") or model.lower() == "seltos"

            # Step 3: Ask fuel / engine configuration if missing (for Seltos or where fuels exist)
            if is_seltos and not fuel:
                fuels = variants.get_model_fuels(maker, model)
                if len(fuels) > 1:
                    need = NeedInput(
                        field="variant_attr",
                        attr="fuel",
                        question=f"Is it {' or '.join(f.lower() for f in fuels)}?",
                        options=fuels,
                    )
                    return _profile_updates(profiles) | {"pending": need.to_dict()}

            # Step 3b: Ask transmission if missing (for Seltos)
            if is_seltos and not transmission:
                transmissions = variants.get_model_transmissions(maker, model)
                need = NeedInput(
                    field="variant_attr",
                    attr="transmission",
                    question="Which transmission is it: manual or automatic?",
                    options=transmissions,
                )
                return _profile_updates(profiles) | {"pending": need.to_dict()}

            # Step 4: Ask vehicle trim / variant if missing (for Seltos)
            if is_seltos and not trim:
                trims = variants.get_model_trims(maker, model)
                if trims:
                    need = NeedInput(
                        field="trim",
                        question=f"Which variant or trim is your {maker.title()} {model.title()}?",
                        options=trims,
                    )
                    return _profile_updates(profiles) | {"pending": need.to_dict()}

            # Step 5: Confirm vehicle profile
            if getattr(session, "require_confirmation", False):
                lines = [f"I will search for {part} for:"]
                lines.append(f"- Make: {maker.title()}")
                lines.append(f"- Model: {model.title()}")
                lines.append(f"- Year: {year}")
                lines.append(f"- Variant / Trim: {trim or 'Standard'}")
                if fuel:
                    lines.append(f"- Fuel: {fuel.title()}")
                if engine:
                    lines.append(f"- Engine: {engine}")
                if transmission:
                    lines.append(f"- Transmission: {transmission.upper() if len(transmission) <= 4 else transmission.title()}")
                lines.append("Would you like to search with these details, or change anything?")
                car_label = f"{maker} {model} {year} {trim or ''}".strip()
                need = {
                    "field": "confirm_search",
                    "question": "\n".join(lines),
                    "options": ["Confirm & Search", "Change Details"],
                    "candidate": car_label,
                    "vendor": "all",
                }
                return _profile_updates(profiles) | {"pending": need}

    async def advance_vendor(name: str, adapter):
        try:
            return await adapter.advance(profiles[name])
        except Exception:
            log.exception("%s navigation failed", name)
            return None

    vendors = list(session.adapters.items())
    needs = {}

    # Advance Boodmo first so it can lead vehicle identification without CPU contention
    if "boodmo" in session.adapters:
        needs["boodmo"] = await advance_vendor("boodmo", session.adapters["boodmo"])
        boodmo_p = profiles.get("boodmo")
        if boodmo_p and boodmo_p.variant and "autodukan" in session.adapters:
            autodukan_p = profiles.get("autodukan")
            if autodukan_p:
                autodukan_p.hints["boodmo_variant"] = boodmo_p.variant

    boodmo_need = needs.get("boodmo")
    for name, adapter in vendors:
        if name == "boodmo":
            continue
        # If Boodmo is actively clarifying core vehicle identity (maker, model, year),
        # defer other vendor advances until the vehicle is known
        if boodmo_need and boodmo_need.field in ("maker", "model", "year"):
            continue
        needs[name] = await advance_vendor(name, adapter)

    boodmo_p = profiles.get("boodmo")
    autodukan_p = profiles.get("autodukan")
    valid_needs = []
    boodmo_has_active_need = bool(boodmo_need)
    for vendor, adapter in vendors:
        vendor_need = needs.get(vendor)
        if not vendor_need:
            continue
        # AutoDukan must NEVER ask the mechanic for maker, model, or year if the vehicle is already known
        if vendor == "autodukan":
            known_make = (boodmo_p and (boodmo_p.maker or boodmo_p.hints.get("make"))) or (autodukan_p and (autodukan_p.maker or autodukan_p.hints.get("make")))
            known_model = (boodmo_p and (boodmo_p.model or boodmo_p.hints.get("model"))) or (autodukan_p and (autodukan_p.model or autodukan_p.hints.get("model")))
            if vendor_need.field in ("maker", "model", "year") and (known_make or known_model):
                log.info("AutoDukan asked for %s when vehicle is known (%s %s); falling back to direct search",
                         vendor_need.field, known_make, known_model)
                await adapter._search_fallback(autodukan_p or profiles["autodukan"])
                continue
            # If Boodmo is present in the session and actively has a need, or Boodmo already has a variant, let Boodmo lead
            if vendor_need.field in ("variant", "variant_attr"):
                if "boodmo" in session.adapters and (boodmo_has_active_need or (boodmo_p and boodmo_p.variant)):
                    log.info("AutoDukan asked for variant when Boodmo is leading; falling back to direct search")
                    await adapter._search_fallback(autodukan_p or profiles["autodukan"])
                    continue
        valid_needs.append(_need_for_vendor(vendor_need, vendor))

    need = valid_needs[0] if valid_needs else None
    if not need and getattr(session, "require_confirmation", False) and not state.get("confirmed"):
        active_p = boodmo_p or profiles.get("autodukan")
        maker = (boodmo_p and boodmo_p.maker) or (profiles.get("autodukan") and profiles["autodukan"].maker) or ""
        model = (boodmo_p and boodmo_p.model) or (profiles.get("autodukan") and profiles["autodukan"].model) or ""
        year = (boodmo_p and boodmo_p.year) or (profiles.get("autodukan") and profiles["autodukan"].year) or ""
        variant = (boodmo_p and boodmo_p.variant) or (profiles.get("autodukan") and profiles["autodukan"].variant) or ""
        trim = (active_p and active_p.hints.get("trim")) or ""
        fuel = (active_p and active_p.hints.get("fuel")) or ""
        engine = (active_p and active_p.hints.get("engine")) or ""
        transmission = (active_p and active_p.hints.get("transmission")) or ""
        part = (active_p and (active_p.part or active_p.category)) or "parts"
        lines = [f"I will search for {part} for:"]
        if maker: lines.append(f"- Make: {maker.title()}")
        if model: lines.append(f"- Model: {model.title()}")
        if year: lines.append(f"- Year: {year}")
        if trim: lines.append(f"- Variant / Trim: {trim}")
        elif variant: lines.append(f"- Variant / Trim: {variant}")
        if fuel: lines.append(f"- Fuel: {fuel.title()}")
        if engine: lines.append(f"- Engine: {engine}")
        if transmission: lines.append(f"- Transmission: {transmission.upper() if len(transmission) <= 4 else transmission.title()}")
        lines.append("Would you like to search with these details, or change anything?")
        car_label = f"{maker} {model} {year} {trim or variant or ''}".strip()
        need = {
            "field": "confirm_search",
            "question": "\n".join(lines),
            "options": ["Confirm & Search", "Change Details"],
            "candidate": car_label,
            "vendor": "all",
        }
    return _profile_updates(profiles) | {"pending": need}


async def interpret(state: State, config) -> State:
    p = dict(state["pending"])
    ans = (state.get("answer") or "").strip()
    profiles = _profiles_from_state(state)
    vendor = p.get("vendor", "boodmo")
    if vendor not in profiles:
        vendor = "boodmo"
    profile = profiles[vendor]
    turn = {"t": time.time(), "role": "exchange", "vendor": vendor,
            "field": p["field"], "question": p["question"],
            "options": p.get("options"), "answer": ans}

    if CANCEL.match(ans):
        return _profile_updates(profiles) | {
            "cancelled": True, "pending": None, "turns": [turn | {"resolved": "cancel"}]
        }
    hints_before = dict(profile.hints)
    _quick_hints(ans, profile)
    if p["field"] in ("variant_attr", "variant", "confirm") and len(ans) <= 40:
        # Keep short answers: a trim said out of turn ("XZA plus" to "manual or
        # automatic?") is matched against the site's trims on the next step.
        profile.hints.setdefault("mentions", []).append(ans)
    new_hints = {k: v for k, v in profile.hints.items() if k not in hints_before}
    siblings = _other_profiles(profiles, vendor)
    for key, value in new_hints.items():
        if key != "mentions":
            for sibling in siblings:
                old_value = sibling.hints.get(key)
                sibling.hints[key] = value
                if key in ("engine", "trim", "fuel", "transmission") and old_value != value:
                    sibling.variant = None
                    sibling.variant_filters = {}

    if p["field"] == "confirm_search":
        ans_lower = ans.lower().strip()
        yes = llm.is_yes(ans) or any(w in ans_lower for w in ("confirm", "search", "ready", "proceed", "start", "haan", "theek", "ok", "yes"))
        if yes and not any(w in ans_lower for w in ("change", "modify", "edit", "no", "nahin", "wait")):
            return _profile_updates(profiles) | {
                "pending": None, "confirmed": True, "turns": [turn | {"resolved": "confirmed"}]
            }
        # If user asked to modify or gave new hints, apply them
        _quick_hints(ans, profile)
        for k in ("year", "fuel", "transmission", "engine", "trim"):
            if k in profile.hints:
                for vp in profiles.values():
                    vp.hints[k] = profile.hints[k]
                    if k == "year":
                        vp.year = profile.hints[k]
        turn["resolved"] = "modify"
        return _profile_updates(profiles) | {
            "pending": None, "confirmed": False, "turns": [turn | {"resolved": "modify"}]
        }

    if p["field"] == "confirm":
        yes = llm.is_yes(ans)
        orig = p.get("original", {})
        if yes:
            vendor = orig.get("vendor", vendor)
            _apply(profiles[vendor], orig, p.get("candidate"), config, profiles, vendor)
            return _resolved(profiles, turn, p.get("candidate"))
        if yes is False:
            orig = dict(orig, note="Okay.")
            return _profile_updates(profiles) | {
                "pending": orig, "turns": [turn | {"resolved": None}]
            }
        p = orig
        vendor = p.get("vendor", vendor)
        profile = profiles[vendor]
        siblings = _other_profiles(profiles, vendor)

    if p["field"] == "trim":
        if DONT_KNOW.search(ans) or any(phrase in ans.lower() for phrase in ("other", "not sure", "don't know", "skip", "unknown")):
            val = "Standard"
            profile.hints["trim_unverified"] = True
            _apply(profile, p, val, config, profiles, vendor)
            return _resolved(profiles, turn, val)
        options = p.get("options") or []
        matched_idx = llm.match_option_locally(ans, options)
        if matched_idx is not None and 0 <= matched_idx < len(options):
            val = options[matched_idx]
            if val in ("Other / Not sure", "Other", "Not sure"):
                val = "Standard"
                profile.hints["trim_unverified"] = True
        else:
            val = ans.strip()
            profile.hints["trim_unverified"] = True
        _apply(profile, p, val, config, profiles, vendor)
        return _resolved(profiles, turn, val)

    if p["field"] == "variant_attr" and p.get("candidate"):
        # "Just to check: is it XZ plus?" -- yes applies it, no drops it, anything
        # else is matched against the attribute's options below.
        profile.hints["mentions"] = []
        yes = llm.is_yes(ans)
        if yes and len(ans.split()) <= 3:
            _apply(profile, p, p["candidate"], config, profiles, vendor)
            return _resolved(profiles, turn, p["candidate"])
        if yes is False and len(ans.split()) <= 2:
            return _resolved(profiles, turn, {"rejected": p["candidate"]})
        p = dict(p, candidate=None)

    if p["field"] == "part":
        options = p.get("options") or []
        resolved_part = None
        if options:
            idx, _ = await llm.match_option_async(ans, p.get("question", ""), options)
            if idx is not None and 0 <= idx < len(options):
                resolved_part = options[idx]
        if not resolved_part:
            try:
                parsed = await llm.parse_request_async(ans)
            except Exception:
                parsed = llm.ParsedRequest(part=ans)
            resolved_part = parsed.part or ans
            if resolved_part and ans:
                m = re.search(rf"\b({re.escape(resolved_part)}s?)\b", ans, re.I)
                if m:
                    resolved_part = m.group(1).capitalize()
            for k, v in parsed.model_dump().items():
                if v and k != "part":
                    for vp in profiles.values():
                        vp.hints.setdefault(k, v)
        for vendor_profile in profiles.values():
            vendor_profile.part = resolved_part
            vendor_profile.requested_part = resolved_part
            vendor_profile.category = None
            vendor_profile.hints["requested_part"] = resolved_part
            _quick_hints(ans, vendor_profile)
        car = " ".join(x for x in (profile.maker or profile.hints.get("make"), profile.model or profile.hints.get("model")) if x)
        _session(config).announce(f"Okay, looking for {_a(resolved_part)}{resolved_part}"
                                  + (f" for a {car}" if car else "")
                                  + " on Boodmo and AutoDukan.")
        return _resolved(profiles, turn, resolved_part)

    options = p.get("options") or []
    if p.get("field") == "category":
        ans_clean = ans.lower().strip()
        is_none_or_fallback = (
            any(phrase in ans_clean for phrase in ("none of these", "none", "other", "part name", "search by part", "search directly", "neither", "nahin", "skip"))
            or bool(DONT_KNOW.search(ans))
        )
        if is_none_or_fallback:
            session = _session(config)
            for vendor_name, vendor_profile in profiles.items():
                vendor_profile.hints["category_fallback"] = True
                vendor_profile.hints["use_search_fallback"] = True
                vendor_profile.hints["category_resolved"] = True
                adapter = session.adapters.get(vendor_name)
                if adapter and hasattr(adapter, "choose_category"):
                    adapter.choose_category(vendor_profile, "None of these")
                elif adapter:
                    setattr(adapter, "_using_search_fallback", True)
                    setattr(adapter, "_category_confirmed", True)
            return _resolved(profiles, turn, "Search by part name instead")

    if DONT_KNOW.search(ans):
        if p["field"] == "variant_attr":
            profile.variant_filters[p["attr"]] = variants.SKIP
            return _resolved(profiles, turn, "unknown")
        hint = {
            "year": "I need the model year to check fitment. It's on the RC book, or the sticker on the door pillar.",
            "variant": "The variant decides which part fits. The badge on the tailgate usually shows it.",
        }.get(p["field"], "I need this to check compatibility.")
        return _profile_updates(profiles) | {
            "pending": dict(p, note=hint),
            "turns": [turn | {"resolved": None}],
        }

    spoken = p.get("spoken") or ([variants.spoken_value(p["attr"], o) for o in options]
                                 if p.get("attr") else None)
    idx, unsure = await llm.match_option_async(ans, p["question"], options, spoken)
    if idx is None:
        useful = {k: v for k, v in new_hints.items()
                  if profile.variant_filters.get(k) in (None, variants.SKIP)}
        if useful and p["field"] in ("variant_attr", "variant"):
            # Not an answer to this question, but useful ("it's manual"): narrow with it
            # and let the navigator pick the next question.
            for key, value in useful.items():
                for sibling in siblings:
                    sibling.hints.setdefault(key, value)
            return _resolved(profiles, turn, {"hints": useful})
        retry = dict(p, note="Sorry, I didn't catch that."
                     + (" You can say the number." if p["field"] == "variant" else ""))
        return _profile_updates(profiles) | {
            "pending": retry, "turns": [turn | {"resolved": None}]
        }
    value = options[idx]
    if unsure:
        label = spoken[idx] if spoken else value
        confirm = {"field": "confirm", "question": f"Did you mean {label}?", "options": ["yes", "no"],
                   "candidate": value, "original": p | {"vendor": vendor}, "note": None,
                   "vendor": vendor}
        return _profile_updates(profiles) | {
            "pending": confirm, "turns": [turn | {"resolved": None}]
        }
    _apply(profile, p, value, config, profiles, vendor)
    return _resolved(profiles, turn, value)


def _resolved(profiles: dict[str, VehicleProfile], turn: dict, value) -> State:
    return _profile_updates(profiles) | {
        "pending": None, "answer": None, "turns": [turn | {"resolved": value}]
    }


def _apply(profile: VehicleProfile, need: dict, value: str, config,
           profiles: dict[str, VehicleProfile], vendor: str):
    f = need["field"]
    if f == "category":
        profile.hints["category_resolved"] = True
        session = _session(config)
        adapter = session.adapters.get(vendor)
        if adapter and hasattr(adapter, "choose_category"):
            adapter.choose_category(profile, value)
        else:
            profile.category = value
    elif f in ("maker", "model", "year"):
        if getattr(profile, f) != value:
            for down in RESETS[f]:
                setattr(profile, down, None)
            profile.variant_filters = {}
        setattr(profile, f, value)
        hint_key = {"maker": "make", "model": "model", "year": "year"}[f]
        for sibling in _other_profiles(profiles, vendor):
            previous_hint = sibling.hints.get(hint_key)
            changed_hint = bool(previous_hint and str(previous_hint).casefold() != value.casefold())
            sibling.hints[hint_key] = value
            sibling_value = getattr(sibling, f)
            if sibling_value and str(sibling_value).casefold() != value.casefold():
                setattr(sibling, f, None)
            if f == "maker":
                if changed_hint:
                    sibling.hints.pop("model", None)
                    for key in ("trim", "engine", "fuel", "transmission"):
                        sibling.hints.pop(key, None)
                    sibling.variant_filters = {}
                    sibling.variant = None
                    sibling.model = sibling.year = None
            elif f == "model":
                if changed_hint:
                    for key in ("trim", "engine", "fuel", "transmission"):
                        sibling.hints.pop(key, None)
                    sibling.variant_filters = {}
                    sibling.variant = None
                    sibling.year = None
            elif f == "year":
                if changed_hint:
                    sibling.year = None
                    sibling.variant = None
                    sibling.variant_filters = {}
    elif f == "variant_attr":
        profile.variant_filters[need["attr"]] = value
        profile.hints[need["attr"]] = value
        for sibling in _other_profiles(profiles, vendor):
            if sibling.hints.get(need["attr"]) != value:
                sibling.variant = None
                sibling.variant_filters = {}
            sibling.hints[need["attr"]] = value
    elif f == "trim":
        profile.hints["trim"] = value
        for sibling in _other_profiles(profiles, vendor):
            sibling.hints["trim"] = value
    elif f == "variant":
        profile.variant = value
        for sibling in _other_profiles(profiles, vendor):
            sibling.hints["boodmo_variant"] = value
            sibling.hints["variant"] = value


async def collect(state: State, config) -> State:
    profiles = _profiles_from_state(state)
    profile = profiles["boodmo"]
    s = _session(config)
    thread_id = config["configurable"].get("thread_id", "")
    s.announce("Got the vehicle details. Checking prices and stock across all suppliers.")

    from . import cache_manager, relevance

    # Fast Cache Check (Choice 1 + Strategy 1)
    cached_outcomes = None if (state.get("force_refresh") or state.get("bypass_cache")) else cache_manager.get_cached_search(
        maker=profile.maker,
        model=profile.model,
        year=profile.year,
        variant=profile.variant,
        part=profile.part or profile.category,
    )
    if cached_outcomes:
        is_demo = any(o.get("is_demo") for o in cached_outcomes)
        if is_demo:
            s.announce("Instantly retrieved demonstration catalogue records.")
            cache_msg = "Instantly retrieved demonstration sample records."
        else:
            s.announce("Instantly retrieved verified live pricing from cache.")
            cache_msg = "Instantly retrieved from 24h verified supplier cache."

        # Release browser resources immediately since cache delivered instant result
        for name, adapter in list(s.adapters.items()):
            try:
                await adapter.close()
            except Exception:
                pass
        vendor_outcomes = cached_outcomes
        target_maker = profile.maker or profile.hints.get("make") or ""
        target_model = profile.model or profile.hints.get("model") or ""
        target_year = profile.year or str(profile.hints.get("year") or "")
        target_variant = profile.variant or ""

        # Normalize vendor and supplier on each vendor outcome and its listings
        for vo in vendor_outcomes:
            v_name = vo.get("vendor", "")
            s_title = "Boodmo" if v_name == "boodmo" else "AutoDukan" if v_name == "autodukan" else v_name.title()
            vo["vendor"] = v_name
            vo["supplier"] = s_title
            vo["supplier_name"] = s_title
            vo_listings = vo.get("listings") or []
            classified_vo = relevance.classify_listings(
                [
                    {
                        **(l.to_dict() if hasattr(l, "to_dict") else dict(l)),
                        "vendor": v_name,
                        "supplier": s_title,
                        "supplier_name": s_title,
                        "catalog_vehicle": vo.get("vehicle_label", ""),
                    }
                    for l in vo_listings
                ],
                query=state.get("request", ""),
                requested_part=profile.requested_part or profile.hints.get("requested_part") or profile.part or profile.category or "",
                category=profile.category or profile.hints.get("category") or "",
                target_maker=target_maker,
                target_model=target_model,
                target_year=target_year,
                target_variant=target_variant,
            )
            vo["listings"] = relevance.rank_listings(classified_vo, price_order="asc")

        all_listings = [
            listing
            for vo in vendor_outcomes
            for listing in vo.get("listings", [])
        ]
        ranked = relevance.rank_listings(all_listings, price_order="asc")

        return _profile_updates(profiles) | {"outcome": {
            "status": "ok",
            "vehicle_label": " ".join(x for x in (
                profile.maker, profile.model, profile.year, profile.variant,
            ) if x),
            "listings": ranked,
            "vendor_outcomes": vendor_outcomes,
            "source_url": None,
            "message": cache_msg,
            "is_cached": True,
            "is_demo": is_demo,
            "source_type": "demo" if is_demo else "cached",
        }}

    async def collect_vendor(adapter, vendor_profile: VehicleProfile):
        t0 = time.time()
        try:
            res = await adapter.collect(vendor_profile)
            if not getattr(res, "elapsed_seconds", 0.0):
                res.elapsed_seconds = round(time.time() - t0, 2)
            return res
        except Exception as e:
            elapsed = round(time.time() - t0, 2)
            log.exception("%s collection failed", adapter.name)
            err_str = str(e).lower()
            if any(k in err_str for k in ("challenge", "cloudflare", "turnstile", "verify you are human")):
                status = "challenge_detected"
                msg = f"{adapter.name.title()} bot-protection challenge encountered; automated search safely skipped."
            elif any(k in err_str for k in ("blocked", "denied", "403", "429")):
                status = "blocked"
                msg = f"{adapter.name.title()} access blocked (403/429); automated search safely skipped."
            else:
                status = "failed"
                msg = f"{adapter.name} search failed: {e}"
            return SearchOutcome(
                vendor=adapter.name,
                status=status,
                vehicle_label=" ".join(x for x in (
                    vendor_profile.maker, vendor_profile.model, vendor_profile.year,
                    vendor_profile.variant,
                ) if x),
                listings=[],
                source_url=getattr(adapter, "last_search_url", None) or (getattr(adapter, "page", None) and getattr(adapter.page, "url", "")),
                message=msg,
                elapsed_seconds=elapsed,
            )

    BOODMO_TIMEOUT = float(os.environ.get("BOODMO_TIMEOUT_SECONDS", "30.0"))
    AUTODUKAN_TIMEOUT = float(os.environ.get("AUTODUKAN_TIMEOUT_SECONDS", "30.0"))

    async def collect_vendor_safe(name: str, adapter, vendor_profile: VehicleProfile):
        timeout_val = AUTODUKAN_TIMEOUT if name == "autodukan" else BOODMO_TIMEOUT
        t0 = time.time()
        try:
            return await asyncio.wait_for(collect_vendor(adapter, vendor_profile), timeout=timeout_val)
        except asyncio.TimeoutError:
            elapsed = round(time.time() - t0, 2)
            log.warning("%s collection timed out after %.1fs", name, timeout_val)
            return SearchOutcome(
                vendor=name,
                status="timeout",
                vehicle_label=" ".join(x for x in (
                    vendor_profile.maker, vendor_profile.model, vendor_profile.year,
                    vendor_profile.variant,
                ) if x),
                listings=[],
                source_url=getattr(adapter, "last_search_url", None) or "",
                message=f"{name.title()} search timed out after {int(timeout_val)} seconds; continuing.",
                elapsed_seconds=elapsed,
            )
        finally:
            try:
                await adapter.close()
            except Exception as e:
                log.warning("Error closing %s adapter: %s", name, e)

    sem = get_search_semaphore()
    is_queued = sem.locked()
    if is_queued:
        s.announce(f"Search queued. Waiting for server worker capacity (max concurrent: {MAX_CONCURRENT_SEARCHES})...")
        if hasattr(s, "on_queued") and callable(s.on_queued):
            try:
                s.on_queued()
            except Exception as q_err:
                log.warning("Error in on_queued callback: %s", q_err)

    async with sem:
        if is_queued:
            s.announce("Server search capacity acquired. Checking prices and stock across suppliers...")
        t_start_all = time.time()
        perf_monitor.log_search_started(thread_id, state.get("request", ""))

        # Progressive concurrent supplier collection via asyncio.as_completed
        supplier_tasks = {
            asyncio.create_task(collect_vendor_safe(name, adapter, profiles[name])): name
            for name, adapter in s.adapters.items()
        }

        outcomes = []
        from . import relevance
        target_maker = profile.maker or profile.hints.get("make") or ""
        target_model = profile.model or profile.hints.get("model") or ""
        target_year = profile.year or str(profile.hints.get("year") or "")
        target_variant = profile.variant or ""
        req_part = profile.requested_part or profile.hints.get("requested_part") or profile.part or profile.category or ""

        for fut in asyncio.as_completed(supplier_tasks.keys()):
            outcome = await fut
            elapsed = getattr(outcome, "elapsed_seconds", 0.0)

            raw_count = getattr(outcome, "raw_count", None)
            if raw_count is None:
                raw_count = len(outcome.listings) if outcome.listings else 0
            outcome.raw_count = raw_count
            raw_listings = list(outcome.listings) if outcome.listings else []
            outcome.raw_listings = raw_listings

            v_name = outcome.vendor
            s_title = "Boodmo" if v_name == "boodmo" else "AutoDukan" if v_name == "autodukan" else v_name.title()

            classified_vo = relevance.classify_listings(
                [
                    {
                        **(l.to_dict() if hasattr(l, "to_dict") else dict(l)),
                        "vendor": v_name,
                        "supplier": s_title,
                        "supplier_name": s_title,
                        "catalog_vehicle": outcome.vehicle_label or "",
                    }
                    for l in raw_listings
                ],
                query=state.get("request", ""),
                requested_part=req_part,
                category=profile.category or profile.hints.get("category") or "",
                target_maker=target_maker,
                target_model=target_model,
                target_year=target_year,
                target_variant=target_variant,
            )
            ranked_listings = relevance.rank_listings(classified_vo, price_order="asc")
            outcome.listings = [
                Listing.from_dict(d) if not isinstance(d, Listing) else d
                for d in ranked_listings
            ]
            rel_count = len(ranked_listings)

            if rel_count > 0:
                s.announce(f"{s_title} completed: {rel_count} matching {req_part.lower() or 'parts'} found ({elapsed:.1f}s)")
            elif raw_count > 0:
                s.announce(f"{s_title} completed: {raw_count} catalogue listings extracted, 0 matching {req_part.lower() or 'parts'} ({elapsed:.1f}s)")
            else:
                s.announce(f"{s_title} completed: 0 parts found ({elapsed:.1f}s)")

            if hasattr(s, "on_supplier_completed") and callable(s.on_supplier_completed):
                try:
                    s.on_supplier_completed(outcome)
                except Exception as cb_err:
                    log.warning("Error in on_supplier_completed callback: %s", cb_err)

            outcomes.append(outcome)

        total_elapsed = round(time.time() - t_start_all, 2)
        perf_monitor.log_search_completed(thread_id, total_elapsed)

    vendor_outcomes = []
    for outcome in outcomes:
        vo = asdict(outcome)
        v_name = vo.get("vendor", "")
        s_title = "Boodmo" if v_name == "boodmo" else "AutoDukan" if v_name == "autodukan" else v_name.title()
        vo["vendor"] = v_name
        vo["supplier"] = s_title
        vo["supplier_name"] = s_title
        vo["raw_count"] = getattr(outcome, "raw_count", len(outcome.listings))
        vendor_outcomes.append(vo)

    # Save newly scraped live outcomes to cache for 24 hours
    try:
        if any(o.get("listings") for o in vendor_outcomes):
            cache_manager.save_cached_search(
                maker=profile.maker,
                model=profile.model,
                year=profile.year,
                variant=profile.variant,
                part=req_part,
                outcomes=vendor_outcomes,
                ttl_hours=24.0,
            )
    except Exception as exc:
        log.warning("Could not cache search outcomes: %s", exc)

    all_listings = [
        listing
        for vo in vendor_outcomes
        for listing in vo.get("listings", [])
    ]
    all_listings = relevance.rank_listings(all_listings, price_order="asc")

    statuses = {outcome.status for outcome in outcomes}
    status = "ok" if "ok" in statuses else "no_results" if "no_results" in statuses else "failed"
    return _profile_updates(profiles) | {"outcome": {
        "status": status,
        "vehicle_label": " ".join(x for x in (
            profile.maker, profile.model, profile.year, profile.variant,
        ) if x),
        "listings": all_listings,
        "vendor_outcomes": vendor_outcomes,
        "source_url": None,
        "message": "; ".join(outcome.message for outcome in outcomes if outcome.message),
    }}


async def present(state: State) -> State:
    if state.get("cancelled"):
        summary = "Okay, search cancelled. Nothing was ordered."
    elif state.get("error"):
        summary = f"Sorry, {state['error']}"
    else:
        summary = summarize(VehicleProfile.from_dict(state["profile"]), state["outcome"])
    return {"summary": summary, "turns": [{"t": time.time(), "role": "agent", "text": summary}]}


# ---------------------------------------------------------------- summary

def _a(word: str) -> str:
    """Article for a part name; plurals ("brake pads") take none."""
    w = word.strip().lower()
    if w.endswith("s") and not w.endswith("ss"):
        return ""
    return "an " if w[:1] in "aeiou" else "a "


def _rupees(x: float) -> str:
    return f"₹{x:,.0f}" if x == int(x) else f"₹{x:,.2f}"


def _name(l: dict) -> str:
    return f"{l['brand'].title()} {l['title'].title()}".replace("_", " ")


def summarize(profile: VehicleProfile, outcome: dict) -> str:
    req_part = (
        profile.requested_part
        or profile.hints.get("requested_part")
        or profile.part
        or profile.category
        or "parts"
    ).strip().title()

    vehicle = " ".join(str(value) for value in (
        profile.maker, profile.model, profile.year, profile.variant
    ) if value) or "the requested vehicle"

    if outcome.get("vendor_outcomes") is not None:
        prefix = f"Your {vehicle} {req_part} search is complete."
        summaries = [
            _summarize_vendor(profile, vendor_outcome)
            for vendor_outcome in outcome["vendor_outcomes"]
        ]
        combined = " ".join(summary for summary in summaries if summary)
        return f"{prefix} {combined}".strip()

    return _summarize_boodmo(profile, outcome)


def _summarize_vendor(profile: VehicleProfile, outcome: dict) -> str:
    vendor_key = (outcome.get("vendor") or "").lower()
    vendor = "Boodmo" if vendor_key == "boodmo" else "AutoDukan" if vendor_key == "autodukan" else (outcome.get("supplier") or vendor_key.title() or "Supplier")
    req_part = (
        profile.requested_part
        or profile.hints.get("requested_part")
        or profile.part
        or profile.category
        or "parts"
    ).strip().title()
    label = outcome.get("vehicle_label") or " ".join(x for x in (
        profile.maker, profile.model, profile.year, profile.variant,
    ) if x) or "the requested vehicle"

    status = outcome.get("status")
    if status in ("challenge_detected", "blocked"):
        return f"{vendor} access was blocked or bot-protected; automated search safely skipped."
    if status == "timeout":
        return f"{vendor} search timed out."
    if status != "ok" and status != "no_results":
        return f"{vendor}: {outcome.get('message') or 'search failed.'}"

    listings = outcome.get("listings") or []
    rel_count = len(listings)
    raw_count = outcome.get("raw_count")
    if raw_count is None:
        raw_count = len(outcome.get("raw_listings", [])) or rel_count

    if rel_count > 0:
        in_stock = [l for l in listings if l.get("available") is True]
        if in_stock:
            cheapest = min(in_stock, key=lambda l: l.get("price") or float("inf"))
            o = cheapest.get("best_offer") or {}
            price_val = cheapest.get("price")
            price_str = _rupees(price_val) if price_val is not None else ""
            brand_title = f"{cheapest.get('brand', '').title()} {cheapest.get('title', '').title()}".strip()
            part_str = f", part {cheapest.get('part_number')}" if cheapest.get("part_number") else ""
            seller = cheapest.get("seller") or o.get("seller")
            dispatch = cheapest.get("dispatch") or o.get("dispatch")
            if seller:
                seller_info = f" from seller {seller}, in stock, dispatch in {dispatch or '24–48 hours'}."
            else:
                seller_info = f" at {price_str}."
            main_text = f"{vendor} returned {rel_count} relevant product{'s' if rel_count != 1 else ''}. Cheapest in stock: {brand_title}{part_str}, {price_str}{seller_info}".strip()
            other_items = [l for l in in_stock if l != cheapest][:2]
            if other_items:
                extras = [f"{l.get('brand', '').title()} {l.get('title', '').title()} ({'part ' + l['part_number'] + ', ' if l.get('part_number') else ''}{_rupees(l['price']) if l.get('price') is not None else ''})".strip() for l in other_items]
                main_text += f" Also in stock: {'; '.join(extras)}."
            return main_text
        return f"{vendor} returned {rel_count} relevant product{'s' if rel_count != 1 else ''}."

    if raw_count > 0:
        quantifier = "neither" if raw_count == 2 else "none"
        return f"{vendor} extracted {raw_count} catalogue listing{'s' if raw_count != 1 else ''}, but {quantifier} matched the requested {req_part} category."

    return f"{vendor} returned no matching products."


def _summarize_boodmo(profile: VehicleProfile, outcome: dict) -> str:
    vehicle = " ".join(str(value) for value in (
        profile.maker, profile.model, profile.year, profile.variant
    ) if value) or "the requested vehicle"
    if outcome.get("status") == "no_results":
        return f"For the {vehicle}: {outcome.get('message', 'No parts found.')}"
    if outcome.get("status") != "ok":
        return f"For the {vehicle}: {outcome.get('message', 'Search failed.')} I won't suggest a part I can't verify."
    ls = outcome.get("listings") or []
    exact = [l for l in ls if l.get("match", "exact") == "exact"]
    related = len(ls) - len(exact)
    in_stock = [l for l in exact if l.get("available") is True]
    unchecked = [l for l in exact if l.get("available") is None]
    n = len(exact)
    raw_count = outcome.get("raw_count")
    if raw_count is None:
        raw_count = len(outcome.get("raw_listings", [])) or len(ls)

    active_cat = (
        profile.requested_part
        or profile.hints.get("requested_part")
        or profile.part
        or profile.category
        or "parts"
    ).strip().title()

    if n == 0 and raw_count > 0:
        quantifier = "neither" if raw_count == 2 else "none"
        return f"For the {vehicle}: Boodmo extracted {raw_count} catalogue listing{'s' if raw_count != 1 else ''}, but {quantifier} matched the requested {active_cat} category."

    lines = [f"For the {vehicle}, Boodmo lists {n} part{'s' if n != 1 else ''} under "
             f"{active_cat} that fit this variant."]
    if in_stock:
        b = in_stock[0]
        o = b.get("best_offer")
        price_val = b.get("price")
        price_str = _rupees(price_val) if price_val is not None else ""
        if o and isinstance(o, dict) and o.get("seller"):
            lines.append(f"Cheapest in stock: {_name(b)}, part {b.get('part_number', '')}, {price_str}"
                         f" from seller {o.get('seller')}, {o.get('stock', 'in stock')}, dispatch in {o.get('dispatch', '1-2 days')}.")
        else:
            lines.append(f"Cheapest in stock: {_name(b)}, part {b.get('part_number', '')}, {price_str}.")
        others = [f"{_name(l)} at {_rupees(l['price'])}" for l in in_stock[1:3] if l.get("price") is not None]
        if others:
            lines.append("Also in stock: " + "; ".join(others) + ".")
    else:
        lines.append("None of them has a seller with stock right now.")
    if unchecked:
        lines.append(f"{len(unchecked)} more have a listed price but I didn't check their stock.")
    gone = n - len(in_stock) - len(unchecked)
    if gone and in_stock:
        lines.append(f"{gone} {'are' if gone > 1 else 'is'} listed with no seller in stock.")
    if related:
        lines.append(f"I left out {related} related part{'s' if related > 1 else ''} like assemblies or housings.")
    if any(l.get("fitment") == "partial" for l in exact):
        lines.append("Some only fit one of Boodmo's catalogue entries for this variant, so they're marked partial.")
    lines.append("Boodmo doesn't give seller addresses, so I haven't ranked by distance. Nothing has been ordered.")
    return " ".join(lines)


# ---------------------------------------------------------------- graph

def _after_understand(state: State) -> str:
    return "interpret" if state.get("pending") else "navigate"


def _after_navigate(state: State) -> str:
    if state.get("error"):
        return "present"
    return "interpret" if state.get("pending") else "collect"


def _after_interpret(state: State) -> str:
    if state.get("cancelled"):
        return "present"
    if state.get("confirmed"):
        return "collect"
    return "interpret" if state.get("pending") else "navigate"


def build_graph():
    g = StateGraph(State)
    g.add_node("understand", understand)
    g.add_node("navigate", navigate)
    g.add_node("interpret", interpret)
    g.add_node("collect", collect)
    g.add_node("present", present)
    g.add_edge(START, "understand")
    g.add_conditional_edges("understand", _after_understand, ["interpret", "navigate"])
    g.add_conditional_edges("navigate", _after_navigate, ["interpret", "collect", "present"])
    g.add_conditional_edges("interpret", _after_interpret, ["interpret", "navigate", "collect", "present"])
    g.add_edge("collect", "present")
    g.add_edge("present", END)
    return g.compile(checkpointer=InMemorySaver(), interrupt_before=["interpret"])


class Conversation:
    """One search conversation. `start()` and `reply()` return the next question
    (a NeedInput dict) or None when the search is finished; then read `.state`."""

    def __init__(self, thread_id: str):
        self.graph = build_graph()
        self.config = {"configurable": {"thread_id": thread_id}}
        self.state: dict = {}

    async def _run(self, inp) -> Optional[dict]:
        self.state = await self.graph.ainvoke(inp, self.config)
        snap = await self.graph.aget_state(self.config)
        if snap.next == ("interpret",):
            return self.state.get("pending")
        return None

    async def start(self, request: str) -> Optional[dict]:
        return await self._run({"request": request})

    async def reply(self, answer: str) -> Optional[dict]:
        await self.graph.aupdate_state(self.config, {"answer": answer})
        return await self._run(None)


def save_run(thread_id: str, state: dict) -> Path:
    RUNS.mkdir(exist_ok=True)
    path = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{thread_id[:8]}.json"
    keep = {k: state.get(k) for k in (
        "request", "profile", "supplier_profiles", "outcome", "summary", "error", "cancelled", "turns"
    )}
    path.write_text(json.dumps(keep, indent=2, default=str, ensure_ascii=False))
    return path
