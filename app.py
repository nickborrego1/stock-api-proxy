from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, date
from dateutil import parser as dtparser

app = Flask(__name__)
CORS(app)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


# ---------- helpers ---------------------------------------------------------
def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def previous_fy_bounds(today: date | None = None) -> tuple[date, date]:
    today = today or datetime.utcnow().date()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


def parse_exdate(txt: str):
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


def clean_amount(cell: str) -> float | None:
    t = cell.replace("\xa0", "").replace(" ", "").replace("$", "").strip()
    if t.lower().endswith(("c", "¢")):
        try:
            return float(t[:-1]) / 100.0
        except ValueError:
            return None
    try:
        return float(t)
    except ValueError:
        return None


# ---------- scrape ----------------------------------------------------------
def locate_div_table(soup: BeautifulSoup):
    for tbl in soup.find_all("table"):
        hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if any(("dividend" in h or "amount" in h) for h in hdrs) and "franking" in hdrs:
            return tbl
    return None


def col_idx(headers: list[str], *keys: str) -> int | None:
    for k in keys:
        for i, h in enumerate(headers):
            if k in h:
                return i
    return None


def fetch_dividend_stats(code: str) -> tuple[float | None, float | None]:
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        html = requests.get(url, headers={"User-Agent": UA}, timeout=15).text
    except Exception:
        return None, None

    soup = BeautifulSoup(html, "html.parser")
    tbl = locate_div_table(soup)
    if not tbl:
        return None, None

    hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
    ex_i   = col_idx(hdrs, "ex")
    div_i  = col_idx(hdrs, "dividend", "amount")
    fran_i = col_idx(hdrs, "franking")
    if None in (ex_i, div_i, fran_i):
        return None, None

    fy_start, fy_end = previous_fy_bounds()
    tot_cash = tot_fran_cash = 0.0

    for tr in tbl.find_all("tr")[1:]:                # skip header row
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]

        # pad out short rows (colspan)
        while len(cells) <= max(ex_i, div_i, fran_i):
            cells.append("")

        try:
            ex_raw, amt_raw, frac_raw = cells[ex_i], cells[div_i], cells[fran_i]
        except IndexError:
            continue

        exd = parse_exdate(ex_raw)
        if not exd or not (fy_start <= exd <= fy_end):
            continue

        amt = clean_amount(amt_raw)
        if amt is None:
            continue

        try:
            fr_pct = float(re.sub(r"[^\d.]", "", frac_raw))
        except ValueError:
            fr_pct = 0.0

        tot_cash += amt
        tot_fran_cash += amt * (fr_pct / 100.0)

    if tot_cash == 0:
        return None, None
    return round(tot_cash, 6), round(tot_fran_cash / tot_cash * 100, 2)


# ---------- debug helper ----------------------------------------------------
def fetch_dividend_stats_debug(code: str):
    fy_start, fy_end = previous_fy_bounds()
    cash = fran = 0.0
    rows = []

    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    soup = BeautifulSoup(requests.get(url, headers={"User-Agent": UA}, timeout=15).text, "html.parser")
    tbl = locate_div_table(soup)
    if not tbl:
        return {"error": "table not found"}

    hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
    ex_i   = col_idx(hdrs, "ex")
    div_i  = col_idx(hdrs, "dividend", "amount")
    fran_i = col_idx(hdrs, "franking")

    for tr in tbl.find_all("tr")[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        while len(cells) <= max(ex_i, div_i, fran_i):
            cells.append("")

        ex_raw, amt_raw, frac_raw = cells[ex_i], cells[div_i], cells[fran_i]

        exd = parse_exdate(ex_raw)
        amt = clean_amount(amt_raw)
        fpc = float(re.sub(r"[^\d.]", "", frac_raw or "0") or 0)

        inside = all([exd, amt]) and fy_start <= exd <= fy_end
        if inside:
            cash += amt
            fran += amt * (fpc / 100.0)

        rows.append({"ex": ex_raw, "parsed": str(exd), "amt": amt_raw,
                     "amt_ok": amt is not None, "fran%": fpc, "in_FY": inside})

    tot_fran = 0 if cash == 0 else round(fran / cash * 100, 2)
    return {"tot_cash": round(cash, 6), "tot_fran": tot_fran, "rows": rows}


# ---------- Flask -----------------------------------------------------------
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

    # debug path
    if request.args.get("debug"):
        return jsonify(fetch_dividend_stats_debug(base)), 200

    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend12, franking = fetch_dividend_stats(base)

    return jsonify(symbol=symbol, price=price,
                   dividend12=dividend12, franking=franking)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
