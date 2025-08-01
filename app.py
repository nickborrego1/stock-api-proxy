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
    )
}

# ---------- helpers ----------
def normalise(raw: str) -> str:
    """'vhy' → 'VHY.AX'   'vhy.ax' stays 'VHY.AX'."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def scrape_investsmart(code: str):
    """
    Scrape InvestSMART dividend table.
    Returns list of (ex_date, amount, franking_pct).
    """
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    r   = requests.get(url, headers=UA, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")

    rows  = soup.select("table tbody tr")
    print(f"InvestSMART rows for {code}: {len(rows)}")
    data  = []
    date_re = "%d %b %Y"

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 5:          # expect: Ex-Date | Amount | Franking | ...
            continue
        ex  = tds[0].get_text(strip=True)
        amt = tds[1].get_text(strip=True).replace("$", "")
        fr  = tds[2].get_text(strip=True).replace("%", "")
        try:
            ex_date = datetime.strptime(ex, date_re).date()
            amount  = float(amt)
            frank   = float(fr)
            data.append((ex_date, amount, frank))
        except Exception:
            continue
    return data

# ---------- route ----------
@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    symbol = normalise(raw)          # e.g. VHY.AX
    base   = symbol.split(".")[0]    # VHY

    # price via yfinance
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {e}"}), 500

    # trailing-12-month dividend via yfinance
    dividend12 = None
    try:
        hist = yf.Ticker(symbol).dividends
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cut = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cut].sum())
    except Exception as e:
        print("Dividend error:", e)

    # weighted franking via InvestSMART scrape
    franking = 42
    try:
        rows = scrape_investsmart(base)
        cut  = datetime.utcnow().date() - timedelta(days=365)
        tot_div = tot_frank = 0
        for ex, amt, fr in rows:
            if ex >= cut:
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

# ---------- main ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
