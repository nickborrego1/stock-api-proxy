# app.py — ASX dividend proxy (InvestSMART, robust pagination & row-shift handling)
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urljoin
from typing import Optional

import re, requests
from bs4 import BeautifulSoup, Tag
from dateutil import parser as dtparser
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
CORS(app)

UA            = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0 Safari/537.36")
ROWS_PER_PAGE = 250


# ───────────────────────── helpers ─────────────────────────
def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def previous_fy_bounds(today: Optional[date] = None) -> tuple[date, date]:
    today = today or datetime.utcnow().date()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def parse_exdate(txt: str) -> Optional[date]:
    txt = (
        txt.replace("\u00a0", " ")
           .replace("\u2011", "-").replace("\u2012", "-")
           .replace("\u2013", "-").replace("\u2014", "-")
           .strip()
    )
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y",
                "%d/%m/%Y", "%d %b %y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    try:
        return dtparser.parse(txt, dayfirst=True).date()
    except Exception:
        return None


def _parse_num(num: str, cents_hint: bool) -> Optional[float]:
    try:
        val = float(num)
        return val / 100.0 if cents_hint else val
    except ValueError:
        return None


def clean_amount_cell(td: Tag) -> Optional[float]:
    txt = td.get_text(" ", strip=True)
    amt = _parse_num(txt.lower().replace("$", ""), "c" in txt.lower())
    if amt is not None:
        return amt

    # fallback – look at raw HTML
    html = td.decode_contents()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(cpu|c|¢)?", html, flags=re.I)
    if not m:
        return None
    num, unit = m.group(1), (m.group(2) or "").lower()
    return _parse_num(num, unit in ("cpu", "c", "¢"))


# ───────────────────── scraping core ──────────────────────
def wanted_table(tbl) -> bool:
    hdr = " ".join(th.get_text(strip=True).lower() for th in tbl.find_all("th"))
    return "ex" in hdr and "dividend" in hdr and "date" in hdr


def header_index(headers: list[str], *needles: str) -> Optional[int]:
    """Exact header match preferred; fallback to substring."""
    needles = [n.lower() for n in needles]

    for n in needles:          # exact
        for i, h in enumerate(headers):
            if h.strip() == n:
                return i
    for n in needles:          # substring
        for i, h in enumerate(headers):
            if n in h:
                return i
    return None


def get_all_pages(start_url: str) -> list[BeautifulSoup]:
    """Follow ‘next’ pagination links until exhausted."""
    soups, next_url = [], start_url
    sess = requests.Session()
    sess.headers["User-Agent"] = UA

    while next_url:
        html = sess.get(next_url, timeout=15).text
        soup = BeautifulSoup(html, "html.parser")
        soups.append(soup)

        # look for <li class="next"> or <a rel="next">
        nxt_li = soup.find("li", class_=lambda c: c and "next" in c.lower())
        nxt_a  = (nxt_li.a if nxt_li and nxt_li.a else
                  soup.find("a", rel=lambda r: r and "next" in r.lower()))
        next_url = urljoin(start_url, nxt_a["href"]) if nxt_a and nxt_a.get("href") else None

    return soups


def fetch_dividend_stats(code: str, debug: bool = False):
    base_url = (f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
                f"?size={ROWS_PER_PAGE}&OrderBy=6&OrderByOrientation=Descending")
    soups = get_all_pages(base_url)

    fy_start, fy_end = previous_fy_bounds()
    cash = franked_cash = 0.0
    dbg_rows = []

    for soup in soups:
        for tbl in (t for t in soup.find_all("table") if wanted_table(t)):
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            ex_i   = header_index(hdrs, "ex")
            div_i  = header_index(hdrs, "dividend")
            fran_i = header_index(hdrs, "franking")

            if ex_i is None or div_i is None:
                continue

            for tr in tbl.find_all("tr")[1:]:
                tds = tr.find_all(["td", "th"])

                # Detect rows that have extra leading cells (page puts "H F Result" etc.)
                shift = max(0, len(tds) - len(hdrs))

                def adj(idx: int) -> int:
                    # extra cells appear before the Dividend column
                    return idx + shift if shift and idx >= div_i else idx

                exd = parse_exdate(tds[adj(ex_i)].get_text(" ", strip=True))
                amt = clean_amount_cell(tds[adj(div_i)])

                fran_txt = (tds[adj(fran_i)].get_text(" ", strip=True)
                            if fran_i is not None else "")
                try:
                    fr_pct = float(re.sub(r"[^\d.]", "", fran_txt)) if fran_txt else 0.0
                except ValueError:
                    fr_pct = 0.0

                inside = bool(exd and amt and fy_start <= exd <= fy_end)
                if inside:
                    cash += amt
                    franked_cash += amt * (fr_pct / 100.0)

                if debug:
                    dbg_rows.append({
                        "ex": tds[adj(ex_i)].get_text(" ", strip=True),
                        "parsed": str(exd),
                        "amt": tds[adj(div_i)].get_text(" ", strip=True),
                        "amt_ok": amt is not None,
                        "fran%": fr_pct,
                        "in_FY": inside,
                    })

    if debug:
        return {
            "tot_cash": round(cash, 6),
            "tot_fran": 0 if cash == 0 else round(franked_cash / cash * 100, 2),
            "rows": dbg_rows,
        }

    return (None, None) if cash == 0 else (
        round(cash, 6),
        round(franked_cash / cash * 100, 2)
    )


# ────────────────────── Flask layer ───────────────────────
@app.route("/")
def home():
    return "Stock API Proxy – /stock?symbol=CODE", 200


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    if "debug" in request.args:
        return jsonify(fetch_dividend_stats(base, debug=True)), 200

    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend12, franking = fetch_dividend_stats(base)
    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
