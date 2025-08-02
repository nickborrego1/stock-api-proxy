from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, date

app = Flask(__name__)
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ------------------------------------------------------------------ #
# helpers
# ------------------------------------------------------------------ #
def normalise(raw: str) -> str:
    """'vhy' → 'VHY.AX'  (keep suffix if user already typed one)."""
    raw = raw.strip().upper()
    return raw if "." in raw else f"{raw}.AX"

def parse_exdate(txt: str) -> date | None:
    txt = txt.strip()
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None

def current_fy_window() -> tuple[date, date]:
    """
    Return (start, end) dates for the **latest completed / in-progress**
    Australian financial year  (1 Jul → 30 Jun).
    """
    today = datetime.utcnow().date()
    yr = today.year
    if today.month < 7:            # Jan-Jun  → we’re in FY that started last year
        start = date(yr - 1, 7, 1)
    else:                          # Jul-Dec → FY starts this year
        start = date(yr, 7, 1)
    end = start.replace(year=start.year + 1) - timedelta(days=1)
    return start, end

FY_START, FY_END = current_fy_window()

# ------------------------------------------------------------------ #
# scraping  (MarketIndex → InvestSMART fallback)
# ------------------------------------------------------------------ #
def scrape_dividends(code: str):
    """
    Returns (cash_dividends_in_current_FY , weighted_fran_pct)  or  (None, None).
    """

    def parse_table(html: str):
        soup = BeautifulSoup(html, "html.parser")
        target = None
        for tbl in soup.find_all("table"):
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            if {"amount", "franking"}.issubset(hdrs):
                target = tbl
                break
        if target is None:
            return None, None

        hdrs = [th.get_text(strip=True).lower() for th in target.find_all("th")]
        try:
            ex_i = next(i for i, h in enumerate(hdrs) if "ex" in h)
            amt_i = hdrs.index("amount")
            fr_i  = hdrs.index("franking")
        except (StopIteration, ValueError):
            return None, None

        tot_div = tot_fran_cash = 0.0

        for tr in target.find("tbody").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) <= max(ex_i, amt_i, fr_i):
                continue

            exd = parse_exdate(tds[ex_i].get_text())
            if not exd or not (FY_START <= exd <= FY_END):
                continue

            # amount
            try:
                amt = float(
                    re.sub(r"[^\d.,]", "", tds[amt_i].get_text()).replace(",", "")
                )
            except ValueError:
                continue
            # franking %
            try:
                fr_pct = float(re.sub(r"[^\d.]", "", tds[fr_i].get_text()))
            except ValueError:
                fr_pct = 0.0

            tot_div += amt
            tot_fran_cash += amt * (fr_pct / 100.0)

        if tot_div == 0:
            return None, None
        return round(tot_div, 6), round(tot_fran_cash * 100 / tot_div, 2)

    # ---- 1) MarketIndex ------------------------------------------
    mi_url = f"https://www.marketindex.com.au/asx/{code.lower()}"
    try:
        r = requests.get(
            mi_url,
            headers={
                "User-Agent": UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
            },
            timeout=15,
        )
        if r.ok:
            out = parse_table(r.text)
            if all(out):
                return out
        elif r.status_code != 403:
            app.logger.warning("MarketIndex returned %s", r.status_code)
    except Exception as e:
        app.logger.warning("MarketIndex error: %s", e)

    # ---- 2) InvestSMART fallback ---------------------------------
    is_url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        r = requests.get(is_url, headers={"User-Agent": UA}, timeout=15)
        if r.ok:
            return parse_table(r.text)
    except Exception as e:
        app.logger.warning("InvestSMART error: %s", e)

    return None, None

# ------------------------------------------------------------------ #
# Flask routes
# ------------------------------------------------------------------ #
@app.route("/")
def home():
    return (
        "Stock API Proxy – /stock?symbol=CODE  (values use current Australian FY)",
        200,
    )

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # ---- price ----------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # ---- dividends & franking ------------------------------------
    dividend12, franking = scrape_dividends(base)

    return jsonify(
        symbol     = symbol,
        price      = price,
        dividend12 = dividend12,
        franking   = franking,
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
