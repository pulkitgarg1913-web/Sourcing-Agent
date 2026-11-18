"""Shared data shapes for the conversational parts search."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class VehicleProfile:
    """Everything we know about the vehicle and part, filled in over the conversation.

    Values here are always confirmed against the supplier's own option lists
    before use; the raw user hints are kept separately in `hints`.
    """
    part: Optional[str] = None
    requested_part: Optional[str] = None    # Canonical requested part name from user
    category: Optional[str] = None          # resolved supplier category name
    maker: Optional[str] = None
    model: Optional[str] = None
    year: Optional[str] = None
    variant_filters: dict = field(default_factory=dict)   # attr -> chosen value
    variant: Optional[str] = None           # final modification label
    hints: dict = field(default_factory=dict)              # unverified hints parsed from speech
    rejected_years: list = field(default_factory=list)     # years that had no variants

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | "VehicleProfile" | None) -> "VehicleProfile":
        if isinstance(d, cls):
            return d
        return cls(**(d or {}))


@dataclass
class NeedInput:
    """The supplier site needs a value we don't have. Carries the site's real options."""
    field: str                 # category | maker | model | year | variant_attr | variant
    question: str
    options: list[str]
    attr: Optional[str] = None # for variant_attr: which attribute (fuel, transmission, ...)
    note: Optional[str] = None # context to say before the question (e.g. "no variants for 2026")
    spoken: Optional[list] = None  # how each option is said aloud, if different from the label
    candidate: Optional[str] = None  # a value heard earlier that needs a yes/no confirmation

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Offer:
    seller: str
    price: Optional[float]
    stock: Optional[str]
    dispatch: Optional[str]


@dataclass
class Listing:
    title: str
    brand: str = ""
    part_number: str = ""
    price: Optional[float] = None
    mrp: Optional[float] = None
    url: str = ""
    origin: Optional[str] = None            # OEM / Aftermarket
    part_class: Optional[str] = None        # supplier's part class, e.g. "Oil Filter"
    match: str = "exact"                    # exact | related (different part class)
    offers: list = field(default_factory=list)
    best_offer: Optional[dict] = None
    fitment: str = "unverified"             # verified | partial | unverified
    fitment_basis: str = ""
    available: Optional[bool] = None        # True in stock, False no offer, None not checked
    notes: list = field(default_factory=list)
    relevance: str = "EXACT_MATCH"          # EXACT_MATCH | CLOSE_MATCH | COMPONENT | ACCESSORY | NEEDS_REVIEW | IRRELEVANT
    relevance_score: float = 1.0            # 0.0 to 1.0 confidence
    relevance_reason: str = ""              # Explanation for classification
    product_type: str = "complete_assembly" # complete_assembly | component | accessory
    vendor: Optional[str] = None            # "boodmo" | "autodukan"
    supplier: Optional[str] = None          # "Boodmo" | "AutoDukan"
    supplier_name: Optional[str] = None     # "Boodmo" | "AutoDukan"
    seller: Optional[str] = None
    dispatch: Optional[str] = None
    part_family_classification: Optional[str] = None
    classification_reason: Optional[str] = None
    source: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | "Listing" | None) -> "Listing":
        if isinstance(d, cls):
            return d
        if not d:
            return cls(title="", brand="", part_number="", price=None, mrp=None, url="")
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in fields}
        filtered.setdefault("title", "")
        filtered.setdefault("brand", "")
        filtered.setdefault("part_number", "")
        filtered.setdefault("price", None)
        filtered.setdefault("mrp", None)
        filtered.setdefault("url", "")
        return cls(**filtered)


@dataclass
class SearchOutcome:
    vendor: str
    status: str                                  # ok | no_results | failed | timeout | blocked
    vehicle_label: str = ""
    listings: list = field(default_factory=list)
    source_url: str = ""
    message: str = ""
    elapsed_seconds: float = 0.0
    source_type: str = "live"   # live | cached | demo
    is_cached: bool = False
    is_demo: bool = False
    raw_count: Optional[int] = None
    raw_listings: list = field(default_factory=list)
