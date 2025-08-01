from flask import Flask, request, jsonify
import json, re, unicodedata, yfinance as yf, pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from flask_cors import CORS
from playwright.sync_api import sync_playwright

app = Flask(__name__)
CORS(app)

# ────────────────────────────── CONFIG ──────────────────────────────
CACHE_FILE   = Path("franking_cache.json")
SECRET_TOKEN = "mySecret123"          # <-- change if you like
BROWSER_ARGS = ["--no-sandbox"]       # required on Render free tier
NET_TIMEOUT  = 30_000                 # 30 s (ms) for slow first loads
# ────────────────────────────────────────────────────────────────────


# ---------- helpers ----------
def normalise(raw: str) -> str:
    """'vhy' → 'VHY.AX';  'vhy.ax' remains 'VHY.AX'."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def clean_num(txt: str) -> float:
    """Remove $, commas, NBSP, etc.  Return 0.0 on blanks."""
    cleaned = re.sub(r"[^\d.]", "", unicodedata.normalize("NFKD", txt)) or "0"
    return float(cleaned)

def scrape_investsmart(symbol_base: str) -> float | None:
    """
    Headless Chromium scrape via Playwright.
    Returns weighted franking % for last-365-days, or None on failure.
    """
    url = f"https://www.investsmart.com.au/shares/asx-{symbol_base.lower()}/dividends"
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frank = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(args=BROWSER_ARGS)
        page    = browser.new_page()
        try:
            page.goto(url, timeout=NET_TIMEOUT)
            # wait until no network requests for 0.5 s (React finished)
            page.wait_for_load_state("networkidle", timeout=NET_TIMEOUT)
        except Exception:
            browser.close()
            return None

        # dismiss cookie banner if present
        try:
            page.locator("button:has-text('Accept')").click(timeout=2_000)
        except Exception:
            pass

        rows = page.query_selector_all("table tbody tr")
        for tr in rows:
            tds = tr.query_selector_all("td")
            if len(tds) < 3:
                continue
            ex_date = pd.to_datetime(tds[0].inner_text().strip(),
                                     dayfirst=True, errors="coerce").date()
            if not ex_date or ex_date < cutoff:
                continue
            amount = clean_num(tds[1].inner_text())
            frank  = clean_num(tds[2].inner_text())
            tot_div   += amount
            tot_frank += amount * (frank / 100)

        browser.close()

    return round((tot_frank / tot_div) * 100, 2) if tot_div else None


# ---------- cache helpers ----------
def read_cache(base: str) -> float | None:
    if not CACHE_FILE.exists():
        return None
    entry = json.loads(CACHE_FILE.read_text()).get(base.upper())
    return entry.get("franking") if entry else None

def update_cache(base: str, value: float):
    data = {}
    if CACHE_FILE.exists():
        data = json.loads(CACHE_FILE.read_text())
    data[base.upper()] = {"franking": value,
                          "timestamp": datetime.utcnow().isoformat()}
    CACHE_FILE.write_text(json.dumps(data))


# ---------- routes ----------
@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400
    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # price + trailing 12-month dividend via yfinance
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

    franking = read_cache(base) or 42     # default if not cached
    return jsonify({"symbol": symbol, "price": price,
                    "dividend12": dividend12, "franking": franking})


@app.route("/refresh_fran")
def refresh_fran():
    if request.args.get("token") != SECRET_TOKEN:
        return jsonify({"error": "Bad token"}), 403
    base = request.args.get("symbol", "").strip().upper()
    if not base:
        return jsonify({"error": "Provide ?symbol=CODE"}), 400

    value = scrape_investsmart(base)
    if value is None:
        return jsonify({"error": "Scrape failed"}), 500
    update_cache(base, value)
    return jsonify({"symbol": base, "franking": value})


@app.route("/")
def root():
    return "Proxy live — use /stock and /refresh_fran"


# ---------- main ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
