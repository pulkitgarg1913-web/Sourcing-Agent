"""Turn a long list of supplier variant labels into a few spoken questions.

Boodmo labels look like "2.0L XZA+ AT/Diesel/BS6" or
"2.0L XZ+ MT/Diesel/KYROTEC TURBOCHARGED/BS6". Reading 22 of these aloud is
useless, so we split them into attributes and ask about the attribute that
best splits the remaining options, until a short list is left.
"""
from __future__ import annotations

import re

FUELS = ("DIESEL", "PETROL", "CNG", "LPG", "ELECTRIC", "HYBRID")
TRANSMISSIONS = ("AMT", "AGS", "CVT", "DCT", "DSG", "IVT", "IMT", "AT", "MT")
EMISSIONS = re.compile(r"^BS\s?[2-6IV]+(\.\d)?$")

# Order in which a mechanic can most easily answer.
ATTR_ORDER = ("fuel", "transmission", "engine", "trim", "spec", "emission")
OTHER = "OTHER"          # label has no value for this attribute
MAX_LISTED_VALUES = 8    # beyond this, ask open-ended with a few examples

ATTR_QUESTIONS = {
    "fuel": "Is it {opts}?",
    "engine": "Which engine is it: {opts}?",
    "transmission": "Is it {opts}?",
    "trim": "Which trim is it: {opts}?",
    "spec": "Which version is it: {opts}?",
    "emission": "Is it {opts}?",
}

SPOKEN = {"MT": "manual", "AT": "automatic", "AMT": "AMT automatic", "AGS": "AGS automatic",
          "CVT": "CVT automatic", "DCT": "DCT automatic", "DSG": "DSG automatic",
          "IVT": "IVT automatic", "IMT": "clutchless manual (iMT)"}

# How many full labels we are willing to read out at once.
MAX_READOUT = 4

# Filter value recorded when the mechanic doesn't know an attribute.
SKIP = "*unknown*"


def parse_label(label: str) -> dict:
    """Split a modification label into comparable attributes."""
    parts = [p.strip() for p in label.split("/") if p.strip()]
    head = parts[0] if parts else label
    attrs = {"fuel": None, "engine": None, "transmission": None, "trim": None, "spec": None,
             "emission": None}

    tokens = head.split()
    if tokens and re.match(r"^\d+(\.\d+)?L$", tokens[0], re.I):
        attrs["engine"] = tokens.pop(0).upper()
    if tokens and tokens[-1].upper() in TRANSMISSIONS:
        attrs["transmission"] = tokens.pop().upper()
    if tokens:
        # Trim is the first token (XZ+, XZA, SX(O)...); drive layout etc. stays in spec.
        attrs["trim"] = tokens[0].upper()
        rest = [t for t in tokens[1:]]
    else:
        rest = []

    extras = []
    for p in parts[1:]:
        up = p.upper()
        if up in FUELS:
            attrs["fuel"] = up
        elif EMISSIONS.match(up):
            attrs["emission"] = up.replace(" ", "")
        else:
            extras.append(up)
    spec = " ".join(rest + extras).strip()
    attrs["spec"] = spec or "STANDARD"
    return attrs


def spoken_value(attr: str, value: str) -> str:
    if value == OTHER:
        return "something else"
    if attr == "emission":
        return value.replace("BS6.2", "BS6 phase 2")
    if attr == "transmission":
        return SPOKEN.get(value, value)
    if attr == "fuel":
        return value.lower()
    if attr == "trim":
        return value.replace("+", " plus")
    if attr == "spec":
        return value.lower()
    return value


def unique_labels(labels: list[str]) -> list[str]:
    seen, out = set(), []
    for l in labels:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out


def apply_filters(labels: list[str], filters: dict) -> list[str]:
    out = []
    active = {k: v for k, v in filters.items() if v != SKIP}
    for l in labels:
        a = parse_label(l)
        if all((a.get(k) or OTHER) == v for k, v in active.items()):
            out.append(l)
    return out


def apply_hints(labels: list[str], hints: dict) -> tuple[list[str], dict, dict]:
    """Narrow using details the mechanic already said, but only when the hint
    actually matches one of the site's values. Returns (labels, filters_applied,
    source) where source[attr] is "said" (from the request) or "mention"
    (said out of turn, which should be confirmed before use)."""
    applied, source = {}, {}
    cur = labels
    mentions = (hints or {}).get("mentions") or []
    for attr in ATTR_ORDER:
        if attr == "spec" or attr == "emission":
            continue
        values = {parse_label(l)[attr] for l in cur}
        said = (hints or {}).get(attr)
        match = match_attr_value(attr, str(said), values) if said else None
        origin = "said"
        if not match:
            match = next((m for h in mentions if (m := match_attr_value(attr, str(h), values))), None)
            origin = "mention"
        if match:
            nxt = apply_filters(cur, {attr: match})
            if nxt:
                cur, applied[attr], source[attr] = nxt, match, origin
    return cur, applied, source


