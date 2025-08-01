from flask import Flask, request, jsonify
import requests, yfinance as yf, pandas as pd
from datetime import datetime, timedelta
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

ASX_JSON = "https://www.asx.com.au/api/v1/search/dividends?code={code}&offset=0&limit=50"

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def fetch_asx_json(code: str):
    r = requests.get(ASX_JSON.format(code=code), timeout=15,
                     headers={"User-Agent":"Mozilla/5.0"})
    return r.json()

@app.route("/stock")
def stock():
    raw = request.args.get("symbol","").strip()
    if not raw:
        return jsonify({"error":"No symbol provided"}),400
    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # ---- price via yfinance ----
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify({"error":f"Price fetch failed: {e}"}),500

    # ---- dividend via yfinance ----
    dividend12 = None
    try:
        hist = yf.Ticker(symbol).dividends
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cutoff = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cutoff].sum())
    except: pass

    # ---- weighted franking via ASX JSON ----
    franking = 42
    try:
        j = fetch_asx_json(base)
        cutoff = datetime.utcnow().date() - timedelta(days=365)
        tot_div = tot_frank = 0
        for row in j.get("data", []):
            ex = datetime.strptime(row["exDate"], "%Y-%m-%d").date()
            if ex >= cutoff:
                amt = float(row["amount"])
                fr  = float(row["frankingPercent"])
                tot_div   += amt
                tot_frank += amt * (fr/100)
        if tot_div:
            franking = round((tot_frank / tot_div) * 100, 2)
    except Exception as e:
        print("Franking JSON error:", e)

    return jsonify({
        "symbol": symbol,
        "price": price,
        "dividend12": dividend12,
        "franking": franking
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
