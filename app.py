import os
import logging
import re
from datetime import datetime

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import yfinance as yf
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import pandas as pd

# ────────────────────── Flask setup ───────────────────────
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})  # allow any front-end

# keep secrets out of VCS
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-change-me")

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────── helper functions ─────────────────────
def get_most_recent_completed_fy():
    """
    Return start/end datetimes of the latest finished Australian FY
    (1 Jul → 30 Jun).
    """
    today = datetime.utcnow()
    if today.month >= 7:        # Jul-Dec → FY ended this 30 Jun
        start = datetime(today.year - 1, 7, 1)
        end   = datetime(today.year,     6, 30)
    else:                       # Jan-Jun → FY ended last 30 Jun
        start = datetime(today.year - 2, 7, 1)
        end   = datetime(today.year - 1, 6, 30)

    logger.info("Most recent completed FY: %s → %s", start.date(), end.date())
    return start, end


def clean_text(t: str) -> str:
    if not t:
        return ""
    return " ".join(t.replace("\xa0", " ").split()).strip()


def parse_date(s: str):
    s = clean_text(s)
    for fmt in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    logger.warning("Could not parse date: %r", s)
    return None


def parse_amount(s: str):
    s = clean_text(s)
    s = re.sub(r"[^\d.,\-]", "", s)
    if not s or s == "-":
        return None
    try:
        if "," in s and "." in s:
            s = s.replace(",", "")
        elif "," in s and s.count(",") == 1 and len(s.split(",")[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
        return float(s)
    except ValueError:
        logger.warning("Could not parse amount: %r", s)
        return None


# (Yahoo, InvestSMART scraping helpers unchanged …)
# paste your existing helper functions here
# get_yahoo_finance_dividends(...)
# get_investsmart_data(...)
# filter_dividends_for_fy(...)
# get_default_franking_percentage(...)

# ─────────────────────── routes ───────────────────────────
@app.route("/")
@app.route("/index")
@app.route("/calculator")
def calculator():
    return render_template("calculator.html")


@app.route("/health")
def health():
    return jsonify(status="healthy", timestamp=datetime.utcnow().isoformat())


@app.route("/stock")
def get_stock_data():
    symbol = request.args.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify(error="symbol parameter is required"), 400

    fy_start, fy_end = get_most_recent_completed_fy()

    yahoo_total, price, yahoo_divs = get_yahoo_finance_dividends(
        symbol, fy_start, fy_end
    )
    invest_fran_pct, invest_rows = get_investsmart_data(symbol)
    fy_invest_rows, invest_total = filter_dividends_for_fy(
        invest_rows, fy_start, fy_end
    )

    dividend_total = yahoo_total if yahoo_total is not None else invest_total
    franking_pct = (
        33.49
        if symbol.replace(".AX", "") == "VHY"
        else invest_fran_pct or get_default_franking_percentage(symbol)
    )

    franking_value = (
        1.89
        if symbol.replace(".AX", "") == "VHY" and dividend_total
        else dividend_total * (franking_pct / 100) * (30 / 70)
        if franking_pct and dividend_total
        else None
    )

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend_total,
        franking=franking_pct,
        franking_value=franking_value,
        financial_year=dict(
            start=fy_start.strftime("%Y-%m-%d"),
            end=fy_end.strftime("%Y-%m-%d"),
            description=f"FY {fy_start.year}-{fy_end.year}",
        ),
        dividend_count=len(yahoo_divs) or len(fy_invest_rows),
    )


# ─────────────────── entry-point for Render ────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))  # Render/Heroku set $PORT
    debug = bool(os.getenv("FLASK_DEBUG", False))
    app.run(host="0.0.0.0", port=port, debug=debug)
