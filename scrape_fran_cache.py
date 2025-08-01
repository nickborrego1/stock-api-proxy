# scrape_fran_cache.py
import json, re, unicodedata, asyncio
from pathlib import Path
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

CACHE = Path("franking_cache.json")
ASX_CODES = ["VHY"]  # ← add any tickers you want cached

def clean_num(txt: str) -> float:
    s = unicodedata.normalize("NFKD", txt)
    s = re.sub(r"[^\d.]", "", s) or "0"
    return float(s)

async def scrape_one(page, code: str):
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    await page.goto(url, timeout=30000)
    await page.wait_for_load_state("networkidle", timeout=30000)
    # dismiss banner
    try:
        await page.locator("button:has-text('Accept')").click(timeout=2000)
    except:
        pass

    rows = await page.query_selector_all("table tbody tr")
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frank = 0.0
    for tr in rows:
        tds = await tr.query_selector_all("td")
        if len(tds) < 6:
            continue
        # parse date
        raw = (await tds[5].inner_text()).strip()
        try:
            ex = datetime.strptime(raw, "%d %b %Y").date()
        except:
            continue
        if ex < cutoff:
            continue
        amt   = clean_num(await tds[3].inner_text())
        frank = clean_num(await tds[4].inner_text())
        tot_div   += amt
        tot_frank += amt * (frank/100)

    return round((tot_frank/tot_div)*100, 2) if tot_div else None

async def main():
    data = {}
    if CACHE.exists():
        data = json.loads(CACHE.read_text())

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page    = await browser.new_page()
        for code in ASX_CODES:
            fran = await scrape_one(page, code)
            if fran is not None:
                data[code] = {"franking": fran, "timestamp": datetime.utcnow().isoformat()}
                print(f"{code}: {fran}%")
        await browser.close()

    CACHE.write_text(json.dumps(data))

if __name__ == "__main__":
    asyncio.run(main())
