from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, yfinance as yf, pandas as pd
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
ASX_JSON_URL = "https://www.asx.com.au/api/markets/trade-our-cash-market/dividend-search"

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def fetch_franking_asx_json(code: str):
    """Hit ASX’s JSON endpoint correctly with asxCode=CODE."""
    try:
        resp = requests.get(
            ASX_JSON_URL,
            params={"asxCode": code},           # ← singular
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=10
        )
        resp.raise_for_status()
        records = resp.json().get("rows", [])
    except Exception:
        return []

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    out = []
    for r in records:
        try:
            ex = datetime.strptime(r["exDate"], "%d %b %Y").date()
            if ex < cutoff:
                continue
            amt = float(r.get("dividendAmount", 0))
            frk = float(r.get("frankingPercentage", 0))
            out.append((ex, amt, frk))
        except Exception:
            continue
    return out

def fetch_franking_investsmart(code: str):
    """Fallback scrape if ASX JSON returns empty."""
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        r.raise_for_status()
    except Exception:
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "html.parser")
    tbl = soup.find("table")
    if not tbl or not tbl.tbody:
        return []

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    out = []
    for tr in tbl.tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        try:
            ex = datetime.strptime(tds[5].get_text(strip=True), "%d %b %Y").date()
            if ex < cutoff:
                continue
            amt = float(tds[3].get_text(strip=True).replace("$","").replace(",",""))
            frk = float(tds[4].get_text(strip=True).replace("%",""))
            out.append((ex, amt, frk))
        except:
            continue
    return out

@app.route("/")
def index():
    return "Stock API Proxy running — use /stock?symbol=CODE", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol","").strip()
    if not raw:
        return jsonify({"error":"No symbol provided"}), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # 1) Price
    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error":f"Price fetch failed: {e}"}), 500

    # 2) Trailing-12m dividends
    dividend12 = None
    try:
        hist = tkr.dividends
        if not hist.empty:
            # strip any tz info
            hist.index = pd.to_datetime(hist.index).tz_localize(None)
            cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=365)
            mask = hist.index >= cutoff
            dividend12 = float(hist.loc[mask].sum())
    except Exception:
        dividend12 = None

    # 3) Weighted franking
    rows = fetch_franking_asx_json(base)
    if not rows:
        rows = fetch_franking_investsmart(base)

    franking = None
    if rows:
        tot_div = sum(a for _,a,_ in rows)
        tot_frk = sum(a*(f/100) for _,a,f in rows)
        if tot_div > 0:
            franking = round((tot_frk / tot_div) * 100, 2)

    return jsonify({
        "symbol":     symbol,
        "price":      price,
        "dividend12": dividend12,
        "franking":   franking
    })

if __name__=="__main__":
    app.run(host="0.0.0.0", port=8080)
