from flask import Flask, request, jsonify
import yfinance as yf, pandas as pd, re, asyncio
from datetime import datetime, timedelta
from flask_cors import CORS
from playwright.sync_api import sync_playwright, TimeoutError as PlayTimeout

app = Flask(__name__)
CORS(app)

# ---------- helpers ----------
def normalise(raw: str) -> str:
    """Accept 'vhy' or 'vhy.ax' and return 'VHY.AX'."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def scrape_investsmart_playwright(code: str):
    """
    Use Playwright headless Chromium to fetch InvestSMART dividends
    and return list of (ex_date, amount, franking %) within 12 months.
    """
    url  = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    kept   = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page    = browser.new_page()
        try:
            page.goto(url, timeout=15000)
            page.wait_for_selector("table tbody tr", timeout=15000)
        except PlayTimeout:
            browser.close()
            return kept

        rows = page.query_selector_all("table tbody tr")
        for tr in rows:
            cells = tr.query_selector_all("td")
            if len(cells) < 3:
                continue
            raw_date = cells[0].inner_text().strip()
            raw_amt  = cells[1].inner_text().strip()
            raw_fr   = cells[2].inner_text().strip()

            # Clean numeric strings
            clean_amt = re.sub(r"[^\d.]", "", raw_amt) or "0"
            clean_fr  = re.sub(r"[^\d.]", "", raw_fr)  or "0"

            try:
                ex_date = pd.to_datetime(raw_date, dayfirst=True).date()
                amt_val = float(clean_amt)
                fr_val  = float(clean_fr)
            except Exception:
                continue

            if ex_date >= cutoff:
                kept.append((ex_date, amt_val, fr_val))

        browser.close()
    return kept

# ---------- route ----------
@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # ---- price via yfinance ----
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {e}"}), 500

    # ---- trailing-12-month dividend via yfinance ----
    dividend12 = None
    try:
        hist = yf.Ticker(symbol).dividends
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cut = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cut].sum())
    except Exception as e:
        print("Dividend error:", e)

    # ---- weighted franking via Playwright scrape ----
    franking = 42
    try:
        rows = scrape_investsmart_playwright(base)
        tot_div = tot_frank = 0
        for _, amt, fr in rows:
            tot_div   += amt
            tot_frank += amt * (fr / 100)
        if tot_div:
            franking = round((tot_frank / tot_div) * 100, 2)
        print(f"Scraped {len(rows)} rows for {base}; franking = {franking}%")
    except Exception as e:
        print("Franking scrape error:", e)

    return jsonify(
        {
            "symbol": symbol,
            "price": price,
            "dividend12": dividend12,
            "franking": franking,
        }
    )

@app.route("/")
def root():
    return "Proxy live (Playwright)"

# ---------- main ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
