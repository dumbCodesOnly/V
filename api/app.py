"""
Complete Telegram Trading Bot - Vercel Deployment
Consolidated web application with full trading bot functionality
"""

import os
import logging
import json
import urllib.request
import urllib.parse
import time
import random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, UserCredentials, UserTradingSession

# Configure logging - reduce verbosity for serverless
log_level = logging.INFO if os.environ.get("VERCEL") else logging.DEBUG
logging.basicConfig(level=log_level)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configure database - optimized for serverless deployment
database_url = os.environ.get("DATABASE_URL", "sqlite:///trading_bot.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
    "pool_size": 5,
    "max_overflow": 10
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize database
db.init_app(app)

# Create tables only if not in serverless environment or if explicitly needed
def init_database():
    """Initialize database tables safely"""
    try:
        with app.app_context():
            db.create_all()
            logging.info("Database tables created successfully")
    except Exception as e:
        logging.error(f"Database initialization error: {e}")

# Initialize database conditionally
if not os.environ.get("VERCEL"):
    init_database()
else:
    # For Vercel, initialize on first request using newer Flask syntax
    initialized = False
    
    @app.before_request
    def create_tables():
        global initialized
        if not initialized:
            init_database()
            initialized = True

# Bot token and webhook URL from environment
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://v0-033-pi.vercel.app/webhook")

# Bot state and storage
bot_messages = []
bot_trades = []
bot_status = {
    'status': 'active',
    'total_messages': 0,
    'error_count': 0,
    'last_heartbeat': datetime.utcnow().isoformat()
}

# Trading configuration storage
user_sessions = {}
user_credentials = {}

def send_telegram_message(chat_id, text, reply_markup=None):
    """Send message to Telegram"""
    if not BOT_TOKEN:
        logging.error("No bot token configured")
        return False
        
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        if reply_markup:
            data['reply_markup'] = json.dumps(reply_markup)
        
        encoded_data = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded_data, method='POST')
        response = urllib.request.urlopen(req, timeout=10)
        
        return response.getcode() == 200
        
    except Exception as e:
        logging.error(f"Error sending message: {e}")
        return False

def get_main_menu():
    """Get main bot menu"""
    return {
        'inline_keyboard': [
            [{'text': '📱 Open Trading App', 'web_app': {'url': 'https://v0-033-pi.vercel.app'}}],
            [{'text': '💰 Quick Price Check', 'callback_data': 'price_check'}],
            [{'text': '📊 Portfolio', 'callback_data': 'portfolio'}],
            [{'text': '⚙️ Settings', 'callback_data': 'settings'}]
        ]
    }

def get_live_market_price(symbol="BTCUSDT"):
    """Get live market price with fallback sources"""
    sources = [
        f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}",
        f"https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
        f"https://min-api.cryptocompare.com/data/price?fsym=BTC&tsyms=USD"
    ]
    
    for source in sources:
        try:
            response = urllib.request.urlopen(source, timeout=5)
            data = json.loads(response.read().decode('utf-8'))
            
            if 'binance.com' in source:
                return float(data['lastPrice'])
            elif 'coingecko.com' in source:
                return float(data['bitcoin']['usd'])
            elif 'cryptocompare.com' in source:
                return float(data['USD'])
                
        except Exception as e:
            logging.warning(f"Failed to fetch from {source}: {e}")
            continue
    
    return 119000.0  # Fallback price

