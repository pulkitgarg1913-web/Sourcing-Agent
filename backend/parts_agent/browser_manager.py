"""Centralized BrowserManager for Conversational Business Sourcing Agent.

Architectural guarantees:
1. Starts Playwright once per application worker.
2. Launches ONE shared Chromium browser process with production-optimized flags.
3. Provides independent BrowserContext instances per customer session and supplier adapter.
4. Prevents one supplier's navigation, cookies, or errors from affecting the other.
5. Gracefully handles browser disconnections, target crashes, and closed pages.
6. Automatically cleans up session contexts when sessions terminate.
7. Shuts down Chromium and Playwright cleanly during FastAPI lifespan shutdown.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

log = logging.getLogger(__name__)

# Standard user agent for consistent rendering across suppliers
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

# Production-safe Chromium launch arguments for Railway container deployment.
# Note:
# - No artificial 96MB V8 JS memory limit (--js-flags=--max-old-space-size=96 removed)
#   which previously caused renderer crashes on JavaScript-heavy SPA catalogs.
# - Security isolation features (IsolateOrigins, site-per-process) are preserved.
# - Necessary container headless flags (--no-sandbox, --disable-dev-shm-usage, --disable-gpu) are retained.
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
    "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints",
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
    "--force-color-profile=srgb",
    "--disk-cache-size=1048576",
    "--media-cache-size=1048576",
]


class BrowserManager:
    """Manages the lifecycle of a shared Chromium browser runtime across sessions."""

    _instance: Optional["BrowserManager"] = None

    def __init__(self):
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        # Mapping: (session_id, vendor) -> BrowserContext
        self._contexts: Dict[Tuple[str, str], BrowserContext] = {}
        # Mapping: (session_id, vendor) -> Page
        self._pages: Dict[Tuple[str, str], Page] = {}
        self._headless: bool = True if not os.environ.get("DISPLAY") else True
        self._initialized: bool = False

    @classmethod
    def get_instance(cls) -> "BrowserManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_running(self) -> bool:
        return bool(self._browser and self._browser.is_connected())

    async def _launch_browser_locked(self, *, headless: Optional[bool] = None) -> None:
        """Internal helper to launch browser while self._lock is already held."""
        if headless is not None:
            self._headless = headless if os.environ.get("DISPLAY") else True

        if self._browser and self._browser.is_connected():
            return

        # Clear dead references from previous browser instance if any
        self._contexts.clear()
        self._pages.clear()

        log.info("Initializing centralized Chromium browser runtime (headless=%s)...", self._headless)
        if self._pw is None:
            self._pw = await async_playwright().start()

        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=CHROMIUM_ARGS,
        )
        self._initialized = True
        log.info("Centralized Chromium browser runtime ready (PID=%s).", getattr(self._browser, "process_id", "N/A"))

    async def initialize(self, *, headless: Optional[bool] = None) -> None:
        """Launch the shared Chromium browser process once per worker."""
        async with self._lock:
            await self._launch_browser_locked(headless=headless)

    async def _ensure_browser(self) -> Browser:
        """Ensure the browser is running, auto-recovering from unexpected disconnections."""
        if not self._browser or not self._browser.is_connected():
            log.warning("Shared Chromium browser disconnected or not running; re-initializing...")
            if self._lock.locked():
                await self._launch_browser_locked(headless=self._headless)
            else:
                await self.initialize(headless=self._headless)
        return self._browser

    async def get_context(
        self,
        session_id: str,
        vendor: str,
        *,
        record_video_dir: Optional[str] = None,
        viewport_width: int = 1366,
        viewport_height: int = 900,
    ) -> BrowserContext:
        """Provide an isolated BrowserContext for a specific session and supplier adapter."""
        key = (session_id, vendor)
        async with self._lock:
            browser = await self._ensure_browser()

            ctx = self._contexts.get(key)
            # Verify context validity
            if ctx is not None:
                try:
                    # Test if context is still alive
                    if ctx.pages or len(ctx.browser.contexts) > 0:
                        return ctx
                except Exception:
                    log.warning("Context for session %s vendor %s invalid; recreating", session_id, vendor)
                    self._contexts.pop(key, None)

            # Create an independent context with custom viewport and route optimizations
            ctx_opts = {
                "viewport": {"width": viewport_width, "height": viewport_height},
                "user_agent": UA,
                "locale": "en-IN",
            }
            if record_video_dir:
                Path(record_video_dir).mkdir(parents=True, exist_ok=True)
                ctx_opts["record_video_dir"] = record_video_dir
                ctx_opts["record_video_size"] = {"width": 1280, "height": 720}

            ctx = await browser.new_context(**ctx_opts)

            # Block heavy non-essential assets to reduce memory and CPU on Railway
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

            self._contexts[key] = ctx
            log.info("Created isolated BrowserContext for session '%s', vendor '%s'", session_id, vendor)
            return ctx

    async def get_page(
        self,
        session_id: str,
        vendor: str,
        *,
        record_video_dir: Optional[str] = None,
        viewport_width: int = 1366,
        viewport_height: int = 900,
    ) -> Page:
        """Get or create the dedicated Page for a supplier adapter within a session."""
        key = (session_id, vendor)
        async with self._lock:
            page = self._pages.get(key)
            if page is not None and not page.is_closed():
                try:
                    # Fast ping to ensure target is not crashed
                    await page.evaluate("1")
                    return page
                except Exception as e:
                    log.warning("Existing page for %s crashed or unresponsive: %s; recreating...", key, e)
                    try:
                        await page.close()
                    except Exception:
                        pass
                    self._pages.pop(key, None)

            # Unlock temporarily to call get_context without deadlock
            # Note: _lock is re-entrant or handled by using inner logic
            browser = await self._ensure_browser()
            ctx = self._contexts.get(key)
            if ctx is not None:
                try:
                    if not (ctx.browser and ctx.browser.is_connected()):
                        self._contexts.pop(key, None)
                        ctx = None
                except Exception:
                    self._contexts.pop(key, None)
                    ctx = None

            if ctx is None:
                ctx_opts = {
                    "viewport": {"width": viewport_width, "height": viewport_height},
                    "user_agent": UA,
                    "locale": "en-IN",
                }
                if record_video_dir:
                    Path(record_video_dir).mkdir(parents=True, exist_ok=True)
                    ctx_opts["record_video_dir"] = record_video_dir
                    ctx_opts["record_video_size"] = {"width": 1280, "height": 720}

                ctx = await browser.new_context(**ctx_opts)
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
                self._contexts[key] = ctx

            page = await ctx.new_page()
            self._pages[key] = page
            log.info("Created dedicated Page for session '%s', vendor '%s'", session_id, vendor)
            return page

    async def close_adapter(self, session_id: str, vendor: str) -> None:
        """Close page and context for a specific supplier adapter."""
        key = (session_id, vendor)
        async with self._lock:
            page = self._pages.pop(key, None)
            if page:
                try:
                    if not page.is_closed():
                        await page.close()
                except Exception as e:
                    log.warning("Error closing page for %s: %s", key, e)

            ctx = self._contexts.pop(key, None)
            if ctx:
                try:
                    await ctx.close()
                except Exception as e:
                    log.warning("Error closing context for %s: %s", key, e)

    async def close_session(self, session_id: str) -> None:
        """Close all contexts and pages associated with a customer session."""
        async with self._lock:
            keys_to_close = [k for k in self._contexts.keys() if k[0] == session_id]
            for key in keys_to_close:
                page = self._pages.pop(key, None)
                if page:
                    try:
                        if not page.is_closed():
                            await page.close()
                    except Exception as e:
                        log.warning("Error closing page for %s: %s", key, e)

                ctx = self._contexts.pop(key, None)
                if ctx:
                    try:
                        await ctx.close()
                    except Exception as e:
                        log.warning("Error closing context for %s: %s", key, e)
            log.info("Closed all browser contexts for session '%s'", session_id)

    async def shutdown(self) -> None:
        """Gracefully shut down all contexts, pages, Chromium, and Playwright."""
        async with self._lock:
            log.info("Shutting down BrowserManager and terminating Chromium...")
            # 1. Close all open pages
            for key, page in list(self._pages.items()):
                try:
                    if not page.is_closed():
                        await page.close()
                except Exception:
                    pass
            self._pages.clear()

            # 2. Close all contexts
            for key, ctx in list(self._contexts.items()):
                try:
                    await ctx.close()
                except Exception:
                    pass
            self._contexts.clear()

            # 3. Close browser
            if self._browser:
                try:
                    await self._browser.close()
                except Exception as e:
                    log.warning("Error closing Chromium: %s", e)
                self._browser = None

            # 4. Stop Playwright
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception as e:
                    log.warning("Error stopping Playwright: %s", e)
                self._pw = None

            self._initialized = False
            log.info("BrowserManager shutdown complete.")


def get_browser_manager() -> BrowserManager:
    """Return the global BrowserManager singleton."""
    return BrowserManager.get_instance()
