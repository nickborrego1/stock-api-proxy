# app.py
from flask import Flask, request, jsonify
import json, yfinance as yf, pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

CACHE_FILE = Path("franking_cache.json")

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def read_fran(code: str) -> float:
    if not CACHE_FILE.exists():
        return 42.0
    data = json.loads(CACHE_FILE.read_text())
    return data.get(code.upper(), {}).get("franking", 42.0)

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
        hist  = tkr.dividends
        dividend12 = None
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cutoff = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cutoff].sum())
    except Exception as e:
        return jsonify(error=f"yfinance error: {e}"), 500

    return jsonify(
        symbol     = symbol,
        price      = price,
        dividend12 = dividend12,
        franking   = read_fran(base)
    )

@app.route("/")
def root():
    return "ASX Proxy live — use /stock?symbol=CODE"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
