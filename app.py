# app.py — ASX dividend + franking API (pagination-safe, FY-correct)

from __future__ import annotations
from datetime import datetime, date
from typing import Optional
from urllib.parse import urljoin
import re, requests, yfinance as yf
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
ROWS_PER_PAGE = 250           # InvestSMART max

# ───────────────────────── helpers ─────────────────────────
def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def last_completed_fy_bounds(today: Optional[date] = None) -> tuple[date, date]:
    today = today or datetime.utcnow().date()
    if today.month >= 7:                     # Jul-Dec → FY ended 30 Jun this year
        start_year, end_year = today.year - 1, today.year
    else:                                    # Jan-Jun → FY ended 30 Jun last year
        start_year, end_year = today.year - 2, today.year - 1
    return date(start_year, 7, 1), date(end_year, 6, 30)

def parse_exdate(txt: str) -> Optional[date]:
    txt = (txt.replace("\u00a0", " ")
              .replace("\u2011", "-").replace("\u2012", "-")
              .replace("\u2013", "-").replace("\u2014", "-")
              .strip())
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d/%m/%Y", "%d %b %y"):
        try:  return datetime.strptime(txt, fmt).date()
        except ValueError:  pass
    try:  return dtparser.parse(txt, dayfirst=True).date()
    except Exception:  return None

_WS = re.compile(r"\s+")
def looks_like_money(raw: str) -> bool:
    r = raw.lower()
    return any(t in r for t in ("$", "¢", " cpu", "cents", " cent", "c ")) or r.endswith("c")

def clean_amount(raw: str) -> Optional[float]:
    t = (_WS.sub("", raw).replace("$", "").replace(",", "").lower())
    for suf in ("cpu", "cents", "cent", "¢", "c"):
        if t.endswith(suf):
            t = t[:-len(suf)]
            try:  return float(t) / 100.0
            except ValueError:  return None
    try:  return float(t)
    except ValueError:  return None

# ───────────────────── scraping core ──────────────────────
def get_pages(start: str) -> list[BeautifulSoup]:
    soups, url = [], start
    session = requests.Session(); session.headers["User-Agent"] = UA
    while url:
        html = session.get(url, timeout=15).text
        soup = BeautifulSoup(html, "html.parser")
        soups.append(soup)
        nxt = soup.select_one(".pagination a:contains('»'), a[rel='next']")
        url = urljoin(start, nxt["href"]) if nxt and nxt.get("href") else None
    return soups

def row_stats(tr) -> Optional[tuple[date, float, float]]:
    tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
    if not tds:  return None
    # find ex-date cell
    ex_idx, exd = next(((i, d) for i, d in ((i, parse_exdate(x)) for i,x in enumerate(tds)) if d), (None, None))
    if ex_idx is None:  return None
    # franking % is first cell with '%' before ex-date
    fran_pct = 0.0
    for i in range(ex_idx - 1, -1, -1):
        if "%" in tds[i]:
            fran_pct = float(re.sub(r"[^\d.]", "", tds[i]) or 0)
            break
    # dividend = first money-looking cell before franking / ex-date
    stop = i if "%" in tds[i] else ex_idx
    for j in range(stop - 1, -1, -1):
        if looks_like_money(tds[j]):
            amt = clean_amount(tds[j])
            if amt is not None:  return exd, amt, fran_pct
    return None

def fy_stats(code: str, fy_start: date, fy_end: date) -> tuple[float, float]:
    url = (f"https://www.investsmart.com.au/shares/asx-{code}/dividends"
           f"?size={ROWS_PER_PAGE}&OrderBy=6&OrderByOrientation=Descending")
    cash = fran_cash = 0.0
    for soup in get_pages(url):
        for tr in soup.select("table tbody tr"):
            r = row_stats(tr)
            if not r:  continue
            exd, amt, pct = r
            if fy_start <= exd <= fy_end:
                cash += amt
                fran_cash += amt * (pct / 100.0)
    return cash, fran_cash

# ───────────────────────── API layer ───────────────────────
@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():  return jsonify(error="no symbol"), 400
    symbol = normalise(raw); base = symbol.split(".")[0].lower()

    try:  price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:  return jsonify(error=f"price fetch failed: {e}"), 500

    fy_start, fy_end = last_completed_fy_bounds()
    div_cash, fran_cash = fy_stats(base, fy_start, fy_end)
    if div_cash == 0:
        return jsonify(symbol=symbol, price=price,
                       dividend12=None, franking=None)

    return jsonify(symbol=symbol,
                   price=price,
                   dividend12=round(div_cash, 6),
                   franking=round(fran_cash / div_cash * 100, 2))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
