from flask import Flask, jsonify
from flask_cors import CORS
import yfinance as yf
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import json

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

def get_last_completed_fy():
    today = datetime.now()
    if today.month < 7:  # Current year FY hasn't ended yet
        start_date = datetime(today.year - 2, 7, 1)
        end_date = datetime(today.year - 1, 6, 30)
    else:
        start_date = datetime(today.year - 1, 7, 1)
        end_date = datetime(today.year, 6, 30)
    return start_date, end_date

def fetch_yahoo_dividends(ticker, start_date, end_date):
    try:
        # Append .AX for ASX stocks
        stock = yf.Ticker(f"{ticker}.AX")
        company_name = stock.info.get('longName', ticker)
        current_price = stock.info.get('currentPrice', 0)
        
        # Get dividend history
        dividends = stock.dividends
        
        if dividends.empty:
            return None, None, None, None
        
        # Filter for the financial year
        fy_dividends = dividends.loc[start_date:end_date]
        
        if fy_dividends.empty:
            return None, None, None, None
        
        total_dividend = fy_dividends.sum()
        return company_name, current_price, total_dividend, fy_dividends
    
    except Exception as e:
        print(f"Yahoo Finance error: {str(e)}")
        return None, None, None, None

def fetch_investsmart_franking(ticker):
    try:
        url = f"https://www.investsmart.com.au/shares/asx-{ticker.lower()}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find franking information
        franking_data = {}
        franking_section = soup.find('h3', string='Dividend Franking')
        
        if not franking_section:
            # Try alternative headings
            franking_section = soup.find('h3', string=re.compile(r'Dividend Franking', re.IGNORECASE))
        
        if franking_section:
            table = franking_section.find_next('table')
            if table:
                rows = table.find_all('tr')
                for row in rows[1:]:  # Skip header
                    cols = row.find_all('td')
                    if len(cols) >= 3:
                        date_str = cols[0].text.strip()
                        amount = cols[1].text.strip()
                        franking = cols[2].text.strip()
                        
                        # Convert date to standard format
                        try:
                            date = datetime.strptime(date_str, '%d/%m/%y')
                            date_key = date.strftime('%Y-%m-%d')
                        except ValueError:
                            date_key = date_str
                            
                        # Extract franking percentage
                        franking_percent = 0
                        if franking == 'Fully Franked':
                            franking_percent = 100
                        elif franking == 'Unfranked':
                            franking_percent = 0
                        else:
                            # Extract percentage from text
                            match = re.search(r'(\d+)%', franking)
                            if match:
                                franking_percent = int(match.group(1))
                        
                        franking_data[date_key] = franking_percent
        
        return franking_data
    
    except Exception as e:
        print(f"InvestSMART error: {str(e)}")
        return {}

def calculate_totals(dividends, franking_data):
    total_dividend = dividends.sum()
    dividend_count = len(dividends)
    dividend_average = total_dividend / dividend_count if dividend_count > 0 else 0
    
    total_franking_credits = 0
    franking_percentages = []
    
    # Match dividends with franking data
    for date, amount in dividends.items():
        date_str = date.strftime('%Y-%m-%d')
        franking_percent = franking_data.get(date_str, 0)
        
        # Calculate franking credits (AUS tax rate 30%)
        franking_credit = amount * (franking_percent / 100) * (0.3 / 0.7)
        total_franking_credits += franking_credit
        franking_percentages.append(franking_percent)
    
    avg_franking = sum(franking_percentages) / len(franking_percentages) if franking_percentages else 0
    
    return {
        "dividend_total": round(total_dividend, 4),
        "dividend_average": round(dividend_average, 4),
        "franking_total": round(total_franking_credits, 4),
        "franking_percent_average": round(avg_franking, 2)
    }

@app.route('/fetch_dividend_data/<ticker>')
def fetch_dividend_data(ticker):
    # Get financial year dates
    start_date, end_date = get_last_completed_fy()
    fy_str = f"{start_date.strftime('%d/%m/%Y')} to {end_date.strftime('%d/%m/%Y')}"
    
    # Fetch data from sources
    company_name, current_price, total_dividend, dividends = fetch_yahoo_dividends(
        ticker, start_date, end_date
    )
    
    if not company_name:
        return jsonify({
            "error": "Sorry, we couldn't fetch dividend data for this ticker. Please try another or contact support.",
            "status": "error"
        }), 404
    
    franking_data = fetch_investsmart_franking(ticker)
    
    # Calculate results
    results = calculate_totals(dividends, franking_data)
    
    return jsonify({
        "company": company_name,
        "ticker": ticker,
        "current_price": current_price,
        "financial_year": fy_str,
        "dividend_total": results["dividend_total"],
        "dividend_average": results["dividend_average"],
        "franking_total": results["franking_total"],
        "franking_percent_average": results["franking_percent_average"],
        "sources": ["Yahoo Finance", "InvestSMART"],
        "status": "success"
    })

if __name__ == '__main__':
    app.run(debug=True)
