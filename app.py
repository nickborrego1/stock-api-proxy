from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import yfinance as yf
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re

app = Flask(__name__)
CORS(app)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

def normalise(raw: str) -> str:
    """Turn 'VHY' or 'vhy.ax' into 'VHY.AX'."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def fetch_franking_asx(code: str) -> list[tuple[datetime.date,float,float]]:
    """
    Scrape InvestSMART dividends page for ASX:<code>.
    Returns list of (ex_date, amount, franking%) tuples.
    """
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table")
    if not table or not table.tbody:
        return []

    data = []
    for tr in table.tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        # Dividend amount
        amt_txt = tds[3].get_text(strip=True)
        frank_txt = tds[4].get_text(strip=True)
        date_txt = tds[5].get_text(strip=True)

        # Parse
        try:
            amt   = float(re.sub(r"[^\d.]", "", amt_txt))
            frank = float(re.sub(r"[^\d.]", "", frank_txt))
            ex_date = datetime.strptime(date_txt, "%d %b %Y").date()
        except Exception:
            continue

        data.append((ex_date, amt, frank))
    return data

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    # Normalize to VHY.AX
    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # 1) Fetch price + trailing 12-month dividends from Yahoo
    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {e}"}), 500

    div12 = None
    try:
        hist = tkr.dividends
        if not hist.empty:
            hist.index = pd.to_datetime(hist.index)
            cutoff = pd.Timestamp.utcnow() - pd.DateOffset(days=365)
            div12 = float(hist[hist.index >= cutoff].sum())
    except Exception:
        div12 = None

    # 2) Scrape weighted franking
    weighted_f = None
    try:
        rows = fetch_franking_asx(base)
        cutoff = datetime.utcnow().date() - timedelta(days=365)
        tot_div = tot_frank = 0.0

        for ex_date, amt, frank in rows:
            if ex_date >= cutoff:
                tot_div   += amt
                tot_frank += amt * (frank / 100.0)

        if tot_div > 0:
            weighted_f = round((tot_frank / tot_div) * 100, 2)
    except Exception:
        weighted_f = None

    return jsonify({
        "symbol":     symbol,
        "price":      price,
        "dividend12": div12,
        "franking":   weighted_f
    })

@app.route("/")
def index():
    return "Stock API Proxy running.", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
