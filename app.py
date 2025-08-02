from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, date
from dateutil import parser as dtparser         # flexible date fallback

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


def previous_fy_bounds(today: date | None = None):
    if today is None:
        today = datetime.utcnow().date()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return date(start_year, 7, 1), date(start_year + 1, 6, 30)


# ---------- scraper ---------------------------------------------------------
def fetch_dividend_stats(code: str):
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        res = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        res.raise_for_status()
    except Exception as e:
        print("InvestSMART request error:", e)
        return None, None

    soup = BeautifulSoup(res.text, "html.parser")
    div_tbl = next(
        (tbl for tbl in soup.find_all("table")
         if {"dividend", "franking"}.issubset(
             [th.get_text(strip=True).lower() for th in tbl.find_all("th")])),
        None)
    if div_tbl is None:
        return None, None

    hdr = [th.get_text(strip=True).lower() for th in div_tbl.find_all("th")]
    try:
        ex_i   = next(i for i, h in enumerate(hdr) if "ex" in h and "date" in h)
        div_i  = hdr.index("dividend")
        fran_i = hdr.index("franking")
    except (ValueError, StopIteration):
        return None, None

    fy_start, fy_end = previous_fy_bounds()
    tot_div = tot_fran_cash = 0.0

    for tr in div_tbl.find("tbody").find_all("tr"):
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
            fr_pct = float(re.sub(r"[^\d.]", "", tds[fran_i].get_text()))
        except ValueError:
            fr_pct = 0.0
        tot_div      += amt
        tot_fran_cash += amt * (fr_pct / 100.0)

    if tot_div == 0:
        return None, None
    return round(tot_div, 6), round(tot_fran_cash / tot_div * 100, 2)


# ---------- Flask endpoints --------------------------------------------------
@app.route("/")
def home():
    return "Stock API Proxy – /stock?symbol=CODE", 200


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "")
    if not raw.strip():
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
base   = symbol.split(".")[0]   # keep everything before the dot
