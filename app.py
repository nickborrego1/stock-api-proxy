from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/")
def index():
    return "Stock API Proxy (Yahoo/yfinance) running."

@app.route("/stock")
def stock():
    symbol = request.args.get("symbol")
    if not symbol:
        return jsonify({"error": "No symbol provided"}), 400

    try:
        ticker = yf.Ticker(symbol)
        price  = ticker.fast_info["lastPrice"]

        # trailing 12-month dividends
        hist   = ticker.dividends
        last12 = hist[hist.index >= hist.index.max() - pd.DateOffset(months=12)]
        dividend12 = float(last12.sum()) if not last12.empty else None

    except Exception as e:
        return jsonify({"error": f"Fetch failed: {e}"}), 500

    return jsonify({
        "price": price,
        "dividend12": dividend12,
        "franking": 42   # default editable
    })

if __name__ == "__main__":
    import pandas as pd   # ensure pandas import for DateOffset
    app.run(host="0.0.0.0", port=8080)
