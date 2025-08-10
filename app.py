import os
import logging
from flask import Flask, request, jsonify, render_template
import requests
from datetime import datetime

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
            response_text = process_command(text, chat_id, user)
            
            # Send response back to Telegram
            if BOT_TOKEN and chat_id:
                send_telegram_message(chat_id, response_text)
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        logging.error(f"Error processing webhook: {e}")
        bot_status['error_count'] += 1
        return jsonify({'status': 'error', 'message': str(e)}), 500

def process_command(text, chat_id, user):
    """Process bot commands"""
    if not text:
        return "🤔 I didn't receive any text. Type /help to see available commands."
    
    if text.startswith('/start'):
        return f"""🤖 Welcome to Trading Bot, {user.get('first_name', 'User')}!

Available commands:
/start - Show this welcome message
/help - Get help with commands
/price <symbol> - Get current price for a symbol
/buy <symbol> <quantity> - Place a buy order
/sell <symbol> <quantity> - Place a sell order
/portfolio - View your portfolio
/trades - View your recent trades

Example: /price BTCUSDT
Example: /buy ETHUSDT 0.1"""
    
    elif text.startswith('/help'):
        return """📚 Trading Bot Help

Commands:
• /price <symbol> - Get current market price
  Example: /price BTCUSDT

• /buy <symbol> <quantity> - Place buy order
  Example: /buy ETHUSDT 0.1

• /sell <symbol> <quantity> - Place sell order
  Example: /sell BTCUSDT 0.001

• /portfolio - View your current holdings

• /trades - View your trading history

⚠️ Note: This is a demo trading environment."""
    
    elif text.startswith('/price'):
        parts = text.split()
        if len(parts) < 2:
            return "❌ Please provide a symbol. Example: /price BTCUSDT"
        
        symbol = parts[1].upper()
        price = get_mock_price(symbol)
        if price:
            return f"💰 {symbol}: ${price:.4f}"
        else:
            return f"❌ Could not fetch price for {symbol}"
    
    elif text.startswith('/buy') or text.startswith('/sell'):
        parts = text.split()
        if len(parts) < 3:
            action = parts[0][1:]  # Remove '/'
            return f"❌ Please provide symbol and quantity. Example: /{action} BTCUSDT 0.001"
        
        action = parts[0][1:]  # Remove '/'
        symbol = parts[1].upper()
        try:
            quantity = float(parts[2])
        except ValueError:
            return "❌ Invalid quantity. Please provide a valid number."
        
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
            
            return f"✅ {action.capitalize()} order executed: {quantity} {symbol} at ${price:.4f}"
        else:
            return f"❌ {action.capitalize()} order failed: Invalid symbol or quantity"
    
    elif text.startswith('/portfolio'):
        return "📊 Your portfolio is empty. Start trading to see your holdings!"
    
    elif text.startswith('/trades'):
        user_trades = [t for t in bot_trades if t['user_id'] == str(user.get('id', 'unknown'))]
        if not user_trades:
            return "📈 No recent trades found."
        
        response = "📈 Recent Trades:\n\n"
        for trade in user_trades[-5:]:  # Show last 5 trades
            status_emoji = "✅" if trade['status'] == "executed" else "⏳"
            response += f"{status_emoji} {trade['action'].upper()} {trade['quantity']} {trade['symbol']}"
            response += f" @ ${trade['price']:.4f}\n"
            timestamp = datetime.fromisoformat(trade['timestamp'])
            response += f"   {timestamp.strftime('%Y-%m-%d %H:%M')}\n\n"
        
        return response
    
    else:
        return "🤔 I didn't understand that command. Type /help to see available commands."

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

def send_telegram_message(chat_id, text):
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
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logging.error(f"Error sending Telegram message: {e}")
        return False

def setup_webhook():
    """Setup webhook for the bot"""
    if WEBHOOK_URL and BOT_TOKEN:
        try:
            webhook_url = f"{WEBHOOK_URL}/webhook"
            response = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                data={"url": webhook_url},
                timeout=10
            )
            if response.status_code == 200:
                logging.info(f"Webhook set successfully to {webhook_url}")
                bot_status['status'] = 'active'
            else:
                logging.error(f"Failed to set webhook: {response.text}")
        except Exception as e:
            logging.error(f"Error setting webhook: {e}")
    else:
        logging.warning("WEBHOOK_URL or BOT_TOKEN not provided, webhook not set")

if __name__ == "__main__":
    # Setup webhook on startup
    setup_webhook()
    app.run(host="0.0.0.0", port=5000, debug=True)