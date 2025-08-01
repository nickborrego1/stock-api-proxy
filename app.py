from flask import Flask, request, jsonify
import requests
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Allow cross-origin requests

ALPHA_VANTAGE_API_KEY = "5CZN15ICMRH2QQW7"

@app.route("/")
def index():
    return "Stock API Proxy running."

@app.route("/stock", methods=["GET"])
def stock():
    symbol = request.args.get("symbol")
    if not symbol:
        return jsonify({"error": "No symbol provided"}), 400

    # Get price
    price_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
    price_r = requests.get(price_url, timeout=10)
    price_json = price_r.json()
    price = None
    try:
        price = float(price_json["Global Quote"]["05. price"])
    except Exception:
        price = None

    # Get 12m dividend sum
    div_url = f"https://www.alphavantage.co/query?function=TIME_SERIES_MONTHLY_ADJUSTED&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
    div_r = requests.get(div_url, timeout=10)
    div_json = div_r.json()
    dividend12 = 0
    try:
        count = 0
        for _, obj in div_json["Monthly Adjusted Time Series"].items():
            dividend = float(obj["7. dividend amount"])
            dividend12 += dividend
            count += 1
            if count == 12:
                break
    except Exception:
        dividend12 = None

    # Franking not available via API; default 42
    result = {
        "price": price,
        "dividend12": dividend12,
        "franking": 42
    }
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
