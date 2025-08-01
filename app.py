from flask import Flask, request, jsonify
import json, re, unicodedata, requests, yfinance as yf, pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from flask_cors import CORS
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)

# ──────────────────────────── Config ────────────────────────────
CACHE_FILE   = Path("franking_cache.json")
SECRET_TOKEN = "mySecret123"      # ← change to your secret
ASX_URL      = "https://www.asx.com.au/asx/v2/markets/dividends.do"
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
# ─────────────────────────────────────────────────────────────────

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def clean_num(txt: str) -> float:
    # strip non-numeric (including $ , % )
    t = unicodedata.normalize("NFKD", txt)
    t = re.sub(r"[^\d\.]", "", t)
    return float(t or 0)

def scrape_asx_fran(symbol_base: str) -> float | None:
    """
    Scrape the ASX static dividends search page:
    https://www.asx.com.au/asx/v2/markets/dividends.do?by=recent&q=SYMBOL
    Returns the weighted franking % over the last 365 days or None.
    """
    params = {"by": "recent", "q": symbol_base}
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(ASX_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print("ASX page fetch error:", e)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("table tbody tr")
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frank = 0.0

    for tr in rows:
        tds = tr.find_all("td")
        # Expected columns: 0:Sector,1:MarketCap,2:Type,3:Dividend,4:Franking%,5:ExDate,...
        if len(tds) < 6:
            continue
        # parse ex-dividend date
        try:
            ex = pd.to_datetime(tds[5].get_text(strip=True), dayfirst=True).date()
        except Exception:
            continue
        if ex < cutoff:
            continue

        amt   = clean_num(tds[3].get_text(strip=True))
        frank = clean_num(tds[4].get_text(strip=True))
        tot_div   += amt
        tot_frank += amt * (frank / 100)

    if tot_div <= 0:
        return None

    return round((tot_frank / tot_div) * 100, 2)

def read_cache(base: str) -> float | None:
    if not CACHE_FILE.exists():
        return None
    data = json.loads(CACHE_FILE.read_text())
    entry = data.get(base.upper())
    return entry.get("franking") if entry else None

def update_cache(base: str, value: float):
    data = {}
    if CACHE_FILE.exists():
        data = json.loads(CACHE_FILE.read_text())
    data[base.upper()] = {
        "franking":  value,
        "timestamp": datetime.utcnow().isoformat()
    }
    CACHE_FILE.write_text(json.dumps(data))

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # 1) price + trailing 12m dividend via yfinance
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

    # 2) franking from cache (or default)
    franking = read_cache(base) or 42.0

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

    fran = scrape_asx_fran(base)
    if fran is None:
        return jsonify({"error": "Scrape failed"}), 500

    update_cache(base, fran)
    return jsonify({"symbol": base, "franking": fran})

@app.route("/")
def root():
    return "Proxy live — use /stock and /refresh_fran"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
