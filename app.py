import json
import datetime
from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

app = Flask(__name__)

def scrape_marketindex(symbol):
    url = f"https://www.marketindex.com.au/asx/{symbol.lower()}"
    data = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000)
            # Wait for the Dividends table
            page.wait_for_selector("table", timeout=15000)
            html = page.content()
            browser.close()
        soup = BeautifulSoup(html, "html.parser")
        # Find the dividends table
        table = soup.find("table", string=lambda x: x and "Ex Dividend" in x)
        if table is None:
            # fallback: first table
            tables = soup.find_all("table")
            table = tables[0] if tables else None
        if table is None:
            return None

        rows = table.find_all("tr")[1:]  # Skip header
        today = datetime.date.today()
        one_year_ago = today - datetime.timedelta(days=365)
        total_div = 0.0
        franked_cash = 0.0
        franked_total = 0.0
        found_any = False
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            ex_date_str = cells[0].get_text(strip=True)
            dividend_str = cells[2].get_text(strip=True)
            franking_str = cells[4].get_text(strip=True)
            try:
                ex_date = datetime.datetime.strptime(ex_date_str, "%d-%b-%Y").date()
            except Exception:
                continue
            if ex_date < one_year_ago:
                continue
            found_any = True
            try:
                div = float(dividend_str.replace("$", "").replace(",", ""))
                total_div += div
            except Exception:
                continue
            try:
                frank_pct = float(franking_str.replace("%", ""))
                franked_cash += div * (frank_pct / 100)
                franked_total += frank_pct
            except Exception:
                pass

        avg_franking = (franked_cash / total_div * 100) if total_div > 0 else None
        return {
            "dividend12": total_div if found_any else None,
            "franking": round(avg_franking, 2) if avg_franking else None
        }
    except Exception as e:
        print("Scrape error:", e)
        return None

@app.route("/stock")
def stock():
    symbol = request.args.get("symbol", "VHY")
    price = 75.25  # Placeholder, ideally get from yfinance or elsewhere
    data = scrape_marketindex(symbol)
    if not data:
        return jsonify({
            "dividend12": None,
            "franking": None,
            "price": price,
            "symbol": f"{symbol}.AX"
        })
    data["price"] = price
    data["symbol"] = f"{symbol}.AX"
    return jsonify(data)

if __name__ == "__main__":
    app.run(debug=True)
