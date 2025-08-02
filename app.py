# app.py — ASX dividend proxy (InvestSMART, pagination-aware, FY-correct)
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlparse, parse_qsl, urlencode
from typing import Optional

import re
import requests
from bs4 import BeautifulSoup, Tag
from dateutil import parser as dtparser
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
CORS(app)

UA           = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0 Safari/537.36")
ROWS_PER_PAGE = 250    # maximum the site allows


# ───────────────────────── helpers ─────────────────────────
def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def previous_fy_bounds(today: Optional[date] = None) -> tuple[date, date]:
    today = today or datetime.utcnow().date()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def parse_exdate(txt: str) -> Optional[date]:
    txt = (txt.replace("\u00a0", " ")
              .replace("\u2011", "-").replace("\u2012", "-")
              .replace("\u2013", "-").replace("\u2014", "-")
              .strip())
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
        v = float(num)
        return v / 100.0 if cents_hint else v
    except ValueError:
        return None


def clean_amount_cell(td: Tag) -> Optional[float]:
    """
    Robustly extract the dividend cash amount from a <td>.
    Handles $-prefixed dollars, ¢, ‘cpu’, stray&nbsp;s, etc.
    """
    txt = td.get_text(" ", strip=True)
    amt = _parse_num(txt.lower().replace("$", ""), "c" in txt.lower())
    if amt is not None:
        return amt

    # Fallback: search raw HTML
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
    needles = [n.lower() for n in needles]

    # 1. exact match
    for n in needles:
        for i, h in enumerate(headers):
            if h.strip() == n:
                return i

    # 2. substring fallback
    for n in needles:
        for i, h in enumerate(headers):
            if n in h:
                return i
    return None


def rows_in_tables(soup: BeautifulSoup) -> int:
    return sum(len(t.find_all("tr")) - 1
               for t in soup.find_all("table") if wanted_table(t))


def get_all_pages(start_url: str) -> list[BeautifulSoup]:
    soups, page = [], 1
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    while True:
        u = urlparse(start_url)
        q = dict(parse_qsl(u.query))
        q["page"] = str(page)
        url = u._replace(query=urlencode(q, doseq=True)).geturl()

        r = session.get(url, timeout=15)
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        soups.append(soup)

        if rows_in_tables(soup) < ROWS_PER_PAGE:
            break
        page += 1
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
            dist_i = header_index(hdrs, "distribution") or 2  # used for offset logic

            if ex_i is None or div_i is None:
                continue

            for tr in tbl.find_all("tr")[1:]:
                tds = tr.find_all(["td", "th"])
                # Adjust for rows where 'Distribution Type' is split into extra <td>s
                shift = max(0, len(tds) - len(hdrs))
                def adj(idx: int) -> int:
                    return idx + shift if shift and idx > dist_i else idx

                try:
                    exd = parse_exdate(tds[adj(ex_i)].get_text(" ", strip=True))
                except IndexError:
                    exd = None

                amt = clean_amount_cell(tds[adj(div_i)]) if len(tds) > adj(div_i) else None

                fran_txt = (tds[adj(fran_i)].get_text(" ", strip=True)
                            if fran_i is not None and len(tds) > adj(fran_i) else "")
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
                        "ex": tds[adj(ex_i)].get_text(" ", strip=True) if len(tds) > adj(ex_i) else "",
                        "parsed": str(exd),
                        "amt": tds[adj(div_i)].get_text(" ", strip=True) if len(tds) > adj(div_i) else "",
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
