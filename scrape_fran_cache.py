# scrape_fran_cache.py

import json
import re
import unicodedata
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

# ── Configuration ────────────────────────────────────────────────
CACHE       = Path("franking_cache.json")
ASX_CODES   = ["VHY"]    # ← add any other ASX codes here
USER_AGENT  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
# ────────────────────────────────────────────────────────────────

def clean_num(txt: str) -> float:
    """Strip currency/percent and return float."""
    s = unicodedata.normalize("NFKD", txt)
    s = re.sub(r"[^\d.]", "", s) or "0"
    return float(s)

async def scrape_one(page, code: str) -> float | None:
    """
    Scrape InvestSMART dividends page for one code.
    Returns weighted franking % over last 365 days, or None.
    """
    url    = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frank = 0.0

    # Navigate
    await page.goto(url, timeout=30000)
    await page.wait_for_load_state("networkidle", timeout=30000)

    # Dismiss cookie banner if present
    try:
        await page.locator("button:has-text('Accept')").click(timeout=2000)
    except:
        pass

    rows = await page.query_selector_all("table tbody tr")
    for tr in rows:
        tds = await tr.query_selector_all("td")
        if len(tds) < 6:
            continue

        raw_date = (await tds[5].inner_text()).strip()
        try:
            ex = datetime.strptime(raw_date, "%d %b %Y").date()
        except:
            continue
        if ex < cutoff:
            continue

        amt   = clean_num(await tds[3].inner_text())
        frank = clean_num(await tds[4].inner_text())
        tot_div   += amt
        tot_frank += amt * (frank/100)

    if tot_div <= 0:
        return None
    return round((tot_frank / tot_div) * 100, 2)

async def main():
    data = {}
    if CACHE.exists():
        data = json.loads(CACHE.read_text())

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page    = await browser.new_page()
        for code in ASX_CODES:
            print(f"→ Scraping {code}…")
            fran = await scrape_one(page, code)
            if fran is None:
                print(f"‼️  No franking data scraped for {code}")
            else:
                print(f"✅  {code}: {fran}%")
                data[code] = {
                    "franking":  fran,
                    "timestamp": datetime.utcnow().isoformat()
                }
        await browser.close()

    CACHE.write_text(json.dumps(data, indent=2))
    print("✅ Cache updated.")

if __name__ == "__main__":
    asyncio.run(main())