def match_attr_value(attr: str, hint: str, values: set) -> str | None:
    h = hint.strip().upper()
    if attr == "transmission":
        if h in ("MANUAL", "MT"):
            h = "MT"
        elif h in ("AUTOMATIC", "AUTO", "AT"):
            # Prefer plain AT; otherwise any automatic type, if exactly one exists.
            autos = [v for v in values if v and v not in ("MT", "IMT", OTHER)]
            if "AT" in values:
                h = "AT"
            elif len(autos) == 1:
                h = autos[0]
    if attr == "trim":
        h = h.replace(" PLUS", "+").replace("PLUS", "+").replace(" ", "")
    if attr == "engine" and re.match(r"^\d+(\.\d+)?$", h):
        h = h + "L"
    return h if h in values else None


EASY_ATTRS = ("fuel", "transmission", "engine")   # quick to answer, few values
LAST_RESORT = ("spec", "emission")                 # hard for a mechanic to know


def _groups(labels: list[str], attr: str) -> dict:
    groups: dict = {}
    for l in labels:
        groups.setdefault(parse_label(l)[attr] or OTHER, []).append(l)
    return groups


def next_question(labels: list[str], exclude: tuple = ()) -> tuple[str, list[str]] | None:
    """Pick the attribute to ask about next, or None if the list is short enough
    (or can't be split further) to read out directly.

    Order: an easy attribute (fuel, manual/automatic, engine) if it removes a
    meaningful share of the options; then the trim badge; then whatever is
    left (version tags, emission norm) as a last resort."""
    uniq = unique_labels(labels)
    if len(uniq) <= MAX_READOUT:
        return None

    def values(groups):
        return sorted(groups, key=lambda v: (v == OTHER, v))

    for attr in EASY_ATTRS:
        if attr in exclude:
            continue
        g = _groups(uniq, attr)
        if len(g) >= 2 and len(g) <= MAX_LISTED_VALUES and max(map(len, g.values())) <= 0.85 * len(uniq):
            return attr, values(g)
    if "trim" not in exclude:
        g = _groups(uniq, "trim")
        if len(g) >= 2:
            return "trim", values(g)
    rest = [(a, _groups(uniq, a)) for a in ATTR_ORDER if a not in exclude]
    rest = [(a, g) for a, g in rest if len(g) >= 2]
    if not rest:
        return None
    attr, g = min(rest, key=lambda ag: (ag[0] in LAST_RESORT, max(map(len, ag[1].values()))))
    return attr, values(g)


def describe_differences(labels: list[str]) -> list[str]:
    """Short spoken descriptions naming only what differs between the labels,
    e.g. ["automatic", "manual, kyrotec turbocharged", "manual"]."""
    attrs = [parse_label(l) for l in labels]
    differing = [a for a in ATTR_ORDER if len({x[a] for x in attrs}) > 1]
    out = []
    for l, x in zip(labels, attrs):
        bits = [spoken_value(a, x[a]) for a in differing if x[a] and x[a] != "STANDARD"]
        out.append(", ".join(bits) or "standard")
    if len(set(out)) < len(out):
        return labels  # can't tell them apart briefly; read the full labels
    return out


def speak_label(label: str) -> str:
    """'2.0L XZ+ BLACK MT/Diesel/BS6' -> '2.0L XZ plus black, manual, diesel'."""
    a = parse_label(label)
    head = " ".join(x for x in (a["engine"], spoken_value("trim", a["trim"]) if a["trim"] else None,
                                a["spec"].lower() if a["spec"] != "STANDARD" else None) if x)
    tail = [spoken_value("transmission", a["transmission"]) if a["transmission"] else None,
            a["fuel"].lower() if a["fuel"] else None]
    return ", ".join([head] + [t for t in tail if t])


def question_for(attr: str, values: list[str], labels: list[str] | None = None) -> str:
    if len(values) > MAX_LISTED_VALUES:
        # Too many to read out: ask open-ended with the most common values as examples.
        counts = {v: 0 for v in values}
        for l in unique_labels(labels or []):
            counts[parse_label(l)[attr] or OTHER] = counts.get(parse_label(l)[attr] or OTHER, 0) + 1
        common = [v for v in sorted(values, key=lambda v: -counts.get(v, 0)) if v != OTHER][:3]
        examples = ", ".join(spoken_value(attr, v) for v in common)
        return f"Which {attr} is it? For example {examples}."
    spoken = [spoken_value(attr, v) for v in values]
    opts = ", ".join(spoken[:-1]) + " or " + spoken[-1] if len(spoken) > 1 else spoken[0]
    return ATTR_QUESTIONS[attr].format(opts=opts)
