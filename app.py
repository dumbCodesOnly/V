import os
import logging
from flask import Flask, request, jsonify, render_template
from datetime import datetime
import urllib.request
import urllib.parse
import json

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Bot token and webhook URL from environment
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# Simple in-memory storage for the bot (replace with database in production)
bot_messages = []
bot_trades = []
bot_status = {
    'status': 'inactive',
    'total_messages': 0,
    'total_trades': 0,
    'error_count': 0,
    'last_heartbeat': None
}

@app.route('/')
def dashboard():
    """Bot dashboard"""
    return render_template('dashboard.html')

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/status')
def get_bot_status():
    """Get bot status"""
    # Check if bot is active (heartbeat within last 5 minutes)
    if bot_status['last_heartbeat']:
        time_diff = datetime.utcnow() - datetime.fromisoformat(bot_status['last_heartbeat'])
        is_active = time_diff.total_seconds() < 300  # 5 minutes
        bot_status['status'] = 'active' if is_active else 'inactive'
    
    return jsonify(bot_status)

@app.route('/api/recent-messages')
def recent_messages():
    """Get recent bot messages"""
    return jsonify(bot_messages[-10:])  # Last 10 messages

@app.route('/api/recent-trades')
def recent_trades():
    """Get recent trades"""
    return jsonify(bot_trades[-10:])  # Last 10 trades

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook"""
    try:
        # Get the JSON data from Telegram
        json_data = request.get_json()
        
        if not json_data:
            logging.warning("No JSON data received")
            return jsonify({'status': 'error', 'message': 'No JSON data'}), 400
        
        # Process the update
        if 'message' in json_data:
            message = json_data['message']
            user = message.get('from', {})
            chat_id = message.get('chat', {}).get('id')
            text = message.get('text', '')
            
            # Update bot status
            bot_status['last_heartbeat'] = datetime.utcnow().isoformat()
            bot_status['total_messages'] += 1
            
            # Log the message
            bot_messages.append({
                'id': len(bot_messages) + 1,
                'user_id': str(user.get('id', 'unknown')),
                'username': user.get('username', 'Unknown'),
                'message': text,
                'timestamp': datetime.utcnow().isoformat(),
                'command_type': text.split()[0] if text.startswith('/') else 'message'
            })
            
            # Process the command
            response_text, keyboard = process_command(text, chat_id, user)
            
            # Send response back to Telegram
            if BOT_TOKEN and chat_id:
                send_telegram_message(chat_id, response_text, keyboard)
        
        # Handle callback queries from inline keyboards
        elif 'callback_query' in json_data:
            callback_query = json_data['callback_query']
            chat_id = callback_query['message']['chat']['id']
            message_id = callback_query['message']['message_id']
            callback_data = callback_query['data']
            user = callback_query.get('from', {})
            
            # Update bot status
            bot_status['last_heartbeat'] = datetime.utcnow().isoformat()
            
            # Log the callback
            bot_messages.append({
                'id': len(bot_messages) + 1,
                'user_id': str(user.get('id', 'unknown')),
                'username': user.get('username', 'Unknown'),
                'message': f"[CALLBACK] {callback_data}",
                'timestamp': datetime.utcnow().isoformat(),
                'command_type': 'callback'
            })
            
            # Process the callback
            response_text, keyboard = handle_callback_query(callback_data, chat_id, user)
            
            # Send response back to Telegram
            if BOT_TOKEN and chat_id and response_text:
                edit_telegram_message(chat_id, message_id, response_text, keyboard)
            
            # Answer the callback query to remove loading state
            answer_callback_query(callback_query['id'])
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        logging.error(f"Error processing webhook: {e}")
        bot_status['error_count'] += 1
        return jsonify({'status': 'error', 'message': str(e)}), 500

def process_command(text, chat_id, user):
    """Process bot commands"""
    if not text:
        return "🤔 I didn't receive any text. Type /help to see available commands.", None
    
    if text.startswith('/start'):
        welcome_text = f"""🤖 Welcome to Trading Bot, {user.get('first_name', 'User')}!

Use the menu below to navigate:"""
        return welcome_text, get_main_menu()
    
    elif text.startswith('/menu'):
        return "📋 Main Menu:", get_main_menu()
    
    elif text.startswith('/help'):
        help_text = """📚 Trading Bot Help

🔹 /start - Show main menu
🔹 /menu - Access main menu
🔹 /price <symbol> - Get current price (e.g., /price BTCUSDT)
🔹 /buy <symbol> <quantity> - Place buy order
🔹 /sell <symbol> <quantity> - Place sell order

