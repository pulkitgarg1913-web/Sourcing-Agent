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
from .models import NeedInput, SearchOutcome, VehicleProfile

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

    @property
    def adapters(self) -> dict[str, object]:
        return {
            "boodmo": self.boodmo_adapter,
            "autodukan": self.autodukan_adapter,
        }


SESSIONS: dict[str, Session] = {}


def open_session(thread_id: str, *, headless: bool = True,
                 progress: Callable[[str], None] | None = None,
                 announce: Callable[[str], None] | None = None,
                 record_video_dir: Optional[str] = None,
                 require_confirmation: bool = False) -> Session:
    s = Session(boodmo_adapter=BoodmoAdapter(headless=headless, progress=progress, record_video_dir=record_video_dir),
                autodukan_adapter=AutoDukanAdapter(headless=headless, progress=progress, record_video_dir=record_video_dir),
                announce=announce or (lambda t: None),
                require_confirmation=require_confirmation)
    SESSIONS[thread_id] = s
    return s


async def close_session(thread_id: str):
    s = SESSIONS.pop(thread_id, None)
    if s:
        adapters = list(s.adapters.values())
        results = await asyncio.gather(*(adapter.close() for adapter in adapters),
                                       return_exceptions=True)
        for adapter, result in zip(adapters, results):
            if isinstance(result, Exception):
                log.warning("Could not close %s browser session: %s", adapter.name, result)


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
    if not profile.year and (m := re.search(r"\b(19[89]\d|20[0-4]\d)\b", t)):
        profile.hints.setdefault("year", m.group(1))
    for fuel in ("diesel", "petrol", "cng", "electric"):
        if re.search(rf"\b{fuel}\b", t):
            profile.hints.setdefault("fuel", fuel)
    if re.search(r"\b(automatic|auto)\b", t):
        profile.hints.setdefault("transmission", "automatic")
    elif re.search(r"\bmanual\b", t):
        profile.hints.setdefault("transmission", "manual")


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
        if boodmo_p.variant:
            p.hints["boodmo_variant"] = boodmo_p.variant
        if not p.part and boodmo_p.part:
            p.part = boodmo_p.part
        profiles[vendor] = p
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
    profile.part = parsed.part
    profile.hints = {k: v for k, v in parsed.model_dump().items()
                     if v and k not in ("part",)}
    profile.hints["raw_request"] = text
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

    async def advance_vendor(name: str, adapter):
        try:
            return await adapter.advance(profiles[name])
        except Exception:
            log.exception("%s navigation failed", name)
            return None

    vendors = list(session.adapters.items())
    needs = await asyncio.gather(*(
        advance_vendor(name, adapter) for name, adapter in vendors
    ))
    # Each website advances concurrently. Ask one supplier's question at a
    # time, carrying its identity so answers update only that supplier profile.
    # If Boodmo has resolved its variant, pass it to AutoDukan immediately
    # so AutoDukan can select its variant without waiting or needing an extra mechanic turn.
    boodmo_p = profiles.get("boodmo")
    if boodmo_p and boodmo_p.variant and "autodukan" in session.adapters:
        autodukan_p = profiles.get("autodukan")
        if autodukan_p and not autodukan_p.variant:
            autodukan_p.hints["boodmo_variant"] = boodmo_p.variant
            for i, (vendor, adapter) in enumerate(vendors):
                if vendor == "autodukan":
                    needs[i] = await advance_vendor("autodukan", adapter)

    valid_needs = []
    boodmo_has_active_need = any(v == "boodmo" and n is not None for (v, _), n in zip(vendors, needs))
    for (vendor, _adapter), vendor_need in zip(vendors, needs):
        if not vendor_need:
            continue
        # If Boodmo is present in the session and actively has a need, let Boodmo lead the variant questions.
        # AutoDukan will match Boodmo's resolved variant automatically once finalized.
        if vendor == "autodukan" and vendor_need.field in ("variant", "variant_attr"):
            if "boodmo" in session.adapters and boodmo_has_active_need:
                continue
        valid_needs.append(_need_for_vendor(vendor_need, vendor))

    need = valid_needs[0] if valid_needs else None
    if not need and getattr(session, "require_confirmation", False) and not state.get("confirmed"):
        active_p = boodmo_p or profiles.get("autodukan")
        maker = (boodmo_p and boodmo_p.maker) or (profiles.get("autodukan") and profiles["autodukan"].maker) or ""
        model = (boodmo_p and boodmo_p.model) or (profiles.get("autodukan") and profiles["autodukan"].model) or ""
        year = (boodmo_p and boodmo_p.year) or (profiles.get("autodukan") and profiles["autodukan"].year) or ""
        variant = (boodmo_p and boodmo_p.variant) or (profiles.get("autodukan") and profiles["autodukan"].variant) or ""
        part = (active_p and (active_p.part or active_p.category)) or "parts"
        car_label = " ".join(x for x in (maker, model, year, variant) if x)
        need = {
            "field": "confirm_search",
            "question": f"Ready to search suppliers for {part} fitting {car_label}?",
            "options": ["Confirm & Search Suppliers", "Change Vehicle Details"],
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
        # If user asked to modify or gave new hints, let hints be applied
        p = dict(p, note="You can confirm or tell me which vehicle detail to change.")
        yes = llm.is_yes(ans)
        orig = p["original"]
        if yes:
            vendor = orig.get("vendor", vendor)
            _apply(profiles[vendor], orig, p["candidate"], config, profiles, vendor)
            return _resolved(profiles, turn, p["candidate"])
        if yes is False:
            orig = dict(orig, note="Okay.")
            return _profile_updates(profiles) | {
                "pending": orig, "turns": [turn | {"resolved": None}]
            }
        p = orig   # they answered the original question instead of yes/no; match it below
        vendor = p.get("vendor", vendor)
        profile = profiles[vendor]
        siblings = _other_profiles(profiles, vendor)

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
            for k, v in parsed.model_dump().items():
                if v and k != "part":
                    for vp in profiles.values():
                        vp.hints.setdefault(k, v)
        for vendor_profile in profiles.values():
            vendor_profile.part = resolved_part
            vendor_profile.category = None
            _quick_hints(ans, vendor_profile)
        car = " ".join(x for x in (profile.maker or profile.hints.get("make"), profile.model or profile.hints.get("model")) if x)
        _session(config).announce(f"Okay, looking for {_a(resolved_part)}{resolved_part}"
                                  + (f" for a {car}" if car else "")
                                  + " on Boodmo and AutoDukan.")
        return _resolved(profiles, turn, resolved_part)

    options = p.get("options") or []
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
        if vendor == "boodmo":
            _session(config).boodmo_adapter.choose_category(profile, value)
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
                sibling.variant_filters = {}
                sibling.variant = None
                sibling.model = sibling.year = None
                if changed_hint:
                    for key in ("trim", "engine", "fuel", "transmission"):
                        sibling.hints.pop(key, None)
            elif f == "year":
                if changed_hint:
                    sibling.year = None
                    sibling.variant = None
                    sibling.variant_filters = {}
    elif f == "variant_attr":
        profile.variant_filters[need["attr"]] = value
        for sibling in _other_profiles(profiles, vendor):
            if sibling.hints.get(need["attr"]) != value:
                sibling.variant = None
                sibling.variant_filters = {}
            sibling.hints[need["attr"]] = value
    elif f == "variant":
        profile.variant = value
        for sibling in _other_profiles(profiles, vendor):
            sibling.hints["boodmo_variant"] = value
            sibling.hints["variant"] = value


async def collect(state: State, config) -> State:
    profiles = _profiles_from_state(state)
    profile = profiles["boodmo"]
    s = _session(config)
    s.announce("Got the vehicle details. Checking prices and stock across all suppliers.")

    async def collect_vendor(adapter, vendor_profile: VehicleProfile):
        try:
            return await adapter.collect(vendor_profile)
        except Exception as e:
            log.exception("%s collection failed", adapter.name)
            return SearchOutcome(
                vendor=adapter.name,
                status="failed",
                vehicle_label=" ".join(x for x in (
                    vendor_profile.maker, vendor_profile.model, vendor_profile.year,
                    vendor_profile.variant,
                ) if x),
                listings=[],
                source_url=getattr(adapter, "last_search_url", None),
                message=f"{adapter.name} search failed: {e}",
            )

    vendors = list(s.adapters.items())
    outcomes = []
    for name, adapter in vendors:
        outcomes.append(await collect_vendor(adapter, profiles[name]))
        try:
            await adapter.close()
        except Exception as e:
            log.warning("Error closing %s adapter: %s", name, e)
    vendor_outcomes = [asdict(outcome) for outcome in outcomes]
    all_listings = [
        {**listing, "vendor": outcome.vendor}
        for outcome in outcomes
        for listing in outcome.listings
    ]
    from . import relevance

    # Classify all extracted listings against customer request
    all_listings = relevance.classify_listings(
        all_listings,
        query=state.get("request", ""),
        requested_part=profile.part or ""
    )
    # Rank listings so primary matches appear first, followed by components, then accessories
    all_listings = relevance.rank_listings(all_listings, price_order="asc")

    # Also classify listings within each vendor_outcome
    for vo in vendor_outcomes:
        vo_listings = vo.get("listings") or []
        vo["listings"] = relevance.rank_listings(
            relevance.classify_listings(vo_listings, query=state.get("request", ""), requested_part=profile.part or ""),
            price_order="asc"
        )

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
    if outcome.get("vendor_outcomes") is not None:
        summaries = [
            _summarize_vendor(profile, vendor_outcome)
            for vendor_outcome in outcome["vendor_outcomes"]
        ]
        return " ".join(summary for summary in summaries if summary)

    return _summarize_boodmo(profile, outcome)


def _summarize_vendor(profile: VehicleProfile, outcome: dict) -> str:
    if outcome.get("vendor") == "boodmo":
        return _summarize_boodmo(profile, outcome)

    vendor = {
        "autodukan": "AutoDukan",
    }.get(outcome.get("vendor"), outcome.get("vendor") or "Supplier")
    label = outcome.get("vehicle_label") or " ".join(x for x in (
        profile.maker, profile.model, profile.year, profile.variant,
    ) if x)
    if outcome.get("status") == "no_results":
        return outcome.get("message") or f"{vendor} returned no product listings for {label}."
    if outcome.get("status") != "ok":
        return f"{vendor}: {outcome.get('message') or 'search failed.'}"
    listings = outcome.get("listings") or []
    if not listings:
        return f"{vendor} returned no product listings for {label}."
    verified = [l for l in listings if l.get("fitment") == "verified"]
    in_stock = [l for l in listings if l.get("available") is True]
    if in_stock:
        cheapest = min(in_stock, key=lambda l: l.get("price") or float("inf"))
        price_str = _rupees(cheapest["price"]) if cheapest.get("price") is not None else ""
        return (f"{vendor} lists {len(listings)} verified part{'s' if len(listings) != 1 else ''} for {label}. "
                f"Cheapest in stock is {cheapest.get('brand')} {cheapest.get('title')} at {price_str}.")
    if verified:
        return f"{vendor} lists {len(listings)} verified part{'s' if len(listings) != 1 else ''} for {label}."
    return (f"{vendor} returned {len(listings)} listing{'s' if len(listings) != 1 else ''} for {label}. "
            "Confirm compatibility before purchase.")


def _summarize_boodmo(profile: VehicleProfile, outcome: dict) -> str:
    vehicle = " ".join(str(value) for value in (
        profile.maker, profile.model, profile.year, profile.variant
    ) if value) or "the requested vehicle"
    if outcome["status"] == "no_results":
        return f"For the {vehicle}: {outcome['message']}"
    if outcome["status"] != "ok":
        return f"For the {vehicle}: {outcome['message']} I won't suggest a part I can't verify."
    ls = outcome["listings"]
    exact = [l for l in ls if l["match"] == "exact"]
    related = len(ls) - len(exact)
    in_stock = [l for l in exact if l["available"] is True]
    unchecked = [l for l in exact if l["available"] is None]
    n = len(exact)
    active_cat = profile.hints.get("resolved_alternate_category") or profile.category
    lines = [f"For the {vehicle}, Boodmo lists {n} part{'s' if n != 1 else ''} under "
             f"{active_cat} that fit this variant."]
    if in_stock:
        b = in_stock[0]
        o = b["best_offer"]
        lines.append(f"Cheapest in stock: {_name(b)}, part {b['part_number']}, {_rupees(b['price'])}"
                     f" from seller {o['seller']}, {o['stock']}, dispatch in {o['dispatch']}.")
        others = [f"{_name(l)} at {_rupees(l['price'])}" for l in in_stock[1:3]]
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
    if any(l["fitment"] == "partial" for l in exact):
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
    g.add_conditional_edges("interpret", _after_interpret, ["interpret", "navigate", "present"])
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
