from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, date
from dateutil import parser as dtparser

app = Flask(__name__)
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ---------- helpers ---------------------------------------------------------

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def parse_exdate(txt: str):
    txt = txt.replace("\xa0", " ").strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    try:
        return dtparser.parse(txt, dayfirst=True).date()
    except Exception:
        return None


def clean_amount(cell_text: str) -> float | None:
    t = (
        cell_text.replace("\xa0", "")
        .replace(" ", "")
        .replace("$", "")
        .strip()
    )
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


def previous_fy_bounds(today: date | None = None):
    today = today or datetime.utcnow().date()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)

# ---------- main scrape -----------------------------------------------------

def _find_div_table(soup: BeautifulSoup):
    for tbl in soup.find_all("table"):
        hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if any("dividend" in h or "amount" in h for h in hdrs) and "franking" in hdrs:
            return tbl
    return None


def _col_index(headers: list[str], *keys: str):
    for k in keys:
        for i, h in enumerate(headers):
            if k in h:
                return i
    return None


def fetch_dividend_stats(code: str):
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        res = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        res.raise_for_status()
    except Exception:
        return None, None

    soup = BeautifulSoup(res.text, "html.parser")
    tbl = _find_div_table(soup)
    if not tbl:
        return None, None

    hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
    ex_i, div_i, fran_i = (
        _col_index(hdrs, "ex"),
        _col_index(hdrs, "dividend", "amount"),
        _col_index(hdrs, "franking"),
    )
    if None in (ex_i, div_i, fran_i):
        return None, None

    fy_start, fy_end = previous_fy_bounds()
    cash = fran_cash = 0.0

    for tr in tbl.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ex_i, div_i, fran_i):
            continue
        exd = parse_exdate(tds[ex_i].get_text())
        if not exd or not (fy_start <= exd <= fy_end):
            continue
        amt = clean_amount(tds[div_i].get_text())
        if amt is None:
            continue
        try:
            fpc = float(re.sub(r"[^\d.]", "", tds[fran_i].get_text()))
        except ValueError:
            fpc = 0.0
        cash += amt
        fran_cash += amt * (fpc / 100.0)

    if cash == 0:
        return None, None
    return round(cash, 6), round(fran_cash / cash * 100, 2)

# ---------- debug helper ----------------------------------------------------

def fetch_dividend_stats_debug(code: str):
    fy_start, fy_end = previous_fy_bounds()
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    soup = BeautifulSoup(requests.get(url, headers={"User-Agent": UA}, timeout=15).text, "html.parser")
    tbl = _find_div_table(soup)
    if not tbl:
        return {"error": "no dividend table"}

    hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
    ex_i, div_i, fran_i = (
        _col_index(hdrs, "ex"),
        _col_index(hdrs, "dividend", "amount"),
        _col_index(hdrs, "franking"),
    )
    report, cash, fran_cash = [], 0.0, 0.0

    for tr in tbl.find_all("tr")[1:]:
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) <= max(ex_i, div_i, fran_i):
            continue
        ex_raw, amt_raw, fran_raw = tds[ex_i], tds[div_i], tds[fran_i]
        exd = parse_exdate(ex_raw)
        amt = clean_amount(amt_raw)
        try:
            fpc = float(re.sub(r"[^\d.]", "", fran_raw) or 0)
        except ValueError:
            fpc = 0.0
        keep = all([exd, amt]) and fy_start <= exd <= fy_end
        if keep:
            cash += amt
            fran_cash += amt * (fpc / 100.0)
        report.append({
            "ex": ex_raw,
            "parsed": str(exd),
            "amt": amt_raw,
            "amt_ok": amt is not None,
            "fran%": fpc,
            "in_FY": keep
        })
    return {
        "tot_cash": round(cash, 6),
        "tot_fran": 0 if cash == 0 else round(fran_cash / cash * 100, 2),
        "rows": report
    }

# ---------- Flask endpoints -------------------------------------------------

@app.route("/")
def home():
    return "Stock API Proxy – /stock?symbol=CODE", 200


@app.route("/stock")
def stock():
    # debug path --------------------------------------------------
    if request.args.get("debug") is not None:
        base = normalise(request.args.get("symbol", "VHY")).split(".")[0]
        return jsonify(fetch_dividend_stats_debug(base)), 200

    # normal path -------------------------------------------------
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend12, franking = fetch_dividend_stats(base)

    return jsonify(
        symbol     = symbol,
        price      = price,
        dividend12 = dividend12,
        franking   = franking
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
