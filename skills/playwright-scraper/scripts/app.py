"""
Playwright scraper skill — loads a page in a headless Chromium browser
and returns the visible text content. Handles JS-rendered pages and
sites with basic anti-bot protection.
"""

import os
import sys
import json

from playwright.sync_api import sync_playwright


MAX_CHARS = 4000


def scrape(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)

        text = page.inner_text("body")
        browser.close()

    # Collapse whitespace and truncate
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    content = "\n".join(lines)
    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS] + "\n[truncated]"
    return content


def main():
    raw = os.environ.get("SKILL_INPUT", "") or sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"url": raw.strip()}

    url = data.get("url", "").strip()
    if not url:
        print("No URL provided")
        sys.exit(1)

    if not url.startswith(("http://", "https://")):
        print(f"Invalid URL: {url}")
        sys.exit(1)

    print(scrape(url))


if __name__ == "__main__":
    main()