Use the interactive menu for advanced features like multi-trade management, portfolio analytics, and configuration."""
        return help_text, get_main_menu()
    
    elif text.startswith('/price'):
        parts = text.split()
        if len(parts) < 2:
            return "❌ Please provide a symbol. Example: /price BTCUSDT", None
        
        symbol = parts[1].upper()
        price = get_mock_price(symbol)
        if price:
            return f"💰 {symbol}: ${price:.4f}", None
        else:
            return f"❌ Could not fetch price for {symbol}", None
    
    elif text.startswith('/buy') or text.startswith('/sell'):
        parts = text.split()
        if len(parts) < 3:
            action = parts[0][1:]  # Remove '/'
            return f"❌ Please provide symbol and quantity. Example: /{action} BTCUSDT 0.001", None
        
        action = parts[0][1:]  # Remove '/'
        symbol = parts[1].upper()
        try:
            quantity = float(parts[2])
        except ValueError:
            return "❌ Invalid quantity. Please provide a valid number.", None
        
        # Mock trade execution
        price = get_mock_price(symbol)
        if price and quantity > 0:
            # Record the trade
            trade = {
                'id': len(bot_trades) + 1,
                'user_id': str(user.get('id', 'unknown')),
                'symbol': symbol,
                'action': action,
                'quantity': quantity,
                'price': price,
                'status': 'executed',
                'timestamp': datetime.utcnow().isoformat()
            }
            bot_trades.append(trade)
            bot_status['total_trades'] += 1
            
            return f"✅ {action.capitalize()} order executed: {quantity} {symbol} at ${price:.4f}", None
        else:
            return f"❌ {action.capitalize()} order failed: Invalid symbol or quantity", None
    
    elif text.startswith('/portfolio'):
        return "📊 Your portfolio is empty. Start trading to see your holdings!", None
    
    elif text.startswith('/trades'):
        user_trades = [t for t in bot_trades if t['user_id'] == str(user.get('id', 'unknown'))]
        if not user_trades:
            return "📈 No recent trades found.", None
        
        response = "📈 Recent Trades:\n\n"
        for trade in user_trades[-5:]:  # Show last 5 trades
            status_emoji = "✅" if trade['status'] == "executed" else "⏳"
            response += f"{status_emoji} {trade['action'].upper()} {trade['quantity']} {trade['symbol']}"
            response += f" @ ${trade['price']:.4f}\n"
            timestamp = datetime.fromisoformat(trade['timestamp'])
            response += f"   {timestamp.strftime('%Y-%m-%d %H:%M')}\n\n"
        
        return response, None
    
    else:
        return "🤔 I didn't understand that command. Type /help to see available commands.", None

def get_mock_price(symbol):
    """Get mock price for a trading symbol"""
    mock_prices = {
        'BTCUSDT': 45000.00,
        'ETHUSDT': 3000.00,
        'ADAUSDT': 0.45,
        'DOGEUSDT': 0.08,
        'BNBUSDT': 350.00,
        'XRPUSDT': 0.60,
        'SOLUSDT': 100.00,
        'MATICUSDT': 0.85,
        'LTCUSDT': 150.00,
        'AVAXUSDT': 25.00
    }
    
    import random
    base_price = mock_prices.get(symbol)
    if base_price:
        # Add small random variation (+/- 2%)
        variation = random.uniform(-0.02, 0.02)
        return base_price * (1 + variation)
    return None

def send_telegram_message(chat_id, text, keyboard=None):
    """Send message to Telegram"""
    if not BOT_TOKEN:
        logging.warning("BOT_TOKEN not set, cannot send message")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        if keyboard:
            data['reply_markup'] = json.dumps(keyboard)
        
        data_encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=data_encoded, method='POST')
        response = urllib.request.urlopen(req, timeout=10)
        return response.getcode() == 200
    except Exception as e:
        logging.error(f"Error sending Telegram message: {e}")
        return False

def edit_telegram_message(chat_id, message_id, text, keyboard=None):
    """Edit existing Telegram message"""
    if not BOT_TOKEN:
        logging.warning("BOT_TOKEN not set, cannot edit message")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        data = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        if keyboard:
            data['reply_markup'] = json.dumps(keyboard)
        
        data_encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=data_encoded, method='POST')
        response = urllib.request.urlopen(req, timeout=10)
        return response.getcode() == 200
    except Exception as e:
        logging.error(f"Error editing Telegram message: {e}")
        return False

def answer_callback_query(callback_query_id, text=""):
    """Answer callback query to remove loading state"""
    if not BOT_TOKEN:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
        data = {
            'callback_query_id': callback_query_id,
            'text': text
        }
        data_encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=data_encoded, method='POST')
        response = urllib.request.urlopen(req, timeout=10)
        return response.getcode() == 200
    except Exception as e:
        logging.error(f"Error answering callback query: {e}")
        return False

def setup_webhook():
    """Setup webhook for the bot"""
    if WEBHOOK_URL and BOT_TOKEN:
        try:
            webhook_url = f"{WEBHOOK_URL}/webhook"
            data = urllib.parse.urlencode({"url": webhook_url}).encode('utf-8')
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                data=data,
                method='POST'
            )
            response = urllib.request.urlopen(req, timeout=10)
            if response.getcode() == 200:
                logging.info(f"Webhook set successfully to {webhook_url}")
                bot_status['status'] = 'active'
            else:
                logging.error(f"Failed to set webhook: HTTP {response.getcode()}")
        except Exception as e:
            logging.error(f"Error setting webhook: {e}")
    else:
        logging.warning("WEBHOOK_URL or BOT_TOKEN not provided, webhook not set")

def get_main_menu():
    """Get main menu keyboard"""
    return {
        "inline_keyboard": [
            [{"text": "🔄 Multi-Trade Manager", "callback_data": "menu_multitrade"}],
            [{"text": "⚙️ Configuration", "callback_data": "menu_config"}],
            [{"text": "📊 Trading", "callback_data": "menu_trading"}],
            [{"text": "💼 Portfolio & Analytics", "callback_data": "menu_portfolio"}],
            [{"text": "📈 Quick Price Check", "callback_data": "quick_price"}],
            [{"text": "📋 Help", "callback_data": "help"}]
        ]
    }

def get_trading_menu():
    """Get trading menu keyboard"""
    return {
        "inline_keyboard": [
            [{"text": "💱 Select Trading Pair", "callback_data": "select_pair"}],
            [{"text": "📈 Long Position", "callback_data": "set_side_long"}, 
             {"text": "📉 Short Position", "callback_data": "set_side_short"}],
            [{"text": "💰 Quick Buy", "callback_data": "quick_buy"}],
            [{"text": "💸 Quick Sell", "callback_data": "quick_sell"}],
            [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
        ]
    }

def get_portfolio_menu():
    """Get portfolio menu keyboard"""
    return {
        "inline_keyboard": [
            [{"text": "📊 Portfolio Summary", "callback_data": "portfolio_summary"}],
            [{"text": "📈 Recent Trades", "callback_data": "recent_trades"}],
            [{"text": "💹 Performance", "callback_data": "performance"}],
            [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
        ]
    }

def get_pairs_menu():
    """Get trading pairs selection menu"""
    pairs = [
        "BTC/USDT", "ETH/USDT", "BNB/USDT", "ADA/USDT",
        "SOL/USDT", "XRP/USDT", "DOT/USDT", "DOGE/USDT"
    ]
    
    keyboard = []
    for i in range(0, len(pairs), 2):
        row = []
        for j in range(2):
            if i + j < len(pairs):
                pair = pairs[i + j]
                row.append({"text": pair, "callback_data": f"pair_{pair.replace('/', '_')}"})
        keyboard.append(row)
    
    keyboard.append([{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}])
    return {"inline_keyboard": keyboard}

def handle_callback_query(callback_data, chat_id, user):
    """Handle callback query from inline keyboard"""
    try:
        # Main menu handlers
        if callback_data == "main_menu":
            return "🏠 Main Menu:", get_main_menu()
        elif callback_data == "menu_trading":
            return "📊 Trading Menu:", get_trading_menu()
        elif callback_data == "menu_portfolio":
            return "💼 Portfolio & Analytics:", get_portfolio_menu()
        elif callback_data == "select_pair":
            return "💱 Select a trading pair:", get_pairs_menu()
        elif callback_data == "help":
            help_text = """📚 Trading Bot Help

