"""Terminal recorder and video compositor for the Parts Sourcing Agent.

Renders terminal output in real time into an HTML terminal window recorded by
Playwright, and composites terminal and browser videos side-by-side using FFmpeg.
"""
from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path
import re
import subprocess
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

log = logging.getLogger(__name__)

TERMINAL_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 16px;
    background-color: #0b0f19;
    color: #e2e8f0;
    font-family: 'JetBrains Mono', 'Fira Code', 'DejaVu Sans Mono', Consolas, monospace;
    font-size: 13.5px;
    line-height: 1.5;
    height: 100vh;
    overflow: hidden;
  }
  .window {
    background: #111827;
    border-radius: 12px;
    border: 1px solid #1f2937;
    box-shadow: 0 20px 40px rgba(0,0,0,0.7);
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .titlebar {
    background: #1f2937;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    border-bottom: 1px solid #374151;
  }
  .dots {
    display: flex;
    gap: 8px;
    margin-right: 16px;
  }
  .dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
  }
  .dot-red { background: #ef4444; }
  .dot-yellow { background: #f59e0b; }
  .dot-green { background: #10b981; }
  .title {
    color: #9ca3af;
    font-size: 12.5px;
    font-weight: 600;
    letter-spacing: 0.5px;
  }
  .content {
    flex: 1;
    padding: 16px 20px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .prompt { color: #38bdf8; font-weight: bold; }
  .command { color: #f8fafc; font-weight: 600; }
  .agent { color: #06b6d4; font-weight: bold; }
  .mechanic { color: #22c55e; font-weight: bold; }
  .progress { color: #94a3b8; font-style: italic; }
  .info {
    color: #64748b;
    font-size: 11.5px;
    font-family: inherit;
    margin: 2px 0;
    white-space: pre-wrap;
  }
  .table {
    color: #f1f5f9;
    font-family: inherit;
    font-size: 11px;
    line-height: 1.35;
    background: #0d1322;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 12px;
    margin: 8px 0;
    overflow-x: auto;
    white-space: pre;
  }
  .table-head { color: #94a3b8; font-weight: bold; }
  .verified { color: #10b981; font-weight: bold; }
  .price { color: #facc15; font-weight: bold; }
  .cursor {
    display: inline-block;
    width: 8px;
    height: 16px;
    background: #38bdf8;
    margin-left: 2px;
    vertical-align: text-bottom;
    animation: blink 1s step-end infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }
</style>
</head>
<body>
<div class="window">
  <div class="titlebar">
    <div class="dots">
      <div class="dot dot-red"></div>
      <div class="dot dot-yellow"></div>
      <div class="dot dot-green"></div>
    </div>
    <div class="title">Terminal — Conversational Parts Sourcing Agent (Boodmo & AutoDukan)</div>
  </div>
  <div id="term" class="content"></div>
</div>
<script>
  const term = document.getElementById("term");
  function append(htmlSnippet) {
    const cur = document.getElementById("active-cursor");
    if (cur) cur.remove();
    term.insertAdjacentHTML("beforeend", htmlSnippet + '<span id="active-cursor" class="cursor"></span>');
    term.scrollTop = term.scrollHeight;
  }
</script>
</body>
</html>"""


class TerminalRecorder:
    def __init__(self, record_video_dir: Optional[str] = None, width: int = 960, height: int = 1080):
        self.record_video_dir = record_video_dir
        self.width = width
        self.height = height
        self._pw = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.video_path: Optional[str] = None

    async def start(self):
        if self.page:
            return
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx_opts = {
            "viewport": {"width": self.width, "height": self.height},
        }
        if self.record_video_dir:
            Path(self.record_video_dir).mkdir(parents=True, exist_ok=True)
            ctx_opts["record_video_dir"] = self.record_video_dir
            ctx_opts["record_video_size"] = {"width": self.width, "height": self.height}
        self.context = await self.browser.new_context(**ctx_opts)
        self.page = await self.context.new_page()
        await self.page.set_content(TERMINAL_HTML)
        await self.page.wait_for_timeout(300)

    async def append_html(self, snippet: str):
        if not self.page:
            return
        try:
            await self.page.evaluate("s => append(s)", snippet)
            # Give Playwright a short moment to render the frame
            await self.page.wait_for_timeout(150)
        except Exception as exc:
            log.debug("Terminal append failed: %s", exc)

    async def write_prompt(self, command: str):
        snippet = f'<span class="prompt">habilelabs@pulkit:~/Development/Convo_AI_Sourcing_Agent$ </span><span class="command">{html.escape(command)}</span><br><br>'
        await self.append_html(snippet)

    async def write_agent(self, text: str):
        snippet = f'<span class="agent">Agent:</span> {html.escape(text)}<br>'
        await self.append_html(snippet)

    async def write_mechanic(self, text: str):
        snippet = f'<span class="mechanic">Mechanic:</span> {html.escape(text)}<br>'
        await self.append_html(snippet)

    async def write_progress(self, text: str):
        snippet = f'<span class="progress">  · {html.escape(text)}</span><br>'
        await self.append_html(snippet)

    async def write_info(self, text: str):
        snippet = f'<div class="info">{html.escape(text)}</div>'
        await self.append_html(snippet)

    async def write_table_text(self, text: str):
        escaped = html.escape(text)
        # Highlight verified, prices, and headers
        escaped = re.sub(r"\b(verified)\b", r'<span class="verified">\1</span>', escaped)
        escaped = re.sub(r"(₹[\d,]+(?:\.\d{2})?)", r'<span class="price">\1</span>', escaped)
        snippet = f'<div class="table">{escaped}</div>'
        await self.append_html(snippet)

    async def close(self) -> Optional[str]:
        if self.page:
            # Let final frame linger so the viewer can read the final table and summary
            try:
                await self.page.wait_for_timeout(4000)
                if self.page.video:
                    self.video_path = await self.page.video.path()
            except Exception:
                pass
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()
        self.page = None
        return self.video_path


def compose_3in1_video(
    terminal_video: str,
    autodukan_video: str,
    boodmo_video: str,
    output_mp4: str,
) -> bool:
    """Create a unified 1920x1080 master demonstration video:

    - Left (960x1080): Live Terminal showing questions, answers, and comparison table.
    - Top-Right (960x540): AutoDukan website automation.
    - Bottom-Right (960x540): Boodmo website automation.
    """
    filter_complex = (
        # 1. Scale and pad terminal to left half (960x1080)
        "[0:v]scale=960:1080:force_original_aspect_ratio=decrease,pad=960:1080:(ow-iw)/2:(oh-ih)/2:color=#0b0f19[term];"
        # 2. Scale AutoDukan to top right (960x540)
        "[1:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:color=#111827[dukan];"
        # 3. Scale Boodmo to bottom right (960x540)
        "[2:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:color=#111827[boodmo];"
        # 4. Stack AutoDukan and Boodmo vertically into right column (960x1080)
        "[dukan][boodmo]vstack[right];"
        # 5. Join Terminal and Right Column horizontally (1920x1080)
        "[term][right]hstack[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", terminal_video,
        "-i", autodukan_video,
        "-i", boodmo_video,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        output_mp4,
    ]

    log.info("Compositing master 3-in-1 video: %s", output_mp4)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        log.error("FFmpeg composition failed: %s", res.stderr[-800:])
        return False
    return True


def compose_split_video(
    terminal_video: str,
    browser_video: str,
    output_mp4: str,
    browser_label: str = "Browser Automation",
) -> bool:
    """Create a side-by-side 1920x1080 video: Left Terminal, Right Browser."""
    filter_complex = (
        "[0:v]scale=960:1080:force_original_aspect_ratio=decrease,pad=960:1080:(ow-iw)/2:(oh-ih)/2:color=#0b0f19[term];"
        "[1:v]scale=960:1080:force_original_aspect_ratio=decrease,pad=960:1080:(ow-iw)/2:(oh-ih)/2:color=#111827[browser];"
        "[term][browser]hstack[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", terminal_video,
        "-i", browser_video,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        output_mp4,
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0
