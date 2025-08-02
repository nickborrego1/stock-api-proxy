# app.py — ASX dividend proxy (InvestSMART, pagination-aware)
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urljoin

import re, requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
CORS(app)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


# ────────────────────────────────── helpers ──────────────────────────────────
def normalise(raw: str) -> str:
    """Translate e.g. 'vhy' → 'VHY.AX'."""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def previous_fy_bounds(today: date | None = None) -> tuple[date, date]:
    """Return start & end (inclusive) of the *previous* Australian FY."""
    today = today or datetime.utcnow().date()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def parse_exdate(txt: str) -> date | None:
    """Robust date reader – accepts most formats InvestSMART emits."""
    txt = (
        txt.replace("\u00a0", " ")       # NB-space
           .replace("\u2011", "-")       # figure dash
           .replace("\u2012", "-")       # en dash variants
           .replace("\u2013", "-")
           .replace("\u2014", "-")
           .strip()
    )
    # First try a few exact strptime patterns (cheap)
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y",
                "%d/%m/%Y", "%d %b %y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    # Fall back to dateutil
    try:
        return dtparser.parse(txt, dayfirst=True).date()
    except Exception:
        return None


def clean_amount(cell: str) -> float | None:
    """Return the cash value per share in **dollars** (so 62 ¢ ➜ 0.62)."""
    t = (
        cell.replace("\u00a0", "")
            .replace(" ", "")
            .replace("$", "")
            .replace(",", "")
            .strip()
            .lower()
    )
    # Look for cent suffixes
    for suf in ("cpu", "c", "¢"):
        if t.endswith(suf):
            try:
                return float(t[:-len(suf)]) / 100.0
            except ValueError:
                return None
    try:
        return float(t)
    except ValueError:
        return None


# ────────────────────────────── scraping core ───────────────────────────────
def wanted_table(tbl) -> bool:
    hdr = " ".join(th.get_text(strip=True).lower() for th in tbl.find_all("th"))
    return "ex" in hdr and "dividend" in hdr and "date" in hdr


def header_index(headers: list[str], *needles: str) -> int | None:
    for n in needles:
        for i, h in enumerate(headers):
            if n in h:
                return i
    return None


def get_all_pages(start_url: str) -> list[BeautifulSoup]:
    """Follow `»` pagination links and return BeautifulSoup for every page."""
    soups, next_url = [], start_url
    while next_url:
        html = requests.get(next_url,
                            headers={"User-Agent": UA},
                            timeout=15).text
        soup = BeautifulSoup(html, "html.parser")
        soups.append(soup)

        # InvestSMART uses the right-chevron link without rel="next"
        nxt = soup.select_one(".pagination a:contains('»')")
        next_url = urljoin(start_url, nxt["href"]) if nxt else None
    return soups


def fetch_dividend_stats(code: str, debug: bool = False):
    # Up to 250 rows on one page; if more, pagination still works.
    base_url = (f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
                f"?size=250&OrderBy=6&OrderByOrientation=Descending")
    soups = get_all_pages(base_url)

    fy_start, fy_end = previous_fy_bounds()
    cash, franked_cash = 0.0, 0.0
    dbg_rows = []

    for soup in soups:
        for tbl in (t for t in soup.find_all("table") if wanted_table(t)):
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            ex_i   = header_index(hdrs, "ex")             # ex-dividend date
            div_i  = header_index(hdrs, "dividend", "distribution", "payout")
            fran_i = header_index(hdrs, "franking")

            if ex_i is None or div_i is None:
                continue   # skip unexpected tables

            for tr in tbl.find_all("tr")[1:]:             # row 0 = header
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]

                # Pad if row is shorter than expected (defensive)
                while len(cells) <= max(ex_i, div_i, fran_i or 0):
                    cells.append("")

                exd = parse_exdate(cells[ex_i])
                amt = clean_amount(cells[div_i])
                try:
                    fr_pct = float(re.sub(r"[^\d.]", "", cells[fran_i])) if fran_i is not None else 0.0
                except ValueError:
                    fr_pct = 0.0

                inside = bool(exd and amt and fy_start <= exd <= fy_end)
                if inside:
                    cash += amt
                    franked_cash += amt * (fr_pct / 100.0)

                if debug:
                    dbg_rows.append(
                        dict(ex=cells[ex_i],
                             parsed=str(exd),
                             amt=cells[div_i],
                             amt_ok=amt is not None,
                             fran=fr_pct,
                             in_FY=inside)
                    )

    if debug:
        return dict(tot_cash=round(cash, 6),
                    tot_fran=0 if cash == 0 else round(franked_cash / cash * 100, 2),
                    rows=dbg_rows)

    return (None, None) if cash == 0 else (
        round(cash, 6),
        round(franked_cash / cash * 100, 2)
    )


# ────────────────────────────── Flask layer ────────────────────────────────
@app.route("/")
def home():
    return "Stock API Proxy – /stock?symbol=CODE", 200


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]          # e.g. VHY

    if "debug" in request.args:
        return jsonify(fetch_dividend_stats(base, debug=True)), 200

    # Spot price -------------------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # Dividends --------------------------------------------------------------
    dividend12, franking = fetch_dividend_stats(base)
    return jsonify(symbol=symbol,
                   price=price,
                   dividend12=dividend12,
                   franking=franking)


if __name__ == "__main__":
    # gunicorn / render.com will override host/port; local dev only
    app.run(host="0.0.0.0", port=8080, debug=False)
