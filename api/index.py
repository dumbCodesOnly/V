"""
Vercel serverless function entry point
"""
from flask import Flask, request, jsonify, render_template
from datetime import datetime, timedelta
import urllib.request
import urllib.parse
import json
import random
import time
import os
import logging

# Set Vercel environment variable
os.environ["VERCEL"] = "1"

# Configure logging for serverless
logging.basicConfig(level=logging.INFO)

# Create Flask app directly in serverless function
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Simple storage for demo (in production, use proper database)
bot_messages = []
bot_trades = []
bot_status = {
    'status': 'active',
    'total_messages': 5,
    'total_trades': 2,
    'error_count': 0,
    'last_heartbeat': datetime.utcnow().isoformat()
}

# Demo data initialization
def initialize_demo_data():
    global bot_messages, bot_trades
    
    bot_messages = [
        {
            'id': 1,
            'username': 'demo_user',
            'message': 'Setup API credentials',
            'timestamp': datetime.utcnow().isoformat(),
            'type': 'command'
        },
        {
            'id': 2,
            'username': 'demo_user', 
            'message': 'Create new trade configuration',
            'timestamp': datetime.utcnow().isoformat(),
            'type': 'command'
        }
    ]
    
    bot_trades = [
        {
            'id': 1,
            'user_id': 'demo_user',
            'symbol': 'BTCUSDT',
            'side': 'long',
            'entry_price': 45000,
            'quantity': 0.001,
            'status': 'active',
            'pnl': 250.50,
            'created_at': datetime.utcnow().isoformat()
        },
        {
            'id': 2,
            'user_id': 'demo_user',
            'symbol': 'ETHUSDT', 
            'side': 'short',
            'entry_price': 3200,
            'quantity': 0.01,
            'status': 'closed',
            'pnl': -125.25,
            'created_at': datetime.utcnow().isoformat()
        }
    ]

# Initialize demo data
initialize_demo_data()

# Routes
@app.route('/')
def index():
    return render_template('mini_app.html')

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})

@app.route('/api/market-data')
def market_data():
    """Fetch live market data from Binance API"""
    try:
        symbol = request.args.get('symbol', 'BTCUSDT')
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            
        market_info = {
            'symbol': data['symbol'],
            'price': float(data['lastPrice']),
            'change': float(data['priceChange']),
            'changePercent': float(data['priceChangePercent']),
            'high': float(data['highPrice']),
            'low': float(data['lowPrice']),
            'volume': float(data['volume']),
            'quoteVolume': float(data['quoteVolume']),
            'openPrice': float(data['openPrice']),
            'timestamp': int(time.time() * 1000)
        }
        
        logging.info(f"Successfully fetched market data for {symbol}")
        return jsonify(market_info)
        
    except Exception as e:
        logging.error(f"Error fetching market data: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/kline-data')
def kline_data():
    """Fetch live kline/candlestick data from Binance API"""
    try:
        symbol = request.args.get('symbol', 'BTCUSDT')
        interval = request.args.get('interval', '1h')
        limit = request.args.get('limit', '50')
        
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
        
        # Convert to Chart.js format
        chart_data = []
        for kline in data:
            chart_data.append({
                'x': int(kline[0]),  # timestamp
                'o': float(kline[1]),  # open
                'h': float(kline[2]),  # high
                'l': float(kline[3]),  # low
                'c': float(kline[4])   # close
            })
        
        logging.info(f"Successfully fetched {len(chart_data)} candlesticks for {symbol}")
        return jsonify(chart_data)
        
    except Exception as e:
        logging.error(f"Error fetching kline data: {e}")
        return jsonify({'error': str(e)}), 500

# Vercel serverless function handler
def handler(request):
    return app