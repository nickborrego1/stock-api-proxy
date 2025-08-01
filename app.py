"""
Light-weight Stock API Proxy
——————————————
• Price from Yahoo Finance (yfinance.fast_info["lastPrice"])
• Trailing-12-month cash dividend + weighted franking %
  scraped from MarketIndex dividends table.
• No Playwright, no headless browser, no ASX API.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf, pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ------------------------------------------------------------------ #
# helper functions
# ------------------------------------------------------------------ #

def normalise(raw: str) -> str:
    """Turn vhy  → VHY.AX   or   vhy.ax → VHY.AX"""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def parse_exdate(text: str):
    text = text.strip()
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None

# ------------------------------------------------------------------ #
# scrape MarketIndex for dividend + franking
# ------------------------------------------------------------------ #

def scrape_marketindex(code: str):
    """
    Return (dividend12, weighted_fran_pct)
    or (None, None) if nothing scraped.
    """
    url = f"https://www.marketindex.com.au/asx/{code.lower()}"
    try:
        res = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        res.raise_for_status()
    except Exception as e:
        print("MarketIndex request error:", e)
        return None, None

    soup = BeautifulSoup(res.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None, None

    # locate dividends table by headers
    div_tbl = None
    for tbl in tables:
        hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if {"amount", "franking"}.issubset(set(hdrs)):
            div_tbl = tbl
            break
    if div_tbl is None:
        return None, None

    hdrs = [th.get_text(strip=True).lower() for th in div_tbl.find_all("th")]
    try:
        ex_idx  = next(i for i, h in enumerate(hdrs)
                       if "ex" in h and ("date" in h or "dividend" in h))
        amt_idx = hdrs.index("amount")
        frk_idx = hdrs.index("franking")
    except (StopIteration, ValueError):
        return None, None

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = 0.0
    tot_frk_cash = 0.0

    for tr in div_tbl.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ex_idx, amt_idx, frk_idx):
            continue

        ex_date = parse_exdate(tds[ex_idx].get_text())
        if not ex_date or ex_date < cutoff:
            continue

        # cash dividend
        try:
            amt = float(re.sub(r"[^\d.]", "", tds[amt_idx].get_text()))
        except ValueError:
            continue

        # franking %
        try:
            fr_pct = float(re.sub(r"[^\d.]", "", tds[frk_idx].get_text()))
        except ValueError:
            fr_pct = 0.0

        tot_div += amt
        tot_frk_cash += amt * (fr_pct / 100.0)

    if tot_div == 0:
        return None, None

    weighted_fran = round((tot_frk_cash / tot_div) * 100, 2)
    return round(tot_div, 6), weighted_fran

# ------------------------------------------------------------------ #
# Flask endpoints
# ------------------------------------------------------------------ #

@app.route("/")
def home():
    return "Stock API Proxy (MarketIndex scraper) — use /stock?symbol=CODE", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # ---- price -----------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # ---- dividend + franking ---------------------------------------
    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol     = symbol,
        price      = price,
        dividend12 = dividend12,
        franking   = franking
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frk_cash = 0.0

    for tr in div_tbl.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ex_idx, amt_idx, frk_idx):
            continue
        ex_date = parse_exdate(tds[ex_idx].get_text())
        if not ex_date or ex_date < cutoff:
            continue
        try:
            amt = float(re.sub(r"[^\d.]", "", tds[amt_idx].get_text()))
        except ValueError:
            continue
        try:
            fr_pct = float(re.sub(r"[^\d.]", "", tds[frk_idx].get_text()))
        except ValueError:
            fr_pct = 0.0
        tot_div     += amt
        tot_frk_cash += amt * (fr_pct / 100.0)

    if tot_div == 0:
        return None, None
    return round(tot_div, 6), round((tot_frk_cash / tot_div) * 100, 2)

# ---------- Flask endpoints -----------------------------------------------

@app.route("/")
def home():
    return "Stock API Proxy (MarketIndex)", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # price
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # dividends + franking
    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
    try:
        res = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        res.raise_for_status()
    except Exception as e:
        print("MarketIndex request error:", e)
        return None, None

    soup = BeautifulSoup(res.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None, None

    # locate the dividends table (must have Amount & Franking headers)
    div_tbl = None
    for tbl in tables:
        headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if {"amount", "franking"}.issubset(set(headers)):
            div_tbl = tbl
            break
    if div_tbl is None:
        return None, None

    # column indexes
    hdrs = [th.get_text(strip=True).lower() for th in div_tbl.find_all("th")]
    try:
        ex_idx  = next(i for i,h in enumerate(hdrs) if "ex" in h and ("date" in h or "dividend" in h))
        amt_idx = hdrs.index("amount")
        frk_idx = hdrs.index("franking")
    except StopIteration:
        return None, None

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = 0.0
    tot_frk_cash = 0.0

    for tr in div_tbl.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ex_idx, amt_idx, frk_idx):
            continue

        ex_date = parse_exdate(tds[ex_idx].get_text())
        if not ex_date or ex_date < cutoff:
            continue

        # cash amount
        amt_txt = tds[amt_idx].get_text()
        try:
            amt = float(re.sub(r"[^\d.]", "", amt_txt))
        except ValueError:
            continue

        # franking %
        fr_txt = tds[frk_idx].get_text()
        try:
            fr_pct = float(re.sub(r"[^\d.]", "", fr_txt))
        except ValueError:
            fr_pct = 0.0

        tot_div += amt
        tot_frk_cash += amt * (fr_pct / 100.0)

    if tot_div == 0:
        return None, None
    weighted_fran = round((tot_frk_cash / tot_div) * 100, 2)
    return round(tot_div, 6), weighted_fran

# ------------------------------------------------------------------ #
# Flask endpoints
# ------------------------------------------------------------------ #

@app.route("/")
def index():
    return "Stock API Proxy (MarketIndex scraper) — /stock?symbol=CODE", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # --- price via Yahoo ---------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # --- dividends + franking via MarketIndex ------------------------------
    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol     = symbol,
        price      = price,
        dividend12 = dividend12,
        franking   = franking
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
        r.raise_for_status()
    except Exception as e:
        print("MarketIndex request error:", e)
        return None, None

    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None, None

    # Identify the dividends table by headers
    div_table = None
    for tbl in tables:
        headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if {"ex-date", "amount", "franking"}.issubset(set(headers)):
            div_table = tbl
            break
    if div_table is None:
        return None, None

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_amt = 0.0
    tot_fran = 0.0
    for tr in div_table.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        ex_date = parse_exdate(tds[0].get_text())
        if not ex_date or ex_date < cutoff:
            continue
        try:
            amt = float(re.sub(r"[^\d.]", "", tds[2].get_text()))
        except ValueError:
            continue
        try:
            fran_pct = float(re.sub(r"[^\d.]", "", tds[3].get_text()))
        except ValueError:
            fran_pct = 0.0
        tot_amt += amt
        tot_fran += amt * (fran_pct / 100.0)

    if tot_amt == 0:
        return None, None
    weighted_fran = round((tot_fran / tot_amt) * 100, 2)
    return round(tot_amt, 6), weighted_fran

@app.route("/")
def home():
    return "Stock API Proxy (MarketIndex scrape)", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # 1 Price
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # 2 Dividends + franking from MarketIndex
    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
