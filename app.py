from flask import Flask, request, jsonify
import requests, yfinance as yf, pandas as pd, re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dateutil import parser as dtparse           # ← NEW
from flask_cors import CORS
import unicodedata

app = Flask(__name__)
CORS(app)

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115 Safari/537.36"
    ),
    "Referer": "https://www.investsmart.com.au/",
}

def normalise(raw: str) -> str:
    return raw.strip().upper() if "." in raw else f"{raw.strip().upper()}.AX"

def clean_num(text: str) -> str:
    """Remove $ , NBSP, and any unicode space so float() works."""
    txt = unicodedata.normalize("NFKD", text)
    txt = re.sub(r"[^\d.\-]", "", txt)      # keep digits, dot, minus
    return txt or "0"

def scrape_investsmart(code: str):
    url  = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    html = requests.get(url, headers=UA, timeout=15).text
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tbody tr")
    print(f"InvestSMART rows for {code}: {len(rows)}")

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    kept   = []

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        try:
            ex_date = dtparse.parse(tds[0].get_text(strip=True), dayfirst=True).date()
        except Exception:
            continue

        raw_amt = clean_num(tds[1].get_text(strip=True))
        raw_fr  = clean_num(tds[2].get_text(strip=True))

        try:
            amount = float(raw_amt)
            frank  = float(raw_fr)
        except ValueError:
            continue

        if ex_date >= cutoff:
            kept.append((ex_date, amount, frank))

    print(f"Kept {len(kept)} rows for {code} within 12 months")
    return kept

@app.route("/stock")
def stock():
    raw = request.args.get("symbol","")
    if not raw:
        return jsonify({"error":"No symbol provided"}),400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error":f"Price fetch failed: {e}"}),500

    dividend12 = None
    try:
        hist = yf.Ticker(symbol).dividends
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cut = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cut].sum())
    except Exception as e:
        print("Dividend error:", e)

    franking = 42
    try:
        rows = scrape_investsmart(base)
        tot_div = tot_frank = 0
        for _, amt, fr in rows:
            tot_div   += amt
            tot_frank += amt * (fr / 100)
        if tot_div:
            franking = round((tot_frank / tot_div) * 100, 2)
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
def root(): return "Proxy live"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
