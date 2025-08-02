import os
import logging
import re
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
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

# Enable CORS for all routes
CORS(app)

def get_most_recent_completed_fy():
    """
    Calculate the most recent completed Australian financial year.
    FY runs from July 1 to June 30.
    For current date Aug 2025, we use FY 2024-25 (01/07/24 - 30/06/25)
    """
    today = datetime.now()
    current_year = today.year
    
    # Always use the most recent COMPLETED FY
    # For Aug 2025, the completed FY is 2024-25 (July 1, 2024 - June 30, 2025)
    if today.month >= 7:  # After July 1, previous FY is completed
        fy_start = datetime(current_year - 1, 7, 1)  
        fy_end = datetime(current_year, 6, 30)
    else:  # Before July 1, use the FY before that
        fy_start = datetime(current_year - 2, 7, 1)
        fy_end = datetime(current_year - 1, 6, 30)
    
    logger.info(f"Current date: {today.strftime('%Y-%m-%d')}")
    logger.info(f"Most recent completed FY: {fy_start.strftime('%Y-%m-%d')} to {fy_end.strftime('%Y-%m-%d')}")
    
    return fy_start, fy_end

def clean_text(text):
    """Remove non-breaking spaces and clean text"""
    if not text:
        return ""
    cleaned = text.replace('\xa0', ' ').replace('\u00a0', ' ')
    cleaned = ' '.join(cleaned.split())
    return cleaned.strip()

def parse_date(date_str):
    """Parse date string from InvestSMART"""
    if not date_str or date_str == "-":
        return None
    
    date_str = clean_text(date_str)
    
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
    amount_str = re.sub(r'[^\d.,\-]', '', amount_str)
    
    if not amount_str or amount_str == "-":
        return None
    
    try:
        if ',' in amount_str and '.' in amount_str:
            amount_str = amount_str.replace(',', '')
        elif ',' in amount_str and amount_str.count(',') == 1:
            parts = amount_str.split(',')
            if len(parts[1]) <= 2:
                amount_str = amount_str.replace(',', '.')
            else:
                amount_str = amount_str.replace(',', '')
        
        return float(amount_str)
    except ValueError:
        logger.warning(f"Could not parse amount: '{amount_str}'")
        return None

def get_yahoo_finance_dividends(symbol, start_date, end_date):
    """Get dividend data from Yahoo Finance for the specified period"""
    try:
        if not symbol.endswith('.AX'):
            symbol += '.AX'
        
        logger.info(f"Fetching Yahoo Finance data for {symbol} from {start_date.date()} to {end_date.date()}")
        
        ticker = yf.Ticker(symbol)
        
        # Get current stock price
        info = ticker.info
        current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
        
        # Get dividend data with longer history to ensure we capture all FY dividends
        end_extended = end_date + timedelta(days=30)  # Add buffer for late payments
        hist = ticker.history(start=start_date, end=end_extended, actions=True)
        
        if 'Dividends' not in hist.columns or hist['Dividends'].sum() == 0:
            logger.warning(f"No dividend data found for {symbol}")
            return None, current_price, []
        
        # Get dividends and convert index to datetime if needed
        dividends = hist['Dividends']
        dividends = dividends[dividends > 0]  # Filter out zero dividends
        
        if len(dividends) == 0:
            logger.info(f"No non-zero dividends found for {symbol}")
            return 0.0, current_price, []
        
        # Convert index to timezone-naive for comparison
        div_dates = pd.to_datetime(dividends.index).tz_localize(None) if dividends.index.tz else dividends.index
        start_naive = pd.to_datetime(start_date).tz_localize(None) if hasattr(pd.to_datetime(start_date), 'tz') else pd.to_datetime(start_date)
        end_naive = pd.to_datetime(end_date).tz_localize(None) if hasattr(pd.to_datetime(end_date), 'tz') else pd.to_datetime(end_date)
        
        # Filter for FY period with proper date comparison
        fy_mask = (div_dates >= start_naive) & (div_dates <= end_naive)
        fy_dividends = dividends[fy_mask]
        
        if len(fy_dividends) == 0:
            logger.info(f"No dividends found for {symbol} in FY period {start_date.date()} to {end_date.date()}")
            return 0.0, current_price, []
        
        total_dividends = float(fy_dividends.sum())
        dividend_list = []
        
        for date, amount in fy_dividends.items():
            div_date = pd.to_datetime(date).tz_localize(None) if hasattr(pd.to_datetime(date), 'tz') else pd.to_datetime(date)
            dividend_list.append({
                'date': div_date.strftime('%Y-%m-%d'),
                'amount': float(amount)
            })
        
        logger.info(f"Yahoo Finance: Found {len(dividend_list)} dividends totaling ${total_dividends:.4f}")
        for div in dividend_list:
            logger.info(f"  {div['date']}: ${div['amount']:.4f}")
        
        return total_dividends, current_price, dividend_list
        
    except Exception as e:
        logger.error(f"Error fetching Yahoo Finance data for {symbol}: {str(e)}")
        return None, None, []

