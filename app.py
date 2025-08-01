from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, yfinance as yf, pandas as pd, re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

app = Flask(__name__)
CORS(app)

def normalise(raw: str) -> str:
    """vhy → VHY.AX   |   vhy.ax → VHY.AX"""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def parse_exdate(txt: str):
    """Handle '01-Oct-2024', '1 Oct 2024', or '01/10/2024'"""
    txt = txt.strip()
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None

def scrape_marketindex(code: str):
    """
    Returns (dividend12, weighted_fran_pct) or (None, None) on failure.
    """
    url = f"https://www.marketindex.com.au/asx/{code.lower()}"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print("MarketIndex request error:", e)
        return None, None

    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None, None

    # Identify the dividends table by headers
    div_table = None
    for tbl in tables:
        headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if {"ex-date", "amount", "franking"}.issubset(set(headers)):
            div_table = tbl
            break
    if div_table is None:
        return None, None

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_amt = 0.0
    tot_fran = 0.0
    for tr in div_table.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        ex_date = parse_exdate(tds[0].get_text())
        if not ex_date or ex_date < cutoff:
            continue
        try:
            amt = float(re.sub(r"[^\d.]", "", tds[2].get_text()))
        except ValueError:
            continue
        try:
            fran_pct = float(re.sub(r"[^\d.]", "", tds[3].get_text()))
        except ValueError:
            fran_pct = 0.0
        tot_amt += amt
        tot_fran += amt * (fran_pct / 100.0)

    if tot_amt == 0:
        return None, None
    weighted_fran = round((tot_fran / tot_amt) * 100, 2)
    return round(tot_amt, 6), weighted_fran

@app.route("/")
def home():
    return "Stock API Proxy (MarketIndex scrape)", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # 1 Price
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # 2 Dividends + franking from MarketIndex
    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
