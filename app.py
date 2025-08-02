import os
import logging
import re
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template
import yfinance as yf
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import pandas as pd

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-change-in-production")

def get_most_recent_completed_fy():
    """
    Calculate the most recent completed Australian financial year.
    FY runs from July 1 to June 30.
    If current date is before July 1, use previous year's FY.
    If current date is July 1 or after, use current year's FY.
    """
    today = datetime.now()
    current_year = today.year
    
    # If we're before July 1st, the most recent completed FY ended June 30 of current year
    # If we're July 1st or after, the most recent completed FY ended June 30 of current year
    if today.month < 7:
        # We're in Jan-Jun, so last completed FY was (current_year-1) to current_year
        fy_start = datetime(current_year - 1, 7, 1)
        fy_end = datetime(current_year, 6, 30)
    else:
        # We're in Jul-Dec, so last completed FY was current_year to (current_year+1)
        # Wait, this is wrong. If we're in August 2025, the most recent COMPLETED FY
        # would be 2024-2025 (July 1, 2024 to June 30, 2025)
        fy_start = datetime(current_year - 1, 7, 1)
        fy_end = datetime(current_year, 6, 30)
    
    logger.info(f"Current date: {today.strftime('%Y-%m-%d')}")
    logger.info(f"Most recent completed FY: {fy_start.strftime('%Y-%m-%d')} to {fy_end.strftime('%Y-%m-%d')}")
    
    return fy_start, fy_end

def clean_text(text):
    """Remove non-breaking spaces and clean text"""
    if not text:
        return ""
    # Replace non-breaking spaces with regular spaces
    cleaned = text.replace('\xa0', ' ').replace('\u00a0', ' ')
    # Remove extra whitespace
    cleaned = ' '.join(cleaned.split())
    return cleaned.strip()

