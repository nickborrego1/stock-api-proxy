from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # allow cross-origin requests from your calculator

# ---------- helpers ----------

def normalise_symbol(raw: str) -> str:
    """
    Accepts 'vhy', 'VHY', 'vhy.ax', 'VHY.AX' and returns 'VHY.AX'.
    """
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

# ---------- routes ----------

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

        # --- price ---
        price = float(ticker.fast_info["lastPrice"])

        # --- trailing-12-month dividends ---
        hist = ticker.dividends
        if hist.empty:
            dividend12 = None
        else:
            # ensure DateTimeIndex and force timezone-naïve comparison
            hist.index = pd.to_datetime(hist.index)
            if hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)

            cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.DateOffset(days=365)
            last12 = hist[hist.index >= cutoff]
            dividend12 = float(last12.sum()) if not last12.empty else None

    except Exception as e:
        return jsonify({"error": f"Fetch failed: {e}"}), 500

    return jsonify({
        "symbol"    : symbol,
        "price"     : price,
        "dividend12": dividend12,
        "franking"  : 42          # default editable in front-end
    })

# ---------- main ----------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
