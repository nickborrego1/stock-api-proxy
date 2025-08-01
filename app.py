from flask import Flask, request, jsonify
import requests, yfinance as yf, pandas as pd, re
from bs4 import BeautifulSoup
from datetime import timedelta
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

def normalise(raw: str) -> str:
    """Turn VHY or vhy.ax → VHY.AX"""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def fetch_franking(symbol_no_ax: str) -> list[tuple[pd.Timestamp,float,float]]:
    """
    Scrape ASX dividend search page for the last year's dividends.
    Returns list of tuples (ex_date, amount, franking_percent).
    """
    url = f"https://www.asx.com.au/markets/trade-our-cash-market/dividend-search?asxCode={symbol_no_ax}"
    r = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("table tbody tr")
    data=[]
    for tr in rows:
        tds=tr.find_all("td")
        if len(tds)<7: continue
        try:
            amount  = float(tds[3].get_text(strip=True).replace("$",""))
            frank   = float(tds[4].get_text(strip=True).replace("%",""))
            ex_date = pd.to_datetime(tds[5].get_text(strip=True), dayfirst=True)
            data.append((ex_date, amount, frank))
        except Exception:
            continue
    return data

@app.route("/")
def idx(): return "Stock API Proxy running."

@app.route("/stock")
def stock():
    raw = request.args.get("symbol","")
    if not raw: return jsonify({"error":"No symbol provided"}),400
    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error":f"Price fetch failed: {e}"}),500

    # Dividends last 365d (yfinance)
    div12=0
    try:
        hist = tkr.dividends
        if not hist.empty:
            hist.index = pd.to_datetime(hist.index)
            if hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)
            cutoff=pd.Timestamp.utcnow().tz_localize(None)-pd.DateOffset(days=365)
            div12 = float(hist[hist.index>=cutoff].sum())
    except: pass

    # Weighted franking via ASX scrape
    weighted_f=42
    try:
        cut = pd.Timestamp.utcnow().tz_localize(None)-pd.DateOffset(days=365)
        rows=fetch_franking(base)
        tot_div=tot_frank=0
        for exdate,amt,fr in rows:
            if exdate>=cut:
                tot_div   += amt
                tot_frank += amt*(fr/100)
        if tot_div>0:
            weighted_f=round((tot_frank/tot_div)*100,2)
    except: pass

    return jsonify({"symbol":symbol,"price":price,"dividend12":div12,"franking":weighted_f})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=8080)
