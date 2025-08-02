# app.py  –  Stock API proxy (InvestSMART, FY-aware)
from __future__ import annotations
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from dateutil import parser as dtparser     # robust date fallback

app = Flask(__name__)
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ---------- helpers ----------------------------------------------------------
def normalise(raw: str) -> str:
    """'vhy' → 'VHY.AX'.  Leave suffix untouched if user already supplied one."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def previous_fy_bounds(today: date | None = None) -> tuple[date, date]:
    """
    Return (start, end) of the last *completed* AU financial year.
      e.g.  2025-08-02 → (2024-07-01, 2025-06-30)
            2025-03-15 → (2023-07-01, 2024-06-30)
    """
    today = today or datetime.utcnow().date()
    if today.month >= 7:              # already in the new FY
        start_year = today.year - 1
    else:                             # Jan-Jun → back two FYs
        start_year = today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def parse_exdate(txt: str) -> date | None:
    """
    Parse InvestSMART ex-div dates.  Handles:
      • ‘1 Jul 2024’, ‘01-Jul-24’, ‘1 Jul 2024’ (&nbsp;)  …
      • falls back to python-dateutil for oddballs.
    """
    txt = txt.replace("\xa0", " ").strip()
    QUICK = ("%d %b %Y", "%d %b %y", "%d %B %Y", "%d-%b-%Y", "%d/%m/%Y")
    for fmt in QUICK:
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    try:
        return dtparser.parse(txt, dayfirst=True).date()
    except Exception:
        return None


def clean_amount(cell: str) -> float | None:
    """
    Convert dividend strings to dollars:
      '$2.43' → 2.43
      '61.79¢' / '61.79c' → 0.6179
    """
    t = cell.replace("\xa0", "").replace("$", "").strip().lower()
    if t.endswith(("c", "¢")):
        t = t[:-1]
        try:
            return float(t) / 100.0
        except ValueError:
            return None
    try:
        return float(t)
    except ValueError:
        return None


# ---------- main scrape ------------------------------------------------------
def fetch_dividend_stats(code: str) -> tuple[float | None, float | None]:
    """
    Returns (cash_div_last_FY, weighted_fran_pct)  OR  (None, None)
    """
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        res = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        res.raise_for_status()
    except Exception as e:
        print("InvestSMART request error:", e)
        return None, None

    soup = BeautifulSoup(res.text, "html.parser")

    # locate the first table that contains BOTH a dividend & franking column
    div_tbl = None
    for tbl in soup.find_all("table"):
        hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if any("dividend" in h or "amount" in h for h in hdrs) and "franking" in hdrs:
            div_tbl = tbl
            break
    if not div_tbl:
        return None, None

    hdrs = [th.get_text(strip=True).lower() for th in div_tbl.find_all("th")]

    # flexible header lookup (handles ‘amount’, ‘dividend per share’, etc.)
    def find_idx(names: list[str]) -> int | None:
        for nm in names:
            for i, h in enumerate(hdrs):
                if nm in h:
                    return i
        return None

    ex_i   = find_idx(["ex", "ex-div"])            # must contain “ex”
    div_i  = find_idx(["dividend", "amount"])
    fran_i = find_idx(["franking"])
    if None in (ex_i, div_i, fran_i):
        return None, None

    fy_start, fy_end = previous_fy_bounds()
    tot_cash = tot_fran_cash = 0.0

    for tr in div_tbl.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ex_i, div_i, fran_i):
            continue

        exd = parse_exdate(tds[ex_i].get_text())
        if not exd or not (fy_start <= exd <= fy_end):
            continue

        amt = clean_amount(tds[div_i].get_text())
        if amt is None:
            continue

        try:
            fran_pct = float(re.sub(r"[^\d.]", "", tds[fran_i].get_text()))
        except ValueError:
            fran_pct = 0.0

        tot_cash       += amt
        tot_fran_cash  += amt * (fran_pct / 100.0)

    if tot_cash == 0:
        return None, None
    return round(tot_cash, 6), round((tot_fran_cash / tot_cash) * 100, 2)


# ---------- Flask endpoints --------------------------------------------------
@app.route("/")
def home():
    return (
        "Stock API Proxy – call /stock?symbol=CODE  (e.g. /stock?symbol=VHY)",
        200,
    )


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # live price -----------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # dividends & franking ------------------------------------------
    dividend, franking = fetch_dividend_stats(base)

    return jsonify(
        symbol     = symbol,
        price      = price,
        dividend12 = dividend,   # now FY-accurate
        franking   = franking
    )


if __name__ == "__main__":                 # local dev:  python app.py
    app.run(host="0.0.0.0", port=8080, debug=True)
