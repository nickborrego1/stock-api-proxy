from __future__ import annotations
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf
from bs4 import BeautifulSoup, Tag
from datetime import datetime, date
from dateutil import parser as dtparser
from urllib.parse import urljoin

app = Flask(__name__)
CORS(app)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
ROWS_PER_PAGE = 250  # max InvestSMART allows


# ───────────────────────── helpers ─────────────────────────
def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def previous_fy_bounds(today: date | None = None) -> tuple[date, date]:
    today = today or datetime.utcnow().date()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def parse_exdate(txt: str) -> date | None:
    txt = txt.replace("\xa0", " ").strip()          # squash &nbsp;
    for fmt in ("%d %b %Y", "%d %b %y", "%d %B %Y",
                "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    try:
        return dtparser.parse(txt, dayfirst=True).date()
    except Exception:
        return None


def clean_amount(cell_text: str) -> float | None:
    t = (cell_text.replace("\xa0", "")
                  .replace(" ", "")
                  .replace("$", "")
                  .strip())
    if t.lower().endswith(("c", "¢")):
        t = t[:-1]
        try:
            return float(t) / 100.0
        except ValueError:
            return None
    try:
        return float(t)
    except ValueError:
        return None


# ───────────────────── scraping core ──────────────────────
def wanted_table(tbl) -> bool:
    hdr = " ".join(th.get_text(strip=True).lower() for th in tbl.find_all("th"))
    return "dividend" in hdr and "franking" in hdr and "ex" in hdr


def header_index(headers: list[str], *needles: str) -> int | None:
    needles = [n.lower() for n in needles]
    # exact match first
    for n in needles:
        for i, h in enumerate(headers):
            if h.strip() == n:
                return i
    # substring fallback
    for n in needles:
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

        nxt_li = soup.find("li", class_=lambda c: c and "next" in c.lower())
        nxt_a  = nxt_li.a if nxt_li and nxt_li.a else soup.find("a", rel="next")
        next_url = urljoin(start_url, nxt_a["href"]) if nxt_a and nxt_a.get("href") else None

    return soups


def fetch_dividend_stats(code: str) -> tuple[float | None, float | None]:
    base_url = (f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
                f"?size={ROWS_PER_PAGE}&OrderBy=6&OrderByOrientation=Descending")
    soups = get_all_pages(base_url)

    fy_start, fy_end = previous_fy_bounds()
    cash = fran_cash = 0.0

    for soup in soups:
        for tbl in (t for t in soup.find_all("table") if wanted_table(t)):
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            ex_i   = header_index(hdrs, "ex")
            div_i  = header_index(hdrs, "dividend", "amount")
            fran_i = header_index(hdrs, "franking")
            dist_i = header_index(hdrs, "distribution")  # first variable-width col

            if None in (ex_i, div_i, fran_i):
                continue

            for tr in tbl.find("tbody").find_all("tr"):
                tds = tr.find_all(["td", "th"])
                if not tds:
                    continue
                shift = len(tds) - len(hdrs)

                def adj(idx: int) -> int:
                    return idx + shift if shift and dist_i is not None and idx > dist_i else idx

                if adj(ex_i) >= len(tds) or adj(div_i) >= len(tds):
                    continue

                exd = parse_exdate(tds[adj(ex_i)].get_text())
                if not exd or not (fy_start <= exd <= fy_end):
                    continue

                amt = clean_amount(tds[adj(div_i)].get_text())
                if amt is None:
                    continue

                try:
                    fr_pct = float(re.sub(r"[^\d.]", "", tds[adj(fran_i)].get_text()))
                except ValueError:
                    fr_pct = 0.0

                cash      += amt
                fran_cash += amt * (fr_pct / 100.0)

    if cash == 0:
        return None, None
    return round(cash, 6), round(fran_cash / cash * 100, 2)


# ────────────────────── Flask layer ───────────────────────
@app.route("/")
def home():
    return "Stock API Proxy – /stock?symbol=CODE  (e.g. /stock?symbol=VHY)", 200


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend, franking = fetch_dividend_stats(base)
    return jsonify(symbol=symbol,
                   price=price,
                   dividend12=dividend,
                   franking=franking)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
