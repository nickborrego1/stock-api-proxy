from flask import Flask, request, jsonify
import requests, yfinance as yf, pandas as pd, re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from flask_cors import CORS

app = Flask(__name__)               #  ← must be top-level
CORS(app)

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115 Safari/537.36"
    ),
    "Referer": "https://www.investsmart.com.au/",
}

# ---------- helpers ----------
def normalise(raw: str) -> str:
    """'vhy' → 'VHY.AX'   'vhy.ax' stays 'VHY.AX'."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def scrape_investsmart(code: str):
    """
    Return list of (ex_date, amount, franking%) for the last 12 months.
    """
    url  = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    html = requests.get(url, headers=UA, timeout=15).text
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tbody tr")
    print(f"InvestSMART rows for {code}: {len(rows)}")

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    kept   = []

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 3:            # need at least Ex-Date, Amount, Franking
            continue

        raw_date = tds[0].get_text(strip=True)
        raw_amt  = re.sub(r"[^0-9.]", "", tds[1].get_text(strip=True)) or "0"
        raw_fr   = re.sub(r"[^0-9.]", "", tds[2].get_text(strip=True)) or "0"

        try:
            ex_date = pd.to_datetime(raw_date, dayfirst=True).date()
            amount  = float(raw_amt)
            frank   = float(raw_fr)
        except Exception:
            continue

        if ex_date >= cutoff:
            kept.append((ex_date, amount, frank))

    print(f"Kept {len(kept)} rows for {code} within 12 months")
    return kept

# ---------- route ----------
@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    symbol = normalise(raw)          # e.g. VHY.AX
    base   = symbol.split(".")[0]    # VHY

    # ---- price via yfinance ----
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {e}"}), 500

    # ---- trailing-12-month dividend via yfinance ----
    dividend12 = None
    try:
        hist = yf.Ticker(symbol).dividends
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cut = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cut].sum())
    except Exception as e:
        print("Dividend error:", e)

    # ---- weighted franking via InvestSMART ----
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
def root():
    return "Proxy live"

# ---------- main ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
