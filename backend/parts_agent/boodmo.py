"""Boodmo browser adapter.

Drives the real category page the way a person would: pick the part
category, then Maker -> Model Line -> Year -> Modification. Whenever the site
asks for something we don't know, `advance()` returns a NeedInput carrying the
site's own option list instead of guessing. Listings are only collected once a
full modification is selected, so compatibility comes from Boodmo's fitment
filter rather than from title matching.

Only the rendered pages are read. Boodmo's JSON API rejects direct calls
without its own headers, and we don't try to work around that.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Callable, Optional

from playwright.async_api import async_playwright, Page, Browser

from . import llm, variants
from .models import Listing, NeedInput, SearchOutcome, VehicleProfile

log = logging.getLogger(__name__)

BASE = "https://boodmo.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0 Safari/537.36")
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--disable-software-rasterizer",
    "--mute-audio",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-extensions-with-background-pages",
    "--disable-extensions",
    "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints,IsolateOrigins,site-per-process",
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
    "--force-color-profile=srgb",
    "--js-flags=--max-old-space-size=128",
]
CATALOG_FILE = Path(__file__).resolve().parent.parent / "data" / "boodmo_catalog.json"
if not CATALOG_FILE.exists():
    _alt_cat = Path(__file__).resolve().parent.parent.parent / "data" / "boodmo_catalog.json"
    if _alt_cat.exists():
        CATALOG_FILE = _alt_cat

CACHE = Path(__file__).resolve().parent.parent / "cache" / "boodmo_categories.json"
if not CACHE.exists():
    _alt_cache = Path(__file__).resolve().parent.parent.parent / "cache" / "boodmo_categories.json"
    if _alt_cache.exists():
        CACHE = _alt_cache


def _load_catalog() -> dict:
    data = {}
    if CATALOG_FILE.exists():
        try:
            data = json.loads(CATALOG_FILE.read_text())
        except Exception:
            pass
    if CACHE.exists():
        try:
            legacy = json.loads(CACHE.read_text())
            if not data:
                data = legacy
            else:
                data.setdefault("leaf", {})
                data["leaf"].update(legacy.get("leaf", {}))
                data["leaf"].update(data.get("categories", {}))
        except Exception:
            pass
    if "categories" in data and "leaf" not in data:
        data["leaf"] = data["categories"]
    return data

# Placeholder text of each vehicle dropdown in the category sidebar.
SELECTS = {
    "maker": r"^Choose car maker",
    "model": r"^Choose Model Line",
    "year": r"^Choose Year",
    "variant": r"^Choose Modification",
}

ENRICH_LIMIT = 2 if not os.environ.get("DISPLAY") else 12      # product pages to open for seller/stock details
ENRICH_TABS = 1 if not os.environ.get("DISPLAY") else 2        # opened concurrently

# Words that turn a part class into an accessory of the requested part,
# e.g. "Oil Filter Assy" / "Oil Filter Housing" under the Oil Filter category.
ACCESSORY_WORDS = {"assy", "support", "housing", "bracket", "gasket", "cover", "seal", "sensor",
                   "mounting", "adapter", "cap", "holder", "clip", "pipe", "hose"}


def _price(text) -> Optional[float]:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    if not isinstance(text, str):
        text = str(text)
    m = re.search(r"[\d,]+(?:\.\d+)?", text.replace("₹", ""))
    return float(m.group(0).replace(",", "")) if m else None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _is_related(part_class: str, category: str) -> bool:
    """True when the listing's class is an accessory of the category's part
    rather than the part itself. Different wording for the same part
    ("Brake Pad Set" under "Front Brake Pads") is not treated as related."""
    cls, cat = set(_norm(part_class).split()), set(_norm(category).split())
    cls_s = {w.rstrip("s") for w in cls}
    cat_s = {w.rstrip("s") for w in cat}
    return bool((cls_s - cat_s) & ACCESSORY_WORDS) and not (cat_s - cls_s) & ACCESSORY_WORDS


def _part_number(href: str, masked: str) -> tuple[str, str]:
    """Boodmo masks part numbers ("KLP...9AA"). Some product URLs carry the full
    number; use it only when it agrees with the masked prefix and suffix.
    Returns (part_number, boodmo_item_id)."""
    m = re.search(r"/part-(.+)-(\d+)/?$", href)
    if not m:
        return masked, ""
    slug, item_id = m.groups()
    if masked and "..." in masked:
        pre, suf = (s.upper() for s in masked.split("...", 1))
        for tok in re.split(r"[_-]", slug):
            t = tok.upper()
            if len(t) > len(pre) + len(suf) and t.startswith(pre) and t.endswith(suf):
                return t, item_id
    return masked, item_id


class BoodmoAdapter:
    name = "boodmo"

    def __init__(self, headless: bool = True, progress: Callable[[str], None] | None = None,
                 record_video_dir: Optional[str] = None):
        if not os.environ.get("DISPLAY"):
            headless = True
        self.headless = headless
        self.progress = progress or (lambda msg: None)
        self.record_video_dir = record_video_dir
        self.video_path: Optional[str] = None
        self._pw = None
        self.browser: Browser | None = None
        self.page: Page | None = None
        self.category_href: Optional[str] = None
        # After a modification is picked, Boodmo rewrites the dropdown labels (e.g.
        # drops the engine code), so remember the selection instead of re-finding it.
        self._selected: Optional[tuple[str, str]] = None   # (label, page url)
        self._variant_dupes = 1
        self._using_search_fallback = False

    # ------------------------------------------------------------ lifecycle

    async def start(self):
        if self.page and not self.page.is_closed():
            try:
                await self.page.evaluate("1")
                return
            except Exception:
                log.warning("Boodmo page target crashed or unresponsive; restarting...")
                await self.close()
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=self.headless, args=CHROMIUM_ARGS)
        ctx_opts = {
            "viewport": {"width": 1366, "height": 900},
            "user_agent": UA,
            "locale": "en-IN",
        }
        if self.record_video_dir:
            Path(self.record_video_dir).mkdir(parents=True, exist_ok=True)
            ctx_opts["record_video_dir"] = self.record_video_dir
            ctx_opts["record_video_size"] = {"width": 1280, "height": 720}
        ctx = await self.browser.new_context(**ctx_opts)
        async def _block_heavy(route):
            try:
                if route.request.resource_type in ("image", "media", "font"):
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                pass

        try:
            await ctx.route("**/*", _block_heavy)
        except Exception:
            pass
        self.page = await ctx.new_page()

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
        self.page = self.browser = self._pw = None

    async def _wait(self, ms: int):
        await asyncio.sleep(ms / 1000.0)

    async def _goto(self, url: str):
        for attempt in range(2):
            try:
                if not self.page or self.page.is_closed():
                    if not self.browser or not self.browser.is_connected():
                        await self.start()
                    else:
                        ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
                        self.page = await ctx.new_page()
                await self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
                break
            except Exception as exc:
                err_msg = str(exc).lower()
                is_crash = "crashed" in err_msg or "closed" in err_msg
                log.warning("Boodmo navigation attempt %d failed for %s (%s)", attempt + 1, url, exc)
                if is_crash and self.page:
                    try:
                        await self.page.close()
                    except Exception:
                        pass
                    self.page = None
                if attempt == 0:
                    try:
                        if not self.page or self.page.is_closed():
                            ctx = self.browser.contexts[0] if (self.browser and self.browser.contexts) else None
                            if ctx:
                                self.page = await ctx.new_page()
                        if self.page:
                            await self.page.goto(url, wait_until="commit", timeout=12000)
                            break
                    except Exception as e2:
                        log.warning("Boodmo retry commit failed: %s", e2)
                else:
                    raise
        try:
            if self.page and not self.page.is_closed():
                await self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

    # ------------------------------------------------------------ categories

    def _load_cache(self) -> dict:
        return _load_catalog()

    def _save_cache(self, data: dict):
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        try:
            CACHE.write_text(json.dumps(data, indent=1))
        except Exception:
            pass

    async def _top_categories(self) -> dict:
        catalog = _load_catalog()
        return catalog.get("sections") or catalog.get("top") or {}

    async def resolve_category(self, profile: VehicleProfile) -> Optional[NeedInput]:
        """Map the spoken part name to one Boodmo leaf category using the comprehensive offline catalogue."""
        if profile.category and self.category_href:
            return None

        catalog = _load_catalog()
        cat_map = catalog.get("categories", {})
        synonyms = catalog.get("synonyms_and_fallbacks", {})

        part = (profile.part or "").strip()
        norm_p = _norm(part)

        # 1. Check verified synonyms and common part names
        if norm_p in synonyms:
            entry = synonyms[norm_p]
            primary = entry["primary"]
            alternates = entry.get("alternates", [])
            href = cat_map.get(primary) or catalog.get("leaf", {}).get(primary)
            if href:
                profile.category = primary
                self.category_href = href
                profile.hints["alternate_categories"] = [alt for alt in alternates if alt in cat_map]
                return None

        # 2. Check direct case-insensitive exact match in categories
        for name, href in cat_map.items():
            if _norm(name) == norm_p:
                profile.category = name
                self.category_href = href
                words = set(norm_p.split())
                alts = [
                    n for n in cat_map
                    if n != name and words & set(_norm(n).split())
                ][:4]
                profile.hints["alternate_categories"] = alts
                return None

        # 3. Substring matching in categories
        exact_substrings = [
            name for name in cat_map
            if norm_p and (norm_p in _norm(name) or _norm(name) in norm_p)
        ]
        if len(exact_substrings) == 1:
            primary = exact_substrings[0]
            profile.category = primary
            self.category_href = cat_map[primary]
            profile.hints["alternate_categories"] = []
            return None
        elif len(exact_substrings) > 1:
            exact_substrings.sort(key=lambda n: (not _norm(n).startswith(norm_p), len(n)))
            primary = exact_substrings[0]
            profile.category = primary
            self.category_href = cat_map[primary]
            profile.hints["alternate_categories"] = exact_substrings[1:5]
            return None

        # 4. LLM semantic categorization with offline fallback
        self.progress(f"Finding the '{part}' category on Boodmo")
        cat_names = list(cat_map.keys())
        part_words = set(norm_p.split())
        cat_cands = sorted(cat_names, key=lambda c: len(part_words & set(_norm(c).split())), reverse=True)[:100]
        ranked: list[str] = []
        try:
            resp = await llm.generate_async(
                "You are an automotive parts expert. Which Boodmo category best matches this car part?\n"
                f"Part: {part}\n"
                "Return up to 3 category names from the list below, exactly as written, one per line:\n"
                + "\n".join(cat_cands)
            )
            picks = [l.strip(" -*") for l in (resp.text or "").splitlines() if l.strip(" -*") in cat_map]
            ranked = picks[:3]
        except Exception:
            ranked = []

        if not ranked:
            ranked = llm.closest(part, cat_names, n=5)

        if ranked:
            primary = ranked[0]
            profile.category = primary
            self.category_href = cat_map.get(primary, "")
            profile.hints["alternate_categories"] = [r for r in ranked[1:4] if r in cat_map]
            return None

        cands = ["Maintenance Service Parts", "Filters", "Brake System", "Engine"]
        return NeedInput(field="category",
                         question=f"Boodmo has a few kinds of {part}. Which one do you need: "
                                  + ", ".join(cands[:-1]) + " or " + cands[-1] + "?",
                         options=cands)

    def _set_category(self, profile: VehicleProfile, name: str, href: str,
                      remember_part: bool = False) -> None:
        profile.category = name
        self.category_href = href
        return None

    def choose_category(self, profile: VehicleProfile, name: str) -> None:
        """Apply a category the mechanic picked from a NeedInput list."""
        catalog = _load_catalog()
        cat_map = catalog.get("categories", {}) or catalog.get("leaf", {})
        if name in cat_map:
            self._set_category(profile, name, cat_map[name], remember_part=True)
            return
        cache = self._load_cache()
        for kids in [*cache.get("children", {}).values(), cache.get("top", {})]:
            if name in kids:
                self._set_category(profile, name, kids[name], remember_part=True)
                return
        raise KeyError(name)

    # ------------------------------------------------------------ dropdowns

    async def _select_state(self, key: str) -> Optional[dict]:
        return await self.page.evaluate(
            """(pat) => {
                const re = new RegExp(pat, 'i');
                const s = [...document.querySelectorAll('select')]
                    .find(s => s.options.length && re.test(s.options[0].text.trim()));
                if (!s) return null;
                const opts = [...s.options].slice(1).map(o => o.text.trim());
                return {options: opts, selected: s.selectedIndex > 0 ? s.options[s.selectedIndex].text.trim() : null};
            }""", SELECTS[key])

    async def _wait_options(self, key: str, timeout_ms: int = 9000) -> Optional[dict]:
        """Wait until the dropdown has options and they've stopped changing. The
        list refreshes asynchronously after an upstream choice, and reading it
        too early returns the unfiltered list (e.g. every year's variants)."""
        waited, stable = 0, 0
        state, prev = None, None
        while waited < timeout_ms:
            state = await self._select_state(key)
            if state and state["options"]:
                stable = stable + 1 if state["options"] == prev else 0
                prev = state["options"]
                if stable >= 2:
                    return state
            await self._wait(300)
            waited += 300
        return state

    async def _options(self, key: str, profile: VehicleProfile) -> Optional[dict]:
        """Like _wait_options, but if the dropdown doesn't show up (slow page),
        reload once and restore the upstream selections."""
        state = await self._wait_options(key)
        if state and state["options"]:
            return state
        self.progress("Page was slow, reloading")
        await self._goto(self.page.url)
        for up in ("maker", "model", "year"):
            if up == key:
                break
            value = getattr(profile, up)
            st = await self._wait_options(up, timeout_ms=15000)
            if value and st and st["selected"] != value:
                await self._choose(up, value)
        return await self._wait_options(key, timeout_ms=15000)

    async def _choose(self, key: str, label: str, occurrence: int = 0):
        """Select an option by its visible label (labels can repeat, so by index)."""
        idx = await self.page.evaluate(
            """([pat, label, occ]) => {
                const re = new RegExp(pat, 'i');
                const s = [...document.querySelectorAll('select')]
                    .find(s => s.options.length && re.test(s.options[0].text.trim()));
                let seen = 0;
                for (let i = 0; i < s.options.length; i++)
                    if (s.options[i].text.trim() === label && seen++ === occ) return i;
                return -1;
            }""", [SELECTS[key], label, occurrence])
        if idx < 0:
            raise RuntimeError(f"Option {label!r} not found in {key} dropdown")
        before = self.page.url
        try:
            selected = await self.page.evaluate("""([pat, idx]) => {
                const re = new RegExp(pat, 'i');
                const s = [...document.querySelectorAll('select')]
                    .find(s => s.options.length && re.test(s.options[0].text.trim()));
                if (s && idx >= 0 && idx < s.options.length) {
                    s.selectedIndex = idx;
                    s.dispatchEvent(new Event('change', { bubbles: true }));
                    s.dispatchEvent(new Event('input', { bubbles: true }));
                    return true;
                }
                return false;
            }""", [SELECTS[key], idx])
            if not selected:
                handle = await self.page.evaluate_handle(
                    """(pat) => {
                        const re = new RegExp(pat, 'i');
                        return [...document.querySelectorAll('select')]
                            .find(s => s.options.length && re.test(s.options[0].text.trim()));
                    }""", SELECTS[key])
                await handle.as_element().select_option(index=idx)
        except Exception as exc:
            log.warning("Boodmo select option %s failed (%s)", label, exc)
            handle = await self.page.evaluate_handle(
                """(pat) => {
                    const re = new RegExp(pat, 'i');
                    return [...document.querySelectorAll('select')]
                        .find(s => s.options.length && re.test(s.options[0].text.trim()));
                }""", SELECTS[key])
            await handle.as_element().select_option(index=idx)
        if key in ("modification", "variant"):
            for _ in range(25):
                await self._wait(200)
                if self.page.url != before:
                    break
        else:
            # Child dropdowns are updated asynchronously via fast AJAX; do not stall in 7.5s url loop
            await self._wait(250)

    async def _ensure_category_page(self):
        if not self.page.url.startswith(BASE + self.category_href):
            self.progress("Opening the category page")
            await self._goto(BASE + self.category_href)

    # ------------------------------------------------------------ the walk

    async def _search_fallback(self, profile: VehicleProfile) -> None:
        import urllib.parse
        make = profile.maker or profile.hints.get("make") or ""
        model = profile.model or profile.hints.get("model") or ""
        part = profile.part or profile.category or ""
        terms = [make, model, part]
        query = " ".join(t for t in terms if t).strip()
        self.progress(f"Falling back to search for '{query}' on Boodmo")
        url = f"{BASE}/search/{urllib.parse.quote_plus(query)}/"
        await self._goto(url)
        await self._wait(800)
        self._using_search_fallback = True
        self._selected = ("search_fallback", self.page.url)

    async def advance(self, profile: VehicleProfile) -> Optional[NeedInput]:
        """Push the vehicle selector as far as known values allow.

        Returns NeedInput when the site needs something we don't have, or None
        when a full modification is selected. Safe to call repeatedly: values
        already selected on the page are left alone.
        """
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
        if profile.variant and self._selected == (profile.variant, self.page.url):
            return None
        await self._ensure_category_page()

        # ---- maker
        state = await self._options("maker", profile)
        if not state:
            log.warning("Boodmo vehicle selector not found on category page; falling back to keyword search")
            await self._search_fallback(profile)
            return None
        makers = list(dict.fromkeys(state["options"]))
        if not profile.maker or profile.maker not in makers:
            need = self._match_or_ask("maker", profile.maker or profile.hints.get("make"), makers,
                                      "What is the car's make (brand), such as Tata, Hyundai, or Maruti Suzuki?",
                                      profile)
            if need:
                return need
        if state["selected"] != profile.maker:
            self.progress(f"Selecting maker {profile.maker}")
            await self._choose("maker", profile.maker)

        # ---- model line
        state = await self._options("model", profile)
        models = list(dict.fromkeys(state["options"])) if state else []
        if not models:
            log.warning("Boodmo shows no model lines for %s; falling back to keyword search", profile.maker)
            await self._search_fallback(profile)
            return None
        if not profile.model or profile.model not in models:
            candidate_model = profile.model or profile.hints.get("model")
            if candidate_model:
                matched_idx = llm.match_option_locally(candidate_model, models)
                if matched_idx is None:
                    log.info("Boodmo model '%s' not in dropdown choices; falling back to search bar", candidate_model)
                    await self._search_fallback(profile)
                    return None
                profile.model = models[matched_idx]
            else:
                need = self._match_or_ask("model", None, models,
                                          f"Which {profile.maker.title()} model is it? For example, "
                                          + ", ".join(models[:3]) + ("." if models else "?"), profile)
                if need:
                    return need
        if state["selected"] != profile.model:
            self.progress(f"Selecting model {profile.model}")
            await self._choose("model", profile.model)

        # ---- year
        state = await self._options("year", profile)
        years = [y for y in (state["options"] if state else []) if y not in profile.rejected_years]
        if not years:
            raise RuntimeError(f"Boodmo lists no years for {profile.maker} {profile.model}")
        if not profile.year:
            hint = str(profile.hints.get("year") or "")
            if hint and hint in years:
                profile.year = hint
            else:
                note = None
                if hint:
                    note = f"Boodmo doesn't list the {profile.model.title()} for {hint}."
                return NeedInput(field="year", options=years, note=note,
                                 question=f"Which year is the {profile.model.title()}? "
                                          f"Boodmo lists {years[0]} to {years[-1]}.")
        if state["selected"] != profile.year:
            self.progress(f"Selecting year {profile.year}")
            await self._choose("year", profile.year)

        # ---- modification (variant)
        state = await self._options("variant", profile)
        labels = state["options"] if state else []
        if not labels:
            # The site has nothing for this year: say so and ask again.
            bad = profile.year
            profile.rejected_years.append(bad)
            profile.year = None
            left = [y for y in years if y != bad]
            return NeedInput(field="year", options=left,
                             note=f"Boodmo has no {profile.model.title()} variants listed for {bad}.",
                             question=f"Which other year should I try? Options are {', '.join(left)}.")
        if profile.variant and profile.variant in labels:
            await self._select_variant(profile.variant, labels)
            return None
        profile.variant = None

        # Apply details the mechanic mentioned in passing ("2021 diesel automatic"),
        # but only where they match one of the site's own values.
        current = variants.apply_filters(labels, profile.variant_filters)
        pending_hints = {k: v for k, v in profile.hints.items()
                         if profile.variant_filters.get(k) in (None, variants.SKIP)}
        if pending_hints:
            _, applied, source = variants.apply_hints(current, pending_hints)
            said = {k: v for k, v in applied.items() if source[k] == "said"}
            profile.variant_filters.update(said)
            if said:
                self.progress(f"Using what you said: {said}")
            mentioned = [(k, v) for k, v in applied.items() if source[k] == "mention"
                         and profile.variant_filters.get(k) in (None, variants.SKIP)]
            if mentioned:
                # Said out of turn (and possibly misheard): confirm before narrowing.
                attr, value = mentioned[0]
                pool = variants.apply_filters(labels, profile.variant_filters)
                values = sorted({variants.parse_label(l)[attr] or variants.OTHER for l in pool})
                return NeedInput(field="variant_attr", attr=attr, options=values, candidate=value,
                                 question=f"Just to check: is it {variants.spoken_value(attr, value)}?")
        remaining = variants.unique_labels(variants.apply_filters(labels, profile.variant_filters))
        if not remaining:
            # The mechanic's details matched nothing; start the variant questions over.
            profile.variant_filters = {}
            remaining = variants.unique_labels(labels)
        if len(remaining) == 1:
            profile.variant = remaining[0]
            self.progress(f"Only one variant fits: {profile.variant}")
            await self._select_variant(profile.variant, labels)
            return None
        q = variants.next_question(remaining, exclude=tuple(profile.variant_filters))
        if q:
            attr, values = q
            return NeedInput(field="variant_attr", attr=attr, options=values,
                             question=variants.question_for(attr, values, remaining),
                             note=f"Boodmo lists {len(remaining)} {profile.model.title()} "
                                  f"{profile.year} variants." if not profile.variant_filters else None)
        spoken = variants.describe_differences(remaining)
        listed = "; ".join(f"{i + 1}, {s}" for i, s in enumerate(spoken))
        return NeedInput(field="variant", options=remaining, spoken=spoken,
                         question=f"There are {len(remaining)} versions. Which one is it: {listed}?")

    async def _select_variant(self, label: str, labels: list[str], occurrence: int = 0):
        self._variant_dupes = max(labels.count(label), 1)
        await self._choose("variant", label, occurrence=occurrence)
        self._selected = (label, self.page.url)

    async def _reselect(self, profile: VehicleProfile, occurrence: int):
        """Walk the selector again to reach another catalogue entry with the same label."""
        self._selected = None
        await self._goto(BASE + self.category_href)
        for key, value in (("maker", profile.maker), ("model", profile.model), ("year", profile.year)):
            await self._wait_options(key)
            await self._choose(key, value)
        state = await self._wait_options("variant")
        await self._select_variant(profile.variant, state["options"] if state else [], occurrence)

    def _match_or_ask(self, field: str, hint: Optional[str], options: list[str], question: str,
                      profile: VehicleProfile) -> Optional[NeedInput]:
        if hint:
            i = llm.match_option_locally(hint, options)
            if i is not None:
                setattr(profile, field, options[i])
                return None
            close = llm.closest(hint, options)
            return NeedInput(field=field, options=close,
                             note=f"I couldn't find '{hint}' on Boodmo.",
                             question=f"Did you mean {', '.join(close[:-1])} or {close[-1]}?")
        return NeedInput(field=field, options=options, question=question)

    # ------------------------------------------------------------ listings

    async def _read_tiles(self) -> list[dict]:
        try:
            if not self.page or self.page.is_closed():
                return []
            await self.page.wait_for_selector(".products-grid .product-item-tile, .result-header__total",
                                              timeout=8000)
        except Exception:
            pass
        await self._wait(600)
        for attempt in range(2):
            try:
                if not self.page or self.page.is_closed():
                    return []
                return await self.page.evaluate(
                    """() => [...document.querySelectorAll('.products-grid .product-item-tile')].map(t => {
                        const q = s => t.querySelector(s)?.innerText.trim() || null;
                        return {
                            title: q('.product-item-tile__title'),
                            price: q('.product-item-tile__price__current'),
                            mrp: q('.product-item-tile__price__mrp--discount'),
                            brand: q('.product-item-tile__desc__brand'),
                            number: q('.product-item-tile__desc__number'),
                            href: t.querySelector('a.product-item-tile__in')?.getAttribute('href'),
                        };
                    })""")
            except Exception as exc:
                log.warning("Boodmo _read_tiles attempt %d failed: %s", attempt + 1, exc)
                if attempt == 0 and self.page and not self.page.is_closed():
                    try:
                        await self.page.reload(wait_until="domcontentloaded", timeout=12000)
                        await self._wait(1000)
                    except Exception:
                        pass
        return []

    async def _enrich(self, page: Page, listing: Listing, category: Optional[str] = None):
        try:
            await page.goto(listing.url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            try:
                await page.goto(listing.url, wait_until="commit", timeout=10000)
            except Exception as exc:
                listing.notes.append(f"product page navigation failed: {exc}")
                return
        try:
            await page.wait_for_selector(".part-info-spec, .part-offers__list__item, .part-offers", timeout=3500)
        except Exception:
            if not await page.query_selector(".part-info-spec"):
                listing.notes.append("product page did not load; stock not checked")
                return
        await asyncio.sleep(0.2)
        info = await page.evaluate(
            """() => {
                const spec = {};
                document.querySelectorAll('.part-info-spec__item').forEach(i => {
                    const k = i.querySelector('.part-info-spec__title')?.innerText.trim();
                    const v = i.querySelector('.part-info-spec__value')?.innerText.trim();
                    if (k) spec[k] = v;
                });
                const offers = [...document.querySelectorAll('.part-offers__list__item')].map(o => {
                    const out = {};
                    o.querySelectorAll('.part-offers__list__item__in').forEach(c => {
                        const k = c.querySelector('.part-offers__list__item__title')?.innerText.trim();
                        const v = c.querySelector('.part-offers__list__item__value');
                        if (k) out[k] = (k === 'Price' ? v?.querySelector('span')?.innerText : v?.innerText)?.trim();
                    });
                    out.stock = o.querySelector('.part-offers__list__item--stock')?.innerText.trim() || null;
                    return out;
                });
                return {spec, offers};
            }""")
        listing.origin = info["spec"].get("Origin")
        cls = info["spec"].get("Class")
        if cls:
            listing.part_class = cls
            if category and _is_related(cls, category):
                listing.match = "related"
                listing.notes.append(f"related part ({cls}), not the {category.lower()} itself")
        for o in info["offers"]:
            if not o.get("Sold by"):
                continue
            listing.offers.append({"seller": o.get("Sold by"), "price": _price(o.get("Price")),
                                   "stock": o.get("stock"), "dispatch": o.get("Dispatch within")})
        priced = [o for o in listing.offers if o["price"] is not None]
        if priced:
            listing.best_offer = min(priced, key=lambda o: o["price"])
            listing.price = listing.best_offer["price"]
        listing.available = bool(priced)
        if not priced:
            listing.notes.append("no seller offer on the product page")

    async def collect(self, profile: VehicleProfile) -> SearchOutcome:
        """Read listings for the selected modification. When Boodmo has several
        catalogue entries with the same label, check each one and mark parts that
        don't appear under all of them as partial fits."""
        await self.start()
        if getattr(self, "_using_search_fallback", False):
            vehicle = f"{profile.maker or profile.hints.get('make') or ''} {profile.model or profile.hints.get('model') or ''}".strip() or "the requested vehicle"
            source_url = self.page.url if self.page else None
            tiles = await self._read_tiles()
            if not tiles and ("/part-" in self.page.url):
                try:
                    title = await self.page.locator("h1").inner_text()
                    price_loc = self.page.locator(".product-price__current, [class*='price__current']").first
                    price_text = await price_loc.inner_text() if await price_loc.count() else None
                    tiles = [{
                        "title": title.strip(),
                        "price": price_text,
                        "mrp": None,
                        "brand": profile.maker or "",
                        "number": "",
                        "href": self.page.url.replace(BASE, "")
                    }]
                except Exception:
                    pass
            listings: list[Listing] = []
            for t in tiles:
                number, item_id = _part_number(t.get("href") or "", t.get("number") or "")
                listing = Listing(
                    title=(t.get("title") or "").strip(), brand=t.get("brand") or profile.maker or "", part_number=number,
                    price=_price(t.get("price")), mrp=_price(t.get("mrp")), url=BASE + (t.get("href") or ""),
                    fitment="unverified",
                    fitment_basis=f"Boodmo keyword search bar fallback for '{vehicle}'",
                )
                if "..." in number:
                    listing.notes.append(f"Boodmo masks the full part number; item id {item_id}")
                listings.append(listing)
            return SearchOutcome(
                vendor=self.name,
                status="ok" if listings else "no_results",
                vehicle_label=vehicle,
                listings=listings,
                source_url=source_url,
                message=f"Boodmo search bar found {len(listings)} listings for {vehicle}." if listings else f"Boodmo search bar found no listings for {vehicle}."
            )

        dupes = self._variant_dupes
        vehicle = f"{profile.maker} {profile.model} {profile.year} {profile.variant}"
        seen: dict = {}
        source_url = None
        try:
            for occ in range(dupes):
                page_url = self.page.url if self.page else ""
                if occ or self._selected != (profile.variant, page_url):
                    await self._reselect(profile, occ)
                source_url = source_url or (self.page.url if self.page else "")
                self.progress(f"Reading {profile.category} listings for {vehicle}"
                              + (f" (catalogue entry {occ + 1} of {dupes})" if dupes > 1 else ""))
                for t in await self._read_tiles():
                    if not t.get("href"):
                        continue
                    key = t["href"]
                    if key not in seen:
                        seen[key] = {"tile": t, "hits": 0}
                    seen[key]["hits"] += 1
        except Exception as exc:
            log.warning("Boodmo catalogue walk failed (%s); falling back to direct search", exc)
            await self._search_fallback(profile)
            await self._wait(1200)
            tiles = await self._read_tiles()
            listings = []
            for t in tiles:
                number, item_id = _part_number(t.get("href") or "", t.get("number") or "")
                listing = Listing(
                    title=(t.get("title") or "").strip(), brand=t.get("brand") or profile.maker or "", part_number=number,
                    price=_price(t.get("price")), mrp=_price(t.get("mrp")), url=BASE + (t.get("href") or ""),
                    fitment="unverified",
                    fitment_basis=f"Boodmo keyword search bar fallback for '{vehicle}'",
                )
                if "..." in number:
                    listing.notes.append(f"Boodmo masks the full part number; item id {item_id}")
                listings.append(listing)
            return SearchOutcome(
                vendor=self.name,
                status="ok" if listings else "no_results",
                vehicle_label=vehicle,
                listings=listings,
                source_url=self.page.url if self.page else None,
                message=f"Boodmo search bar found {len(listings)} listings for {vehicle}." if listings else f"Boodmo search bar found no listings for {vehicle}."
            )

        listings: list[Listing] = []
        for href, rec in seen.items():
            t = rec["tile"]
            number, item_id = _part_number(href, t.get("number") or "")
            full = rec["hits"] == max(dupes, 1)
            listing = Listing(
                title=(t.get("title") or "").strip(), brand=t.get("brand") or "", part_number=number,
                price=_price(t.get("price")), mrp=_price(t.get("mrp")), url=BASE + href,
                fitment="verified" if full else "partial",
                fitment_basis=(f"Boodmo fitment filter: {vehicle}" if full else
                               f"listed under {rec['hits']} of {dupes} Boodmo entries named '{profile.variant}'"),
            )
            if "..." in number:
                listing.notes.append(f"Boodmo masks the full part number; item id {item_id}")
            listings.append(listing)

        if not listings:
            # Multi-category fallback: if primary category returned 0 parts,
            # locate it in other candidate categories from the Boodmo catalogue
            alternates = list(profile.hints.get("alternate_categories") or [])
            catalog = _load_catalog()
            cat_map = catalog.get("categories", {})
            checked_categories = [profile.category]

            current_url = self.page.url
            # Detect vehicle suffix if present in URL: e.g. /catalog/1234-slug/<vehicle_suffix>
            vehicle_suffix = ""
            if self.category_href and self.category_href in current_url:
                parts_after = current_url.split(self.category_href, 1)
                if len(parts_after) > 1 and parts_after[1].strip("/"):
                    vehicle_suffix = parts_after[1].lstrip("/")

            while alternates and not listings:
                alt_cat = alternates.pop(0)
                if alt_cat in checked_categories:
                    continue
                checked_categories.append(alt_cat)
                alt_href = cat_map.get(alt_cat)
                if not alt_href:
                    continue

                target_url = f"{BASE}{alt_href}{vehicle_suffix}"
                self.progress(f"No parts under {profile.category}; checking alternate category '{alt_cat}'")
                try:
                    await self._goto(target_url)
                    alt_tiles = await self._read_tiles()
                    if alt_tiles:
                        for t in alt_tiles:
                            if not t.get("href"):
                                continue
                            href = t["href"]
                            if href not in seen:
                                seen[href] = {"tile": t, "hits": 1}
                                number, item_id = _part_number(href, t.get("number") or "")
                                listing = Listing(
                                    title=(t.get("title") or "").strip(), brand=t.get("brand") or "", part_number=number,
                                    price=_price(t.get("price")), mrp=_price(t.get("mrp")), url=BASE + href,
                                    fitment="verified",
                                    fitment_basis=f"Boodmo fitment filter: {vehicle} (under {alt_cat})",
                                )
                                if "..." in number:
                                    listing.notes.append(f"Boodmo masks the full part number; item id {item_id}")
                                listings.append(listing)
                        if listings:
                            profile.hints["resolved_alternate_category"] = alt_cat
                            source_url = self.page.url
                            break
                except Exception as exc:
                    log.warning("Could not check alternate Boodmo category '%s': %s", alt_cat, exc)

        if not listings:
            checked_str = ", ".join(checked_categories)
            return SearchOutcome(vendor="boodmo", status="no_results", vehicle_label=vehicle,
                                 listings=[], source_url=source_url,
                                 message=f"This part is not listed in Boodmo's catalogue for {vehicle}. (Checked categories: {checked_str})")

        # Product pages: seller offers, stock, part class. Tiles without a price have
        # no seller offer, so only priced ones are opened, a few tabs at a time.
        priced = sorted((l for l in listings if l.price is not None), key=lambda l: l.price)
        for l in listings:
            if l.price is None:
                l.available = False
        to_check, unchecked = priced[:ENRICH_LIMIT], priced[ENRICH_LIMIT:]
        if to_check:
            self.progress(f"Checking sellers and stock on {len(to_check)} product pages")
            pages = [await self.page.context.new_page() for _ in range(min(ENRICH_TABS, len(to_check)))]
            queue: asyncio.Queue = asyncio.Queue()
            for l in to_check:
                queue.put_nowait(l)

            async def worker(pg: Page):
                while not queue.empty():
                    l = queue.get_nowait()
                    try:
                        await self._enrich(pg, l, profile.category)
                    except Exception as e:
                        l.notes.append(f"could not open product page ({type(e).__name__}); stock not checked")
            try:
                await asyncio.gather(*(worker(pg) for pg in pages))
            finally:
                for pg in pages:
                    await pg.close()
        for l in unchecked:
            l.notes.append("listed price only; seller stock not checked")

        # In stock first, then unchecked, then unavailable; exact parts before related ones.
        rank = {True: 0, None: 1, False: 2}
        listings.sort(key=lambda l: (rank[l.available], l.match != "exact", l.fitment != "verified",
                                     l.price or 1e12))
        return SearchOutcome(vendor="boodmo", status="ok", vehicle_label=vehicle,
                             listings=[l.to_dict() for l in listings], source_url=source_url)
