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

# Multi-trade management storage
user_trade_configs = {}  # {user_id: {trade_id: TradeConfig}}
user_selected_trade = {}  # {user_id: trade_id}
trade_counter = 0

# User configuration storage
user_configs = {}  # {user_id: {setting: value}}

class TradeConfig:
    def __init__(self, trade_id, name="New Trade"):
        self.trade_id = trade_id
        self.name = name
        self.symbol = None
        self.side = None  # 'long' or 'short'
        self.amount = None
        self.leverage = 1
        self.entry_price = None
        # Take profit system - percentages and allocations
        self.take_profits = []  # List of {percentage: float, allocation: float}
        self.tp_config_step = "percentages"  # "percentages" or "allocations"
        self.stop_loss_percent = None
        self.breakeven_after = None
        self.trailing_stop = False
        self.trail_percent = None
        self.status = "configured"  # configured, active, stopped
        
    def get_display_name(self):
        if self.symbol and self.side:
            return f"{self.name} ({self.symbol} {self.side.upper()})"
        return self.name
        
    def is_complete(self):
        return all([self.symbol, self.side, self.amount])
        
    def get_config_summary(self):
        summary = f"📋 {self.get_display_name()}\n\n"
        summary += f"Symbol: {self.symbol or 'Not set'}\n"
        summary += f"Side: {self.side or 'Not set'}\n"
        summary += f"Amount: {self.amount or 'Not set'}\n"
        summary += f"Leverage: {self.leverage}x\n"
        summary += f"Entry: {self.entry_price or 'Market'}\n"
        
        # Show take profits
        if self.take_profits:
            summary += f"Take Profits:\n"
            for i, tp in enumerate(self.take_profits, 1):
                summary += f"  TP{i}: {tp.get('percentage', 0)}% ({tp.get('allocation', 0)}%)\n"
        else:
            summary += f"Take Profits: Not set\n"
            
        summary += f"Stop Loss: {self.stop_loss_percent}%" if self.stop_loss_percent else "Stop Loss: Not set\n"
        summary += f"Status: {self.status.title()}\n"
        return summary

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
        # Check if it's a numeric input for trade configuration
        if chat_id in user_selected_trade:
            trade_id = user_selected_trade[chat_id]
            if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                config = user_trade_configs[chat_id][trade_id]
                
                # Try to parse as numeric value for amount/price setting
                try:
                    value = float(text)
                    
                    # Check if we're expecting an amount input
                    if not config.amount:
                        config.amount = value
                        return f"✅ Set trade amount to {value} USDT", get_trading_menu(chat_id)
                    
                    # Check if we're expecting an entry price
                    elif not config.entry_price and config.symbol:
                        config.entry_price = value
                        return f"✅ Set entry price to ${value:.4f}", get_trading_menu(chat_id)
                    
                    # Check if we're expecting take profit percentages or allocations
                    elif config.tp_config_step == "percentages":
                        # Add new take profit percentage
                        config.take_profits.append({"percentage": value, "allocation": None})
                        tp_num = len(config.take_profits)
                        
                        if tp_num < 3:  # Allow up to 3 TPs
                            return f"✅ Added TP{tp_num}: {value}%\n\n🎯 Add another TP percentage or continue to allocations:", get_tp_percentage_input_menu()
                        else:
                            config.tp_config_step = "allocations"
                            return f"✅ Added TP{tp_num}: {value}%\n\n📊 Now set position allocation for each TP:", get_tp_allocation_menu(chat_id)
                    
                    elif config.tp_config_step == "allocations":
                        # Set allocation for the next TP that needs it
                        for tp in config.take_profits:
                            if tp["allocation"] is None:
                                tp["allocation"] = value
                                tp_num = config.take_profits.index(tp) + 1
                                
                                # Check if more allocations needed
                                remaining = [tp for tp in config.take_profits if tp["allocation"] is None]
                                if remaining:
                                    return f"✅ Set TP{tp_num} allocation: {value}%\n\n📊 Set allocation for next TP:", get_tp_allocation_menu(chat_id)
                                else:
                                    # All allocations set, validate and continue
                                    total_allocation = sum(tp["allocation"] for tp in config.take_profits)
                                    if total_allocation > 100:
                                        return f"❌ Total allocation ({total_allocation}%) exceeds 100%\n\nPlease reset allocations:", get_tp_allocation_reset_menu()
                                    else:
                                        return f"✅ Take profits configured! Total allocation: {total_allocation}%\n\n🛑 Now set your stop loss:", get_stoploss_menu()
                                break
                    
                    # Check if we're expecting stop loss
                    elif not config.stop_loss_percent:
                        config.stop_loss_percent = value
                        return f"✅ Set stop loss to {value}%\n\n🎯 Trade configuration complete!", get_trading_menu(chat_id)
                    
                except ValueError:
                    pass
        
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