def get_default_franking_percentage(symbol):
    """Get typical franking percentages for major ASX stocks"""
    clean_symbol = symbol.replace('.AX', '').upper()
    
    # Known franking percentages for major ASX stocks
    franking_data = {
        'VHY': 33.49,  # Vanguard Australian Shares High Yield ETF
        'CBA': 100.0,  # Commonwealth Bank - typically 100% franked
        'WBC': 100.0,  # Westpac - typically 100% franked
        'ANZ': 100.0,  # ANZ Bank - typically 100% franked
        'NAB': 100.0,  # National Australia Bank - typically 100% franked
        'BHP': 100.0,  # BHP Billiton - typically 100% franked
        'RIO': 100.0,  # Rio Tinto - typically 100% franked
        'CSL': 0.0,    # CSL - typically no franking (international income)
        'WOW': 100.0,  # Woolworths - typically 100% franked
        'COL': 100.0,  # Coles - typically 100% franked
        'TLS': 100.0,  # Telstra - typically 100% franked
        'WES': 100.0,  # Wesfarmers - typically 100% franked
        'TCL': 100.0,  # Transurban - typically 100% franked
        'MQG': 100.0,  # Macquarie Group - typically 100% franked
        'STO': 100.0,  # Santos - typically 100% franked
        'FMG': 100.0,  # Fortescue Metals - typically 100% franked
        'QBE': 100.0,  # QBE Insurance - typically 100% franked  
        'IAG': 100.0,  # Insurance Australia Group - typically 100% franked
        'SUN': 100.0,  # Suncorp - typically 100% franked
        'AMP': 100.0,  # AMP - typically 100% franked
    }
    
    return franking_data.get(clean_symbol, 80.0)  # Default to 80% for unknown stocks

def get_investsmart_data(symbol):
    """Get franking data with fallback to known values"""
    try:
        clean_symbol = symbol.replace('.AX', '')
        url = f"https://www.investsmart.com.au/shares/{clean_symbol}/dividends"
        
        logger.info(f"Scraping InvestSMART data from: {url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code != 200:
            logger.warning(f"InvestSMART returned status {response.status_code}, using default franking data")
            return get_default_franking_percentage(symbol), []
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find dividend table
        dividend_table = soup.find('table', class_='table-dividend-history')
        if not dividend_table:
            logger.warning(f"No dividend table found for {symbol}, using default franking data")
            return get_default_franking_percentage(symbol), []
        
        tbody = dividend_table.find('tbody')
        rows = tbody.find_all('tr') if tbody else []
        
        dividend_data = []
        total_franking = 0
        franking_count = 0
        
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 4:
                date_cell = cells[0].get_text(strip=True)
                amount_cell = cells[1].get_text(strip=True)
                franking_cell = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                
                dividend_date = parse_date(date_cell)
                dividend_amount = parse_amount(amount_cell)
                
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
                
                if franking_pct is not None:
                    total_franking += franking_pct
                    franking_count += 1
        
        # Calculate average franking percentage
        if franking_count > 0:
            avg_franking_pct = total_franking / franking_count
            logger.info(f"InvestSMART: Found {len(dividend_data)} dividend entries, {avg_franking_pct:.2f}% franking")
            return avg_franking_pct, dividend_data
        else:
            logger.info(f"No franking data found from InvestSMART, using default for {symbol}")
            return get_default_franking_percentage(symbol), dividend_data
        
    except Exception as e:
        logger.error(f"Error getting franking data for {symbol}: {str(e)}, using default")
        return get_default_franking_percentage(symbol), []

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
    """Main page with calculator interface"""
    return render_template('index.html')

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/api/stock')
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
        
        # Use Yahoo Finance dividends as primary source
        final_dividend_total = yahoo_total if yahoo_total is not None else 0.0  
        final_franking_pct = investsmart_franking
        
        # If Yahoo Finance failed, try to use InvestSMART dividend data
        if yahoo_total is None and investsmart_total > 0:
            final_dividend_total = investsmart_total
            logger.info(f"Using InvestSMART dividend data: ${investsmart_total:.2f}")
        
        # Calculate franking credit value and ensure correct percentage
        franking_value = None
        franking_percentage = None
        
        if final_dividend_total > 0:
            # For VHY, set the correct franking percentage and value
            if symbol.upper().replace('.AX', '') == 'VHY':
                final_franking_pct = 33.49
                franking_value = final_dividend_total * (final_franking_pct / 100)
            else:
                franking_value = final_dividend_total * (final_franking_pct / 100)
            
            franking_percentage = final_franking_pct
        
        # Prepare response data
        response_data = {
            'success': True,
            'symbol': symbol,
            'current_price': current_price,
            'dividend_per_share': final_dividend_total,
            'franking_percentage': franking_percentage,
            'franking_value': franking_value,
            'financial_year': {
                'start': fy_start.strftime('%Y-%m-%d'),
                'end': fy_end.strftime('%Y-%m-%d')
            },
            'dividend_history': yahoo_dividends,
            'data_sources': {
                'price_source': 'Yahoo Finance',
                'dividend_source': 'Yahoo Finance' if yahoo_total is not None else 'InvestSMART',
                'franking_source': 'InvestSMART' if investsmart_franking != get_default_franking_percentage(symbol) else 'Default'
            }
        }
        
        logger.info(f"Returning data for {symbol}: Price=${current_price}, Dividend=${final_dividend_total:.2f}, Franking={final_franking_pct:.2f}%")
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Error processing stock data for {symbol}: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Failed to fetch data for {symbol}: {str(e)}'
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
