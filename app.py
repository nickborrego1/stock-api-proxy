from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
from flask_cors import CORS
from datetime import timedelta

app = Flask(__name__)
CORS(app)  # allow CORS so the browser can call your proxy

def normalise_symbol(raw: str) -> str:
    """
    Accepts 'vhy', 'VHY', 'vhy.ax', 'VHY.AX' and returns
    the uppercase Yahoo symbol with '.AX' suffix.
    """
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

@app.route("/")
def index():
    return "Stock API Proxy (yfinance) running."

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw:
        return jsonify({"error": "No symbol provided"}), 400

    symbol = normalise_symbol(raw)

    try:
        ticker = yf.Ticker(symbol)

        # Current (last) price
        price = float(ticker.fast_info["lastPrice"])

        # Dividends in the last 365 days
        hist = ticker.dividends
        if hist.empty:
            dividend12 = None
        else:
            # Ensure index is timezone-naïve so we can compare
            hist.index = hist.index.tz_localize(None)
            cutoff     = pd.Timestamp.utcnow() - pd.DateOffset(days=365)
            last12     = hist[hist.index >= cutoff]
            dividend12 = float(last12.sum()) if not last12.empty else None

    except Exception as e:
        return jsonify({"error": f"Fetch failed: {e}"}), 500

    # Default franking 42 %; user can edit in calculator
    return jsonify({
        "symbol"     : symbol,
        "price"      : price,
        "dividend12" : dividend12,
        "franking"   : 42
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