def get_multitrade_menu(user_id):
    """Get multi-trade management menu"""
    user_trades = user_trade_configs.get(user_id, {})
    
    keyboard = [
        [{"text": "📋 View All Trades", "callback_data": "multitrade_list"}],
        [{"text": "➕ Create New Trade", "callback_data": "multitrade_new"}],
    ]
    
    if user_trades:
        keyboard.extend([
            [{"text": "🎯 Select Trade", "callback_data": "multitrade_select"}],
            [{"text": "🚀 Start Selected Trade", "callback_data": "multitrade_start"}],
            [{"text": "⏹️ Stop All Trades", "callback_data": "multitrade_stop_all"}],
        ])
    
    keyboard.extend([
        [{"text": "📊 Multi-Trade Status", "callback_data": "multitrade_status"}],
        [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
    ])
    
    return {"inline_keyboard": keyboard}

def get_trading_menu(user_id=None):
    """Get trading menu keyboard"""
    config = None
    if user_id and user_id in user_selected_trade:
        trade_id = user_selected_trade[user_id]
        config = user_trade_configs.get(user_id, {}).get(trade_id)
    
    keyboard = [
        [{"text": "💱 Select Trading Pair", "callback_data": "select_pair"}],
        [{"text": "📈 Long Position", "callback_data": "set_side_long"}, 
         {"text": "📉 Short Position", "callback_data": "set_side_short"}],
        [{"text": "📊 Set Leverage", "callback_data": "set_leverage"}],
        [{"text": "💰 Set Amount", "callback_data": "set_amount"}],
        [{"text": "🎯 Set Entry Price", "callback_data": "set_entry"}],
        [{"text": "🎯 Set Take Profits", "callback_data": "set_takeprofit"}],
        [{"text": "🛑 Set Stop Loss", "callback_data": "set_stoploss"}],
    ]
    
    # Add trade execution button if config is complete
    if config and config.is_complete():
        keyboard.append([{"text": "🚀 Execute Trade", "callback_data": "execute_trade"}])
    
    keyboard.append([{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}])
    return {"inline_keyboard": keyboard}

def get_config_menu():
    """Get configuration menu keyboard"""
    return {
        "inline_keyboard": [
            [{"text": "🏷️ Set Trade Name", "callback_data": "set_trade_name"}],
            [{"text": "⚖️ Break-even Settings", "callback_data": "set_breakeven"}],
            [{"text": "📈 Trailing Stop", "callback_data": "set_trailstop"}],
            [{"text": "⚙️ Default Settings", "callback_data": "default_settings"}],
            [{"text": "🔄 Reset All Settings", "callback_data": "reset_settings"}],
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

def get_trade_selection_menu(user_id):
    """Get trade selection menu for a specific user"""
    user_trades = user_trade_configs.get(user_id, {})
    keyboard = []
    
    for trade_id, config in user_trades.items():
        status_emoji = "🟢" if config.status == "active" else "🟡" if config.status == "configured" else "🔴"
        button_text = f"{status_emoji} {config.get_display_name()}"
        keyboard.append([{"text": button_text, "callback_data": f"select_trade_{trade_id}"}])
    
    keyboard.append([{"text": "🏠 Back to Multi-Trade", "callback_data": "menu_multitrade"}])
    return {"inline_keyboard": keyboard}

def get_trade_actions_menu(trade_id):
    """Get actions menu for a specific trade"""
    return {
        "inline_keyboard": [
            [{"text": "✏️ Edit Trade", "callback_data": f"edit_trade_{trade_id}"}],
            [{"text": "🚀 Start Trade", "callback_data": f"start_trade_{trade_id}"}],
            [{"text": "⏹️ Stop Trade", "callback_data": f"stop_trade_{trade_id}"}],
            [{"text": "📋 Configure Trade", "callback_data": f"config_trade_{trade_id}"}],
            [{"text": "🗑️ Delete Trade", "callback_data": f"delete_trade_{trade_id}"}],
            [{"text": "🏠 Back to List", "callback_data": "multitrade_list"}]
        ]
    }

def get_leverage_menu():
    """Get leverage selection menu"""
    leverages = ["1x", "2x", "5x", "10x", "20x", "50x", "100x"]
    keyboard = []
    
    for i in range(0, len(leverages), 3):
        row = []
        for j in range(3):
            if i + j < len(leverages):
                lev = leverages[i + j]
                row.append({"text": lev, "callback_data": f"leverage_{lev[:-1]}"})
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
            return "📊 Trading Menu:", get_trading_menu(chat_id)
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
                # Set the symbol in the current trade if one is selected
                if chat_id in user_selected_trade:
                    trade_id = user_selected_trade[chat_id]
                    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                        config = user_trade_configs[chat_id][trade_id]
                        config.symbol = symbol
                        
                        # Directly go to trading menu after selecting pair
                        response = f"✅ Selected trading pair: {pair}\n💰 Current Price: ${price:.4f}\n\n📊 Configure your trade below:"
                        return response, get_trading_menu(chat_id)
                else:
                    # If no trade is selected, show the basic pair info and trading menu
                    response = f"💰 {pair} Current Price: ${price:.4f}\n\n📊 Use the trading menu to configure your trade:"
                    return response, get_trading_menu(chat_id)
            else:
                return f"❌ Could not fetch price for {pair}", get_pairs_menu()
        
        # Set symbol for current trade (keeping this for compatibility)
        elif callback_data.startswith("set_symbol_"):
            symbol = callback_data.replace("set_symbol_", "")
            if chat_id in user_selected_trade:
                trade_id = user_selected_trade[chat_id]
                if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                    config = user_trade_configs[chat_id][trade_id]
                    config.symbol = symbol
                    return f"✅ Set symbol to {symbol}", get_trading_menu(chat_id)
            return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)
        
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
        
        # Multi-trade management handlers
        elif callback_data == "menu_multitrade":
            user_trades = user_trade_configs.get(chat_id, {})
            summary = f"🔄 Multi-Trade Manager\n\n"
            summary += f"Total Trades: {len(user_trades)}\n"
            if user_trades:
                active_count = sum(1 for config in user_trades.values() if config.status == "active")
                summary += f"Active: {active_count}\n"
                if chat_id in user_selected_trade:
                    selected_trade = user_trade_configs[chat_id].get(user_selected_trade[chat_id])
                    if selected_trade:
                        summary += f"Selected: {selected_trade.get_display_name()}\n"
            return summary, get_multitrade_menu(chat_id)
            
        elif callback_data == "menu_config":
            config_summary = "⚙️ Configuration Settings\n\n"
            user_config = user_configs.get(chat_id, {})
            config_summary += f"Default Leverage: {user_config.get('default_leverage', '1x')}\n"
            config_summary += f"Break-even Mode: {user_config.get('breakeven_mode', 'After TP1')}\n"
            config_summary += f"Trailing Stop: {user_config.get('trailing_stop', 'Disabled')}\n"
            return config_summary, get_config_menu()
            
        # Multi-trade specific handlers
        elif callback_data == "multitrade_new":
            global trade_counter
            trade_counter += 1
            trade_id = f"trade_{trade_counter}"
            
            if chat_id not in user_trade_configs:
                user_trade_configs[chat_id] = {}
            
            new_trade = TradeConfig(trade_id, f"Trade #{trade_counter}")
            user_trade_configs[chat_id][trade_id] = new_trade
            user_selected_trade[chat_id] = trade_id
            
            return f"✅ Created new trade: {new_trade.get_display_name()}", get_multitrade_menu(chat_id)
            
        elif callback_data == "multitrade_list":
            user_trades = user_trade_configs.get(chat_id, {})
            if not user_trades:
                return "📋 No trades configured yet.", get_multitrade_menu(chat_id)
            
            response = "📋 Your Trading Configurations:\n\n"
            for trade_id, config in user_trades.items():
                status_emoji = "🟢" if config.status == "active" else "🟡" if config.status == "configured" else "🔴"
                response += f"{status_emoji} {config.get_display_name()}\n"
                response += f"   {config.symbol or 'No symbol'} | {config.side or 'No side'}\n\n"
            
            keyboard = {"inline_keyboard": []}
            for trade_id, config in list(user_trades.items())[:5]:  # Show first 5 trades
                status_emoji = "🟢" if config.status == "active" else "🟡"
                button_text = f"{status_emoji} {config.name}"
                keyboard["inline_keyboard"].append([{"text": button_text, "callback_data": f"select_trade_{trade_id}"}])
            
            keyboard["inline_keyboard"].append([{"text": "🏠 Back to Multi-Trade", "callback_data": "menu_multitrade"}])
            return response, keyboard
            
        elif callback_data == "multitrade_select":
            return "🎯 Select a trade to configure:", get_trade_selection_menu(chat_id)
            
        elif callback_data.startswith("select_trade_"):
            trade_id = callback_data.replace("select_trade_", "")
            if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                user_selected_trade[chat_id] = trade_id
                config = user_trade_configs[chat_id][trade_id]
                response = f"✅ Selected: {config.get_display_name()}\n\n{config.get_config_summary()}"
                return response, get_trade_actions_menu(trade_id)
            return "❌ Trade not found.", get_multitrade_menu(chat_id)
            
        elif callback_data == "multitrade_start":
            if chat_id not in user_selected_trade:
                return "❌ No trade selected. Please select a trade first.", get_multitrade_menu(chat_id)
                
            trade_id = user_selected_trade[chat_id]
            config = user_trade_configs[chat_id][trade_id]
            
            if not config.is_complete():
                return "❌ Trade configuration incomplete. Please set symbol, side, and amount.", get_multitrade_menu(chat_id)
                
            config.status = "active"
            return f"🚀 Started trade: {config.get_display_name()}", get_multitrade_menu(chat_id)
            
        elif callback_data == "multitrade_stop_all":
            user_trades = user_trade_configs.get(chat_id, {})
            stopped_count = 0
            for config in user_trades.values():
                if config.status == "active":
                    config.status = "stopped"
                    stopped_count += 1
            return f"⏹️ Stopped {stopped_count} active trades.", get_multitrade_menu(chat_id)
            
        elif callback_data == "multitrade_status":
            user_trades = user_trade_configs.get(chat_id, {})
            if not user_trades:
                return "📊 No trades to show status for.", get_multitrade_menu(chat_id)
                
            response = "📊 Multi-Trade Status:\n\n"
            for config in user_trades.values():
                status_emoji = "🟢" if config.status == "active" else "🟡" if config.status == "configured" else "🔴"
                response += f"{status_emoji} {config.get_display_name()}\n"
                response += f"   Status: {config.status.title()}\n"
                if config.symbol:
                    response += f"   {config.symbol} {config.side or 'N/A'}\n"
                response += "\n"
            
            return response, get_multitrade_menu(chat_id)
        
        # Configuration handlers
        elif callback_data == "set_breakeven":
            return "⚖️ Break-even Settings\n\nChoose when to move stop loss to break-even:", get_breakeven_menu()
        elif callback_data.startswith("breakeven_"):
            breakeven_mode = callback_data.replace("breakeven_", "")
            return handle_set_breakeven(chat_id, breakeven_mode)
        elif callback_data == "set_trailstop":
            return "📈 Trailing Stop Settings\n\nConfigure trailing stop parameters:", get_trailing_stop_menu()
        elif callback_data == "set_trail_percent":
            return "📉 Enter trailing stop percentage (e.g., 2 for 2%):", get_config_menu()
        elif callback_data == "set_trail_activation":
            return "🎯 Enter activation profit percentage (e.g., 5 for 5%):", get_config_menu()
        elif callback_data == "disable_trailing":
            return handle_disable_trailing(chat_id)
        elif callback_data == "default_settings":
            user_config = user_configs.get(chat_id, {})
            return f"⚙️ Default Settings:\n\nLeverage: {user_config.get('default_leverage', '1x')}\nBreak-even: {user_config.get('breakeven_mode', 'After TP1')}", get_default_settings_menu()
        elif callback_data == "change_default_leverage":
            return "⚖️ Select new default leverage:", get_default_leverage_menu()
        elif callback_data.startswith("default_lev_"):
            leverage = callback_data.replace("default_lev_", "")
            return handle_set_default_leverage(chat_id, leverage)
        elif callback_data == "change_breakeven_mode":
            return "⚖️ Select break-even mode:", get_breakeven_mode_menu()
        elif callback_data.startswith("breakeven_mode_"):
            mode = callback_data.replace("breakeven_mode_", "")
            return handle_set_breakeven_mode(chat_id, mode)
        elif callback_data == "reset_settings":
            if chat_id in user_configs:
                user_configs[chat_id] = {}
            return "🔄 All settings have been reset to defaults.", get_config_menu()
            
        # Trading configuration handlers
        elif callback_data == "set_side_long":
            return handle_set_side(chat_id, "long")
        elif callback_data == "set_side_short":
            return handle_set_side(chat_id, "short")
        elif callback_data == "set_leverage":
            return "📊 Select leverage for this trade:", get_leverage_menu()
        elif callback_data.startswith("leverage_"):
            leverage = int(callback_data.replace("leverage_", ""))
            return handle_set_leverage_wizard(chat_id, leverage)
        elif callback_data == "set_amount":
            return "💰 Set the trade amount (e.g., 100 USDT)\n\nPlease type the amount you want to trade.", get_trading_menu(chat_id)
        elif callback_data == "execute_trade":
            return handle_execute_trade(chat_id, user)
            
        # Trade action handlers
        elif callback_data.startswith("start_trade_"):
            trade_id = callback_data.replace("start_trade_", "")
            return handle_start_trade(chat_id, trade_id)
        elif callback_data.startswith("stop_trade_"):
            trade_id = callback_data.replace("stop_trade_", "")
            return handle_stop_trade(chat_id, trade_id)
        elif callback_data.startswith("delete_trade_"):
            trade_id = callback_data.replace("delete_trade_", "")
            return handle_delete_trade(chat_id, trade_id)
        elif callback_data.startswith("config_trade_"):
            trade_id = callback_data.replace("config_trade_", "")
            return handle_config_trade(chat_id, trade_id)
        elif callback_data.startswith("edit_trade_"):
            trade_id = callback_data.replace("edit_trade_", "")
            return handle_edit_trade(chat_id, trade_id)
        
        # Trading configuration input handlers
        elif callback_data == "set_takeprofit":
            if chat_id in user_selected_trade:
                trade_id = user_selected_trade[chat_id]
                if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                    config = user_trade_configs[chat_id][trade_id]
                    config.take_profits = []  # Reset take profits
                    config.tp_config_step = "percentages"  # Start with percentages
                    return "🎯 Take Profit Setup\n\nFirst, set your take profit percentages.\nEnter percentage for TP1 (e.g., 10 for 10% profit):", get_tp_percentage_input_menu()
            return "❌ No trade selected.", get_trading_menu(chat_id)
        elif callback_data == "set_stoploss":
            return "🛑 Stop Loss Settings\n\nSet your stop loss percentage (e.g., 5 for 5%):", get_stoploss_menu()
        # New take profit system handlers
        elif callback_data.startswith("tp_add_percent_"):
            percent = float(callback_data.replace("tp_add_percent_", ""))
            if chat_id in user_selected_trade:
                trade_id = user_selected_trade[chat_id]
                if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                    config = user_trade_configs[chat_id][trade_id]
                    config.take_profits.append({"percentage": percent, "allocation": None})
                    tp_num = len(config.take_profits)
                    
                    if tp_num < 3:
                        return f"✅ Added TP{tp_num}: {percent}%\n\n🎯 Add another TP or continue to allocations:", get_tp_percentage_input_menu()
                    else:
                        config.tp_config_step = "allocations"
                        return f"✅ Added TP{tp_num}: {percent}%\n\n📊 Now set allocation for TP1:", get_tp_allocation_menu(chat_id)
            return "❌ No trade selected.", get_trading_menu(chat_id)
        
        elif callback_data == "tp_continue_allocations":
            if chat_id in user_selected_trade:
                trade_id = user_selected_trade[chat_id]
                if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                    config = user_trade_configs[chat_id][trade_id]
                    if config.take_profits:
                        config.tp_config_step = "allocations"
                        return f"📊 Set allocation for TP1 ({config.take_profits[0]['percentage']}%):", get_tp_allocation_menu(chat_id)
                    else:
                        return "❌ No take profits set. Add TP percentages first.", get_tp_percentage_input_menu()
            return "❌ No trade selected.", get_trading_menu(chat_id)
        
        elif callback_data.startswith("tp_alloc_"):
            alloc = float(callback_data.replace("tp_alloc_", ""))
            if chat_id in user_selected_trade:
                trade_id = user_selected_trade[chat_id]
                if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                    config = user_trade_configs[chat_id][trade_id]
                    
                    # Find next TP that needs allocation
                    for tp in config.take_profits:
                        if tp["allocation"] is None:
                            tp["allocation"] = alloc
                            tp_num = config.take_profits.index(tp) + 1
                            
                            # Check if more allocations needed
                            remaining = [tp for tp in config.take_profits if tp["allocation"] is None]
                            if remaining:
                                next_tp = remaining[0]
                                next_num = config.take_profits.index(next_tp) + 1
                                return f"✅ Set TP{tp_num} allocation: {alloc}%\n\n📊 Set allocation for TP{next_num} ({next_tp['percentage']}%):", get_tp_allocation_menu(chat_id)
                            else:
                                # All allocations set
                                total_allocation = sum(tp["allocation"] for tp in config.take_profits)
                                if total_allocation > 100:
                                    return f"❌ Total allocation ({total_allocation}%) exceeds 100%\n\nPlease reset and try again:", get_tp_allocation_reset_menu()
                                else:
                                    return f"✅ Take profits configured! Total allocation: {total_allocation}%\n\n🛑 Now set your stop loss:", get_stoploss_menu()
                            break
            return "❌ No trade selected.", get_trading_menu(chat_id)
        
        elif callback_data == "tp_reset_alloc":
            if chat_id in user_selected_trade:
                trade_id = user_selected_trade[chat_id]
                if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                    config = user_trade_configs[chat_id][trade_id]
                    for tp in config.take_profits:
                        tp["allocation"] = None
                    return "🔄 Reset all allocations\n\n📊 Set allocation for TP1:", get_tp_allocation_menu(chat_id)
            return "❌ No trade selected.", get_trading_menu(chat_id)
        
        elif callback_data.startswith("sl_"):
            sl_data = callback_data.replace("sl_", "")
            if sl_data == "custom":
                return "🛑 Enter custom stop loss percentage (e.g., 7.5):", get_trading_menu(chat_id)
            else:
                return handle_set_stoploss(chat_id, float(sl_data))
        
        # Entry price setting
        elif callback_data == "set_entry":
            return "🎯 Entry Price Options:", get_entry_price_menu()
        elif callback_data == "entry_market":
            return handle_set_entry_price(chat_id, "market")
        elif callback_data == "entry_limit":
            return "🎯 Enter your limit price (e.g., 45000.50):", get_trading_menu(chat_id)
        
        # Amount wizard handlers
        elif callback_data.startswith("amount_"):
            amount_data = callback_data.replace("amount_", "")
            if amount_data == "custom":
                return "💰 Enter custom amount in USDT (e.g., 150):", get_trading_menu(chat_id)
            else:
                return handle_set_amount_wizard(chat_id, float(amount_data))
        
        else:
            return "🤔 Unknown action. Please try again.", get_main_menu()
            
    except Exception as e:
        logging.error(f"Error handling callback query: {e}")
        return "❌ An error occurred. Please try again.", get_main_menu()

def get_breakeven_menu():
    """Get break-even configuration menu"""
    return {
        "inline_keyboard": [
            [{"text": "After TP1", "callback_data": "breakeven_tp1"}],
            [{"text": "After TP2", "callback_data": "breakeven_tp2"}],
            [{"text": "After TP3", "callback_data": "breakeven_tp3"}],
            [{"text": "Disable", "callback_data": "breakeven_off"}],
            [{"text": "🏠 Back to Config", "callback_data": "menu_config"}]
        ]
    }

def get_trailing_stop_menu():
    """Get trailing stop configuration menu"""
    return {
        "inline_keyboard": [
            [{"text": "📉 Set Trail Percentage", "callback_data": "set_trail_percent"}],
            [{"text": "🎯 Set Activation Profit %", "callback_data": "set_trail_activation"}],
            [{"text": "❌ Disable Trailing Stop", "callback_data": "disable_trailing"}],
            [{"text": "🏠 Back to Config", "callback_data": "menu_config"}]
        ]
    }

def get_default_settings_menu():
    """Get default settings menu"""
    return {
        "inline_keyboard": [
            [{"text": "⚖️ Change Default Leverage", "callback_data": "change_default_leverage"}],
            [{"text": "🎯 Change Break-even Mode", "callback_data": "change_breakeven_mode"}],
            [{"text": "🏠 Back to Config", "callback_data": "menu_config"}]
        ]
    }

def handle_set_side(chat_id, side):
    """Handle setting trade side (long/short)"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            config.side = side
            return f"✅ Set position to {side.upper()}", get_trading_menu(chat_id)
    return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)

def handle_set_leverage(chat_id, leverage):
    """Handle setting leverage"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            config.leverage = leverage
            return f"✅ Set leverage to {leverage}x", get_trading_menu(chat_id)
    return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)

def handle_execute_trade(chat_id, user):
    """Handle trade execution"""
    if chat_id not in user_selected_trade:
        return "❌ No trade selected.", get_trading_menu(chat_id)
        
    trade_id = user_selected_trade[chat_id]
    config = user_trade_configs[chat_id][trade_id]
    
    if not config.is_complete():
        return "❌ Trade configuration incomplete. Please set symbol, side, and amount.", get_trading_menu(chat_id)
    
    # Execute the trade
    price = get_mock_price(config.symbol)
    if price:
        trade = {
            'id': len(bot_trades) + 1,
            'user_id': str(user.get('id', 'unknown')),
            'symbol': config.symbol,
            'action': config.side,
            'quantity': config.amount / price if config.amount else 0.001,
            'price': price,
            'leverage': config.leverage,
            'status': 'executed',
            'timestamp': datetime.utcnow().isoformat()
        }
        bot_trades.append(trade)
        bot_status['total_trades'] += 1
        config.status = "active"
        
        response = f"🚀 Trade Executed!\n\n"
        response += f"Symbol: {config.symbol}\n"
        response += f"Side: {config.side.upper()}\n"
        response += f"Amount: {config.amount} USDT\n"
        response += f"Leverage: {config.leverage}x\n"
        response += f"Entry Price: ${price:.4f}\n"
        response += f"Quantity: {trade['quantity']:.6f}"
        
        return response, get_trading_menu(chat_id)
    else:
        return f"❌ Could not execute trade for {config.symbol}", get_trading_menu(chat_id)

def handle_start_trade(chat_id, trade_id):
    """Handle starting a specific trade"""
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        config = user_trade_configs[chat_id][trade_id]
        if config.is_complete():
            config.status = "active"
            return f"🚀 Started trade: {config.get_display_name()}", get_trade_actions_menu(trade_id)
        else:
            return "❌ Trade configuration incomplete.", get_trade_actions_menu(trade_id)
    return "❌ Trade not found.", get_multitrade_menu(chat_id)

def handle_stop_trade(chat_id, trade_id):
    """Handle stopping a specific trade"""
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        config = user_trade_configs[chat_id][trade_id]
        config.status = "stopped"
        return f"⏹️ Stopped trade: {config.get_display_name()}", get_trade_actions_menu(trade_id)
    return "❌ Trade not found.", get_multitrade_menu(chat_id)

def handle_delete_trade(chat_id, trade_id):
    """Handle deleting a specific trade"""
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        config = user_trade_configs[chat_id][trade_id]
        trade_name = config.get_display_name()
        del user_trade_configs[chat_id][trade_id]
        if user_selected_trade.get(chat_id) == trade_id:
            del user_selected_trade[chat_id]
        return f"🗑️ Deleted trade: {trade_name}", get_multitrade_menu(chat_id)
    return "❌ Trade not found.", get_multitrade_menu(chat_id)

def handle_config_trade(chat_id, trade_id):
    """Handle configuring a specific trade"""
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        user_selected_trade[chat_id] = trade_id
        config = user_trade_configs[chat_id][trade_id]
        response = f"⚙️ Configuring: {config.get_display_name()}\n\n{config.get_config_summary()}"
        return response, get_trading_menu(chat_id)
    return "❌ Trade not found.", get_multitrade_menu(chat_id)

def handle_edit_trade(chat_id, trade_id):
    """Handle editing a specific trade"""
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        user_selected_trade[chat_id] = trade_id
        config = user_trade_configs[chat_id][trade_id]
        response = f"✏️ Editing: {config.get_display_name()}\n\n{config.get_config_summary()}"
        return response, get_trading_menu(chat_id)
    return "❌ Trade not found.", get_multitrade_menu(chat_id)

def handle_set_stoploss(chat_id, sl_percent):
    """Handle setting stop loss percentage"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            config.stop_loss_percent = sl_percent
            return f"✅ Set stop loss to {sl_percent}%", get_trading_menu(chat_id)
    return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)

def get_tp_percentage_input_menu():
    """Get take profit percentage input menu"""
    return {
        "inline_keyboard": [
            [{"text": "🎯 2%", "callback_data": "tp_add_percent_2"}],
            [{"text": "🎯 5%", "callback_data": "tp_add_percent_5"}],
            [{"text": "🎯 10%", "callback_data": "tp_add_percent_10"}],
            [{"text": "🎯 15%", "callback_data": "tp_add_percent_15"}],
            [{"text": "🎯 25%", "callback_data": "tp_add_percent_25"}],
            [{"text": "📊 Continue to Allocations", "callback_data": "tp_continue_allocations"}],
            [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
        ]
    }

def get_tp_allocation_menu(chat_id):
    """Get take profit allocation menu"""
    if chat_id not in user_selected_trade:
        return get_trading_menu(chat_id)
    
    trade_id = user_selected_trade[chat_id]
    if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
        return get_trading_menu(chat_id)
    
    keyboard = [
        [{"text": "📊 25%", "callback_data": "tp_alloc_25"}],
        [{"text": "📊 30%", "callback_data": "tp_alloc_30"}],
        [{"text": "📊 40%", "callback_data": "tp_alloc_40"}],
        [{"text": "📊 50%", "callback_data": "tp_alloc_50"}],
        [{"text": "🔄 Reset Allocations", "callback_data": "tp_reset_alloc"}],
        [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
    ]
    
    return {"inline_keyboard": keyboard}

def get_tp_allocation_reset_menu():
    """Get take profit allocation reset menu"""
    return {
        "inline_keyboard": [
            [{"text": "🔄 Reset All Allocations", "callback_data": "tp_reset_all_alloc"}],
            [{"text": "🔄 Reset Last Allocation", "callback_data": "tp_reset_last_alloc"}],
            [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
        ]
    }

def get_stoploss_menu():
    """Get stop loss configuration menu"""
    return {
        "inline_keyboard": [
            [{"text": "🛑 2%", "callback_data": "sl_2"}],
            [{"text": "🛑 3%", "callback_data": "sl_3"}],
            [{"text": "🛑 5%", "callback_data": "sl_5"}],
            [{"text": "🛑 10%", "callback_data": "sl_10"}],
            [{"text": "🛑 Custom", "callback_data": "sl_custom"}],
            [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
        ]
    }

def get_entry_price_menu():
    """Get entry price configuration menu"""
    return {
        "inline_keyboard": [
            [{"text": "📊 Market Price", "callback_data": "entry_market"}],
            [{"text": "🎯 Limit Price", "callback_data": "entry_limit"}],
            [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
        ]
    }

def handle_set_entry_price(chat_id, entry_type):
    """Handle setting entry price"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            if entry_type == "market":
                config.entry_price = None  # None means market price
                # Continue wizard to take profits
                return f"✅ Set entry to Market Price\n\n🎯 Now let's set your take profits:", get_tp_percentage_input_menu()
            else:
                return f"✅ Entry type set to {entry_type}. Please specify price.", get_trading_menu(chat_id)
    return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)

