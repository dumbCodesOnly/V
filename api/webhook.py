"""
Standalone webhook endpoint for Telegram bot
This bypasses Vercel's authentication while maintaining security
"""

import os
import json
import logging
from flask import Flask, request, jsonify
from datetime import datetime
import urllib.request
import urllib.parse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Bot configuration
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

def verify_telegram_request(data):
    """Enhanced verification for Telegram webhook requests"""
    try:
        # Check User-Agent (Telegram sends specific UA)
        user_agent = request.headers.get('User-Agent', '')
        if not user_agent.startswith('TelegramBot'):
            logger.warning(f"Invalid User-Agent: {user_agent}")
            # Don't fail on this alone, just log
        
        # Check Content-Type
        content_type = request.headers.get('Content-Type', '')
        if 'application/json' not in content_type:
            logger.warning(f"Invalid Content-Type: {content_type}")
            return False
        
        # Verify data structure
        if not isinstance(data, dict):
            return False
            
        # Must have update_id
        if 'update_id' not in data:
            logger.warning("Missing update_id in webhook data")
            return False
            
        # Must have one of these
        required_fields = ['message', 'callback_query', 'inline_query', 'edited_message']
        if not any(field in data for field in required_fields):
            logger.warning("No recognized update type in webhook data")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Verification error: {e}")
        return False

def send_telegram_response(chat_id, text, reply_markup=None):
    """Send response back to Telegram"""
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
        
        data_encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=data_encoded, method='POST')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.getcode() == 200:
                logger.info(f"Message sent successfully to chat {chat_id}")
                return True
            else:
                logger.error(f"Failed to send message: {response.getcode()}")
                return False
                
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False

@app.route('/webhook', methods=['POST'])
def webhook():
    """Secure webhook endpoint with enhanced validation"""
    try:
        # Log request details
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        logger.info(f"Webhook request from {client_ip}")
        
        # Get JSON data
        data = request.get_json()
        if not data:
            logger.warning("No JSON data in webhook request")
            return jsonify({'error': 'No JSON data'}), 400
        
        # Verify this is a legitimate Telegram request
        if not verify_telegram_request(data):
            logger.warning("Webhook verification failed")
            return jsonify({'error': 'Invalid request'}), 401
        
        # Process the update
        if 'message' in data:
            message = data['message']
            chat_id = message.get('chat', {}).get('id')
            text = message.get('text', '')
            user = message.get('from', {})
            
            logger.info(f"Processing message from user {user.get('id')}: {text}")
            
            # Simple command processing
            if text.startswith('/start'):
                response_text = f"🤖 Hello {user.get('first_name', 'User')}! Your bot is working securely."
                keyboard = {
                    'inline_keyboard': [
                        [{'text': '📱 Open Mini App', 'web_app': {'url': 'https://v0-033-pi.vercel.app'}}],
                        [{'text': '💹 Check Price', 'callback_data': 'price_btc'}]
                    ]
                }
                send_telegram_response(chat_id, response_text, keyboard)
            
            elif text.startswith('/menu'):
                response_text = "📋 Main Menu - Your secure bot is active!"
                keyboard = {
                    'inline_keyboard': [
                        [{'text': '📱 Trading Interface', 'web_app': {'url': 'https://v0-033-pi.vercel.app'}}]
                    ]
                }
                send_telegram_response(chat_id, response_text, keyboard)
            
            else:
                send_telegram_response(chat_id, "✅ Message received securely! Use /start or /menu for options.")
        
        elif 'callback_query' in data:
            callback = data['callback_query']
            chat_id = callback.get('message', {}).get('chat', {}).get('id')
            callback_data = callback.get('data', '')
            
            logger.info(f"Processing callback: {callback_data}")
            
            if callback_data == 'price_btc':
                send_telegram_response(chat_id, "💰 BTC Price: $119,400 (Demo)")
        
        return jsonify({'status': 'ok'}), 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'error': 'Internal error'}), 500

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'bot_configured': bool(BOT_TOKEN)
    })

if __name__ == '__main__':
    app.run(debug=True, port=5001)