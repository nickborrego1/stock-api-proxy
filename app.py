import os
import logging
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_fy_bounds(ref_date=None):
    """Return start/end dates for the most recent completed Australian FY."""
    today = ref_date or datetime.utcnow()
    year = today.year
    if today.month >= 7:
        start = datetime(year - 1, 7, 1)
        end   = datetime(year,   6, 30)
    else:
        start = datetime(year - 2, 7, 1)
        end   = datetime(year - 1, 6, 30)
    return start, end

def fetch_dividends_asx(ticker):
    """
    Scrape IntelligentInvestor for dividend events, sum up those
    within the last completed FY.
    """
    fy_start, fy_end = get_fy_bounds()
    url = f"https://www.intelligentinvestor.com.au/shares/asx-{ticker.lower()}/{ticker.upper()}/dividends"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    total_div = 0.0
    rows = soup.select("table.dividends-table tbody tr")
    for row in rows:
        date_str = row.select_one("td:nth-of-type(4)").get_text(strip=True)
        amount_str = row.select_one("td:nth-of-type(3)").get_text(strip=True)
        try:
            paid_date = datetime.strptime(date_str, "%d %b %Y")
            amount = float(amount_str.replace("¢", "")) / 100.0  # cents to dollars
        except Exception:
            continue
        if fy_start <= paid_date <= fy_end:
            total_div += amount

    return round(total_div, 2), fy_start.date(), fy_end.date()

@app.route("/api/ticker")
def api_ticker():
    ticker = request.args.get("symbol", "").strip().upper()
    if not ticker:
        return jsonify({"error": "Missing ?symbol= parameter"}), 400

    try:
        div_total, fy_start, fy_end = fetch_dividends_asx(ticker)
    except Exception as e:
        logger.exception("Dividend fetch failed")
        return jsonify({"error": f"Failed to fetch data for {ticker}"}), 502

    return jsonify({
        "symbol": ticker,
        "dividend_sum": div_total,
        "financial_year": f"{fy_start} to {fy_end}"
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
