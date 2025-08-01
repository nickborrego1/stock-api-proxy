from flask import Flask, request, jsonify
import requests, yfinance as yf, pandas as pd, re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115 Safari/537.36"
    ),
    "Referer": "https://www.asx.com.au/",
    "Accept": "text/html,application/xhtml+xml",
}

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def scrape_asx_franing(code: str):
    """Return list of (exDate, amount, franking%) for last 365d."""
    url = (
        "https://www.asx.com.au/markets/trade-our-cash-market/"
        "dividend-search?asxCode=" + code
    )
    for attempt in range(2):  # try twice with different headers
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table tbody tr")
        print(f"Attempt {attempt+1}: scraped {len(rows)} rows for {code}")
        if rows:
            break
        HEADERS["User-Agent"] += " retry"
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    out = []
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        try:
            amt   = float(tds[3].get_text(strip=True).replace("$", ""))
            frank = float(tds[4].get_text(strip=True).replace("%", ""))
            exdt  = datetime.strptime(tds[5].get_text(strip=True), "%d %b %Y").date()
            if exdt >= cutoff:
                out.append((exdt, amt, frank))
        except Exception:
            continue
    return out

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400
    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # price via yfinance
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {e}"}), 500

    # dividend last 365d via yfinance
    dividend12 = None
    try:
        hist = yf.Ticker(symbol).dividends
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cut = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cut].sum())
    except Exception as e:
        print("Dividend error:", e)

    # weighted franking via scrape
    franking = 42
    try:
        rows = scrape_asx_franing(base)
        tot_div = tot_frank = 0
        for exdt, amt, fr in rows:
            tot_div   += amt
            tot_frank += amt * (fr / 100)
        if tot_div:
            franking = round((tot_frank / tot_div) * 100, 2)
    except Exception as e:
        print("Franking scrape error:", e)

    return jsonify(
        {"symbol": symbol, "price": price, "dividend12": dividend12, "franking": franking}
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