🔹 Use the menu buttons to navigate
🔹 Quick commands: /price <symbol>, /buy, /sell
🔹 Multi-trade features for advanced trading
🔹 Portfolio analytics and performance tracking

⚠️ This is a demo environment with mock data."""
            return help_text, get_main_menu()
        
        # Trading pair selection
        elif callback_data.startswith("pair_"):
            pair = callback_data.replace("pair_", "").replace("_", "/")
            symbol = pair.replace("/", "")
            price = get_mock_price(symbol)
            if price:
                response = f"💰 {pair} Current Price: ${price:.4f}\n\nWhat would you like to do?"
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "📈 Quick Buy", "callback_data": f"quick_buy_{symbol}"},
                         {"text": "📉 Quick Sell", "callback_data": f"quick_sell_{symbol}"}],
                        [{"text": "🔄 Refresh Price", "callback_data": f"pair_{pair.replace('/', '_')}"}],
                        [{"text": "🏠 Back to Pairs", "callback_data": "select_pair"}]
                    ]
                }
                return response, keyboard
            else:
                return f"❌ Could not fetch price for {pair}", get_pairs_menu()
        
        # Quick trading
        elif callback_data.startswith("quick_buy_") or callback_data.startswith("quick_sell_"):
            action = "buy" if callback_data.startswith("quick_buy_") else "sell"
            symbol = callback_data.replace(f"quick_{action}_", "")
            price = get_mock_price(symbol)
            quantity = 0.001  # Default small quantity
            
            if price:
                # Record the trade
                trade = {
                    'id': len(bot_trades) + 1,
                    'user_id': str(user.get('id', 'unknown')),
                    'symbol': symbol,
                    'action': action,
                    'quantity': quantity,
                    'price': price,
                    'status': 'executed',
                    'timestamp': datetime.utcnow().isoformat()
                }
                bot_trades.append(trade)
                bot_status['total_trades'] += 1
                
                response = f"✅ {action.capitalize()} order executed!\n\n"
                response += f"Symbol: {symbol}\n"
                response += f"Quantity: {quantity}\n"
                response += f"Price: ${price:.4f}\n"
                response += f"Total: ${quantity * price:.4f}"
                
                return response, get_trading_menu()
            else:
                return f"❌ Could not execute {action} order for {symbol}", get_trading_menu()
        
        # Portfolio handlers
        elif callback_data == "portfolio_summary":
            return "📊 Portfolio is currently empty. Start trading to see your holdings!", get_portfolio_menu()
        elif callback_data == "recent_trades":
            user_trades = [t for t in bot_trades if t['user_id'] == str(user.get('id', 'unknown'))]
            if not user_trades:
                return "📈 No recent trades found.", get_portfolio_menu()
            
            response = "📈 Recent Trades:\n\n"
            for trade in user_trades[-5:]:
                status_emoji = "✅" if trade['status'] == "executed" else "⏳"
                response += f"{status_emoji} {trade['action'].upper()} {trade['quantity']} {trade['symbol']}"
                response += f" @ ${trade['price']:.4f}\n"
                timestamp = datetime.fromisoformat(trade['timestamp'])
                response += f"   {timestamp.strftime('%Y-%m-%d %H:%M')}\n\n"
            
            return response, get_portfolio_menu()
        elif callback_data == "performance":
            user_trades = [t for t in bot_trades if t['user_id'] == str(user.get('id', 'unknown'))]
            total_trades = len(user_trades)
            if total_trades == 0:
                return "📊 No performance data available yet.", get_portfolio_menu()
            
            # Basic performance stats
            response = f"📊 Performance Summary:\n\n"
            response += f"Total Trades: {total_trades}\n"
            response += f"Status: Demo Mode\n"
            response += f"Account: Active\n\n"
            response += "📈 Start trading to see detailed performance metrics!"
            
            return response, get_portfolio_menu()
        
        # Quick price check
        elif callback_data == "quick_price":
            response = "💰 Quick Price Check:\n\n"
            symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT"]
            for symbol in symbols:
                price = get_mock_price(symbol)
                if price:
                    response += f"{symbol}: ${price:.4f}\n"
            
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔄 Refresh Prices", "callback_data": "quick_price"}],
                    [{"text": "💱 Select Pair for Trading", "callback_data": "select_pair"}],
                    [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
                ]
            }
            return response, keyboard
        
        # Placeholder handlers for other menu items
        elif callback_data == "menu_multitrade":
            return "🔄 Multi-Trade Manager\n\nAdvanced trading features coming soon!", get_main_menu()
        elif callback_data == "menu_config":
            return "⚙️ Configuration\n\nSettings and preferences coming soon!", get_main_menu()
        
        else:
            return "🤔 Unknown action. Please try again.", get_main_menu()
            
    except Exception as e:
        logging.error(f"Error handling callback query: {e}")
        return "❌ An error occurred. Please try again.", get_main_menu()

if __name__ == "__main__":
    # Setup webhook on startup
    setup_webhook()
    app.run(host="0.0.0.0", port=5000, debug=True)