01 from flask import Flask, request, jsonify
02 from flask_cors import CORS
03 import requests, re, yfinance as yf
04 from bs4 import BeautifulSoup
05 from datetime import datetime, date, timedelta
06 from dateutil import parser as dtparser
07 
08 app = Flask(__name__)
09 CORS(app)
10 UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
11       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
12 
13 # ------------ helpers ---------------------------------------------------
14 def normalise(raw: str) -> str:
15 ····s = raw.strip().upper()
16 ····return s if "." in s else f"{s}.AX"
17 
18 def parse_exdate(txt: str):
19 ····txt = txt.replace("\xa0", " ").strip()
20 ····for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d/%m/%Y"):
21 ········try:
22 ············return datetime.strptime(txt, fmt).date()
23 ········except ValueError:
24 ············continue
25 ····try:
26 ········return dtparser.parse(txt, dayfirst=True).date()
27 ····except Exception:
28 ········return None
29 
30 def clean_amount(cell_text: str) -> float | None:
31 ····t = (cell_text.replace("\xa0", "")
32 ········             .replace(" ", "")   # no-break space variant
33 ········             .replace(" ", "")
34 ········             .replace("$", "")
35 ········             .strip())
36 ····if t.lower().endswith(("c", "¢")):
37 ········t = t[:-1]
38 ········try:
39 ············return float(t) / 100.0
40 ········except ValueError:
41 ············return None
42 ····try:
43 ········return float(t)
44 ····except ValueError:
45 ········return None
46 
47 def previous_fy_bounds(today: date | None = None):
48 ····if today is None:
49 ········today = datetime.utcnow().date()
50 ····start_year = today.year - 1 if today.month >= 7 else today.year - 2
51 ····return date(start_year, 7, 1), date(start_year + 1, 6, 30)
52 
53 # ------------ main scrape -----------------------------------------------
54 def fetch_dividend_stats(code: str):
55 ····url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
56 ····try:
57 ········res = requests.get(url, headers={"User-Agent": UA}, timeout=15)
58 ········res.raise_for_status()
59 ····except Exception as e:
60 ········print("InvestSMART request error:", e)
61 ········return None, None
62 ····soup = BeautifulSoup(res.text, "html.parser")
63 ····div_tbl = next(
64 ········(tbl for tbl in soup.find_all("table")
65 ········  if {"dividend", "franking"}.issubset(
66 ········      [th.get_text(strip=True).lower() for th in tbl.find_all("th")])),
67 ········None)
68 ····if div_tbl is None:
69 ········return None, None
70 ····hdr = [th.get_text(strip=True).lower() for th in div_tbl.find_all("th")]
71 ····try:
72 ········ex_i   = next(i for i,h in enumerate(hdr) if "ex" in h and "date" in h)
73 ········div_i  = hdr.index("dividend")
74 ········fran_i = hdr.index("franking")
75 ····except (ValueError, StopIteration):
76 ········return None, None
77 ····fy_start, fy_end = previous_fy_bounds()
78 ····tot_div = tot_frk_cash = 0.0
79 ····for tr in div_tbl.find("tbody").find_all("tr"):
80 ········tds = tr.find_all("td")
81 ········if len(tds) <= max(ex_i, div_i, fran_i): continue
82 ········exd = parse_exdate(tds[ex_i].get_text())
83 ········if not exd or not (fy_start <= exd <= fy_end): continue
84 ········amt = clean_amount(tds[div_i].get_text())
85 ········if amt is None: continue
86 ········try:
87 ············fr_pct = float(re.sub(r"[^\d.]", "", tds[fran_i].get_text()))
88 ········except ValueError:
89 ············fr_pct = 0.0
90 ········tot_div      += amt
91 ········tot_frk_cash += amt * (fr_pct / 100.0)
92 ····if tot_div == 0:
93 ········return None, None
94 ····return round(tot_div, 6), round(tot_frk_cash / tot_div * 100, 2)
95 
96 # ------------ Flask routes ----------------------------------------------
97 @app.route("/")
98 def home():
99 ····return "Stock API Proxy – /stock?symbol=CODE", 200
100 
101 @app.route("/stock")
102 def stock():
103 ····raw = request.args.get("symbol", "")
104 ····if not raw.strip():
105 ········return jsonify(error="No symbol provided"), 400
106 ····symbol = normalise(raw)
107 ····base   = symbol.split(".")[0]
108 ····try:
109 ········price = float(yf.Ticker(symbol).fast_info["lastPrice"])
110 ····except Exception as e:
111 ········return jsonify(error=f"Price fetch failed: {e}"), 500
112 ····div12, fran = fetch_dividend_stats(base)
113 ····return jsonify(symbol=symbol, price=price, dividend12=div12, franking=fran)
114 
115 if __name__ == "__main__":
116 ····app.run(host="0.0.0.0", port=8080)