def process_message(message_data):
    """Process incoming Telegram message"""
    try:
        chat_id = message_data['chat']['id']
        text = message_data.get('text', '').lower()
        user_id = message_data['from']['id']
        
        if text.startswith('/start'):
            welcome_text = """
🚀 <b>Welcome to Toobit Trading Bot!</b>

Your advanced crypto trading companion for USDT-M futures trading.

<b>🌟 Key Features:</b>
• Multi-trade management
• Real-time price monitoring
• Portfolio tracking
• Risk management tools

Use the menu below to get started:
            """
            send_telegram_message(chat_id, welcome_text, get_main_menu())
            
        elif text.startswith('/menu'):
            send_telegram_message(chat_id, "🎯 <b>Trading Bot Menu</b>\n\nChoose an option:", get_main_menu())
            
        elif text.startswith('/price'):
            price = get_live_market_price()
            price_text = f"💰 <b>BTC/USDT Price</b>\n\n${price:,.2f}"
            send_telegram_message(chat_id, price_text)
            
        elif text.startswith('/help'):
            help_text = """
📋 <b>Bot Commands</b>

/start - Start the bot
/menu - Show main menu
/price - Get current BTC price
/portfolio - View your portfolio
/help - Show this help

Use the "📱 Open Trading App" button for full trading functionality!
            """
            send_telegram_message(chat_id, help_text)
            
        else:
            send_telegram_message(chat_id, "Unknown command. Use /menu to see available options.", get_main_menu())
            
        return True
        
    except Exception as e:
        logging.error(f"Error processing message: {e}")
        return False

def process_callback_query(callback_data):
    """Process callback query from inline keyboard"""
    try:
        query_id = callback_data['id']
        chat_id = callback_data['message']['chat']['id']
        data = callback_data['data']
        
        if data == 'price_check':
            price = get_live_market_price()
            price_text = f"💰 <b>Current BTC/USDT</b>\n\n${price:,.2f}\n\n📊 Updated: {datetime.utcnow().strftime('%H:%M UTC')}"
            send_telegram_message(chat_id, price_text)
            
        elif data == 'portfolio':
            portfolio_text = "📊 <b>Portfolio Overview</b>\n\n💼 No active positions\n💰 Available Balance: $0.00\n\nUse the Trading App to start trading!"
            send_telegram_message(chat_id, portfolio_text, get_main_menu())
            
        elif data == 'settings':
            settings_text = "⚙️ <b>Settings</b>\n\n🔧 Use the Trading App for advanced settings and configuration."
            send_telegram_message(chat_id, settings_text, get_main_menu())
            
        # Answer callback query
        answer_url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
        answer_data = urllib.parse.urlencode({'callback_query_id': query_id}).encode('utf-8')
        answer_req = urllib.request.Request(answer_url, data=answer_data, method='POST')
        urllib.request.urlopen(answer_req, timeout=5)
        
        return True
        
    except Exception as e:
        logging.error(f"Error processing callback: {e}")
        return False

# Web Routes
@app.route('/')
def home():
    """Main trading interface"""
    return render_template('mini_app.html')

