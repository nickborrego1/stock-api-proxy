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

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def fetch_franking_asx_json(code: str):
    """
    Hit ASX's JSON endpoint for dividend-search.
    Returns list of (ex_date, amount, franking%) for the last 365 days.
    """
    url = "https://www.asx.com.au/api/markets/trade-our-cash-market/dividend-search"
    params = {"asxCodes": code}
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT
    }
    try:
        rsp = requests.get(url, params=params, headers=headers, timeout=10)
        rsp.raise_for_status()
        data = rsp.json().get("rows", [])
    except Exception:
        return []

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    rows = []
    for rec in data:
        try:
            ex = datetime.strptime(rec["exDate"], "%d %b %Y").date()
            if ex < cutoff:
                continue
            amt = float(rec.get("dividendAmount", 0))
            frk = float(rec.get("frankingPercentage", 0))
            rows.append((ex, amt, frk))
        except Exception:
            continue
    return rows

def fetch_franking_investsmart(code: str):
    """
    Fallback: scrape InvestSMART page HTML if ASX JSON fails.
    """
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
    except Exception:
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "html.parser")
    tbl = soup.find("table")
    if not tbl or not tbl.tbody:
        return []

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    rows = []
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
            rows.append((ex, amt, frk))
        except Exception:
            continue
    return rows

@app.route("/")
def idx():
    return "Stock API Proxy running.", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base = symbol.split(".")[0]

    # 1) Price + trailing-12m dividends
    try:
        tkr = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
        hist = tkr.dividends
        dividend12 = None
        if not hist.empty:
            idx = pd.to_datetime(hist.index).tz_localize(None)
            cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=365)
            dividend12 = float(hist[idx >= cutoff].sum())
    except Exception as e:
        return jsonify(error=f"yfinance error: {e}"), 500

    # 2) Weighted franking
    rows = fetch_franking_asx_json(base)
    if not rows:
        rows = fetch_franking_investsmart(base)

    franking = None
    if rows:
        tot_div = sum(a for _, a, _ in rows)
        tot_frk = sum(a * (f / 100.0) for _, a, f in rows)
        if tot_div > 0:
            franking = round((tot_frk / tot_div) * 100, 2)

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
