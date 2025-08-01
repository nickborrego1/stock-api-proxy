from flask import Flask, request, jsonify
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

def normalise_symbol(raw: str) -> str:
    """Add .AX suffix if user omitted it."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def lookup_symbol(query: str) -> str | None:
    """
    If user typed a company name (e.g. 'Vanguard high yield'),
    try Yahoo autocomplete and return first ASX match, else None.
    """
    url = f"https://autoc.finance.yahoo.com/autoc?query={query}&region=AU"
    try:
        data = requests.get(url, timeout=10).json()
        for item in data.get("ResultSet", {}).get("Result", []):
            if item["exchDisp"] == "ASX":
                return item["symbol"]  # e.g. 'VHY.AX'
    except Exception:
        pass
    return None

@app.route("/")
def index():
    return "Stock API Proxy (Yahoo yfinance) running."

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    # if user typed a company name (>3 chars, no dot), try lookup
    symbol = normalise_symbol(raw)
    if "." not in raw and len(raw) > 3:      # likely a name, not a code
        lookup = lookup_symbol(raw)
        if lookup:
            symbol = lookup

    try:
        ticker = yf.Ticker(symbol)
        price  = float(ticker.fast_info["lastPrice"])

        # dividends in last 365 days
        hist    = ticker.dividends
        cutoff  = datetime.utcnow() - timedelta(days=365)
        last12  = hist[hist.index >= cutoff]
        dividend12 = float(last12.sum()) if not last12.empty else None

    except Exception as e:
        return jsonify({"error": f"Fetch failed: {e}"}), 500

    return jsonify({
        "symbol": symbol,
        "price": price,
        "dividend12": dividend12,
        "franking": 42
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