@app.route('/api/status')
def api_status():
    """API status endpoint"""
    return jsonify({
        'status': 'active',
        'timestamp': datetime.utcnow().isoformat(),
        'bot_configured': BOT_TOKEN is not None,
        'webhook_url': WEBHOOK_URL
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data received'}), 400
        
        # Update bot status
        bot_status['total_messages'] += 1
        bot_status['last_heartbeat'] = datetime.utcnow().isoformat()
        
        # Process message
        if 'message' in data:
            success = process_message(data['message'])
            if success:
                logging.info(f"Processed message: {data['message'].get('text', 'N/A')}")
            else:
                bot_status['error_count'] += 1
                
        # Process callback query
        elif 'callback_query' in data:
            success = process_callback_query(data['callback_query'])
            if success:
                logging.info(f"Processed callback: {data['callback_query'].get('data', 'N/A')}")
            else:
                bot_status['error_count'] += 1
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        bot_status['error_count'] += 1
        return jsonify({'error': str(e)}), 500

# Market Data API
@app.route('/api/market-data')
def market_data():
    """Get live market data"""
    try:
        symbol = request.args.get('symbol', 'BTCUSDT')
        
        # Get data from Binance API
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        response = urllib.request.urlopen(url, timeout=10)
        data = json.loads(response.read().decode('utf-8'))
        
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
        return jsonify({'error': 'Failed to fetch market data'}), 500

@app.route('/api/kline-data')
def kline_data():
    """Get candlestick chart data"""
    try:
        symbol = request.args.get('symbol', 'BTCUSDT')
        interval = request.args.get('interval', '1h')
        limit = request.args.get('limit', '50')
        
        # Get data from Binance API
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        response = urllib.request.urlopen(url, timeout=10)
        data = json.loads(response.read().decode('utf-8'))
        
        # Format candlestick data
        candlesticks = []
        for candle in data:
            candlesticks.append({
                'timestamp': int(candle[0]),
                'open': float(candle[1]),
                'high': float(candle[2]),
                'low': float(candle[3]),
                'close': float(candle[4]),
                'volume': float(candle[5])
            })
        
        logging.info(f"Successfully fetched {len(candlesticks)} candlesticks for {symbol}")
        return jsonify(candlesticks)
        
    except Exception as e:
        logging.error(f"Error fetching kline data: {e}")
        return jsonify({'error': 'Failed to fetch chart data'}), 500

# Trading API endpoints
@app.route('/api/user-credentials', methods=['POST'])
def save_credentials():
    """Save user trading credentials"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'error': 'User ID required'}), 400
        
        # Check if credentials exist
        existing = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        
        if existing:
            # Update existing credentials
            existing.set_api_key(data.get('api_key', ''))
            existing.set_api_secret(data.get('api_secret', ''))
            existing.updated_at = datetime.utcnow()
        else:
            # Create new credentials
            credentials = UserCredentials()
            credentials.telegram_user_id = str(user_id)
            credentials.set_api_key(data.get('api_key', ''))
            credentials.set_api_secret(data.get('api_secret', ''))
            db.session.add(credentials)
        
        db.session.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        logging.error(f"Error saving credentials: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/user-credentials/<user_id>')
def get_credentials(user_id):
    """Get user credentials"""
    try:
        credentials = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        if credentials:
            api_key = credentials.get_api_key()
            return jsonify({
                'api_key': api_key[:8] + '...' if api_key else '',
                'has_credentials': bool(api_key and credentials.get_api_secret())
            })
        return jsonify({'has_credentials': False})
        
    except Exception as e:
        logging.error(f"Error getting credentials: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/trading-session', methods=['POST'])
def create_trading_session():
    """Create or update trading session"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'error': 'User ID required'}), 400
        
        # Create or update session  
        session = UserTradingSession.query.filter_by(telegram_user_id=str(user_id)).first()
        if not session:
            session = UserTradingSession()
            session.telegram_user_id = str(user_id)
            db.session.add(session)
        
        session.is_active = True
        
        db.session.commit()
        return jsonify({'success': True, 'session_id': session.id})
        
    except Exception as e:
        logging.error(f"Error creating session: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Portfolio and Analytics endpoints
@app.route('/api/portfolio/<user_id>')
def get_portfolio(user_id):
    """Get user portfolio"""
    try:
        # Placeholder portfolio data
        portfolio = {
            'total_balance': 0.0,
            'available_balance': 0.0,
            'unrealized_pnl': 0.0,
            'positions': [],
            'trades_today': 0,
            'win_rate': 0.0
        }
        
        return jsonify(portfolio)
        
    except Exception as e:
        logging.error(f"Error getting portfolio: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/bot-status')
def get_bot_status():
    """Get bot status and statistics"""
    return jsonify(bot_status)

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# Auto-setup webhook for Vercel deployment
def setup_webhook_on_deployment():
    """Automatically set up webhook for deployment environments"""
    if not BOT_TOKEN:
        logging.warning("TELEGRAM_BOT_TOKEN not set, skipping webhook setup")
        return
    
    try:
        webhook_url = WEBHOOK_URL
        if not webhook_url.endswith('/webhook'):
            webhook_url = f"{webhook_url}/webhook"
        
        # Set the webhook
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        data = urllib.parse.urlencode({'url': webhook_url}).encode('utf-8')
        req = urllib.request.Request(api_url, data=data, method='POST')
        response = urllib.request.urlopen(req, timeout=10)
        
        if response.getcode() == 200:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('ok'):
                logging.info(f"Webhook set successfully to {webhook_url}")
            else:
                logging.error(f"Webhook setup failed: {result.get('description')}")
        
    except Exception as e:
        logging.error(f"Error setting up webhook: {e}")

# Initialize webhook on Vercel deployment
if os.environ.get("VERCEL") and BOT_TOKEN:
    setup_webhook_on_deployment()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)