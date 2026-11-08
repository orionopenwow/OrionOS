"""Headless Chrome DevTools Protocol (CDP) & Browser Automation Engine.
Enables Orion to evaluate JavaScript, click elements, fill forms, and scrape
deep contextual DOM data autonomously.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

log = logging.getLogger("orion.cdp")


class CDPBrowserBridge:
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self.is_ready = False

    async def initialize(self) -> bool:
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            )
            self.is_ready = True
            log.info("Headless CDP Browser Bridge initialized")
            return True
        except Exception as exc:
            log.warning("CDP Browser Bridge initialization failed (run playwright install if needed): %s", exc)
            self.is_ready = False
            return False

    async def scrape_page(self, url: str) -> dict[str, Any]:
        """Navigate to URL, wait for DOM idle, and extract sanitized content."""
        if not self.is_ready and not await self.initialize():
            return {"error": "CDP engine unavailable", "url": url}

        page = None
        try:
            page = await self._context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            title = await page.title()
            text = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 8000) : ''")
            __import__("injection_firewall").note(text, f"the web page {url}"[:120])  # Toolkit: Injection Firewall
            return {
                "url": url,
                "title": title,
                "text": text,
                "success": True,
            }
        except Exception as exc:
            log.warning("CDP scrape error on %s: %s", url, exc)
            return {"url": url, "error": str(exc), "success": False}
        finally:
            if page:
                await page.close()

    async def execute_action(self, url: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
        """Execute automated browser steps: click, type, wait, evaluate."""
        if not self.is_ready and not await self.initialize():
            return {"error": "CDP engine unavailable"}

        page = None
        try:
            page = await self._context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=25000)
            logs = []

            for step in actions:
                action_type = step.get("action")
                selector = step.get("selector")
                value = step.get("value")

                if action_type == "click" and selector:
                    await page.click(selector, timeout=5000)
                    logs.append(f"Clicked {selector}")
                elif action_type == "fill" and selector and value:
                    await page.fill(selector, str(value), timeout=5000)
                    logs.append(f"Filled {selector}")
                elif action_type == "wait":
                    await asyncio.sleep(float(value or 1.0))
                    logs.append(f"Waited {value}s")
                elif action_type == "eval" and value:
                    res = await page.evaluate(str(value))
                    logs.append(f"Evaluated JS: {res}")

            final_title = await page.title()
            final_text = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 4000) : ''")
            return {
                "success": True,
                "title": final_title,
                "logs": logs,
                "text": final_text,
            }
        except Exception as exc:
            log.warning("CDP execution failed: %s", exc)
            return {"success": False, "error": str(exc)}
        finally:
            if page:
                await page.close()

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self.is_ready = False
