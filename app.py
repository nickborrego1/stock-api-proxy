# app.py  –  Stock API proxy (InvestSMART)
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta

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
    """'vhy' → 'VHY.AX'.  If suffix already supplied, keep it."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def parse_exdate(txt: str) -> date | None:
    """Handle the few date formats InvestSMART uses."""
    txt = txt.strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def clean_amount(cell_text: str) -> float | None:
    """'$2.43'  → 2.43   |   '61.79¢' → 0.6179"""
    t = cell_text.strip().replace("$", "")
    if "¢" in t:
        t = t.replace("¢", "")
        try:
            return float(t) / 100.0
        except ValueError:
            return None
    try:
        return float(t)
    except ValueError:
        return None


def previous_fy_bounds(today: date | None = None) -> tuple[date, date]:
    """
    Return (start, end) of the *last completed* AU financial year.
    • If today is 2025-08-02 → bounds are 2024-07-01 … 2025-06-30
    • If today is 2025-03-15 → bounds are 2023-07-01 … 2024-06-30
    """
    if today is None:
        today = datetime.utcnow().date()

    if today.month >= 7:           # we are already in the new FY
        start_year = today.year - 1
    else:                          # still in Jan-Jun → go back two
        start_year = today.year - 2

    start = date(start_year,     7, 1)
    end   = date(start_year + 1, 6, 30)
    return start, end


# ------------------------------------------------------------------ #
# main scrape  (InvestSMART)
# ------------------------------------------------------------------ #
def fetch_dividend_stats(code: str) -> tuple[float | None, float | None]:
    """
    Returns (cash_dividend_last_FY, weighted_fran_pct) or (None, None)
    code: plain ASX code e.g. 'VHY'
    """
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"

    try:
        res = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        res.raise_for_status()
    except Exception as e:
        print("InvestSMART request error:", e)
        return None, None

    soup = BeautifulSoup(res.text, "html.parser")
    div_tbl = None
    for tbl in soup.find_all("table"):
        hdr = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if {"dividend", "franking"}.issubset(hdr):
            div_tbl = tbl
            break
    if div_tbl is None:
        return None, None

    hdr = [th.get_text(strip=True).lower() for th in div_tbl.find_all("th")]
    try:
        ex_i   = next(i for i, h in enumerate(hdr) if "ex" in h and "date" in h)
        div_i  = hdr.index("dividend")
        fran_i = hdr.index("franking")
    except (ValueError, StopIteration):
        return None, None

    fy_start, fy_end = previous_fy_bounds()
    tot_div_cash = tot_fran_cash = 0.0

    for tr in div_tbl.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ex_i, div_i, fran_i):
            continue

        exd = parse_exdate(tds[ex_i].get_text())
        if not exd or exd < fy_start or exd > fy_end:
            continue

        amt = clean_amount(tds[div_i].get_text())
        if amt is None:
            continue

        try:
            fran_pct = float(re.sub(r"[^\d.]", "", tds[fran_i].get_text()))
        except ValueError:
            fran_pct = 0.0

        tot_div_cash   += amt
        tot_fran_cash  += amt * (fran_pct / 100.0)

    if tot_div_cash == 0:
        return None, None

    weighted_pct = round((tot_fran_cash / tot_div_cash) * 100, 2)
    return round(tot_div_cash, 6), weighted_pct


# ------------------------------------------------------------------ #
# Flask routes
# ------------------------------------------------------------------ #
@app.route("/")
def home():
    return "Stock API Proxy – call /stock?symbol=CODE  (e.g. /stock?symbol=VHY)", 200


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # 1) live price --------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # 2) dividends & franking ---------------------------------------
    dividend12, franking = fetch_dividend_stats(base)

    return jsonify(
        symbol     = symbol,
        price      = price,
        dividend12 = dividend12,
        franking   = franking
    )


# ------------------------------------------------------------------ #
if __name__ == "__main__":
    # local dev:  python app.py
    app.run(host="0.0.0.0", port=8080)
