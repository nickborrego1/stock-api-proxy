# app.py — ASX dividend + franking API (FY-aware)

from __future__ import annotations
from datetime import datetime, date
from typing import Optional
from urllib.parse import urljoin

import re, requests, pandas as pd, yfinance as yf
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
ROWS_PER_PAGE = 250  # InvestSMART max


# ───────────────────────── helpers ─────────────────────────
def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def last_completed_fy_bounds(today: Optional[date] = None) -> tuple[date, date]:
    """Return start/end of the *last finished* Australian FY."""
    today = today or datetime.utcnow().date()
    # If we’re before 1 Jul, the finished FY is two years back
    fy_end_year = today.year if today.month < 7 else today.year - 1
    return date(fy_end_year - 1, 7, 1), date(fy_end_year, 6, 30)


def parse_exdate(txt: str) -> Optional[date]:
    txt = txt.replace("\xa0", " ").strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d/%m/%Y", "%d %b %y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    try:
        return dtparser.parse(txt, dayfirst=True).date()
    except Exception:
        return None


def extract_float(txt: str) -> Optional[float]:
    try:
        return float(re.sub(r"[^\d.]", "", txt))
    except ValueError:
        return None


# ───────────────────────── scraping ────────────────────────
def wanted_table(tbl) -> bool:
    hdr = " ".join(th.get_text(strip=True).lower() for th in tbl.find_all("th"))
    return all(key in hdr for key in ("ex", "dividend", "franking"))


def header_idx(headers: list[str], name: str) -> int | None:
    name = name.lower()
    for i, h in enumerate(headers):
        if h.strip() == name:
            return i
    for i, h in enumerate(headers):
        if name in h:
            return i
    return None


def all_pages(url: str) -> list[BeautifulSoup]:
    soups, next_url = [], url
    sess = requests.Session()
    sess.headers["User-Agent"] = UA

    while next_url:
        html = sess.get(next_url, timeout=15).text
        soup = BeautifulSoup(html, "html.parser")
        soups.append(soup)

        nxt_li = soup.find("li", class_=lambda c: c and "next" in c.lower())
        nxt_a = nxt_li.a if nxt_li and nxt_li.a else soup.find("a", rel="next")
        next_url = urljoin(url, nxt_a["href"]) if nxt_a and nxt_a.get("href") else None
    return soups


def franking_by_date(code: str, fy_start: date, fy_end: date) -> dict[date, float]:
    """Return {ex-date: franking %} for the FY from InvestSMART pages."""
    base = (
        f"https://www.investsmart.com.au/shares/asx-{code}/dividends"
        f"?size={ROWS_PER_PAGE}&OrderBy=6&OrderByOrientation=Descending"
    )
    result: dict[date, float] = {}

    for soup in all_pages(base):
        for tbl in (t for t in soup.find_all("table") if wanted_table(t)):
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            ex_i = header_idx(hdrs, "ex")
            fran_i = header_idx(hdrs, "franking")
            dist_i = header_idx(hdrs, "distribution")  # where variable width begins
            if ex_i is None or fran_i is None:
                continue

            for tr in tbl.find("tbody").find_all("tr"):
                tds = tr.find_all("td")
                if not tds:
                    continue

                shift = len(tds) - len(hdrs)

                def adj(idx: int) -> int:
                    return idx + shift if shift and dist_i is not None and idx > dist_i else idx

                exd = parse_exdate(tds[adj(ex_i)].get_text())
                if not exd or not (fy_start <= exd <= fy_end):
                    continue

                fr_pct = extract_float(tds[adj(fran_i)].get_text()) or 0.0
                result[exd] = fr_pct
    return result


# ───────────────────────── finance calc ────────────────────
def fy_dividends_yf(symbol: str, fy_start: date, fy_end: date) -> pd.Series:
    """Return Series indexed by date with cash amounts inside the FY."""
    s = yf.Ticker(symbol).dividends
    if s.empty:
        return pd.Series(dtype=float)
    s.index = s.index.date
    return s.loc[(s.index >= fy_start) & (s.index <= fy_end)]


def combined_stats(symbol: str) -> tuple[float | None, float | None]:
    fy_start, fy_end = last_completed_fy_bounds()

    # 1) cash from Yahoo Finance
    cash_series = fy_dividends_yf(symbol, fy_start, fy_end)
    if cash_series.empty:
        return None, None
    cash_total = cash_series.sum()

    # 2) franking % from InvestSMART
    fr_dict = franking_by_date(symbol.split(".")[0].lower(), fy_start, fy_end)

    frank_cash = 0.0
    for exd, amt in cash_series.items():
        pct = fr_dict.get(exd, 0.0)
        frank_cash += amt * (pct / 100)

    frank_pct = 0 if cash_total == 0 else round(frank_cash / cash_total * 100, 2)
    return round(cash_total, 6), frank_pct


# ───────────────────────── API layer ───────────────────────
@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="no symbol"), 400

    symbol = normalise(raw)

    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"price fetch failed: {e}"), 500

    div12, frank_pct = combined_stats(symbol)
    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=div12,
        franking=frank_pct,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
