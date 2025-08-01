from flask import Flask, request, jsonify
import json, re, unicodedata, yfinance as yf, pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from flask_cors import CORS
from playwright.sync_api import sync_playwright

app = Flask(__name__)
CORS(app)

CACHE_FILE = Path("franking_cache.json")
SECRET_TOKEN = "CHANGE_ME"          # set a simple secret so public users can’t hit /refresh

# ---------- helpers ----------
def normalise(raw: str) -> str:
    return raw.strip().upper() if "." in raw else f"{raw.strip().upper()}.AX"

def clean_num(txt: str) -> float:
    s = unicodedata.normalize("NFKD", txt)
    s = re.sub(r"[^\d.]", "", s) or "0"
    return float(s)

def scrape_investsmart(symbol_base: str):
    url = f"https://www.investsmart.com.au/shares/asx-{symbol_base.lower()}/dividends"
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page    = browser.new_page()
        page.goto(url, timeout=15000)
        page.wait_for_selector("table tbody tr", timeout=15000)
        rows = page.query_selector_all("table tbody tr")
        cutoff = datetime.utcnow().date() - timedelta(days=365)
        tot_div = tot_frank = 0
        for tr in rows:
            cells = tr.query_selector_all("td")
            if len(cells) < 3:
                continue
            ex_date = pd.to_datetime(cells[0].inner_text().strip(), dayfirst=True, errors="coerce").date()
            amount  = clean_num(cells[1].inner_text().strip())
            fr_pct  = clean_num(cells[2].inner_text().strip())
            if ex_date and ex_date >= cutoff:
                tot_div   += amount
                tot_frank += amount * (fr_pct / 100)
        browser.close()
    return round((tot_frank / tot_div) * 100, 2) if tot_div else None

def read_cache(symbol_base):
    if not CACHE_FILE.exists():
        return None
    data = json.loads(CACHE_FILE.read_text())
    entry = data.get(symbol_base.upper())
    return entry["franking"] if entry else None

def update_cache(symbol_base, value):
    data = {}
    if CACHE_FILE.exists():
        data = json.loads(CACHE_FILE.read_text())
    data[symbol_base.upper()] = {"franking": value, "timestamp": datetime.utcnow().isoformat()}
    CACHE_FILE.write_text(json.dumps(data))

# ---------- routes ----------
@app.route("/stock")
def stock():
    sym_raw = request.args.get("symbol", "").strip()
    if not sym_raw:
        return jsonify({"error": "No symbol provided"}), 400
    symbol = normalise(sym_raw)
    base   = symbol.split(".")[0]

    # price & dividend via yfinance
    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
        hist  = tkr.dividends
        dividend12 = None
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cut = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cut].sum())
    except Exception as e:
        return jsonify({"error": f"yfinance error: {e}"}), 500

    franking = read_cache(base) or 42   # default if cache missing
    return jsonify({"symbol": symbol, "price": price,
                    "dividend12": dividend12, "franking": franking})

@app.route("/refresh_fran")
def refresh_fran():
    token  = request.args.get("token", "")
    symbol = request.args.get("symbol", "").strip().upper()
    if token != SECRET_TOKEN:
        return jsonify({"error": "Bad token"}), 403
    if not symbol:
        return jsonify({"error": "Provide ?symbol=CODE"}), 400
    try:
        value = scrape_investsmart(symbol)
        if value is None:
            return jsonify({"error": "Could not scrape franking"}), 500
        update_cache(symbol, value)
        return jsonify({"symbol": symbol, "franking": value})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def root():
    return "Proxy live. Hit /stock or /refresh_fran"

# ---------- main ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
