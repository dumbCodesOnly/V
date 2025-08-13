"""
Streamlined Telegram Trading Bot - Vercel Deployment
Clean, single-purpose webhook handler with integrated bot functionality
"""

import os
import logging
import json
import urllib.request
import urllib.parse
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app setup
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Bot configuration
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = "https://v0-033-pi.vercel.app/webhook"

# Bot state
bot_messages = []
bot_status = {
    'status': 'active',
    'total_messages': 0,
    'error_count': 0,
    'last_heartbeat': datetime.utcnow().isoformat()
}

def send_telegram_message(chat_id, text, reply_markup=None):
    """Send message to Telegram"""
    if not BOT_TOKEN:
        logger.error("No bot token configured")
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
        logger.error(f"Error sending message: {e}")
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

def process_message(message_data):
    """Process incoming Telegram message"""
    chat_id = message_data.get('chat', {}).get('id')
    text = message_data.get('text', '')
    user = message_data.get('from', {})
    
    logger.info(f"Processing message from user {user.get('id')}: {text}")
    
    # Update bot status
    bot_status['last_heartbeat'] = datetime.utcnow().isoformat()
    bot_status['total_messages'] += 1
    
    # Log message
    bot_messages.append({
        'user_id': str(user.get('id', 'unknown')),
        'username': user.get('username', 'Unknown'),
        'message': text,
        'timestamp': datetime.utcnow().isoformat()
    })
    
    # Process commands
    if text.startswith('/start'):
        response_text = f"🤖 Welcome to the Trading Bot, {user.get('first_name', 'User')}!\n\nUse the menu below to access trading features:"
        keyboard = get_main_menu()
        send_telegram_message(chat_id, response_text, keyboard)
        
    elif text.startswith('/menu'):
        response_text = "📋 Main Menu - Select an option:"
        keyboard = get_main_menu()
        send_telegram_message(chat_id, response_text, keyboard)
        
    elif text.startswith('/help'):
        response_text = """🆘 Help & Commands:
        
/start - Welcome message
/menu - Show main menu
/help - This help message

Use the Trading App button to access full features!"""
        send_telegram_message(chat_id, response_text)
        
    else:
        response_text = "✅ Message received! Use /menu to see available options or tap the Trading App button."
        keyboard = get_main_menu()
        send_telegram_message(chat_id, response_text, keyboard)

def process_callback(callback_data, chat_id):
    """Process callback query"""
    logger.info(f"Processing callback: {callback_data}")
    
    if callback_data == 'price_check':
        response_text = "💰 BTC: $119,336\n💰 ETH: $2,847\n💰 BNB: $548\n\n(Live prices in Trading App)"
        
    elif callback_data == 'portfolio':
        response_text = "📊 Your Portfolio:\n\nNo active positions.\nOpen the Trading App to start trading!"
        
    elif callback_data == 'settings':
        response_text = "⚙️ Settings:\n\nManage your API keys and preferences in the Trading App."
        
    else:
        response_text = "✅ Feature available in Trading App!"
    
    keyboard = get_main_menu()
    send_telegram_message(chat_id, response_text, keyboard)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Streamlined webhook handler"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data'}), 400
        
        # Basic validation
        if 'update_id' not in data:
            return jsonify({'error': 'Invalid update'}), 400
        
        # Process message
        if 'message' in data:
            process_message(data['message'])
            
        # Process callback query
        elif 'callback_query' in data:
            callback = data['callback_query']
            chat_id = callback.get('message', {}).get('chat', {}).get('id')
            callback_data = callback.get('data', '')
            
            if chat_id and callback_data:
                process_callback(callback_data, chat_id)
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        bot_status['error_count'] += 1
        return jsonify({'error': 'Internal error'}), 500

@app.route('/')
def index():
    """Main trading interface"""
    try:
        return render_template('mini_app.html')
    except Exception as e:
        logger.error(f"Error loading template: {e}")
        return jsonify({'error': 'Template not found'}), 404

@app.route('/api/status')
def status():
    """Bot status endpoint"""
    return jsonify({
        'status': 'healthy',
        'bot_configured': bool(BOT_TOKEN),
        'webhook_url': WEBHOOK_URL,
        'messages_processed': bot_status['total_messages'],
        'last_activity': bot_status['last_heartbeat']
    })

# Automatic webhook setup for Vercel
def setup_webhook():
    """Set up webhook on deployment"""
    if not BOT_TOKEN:
        logger.warning("No bot token - webhook not set")
        return
    
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        data = urllib.parse.urlencode({'url': WEBHOOK_URL}).encode('utf-8')
        
        req = urllib.request.Request(url, data=data, method='POST')
        response = urllib.request.urlopen(req, timeout=10)
        
        if response.getcode() == 200:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('ok'):
                logger.info(f"Webhook set to {WEBHOOK_URL}")
            else:
                logger.error(f"Webhook failed: {result.get('description')}")
        
    except Exception as e:
        logger.error(f"Webhook setup error: {e}")

# Set webhook on Vercel deployment
if os.environ.get("VERCEL") and BOT_TOKEN:
    setup_webhook()

if __name__ == '__main__':
    app.run(debug=True, port=5000)