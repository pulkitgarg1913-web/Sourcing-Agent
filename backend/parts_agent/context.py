"""Centralized Vehicle Context Management and Supplier Clarification Resolution.

Maintains a single source of truth for vehicle information across the conversational
lifecycle and all supplier adapters. Tracks provenance (user_provided, inferred,
verified, unknown) and prevents arbitrary default assignments.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .models import NeedInput, VehicleProfile

log = logging.getLogger(__name__)


class AttributeSource(str, Enum):
    USER_PROVIDED = "user_provided"
    INFERRED = "inferred"
    VERIFIED = "verified"
    UNKNOWN = "unknown"


# Normalization lookup maps
FUEL_NORMALIZATION = {
    "diesel": "Diesel",
    "petrol": "Petrol",
    "gasoline": "Petrol",
    "cng": "CNG",
    "lpg": "LPG",
    "electric": "Electric",
    "ev": "Electric",
    "hybrid": "Hybrid",
}

TRANSMISSION_NORMALIZATION = {
    "manual": "Manual",
    "mt": "Manual",
    "automatic": "Automatic",
    "auto": "Automatic",
    "at": "Automatic",
    "amt": "AMT",
    "ags": "AMT",
    "cvt": "CVT",
    "ivt": "IVT",
    "dct": "DCT",
    "dsg": "DSG",
    "imt": "iMT",
}


def normalize_fuel(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    cleaned = str(val).strip().lower()
    return FUEL_NORMALIZATION.get(cleaned, val.strip().title())


def normalize_transmission(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    cleaned = str(val).strip().lower()
    return TRANSMISSION_NORMALIZATION.get(cleaned, val.strip().upper() if len(val) <= 4 else val.strip().title())


def normalize_trim(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    v = str(val).strip()
    if v.lower() in ("unknown", "null", "none", "*unknown*"):
        return None
    # Normalize common spacing around plus signs
    v = v.replace(" PLUS", "+").replace("PLUS", "+")
    if v.upper() in ("X LINE", "X-LINE"):
        return "X-Line"
    return v


@dataclass
class VehicleContext:
    """Centralized single source of truth for vehicle context."""
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[str] = None
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    variant: Optional[str] = None
    trim: Optional[str] = None
    engine: Optional[str] = None
    requested_part: Optional[str] = None
    provenance: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.make:
            self.make = str(self.make).strip().title()
            self.provenance.setdefault("make", AttributeSource.USER_PROVIDED.value)
        if self.model:
            self.model = str(self.model).strip().title()
            self.provenance.setdefault("model", AttributeSource.USER_PROVIDED.value)
        if self.year:
            m = re.search(r"\b(19\d\d|20\d\d)\b", str(self.year))
            self.year = m.group(1) if m else str(self.year).strip()
            self.provenance.setdefault("year", AttributeSource.USER_PROVIDED.value)
        if self.fuel_type:
            self.fuel_type = normalize_fuel(self.fuel_type)
            self.provenance.setdefault("fuel_type", AttributeSource.USER_PROVIDED.value)
        if self.transmission:
            self.transmission = normalize_transmission(self.transmission)
            self.provenance.setdefault("transmission", AttributeSource.USER_PROVIDED.value)
        if self.variant:
            self.variant = normalize_trim(self.variant)
            self.provenance.setdefault("variant", AttributeSource.USER_PROVIDED.value)
        if self.trim:
            self.trim = normalize_trim(self.trim)
            self.provenance.setdefault("trim", AttributeSource.USER_PROVIDED.value)
        if self.variant and not self.trim:
            self.trim = self.variant
        elif self.trim and not self.variant:
            self.variant = self.trim

    def set_attribute(self, attr: str, value: Any, source: str = "user_provided", force: bool = False):
        """Set a vehicle attribute with normalization and provenance tracking.

        Never assigns 'Standard' or arbitrary defaults unless user explicitly said so.
        """
        canonical_attr = "fuel_type" if attr in ("fuel", "fuel_type") else attr
        if canonical_attr == "trim" and not self.variant:
            self.variant = normalize_trim(value)
        elif canonical_attr == "variant" and not self.trim:
            self.trim = normalize_trim(value)

        # Handle explicit skipping or unknown indicators
        if value is None or str(value).strip().lower() in (
            "unknown", "null", "none", "*unknown*", "other / not sure", "not sure", "don't know", "skip"
        ):
            setattr(self, canonical_attr, None)
            if canonical_attr in ("variant", "trim"):
                self.variant = None
                self.trim = None
            self.provenance[canonical_attr] = AttributeSource.UNKNOWN.value
            return

        # Normalization per attribute
        normalized: Optional[str] = None
        if canonical_attr == "fuel_type":
            normalized = normalize_fuel(value)
        elif canonical_attr == "transmission":
            normalized = normalize_transmission(value)
        elif canonical_attr in ("variant", "trim"):
            normalized = normalize_trim(value)
        elif canonical_attr == "year":
            m = re.search(r"\b(19\d\d|20\d\d)\b", str(value))
            normalized = m.group(1) if m else str(value).strip()
        elif canonical_attr in ("make", "model"):
            normalized = str(value).strip().title()
        else:
            normalized = str(value).strip()

        # Update field
        setattr(self, canonical_attr, normalized)
        if canonical_attr == "variant":
            self.trim = normalized
        elif canonical_attr == "trim":
            self.variant = normalized

        self.provenance[canonical_attr] = source

    def mark_unknown(self, attr: str):
        """Explicitly record an attribute as unknown."""
        canonical_attr = "fuel_type" if attr in ("fuel", "fuel_type") else attr
        setattr(self, canonical_attr, None)
        if canonical_attr in ("variant", "trim"):
            self.variant = None
            self.trim = None
        self.provenance[canonical_attr] = AttributeSource.UNKNOWN.value

    def is_known(self, attr: str) -> bool:
        """Check if an attribute has a verified or user-provided value."""
        canonical_attr = "fuel_type" if attr in ("fuel", "fuel_type") else attr
        val = getattr(self, canonical_attr, None)
        if not val:
            return False
        src = self.provenance.get(canonical_attr)
        return src != AttributeSource.UNKNOWN.value

    def get(self, attr: str, default: Any = None) -> Any:
        canonical_attr = "fuel_type" if attr in ("fuel", "fuel_type") else attr
        if not self.is_known(canonical_attr):
            return default
        return getattr(self, canonical_attr, default)

    def to_dict(self) -> dict:
        return {
            "make": self.make,
            "model": self.model,
            "year": int(self.year) if (self.year and self.year.isdigit()) else self.year,
            "fuel_type": self.fuel_type,
            "transmission": self.transmission,
            "variant": self.variant,
            "trim": self.trim,
            "engine": self.engine,
            "requested_part": self.requested_part,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "VehicleContext":
        if not d:
            return cls()
        ctx = cls(
            make=d.get("make"),
            model=d.get("model"),
            year=str(d.get("year")) if d.get("year") is not None else None,
            fuel_type=d.get("fuel_type") or d.get("fuel"),
            transmission=d.get("transmission"),
            variant=d.get("variant"),
            trim=d.get("trim") or d.get("variant"),
            engine=d.get("engine"),
            requested_part=d.get("requested_part"),
            provenance=dict(d.get("provenance") or {}),
        )
        return ctx

    def sync_to_profile(self, profile: VehicleProfile):
        """Synchronize context to a supplier VehicleProfile."""
        if self.make:
            profile.maker = self.make
            profile.hints["make"] = self.make
        if self.model:
            profile.model = self.model
            profile.hints["model"] = self.model
        if self.year:
            profile.year = self.year
            profile.hints["year"] = self.year
        if self.fuel_type:
            profile.hints["fuel"] = self.fuel_type
            profile.variant_filters["fuel"] = self.fuel_type.upper()
        if self.transmission:
            profile.hints["transmission"] = self.transmission
            profile.variant_filters["transmission"] = (
                "MT" if self.transmission.upper() in ("MANUAL", "MT")
                else "AT" if self.transmission.upper() in ("AUTOMATIC", "AT")
                else self.transmission.upper()
            )
        if self.variant:
            profile.hints["trim"] = self.variant
            profile.hints["variant"] = self.variant
        if self.engine:
            profile.hints["engine"] = self.engine
        if self.requested_part:
            profile.requested_part = self.requested_part
            profile.hints["requested_part"] = self.requested_part

    def sync_from_profile(self, profile: VehicleProfile):
        """Receive newly verified information from a supplier adapter."""
        if profile.maker and not self.make:
            self.set_attribute("make", profile.maker, source=AttributeSource.INFERRED.value)
        if profile.model and not self.model:
            self.set_attribute("model", profile.model, source=AttributeSource.INFERRED.value)
        if profile.year and not self.year:
            self.set_attribute("year", profile.year, source=AttributeSource.INFERRED.value)
        if profile.variant and not self.variant:
            self.set_attribute("variant", profile.variant, source=AttributeSource.VERIFIED.value)


# -----------------------------------------------------------------------------
# Part-Specific Clarification Requirement Determination
# -----------------------------------------------------------------------------

LIGHTING_AND_BODY_PARTS = (
    "headlight", "headlamp", "tail light", "taillight", "fog light", "fog lamp",
    "indicator", "turn signal", "drl", "daytime running light",
    "mirror", "side mirror", "orvm", "door mirror", "rear view mirror",
    "bumper", "grille", "fender", "bonnet", "hood", "tailgate", "spoiler",
    "steering wheel switch", "camera",
)

FUEL_AND_EXHAUST_PARTS = (
    "fuel pump", "fuel filter", "fuel injector", "fuel tank", "glow plug",
    "spark plug", "carburetor", "dpf", "egr", "catalytic converter", "exhaust",
    "turbocharger", "intercooler", "engine oil",
)

TRANSMISSION_AND_CLUTCH_PARTS = (
    "gearbox", "transmission", "clutch", "clutch plate", "clutch kit", "clutch set",
    "flywheel", "gear shift cable", "gear shifter", "gear lever",
    "transmission mount", "gearbox mount", "transmission fluid", "gear oil",
)

ENGINE_MECHANICAL_PARTS = (
    "timing belt", "timing chain", "piston", "piston rings", "cylinder head gasket",
    "head gasket", "radiator", "water pump", "alternator", "engine mount",
)

BRAKE_AND_SUSPENSION_PARTS = (
    "brake", "pad", "brake pad", "brake pads", "disc", "caliper", "rotor", "brake shoe",
    "shock absorber", "shocker", "strut", "suspension", "spring",
)


def get_part_required_attributes(part: str, make: str = "", model: str = "") -> list[str]:
    """Determine which vehicle attributes are genuinely necessary for the requested part.

    Avoids asking unnecessary questions (e.g. asking fuel type for headlights, or
    transmission for an oil filter).
    """
    clean_p = (part or "").lower().strip()
    mk = (make or "").lower().strip()
    md = (model or "").lower().strip()

    # Core identification is always required first
    required = ["make", "model", "year"]

    # 1. Lighting, mirrors, bumpers, and trim-dependent body parts
    if any(k in clean_p for k in LIGHTING_AND_BODY_PARTS):
        required.append("trim")
        # For Kia Seltos, Boodmo catalogue groups trims under fuel/transmission
        if (mk, md) == ("kia", "seltos") or md == "seltos":
            required.extend(["fuel", "transmission"])
        return required

    # 2. Brake and suspension components (trim decides ABS / disc size / booster)
    if any(k in clean_p for k in BRAKE_AND_SUSPENSION_PARTS):
        required.append("trim")
        return required

    # 2. Fuel and engine management systems
    if any(k in clean_p for k in FUEL_AND_EXHAUST_PARTS):
        required.append("fuel")
        return required

    # 3. Transmission, clutch, and shifter systems
    if any(k in clean_p for k in TRANSMISSION_AND_CLUTCH_PARTS):
        required.append("transmission")
        return required

    # 4. Engine internal components
    if any(k in clean_p for k in ENGINE_MECHANICAL_PARTS):
        required.append("engine")
        required.append("fuel")
        return required

    return required


# -----------------------------------------------------------------------------
# Supplier Clarification Resolution
# -----------------------------------------------------------------------------

def resolve_supplier_clarification(
    supplier: str,
    need: NeedInput,
    context: VehicleContext,
    profile: VehicleProfile
) -> tuple[bool, Optional[str]]:
    """Attempt to resolve a supplier clarification request using existing vehicle context.

    Returns (resolved: bool, matching_option: Optional[str]).
    If resolved, returns the concrete option matching verified context without
    prompting the mechanic again.
    """
    f = need.field
    attr_key: Optional[str] = None

    if f in ("maker", "brand"):
        attr_key = "make"
    elif f == "model":
        attr_key = "model"
    elif f == "year":
        attr_key = "year"
    elif f == "variant_attr":
        if need.attr == "fuel":
            attr_key = "fuel_type"
        elif need.attr == "transmission":
            attr_key = "transmission"
        elif need.attr in ("trim", "variant"):
            attr_key = "variant"
        elif need.attr == "engine":
            attr_key = "engine"
        else:
            attr_key = need.attr
    elif f in ("variant", "trim"):
        attr_key = "variant"

    if not attr_key:
        return False, None

    known_val = context.get(attr_key)
    if not known_val:
        return False, None

    # Check if known_val matches one of the options
    target = str(known_val).strip().lower()
    options = need.options or []

    # 1. Exact or case-insensitive match
    for opt in options:
        if opt.strip().lower() == target:
            log.info("Auto-resolved %s %s '%s' using centralized context", supplier, attr_key, opt)
            return True, opt

    # 2. Fuel matching (e.g. Diesel in ['DIESEL', 'PETROL'])
    if attr_key == "fuel_type":
        for opt in options:
            if opt.strip().upper() == target.upper():
                log.info("Auto-resolved %s fuel '%s' using centralized context", supplier, opt)
                return True, opt

    # 3. Transmission matching (Manual -> MT, Automatic -> AT)
    if attr_key == "transmission":
        tx_code = "MT" if target in ("manual", "mt") else "AT" if target in ("automatic", "at", "auto") else target.upper()
        for opt in options:
            if opt.strip().upper() in (tx_code, target.upper()):
                log.info("Auto-resolved %s transmission '%s' using centralized context", supplier, opt)
                return True, opt

    # 4. Trim matching (e.g. HTX in ['1.5L CRDi HTX MT / Diesel', '1.5L HTX AT'])
    if attr_key == "variant":
        clean_target = target.replace(" plus", "+").replace("plus", "+").replace(" ", "")
        matching_cands = []
        for opt in options:
            clean_opt = opt.lower().replace(" plus", "+").replace("plus", "+")
            # Word boundary or token match
            if re.search(rf"\b{re.escape(clean_target)}\b", clean_opt, re.I):
                matching_cands.append(opt)

        if len(matching_cands) == 1:
            log.info("Auto-resolved %s variant '%s' matching '%s' using centralized context", supplier, matching_cands[0], known_val)
            return True, matching_cands[0]
        elif len(matching_cands) > 1:
            # Disambiguate using other verified context attributes (fuel_type, transmission)
            known_fuel = (context.get("fuel_type") or "").upper()
            known_tx = (context.get("transmission") or "").upper()
            tx_code = "MT" if known_tx in ("MANUAL", "MT") else "AT" if known_tx in ("AUTOMATIC", "AT", "AUTO") else known_tx

            best_cand = None
            best_score = -1
            for cand in matching_cands:
                cand_u = cand.upper()
                score = 0
                if known_fuel and known_fuel in cand_u:
                    score += 2
                if tx_code and re.search(rf"\b{re.escape(tx_code)}\b", cand_u):
                    score += 2
                if score > best_score:
                    best_score = score
                    best_cand = cand
            if best_cand:
                log.info("Auto-resolved %s variant '%s' matching '%s' + fuel/tx using centralized context", supplier, best_cand, known_val)
                return True, best_cand

    return False, None
