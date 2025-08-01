from flask import Flask, request, jsonify
import json, yfinance as yf, pandas as pd
from datetime import datetime, timedelta
from flask_cors import CORS
from pathlib import Path

app = Flask(__name__)
CORS(app)

CACHE_FILE = Path("franking_cache.json")

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def load_fran_cache(code: str):
    if not CACHE_FILE.exists():
        return None
    data = json.loads(CACHE_FILE.read_text())
    entry = data.get(code.upper())
    if not entry:
        return None
    # optional freshness check (7 days)
    ts = datetime.fromisoformat(entry["timestamp"])
    if (datetime.utcnow() - ts).days > 7:
        return None
    return entry["franking"]

@app.route("/stock")
def stock():
    raw = request.args.get("symbol","").strip()
    if not raw:
        return jsonify({"error":"No symbol provided"}),400
    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # price & trailing dividend via yfinance
    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
        hist  = tkr.dividends
        dividend12 = None
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cut = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cut].sum())
    except Exception as e:
        return jsonify({"error":f"yfinance error: {e}"}),500

    franking = load_fran_cache(base) or 42
    return jsonify({
        "symbol": symbol,
        "price": price,
        "dividend12": dividend12,
        "franking": franking
    })

@app.route("/")
def root(): return "Proxy with cache live"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
