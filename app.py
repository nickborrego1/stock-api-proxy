# app.py — ASX dividend proxy (InvestSMART, pagination-proof)

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from flask import Flask, jsonify, request
from flask_cors import CORS

# ------------------------------------------------------------------ #
# Flask setup
# ------------------------------------------------------------------ #
app = Flask(__name__)
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def normalise(raw: str) -> str:
    """Translate e.g. 'vhy' → 'VHY.AX'."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def last_completed_fy_bounds(today: date | None = None) -> tuple[date, date]:
    """
    Return the *completed* Australian FY (1 Jul → 30 Jun) that has most
    recently ended.

    e.g.  2 Aug 2025 → (2024-07-01, 2025-06-30)
    """
    today = today or datetime.utcnow().date()
    end_year = today.year if today.month < 7 else today.year - 1
    start_year = end_year - 1
    return date(start_year, 7, 1), date(end_year, 6, 30)


def parse_exdate(txt: str) -> date | None:
    """Robust date reader – accepts most formats InvestSMART emits."""
    txt = (
        txt.replace("\u00a0", " ")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .strip()
    )
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d %b %y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    try:
        return dtparser.parse(txt, dayfirst=True).date()
    except Exception:
        return None


_WS_RE = re.compile(r"\s+")


def clean_amount(cell: str) -> float | None:
    """
    Return the cash value per share in dollars.

    Accepts '$1.04', '77.4¢', '32.83 cent', etc.
    """
    t = (
        _WS_RE.sub("", cell)  # collapse all whitespace incl. NB-space
        .replace("$", "")
        .replace(",", "")
        .lower()
    )
    for suf in ("cpu", "c", "¢", "cent", "cents"):
        if t.endswith(suf):
            try:
                return float(t[: -len(suf)]) / 100.0
            except ValueError:
                return None
    try:
        return float(t)
    except ValueError:
        return None


# ------------------------------------------------------------------ #
# Scraping core
# ------------------------------------------------------------------ #


def get_all_pages(start_url: str) -> list[BeautifulSoup]:
    """Follow » pagination links and return BeautifulSoup for every page."""
    soups: list[BeautifulSoup] = []
    next_url: str | None = start_url
    while next_url:
        html = requests.get(next_url, headers={"User-Agent": UA}, timeout=20).text
        soup = BeautifulSoup(html, "html.parser")
        soups.append(soup)

        nxt = soup.select_one(".pagination a:contains('»')")
        next_url = urljoin(start_url, nxt["href"]) if nxt else None
    return soups


def row_stats(tr) -> tuple[date, float, float] | None:
    """
    Extract (ex-date, cash dividend $, franking %) from a <tr>.
    The function is robust to random blank/br cells.
    """
    cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
    if not cells:
        return None

    # 1) locate the ex-dividend date cell
    ex_idx = None
    exd = None
    for i, txt in enumerate(cells):
        d = parse_exdate(txt)
        if d:
            ex_idx, exd = i, d
            break
    if ex_idx is None:
        return None

    # 2) franking % = first cell *before* ex-date that contains a '%'
    fran_pct = 0.0
    fran_idx = None
    for i in range(ex_idx - 1, -1, -1):
        if "%" in cells[i]:
            try:
                fran_pct = float(re.sub(r"[^\d.]", "", cells[i]))
                fran_idx = i
            except ValueError:
                pass
            break

    # 3) cash amount = first parsable amount *before* franking%
    for j in range((fran_idx or ex_idx) - 1, -1, -1):
        amt = clean_amount(cells[j])
        if amt is not None:
            return exd, amt, fran_pct
    return None


def fetch_dividend_stats(code: str, debug: bool = False):
    """
    Total cash & weighted franking for the *last completed* FY.

    Returns (tuple: cash, franking%) or (None, None) if no rows.
    """
    base_url = (
        f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
        f"?size=250&OrderBy=6&OrderByOrientation=Descending"
    )

    fy_start, fy_end = last_completed_fy_bounds()
    tot_div_cash = tot_fran_cash = 0.0
    dbg_rows = []

    for soup in get_all_pages(base_url):
        for tr in soup.select("table tbody tr"):
            stats = row_stats(tr)
            if not stats:
                continue
            exd, amt, fran_pct = stats
            inside = fy_start <= exd <= fy_end
            if inside:
                tot_div_cash += amt
                tot_fran_cash += amt * (fran_pct / 100.0)

            if debug:
                dbg_rows.append(
                    {
                        "ex": exd.isoformat(),
                        "amt": amt,
                        "fran%": fran_pct,
                        "inside": inside,
                    }
                )

    if debug:
        return {
            "fy": f"{fy_start}→{fy_end}",
            "total_dividend": round(tot_div_cash, 6),
            "weighted_fran%": 0
            if tot_div_cash == 0
            else round(tot_fran_cash / tot_div_cash * 100, 2),
            "rows": dbg_rows,
        }

    if tot_div_cash == 0:
        return None, None
    return round(tot_div_cash, 6), round((tot_fran_cash / tot_div_cash) * 100, 2)


# ------------------------------------------------------------------ #
# Flask routes
# ------------------------------------------------------------------ #
@app.route("/")
def home():
    return (
        "Stock API Proxy – call /stock?symbol=<CODE>  (e.g. /stock?symbol=VHY)",
        200,
    )


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base = symbol.split(".")[0]

    # Live price ---------------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # Dividends ----------------------------------------------------------
    if "debug" in request.args:
        return jsonify(fetch_dividend_stats(base, debug=True)), 200

    cash_div, franking = fetch_dividend_stats(base)
    return jsonify(symbol=symbol, price=price, dividend12=cash_div, franking=franking)


# ------------------------------------------------------------------ #
if __name__ == "__main__":
    # Local dev:  python app.py
    app.run(host="0.0.0.0", port=8080, debug=False)
