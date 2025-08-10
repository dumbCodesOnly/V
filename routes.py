import json
import logging
from flask import request, jsonify, render_template
from telegram import Update
from app import app, db
from models import BotMessage, BotStatus, Trade
from bot import application
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

@app.route('/')
def dashboard():
    """Bot dashboard"""
    return render_template('dashboard.html')

@app.route('/webhook', methods=['POST'])
async def webhook():
    """Handle Telegram webhook"""
    try:
        # Get the JSON data from Telegram
        json_data = request.get_json()
        
        if not json_data:
            logger.warning("No JSON data received")
            return jsonify({'status': 'error', 'message': 'No JSON data'}), 400
        
        # Create Update object from JSON
        update = Update.de_json(json_data, application.bot)
        
        if not update:
            logger.warning("Could not parse update")
            return jsonify({'status': 'error', 'message': 'Invalid update'}), 400
        
        # Process the update
        await application.process_update(update)
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        
        # Update error count
        try:
            with app.app_context():
                status = BotStatus.query.first()
                if status:
                    status.error_count += 1
                    db.session.commit()
        except:
            pass
        
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/status')
def bot_status():
    """Get bot status"""
    try:
        status = BotStatus.query.first()
        if not status:
            return jsonify({
                'status': 'inactive',
                'total_messages': 0,
                'total_trades': 0,
                'error_count': 0,
                'last_heartbeat': None
            })
        
        # Check if bot is active (heartbeat within last 5 minutes)
        if status.last_heartbeat:
            time_diff = datetime.utcnow() - status.last_heartbeat
            is_active = time_diff.total_seconds() < 300  # 5 minutes
        else:
            is_active = False
        
        return jsonify({
            'status': 'active' if is_active else 'inactive',
            'total_messages': status.total_messages,
            'total_trades': status.total_trades,
            'error_count': status.error_count,
            'last_heartbeat': status.last_heartbeat.isoformat() if status.last_heartbeat else None
        })
        
    except Exception as e:
        logger.error(f"Error fetching bot status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/recent-messages')
def recent_messages():
    """Get recent bot messages"""
    try:
        messages = BotMessage.query.order_by(BotMessage.timestamp.desc()).limit(10).all()
        
        result = []
        for msg in messages:
            result.append({
                'id': msg.id,
                'user_id': msg.user_id,
                'username': msg.username,
                'message': msg.message,
                'response': msg.response,
                'timestamp': msg.timestamp.isoformat(),
                'command_type': msg.command_type
            })
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error fetching recent messages: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/recent-trades')
def recent_trades():
    """Get recent trades"""
    try:
        trades = Trade.query.order_by(Trade.timestamp.desc()).limit(10).all()
        
        result = []
        for trade in trades:
            result.append({
                'id': trade.id,
                'user_id': trade.user_id,
                'symbol': trade.symbol,
                'action': trade.action,
                'quantity': trade.quantity,
                'price': trade.price,
                'status': trade.status,
                'timestamp': trade.timestamp.isoformat(),
                'error_message': trade.error_message
            })
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error fetching recent trades: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })

# Initialize webhook on startup
@app.before_first_request
def setup_bot():
    """Setup bot webhook"""
    from bot import setup_webhook
    setup_webhook()
