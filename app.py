# app.py  –  dividend + franking fetcher (InvestSMART → fallback MarketIndex)

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, yfinance as yf, logging
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────
def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def parse_exdate(txt: str):
    txt = txt.strip()
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        try:   return datetime.strptime(txt, fmt).date()
        except ValueError:  continue
    return None

# ──────────────────────────────────────────────────────────────────────
# table parser (works for both sites)
# ──────────────────────────────────────────────────────────────────────
def parse_div_table(html: str):
    soup = BeautifulSoup(html, "html.parser")

    # prefer InvestSMART's <table class="history-table">
    tbl = soup.find("table", class_=lambda c: c and "history" in c.lower())  
    if not tbl:
        # fallback – first table that has both “amount” & “franking” headers
        for t in soup.find_all("table"):
            heads = [th.get_text(strip=True).lower() for th in t.find_all("th")]
            if {"amount", "franking"}.intersection(heads):   tbl = t; break
    if not tbl:  return None, None

    heads = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
    try:
        ex_i  = next(i for i,h in enumerate(heads) if "ex"       in h)
        amt_i = next(i for i,h in enumerate(heads) if "amount"   in h or "dividend" in h)
        fr_i  = next(i for i,h in enumerate(heads) if "frank"    in h)
    except StopIteration:
        return None, None

    cutoff  = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_fran_cash = kept = 0.0

    for tr in tbl.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ex_i, amt_i, fr_i):  continue
        exd = parse_exdate(tds[ex_i].get_text());  if not exd or exd < cutoff:  continue
        try:    amt = float(re.sub(r"[^\d.]", "", tds[amt_i].get_text()))
        except ValueError:  continue
        try:    fpc = float(re.sub(r"[^\d.]", "", tds[fr_i ].get_text()))
        except ValueError:  fpc = 0.0

        tot_div       += amt
        tot_fran_cash += amt * fpc / 100
        kept          += 1

    logging.info("Kept %d rows within 12 m | div12 %.4f | w_fran %.2f",
                 kept, tot_div, 0 if tot_div==0 else 100*tot_fran_cash/tot_div)

    if tot_div == 0:  return None, None
    return round(tot_div, 6), round(tot_fran_cash / tot_div * 100, 2)

# ──────────────────────────────────────────────────────────────────────
# fetch pipeline  (InvestSMART first – MI as backup)
# ──────────────────────────────────────────────────────────────────────
def fetch_div_data(code: str):
    # 1️⃣  InvestSMART
    try:
        url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
        r   = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        if r.ok:
            out = parse_div_table(r.text)
            if all(out):  return out
    except Exception as e:
        logging.warning("InvestSMART error %s", e)

    # 2️⃣  MarketIndex fallback
    try:
        url = f"https://www.marketindex.com.au/asx/{code.lower()}"
        r   = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        if r.ok:
            return parse_div_table(r.text)
    except Exception as e:
        logging.warning("MarketIndex error %s", e)

    return None, None

# ──────────────────────────────────────────────────────────────────────
# routes
# ──────────────────────────────────────────────────────────────────────
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
    try:    price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend12, franking = fetch_div_data(basecode)
    return jsonify(symbol=symbol, price=price,
                   dividend12=dividend12, franking=franking)

# gunicorn entry point (Render etc.)
