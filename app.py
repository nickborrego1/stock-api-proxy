from flask import Flask, request, jsonify
from flask_cors  import CORS
import requests, re, yfinance as yf, logging
from bs4 import BeautifulSoup
from datetime    import datetime, timedelta

app  = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# ------------------------------------------------------------------ #
# helpers
# ------------------------------------------------------------------ #
def normalise(raw: str) -> str:
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
# table-parsing routine (works for both MarketIndex & InvestSMART)
# ------------------------------------------------------------------ #
def parse_div_table(html: str):
    soup = BeautifulSoup(html, "html.parser")

    # pick the table that is explicitly the “dividend history” one
    candidate = None
    for tbl in soup.find_all("table"):
        caption = tbl.find("caption")
        cls     = " ".join(tbl.get("class", [])).lower()
        if (caption and "dividend history" in caption.get_text(strip=True).lower()) \
           or "dividend-history" in cls or "dividend-table" in cls:
            candidate = tbl
            break
    if candidate is None:
        return None, None

    hdrs = [th.get_text(strip=True).lower() for th in candidate.find_all("th")]
    try:
        ex_i  = next(i for i,h in enumerate(hdrs) if "ex" in h)
        amt_i = hdrs.index("amount")
        fr_i  = hdrs.index("franking")
    except (StopIteration, ValueError):
        return None, None

    cutoff  = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_fran_cash = 0.0
    kept    = 0

    for tr in candidate.find("tbody").find_all("tr"):
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
            fpc = float(re.sub(r"[^\d.]", "", tds[fr_i].get_text()))
        except ValueError:
            fpc = 0.0

        tot_div        += amt
        tot_fran_cash  += amt * fpc/100
        kept           += 1

    logging.info("Kept %s rows within 12 m", kept)
    if tot_div == 0:
        return None, None
    return round(tot_div, 6), round((tot_fran_cash / tot_div)*100, 2)

# ------------------------------------------------------------------ #
# primary fetch routine  (MarketIndex → fallback InvestSMART)
# ------------------------------------------------------------------ #
def fetch_div_data(code: str):
    mi_url = f"https://www.marketindex.com.au/asx/{code.lower()}"
    try:
        r = requests.get(mi_url, headers={"User-Agent": UA}, timeout=15)
        if r.status_code == 200:
            out = parse_div_table(r.text)
            if all(out):
                return out
        elif r.status_code != 403:
            logging.warning("MarketIndex status %s", r.status_code)
    except Exception as e:
        logging.warning("MarketIndex error %s", e)

    # fallback
    is_url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        r = requests.get(is_url, headers={"User-Agent": UA}, timeout=15)
        if r.ok:
            return parse_div_table(r.text)
    except Exception as e:
        logging.warning("InvestSMART error %s", e)

    return None, None

# ------------------------------------------------------------------ #
# routes
# ------------------------------------------------------------------ #
@app.route("/")
def root():
    return "Stock API Proxy – /stock?symbol=CODE", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol","").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol   = normalise(raw)
    basecode = symbol.split(".")[0]

    # live price
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend12, franking = fetch_div_data(basecode)

    return jsonify(
        symbol     = symbol,
        price      = price,
        dividend12 = dividend12,
        franking   = franking
    )

# Render runs gunicorn; no __main__ section needed
