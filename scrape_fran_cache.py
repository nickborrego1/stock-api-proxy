"""
Run daily on Render Cron Job.
Scrapes InvestSMART with Playwright headless Chromium
and writes/updates franking_cache.json
"""
import json, re, unicodedata, asyncio
from pathlib import Path
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

CACHE = Path("franking_cache.json")
ASX_CODES = ["VHY", "BHP", "CBA", "WOW"]   # add any tickers you care about

def clean_num(txt: str) -> float:
    return float(re.sub(r"[^\d.]", "", unicodedata.normalize("NFKD", txt)) or "0")

async def scrape_code(page, code):
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    await page.goto(url, timeout=15000)
    await page.wait_for_selector("table tbody tr", timeout=15000)
    rows = await page.query_selector_all("table tbody tr")
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frank = 0
    for tr in rows:
        tds = await tr.query_selector_all("td")
        if len(tds) < 3:
            continue
        ex   = await tds[0].inner_text()
        amt  = clean_num(await tds[1].inner_text())
        frank= clean_num(await tds[2].inner_text())
        try:
            ex_date = datetime.strptime(ex.strip(), "%d %b %Y").date()
        except ValueError:
            continue
        if ex_date >= cutoff:
            tot_div   += amt
            tot_frank += amt * (frank/100)
    w_frank = round((tot_frank / tot_div) * 100, 2) if tot_div else None
    return w_frank

async def main():
    data = {}
    if CACHE.exists():
        data = json.loads(CACHE.read_text())
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page    = await browser.new_page()
        for code in ASX_CODES:
            f = await scrape_code(page, code)
            if f is not None:
                data[code.upper()] = {"franking": f, "timestamp": datetime.utcnow().isoformat()}
                print(f"{code}: {f} %")
        await browser.close()
    CACHE.write_text(json.dumps(data))

if __name__ == "__main__":
    asyncio.run(main())
