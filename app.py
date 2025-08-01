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

# ---------- helpers -------------------------------------------------
def normalise(raw: str) -> str:
    """Convert 'vhy' → 'VHY.AX', keep suffix if already present."""
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


# ---------- scraper -------------------------------------------------
def scrape_marketindex(code: str):
    """
    Return (total_div_last_12m, weighted_fran_pct) or (None, None).
    First try marketindex.com.au; if they block (403), fall back to InvestSMART.
    """

    def parse_table(html: str):
        soup = BeautifulSoup(html, "html.parser")
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
            ex_i = next(i for i, h in enumerate(hdrs) if "ex" in h)
            amt_i = hdrs.index("amount")
            fr_i = hdrs.index("franking")
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
                fr_pct = float(re.sub(r"[^\d.]", "", tds[fr_i].get_text()))
            except ValueError:
                fr_pct = 0.0

            tot_div += amt
            tot_fran_cash += amt * (fr_pct / 100.0)

        if tot_div == 0:
            return None, None
        return round(tot_div, 6), round((tot_fran_cash / tot_div) * 100, 2)

    # --- 1) MarketIndex ---------------------------------------------
    mi_url = f"https://www.marketindex.com.au/asx/{code.lower()}"
    try:
        r = requests.get(
            mi_url,
            headers={"User-Agent": UA, "Referer": "https://www.google.com/"},
            timeout=15,
        )
        if r.ok:
            out = parse_table(r.text)
            if all(out):
                return out
        elif r.status_code != 403:
            print("MarketIndex status:", r.status_code)
    except Exception as e:
        print("MarketIndex error:", e)

    # --- 2) InvestSMART fallback ------------------------------------
    is_url = (
        f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    )
    try:
        r = requests.get(is_url, headers={"User-Agent": UA}, timeout=15)
        if r.ok:
            return parse_table(r.text)
    except Exception as e:
        print("InvestSMART error:", e)

    return None, None


# ---------- routes --------------------------------------------------
@app.route("/")
def home():
    return "Stock API Proxy – use /stock?symbol=CODE", 200


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base = symbol.split(".")[0]

    # price ----------------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)            frk_idx = hdrs.index("franking")
        except (StopIteration, ValueError):
            return None, None

        cutoff = datetime.utcnow().date() - timedelta(days=365)
        tot_div = tot_frk_cash = 0.0

        for tr in target_table.find("tbody").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) <= max(ex_idx, amt_idx, frk_idx):
                continue
            exd = parse_exdate(tds[ex_idx].get_text())
            if not exd or exd < cutoff:
                continue
            try:
                amt = float(re.sub(r"[^\d.]", "", tds[amt_idx].get_text()))
            except ValueError:
                continue
            try:
                fpc = float(re.sub(r"[^\d.]", "", tds[frk_idx].get_text()))
            except ValueError:
                fpc = 0.0
            tot_div += amt
            tot_frk_cash += amt * (fpc / 100.0)

        if tot_div == 0:
            return None, None
        return round(tot_div, 6), round((tot_frk_cash / tot_div) * 100, 2)

    # ---- 1) MarketIndex -------------------------------------------------
    mi_url = f"https://www.marketindex.com.au/asx/{code.lower()}"
    try:
        res = requests.get(
            mi_url,
            headers={
                "User-Agent": UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
            },
            timeout=15,
        )
        if res.status_code == 200:
            out = parse_table(res.text)
            if all(out):
                return out
        elif res.status_code != 403:
            print("MarketIndex non-200:", res.status_code)
    except Exception as e:
        print("MarketIndex error:", e)

    # ---- 2) fallback → InvestSMART --------------------------------------
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
    return (
        "Stock API Proxy – use /stock?symbol=CODE (e.g. /stock?symbol=VHY)",
        200,
    )


@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base = symbol.split(".")[0]

    # price ----------------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)        except ValueError:
            continue
    return None

# ------------------------------------------------------------------ #
# main scraper (MarketIndex + fallback InvestSMART)
# ------------------------------------------------------------------ #