def handle_set_leverage_wizard(chat_id, leverage):
    """Handle setting leverage with wizard flow"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            config.leverage = leverage
            # Continue wizard to amount
            return f"✅ Set leverage to {leverage}x\n\n💰 Now set your trade amount:", get_amount_wizard_menu()
    return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)

def handle_tp_wizard(chat_id, tp_level):
    """Handle take profit setting with wizard flow"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            return f"🎯 Set Take Profit {tp_level}\n\nEnter percentage (e.g., 10 for 10% profit):", get_tp_percentage_menu(tp_level)
    return "❌ No trade selected.", get_trading_menu(chat_id)

def handle_set_breakeven(chat_id, mode):
    """Handle setting break-even mode"""
    if chat_id not in user_configs:
        user_configs[chat_id] = {}
    
    mode_map = {
        "tp1": "After TP1",
        "tp2": "After TP2", 
        "tp3": "After TP3",
        "off": "Disabled"
    }
    
    user_configs[chat_id]['breakeven_mode'] = mode_map.get(mode, "After TP1")
    return f"✅ Break-even set to: {mode_map.get(mode, 'After TP1')}", get_config_menu()

def handle_disable_trailing(chat_id):
    """Handle disabling trailing stop"""
    if chat_id not in user_configs:
        user_configs[chat_id] = {}
    user_configs[chat_id]['trailing_stop'] = 'Disabled'
    return "✅ Trailing stop disabled", get_config_menu()