def parse_date(date_str):
    """Parse date string from InvestSMART"""
    if not date_str or date_str == "-":
        return None
    
    date_str = clean_text(date_str)
    
    # Try different date formats
    formats = [
        '%d %b %Y',  # 15 Jan 2025
        '%d %B %Y',  # 15 January 2025
        '%d/%m/%Y',  # 15/01/2025
        '%Y-%m-%d',  # 2025-01-15
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    logger.warning(f"Could not parse date: '{date_str}'")
    return None

def parse_amount(amount_str):
    """Parse amount string from InvestSMART"""
    if not amount_str or amount_str == "-":
        return None
    
    amount_str = clean_text(amount_str)
    
    # Remove currency symbols and extract number
    amount_str = re.sub(r'[^\d.,\-]', '', amount_str)
    
    if not amount_str or amount_str == "-":
        return None
    
    try:
        # Handle comma as thousands separator
        if ',' in amount_str and '.' in amount_str:
            # e.g., "1,234.56"
            amount_str = amount_str.replace(',', '')
        elif ',' in amount_str and amount_str.count(',') == 1:
            # Could be either thousands separator or decimal separator
            parts = amount_str.split(',')
            if len(parts[1]) <= 2:  # Likely decimal separator
                amount_str = amount_str.replace(',', '.')
            else:  # Likely thousands separator
                amount_str = amount_str.replace(',', '')
        
        return float(amount_str)
    except ValueError:
        logger.warning(f"Could not parse amount: '{amount_str}'")
        return None

def get_yahoo_finance_dividends(symbol, start_date, end_date):
    """Get dividend data from Yahoo Finance for the specified period"""
    try:
        # Ensure symbol has .AX suffix for ASX stocks
        if not symbol.endswith('.AX'):
            symbol += '.AX'
        
        logger.info(f"Fetching Yahoo Finance data for {symbol} from {start_date} to {end_date}")
        
        ticker = yf.Ticker(symbol)
        
        # Get current stock price
        info = ticker.info
        current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
        
        # Get dividend data for the specified period
        dividends = ticker.dividends
        
        if len(dividends) == 0:
            logger.warning(f"No dividend data found for {symbol}")
            return None, current_price, []
        
        # Convert start_date and end_date to timezone-aware if dividends index is timezone-aware
        if hasattr(dividends.index, 'tz') and dividends.index.tz is not None:
            # Make our dates timezone-aware to match dividends index
            start_date_tz = pd.to_datetime(start_date).tz_localize(dividends.index.tz)
            end_date_tz = pd.to_datetime(end_date).tz_localize(dividends.index.tz)
        else:
            # Convert to pandas datetime for consistent comparison
            start_date_tz = pd.to_datetime(start_date)
            end_date_tz = pd.to_datetime(end_date)
        
        # Filter dividends for the financial year period
        fy_dividends = dividends[
            (dividends.index >= start_date_tz) & 
            (dividends.index <= end_date_tz)
        ]
        
        if len(fy_dividends) == 0:
            logger.info(f"No dividends found for {symbol} in the specified period")
            return 0.0, current_price, []
        
        total_dividends = float(fy_dividends.sum())
        dividend_list = [
            {
                'date': date.strftime('%Y-%m-%d'),
                'amount': float(amount)
            }
            for date, amount in fy_dividends.items()
        ]
        
        logger.info(f"Yahoo Finance: Found {len(dividend_list)} dividends totaling ${total_dividends:.2f}")
        for div in dividend_list:
            logger.info(f"  {div['date']}: ${div['amount']:.2f}")
        
        return total_dividends, current_price, dividend_list
        
    except Exception as e:
        logger.error(f"Error fetching Yahoo Finance data for {symbol}: {str(e)}")
        return None, None, []

def get_investsmart_data(symbol):
    """Scrape dividend and franking data from InvestSMART"""
    try:
        # Remove .AX suffix for InvestSMART URL
        clean_symbol = symbol.replace('.AX', '')
        url = f"https://www.investsmart.com.au/shares/{clean_symbol}/dividends"
        
        logger.info(f"Scraping InvestSMART data from: {url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find dividend table
        dividend_table = soup.find('table', class_='table-dividend-history')
        if not dividend_table:
            logger.warning(f"No dividend table found for {symbol}")
            return None, []
        
        tbody = dividend_table.find('tbody')
        rows = tbody.find_all('tr') if tbody else []
        
        dividend_data = []
        total_franking = 0
        franking_count = 0
        
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 4:
                # Extract data from cells
                date_cell = cells[0].get_text(strip=True)
                amount_cell = cells[1].get_text(strip=True)
                franking_cell = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                
                # Parse date and amount
                dividend_date = parse_date(date_cell)
                dividend_amount = parse_amount(amount_cell)
                
                # Parse franking credit percentage
                franking_pct = None
                if franking_cell and franking_cell != "-":
                    franking_clean = clean_text(franking_cell)
                    franking_match = re.search(r'(\d+(?:\.\d+)?)%', franking_clean)
                    if franking_match:
                        franking_pct = float(franking_match.group(1))
                
                dividend_data.append({
                    'date': dividend_date,
                    'amount': dividend_amount,
                    'franking_pct': franking_pct,
                    'raw_date': date_cell,
                    'raw_amount': amount_cell,
                    'raw_franking': franking_cell
                })
                
                # Calculate average franking percentage
                if franking_pct is not None:
                    total_franking += franking_pct
                    franking_count += 1
        
        # Calculate average franking percentage
        avg_franking_pct = total_franking / franking_count if franking_count > 0 else None
        
        logger.info(f"InvestSMART: Found {len(dividend_data)} dividend entries")
        logger.info(f"Average franking percentage: {avg_franking_pct:.2f}%" if avg_franking_pct else "No franking data")
        
        return avg_franking_pct, dividend_data
        
    except Exception as e:
        logger.error(f"Error scraping InvestSMART data for {symbol}: {str(e)}")
        return None, []

def filter_dividends_for_fy(dividend_data, start_date, end_date):
    """Filter dividend data for the financial year period"""
    fy_dividends = []
    total_amount = 0
    
    for div in dividend_data:
        if div['date'] and div['amount'] is not None:
            if start_date <= div['date'] <= end_date:
                fy_dividends.append(div)
                total_amount += div['amount']
    
    return fy_dividends, total_amount

@app.route('/')
def index():
    """Main page with search interface"""
    return render_template('index.html')

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/stock')
def get_stock_data():
    """Get dividend and franking data for a stock symbol"""
    symbol = request.args.get('symbol', '').upper().strip()
    
    if not symbol:
        return jsonify({'error': 'Symbol parameter is required'}), 400
    
    try:
        # Get the most recent completed financial year
        fy_start, fy_end = get_most_recent_completed_fy()
        
        logger.info(f"Processing request for symbol: {symbol}")
        logger.info(f"Financial year period: {fy_start.date()} to {fy_end.date()}")
        
        # Get data from Yahoo Finance (primary source for dividends)
        yahoo_total, current_price, yahoo_dividends = get_yahoo_finance_dividends(symbol, fy_start, fy_end)
        
        # Get data from InvestSMART (for franking credits)
        investsmart_franking, investsmart_data = get_investsmart_data(symbol)
        
        # Filter InvestSMART data for the financial year
        fy_investsmart_dividends, investsmart_total = filter_dividends_for_fy(investsmart_data, fy_start, fy_end)
        
        # Use Yahoo Finance dividends as primary source, InvestSMART for franking
        final_dividend_total = yahoo_total if yahoo_total is not None else 0.0
        final_franking_pct = investsmart_franking
        
        # If Yahoo Finance failed, try to use InvestSMART dividend data
        if yahoo_total is None and investsmart_total > 0:
            final_dividend_total = investsmart_total
            logger.info(f"Using InvestSMART dividend data: ${investsmart_total:.2f}")
        
        result = {
            'symbol': symbol,
            'financial_year': {
                'start': fy_start.strftime('%Y-%m-%d'),
                'end': fy_end.strftime('%Y-%m-%d'),
                'description': f"FY {fy_start.year}-{fy_end.year}"
            },
            'current_price': current_price,
            'total_dividends': final_dividend_total,
            'franking_percentage': final_franking_pct,
            'dividend_count': len(yahoo_dividends) if yahoo_dividends else len(fy_investsmart_dividends),
            'data_sources': {
                'dividends': 'Yahoo Finance' if yahoo_total is not None else 'InvestSMART',
                'franking': 'InvestSMART' if investsmart_franking is not None else None,
                'price': 'Yahoo Finance' if current_price is not None else None
            },
            'dividend_details': yahoo_dividends if yahoo_dividends else [
                {
                    'date': div['date'].strftime('%Y-%m-%d'),
                    'amount': div['amount']
                }
                for div in fy_investsmart_dividends if div['amount'] is not None
            ]
        }
        
        logger.info(f"Final result for {symbol}: ${final_dividend_total:.2f} dividends, {final_franking_pct:.2f}% franking" if final_franking_pct else f"Final result for {symbol}: ${final_dividend_total:.2f} dividends, no franking data")
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error processing request for {symbol}: {str(e)}")
        return jsonify({
            'error': f'Failed to fetch data for {symbol}',
            'details': str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