def scrape_marketindex(code: str):
    """
    Returns (cash_div_12m, weighted_fran_pct)  or  (None, None).
    """
    # ---------- inner helper to parse either site ------------------
    def parse_table(html: str):
        soup = BeautifulSoup(html, "html.parser")
        div_tbl = None
        for tbl in soup.find_all("table"):
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            if {"amount", "franking"}.issubset(set(hdrs)):
                div_tbl = tbl
                break
        if not div_tbl:
            return None, None

        hdrs = [th.get_text(strip=True).lower() for th in div_tbl.find_all("th")]
        try:
            ex_idx  = next(
                i for i, h in enumerate(hdrs)
                if "ex" in h and ("date" in h or "dividend" in h)
            )
            amt_idx = hdrs.index("amount")
            frk_idx = hdrs.index("franking")
        except (StopIteration, ValueError):
            return None, None

        cutoff = datetime.utcnow().date() - timedelta(days=365)
        tot_div = tot_frk_cash = 0.0

        for tr in div_tbl.find("tbody").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) <= max(ex_idx, amt_idx, frk_idx):
                continue
            exd = parse_exdate(tds[ex_idx].get_text())
            if not exd or exd < cutoff:
                continue
            try:
                amt = float(re.sub(r"[^\d.]", "", tds[amt_idx].get_text()))
            except ValueError:
                continue
            try:
                fpc = float(re.sub(r"[^\d.]", "", tds[frk_idx].get_text()))
            except ValueError:
                fpc = 0.0
            tot_div += amt
            tot_frk_cash += amt * (fpc / 100.0)

        if tot_div == 0:
            return None, None
        return round(tot_div, 6), round((tot_frk_cash / tot_div) * 100, 2)

    # ---------- 1) try MarketIndex --------------------------------
    mi_url = f"https://www.marketindex.com.au/asx/{code.lower()}"
    try:
        res = requests.get(
            mi_url,
            headers={
                "User-Agent": UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/"
            },
            timeout=15,
        )
        if res.status_code == 200:
            out = parse_table(res.text)
            if all(out):
                return out
        elif res.status_code != 403:
            print("MarketIndex non-200:", res.status_code)
    except Exception as e:
        print("MarketIndex error:", e)

    # ---------- 2) fallback InvestSMART ----------------------------
    is_url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    try:
        res = requests.get(is_url, headers={"User-Agent": UA}, timeout=15)
        if res.ok:
            return parse_table(res.text)
    except Exception as e:
        print("InvestSMART error:", e)

    return None, None

# ------------------------------------------------------------------ #
# Flask endpoints
# ------------------------------------------------------------------ #

@app.route("/")
def home():
    return "Stock API Proxy (MarketIndex → InvestSMART fallback). Use /stock?symbol=CODE", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    # -------- price ------------------------------------------------
    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    # -------- dividends & franking ---------------------------------
    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
    soup = BeautifulSoup(res.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None, None

    div_tbl = None
    for tbl in tables:
        hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if {"amount", "franking"}.issubset(set(hdrs)):
            div_tbl = tbl
            break
    if div_tbl is None:
        return None, None

    hdrs = [th.get_text(strip=True).lower() for th in div_tbl.find_all("th")]
    try:
        ex_idx  = next(i for i,h in enumerate(hdrs)
                       if "ex" in h and ("date" in h or "dividend" in h))
        amt_idx = hdrs.index("amount")
        frk_idx = hdrs.index("franking")
    except (StopIteration, ValueError):
        return None, None

    cutoff = datetime.utcnow().date() - timedelta(days=365)
    tot_div = tot_frk_cash = 0.0

    for tr in div_tbl.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ex_idx, amt_idx, frk_idx):
            continue
        ex_date = parse_exdate(tds[ex_idx].get_text())
        if not ex_date or ex_date < cutoff:
            continue
        try:
            amt = float(re.sub(r"[^\d.]", "", tds[amt_idx].get_text()))
        except ValueError:
            continue
        try:
            fr_pct = float(re.sub(r"[^\d.]", "", tds[frk_idx].get_text()))
        except ValueError:
            fr_pct = 0.0
        tot_div += amt
        tot_frk_cash += amt * (fr_pct / 100.0)

    if tot_div == 0:
        return None, None
    return round(tot_div, 6), round((tot_frk_cash / tot_div) * 100, 2)

app = Flask(__name__)
CORS(app)

@app.route("/")
def home():
    return "Stock API Proxy (MarketIndex)", 200

@app.route("/stock")
def stock():
    raw = request.args.get("symbol", "").strip()
    if not raw:
        return jsonify(error="No symbol provided"), 400

    symbol = normalise(raw)
    base   = symbol.split(".")[0]

    try:
        price = float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        return jsonify(error=f"Price fetch failed: {e}"), 500

    dividend12, franking = scrape_marketindex(base)

    return jsonify(
        symbol=symbol,
        price=price,
        dividend12=dividend12,
        franking=franking
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
