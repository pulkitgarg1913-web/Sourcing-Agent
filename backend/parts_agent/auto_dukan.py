"""AutoDukan adapter for the Conversational Business Sourcing Agent.

This adapter uses AutoDukan's visible website, not undocumented APIs.

Important safety rules:
- It never treats an empty extraction as proof that the supplier has no stock.
- It does not search by a guessed vehicle or silently accept a different year/variant.
- It leaves product fitment unverified unless product-specific evidence is read.
- AutoDukan's vehicle picker is JavaScript-driven and its exact selectors can
  change. The adapter uses label/text-based Playwright fallbacks and returns a
  NeedInput or extraction_failed outcome when it cannot verify a step.

Expected shared models: Listing, NeedInput, SearchOutcome, VehicleProfile.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.async_api import async_playwright, Browser, Page

from . import llm
from .models import Listing, NeedInput, SearchOutcome, VehicleProfile

log = logging.getLogger(__name__)

BASE = "https://autodukan.com"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]
MAX_PRODUCT_PAGES = 12
DETAIL_WORKERS = 3


def _clean(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _norm(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _price(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = str(value).replace(",", "")
    match = re.search(r"(?:₹|Rs\.?|INR)\s*(\d+(?:\.\d{1,2})?)", text, re.I)
    if not match:
        match = re.fullmatch(r"\s*(\d+(?:\.\d{1,2})?)\s*", text)
    return float(match.group(1)) if match else None


def _vehicle_label(profile: VehicleProfile) -> str:
    values = [profile.maker, profile.model, profile.year, profile.variant]
    return " ".join(str(value).strip() for value in values if value)


def _same_option(requested: str, candidate: str) -> bool:
    """Exact or compact normalized match."""
    if not requested or not candidate:
        return False
    nr, nc = _norm(requested), _norm(candidate)
    return nr == nc or nr.replace(" ", "") == nc.replace(" ", "")


def _variant_signature(value: str) -> str:
    """Normalize vehicle labels across catalogue wording without dropping trim data."""
    # A catalogue may append emissions codes or body style to its label. Those
    # suffixes do not change the engine/trim selection.
    value = re.sub(r"\bbs\s*[-.]?\s*\d+(?:\.\d+)?\b", " ", value, flags=re.I)
    value = re.sub(r"\b\d+\s*doors?\b", " ", value, flags=re.I)
    return _norm(value)


def match_auto_dukan_option(field: str, requested: str, options: list[str]) -> list[str]:
    """Smart matching: exact match, local NLP matching, brand equivalents, token containment."""
    if not requested:
        return []
    exact = [option for option in options if _same_option(requested, option)]
    if exact:
        return exact

    idx = llm.match_option_locally(requested, options)
    if idx is not None and 0 <= idx < len(options):
        return [options[idx]]

    # Normalized compact comparison (stripping spaces/hyphens)
    req_compact = _norm(requested).replace(" ", "")
    for option in options:
        opt_compact = _norm(option).replace(" ", "")
        if req_compact == opt_compact:
            return [option]
        if len(req_compact) >= 4 and (req_compact in opt_compact or opt_compact in req_compact):
            return [option]

    if field == "variant":
        signature = _variant_signature(requested)
        if signature:
            sig_matches = [option for option in options if _variant_signature(option) == signature]
            if sig_matches:
                return sig_matches

    return []


def _matching_options(field: str, requested: str, options: list[str]) -> list[str]:
    return match_auto_dukan_option(field, requested, options)


def _score_best_variant(candidates: list[str], reference_variant: str, hints: dict) -> str:
    from difflib import SequenceMatcher

    def score(v: str) -> float:
        nv = _norm(v)
        nref = _norm(reference_variant)
        s = SequenceMatcher(None, nref, nv).ratio()

        # Penalize cosmetic / special edition tags if reference / user didn't mention them
        mentions_str = " ".join(hints.get("mentions") or []).upper()
        special_tags = ["DARK", "CAMO", "BLACK", "WHITE", "JET", "KAZIRANGA", "RED DARK", "EDITION", "FLEET"]
        for tag in special_tags:
            if tag in v.upper() and tag not in reference_variant.upper() and tag not in mentions_str:
                s -= 0.15

        # Bonus for exact token matches (e.g. "XZA+", "40.5", "AT", "MT", "DIESEL")
        for token in re.findall(r"[A-Z0-9+.]+", nref):
            if len(token) >= 2 and token in nv.split():
                s += 0.05
        return s

    return max(candidates, key=score)


def _electric_model_matches(requested: str, options: list[str]) -> list[str]:
    """Find the supplier's EV model when the mechanic said the base model + electric."""
    base = re.sub(r"\b(?:ev|electric)\b", " ", requested, flags=re.I)
    base = _norm(base)
    if not base:
        return []
    matches = []
    for option in options:
        if not re.search(r"\b(?:ev|electric)\b", option, re.I):
            continue
        option_base = _norm(re.sub(r"\b(?:ev|electric)\b", " ", option, flags=re.I))
        if option_base == base:
            matches.append(option)
    return matches


def _variant_matches_hints(option: str, hints: dict) -> bool:
    """Check only user-provided engine, trim, fuel, and gearbox constraints."""
    label = _norm(option)
    constraints = {key: _clean(str(hints.get(key) or ""))
                   for key in ("engine", "trim", "fuel", "transmission")}
    constraints = {key: value for key, value in constraints.items()
                   if value and _norm(value) not in {"unknown", "skip"}}
    for key, value in constraints.items():
        wanted = _norm(value)
        if key == "fuel":
            aliases = {
                "petrol": ("petrol", "gasoline"), "diesel": ("diesel",),
                "cng": ("cng",), "electric": ("electric", "ev"),
                "hybrid": ("hybrid",),
            }.get(wanted, (wanted,))
            if not any(alias in label.split() for alias in aliases):
                return False
        elif key == "transmission":
            if wanted in {"manual", "mt"}:
                if not re.search(r"\b(?:\d*mt|manual)\b", label):
                    return False
            elif wanted in {"automatic", "auto", "at"}:
                if not re.search(r"\b(?:\d*at|cvt|dct|amt|ags|dsg|automatic)\b", label):
                    return False
            elif wanted not in label.split():
                return False
        elif key == "engine":
            if wanted not in label:
                return False
        elif wanted and f" {wanted} " not in f" {label} ":
            return False
    return True


