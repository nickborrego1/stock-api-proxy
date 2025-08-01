from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, yfinance as yf, pandas as pd, re, unicodedata
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

def normalise(raw: str) -> str:
    s = raw.strip().upper()
    return s if "." in s else f"{s}.AX"

def clean_num(txt: str) -> float:
    s = unicodedata.normalize("NFKD", txt)
    s = re.sub(r"[^\d.]", "", s) or "0"
    return float(s)

def fetch_franking_investsmart(code: str) -> float | None:
    """
    1) Hit the base page to discover the full 'dividends' slug.
    2) Scrape that table for ex-dates, amounts, franking %.
    """
    base_url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}"
    try:
        r = requests.get(base_url, headers={"User-Agent":USER_AGENT}, timeout=10)
        r.raise_for_status()
    except:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    a = soup.find("a", string=re.compile("dividends", re.I))
    if not a or not a.get("href"):
        return None
    div_url = "https://www.investsmart.com.au" + a["href"]

    try:
        r2 = requests.get(div_url, headers={"User-Agent":USER_AGENT}, timeout=10)
        r2.raise_for_status()
    except:
        return None

    soup2 = BeautifulSoup(r2.text, "html.parser")
    tbl = soup2.find("table")
    if not tbl or not tbl.tbody:
        return None

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frank = 0.0
    rows = tbl.tbody.find_all("tr")
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        # InvestSMART columns: [3]=amount, [4]=percent, [5]=ex-date
        amt_txt, frank_txt, date_txt = tds[3].get_text(), tds[4].get_text(), tds[5].get_text()
        try:
            ex = datetime.strptime(date_txt.strip(), "%d %b %Y").date()
        except:
            continue
        if ex < cutoff:
            continue
        amt   = clean_num(amt_txt)
        frank = clean_num(frank_txt)
        tot_div   += amt
        tot_frank += amt * (frank/100.0)

    if tot_div <= 0:
        return None
    return round((tot_frank/tot_div)*100, 2)

def fetch_franking_marketindex(code: str) -> float | None:
    """
    Scrape https://www.marketindex.com.au/asx/{code}/dividends for the table:
    find the one whose headers include "Ex Date" & "Franking".
    """
    url = f"https://www.marketindex.com.au/asx/{code.lower()}/dividends"
    try:
        r = requests.get(url, headers={"User-Agent":USER_AGENT}, timeout=10)
        r.raise_for_status()
    except:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    cutoff = datetime.utcnow().date() - timedelta(days=365)

    # find the right table
    for tbl in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if "ex date" in headers and "franking" in headers:
            tbody = tbl.find("tbody")
            if not tbody:
                continue

            tot_div = tot_frank = 0.0
            for tr in tbody.find_all("tr"):
                tds = tr.find_all("td")
                # assume tds[0]=ex-date, tds[2]=amount, tds[3]=franking%
                try:
                    ex_txt = tds[0].get_text(strip=True)
                    # format might be "DD/MM/YYYY"
                    try:
                        ex = datetime.strptime(ex_txt, "%d/%m/%Y").date()
                    except ValueError:
                        ex = datetime.strptime(ex_txt, "%d %b %Y").date()
                except:
                    continue
                if ex < cutoff:
                    continue
                amt   = clean_num(tds[2].get_text())
                frank = clean_num(tds[3].get_text())
                tot_div   += amt
                tot_frank += amt * (frank/100.0)

            if tot_div <= 0:
                return None
            return round((tot_frank/tot_div)*100, 2)

    return None

@app.route("/stock")
def stock():
    raw = request.args.get("symbol","").strip()
    if not raw:
        return jsonify({"error":"No symbol provided"}), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # 1) Price + trailing-12m dividends
    try:
        tkr   = yf.Ticker(symbol)
        price = float(tkr.fast_info["lastPrice"])
        hist  = tkr.dividends
        dividend12 = None
        if not hist.empty:
            hist.index = hist.index.tz_localize(None)
            cutoff = datetime.utcnow() - timedelta(days=365)
            dividend12 = float(hist[hist.index >= cutoff].sum())
    except Exception as e:
        return jsonify({"error":f"yfinance error: {e}"}), 500

    # 2) Franking: try InvestSMART, then MarketIndex
    franking = fetch_franking_investsmart(base)
    if franking is None:
        franking = fetch_franking_marketindex(base)

    return jsonify({
        "symbol":     symbol,
        "price":      price,
        "dividend12": dividend12,
        "franking":   franking
    })

@app.route("/")
def index():
    return "Stock API Proxy running — use /stock?symbol=CODE", 200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=8080)
