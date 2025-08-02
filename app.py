# app.py — ASX dividend proxy (InvestSMART, pagination-aware – *fixed*)
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urljoin

import re
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

PAGE_SIZE = 250  # use the largest size InvestSMART allows


# ───────── helpers ──────────────────────────────────────────────────────────
def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def previous_fy_bounds(today: date | None = None) -> tuple[date, date]:
    today = today or datetime.utcnow().date()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def parse_exdate(txt: str) -> date | None:
    txt = (
        txt.replace("\u00a0", " ")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .strip()
    )
    for fmt in (
        "%d %b %Y",
        "%d %B %Y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d/%m/%Y",
        "%d %b %y",
    ):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    try:
        return dtparser.parse(txt, dayfirst=True).date()
    except Exception:
        return None


def clean_amount(cell: str) -> float | None:
    t = (
        cell.replace("\u00a0", "")
        .replace(" ", "")
        .replace("$", "")
        .strip()
        .lower()
    )
    for suffix in ("cpu", "c", "¢"):
        if t.endswith(suffix):
            try:
                return float(t[: -len(suffix)]) / 100.0
            except ValueError:
                return None
    try:
        return float(t)
    except ValueError:
        return None


# ───────── scrape core ──────────────────────────────────────────────────────
HEADERS_PAYOUT = ("dividend", "amount", "distribution", "dist", "payout", "(cpu)")


def wanted_table(tbl) -> bool:
    text = " ".join(th.get_text(strip=True).lower() for th in tbl.find_all("th"))
    return "date" in text and any(k in text for k in HEADERS_PAYOUT)


def col_idx(headers: list[str], *keys: str) -> int | None:
    for k in keys:
        for i, h in enumerate(headers):
            if k in h:
                return i
    return None


def get_all_pages(start_url: str) -> list[BeautifulSoup]:
    """
    Follow InvestSMART pagination links (?page=N&size=…) and return a soup per page.
    Two fall-backs are used because the html differs when JS is disabled.
    """
    soups: list[BeautifulSoup] = []
    next_url: str | None = start_url

    while next_url:
        html = requests.get(
            next_url, headers={"User-Agent": UA}, timeout=15
        ).text
        soup = BeautifulSoup(html, "html.parser")
        soups.append(soup)

        # 1️⃣ First, look for a rel="next" anchor (what we *wish* they used)
        nxt = soup.select_one(".pagination a[rel='next']")

        # 2️⃣ Fallback: the <li class="active"> + next <li>
        if not nxt:
            nxt = soup.select_one(".pagination li.active + li a")

        next_url = (
            urljoin(start_url, nxt["href"]) if nxt and "href" in nxt.attrs else None
        )

    return soups


def fetch_dividend_stats(code: str, debug: bool = False):
    # Ask for the *largest* page size so we get every row on page 1
    base_url = (
        f"https://www.investsmart.com.au/shares/asx-{code.lower()}"
        f"/dividends?size={PAGE_SIZE}"
    )
    soups = get_all_pages(base_url)

    fy_start, fy_end = previous_fy_bounds()
    tot_cash = tot_fran_cash = 0.0
    rows = []

    for soup in soups:
        tables = [t for t in soup.find_all("table") if wanted_table(t)]
        for tbl in tables:
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            ex_i = col_idx(hdrs, "ex")
            div_i = col_idx(hdrs, *HEADERS_PAYOUT)
            fran_i = col_idx(hdrs, "franking")

            if ex_i is None or div_i is None:
                # table isn’t the one we want
                continue

            for tr in tbl.find_all("tr")[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                # pad to the longest index we might read
                need = max(ex_i, div_i, fran_i or 0) + 1
                cells.extend([""] * (need - len(cells)))

                ex_raw, amt_raw = cells[ex_i], cells[div_i]
                exd = parse_exdate(ex_raw)
                amt = clean_amount(amt_raw)
                fpc = (
                    float(re.sub(r"[^\d.]", "", cells[fran_i]))
                    if fran_i is not None
                    else 0.0
                )

                inside = all([exd, amt]) and fy_start <= exd <= fy_end
                if inside:
                    tot_cash += amt
                    tot_fran_cash += amt * (fpc / 100.0)

                if debug:
                    rows.append(
                        {
                            "ex": ex_raw,
                            "parsed": str(exd),
                            "amt": amt_raw,
                            "amt_ok": amt is not None,
                            "fran%": fpc,
                            "in_FY": inside,
                        }
                    )

    if debug:
        return {
            "tot_cash": round(tot_cash, 6),
            "tot_fran": 0
            if tot_cash == 0
            else round(tot_fran_cash / tot_cash * 100, 2),
            "rows": rows,
        }

    if tot_cash == 0:
        return None, None
    return round(tot_cash, 6), round(tot_fran_cash / tot_cash * 100, 2)


# ───────── Flask layer ──────────────────────────────────────────────────────
@app.route("/")
def home():
    return "Stock API Proxy – /stock?symbol=CODE", 200


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base = symbol.split(".")[0]

    # Debug mode: return the raw scrape output
    if "debug" in request.args:
        return jsonify(fetch_dividend_stats(base, debug=True)), 200

    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend12, franking = fetch_dividend_stats(base)
    return jsonify(
        symbol=symbol, price=price, dividend12=dividend12, franking=franking
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