def handle_set_default_leverage(chat_id, leverage):
    """Handle setting default leverage"""
    if chat_id not in user_configs:
        user_configs[chat_id] = {}
    user_configs[chat_id]['default_leverage'] = f"{leverage}x"
    return f"✅ Default leverage set to {leverage}x", get_default_settings_menu()

def handle_set_breakeven_mode(chat_id, mode):
    """Handle setting breakeven mode"""
    if chat_id not in user_configs:
        user_configs[chat_id] = {}
    
    mode_map = {
        "tp1": "After TP1",
        "tp2": "After TP2",
        "tp3": "After TP3",
        "off": "Disabled"
    }
    
    user_configs[chat_id]['breakeven_mode'] = mode_map.get(mode, "After TP1")
    return f"✅ Break-even mode set to: {mode_map.get(mode, 'After TP1')}", get_default_settings_menu()

def get_amount_wizard_menu():
    """Get amount setting wizard menu"""
    return {
        "inline_keyboard": [
            [{"text": "💰 $10", "callback_data": "amount_10"}],
            [{"text": "💰 $25", "callback_data": "amount_25"}],
            [{"text": "💰 $50", "callback_data": "amount_50"}],
            [{"text": "💰 $100", "callback_data": "amount_100"}],
            [{"text": "💰 $250", "callback_data": "amount_250"}],
            [{"text": "💰 Custom Amount", "callback_data": "amount_custom"}],
            [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
        ]
    }

def get_tp_percentage_menu(tp_level):
    """Get take profit percentage menu"""
    return {
        "inline_keyboard": [
            [{"text": "🎯 2%", "callback_data": f"tp_set_{tp_level}_2"}],
            [{"text": "🎯 5%", "callback_data": f"tp_set_{tp_level}_5"}],
            [{"text": "🎯 10%", "callback_data": f"tp_set_{tp_level}_10"}],
            [{"text": "🎯 15%", "callback_data": f"tp_set_{tp_level}_15"}],
            [{"text": "🎯 25%", "callback_data": f"tp_set_{tp_level}_25"}],
            [{"text": "🎯 Custom", "callback_data": f"tp_custom_{tp_level}"}],
            [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
        ]
    }

def get_default_leverage_menu():
    """Get default leverage menu"""
    return {
        "inline_keyboard": [
            [{"text": "1x", "callback_data": "default_lev_1"}],
            [{"text": "2x", "callback_data": "default_lev_2"}],
            [{"text": "5x", "callback_data": "default_lev_5"}],
            [{"text": "10x", "callback_data": "default_lev_10"}],
            [{"text": "20x", "callback_data": "default_lev_20"}],
            [{"text": "50x", "callback_data": "default_lev_50"}],
            [{"text": "🏠 Back to Config", "callback_data": "menu_config"}]
        ]
    }

def get_breakeven_mode_menu():
    """Get break-even mode selection menu"""
    return {
        "inline_keyboard": [
            [{"text": "After TP1", "callback_data": "breakeven_mode_tp1"}],
            [{"text": "After TP2", "callback_data": "breakeven_mode_tp2"}],
            [{"text": "After TP3", "callback_data": "breakeven_mode_tp3"}],
            [{"text": "Disable", "callback_data": "breakeven_mode_off"}],
            [{"text": "🏠 Back to Config", "callback_data": "menu_config"}]
        ]
    }

def handle_set_amount_wizard(chat_id, amount):
    """Handle setting amount with wizard flow"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            config.amount = amount
            # Continue wizard to entry price
            return f"✅ Set amount to ${amount} USDT\n\n🎯 Now set your entry price:", get_entry_price_menu()
    return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)

def handle_set_tp_percent(chat_id, tp_level, tp_percent):
    """Handle setting take profit percentage"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            
            if tp_level == "1":
                config.tp1_percent = tp_percent
                return f"✅ Set TP1 to {tp_percent}%\n\n🎯 Set TP2 (optional):", get_tp_percentage_menu("2")
            elif tp_level == "2":
                config.tp2_percent = tp_percent
                return f"✅ Set TP2 to {tp_percent}%\n\n🎯 Set TP3 (optional):", get_tp_percentage_menu("3")
            elif tp_level == "3":
                config.tp3_percent = tp_percent
                return f"✅ Set TP3 to {tp_percent}%\n\n🛑 Now set your stop loss:", get_stoploss_menu()
                
    return "❌ No trade selected.", get_trading_menu(chat_id)

if __name__ == "__main__":
    # Setup webhook on startup
    setup_webhook()
    app.run(host="0.0.0.0", port=5000, debug=True)