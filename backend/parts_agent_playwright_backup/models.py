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
    def from_dict(cls, d: dict | None) -> "VehicleProfile":
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
    brand: str
    part_number: str
    price: Optional[float]
    mrp: Optional[float]
    url: str
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

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchOutcome:
    vendor: str
    status: str                 # ok | no_results | failed
    vehicle_label: str
    listings: list[dict]
    source_url: str
    message: str = ""
