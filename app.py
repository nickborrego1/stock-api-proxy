from flask import Flask, request, jsonify
import requests, yfinance as yf, pandas as pd
from bs4 import BeautifulSoup
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ---------- Helpers ----------

def normalise(raw: str) -> str:
    """Turn VHY or vhy.ax → VHY.AX"""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def fetch_franking(symbol_no_ax: str):
    """
    Scrape ASX dividend page, return list of tuples:
    (ex_date, dividend_amount, franking_percent)
    """
    url = (
        "https://www.asx.com.au/markets/trade-our-cash-market/dividend-search"
        f"?asxCode={symbol_no_ax}"
    )
    headers = {
        # Cloudflare/ASX sometimes blocks default python requests UA
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115 Safari/537.36"
        ),
        "Referer": "https://www.asx.com.au/",
    }
    r = requests.get(url, timeout=15, headers=headers)
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("table tbody tr")
    print(f"Scraped {len(rows)} dividend rows for {symbol_no_ax}")

    data = []
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        try:
            amount = float(tds[3].get_text(strip=True).replace("$", ""))
            frank  = float(tds[4].get_text(strip=True).replace("%", ""))
            ex_dt  = pd.to_datetime(tds[5].get_text(strip=True), dayfirst=True)
            data.append((ex_dt, amount, frank))
        except Exception:
            continue
    return data

# ---------- Routes ----------

@app.route("/")
def idx():
    return "Stock API Proxy (yfinance + ASX scrape) running."

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]  # e.g. VHY

    # --- Price via yfinance ---
    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {e}"}), 500

    # --- Trailing-12-month dividends via yfinance ---
    dividend12 = None
    try:
        hist = tkr.dividends
        if not hist.empty:
            hist.index = pd.to_datetime(hist.index)
            if hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)
            cutoff = pd.Timestamp.utcnow().tz_localize(_
