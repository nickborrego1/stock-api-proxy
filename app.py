# app.py  –  ASX dividend/ franking proxy
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta

app = Flask(__name__)
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ---------- helpers ----------------------------------------------------------
def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def parse_exdate(txt: str):
    txt = txt.strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def clean_amount(cell_text: str) -> float | None:
    t = cell_text.strip().replace("$", "")
    if "¢" in t:
        t = t.replace("¢", "")
        try:
            return float(t) / 100.0
        except ValueError:
            return None
    try:
        return float(t)
    except ValueError:
        return None


# ---------- choose current Australian FY ------------------------------------
today = date.today()
this_fy_start = date(today.year if today >= date(today.year, 7, 1) else today.year - 1, 7, 1)
this_fy_end   = this_fy_start.replace(year=this_fy_start.year + 1) - timedelta(days=1)


# ---------- scraper (InvestSMART) -------------------------------------------
def fetch_dividend_stats(code: str) -> tuple[float | None, float | None]:
    """(cash_divs_this_FY , weighted_fran_pct)  or  (None, None)"""
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print("InvestSMART request error:", e)
        return None, None

    soup = BeautifulSoup(r.text, "html.parser")
    target = None
    for tbl in soup.find_all("table"):
        hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if any(h.startswith(("dividend", "amount")) for h in hdrs) and any("frank" in h for h in hdrs):
            target = tbl
            break
    if target is None:
        return None, None

    hdrs = [th.get_text(strip=True).lower() for th in target.find_all("th")]
    try:
        ex_i  = next(i for i, h in enumerate(hdrs) if "ex" in h)
        amt_i = next(i for i, h in enumerate(hdrs) if h.startswith(("dividend", "amount")))
        fr_i  = next(i for i, h in enumerate(hdrs) if "frank" in h)
    except StopIteration:
        return None, None

    tot_div = tot_fr_cash = 0.0
    for tr in target.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ex_i, amt_i, fr_i):
            continue
        exd = parse_exdate(tds[ex_i].get_text())
        if not exd or not (this_fy_start <= exd <= this_fy_end):
            continue
        amt = clean_amount(tds[amt_i].get_text())
        if amt is None:
            continue
        try:
            fr_pct = float(re.sub(r"[^\d.]", "", tds[fr_i].get_text()))
        except ValueError:
            fr_pct = 0.0
        tot_div      += amt
        tot_fr_cash  += amt * fr_pct / 100.0

    if tot_div == 0:
        return None, None
    return round(tot_div, 6), round(tot_fr_cash * 100 / tot_div, 2)


# ---------- Flask routes -----------------------------------------------------
@app.route("/")
def home():
    return "Stock API Proxy – call /stock?symbol=CODE (e.g. /stock?symbol=VHY)", 200


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

    dividend12, franking = fetch_dividend_stats(base)

    return jsonify(
        symbol     = symbol,
        price      = price,
        dividend12 = dividend12,
        franking   = franking,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
