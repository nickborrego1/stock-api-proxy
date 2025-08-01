# ---------------------------  app.py  ---------------------------------
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ------------------------------------------------------------------ #
# Utilities
# ------------------------------------------------------------------ #
def normalise(raw: str) -> str:
    """'vhy'  ->  'VHY.AX'   (keeps suffix if already present)"""
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"


def parse_exdate(txt: str):
    txt = txt.strip()
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ #
# Scraper  (MarketIndex  ➜  InvestSMART fallback)
# ------------------------------------------------------------------ #
def scrape_marketindex(code: str):
    """
    Returns  (total_cash_div_last_12m, weighted_fran_pct)
    or  (None, None) if nothing could be scraped.
    """

    def parse_table(html: str):
        soup = BeautifulSoup(html, "html.parser")

        # locate the first table containing both “amount” and “franking” headers
        target = None
        for tbl in soup.find_all("table"):
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            if {"amount", "franking"}.issubset(hdrs):
                target = tbl
                break
        if target is None:
            return None, None

        hdrs = [th.get_text(strip=True).lower() for th in target.find_all("th")]
        try:
            ex_i  = next(i for i, h in enumerate(hdrs) if "ex" in h)
            amt_i = hdrs.index("amount")
            fr_i  = hdrs.index("franking")
        except (StopIteration, ValueError):
            return None, None

        cutoff = datetime.utcnow().date() - timedelta(days=365)
        tot_div = tot_fran_cash = 0.0

        for tr in target.find("tbody").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) <= max(ex_i, amt_i, fr_i):
                continue

            exd = parse_exdate(tds[ex_i].get_text())
            if not exd or exd < cutoff:
                continue

            try:
                amt = float(re.sub(r"[^\d.]", "", tds[amt_i].get_text()))
            except ValueError:
                continue
            try:
                fr_pc = float(re.sub(r"[^\d.]", "", tds[fr_i].get_text()))
            except ValueError:
                fr_pc = 0.0

            tot_div       += amt
            tot_fran_cash += amt * (fr_pc / 100.0)

        if tot_div == 0:
            return None, None
        return round(tot_div, 6), round((tot_fran_cash / tot_div) * 100, 2)

    # ---- 1) MarketIndex ------------------------------------------
    mi_url = f"https://www.marketindex.com.au/asx/{code.lower()}"
    try:
        res = requests.get(
            mi_url,
            headers={"User-Agent": UA, "Referer": "https://www.google.com/"},
            timeout=15,
        )
        if res.ok:
            out = parse_table(res.text)
            if all(out):
                return out
        elif res.status_code != 403:
            print("MarketIndex status:", res.status_code)
    except Exception as e:
        print("MarketIndex error:", e)

    # ---- 2) InvestSMART fallback ---------------------------------
    is_url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        res = requests.get(is_url, headers={"User-Agent": UA}, timeout=15)
        if res.ok:
            return parse_table(res.text)
    except Exception as e:
        print("InvestSMART error:", e)

    return None, None


# ------------------------------------------------------------------ #
# Flask routes
# ------------------------------------------------------------------ #
@app.route("/")
def home():
    return "Stock API Proxy – use  /stock?symbol=CODE  (e.g. /stock?symbol=VHY)", 200


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # -------- price (Yahoo) ---------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # -------- dividends + franking --------------------------------
    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking,
    )


# ------------------------------------------------------------------ #
# Run locally
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
# ---------------------------  end of file  ---------------------------
