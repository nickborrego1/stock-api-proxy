from flask import Flask, request, jsonify
import json, re, unicodedata, yfinance as yf, pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from flask_cors import CORS
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

app = Flask(__name__)
CORS(app)

# ────────────────────────── Configuration ──────────────────────────
CACHE_FILE   = Path("franking_cache.json")
SECRET_TOKEN = "mySecret123"          # ← change this to your secret
BROWSER_ARGS = ["--no-sandbox"]       # required on Render free tier
NET_TIMEOUT  = 30_000                 # ms for networkidle & selectors
# ────────────────────────────────────────────────────────────────────

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def clean_num(txt: str) -> float:
    s = unicodedata.normalize("NFKD", txt)
    s = re.sub(r"[^\d.]", "", s) or "0"
    return float(s)

def scrape_investsmart(symbol_base: str) -> float | None:
    """
    Headless Chromium scrape via Playwright.
    Returns weighted franking % over the last 365 days, or None on failure.
    """
    url = f"https://www.investsmart.com.au/shares/asx-{symbol_base.lower()}/dividends"
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frank = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(args=BROWSER_ARGS)
        page    = browser.new_page()
        try:
            page.goto(url, timeout=NET_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=NET_TIMEOUT)
        except PlaywrightTimeout:
            browser.close()
            print("Timeout during page load/networkidle")
            return None

        # Dismiss cookie banner if present
        try:
            page.locator("button:has-text('Accept')").click(timeout=2_000)
        except Exception:
            pass

        # Wait for the dividends table rows
        try:
            page.wait_for_selector("table tbody tr", timeout=NET_TIMEOUT)
        except PlaywrightTimeout:
            print("Timeout waiting for table rows")

        rows = page.query_selector_all("table tbody tr")
        print(f"Scraped {len(rows)} rows for {symbol_base}")

        for tr in rows:
            tds = tr.query_selector_all("td")
            if len(tds) < 3:
                continue

            ex_text = tds[0].inner_text().strip()
            ex_date = pd.to_datetime(ex_text, dayfirst=True, errors="coerce").date()
            if not ex_date or ex_date < cutoff:
                continue

            amount = clean_num(tds[1].inner_text())
            fr_pct = clean_num(tds[2].inner_text())
            tot_div   += amount
            tot_frank += amount * (fr_pct / 100)

        if tot_div == 0:
            return None

        weighted = round((tot_frank / tot_div) * 100, 2)
        print(f"Weighted franking for {symbol_base}: {weighted}%")
        return weighted

def read_cache(base: str) -> float | None:
    if not CACHE_FILE.exists():
        return None
    data = json.loads(CACHE_FILE.read_text())
    entry = data.get(base.upper())
    return entry and entry.get("franking")

def update_cache(base: str, value: float):
    data = {}
    if CACHE_FILE.exists():
        data = json.loads(CACHE_FILE.read_text())
    data[base.upper()] = {"franking": value, "timestamp": datetime.utcnow().isoformat()}
    CACHE_FILE.write_text(json.dumps(data))

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # Fetch price and trailing-12-month dividends via yfinance
    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
        hist  = tkr.dividends
        dividend12 = None
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cutoff = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cutoff].sum())
    except Exception as e:
        return jsonify({"error": f"yfinance error: {e}"}), 500

    franking = read_cache(base) or 42
    return jsonify({
        "symbol": symbol,
        "price": price,
        "dividend12": dividend12,
        "franking": franking
    })

@app.route("/refresh_fran")
def refresh_fran():
    token = request.args.get("token", "")
    if token != SECRET_TOKEN:
        return jsonify({"error": "Bad token"}), 403

    base = request.args.get("symbol", "").strip().upper()
    if not base:
        return jsonify({"error": "Provide ?symbol=CODE"}), 400

    fw = scrape_investsmart(base)
    if fw is None:
        return jsonify({"error": "Scrape failed"}), 500

    update_cache(base, fw)
    return jsonify({"symbol": base, "franking": fw})

@app.route("/")
def root():
    return "Proxy live — use /stock and /refresh_fran"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
