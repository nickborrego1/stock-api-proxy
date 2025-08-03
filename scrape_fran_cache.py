#!/usr/bin/env python3
# scrape_fran_cache.py

import json
import re
import unicodedata
from pathlib import Path
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

# ─── Configuration ──────────────────────────────────────────────
CACHE     = Path("franking_cache.json")
ASX_CODES = ["VHY"]         # ← add any ASX codes you want cached
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
# ────────────────────────────────────────────────────────────────

def clean_num(txt: str) -> float:
    """Strip out non-numeric except dot, return float."""
    s = unicodedata.normalize("NFKD", txt)
    s = re.sub(r"[^\d.]", "", s) or "0"
    return float(s)

def fetch_franking_asx(code: str):
    """
    Scrape InvestSMART dividends page for ASX:<code>.
    Returns weighted franking % over last 365 days or None.
    """
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table")
    if not table or not table.tbody:
        print(f"‼️  No table found for {code}")
        return None

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frank = 0.0
    rows = table.tbody.find_all("tr")
    print(f"🔍 Found {len(rows)} rows for {code}")

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue

        date_txt  = tds[5].get_text(strip=True)
        try:
            ex_date = datetime.strptime(date_txt, "%d %b %Y").date()
        except ValueError:
            continue
        if ex_date < cutoff:
            continue

        amt_txt   = tds[3].get_text(strip=True)
        frank_txt = tds[4].get_text(strip=True)
        amt   = clean_num(amt_txt)
        frank = clean_num(frank_txt)
        tot_div   += amt
        tot_frank += amt * (frank / 100.0)

    if tot_div <= 0:
        print(f"‼️  No dividends in last 12m for {code}")
        return None

    weighted = round((tot_frank / tot_div) * 100, 2)
    print(f"✅  {code}: {weighted}% weighted franking")
    return weighted

def main():
    data = {}
    if CACHE.exists():
        data = json.loads(CACHE.read_text())

    for code in ASX_CODES:
        print(f"→ Scraping {code}…")
        try:
            fran = fetch_franking_asx(code)
        except Exception as e:
            print(f"⚠️ Error fetching {code}: {e}")
            fran = None

        if fran is None:
            print(f"‼️  Failed to fetch franking for {code}")
        else:
            data[code] = {
                "franking":  fran,
                "timestamp": datetime.utcnow().isoformat()
            }

    CACHE.write_text(json.dumps(data, indent=2))
    print("✅ Cache updated.")

if __name__ == "__main__":
    main()
