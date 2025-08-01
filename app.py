from flask import Flask, request, jsonify
import requests, yfinance as yf, pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from flask_cors import CORS

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
    """'vhy' → 'VHY.AX'   'vhy.ax' stays 'VHY.AX'."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def scrape_investsmart(code: str):
    """
    Returns (ex_date, amount, franking%) list for the last 12 months
    by scraping InvestSMART's dividend table.
    """
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    r   = requests.get(url, headers=UA, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("table tbody tr")
    print(f"InvestSMART rows for {code}: {len(rows)}")

    data  = []
    cutoff = datetime.utcnow().date() - timedelta(days=365)

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        raw_date = tds[0].get_text(strip=True)
        raw_amt  = tds[1].get_text(strip=True).replace("$", "").replace(",", "")
        raw_fr   = tds[2].get_text(strip=True).replace("%", "").strip()

        # dash → 0
        if raw_amt in ("", "-"):
            raw_amt = "0"
        if raw_fr in ("", "-"):
            raw_fr = "0"

        # Parse date (InvestSMART uses '1 Jul 2025' or '01 Jul 2025')
        for fmt in ("%d %b %Y", "%-d %b %Y"):
            try:
                ex_date = datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                continue
        else:
            continue  # skip if date parse fails

        try:
            amount = float(raw_amt)
            frank  = float(raw_fr)
        except ValueError:
            continue

        if ex_date >= cutoff:
            data.append((ex_date, amount, frank))

    return data

@app.route("/")
def root():
    return "Stock API Proxy (yfinance + InvestSMART) running."

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]   # e.g. VHY

    # ---- price ----
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {e}"}), 500

    # ---- trailing-12-month dividend ----
    dividend12 = None
    try:
        hist = yf.Ticker(symbol).dividends
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cutoff = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cutoff].sum())
    except Exception as e:
        print("Dividend error:", e)

    # ---- weighted franking % (InvestSMART) ----
    franking = 42  # fallback default
    try:
        rows = scrape_investsmart(base)
        tot_div = tot_frank = 0
        for ex, amt, fr in rows:
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
