# app.py – ASX dividend proxy (InvestSMART first, Yahoo fallback)
from __future__ import annotations

import logging, os, re, requests, yfinance as yf
from datetime import datetime, date, timedelta
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

# ────────────────────────────────── Flask & logging ──────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "dev-secret-key-change-in-production")
CORS(app)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# ────────────────────────────────── helpers ──────────────────────────────────
def most_recent_completed_fy(today: date | None = None) -> tuple[date, date]:
    """Return start/end (inclusive) of the most-recent completed Australian FY."""
    today = today or datetime.utcnow().date()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def parse_exdate(txt: str) -> date | None:
    """Robust date parser for InvestSMART cells."""
    txt = (txt.replace("\u00a0", " ")
              .replace("\u2011", "-").replace("\u2012", "-")
              .replace("\u2013", "-").replace("\u2014", "-")
              .strip())
    for fmt in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(txt, dayfirst=True).date()
    except Exception:
        return None


def clean_amount(cell: str) -> float | None:
    """Convert '$1.04', '62¢', '61.79c', '48.21 CPU' → dollars float."""
    t = (cell.replace("\u00a0", "")
            .replace(" ", "")
            .replace("$", "")
            .replace(",", "")
            .lower())
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


# ───────────────────────────── InvestSMART scraper ───────────────────────────
def _wanted_table(tbl) -> bool:
    hdr = " ".join(th.get_text(strip=True).lower() for th in tbl.find_all("th"))
    return "ex" in hdr and "dividend" in hdr


def _header_index(headers: list[str], *needles: str) -> int | None:
    for n in needles:
        for i, h in enumerate(headers):
            if n in h:
                return i
    return None


def _get_all_pages(start_url: str) -> list[BeautifulSoup]:
    soups, next_url = [], start_url
    while next_url:
        html = requests.get(next_url,
                            headers={"User-Agent": UA},
                            timeout=15).text
        soup = BeautifulSoup(html, "html.parser")
        soups.append(soup)

        nxt = soup.select_one(".pagination a:contains('»')")
        next_url = urljoin(start_url, nxt["href"]) if nxt else None
    return soups


def investsmart_dividends(code: str,
                          fy_start: date,
                          fy_end: date) -> tuple[float, float, list[dict]]:
    """
    Return (total_cash, weighted_franking%, rows[]) for ex-dates falling inside
    [fy_start, fy_end].  rows is a list of {'date','amount','franking'} for debug.
    """
    base_url = (f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
                "?size=250&OrderBy=6&OrderByOrientation=Descending")
    soups = _get_all_pages(base_url)

    cash = franked_cash = 0.0
    rows = []

    for soup in soups:
        for tbl in (t for t in soup.find_all("table") if _wanted_table(t)):
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            ex_i   = _header_index(hdrs, "ex")
            div_i  = _header_index(hdrs, "dividend", "distribution")
            fran_i = _header_index(hdrs, "franking")

            if ex_i is None or div_i is None:
                continue

            for tr in tbl.find_all("tr")[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                while len(cells) <= max(ex_i, div_i, fran_i or 0):
                    cells.append("")

                exd = parse_exdate(cells[ex_i])
                amt = clean_amount(cells[div_i])
                try:
                    fran_pct = float(re.sub(r"[^\d.]", "", cells[fran_i])) if fran_i is not None else 0.0
                except ValueError:
                    fran_pct = 0.0

                if exd and amt and fy_start <= exd <= fy_end:
                    cash += amt
                    franked_cash += amt * (fran_pct / 100.0)
                    rows.append(dict(date=str(exd), amount=amt, franking=fran_pct))

    if cash == 0:
        return 0.0, 0.0, []

    return round(cash, 6), round(franked_cash / cash * 100, 2), rows


# ───────────────────────────── Yahoo fallback / price ────────────────────────
def yahoo_dividends(symbol: str,
                    fy_start: date,
                    fy_end: date) -> tuple[float, list[dict]]:
    if not symbol.endswith(".AX"):
        symbol += ".AX"

    end_buffer = fy_end + timedelta(days=30)
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=fy_start, end=end_buffer, actions=True)

    if "Dividends" not in hist.columns:
        return 0.0, []

    divs = hist["Dividends"][hist["Dividends"] > 0]
    divs = divs[(divs.index >= pd.Timestamp(fy_start)) &
                (divs.index <= pd.Timestamp(fy_end))]

    rows = [{"date": d.strftime("%Y-%m-%d"), "amount": float(a)}
            for d, a in divs.items()]
    return float(divs.sum()), rows


def current_price(symbol: str) -> float | None:
    if not symbol.endswith(".AX"):
        symbol += ".AX"
    info = yf.Ticker(symbol).fast_info
    return info.get("lastPrice") or info.get("regularMarketPrice")


# ───────────────────────────── Default franking table ────────────────────────
DEFAULT_FRANKING = {
    'VHY': 33.49, 'CBA': 100.0, 'WBC': 100.0, 'ANZ': 100.0, 'NAB': 100.0,
    'BHP': 100.0, 'RIO': 100.0, 'CSL': 0.0,   'WOW': 100.0, 'COL': 100.0,
    'TLS': 100.0, 'WES': 100.0, 'TCL': 100.0, 'MQG': 100.0, 'STO': 100.0,
    'FMG': 100.0, 'QBE': 100.0, 'IAG': 100.0, 'SUN': 100.0, 'AMP': 100.0
}


# ────────────────────────────────── routes ───────────────────────────────────
@app.route("/")
def ui():
    return render_template("index.html")


@app.route("/api/stock")
def api_stock():
    symbol = request.args.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify(error="symbol parameter is required"), 400

    fy_start, fy_end = most_recent_completed_fy()
    code = symbol.replace(".AX", "")

    log.info(f"{symbol}: computing dividends for FY {fy_start} – {fy_end}")

    cash, frank_pct, rows = investsmart_dividends(code, fy_start, fy_end)
    source_used = "InvestSMART"

    if cash == 0:           # fallback to Yahoo
        cash, rows = yahoo_dividends(symbol, fy_start, fy_end)
        frank_pct = DEFAULT_FRANKING.get(code, 80.0)
        source_used = "Yahoo Finance"

    price = current_price(symbol) or 0.0
    frank_value = cash * (frank_pct / 100) if cash else 0.0

    return jsonify(
        success=True,
        symbol=symbol,
        current_price=price,
        dividend_per_share=round(cash, 6),
        franking_percentage=round(frank_pct, 2),
        franking_value=round(frank_value, 6),
        financial_year=dict(start=str(fy_start), end=str(fy_end)),
        dividend_rows=rows,
        data_sources=dict(price="Yahoo Finance", dividend=source_used)
    )


@app.route("/health")
def health():
    return jsonify(status="healthy", ts=datetime.utcnow().isoformat())


# ────────────────────────────────── main ─────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
