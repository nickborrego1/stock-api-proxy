from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, yfinance as yf, pandas as pd, re, unicodedata
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def clean_num(txt: str) -> float:
    s = unicodedata.normalize("NFKD", txt)
    s = re.sub(r"[^\d.]", "", s) or "0"
    return float(s)

def fetch_asx_divsearch(code: str):
    """Scrape ASX's own dividend-search page."""
    url = f"https://www.asx.com.au/markets/trade-our-cash-market/dividend-search?asxCode={code}"
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    tbl = soup.find("table")
    if not tbl or not tbl.tbody:
        return []
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    rows = []
    for tr in tbl.tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        try:
            amt     = clean_num(tds[3].get_text())
            frank   = clean_num(tds[4].get_text())
            ex_date = datetime.strptime(tds[5].get_text().strip(), "%d %b %Y").date()
        except:
            continue
        if ex_date >= cutoff:
            rows.append((ex_date, amt, frank))
    return rows

def fetch_franking_investsmart(code: str):
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    if r.status_code != 200:
        return []
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
            amt     = clean_num(tds[3].get_text())
            frank   = clean_num(tds[4].get_text())
            ex_date = datetime.strptime(tds[5].get_text().strip(), "%d %b %Y").date()
        except:
            continue
        if ex_date >= cutoff:
            rows.append((ex_date, amt, frank))
    return rows

@app.route("/stock")
def stock():
    raw = request.args.get("symbol","").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # 1) Price + trailing-12m dividends via yfinance
    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
        hist  = tkr.dividends
        dividend12 = None
        if not hist.empty:
            hist.index = pd.to_datetime(hist.index).tz_localize(None)
            cutoff = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index>=cutoff].sum())
    except Exception as e:
        return jsonify(error=f"yfinance error: {e}"), 500

    # 2) Franking: try ASX dividend-search first
    rows = fetch_asx_divsearch(base)
    if not rows:
        # fallback to InvestSMART
        rows = fetch_franking_investsmart(base)

    franking = None
    if rows:
        tot_div = sum(amt for _,amt,_ in rows)
        tot_frank = sum(amt*(fr/100) for _,amt,fr in rows)
        if tot_div>0:
            franking = round((tot_frank/tot_div)*100,2)

    return jsonify(symbol=symbol,
                   price=price,
                   dividend12=dividend12,
                   franking=franking)

@app.route("/")
def idx():
    return "Stock API Proxy running — use /stock?symbol=CODE", 200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=8080)