class AutoDukanAdapter:
    name = "autodukan"

    def __init__(
        self,
        headless: bool = True,
        progress: Callable[[str], None] | None = None,
        base_url: str = BASE,
        record_video_dir: Optional[str] = None,
    ):
        self.headless = headless
        self.progress = progress or (lambda message: None)
        self.base_url = base_url.rstrip("/")
        self.record_video_dir = record_video_dir
        self.video_path: Optional[str] = None
        self._pw = None
        self.browser: Browser | None = None
        self.page: Page | None = None
        self.last_search_url: Optional[str] = None
        self._vehicle_confirmed = False
        self._category_confirmed = False
        self._car_model_id: Optional[str] = None
        self._last_failure: Optional[str] = None
        self._category_options: list[str] = []
        self._category_leaf_options: list[str] = []
        self._category_paths: dict[str, list[str]] = {}
        self._category_roots: set[str] = set()
        self._confirmed_vehicle: Optional[str] = None
        self._confirmed_category: Optional[str] = None
        self._using_search_fallback = False

    # ------------------------------------------------------------------ lifecycle

    async def start(self):
        if self.page:
            return
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=self.headless, args=CHROMIUM_ARGS)
        ctx_opts = {
            "viewport": {"width": 1440, "height": 1000},
            "user_agent": UA,
            "locale": "en-IN",
        }
        from pathlib import Path
        if self.record_video_dir:
            Path(self.record_video_dir).mkdir(parents=True, exist_ok=True)
            ctx_opts["record_video_dir"] = self.record_video_dir
            ctx_opts["record_video_size"] = {"width": 1280, "height": 720}
        context = await self.browser.new_context(**ctx_opts)
        self.page = await context.new_page()

    async def close(self):
        if self.page:
            try:
                if self.page.video:
                    self.video_path = await self.page.video.path()
            except Exception:
                pass
            await self.page.close()
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()
        self.page = None
        self.browser = None
        self._pw = None

    async def _goto(self, url: str):
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception as exc:
            log.warning("AutoDukan domcontentloaded timeout navigating to %s (%s), retrying with commit...", url, exc)
            try:
                await self.page.goto(url, wait_until="commit", timeout=12000)
            except Exception as e2:
                log.error("AutoDukan navigation failed for %s: %s", url, e2)
                raise
        try:
            await self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            # The storefront may keep analytics or long-polling connections open.
            pass
        await self.page.wait_for_timeout(500)
        self.last_search_url = self.page.url

    # ------------------------------------------------------------------ contract

    async def resolve_category(self, profile: VehicleProfile) -> Optional[NeedInput]:
        if not _clean(getattr(profile, "part", "")):
            return NeedInput(
                field="part",
                question="Which spare part or OEM part number do you need?",
                options=[],
            )
        requested_category = _clean(getattr(profile, "category", ""))
        if requested_category and _norm(requested_category) in self._category_paths:
            return None

        part = _norm(profile.part)
        direct = (
            (r"\b(tail\s*lights?|tail\s*lamps?|taillights?|taillamps?|rear\s*lamps?|rear\s*lights?)\b", "TAILLIGHT"),
            (r"\b(head\s*lights?|head\s*lamps?|headlights?|headlamps?)\b", "HEADLIGHT"),
            (r"\b(fog\s*lights?|fog\s*lamps?)\b", "FOG LAMP"),
            (r"\b(oil\s*filters?)\b", "OIL FILTER"),
            (r"\b(air\s*filters?)\b", "AIR FILTER"),
            (r"\b(cabin\s*filters?|ac\s*filters?)\b", "CABIN FILTER"),
            (r"\b(fuel\s*filters?)\b", "FUEL FILTER"),
            (r"\b(brake\s*pads?|disc\s*pads?)\b", "BRAKE PADS"),
            (r"\balloy\s*wheels?\b", "Alloy Wheels"),
            (r"\b(clutch|clutch\s*plate|clutch\s*set|clutch\s*kit)\b", "CLUTCH SET"),
            (r"\b(transmission\s*bracket|transmission\s*mounting|gearbox\s*mount(?:ing)?|gear\s*mounting)\b", "TRANSMISSION MOUNTING"),
            (r"\b(gear\s*box|gearbox|transmission|manual\s*transmission|automatic\s*transmission|gear\s*shift)\b", "TRANSMISSION"),
            (r"\b(wiper|wiper\s*blades?)\b", "WIPER BLADES"),
            (r"\b(spark\s*plugs?)\b", "SPARK PLUG"),
            (r"\b(radiator)\b", "RADIATOR"),
            (r"\b(horns?)\b", "HORN"),
            (r"\b(batter(?:y|ies))\b", "BATTERY"),
            (r"\b(alternator)\b", "ALTERNATOR"),
            (r"\b(shock\s*absorber|strut)\b", "SHOCK ABSORBER"),
            (r"\b(water\s*pump)\b", "WATER PUMP"),
            (r"\b(timing\s*belt|fan\s*belt|serpentine\s*belt|drive\s*belt)\b", "BELTS"),
        )
        for pattern, category in direct:
            if re.search(pattern, part) or (requested_category and re.search(pattern, _norm(requested_category))):
                profile.category = category
                self._category_paths[_norm(category)] = self._path_for_category(category)
                return None

        # Build the category route only from AutoDukan's own rendered links.
        # This also handles a mechanic selecting a top-level category when the
        # adapter could not confidently pick one from the original request.
        try:
            await self._goto(f"{self.base_url}/category")
            category_links = await self._visible_category_links()
            self._category_options = list(dict.fromkeys(
                self._category_link_label(link)
                for link in category_links
                if link["path"].startswith("/subcategory/") and self._category_link_label(link)
            ))
            self._category_roots = {_norm(label) for label in self._category_options}
        except Exception as exc:
            log.warning("AutoDukan /category failed or timed out: %s. Using default category fallback.", exc)
            self._category_options = ["Lighting", "Steering", "Brake System", "Body Parts", "Filters", "Clutch Set"]
            self._category_roots = {_norm(label) for label in self._category_options}
            category_links = []
            profile.category = profile.part
            self._category_paths[_norm(profile.part)] = [profile.part]
            return None

        parent = requested_category if _norm(requested_category) in self._category_roots else ""
        if not parent:
            selected = None
            if self._category_options:
                try:
                    response = await llm.generate_async(
                        "Choose the best AutoDukan top-level category for this part. "
                        "Return exactly one supplied category label, or NONE.\n"
                        f"Part: {profile.part}\nCategories:\n"
                        + "\n".join(self._category_options)
                    )
                    choice = _clean(response.text or "")
                    selected = next((option for option in self._category_options
                                     if _same_option(choice, option)), None)
                except Exception as exc:
                    log.info("AutoDukan top-level category mapping failed: %s", exc)
            if not selected:
                return NeedInput(
                    field="category",
                    question=f"Which AutoDukan category best fits {profile.part}?",
                    options=self._category_options or ["Lighting", "Steering", "Brake System", "Body Parts"],
                )
            parent = selected

        root = self._category_link(category_links, [parent], "/subcategory/")
        if not root:
            return NeedInput(
                field="category",
                question=f"Which AutoDukan category best fits {profile.part}?",
                options=self._category_options,
            )
        try:
            await self._goto(root["href"])
            child_links = await self._visible_category_links()
        except Exception as exc:
            log.warning("AutoDukan subcategory load failed: %s", exc)
            profile.category = parent
            self._category_paths[_norm(parent)] = [parent]
            return None
        children = [link for link in child_links
                    if link["path"].startswith("/products/")
                    and "page=" in link["href"].casefold()
                    and self._category_link_label(link)]
        child_labels = list(dict.fromkeys(self._category_link_label(link) for link in children))
        self._category_leaf_options = child_labels
        if not child_labels:
            # Some top-level pages are themselves product lists.
            profile.category = parent
            self._category_paths[_norm(parent)] = [parent]
            return None

        if requested_category and _norm(requested_category) in {
            _norm(label) for label in child_labels
        }:
            selected_leaf = next(label for label in child_labels
                                 if _same_option(requested_category, label))
        elif len(child_labels) == 1:
            selected_leaf = child_labels[0]
        else:
            selected_leaf = None
            try:
                response = await llm.generate_async(
                    "Choose the AutoDukan subcategory that best matches the requested part. "
                    "Return exactly one supplied label, or NONE.\n"
                    f"Part: {profile.part}\nParent category: {parent}\nSubcategories:\n"
                    + "\n".join(child_labels)
                )
                choice = _clean(response.text or "")
                selected_leaf = next((label for label in child_labels
                                      if _same_option(choice, label)), None)
            except Exception as exc:
                log.info("AutoDukan subcategory mapping failed: %s", exc)

        if not selected_leaf:
            for link in children:
                label = self._category_link_label(link)
                self._category_paths[_norm(label)] = [parent, label]
            return NeedInput(
                field="category",
                question=f"Which AutoDukan subcategory best fits {profile.part} under {parent}?",
                options=child_labels,
            )

        profile.category = selected_leaf
        self._category_paths[_norm(selected_leaf)] = [parent, selected_leaf]
        return None

    @staticmethod
    def _path_for_category(category: str) -> list[str]:
        """Map common part names to AutoDukan's observed category hierarchy."""
        normalized = _norm(category).replace(" ", "")
        if "tail" in normalized or "rear" in normalized:
            return ["LIGHTING", "TAILLIGHT"]
        if "head" in normalized:
            return ["LIGHTING", "HEADLIGHT"]
        if "fog" in normalized:
            return ["LIGHTING", "FOG LAMP"]
        if "oilfilter" in normalized or "oil" in normalized:
            return ["FILTERS", "OIL FILTER"]
        if "airfilter" in normalized or "air" in normalized:
            return ["FILTERS", "AIR FILTER"]
        if "fuelfilter" in normalized or "fuel" in normalized:
            return ["FILTERS", "FUEL FILTER"]
        if "cabinfilter" in normalized or "cabin" in normalized:
            return ["FILTERS", "CABIN FILTER"]
        if "filter" in normalized:
            return ["FILTERS", "OIL FILTER"]
        if "brakepad" in normalized or "brake" in normalized or "disc" in normalized:
            return ["BRAKE SYSTEM", "BRAKE PADS"]
        if "wheel" in normalized or "rim" in normalized or "alloy" in normalized:
            return ["WHEELS & TYRE", "Rim"]
        if "clutch" in normalized:
            return ["CLUTCH SET"]
        if "wiper" in normalized:
            return ["WIPER BLADES"]
        if "spark" in normalized:
            return ["SPARK PLUG"]
        if "horn" in normalized:
            return ["HORN"]
        if "battery" in normalized:
            return ["BATTERY"]
        if "radiator" in normalized:
            return ["RADIATOR"]
        if "shock" in normalized or "strut" in normalized:
            return ["SHOCK ABSORBER"]
        if "trans" in normalized or "gear" in normalized:
            return ["TRANSMISSION"]
        return [category]

    @staticmethod
    def _slug_for_category(category: str) -> str:
        norm = _norm(category).replace(" ", "")
        if "tail" in norm or "rear" in norm:
            return "taillight"
        if "head" in norm:
            return "headlight"
        if "fog" in norm:
            return "fog-light"
        if "oil" in norm:
            return "oil-filter"
        if "air" in norm:
            return "air-filter"
        if "cabin" in norm or "acfilter" in norm:
            return "cabin-filter"
        if "fuel" in norm:
            return "fuel-filter"
        if "filter" in norm:
            return "oil-filter"
        if "clutch" in norm:
            return "clutch-set"
        if "brake" in norm or "pad" in norm or ("disc" in norm and "brake" in norm):
            return "brake-pads"
        if "shock" in norm or "strut" in norm:
            return "shock-absorber"
        if "wiper" in norm or "blade" in norm or "windscreen" in norm:
            return "wiper-blades"
        if "battery" in norm:
            return "battery"
        if "alternator" in norm:
            return "alternator"
        if "spark" in norm or "plug" in norm:
            return "spark-plug"
        if "horn" in norm:
            return "horn"
        if "radiator" in norm:
            return "radiator"
        if "waterpump" in norm or ("water" in norm and "pump" in norm):
            return "water-pump"
        if "trans" in norm or "gear" in norm:
            return "transmission"
        return re.sub(r"[^a-z0-9]+", "-", _norm(category).strip()).strip("-")

    async def _search_fallback(self, profile: VehicleProfile) -> None:
        import urllib.parse
        make = profile.maker or profile.hints.get("make") or ""
        model = profile.model or profile.hints.get("model") or ""
        part = profile.part or profile.category or ""
        terms = [make, model, part]
        query = " ".join(t for t in terms if t).strip()
        self.progress(f"Falling back to search for '{query}' on AutoDukan")
        url = f"{self.base_url}/products-list?search={urllib.parse.quote_plus(query)}"
        await self._goto(url)
        self._using_search_fallback = True
        self._vehicle_confirmed = True
        self._category_confirmed = True

    async def advance(self, profile: VehicleProfile) -> Optional[NeedInput]:
        """Resolve AutoDukan's own category and vehicle selector before search."""
        await self.start()
        if profile.hints.get("commercial_warning") or getattr(self, "_using_search_fallback", False):
            await self._search_fallback(profile)
            return None
        need = await self.resolve_category(profile)
        if need:
            if profile.hints.get("commercial_warning"):
                await self._search_fallback(profile)
                return None
            return need

        if (self._vehicle_confirmed and self._category_confirmed
                and self._confirmed_vehicle == _vehicle_label(profile)
                and self._confirmed_category == profile.category):
            return None

        if self.page.url.rstrip("/") != self.base_url:
            await self._goto(self.base_url)

        ok, reason, need = await self._select_vehicle(profile)
        if need:
            return need
        if self._using_search_fallback:
            return None
        if not ok:
            self._last_failure = reason
            return NeedInput(
                field="variant" if "variant" in reason.lower() else "vehicle",
                question=(
                    f"Could not verify { _vehicle_label(profile) }. "
                    f"{reason} Please confirm the year and variant."
                ),
                options=[],
            )

        ok, reason = await self._select_category(profile.category)
        if not ok:
            log.info("AutoDukan category '%s' not reachable via direct catalog (%s). Falling back to keyword search.", profile.category, reason)
            await self._search_fallback(profile)
            return None

        self._vehicle_confirmed = True
        self._category_confirmed = True
        self._confirmed_vehicle = _vehicle_label(profile)
        self._confirmed_category = profile.category
        self.last_search_url = self.page.url
        return None

    # ------------------------------------------------------------------ vehicle selector

    async def _vehicle_controls(self):
        """Return the visible AutoDukan Brand/Model/Year/Variant comboboxes."""
        # Keep this DOM locator while MUI's option menu is open. Its modal marks
        # the underlying form aria-hidden, which removes get_by_role() matches
        # even though the combobox remains present and usable after selection.
        locator = self.page.locator('[role="combobox"][aria-haspopup="listbox"]')
        controls = []
        try:
            for index in range(await locator.count()):
                control = locator.nth(index)
                if await control.is_visible():
                    controls.append(control)
        except Exception:
            return []
        return controls

    async def _vehicle_control(self, field: str):
        order = {"brand": 0, "model": 1, "year": 2, "variant": 3}
        controls = await self._vehicle_controls()
        index = order[field]
        return controls[index] if len(controls) > index else None

    async def _open_vehicle_picker(self) -> tuple[bool, str]:
        """Recognize the rendered four-field vehicle form."""
        await self.page.wait_for_timeout(350)
        controls = await self._vehicle_controls()
        if len(controls) >= 4:
            return True, ""
        return False, (
            "AutoDukan's rendered vehicle form is unavailable "
            f"(found {len(controls)} visible comboboxes; expected Brand, Model, Year, Variant)."
        )

    async def _read_vehicle_options(self, field: str):
        """Read options from AutoDukan's visible combobox or native select."""
        control = await self._vehicle_control(field)
        if not control:
            return [], None

        try:
            tag = await control.evaluate("el => el.tagName")
            if await control.get_attribute("aria-disabled") == "true":
                return [], control
            if tag == "SELECT":
                raw = await control.locator("option").all_text_contents()
                return self._usable_vehicle_options(field, [_clean(value) for value in raw]), control

            candidates = self.page.get_by_role("option")
            visible = False
            for index in range(min(await candidates.count(), 250)):
                if await candidates.nth(index).is_visible():
                    visible = True
                    break
            if not visible:
                await control.click(timeout=3000)
                await self.page.wait_for_timeout(300)
            options = []
            for index in range(min(await candidates.count(), 250)):
                item = candidates.nth(index)
                if await item.is_visible():
                    label = _clean(await item.inner_text())
                    if label:
                        options.append(label)
            return self._usable_vehicle_options(field, options), control
        except Exception as exc:
            log.info("AutoDukan could not read %s options: %s", field, type(exc).__name__)
            return [], control

    @staticmethod
    def _usable_vehicle_options(field: str, values: list[str]) -> list[str]:
        placeholder = _norm(field)
        return list(dict.fromkeys(
            value for value in values
            if value and _norm(value) not in {placeholder, "select", f"select {placeholder}"}
        ))

    async def _choose_visible_vehicle_option(self, field: str, value: str, control) -> bool:
        if not control:
            return False
        try:
            tag = await control.evaluate("el => el.tagName")
            if tag == "SELECT":
                await control.select_option(label=value)
                await self.page.wait_for_timeout(500)
                return _same_option(value, await self._field_value(field))

            candidates = self.page.get_by_role("option")
            async def matching_visible_option():
                for index in range(min(await candidates.count(), 250)):
                    option = candidates.nth(index)
                    if await option.is_visible() and _same_option(value, _clean(await option.inner_text())):
                        return option
                return None

            target = await matching_visible_option()
            if not target:
                await control.click(timeout=3000)
                await self.page.wait_for_timeout(250)
                target = await matching_visible_option()
            if not target:
                log.info("AutoDukan option %r disappeared before selecting %s", value, field)
                return False

            try:
                await target.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            try:
                await target.click(timeout=1500)
            except Exception:
                await self.page.wait_for_timeout(200)
                if _same_option(value, await self._field_value(field)):
                    return True
                try:
                    await target.click(timeout=1500, force=True)
                except Exception:
                    await self.page.wait_for_timeout(200)
                    if _same_option(value, await self._field_value(field)):
                        return True
                    # MUI's role=option is the rendered control, so invoking its
                    # click event is a final fallback when overlay hit testing blocks Playwright.
                    await target.evaluate("el => el.click()")
            await self.page.wait_for_timeout(500)
            selected_value = await self._field_value(field)
            if _same_option(value, selected_value):
                return True
            log.info("AutoDukan %s option click did not change the control: wanted=%r actual=%r",
                     field, value, selected_value)
        except Exception as exc:
            log.info("AutoDukan could not select %s=%r: %s: %s",
                     field, value, type(exc).__name__, exc)
        return False

    async def _field_value(self, field: str) -> str:
        control = await self._vehicle_control(field)
        if not control:
            return ""
        try:
            return _clean(await control.evaluate(
                "el => el.value || el.getAttribute('aria-valuetext') || el.innerText || ''"
            ))
        except Exception:
            return ""

    async def _resolve_vehicle_field(self, profile: VehicleProfile, field: str) -> Optional[NeedInput]:
        attr = {"brand": "maker", "model": "model", "year": "year", "variant": "variant"}[field]
        options: list[str] = []
        control = None
        # AutoDukan reloads dependent options after each selection.
        for attempt in range(9):
            options, control = await self._read_vehicle_options(field)
            if options or attempt == 8:
                break
            await self.page.wait_for_timeout(350)

        requested = _clean(getattr(profile, attr) or "")
        hint_key = {"brand": "make", "model": "model", "year": "year"}.get(field)
        hint = _clean(profile.hints.get(hint_key, "")) if hint_key else ""
        display_options = options

        query = requested or hint
        matches = _matching_options(field, query, options) if query else []
        source = query

        # AutoDukan catalogs the Nexon and Nexon EV as different models. If the
        # mechanic supplied electric as a fuel constraint, prefer the EV model
        # before consulting the base-model match; its trim list is unrelated.
        fuel = _norm(str(profile.hints.get("fuel") or ""))
        model_query = requested or hint
        if field == "model" and fuel in {"electric", "ev"} and model_query:
            electric_matches = _electric_model_matches(model_query, options)
            if electric_matches:
                matches = electric_matches
                display_options = electric_matches
                source = f"{model_query} + electric"
            else:
                # Do not accept a combustion model and then expose its trims
                # for an explicitly electric vehicle.
                matches = []
                ev_options = [option for option in options
                              if re.search(r"\b(?:ev|electric)\b", option, re.I)]
                display_options = ev_options or options

        # Variant smart auto-matching
        boodmo_variant = str(profile.hints.get("boodmo_variant") or profile.variant or "").strip()
        if field == "variant":
            constrained = [option for option in options
                           if _variant_matches_hints(option, profile.hints)]
            candidates = constrained if constrained else options

            if boodmo_variant and candidates:
                best = _score_best_variant(candidates, boodmo_variant, profile.hints)
                matches = [best]
                source = f"best fit for Boodmo variant '{boodmo_variant}'"
            elif len(constrained) == 1:
                matches = constrained
                source = "mechanic's stated vehicle details"
            elif not requested and not hint and len(options) == 1:
                matches = [options[0]]
                source = "only available variant"

        if len(matches) == 1:
            selected = matches[0]
            if await self._choose_visible_vehicle_option(field, selected, control):
                setattr(profile, attr, selected)
                log.info("AutoDukan selected its %s option %r using %s",
                         field, selected, source or "the only matching catalogue value")
                return None

        titles = {
            "brand": "What is the car's make (brand), such as Tata, Hyundai, or Maruti Suzuki?",
            "model": f"Which {profile.maker or 'vehicle'} model is it?",
            "year": f"Which year is the {profile.maker or ''} {profile.model or ''}?",
            "variant": f"Which version or trim of the {profile.maker or ''} {profile.model or ''} ({profile.year or ''}) is it?",
        }
        if field == "model" and (requested or hint) and not matches:
            log.info("AutoDukan model '%s' not in dropdown choices; falling back to search bar", requested or hint)
            await self._search_fallback(profile)
            return None

        note = None
        if requested:
            note = f"'{requested}' is not listed in this selector."
        if field == "model" and fuel in {"electric", "ev"} and model_query and not matches:
            note = (f"No electric model found matching '{model_query}'. "
                    f"Options are {', '.join(display_options[:3])}.")
        elif field == "variant" and display_options is not options:
            note = "Several variants match the stated vehicle details."
        log.info("AutoDukan needs a mechanic selection for %s: requested=%r choices=%r url=%s",
                 field, requested or hint or None, display_options[:40], self.page.url)
        return NeedInput(field=attr, question=titles[field], options=display_options, note=note)

    async def _select_vehicle(
        self,
        profile: VehicleProfile
    ) -> tuple[bool, str, Optional[NeedInput]]:
        """Select the four vehicle values from AutoDukan's rendered form."""
        try:
            await self.page.keyboard.press("Escape")
        except Exception:
            pass

        opened, reason = await self._open_vehicle_picker()
        if not opened:
            return False, reason, None

        for field in ("brand", "model", "year", "variant"):
            need = await self._resolve_vehicle_field(profile, field)
            if need:
                return True, "", need
            if self._using_search_fallback:
                return True, "fallback to search bar", None
            await self.page.wait_for_timeout(350)

        before_submit = self.page.url
        submit_control = None
        for label in ("CONFIRM SELECTION", "Find Parts"):
            buttons = self.page.get_by_role("button", name=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I))
            for index in range(min(await buttons.count(), 8)):
                candidate = buttons.nth(index)
                if await candidate.is_visible() and await candidate.is_enabled():
                    submit_control = candidate
                    break
            if submit_control:
                break
        if not submit_control:
            return False, "AutoDukan's vehicle form has no enabled Find Parts button.", None

        try:
            await submit_control.click(timeout=5000)
            await self.page.wait_for_timeout(1200)
            for _ in range(12):
                parsed = urlparse(self.page.url)
                qs = parse_qs(parsed.query)
                if "car_model_id" in qs:
                    self._car_model_id = qs["car_model_id"][0]
                    break
                await self.page.wait_for_timeout(250)
        except Exception as exc:
            return False, f"Vehicle confirmation failed ({type(exc).__name__}).", None

        body = _norm(await self.page.locator("body").inner_text())
        if any(message in body for message in (
            "variant missing", "please select the car variant", "please select the car varient"
        )):
            return False, "AutoDukan still requires a vehicle variant.", None

        if self.page.url == before_submit:
            requested = [str(profile.maker), str(profile.model), str(profile.year), str(profile.variant)]
            body_confirms = all(_norm(value) in body for value in requested)
            field_values = [await self._field_value(field)
                            for field in ("brand", "model", "year", "variant")]
            fields_confirm = all(
                _matching_options(field, wanted, [actual])
                for field, wanted, actual in zip(
                    ("brand", "model", "year", "variant"), requested, field_values
                )
            )
            if not body_confirms and not fields_confirm:
                return False, "AutoDukan did not visibly confirm the selected vehicle.", None

        return True, "", None

    # ------------------------------------------------------------------ category navigation

    async def _visible_category_links(self) -> list[dict[str, str]]:
        """Read the rendered category anchors and their real destination URLs."""
        try:
            links = await self.page.locator("a[href]").evaluate_all("""els => els
                .filter(a => {
                    const r = a.getBoundingClientRect();
                    const s = getComputedStyle(a);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' &&
                        s.visibility !== 'hidden';
                })
                .map(a => ({
                    text: (a.innerText || '').trim(),
                    label: a.getAttribute('aria-label') || '',
                    href: a.href
                }))""")
        except Exception as exc:
            log.info("AutoDukan category links could not be read: %s", type(exc).__name__)
            return []

        site_host = urlparse(self.base_url).netloc.casefold()
        result = []
        for link in links:
            url = link.get("href", "")
            parsed = urlparse(url)
            if parsed.netloc.casefold() != site_host:
                continue
            result.append({
                "text": _clean(link.get("text")),
                "label": _clean(link.get("label")),
                "href": url,
                "path": parsed.path,
            })
        return result

    @staticmethod
    def _category_link(
        links: list[dict[str, str]], labels: list[str], route: str | tuple[str, ...]
    ):
        wanted = {_norm(label).replace(" ", "") for label in labels}
        for link in links:
            if route and not link["path"].startswith(route):
                continue
            names = {
                _norm(link["text"]).replace(" ", ""),
                _norm(link["label"]).replace(" ", ""),
                _norm(re.sub(r"^browse\s+", "", link["label"], flags=re.I)).replace(" ", ""),
            }
            slug = _norm(link["path"].rstrip("/").rsplit("/", 1)[-1]).replace(" ", "")
            if names.intersection(wanted) or slug in wanted:
                return link
            for w in wanted:
                if len(w) >= 4 and (w in slug or slug in w or any(w in n or n in w for n in names if n)):
                    return link
        return None

    @staticmethod
    def _category_labels(category: str) -> list[str]:
        norm = _norm(category).replace(" ", "")
        if "tail" in norm or "rear" in norm:
            return [
                "TAILLIGHT", "TAIL LIGHT", "TAIL LIGHTS", "TAIL LAMP", "TAIL LAMPS",
                "REAR LIGHT", "REAR LIGHTS", "REAR LAMP", "REAR LAMPS",
                "REARLIGHT COMPONENTS", "REAR COMBINATION LIGHT", "REAR COMBINATION LIGHTS",
            ]
        if "head" in norm:
            return ["HEADLIGHT", "HEAD LIGHT", "HEAD LIGHTS", "HEADLAMP", "HEADLAMPS", "HEAD LAMP"]
        if "oil" in norm:
            return ["OIL FILTER", "OILFILTER"]
        if "air" in norm:
            return ["AIR FILTER", "AIRFILTER"]
        if "fuel" in norm:
            return ["FUEL FILTER", "FUELFILTER"]
        if "cabin" in norm:
            return ["CABIN FILTER", "CABINFILTER"]
        if "brake" in norm or "disc" in norm:
            return ["BRAKE PADS", "BRAKE PAD", "DISC PADS", "DISC PAD"]
        return [category, category.replace(" ", "")]

    @staticmethod
    def _category_link_label(link: dict[str, str]) -> str:
        return link["text"] or _clean(re.sub(r"^browse\s+", "", link["label"], flags=re.I))

    async def _select_category(self, category: str) -> tuple[bool, str]:
        requested = _clean(category)
        if not requested:
            return False, "No category was provided."
        self._category_leaf_options = []

        # If vehicle was confirmed and car_model_id is available, stay in the vehicle-filtered catalog!
        slug = self._slug_for_category(requested)
        if self._car_model_id and slug:
            target_url = f"{self.base_url}/products-list/{slug}?car_model_id={self._car_model_id}"
            try:
                await self._goto(target_url)
                await self.page.wait_for_timeout(1500)
                body = _norm(await self.page.locator("body").inner_text())
                if "product not found" in body or "/products-list" in self.page.url:
                    self._category_confirmed = True
                    self._confirmed_category = requested
                    self.last_search_url = self.page.url
                    return True, ""
            except Exception as exc:
                log.info("AutoDukan direct vehicle category navigation failed: %s", exc)

        path = self._category_paths.get(_norm(requested)) or self._path_for_category(requested)

        # The category index is the authoritative entry point; home only lists
        # a featured subset and product pages may carry unrelated navigation.
        try:
            await self._goto(f"{self.base_url}/category")
            links = await self._visible_category_links()
        except Exception as exc:
            log.warning("AutoDukan /category index timed out or failed: %s", exc)
            return False, f"AutoDukan category page timed out: {exc}"

        for index, segment in enumerate(path):
            route = "/subcategory/" if index == 0 else ("/products/", "/subcategory/")
            labels = self._category_labels(requested) if index == len(path) - 1 else [segment]
            link = self._category_link(links, labels, route)

            if not link and index == 0:
                # Some pages expose a category tile without the conventional
                # route prefix. Match its rendered label and use its actual URL.
                link = self._category_link(links, [segment], "")

            if not link:
                self._category_leaf_options = list(dict.fromkeys(
                    self._category_link_label(link) for link in links
                    if self._category_link_label(link) and link["path"].startswith("/products/")
                    and "page=" in link["href"].casefold()
                ))
                # Tail-light links live one level below /subcategory/lighting.
                # Walk the site's rendered route rather than looking for a
                # guessed CSS selector on the current page.
                if index == 0 and len(path) > 1:
                    root = self._category_link(links, [segment], "/subcategory/")
                    if root:
                        try:
                            await self._goto(root["href"])
                            links = await self._visible_category_links()
                            link = self._category_link(links, labels, "/products/")
                        except Exception as exc:
                            return False, f"Could not open category '{segment}' ({type(exc).__name__})."
                if not link and self._category_leaf_options:
                    idx = llm.match_option_locally(requested, self._category_leaf_options)
                    if idx is not None and 0 <= idx < len(self._category_leaf_options):
                        cand = self._category_leaf_options[idx]
                        link = self._category_link(links, [cand], route)
                if not link:
                    choices = ", ".join(self._category_leaf_options[:12])
                    detail = f" Available subcategories: {choices}." if choices else ""
                    return False, f"Category '{segment}' or its requested subcategory was not found.{detail}"

            try:
                await self._goto(link["href"])
            except Exception as exc:
                return False, f"Could not open AutoDukan category '{link['text'] or segment}' ({type(exc).__name__})."
            self._category_leaf_options = list(dict.fromkeys(
                self._category_link_label(item) for item in await self._visible_category_links()
                if self._category_link_label(item) and item["path"].startswith("/products/")
                and "page=" in item["href"].casefold()
            ))
            for label in self._category_leaf_options:
                self._category_paths.setdefault(
                    _norm(label), path[:index + 1] + [label]
                )
            links = await self._visible_category_links()

        body = _norm(await self.page.locator("body").inner_text())
        target = _norm(path[-1])
        current_path = _norm(urlparse(self.page.url).path)
        if (target not in body and target not in current_path
                and not any(_norm(item) in body for item in path)):
            return False, f"AutoDukan did not show the selected category '{requested}'."
        self.last_search_url = self.page.url
        return True, ""

    # ------------------------------------------------------------------ extraction

    async def _read_cards(self) -> list[dict]:
        """Extract product-like cards from AutoDukan's rendered category page."""
        return await self.page.evaluate(
            r"""() => {
                const clean = v => (v || '').replace(/\s+/g, ' ').trim();
                const abs = href => { try { return new URL(href, location.href).href; } catch (_) { return ''; } };
                const bad = /^(home|product list|login|sign in|contact|faq|blogs|about us|all categories|oem brands|oes brands|return policy|privacy policy|terms of use|disclaimer|become an autodukan seller|become a seller|contact us|about us|more than parts.*|we'd love to hear.*|customer care|help center|download app|all rights reserved|terms)$/i;
                const nonProductPaths = /^\/(brands?|services|sellers?|return-policy|privacy-policy|terms-of-use|terms|disclaimer|contact(-us)?|about(-us)?|blogs?|faq|help|cart|checkout|login|register|track-order|order|account|subcategory|products-list)(\/|$)/i;
                const anchors = [...document.querySelectorAll('a[href]')];
                const out = [];
                const seen = new Set();
                for (const a of anchors) {
                    const href = abs(a.getAttribute('href'));
                    const aria = clean(a.getAttribute('aria-label'));
                    const inner = clean(a.innerText);
                    const title = aria.replace(/^View\s+/i, '') || inner;
                    if (!href || !title || bad.test(title) || href === location.href) continue;
                    if (!/^https?:$/.test(new URL(href).protocol)) continue;
                    if (/(^|\.)autodukan\.com$/i.test(new URL(href).hostname) === false) continue;
                    const productUrl = new URL(href);
                    // Rendered taxonomy links use /products/<category>/<subcategory>?page=1.
                    // They are navigation, not product listings.
                    if (productUrl.searchParams.has('page') || /\/subcategory\/[^/]+\/?$/i.test(productUrl.pathname)) continue;
                    // Exclude navigation, brand, seller, and policy routes
                    if (nonProductPaths.test(productUrl.pathname)) continue;
                    if (seen.has(href)) continue;

                    const isProductDetailUrl = /\/product(s)?\/[0-9a-zA-Z_-]+/i.test(productUrl.pathname) && !productUrl.pathname.includes('products-list');
                    let foundCard = false;
                    let card = a;
                    for (let i = 0; i < 6; i++) {
                        if (card.parentElement && (card.innerText.includes('Add to Cart') || card.innerText.includes('Part No:') || card.classList.contains('product-card'))) {
                            foundCard = true;
                            break;
                        }
                        if (card.parentElement) card = card.parentElement;
                    }
                    if (!foundCard && !isProductDetailUrl) continue;

                    const text = clean(card?.innerText || title);
                    if (text.length < title.length || text.length > 2500) continue;
                    if (/product not found|create product enquiry|please select the car variant/i.test(text)) continue;

                    seen.add(href);
                    const priceMatch = text.match(/(?:₹|Rs\.?|INR)\s*[\d,]+(?:\.\d{1,2})?/i);
                    const mrpMatch = text.match(/MRP\s*(?:₹|Rs\.?|INR)\s*[\d,]+(?:\.\d{1,2})?/i);
                    const skuMatch = text.match(/(?:SKU|Part\s*(?:No|Number)|OEM\s*(?:No|Number))\s*[:#-]?\s*([A-Z0-9./_-]{4,})/i);
                    const brandMatch = text.match(/Brand\s*:\s*([^\n\r|₹]+)/i);
                    const img = card?.querySelector('img');
                    const cleanTitle = title.replace(/^Add to Cart\s*/i, '').replace(/^View\s*/i, '').trim();

                    out.push({
                        title: cleanTitle,
                        url: href,
                        cardText: text,
                        priceText: priceMatch ? priceMatch[0] : null,
                        mrpText: mrpMatch ? mrpMatch[0] : null,
                        sku: skuMatch ? skuMatch[1] : null,
                        brand: brandMatch ? brandMatch[1].trim() : null,
                        image: img?.getAttribute('src') || img?.getAttribute('data-src') || null
                    });
                }
                return out;
            }"""
        )

    async def _page_state(self) -> dict:
        return await self.page.evaluate("""() => ({
            title: document.title,
            url: location.href,
            text: (document.body?.innerText || '').slice(0, 12000),
            productLinks: [...document.querySelectorAll('a[href]')]
                .map(a => ({text: (a.innerText || '').trim(), href: a.href}))
                .filter(x => x.text && !/^(home|product list|login|faq|blogs)$/i.test(x.text))
                .slice(0, 80)
        })""")

    async def _read_detail(self, page: Page, listing: Listing):
        await page.goto(listing.url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        data = await page.evaluate(r"""() => {
            const clean = v => (v || '').replace(/\s+/g, ' ').trim();
            const first = selectors => {
                for (const s of selectors) {
                    const n = document.querySelector(s);
                    const v = clean(n?.innerText || n?.textContent || n?.getAttribute('content'));
                    if (v) return v;
                }
                return null;
            };
            let product = null;
            const walk = v => {
                if (!v) return;
                if (Array.isArray(v)) { v.forEach(walk); return; }
                if (typeof v !== 'object') return;
                const t = v['@type'];
                if (t === 'Product' || (Array.isArray(t) && t.includes('Product'))) product = v;
                if (v['@graph']) walk(v['@graph']);
            };
            for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                try { walk(JSON.parse(s.textContent)); } catch (_) {}
            }
            const offer = Array.isArray(product?.offers) ? product.offers[0] : product?.offers;
            return {
                title: first(['h1', '[itemprop="name"]']),
                brand: first(['[itemprop="brand"]', '.product-brand', '[class*="brand"]']) ||
                    (typeof product?.brand === 'string' ? product.brand : product?.brand?.name) || null,
                sku: first(['[itemprop="sku"]', '[class*="sku"]', '[data-sku]']) || product?.sku || product?.mpn || null,
                price: first(['[itemprop="price"]', '[class*="selling-price"]', '[class*="sale-price"]', '[class*="price"]']) ||
                    (offer?.price != null ? String(offer.price) : null),
                availability: offer?.availability || null,
                description: first(['[itemprop="description"]', '.product-description', '[class*="description"]']) || product?.description || null
            };
        }""")
        if data.get("title"):
            cand_title = re.sub(r"^(?:Add to Cart|View)\s*", "", _clean(data["title"]), flags=re.I).strip()
            if cand_title:
                listing.title = cand_title
        cand_brand = _clean(data.get("brand") or "")
        if cand_brand and not re.search(r"\b(?:add to cart|cart|view|product|details)\b", cand_brand, re.I):
            listing.brand = cand_brand
        if data.get("sku") and not listing.part_number:
            listing.part_number = _clean(str(data["sku"]))
        parsed_price = _price(data.get("price"))
        if parsed_price is not None:
            listing.price = parsed_price
        availability = (data.get("availability") or "").lower()
        if "instock" in availability:
            listing.available = True
        elif "outofstock" in availability:
            listing.available = False
        else:
            listing.available = None
            listing.notes.append("stock status was not confirmed by structured product data")
        if data.get("description"):
            listing.notes.append("product description was read from the product page")

    # ------------------------------------------------------------------ collect

    async def collect(self, profile: VehicleProfile) -> SearchOutcome:
        await self.start()
        vehicle = _vehicle_label(profile)

        # Guard against callers that invoke collect() without first calling advance().
        need = await self.advance(profile)
        if need:
            return SearchOutcome(
                vendor=self.name,
                status="extraction_failed",
                vehicle_label=vehicle,
                listings=[],
                source_url=self.last_search_url or self.page.url,
                message=need.question,
            )

        if getattr(self, "_using_search_fallback", False):
            vehicle = f"{profile.maker or profile.hints.get('make') or ''} {profile.model or profile.hints.get('model') or ''}".strip() or "the requested vehicle"
            self.progress(f"Reading AutoDukan search bar results for {vehicle}")
            await self.page.wait_for_timeout(900)
            cards = await self._read_cards()
            listings: list[Listing] = []
            seen: set[str] = set()
            for card in cards:
                url = urljoin(self.base_url + "/", card.get("url") or "")
                title = _clean(card.get("title"))
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                clean_title = re.sub(r"^(?:Add to Cart|View)\s*", "", title, flags=re.I).strip()
                listing = Listing(
                    title=clean_title,
                    brand=_clean(card.get("brand") or profile.maker or ""),
                    part_number=_clean(card.get("sku") or ""),
                    price=_price(card.get("priceText")),
                    mrp=_price(card.get("mrpText")),
                    url=url,
                    fitment="unverified",
                    fitment_basis=f"AutoDukan keyword search bar fallback for '{vehicle}'",
                )
                listings.append(listing)
            return SearchOutcome(
                vendor=self.name,
                status="ok" if listings else "no_results",
                vehicle_label=vehicle,
                listings=listings,
                source_url=self.page.url,
                message=f"AutoDukan search bar found {len(listings)} listings for {vehicle}." if listings else f"AutoDukan search bar found no listings for {vehicle}."
            )

        self.progress(f"Reading AutoDukan {profile.category} listings for {vehicle}")
        await self.page.wait_for_timeout(900)
        state = await self._page_state()
        body_norm = _norm(state.get("text", ""))

        if "variant missing" in body_norm or "please select the car variant" in body_norm or "please select the car varient" in body_norm:
            return SearchOutcome(
                vendor=self.name,
                status="extraction_failed",
                vehicle_label=vehicle,
                listings=[],
                source_url=self.page.url,
                message="AutoDukan is still showing a vehicle-variant mismatch. No products were compared.",
            )

        cards = await self._read_cards()
        if not cards:
            no_results_text = any(
                phrase in body_norm
                for phrase in ("product not found", "no products found", "no product found", "0 products")
            )
            if no_results_text and (self._car_model_id or self._vehicle_confirmed):
                return SearchOutcome(
                    vendor=self.name,
                    status="ok",
                    vehicle_label=vehicle,
                    listings=[],
                    source_url=self.page.url,
                    message=f"AutoDukan has no {profile.category} in stock for {vehicle}.",
                )
            message = (
                "AutoDukan displayed a no-products message for the selected vehicle/category, "
                "but no product cards could be independently extracted. Verify the page manually."
                if no_results_text
                else "AutoDukan loaded, but the adapter could not identify product cards. This is an extraction failure, not proof that the supplier has no matching parts."
            )
            return SearchOutcome(
                vendor=self.name,
                status="extraction_failed",
                vehicle_label=vehicle,
                listings=[],
                source_url=self.page.url,
                message=message,
            )

        listings: list[Listing] = []
        seen: set[str] = set()
        for card in cards:
            url = urljoin(self.base_url + "/", card.get("url") or "")
            title = _clean(card.get("title"))
            if not url or not title or url in seen:
                continue
            seen.add(url)
            is_verified = bool(self._vehicle_confirmed and (self._car_model_id or self._category_confirmed))
            clean_title = re.sub(r"^(?:Add to Cart|View)\s*", "", title, flags=re.I).strip()
            card_brand = _clean(card.get("brand") or profile.maker or "")
            listing = Listing(
                title=clean_title,
                brand=card_brand,
                part_number=_clean(card.get("sku") or ""),
                price=_price(card.get("priceText")),
                mrp=_price(card.get("mrpText")),
                url=url,
                fitment="verified" if is_verified else "unverified",
                fitment_basis=(
                    f"AutoDukan verified vehicle catalog for {vehicle}"
                    if is_verified
                    else "Vehicle/category fitment was not confirmed"
                ),
            )
            if not is_verified:
                listing.notes.append("Confirm exact OEM/variant compatibility with AutoDukan before ordering")
            if listing.price is None:
                listing.notes.append("price was not readable on the category result")
            listing.available = True if listing.price is not None else None
            listings.append(listing)

        if not listings:
            return SearchOutcome(
                vendor=self.name,
                status="extraction_failed",
                vehicle_label=vehicle,
                listings=[],
                source_url=self.page.url,
                message="Product-like links were detected, but none could be normalized into usable listings.",
            )

        self.progress(f"Enriching {min(len(listings), MAX_PRODUCT_PAGES)} AutoDukan product pages")
        targets = listings[:MAX_PRODUCT_PAGES]
        pages = [await self.page.context.new_page() for _ in range(min(DETAIL_WORKERS, len(targets)))]
        queue = list(targets)

        async def worker(tab: Page):
            while queue:
                item = queue.pop(0)
                try:
                    await self._read_detail(tab, item)
                except Exception as exc:
                    item.notes.append(f"detail page could not be read ({type(exc).__name__})")

        try:
            await asyncio.gather(*(worker(tab) for tab in pages))
        finally:
            for tab in pages:
                await tab.close()

        for item in listings[MAX_PRODUCT_PAGES:]:
            item.notes.append("category-card data only; detail page not opened")

        stock_rank = {True: 0, None: 1, False: 2}
        listings.sort(key=lambda item: (stock_rank.get(item.available, 1), item.price if item.price is not None else float("inf")))

        return SearchOutcome(
            vendor=self.name,
            status="ok",
            vehicle_label=vehicle,
            listings=[item.to_dict() for item in listings],
            source_url=self.page.url,
        )
