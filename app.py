from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, yfinance as yf, pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
             "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

app = Flask(__name__)
CORS(app)

def normalise(raw: str) -> str:
    """Turn vhy or VHY.ax → VHY.AX"""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def fetch_franking_asx(code: str):
    """
    Scrape ASX’s own dividend-search page for ex-dates, amounts & frankings.
    Returns list of (ex_date: date, amount: float, franking%: float) for last 365d.
    """
    url = f"https://www.asx.com.au/markets/trade-our-cash-market/dividend-search?asxCodes={code}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # find the table whose headers include “Ex Date”, “Gross” and “Franking”
    tables = soup.find_all("table")
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    for tbl in tables:
        thead = tbl.find("thead")
        if not thead:
            continue
        headers = [th.get_text(strip=True).lower() for th in thead.find_all("th")]
        if "ex date" in headers and "gross" in headers and "franking" in headers:
            idx_ex = headers.index("ex date")
            idx_amt = headers.index("gross")
            idx_frk = headers.index("franking")
            rows = []
            for tr in tbl.find("tbody").find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) <= max(idx_ex, idx_amt, idx_frk):
                    continue
                # parse ex-date
                dtxt = tds[idx_ex].get_text(strip=True)
                try:
                    ex_date = datetime.strptime(dtxt, "%d %b %Y").date()
                except ValueError:
                    try:
                        ex_date = datetime.strptime(dtxt, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                if ex_date < cutoff:
                    continue
                # parse amount
                amt = float(tds[idx_amt].get_text(strip=True).replace("$","").replace(",",""))
                # parse franking %
                frk = float(tds[idx_frk].get_text(strip=True).replace("%",""))
                rows.append((ex_date, amt, frk))
            return rows
    return []

def fetch_franking_marketindex(code: str):
    """
    Scrape MarketIndex’s static table on https://www.marketindex.com.au/asx/{code}
    """
    url = f"https://www.marketindex.com.au/asx/{code.lower()}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    tables = soup.find_all("table")
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    for tbl in tables:
        thead = tbl.find("thead")
        if not thead:
            continue
        headers = [th.get_text(strip=True).lower() for th in thead.find_all("th")]
        if "ex-date" in headers and "amount" in headers and "franking" in headers:
            idx_ex = headers.index("ex-date")
            idx_amt = headers.index("amount")
            idx_frk = headers.index("franking")
            rows = []
            for tr in tbl.find("tbody").find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) <= max(idx_ex, idx_amt, idx_frk):
                    continue
                dtxt = tds[idx_ex].get_text(strip=True)
                # try dd/mm/YYYY or “d MMM YYYY”
                ex_date = None
                for fmt in ("%d/%m/%Y", "%d %b %Y"):
                    try:
                        ex_date = datetime.strptime(dtxt, fmt).date()
                        break
                    except ValueError:
                        continue
                if not ex_date or ex_date < cutoff:
                    continue
                amt = float(tds[idx_amt].get_text(strip=True).replace("$","").replace(",",""))
                frk = float(tds[idx_frk].get_text(strip=True).replace("%",""))
                rows.append((ex_date, amt, frk))
            return rows
    return []

@app.route("/")
def index():
    return "Stock API Proxy (Yahoo + ASX/MarketIndex) running."

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    symbol = normalise(raw)
    base = symbol.split(".")[0]

    # 1) fetch price
    try:
        t = yf.Ticker(symbol)
        price = float(t.fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error": f"Price fetch failed: {e}"}), 500

    # 2) trailing-12m dividend
    dividend12 = None
    try:
        hist = t.dividends
        if not hist.empty:
            # strip tz and sum last 365 days
            idx = pd.to_datetime(hist.index)
            cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=365)
            dividend12 = float(hist[idx >= cutoff].sum())
    except:
        dividend12 = None

    # 3) weighted franking
    franking = None
    try:
        rows = fetch_franking_asx(base)
        if not rows:
            rows = fetch_franking_marketindex(base)
        if rows:
            tot_div = sum(a for _, a, _ in rows)
            tot_frk = sum(a * (f/100.0) for _, a, f in rows)
            if tot_div > 0:
                franking = round((tot_frk / tot_div) * 100, 2)
    except:
        franking = None

    return jsonify({
        "symbol": symbol,
        "price": price,
        "dividend12": dividend12,
        "franking": franking
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
