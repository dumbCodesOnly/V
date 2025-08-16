import os
import logging
from flask import Flask, request, jsonify, render_template
from datetime import datetime, timedelta
import urllib.request
import urllib.parse
import json
import random
import time
from werkzeug.middleware.proxy_fix import ProxyFix
try:
    # Try relative import first (for module import - Vercel/main.py)
    from .models import db, UserCredentials, UserTradingSession
except ImportError:
    # Fall back to absolute import (for direct execution - Telegram workflow)
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
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# Automatic webhook setup for deployments
def setup_webhook_on_deployment():
    """Automatically set up webhook for Vercel/deployment environments"""
    if not BOT_TOKEN:
        logging.warning("TELEGRAM_BOT_TOKEN not set, skipping webhook setup")
        return
    
    try:
        # Detect deployment URL
        deployment_url = None
        
        # Check for Vercel deployment
        if os.environ.get("VERCEL"):
            vercel_url = os.environ.get("VERCEL_URL")
            if vercel_url:
                deployment_url = f"https://{vercel_url}"
        
        # Check for Replit deployment  
        elif os.environ.get("REPLIT_DOMAIN"):
            deployment_url = f"https://{os.environ.get('REPLIT_DOMAIN')}"
        
        # Use custom webhook URL if provided
        elif WEBHOOK_URL and WEBHOOK_URL.strip():
            if WEBHOOK_URL.endswith("/webhook"):
                deployment_url = WEBHOOK_URL[:-8]
            else:
                deployment_url = WEBHOOK_URL
        
        if deployment_url:
            webhook_url = f"{deployment_url}/webhook"
            
            # Prepare webhook data with optional secret token
            webhook_data = {'url': webhook_url}
            secret_token = os.environ.get('WEBHOOK_SECRET_TOKEN')
            if secret_token:
                webhook_data['secret_token'] = secret_token
                logging.info("Setting webhook with secret token for enhanced security")
            
            # Set the webhook
            webhook_api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
            webhook_data_encoded = urllib.parse.urlencode(webhook_data).encode('utf-8')
            
            webhook_req = urllib.request.Request(webhook_api_url, data=webhook_data_encoded, method='POST')
            webhook_response = urllib.request.urlopen(webhook_req, timeout=10)
            
            if webhook_response.getcode() == 200:
                result = json.loads(webhook_response.read().decode('utf-8'))
                if result.get('ok'):
                    logging.info(f"Webhook set successfully to {webhook_url}")
                else:
                    logging.error(f"Webhook setup failed: {result.get('description')}")
            else:
                logging.error(f"Webhook request failed with status {webhook_response.getcode()}")
        else:
            logging.warning("WEBHOOK_URL or BOT_TOKEN not provided, webhook not set")
            
    except Exception as e:
        logging.error(f"Error setting up webhook: {e}")

# Automatic webhook setup disabled - use manual configuration

# Simple in-memory storage for the bot (replace with database in production)
bot_messages = []
bot_trades = []
bot_status = {
    'status': 'active',
    'total_messages': 5,
    'total_trades': 2,
    'error_count': 0,
    'last_heartbeat': datetime.utcnow().isoformat()
}

# User state tracking for API setup
user_api_setup_state = {}  # {user_id: {'step': 'api_key|api_secret|passphrase', 'exchange': 'toobit'}}

# Multi-trade management storage
user_trade_configs = {}  # {user_id: {trade_id: TradeConfig}}
user_selected_trade = {}  # {user_id: trade_id}
trade_counter = 0

# Initialize clean user environment
def initialize_user_environment(user_id):
    """Initialize a clean trading environment for a new user"""
    user_id = int(user_id)
    
    # Create empty trade configurations for user
    if user_id not in user_trade_configs:
        user_trade_configs[user_id] = {}
    
    # Initialize user's selected trade if not exists
    if user_id not in user_selected_trade:
        user_selected_trade[user_id] = None
    
    # Add demo closed positions for testing if user has no trades yet
    if len(user_trade_configs[user_id]) == 0 and user_id == 123456789:  # Demo user only
        # Create demo closed positions
        global trade_counter
        
        # Demo closed position 1 - Profitable BTC long
        trade_counter += 1
        closed_trade_1 = TradeConfig(f"demo_closed_{trade_counter}", f"Demo Position #{trade_counter}")
        closed_trade_1.symbol = "BTCUSDT"
        closed_trade_1.side = "long"
        closed_trade_1.amount = 100.0
        closed_trade_1.leverage = 10
        closed_trade_1.entry_price = 60000.0
        closed_trade_1.status = "stopped"
        closed_trade_1.final_pnl = 25.50
        closed_trade_1.closed_at = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        user_trade_configs[user_id][closed_trade_1.trade_id] = closed_trade_1
        
        # Demo closed position 2 - Loss ETH short  
        trade_counter += 1
        closed_trade_2 = TradeConfig(f"demo_closed_{trade_counter}", f"Demo Position #{trade_counter}")
        closed_trade_2.symbol = "ETHUSDT"
        closed_trade_2.side = "short"
        closed_trade_2.amount = 200.0
        closed_trade_2.leverage = 5
        closed_trade_2.entry_price = 3500.0
        closed_trade_2.status = "stopped"
        closed_trade_2.final_pnl = -15.75
        closed_trade_2.closed_at = (datetime.utcnow() - timedelta(hours=6)).isoformat()
        user_trade_configs[user_id][closed_trade_2.trade_id] = closed_trade_2
        
        # Demo closed position 3 - Recent SOL long
        trade_counter += 1
        closed_trade_3 = TradeConfig(f"demo_closed_{trade_counter}", f"Demo Position #{trade_counter}")
        closed_trade_3.symbol = "SOLUSDT"
        closed_trade_3.side = "long"
        closed_trade_3.amount = 50.0
        closed_trade_3.leverage = 20
        closed_trade_3.entry_price = 180.0
        closed_trade_3.status = "stopped"
        closed_trade_3.final_pnl = 8.20
        closed_trade_3.closed_at = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
        user_trade_configs[user_id][closed_trade_3.trade_id] = closed_trade_3
        
        # Add an active position for demonstration
        trade_counter += 1
        active_trade = TradeConfig(f"demo_active_{trade_counter}", f"Demo Active Position #{trade_counter}")
        active_trade.symbol = "BTCUSDT"
        active_trade.side = "long"
        active_trade.amount = 150.0
        active_trade.leverage = 10
        active_trade.entry_price = 60000.0
        active_trade.current_price = 61000.0  # Set current price for P&L calculation
        active_trade.status = "active"
        active_trade.take_profits = [
            {"level": 1, "percentage": 5.0, "close_percentage": 50.0},
            {"level": 2, "percentage": 10.0, "close_percentage": 50.0}
        ]
        active_trade.stop_loss_percent = 3.0
        active_trade.position_margin = calculate_position_margin(active_trade.amount, active_trade.leverage)
        active_trade.position_value = active_trade.amount * active_trade.leverage
        active_trade.position_size = active_trade.position_value / active_trade.entry_price
        active_trade.unrealized_pnl = calculate_unrealized_pnl(
            active_trade.entry_price, active_trade.current_price,
            active_trade.amount, active_trade.leverage, active_trade.side
        )
        user_trade_configs[user_id][active_trade.trade_id] = active_trade
        
        # Add a pending limit order for demonstration
        trade_counter += 1
        pending_trade = TradeConfig(f"demo_pending_{trade_counter}", f"Demo Pending Position #{trade_counter}")
        pending_trade.symbol = "ETHUSDT"
        pending_trade.side = "long"
        pending_trade.amount = 100.0
        pending_trade.leverage = 5
        pending_trade.entry_type = "limit"
        pending_trade.entry_price = 3400.0  # Below current market price for long limit
        pending_trade.current_price = 3500.0
        pending_trade.status = "pending"
        pending_trade.take_profits = [
            {"level": 1, "percentage": 8.0, "close_percentage": 100.0}
        ]
        pending_trade.stop_loss_percent = 5.0
        user_trade_configs[user_id][pending_trade.trade_id] = pending_trade
        
        # Add a configured trade (not yet executed) for trading tab demonstration
        trade_counter += 1
        config_trade = TradeConfig(f"demo_config_{trade_counter}", f"Demo Config #{trade_counter}")
        config_trade.symbol = "ADAUSDT"
        config_trade.side = "long"
        config_trade.amount = 80.0
        config_trade.leverage = 15
        config_trade.entry_type = "market"
        config_trade.take_profits = [
            {"level": 1, "percentage": 6.0, "close_percentage": 50.0},
            {"level": 2, "percentage": 12.0, "close_percentage": 50.0}
        ]
        config_trade.stop_loss_percent = 4.0
        # Keep default status = "configured" 
        user_trade_configs[user_id][config_trade.trade_id] = config_trade



class TradeConfig:
    def __init__(self, trade_id, name="New Trade"):
        self.trade_id = trade_id
        self.name = name
        self.symbol = ""
        self.side = ""  # 'long' or 'short'
        self.amount = 0.0
        self.leverage = 1
        self.entry_price = 0.0
        self.entry_type = ""  # 'market' or 'limit'
        self.waiting_for_limit_price = False  # Track if waiting for limit price input
        # Take profit system - percentages and allocations
        self.take_profits = []  # List of {percentage: float, allocation: float}
        self.tp_config_step = "percentages"  # "percentages" or "allocations"
        self.stop_loss_percent = 0.0
        self.breakeven_after = 0.0
        # Trailing Stop System - Clean Implementation
        self.trailing_stop_enabled = False
        self.trail_percentage = 0.0  # Percentage for trailing stop
        self.trail_activation_price = 0.0  # Price level to activate trailing stop
        self.waiting_for_trail_percent = False  # Track if waiting for trail percentage input
        self.waiting_for_trail_activation = False  # Track if waiting for trail activation price
        self.status = "configured"  # configured, pending, active, stopped
        # Margin tracking
        self.position_margin = 0.0  # Margin used for this position
        self.unrealized_pnl = 0.0   # Current floating P&L
        self.current_price = 0.0    # Current market price
        self.position_size = 0.0    # Actual position size in contracts
        self.position_value = 0.0   # Total position value
        self.final_pnl = 0.0        # Final P&L when position is closed
        self.closed_at = ""         # Timestamp when position was closed
        
    def get_display_name(self):
        if self.symbol and self.side:
            return f"{self.name} ({self.symbol} {self.side.upper()})"
        return self.name
        
    def is_complete(self):
        return all([self.symbol, self.side, self.amount > 0])
        
    def get_config_summary(self):
        summary = f"📋 {self.get_display_name()}\n\n"
        summary += f"Symbol: {self.symbol if self.symbol else 'Not set'}\n"
        summary += f"Side: {self.side if self.side else 'Not set'}\n"
        summary += f"Amount: {self.amount if self.amount > 0 else 'Not set'}\n"
        summary += f"Leverage: {self.leverage}x\n"
        if self.entry_type == "limit" and self.entry_price > 0:
            summary += f"Entry: ${self.entry_price:.4f} (LIMIT)\n"
        else:
            summary += f"Entry: Market Price\n"
        
        # Show take profits with prices if entry price is available
        if self.take_profits:
            summary += f"Take Profits:\n"
            tp_sl_data = calculate_tp_sl_prices_and_amounts(self) if self.entry_price > 0 else {}
            
            for i, tp in enumerate(self.take_profits, 1):
                tp_percentage = tp.get('percentage', 0)
                tp_allocation = tp.get('allocation', 0)
                
                if tp_sl_data.get('take_profits') and len(tp_sl_data['take_profits']) >= i:
                    tp_calc = tp_sl_data['take_profits'][i-1]
                    summary += f"  TP{i}: ${tp_calc['price']:.4f} (+${tp_calc['profit_amount']:.2f}) [{tp_percentage}% - {tp_allocation}%]\n"
                else:
                    summary += f"  TP{i}: {tp_percentage}% ({tp_allocation}%)\n"
        else:
            summary += f"Take Profits: Not set\n"
            
        # Show stop loss with price if entry price is available
        if self.stop_loss_percent > 0:
            tp_sl_data = calculate_tp_sl_prices_and_amounts(self) if self.entry_price > 0 else {}
            
            if tp_sl_data.get('stop_loss'):
                sl_calc = tp_sl_data['stop_loss']
                summary += f"Stop Loss: ${sl_calc['price']:.4f} (-${sl_calc['loss_amount']:.2f}) [{self.stop_loss_percent}%]\n"
            else:
                summary += f"Stop Loss: {self.stop_loss_percent}%\n"
        else:
            summary += "Stop Loss: Not set\n"
        
        # Show trailing stop status
        if self.trailing_stop_enabled:
            summary += f"Trailing Stop: Enabled\n"
            if self.trail_percentage > 0:
                summary += f"  Trail %: {self.trail_percentage}%\n"
            if self.trail_activation_price > 0:
                summary += f"  Activation: ${self.trail_activation_price:.4f}\n"
        else:
            summary += f"Trailing Stop: Disabled\n"
            
        summary += f"Status: {self.status.title()}\n"
        return summary
    
    def get_progress_indicator(self):
        """Get a visual progress indicator showing configuration completion"""
        steps = {
            "Symbol": "✅" if self.symbol else "⏳",
            "Side": "✅" if self.side else "⏳", 
            "Amount": "✅" if self.amount > 0 else "⏳",
            "Entry": "✅" if (self.entry_type == "market" or (self.entry_type == "limit" and self.entry_price > 0)) else "⏳",
            "Take Profits": "✅" if self.take_profits else "⏳",
            "Stop Loss": "✅" if self.stop_loss_percent > 0 else "⏳"
        }
        
        completed = sum(1 for status in steps.values() if status == "✅")
        total = len(steps)
        progress_bar = "█" * completed + "░" * (total - completed)
        
        progress = f"📊 Progress: {completed}/{total} [{progress_bar}]\n"
        progress += " → ".join([f"{step} {status}" for step, status in steps.items()])
        
        return progress
    
    def get_trade_header(self, current_step=""):
        """Get formatted trade header with progress and settings summary for display"""
        header = f"🎯 {self.get_display_name()}\n"
        header += f"{self.get_progress_indicator()}\n\n"
        
        # Add current settings summary
        header += "📋 Current Settings:\n"
        header += f"   💱 Pair: {self.symbol if self.symbol else 'Not set'}\n"
        header += f"   📈 Side: {self.side.upper() if self.side else 'Not set'}\n"
        # Show position size (margin × leverage) not just margin
        position_size = self.amount * self.leverage if self.amount > 0 else 0
        header += f"   💰 Position Size: ${position_size if position_size > 0 else 'Not set'} (Margin: ${self.amount if self.amount > 0 else 'Not set'})\n"
        header += f"   📊 Leverage: {self.leverage}x\n"
        
        if self.entry_type == "limit" and self.entry_price > 0:
            header += f"   🎯 Entry: ${self.entry_price:.4f} (LIMIT)\n"
        elif self.entry_type == "market":
            header += f"   🎯 Entry: Market Price\n"
        else:
            header += f"   🎯 Entry: Not set\n"
            
        if self.take_profits:
            header += f"   🎯 Take Profits: {len(self.take_profits)} levels\n"
        else:
            header += f"   🎯 Take Profits: Not set\n"
            
        if self.stop_loss_percent > 0:
            header += f"   🛑 Stop Loss: {self.stop_loss_percent}%\n"
        else:
            header += f"   🛑 Stop Loss: Not set\n"
            
        # Break-even settings
        if self.breakeven_after > 0:
            header += f"   ⚖️ Break-even: After {self.breakeven_after}% profit\n"
        else:
            header += f"   ⚖️ Break-even: Not set\n"
            
        # Trailing stop settings
        if self.trailing_stop_enabled:
            trail_info = "Enabled"
            if self.trail_percentage > 0:
                trail_info += f" ({self.trail_percentage}%)"
            if self.trail_activation_price > 0:
                trail_info += f" @ ${self.trail_activation_price:.4f}"
            header += f"   📉 Trailing Stop: {trail_info}\n"
        else:
            header += f"   📉 Trailing Stop: Disabled\n"
        
        if current_step:
            header += f"\n🔧 Current Step: {current_step}\n"
        header += "─" * 40 + "\n"
        return header

@app.route('/')
def mini_app():
    """Telegram Mini App interface - Main route"""
    return render_template('mini_app.html')

@app.route('/miniapp')
def mini_app_alias():
    """Telegram Mini App interface - Alias route"""
    return render_template('mini_app.html')

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/health')
def api_health_check():
    """API Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'api_version': '1.0'
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

@app.route('/api/margin-data')
def margin_data():
    """Get comprehensive margin data for a specific user"""
    user_id = request.args.get('user_id')
    if not user_id or user_id == 'undefined':
        # For testing outside Telegram, use a demo user
        user_id = '123456789'
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({'error': 'Invalid user ID format'}), 400
    
    # Initialize user environment if needed
    initialize_user_environment(chat_id)
    
    # Update all positions with live market data before returning margin data
    update_all_positions_with_live_data()
    
    # Get margin data for this specific user only
    margin_summary = get_margin_summary(chat_id)
    user_positions = []
    
    if chat_id in user_trade_configs:
        for trade_id, config in user_trade_configs[chat_id].items():
            if config.status in ["active", "pending"] and config.symbol:
                # Calculate TP/SL prices and amounts
                tp_sl_data = calculate_tp_sl_prices_and_amounts(config)
                
                user_positions.append({
                    'trade_id': trade_id,
                    'symbol': config.symbol,
                    'side': config.side,
                    'amount': config.amount,  # This is the margin
                    'position_size': config.amount * config.leverage,  # This is the actual position size
                    'leverage': config.leverage,
                    'margin_used': config.position_margin,
                    'entry_price': config.entry_price,
                    'current_price': config.current_price,
                    'unrealized_pnl': config.unrealized_pnl,
                    'status': config.status,
                    'take_profits': config.take_profits,
                    'stop_loss_percent': config.stop_loss_percent,
                    'tp_sl_calculations': tp_sl_data
                })
    
    # Calculate total realized P&L from closed positions
    total_realized_pnl = 0.0
    if chat_id in user_trade_configs:
        for config in user_trade_configs[chat_id].values():
            if config.status == "stopped" and hasattr(config, 'final_pnl') and config.final_pnl is not None:
                total_realized_pnl += config.final_pnl
    
    return jsonify({
        'user_id': user_id,
        'summary': {
            'account_balance': margin_summary['account_balance'],
            'total_margin_used': margin_summary['total_margin'],
            'free_margin': margin_summary['free_margin'],
            'unrealized_pnl': margin_summary['unrealized_pnl'],
            'realized_pnl': total_realized_pnl,
            'total_pnl': margin_summary['unrealized_pnl'] + total_realized_pnl,
            'margin_utilization': (margin_summary['total_margin'] / margin_summary['account_balance'] * 100) if margin_summary['account_balance'] > 0 else 0,
            'total_positions': len(user_positions)
        },
        'positions': user_positions,
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/positions')
def api_positions():
    """Get positions for the web app - alias for margin-data"""
    return margin_data()

@app.route('/api/trading/new')
def api_trading_new():
    """Create new trading configuration"""
    user_id = request.args.get('user_id', '123456789')
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({'error': 'Invalid user ID format'}), 400
    
    # Initialize user environment if needed
    initialize_user_environment(chat_id)
    
    # Generate new trade ID
    global trade_counter
    trade_counter += 1
    trade_id = f"trade_{trade_counter}"
    
    # Create new trade config
    if chat_id not in user_trade_configs:
        user_trade_configs[chat_id] = {}
    
    new_trade = TradeConfig(trade_id, f"Position #{trade_counter}")
    user_trade_configs[chat_id][trade_id] = new_trade
    user_selected_trade[chat_id] = trade_id
    
    return jsonify({
        'success': True,
        'trade_id': trade_id,
        'trade_name': new_trade.name,
        'message': f'Created new position: {new_trade.get_display_name()}'
    })

@app.route('/api/user-trades')
def user_trades():
    """Get all trades for a specific user"""
    user_id = request.args.get('user_id')
    if not user_id or user_id == 'undefined':
        # For testing outside Telegram, use a demo user
        user_id = '123456789'
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({'error': 'Invalid user ID format'}), 400
    
    # Initialize user environment if needed
    initialize_user_environment(chat_id)
    
    user_trade_list = []
    
    if chat_id in user_trade_configs:
        for trade_id, config in user_trade_configs[chat_id].items():
            # Calculate TP/SL prices and amounts
            tp_sl_data = calculate_tp_sl_prices_and_amounts(config)
            
            # For closed positions, get final P&L from bot_trades if not stored in config
            final_pnl = None
            closed_at = None
            if config.status == "stopped":
                if hasattr(config, 'final_pnl') and config.final_pnl is not None:
                    final_pnl = config.final_pnl
                    closed_at = getattr(config, 'closed_at', None)
                else:
                    # Fallback to bot_trades list
                    closed_trade = next((trade for trade in bot_trades 
                                       if trade.get('trade_id') == trade_id and trade.get('user_id') == str(chat_id)), None)
                    if closed_trade:
                        final_pnl = closed_trade.get('final_pnl', 0)
                        closed_at = closed_trade.get('timestamp')
            
            user_trade_list.append({
                'trade_id': trade_id,
                'name': config.name,
                'symbol': config.symbol,
                'side': config.side,
                'amount': config.amount,  # This is the margin
                'position_size': config.amount * config.leverage,  # This is the actual position size
                'leverage': config.leverage,
                'entry_type': config.entry_type,
                'entry_price': config.entry_price,
                'take_profits': config.take_profits,
                'stop_loss_percent': config.stop_loss_percent,
                'status': config.status,
                'position_margin': config.position_margin,
                'unrealized_pnl': config.unrealized_pnl,
                'current_price': config.current_price,
                'breakeven_after': config.breakeven_after,
                'trailing_stop_enabled': config.trailing_stop_enabled,
                'trail_percentage': config.trail_percentage,
                'trail_activation_price': config.trail_activation_price,
                'tp_sl_calculations': tp_sl_data,
                'final_pnl': final_pnl,  # Include final P&L for closed positions
                'closed_at': closed_at   # Include closure timestamp
            })
    
    return jsonify({
        'user_id': user_id,
        'trades': user_trade_list,
        'total_trades': len(user_trade_list),
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/trade-config')
def trade_config():
    """Get specific trade configuration"""
    trade_id = request.args.get('trade_id')
    user_id = request.headers.get('X-Telegram-User-ID')
    
    if not trade_id or not user_id:
        return jsonify({'error': 'Trade ID and User ID required'}), 400
    
    chat_id = int(user_id)
    
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        config = user_trade_configs[chat_id][trade_id]
        return jsonify({
            'trade_id': trade_id,
            'name': config.name,
            'symbol': config.symbol,
            'side': config.side,
            'amount': config.amount,  # This is the margin
            'position_size': config.amount * config.leverage,  # This is the actual position size
            'leverage': config.leverage,
            'entry_type': config.entry_type,
            'entry_price': config.entry_price,
            'take_profits': config.take_profits,
            'stop_loss_percent': config.stop_loss_percent,
            'status': config.status,
            'breakeven_after': config.breakeven_after,
            'trailing_stop_enabled': config.trailing_stop_enabled,
            'trail_percentage': config.trail_percentage,
            'trail_activation_price': config.trail_activation_price
        })
    
    return jsonify({'error': 'Trade not found'}), 404

@app.route('/api/save-trade', methods=['POST'])
def save_trade():
    """Save or update trade configuration"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        trade_data = data.get('trade')
        
        if not user_id or not trade_data:
            return jsonify({'error': 'User ID and trade data required'}), 400
        
        chat_id = int(user_id)
        trade_id = trade_data.get('trade_id')
        
        # Ensure user exists in storage
        if chat_id not in user_trade_configs:
            user_trade_configs[chat_id] = {}
        
        # Create or update trade config
        if trade_id.startswith('new_'):
            # Generate new trade ID
            global trade_counter
            trade_counter += 1
            trade_id = str(trade_counter)
        
        if trade_id not in user_trade_configs[chat_id]:
            user_trade_configs[chat_id][trade_id] = TradeConfig(trade_id, trade_data.get('name', 'New Trade'))
        
        config = user_trade_configs[chat_id][trade_id]
        
        # SAFETY CHECK: Prevent re-execution of active trades by restricting core parameter modifications
        is_active_trade = config.status in ['active', 'pending']
        
        if is_active_trade:
            # For active/pending trades, only allow risk management parameter modifications
            # Check if core parameters are actually being CHANGED (not just present in request)
            core_param_changes = []
            
            if 'symbol' in trade_data and trade_data['symbol'] != config.symbol:
                core_param_changes.append('symbol')
            if 'side' in trade_data and trade_data['side'] != config.side:
                core_param_changes.append('side')
            if 'amount' in trade_data and float(trade_data['amount']) != config.amount:
                core_param_changes.append('amount')
            if 'leverage' in trade_data and int(trade_data['leverage']) != config.leverage:
                core_param_changes.append('leverage')
            if 'entry_type' in trade_data and trade_data['entry_type'] != config.entry_type:
                core_param_changes.append('entry_type')
            if 'entry_price' in trade_data:
                new_entry_price = float(trade_data['entry_price']) if trade_data['entry_price'] else 0.0
                if new_entry_price != config.entry_price:
                    core_param_changes.append('entry_price')
            
            if core_param_changes:
                logging.warning(f"Attempted to modify core parameters {core_param_changes} for active trade {trade_id}. Changes rejected for safety.")
                return jsonify({
                    'error': f"Cannot modify core trade parameters ({', '.join(core_param_changes)}) for active trades. Only risk management parameters (take profits, stop loss, break-even, trailing stop) can be modified.",
                    'active_trade': True,
                    'rejected_changes': core_param_changes
                }), 400
            
            logging.info(f"Modifying risk management parameters for active trade {trade_id} (core parameters unchanged)")
        else:
                # For non-active trades, allow all parameter updates
            if 'symbol' in trade_data:
                config.symbol = trade_data['symbol']
            if 'side' in trade_data:
                config.side = trade_data['side']
            if 'amount' in trade_data:
                config.amount = float(trade_data['amount'])
            if 'leverage' in trade_data:
                config.leverage = int(trade_data['leverage'])
            if 'entry_type' in trade_data:
                config.entry_type = trade_data['entry_type']
            if 'entry_price' in trade_data:
                config.entry_price = float(trade_data['entry_price']) if trade_data['entry_price'] else 0.0
        # Risk management parameters - always allowed for all trade statuses
        risk_params_updated = []
        if 'take_profits' in trade_data:
            config.take_profits = trade_data['take_profits']
            risk_params_updated.append('take_profits')
        if 'stop_loss_percent' in trade_data:
            config.stop_loss_percent = float(trade_data['stop_loss_percent']) if trade_data['stop_loss_percent'] else 0.0
            risk_params_updated.append('stop_loss')
        
        # Update breakeven and trailing stop settings
        if 'breakeven_after' in trade_data:
            config.breakeven_after = trade_data['breakeven_after']
            risk_params_updated.append('breakeven')
        if 'trailing_stop_enabled' in trade_data:
            config.trailing_stop_enabled = bool(trade_data['trailing_stop_enabled'])
            risk_params_updated.append('trailing_stop')
        if 'trail_percentage' in trade_data:
            config.trail_percentage = float(trade_data['trail_percentage']) if trade_data['trail_percentage'] else 0.0
            risk_params_updated.append('trailing_percentage')
        if 'trail_activation_price' in trade_data:
            config.trail_activation_price = float(trade_data['trail_activation_price']) if trade_data['trail_activation_price'] else 0.0
            risk_params_updated.append('trailing_activation')
        
        # Log risk management updates for active trades
        if is_active_trade and risk_params_updated:
            logging.info(f"Updated risk management parameters for active trade {trade_id}: {', '.join(risk_params_updated)}")
        
        # Set as selected trade for user
        user_selected_trade[chat_id] = trade_id
        
        success_message = 'Trade configuration saved successfully'
        if is_active_trade and risk_params_updated:
            success_message = f"Risk management parameters updated for active trade: {', '.join(risk_params_updated)}"
        
        return jsonify({
            'success': True,
            'trade_id': trade_id,
            'message': success_message,
            'active_trade': is_active_trade,
            'risk_params_updated': risk_params_updated if is_active_trade else []
        })
        
    except Exception as e:
        logging.error(f"Error saving trade: {str(e)}")
        return jsonify({'error': 'Failed to save trade configuration'}), 500

@app.route('/api/execute-trade', methods=['POST'])
def execute_trade():
    """Execute a trade configuration"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        trade_id = data.get('trade_id')
        
        if not user_id or not trade_id:
            return jsonify({'error': 'User ID and trade ID required'}), 400
        
        chat_id = int(user_id)
        
        if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
            return jsonify({'error': 'Trade configuration not found'}), 404
        
        config = user_trade_configs[chat_id][trade_id]
        
        # Validate configuration
        if not config.is_complete():
            return jsonify({'error': 'Trade configuration is incomplete'}), 400
        
        # Get current market price
        current_market_price = get_live_market_price(config.symbol)
        
        # Handle limit orders - check if price condition is met
        if config.entry_type == "limit" and config.entry_price > 0:
            # No validation restrictions - allow all limit order types:
            # - Long below market (traditional buy limit)
            # - Long above market (buy stop for breakouts)
            # - Short above market (traditional sell limit)  
            # - Short below market (sell stop for breakdowns)
            
            # Check if limit order should execute
            should_execute = False
            if config.side == "long":
                if config.entry_price <= current_market_price:
                    # Long limit (buy limit): executes when market drops to or below limit price
                    should_execute = current_market_price <= config.entry_price
                else:
                    # Long stop (buy stop): executes when market rises to or above stop price  
                    should_execute = current_market_price >= config.entry_price
            elif config.side == "short":
                if config.entry_price >= current_market_price:
                    # Short limit (sell limit): executes when market rises to or above limit price
                    should_execute = current_market_price >= config.entry_price
                else:
                    # Short stop (sell stop): executes when market drops to or below stop price
                    should_execute = current_market_price <= config.entry_price
            
            if not should_execute:
                # Set as pending order, waiting for price to reach limit
                config.status = "pending"
                config.current_price = current_market_price
                config.unrealized_pnl = 0.0
                
                logging.info(f"Limit order placed: {config.symbol} {config.side} at ${config.entry_price} (current market: ${current_market_price})")
                
                return jsonify({
                    'success': True,
                    'message': f'Limit order placed successfully. Waiting for market price to reach ${config.entry_price:.4f}',
                    'trade': {
                        'trade_id': trade_id,
                        'symbol': config.symbol,
                        'side': config.side,
                        'amount': config.amount,
                        'entry_price': config.entry_price,
                        'current_price': current_market_price,
                        'status': config.status
                    }
                })
        
        # Execute trade (market order or limit order conditions met)
        config.status = "active"
        config.position_margin = calculate_position_margin(config.amount, config.leverage)
        # Position value = margin * leverage (total value of position)
        config.position_value = config.amount * config.leverage
        # Position size = position value / entry price (number of coins)
        config.position_size = config.position_value / current_market_price
        
        if config.entry_type == "market" or config.entry_price is None:
            # Market order: use current price as entry and execution price
            config.current_price = current_market_price
            config.entry_price = current_market_price
        else:
            # Limit order that met conditions: use limit price as entry, current price for display
            config.current_price = current_market_price
            # entry_price already set to limit price
        
        config.unrealized_pnl = 0.0
        
        logging.info(f"Trade executed: {config.symbol} {config.side} at ${config.entry_price} (entry type: {config.entry_type})")
        
        # Log trade execution
        bot_trades.append({
            'id': len(bot_trades) + 1,
            'user_id': str(chat_id),
            'trade_id': trade_id,
            'symbol': config.symbol,
            'side': config.side,
            'amount': config.amount,
            'leverage': config.leverage,
            'entry_price': config.entry_price,
            'timestamp': datetime.utcnow().isoformat(),
            'status': 'executed'
        })
        
        bot_status['total_trades'] += 1
        
        return jsonify({
            'success': True,
            'message': 'Trade executed successfully',
            'trade': {
                'trade_id': trade_id,
                'symbol': config.symbol,
                'side': config.side,
                'amount': config.amount,
                'entry_price': config.entry_price,
                'status': config.status
            }
        })
        
    except Exception as e:
        logging.error(f"Error executing trade: {str(e)}")
        return jsonify({'error': 'Failed to execute trade'}), 500

@app.route('/api/user-credentials')
def get_user_credentials():
    """Get user API credentials status"""
    user_id = request.args.get('user_id')
    if not user_id or user_id == 'undefined':
        user_id = '123456789'  # Demo user
    
    try:
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        
        if user_creds:
            return jsonify({
                'has_credentials': user_creds.has_credentials(),
                'exchange': user_creds.exchange_name,
                'testnet_mode': user_creds.testnet_mode,
                'is_active': user_creds.is_active,
                'last_used': user_creds.last_used.isoformat() if user_creds.last_used else None,
                'created_at': user_creds.created_at.isoformat()
            })
        else:
            return jsonify({
                'has_credentials': False,
                'exchange': None,
                'testnet_mode': True,
                'is_active': False,
                'last_used': None,
                'created_at': None
            })
    except Exception as e:
        logging.error(f"Error getting user credentials: {str(e)}")
        return jsonify({'error': 'Failed to get credentials status'}), 500

@app.route('/api/save-credentials', methods=['POST'])
def save_credentials():
    """Save user API credentials"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
        user_id = data.get('user_id', '123456789')
        exchange = data.get('exchange', 'toobit')
        api_key = data.get('api_key', '').strip()
        api_secret = data.get('api_secret', '').strip()
        passphrase = data.get('passphrase', '').strip()
        
        if not api_key or not api_secret:
            return jsonify({'error': 'API key and secret are required'}), 400
        
        if len(api_key) < 10 or len(api_secret) < 10:
            return jsonify({'error': 'API key and secret seem too short'}), 400
        
        # Get or create user credentials
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        if not user_creds:
            user_creds = UserCredentials()
            user_creds.telegram_user_id = str(user_id)
            user_creds.exchange_name = exchange
            db.session.add(user_creds)
        
        # Update credentials
        user_creds.set_api_key(api_key)
        user_creds.set_api_secret(api_secret)
        if passphrase:
            user_creds.set_passphrase(passphrase)
        user_creds.exchange_name = exchange
        user_creds.is_active = True
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Credentials saved successfully',
            'exchange': exchange,
            'testnet_mode': user_creds.testnet_mode
        })
        
    except Exception as e:
        logging.error(f"Error saving credentials: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to save credentials'}), 500

@app.route('/api/delete-credentials', methods=['POST'])
def delete_credentials():
    """Delete user API credentials"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
        user_id = data.get('user_id', '123456789')
        
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        if not user_creds:
            return jsonify({'error': 'No credentials found'}), 404
        
        db.session.delete(user_creds)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Credentials deleted successfully'
        })
        
    except Exception as e:
        logging.error(f"Error deleting credentials: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to delete credentials'}), 500

@app.route('/api/close-trade', methods=['POST'])
def close_trade():
    """Close an active trade"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        trade_id = data.get('trade_id')
        
        if not user_id or not trade_id:
            return jsonify({'error': 'User ID and trade ID required'}), 400
        
        chat_id = int(user_id)
        
        if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
            return jsonify({'error': 'Trade not found'}), 404
        
        config = user_trade_configs[chat_id][trade_id]
        
        if config.status != "active":
            return jsonify({'error': 'Trade is not active'}), 400
        
        # Simulate closing the trade
        final_pnl = config.unrealized_pnl
        config.status = "stopped"
        config.final_pnl = final_pnl  # Store final P&L in the config object too
        config.closed_at = datetime.utcnow().isoformat()  # Store closure timestamp
        config.unrealized_pnl = 0.0
        
        # Log trade closure
        bot_trades.append({
            'id': len(bot_trades) + 1,
            'user_id': str(chat_id),
            'trade_id': trade_id,
            'symbol': config.symbol,
            'side': config.side,
            'amount': config.amount,
            'final_pnl': final_pnl,
            'timestamp': datetime.utcnow().isoformat(),
            'status': 'closed'
        })
        
        return jsonify({
            'success': True,
            'message': 'Trade closed successfully',
            'final_pnl': final_pnl
        })
        
    except Exception as e:
        logging.error(f"Error closing trade: {str(e)}")
        return jsonify({'error': 'Failed to close trade'}), 500

@app.route('/api/delete-trade', methods=['POST'])
def delete_trade():
    """Delete a trade configuration"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        trade_id = data.get('trade_id')
        
        if not user_id or not trade_id:
            return jsonify({'error': 'User ID and trade ID required'}), 400
        
        chat_id = int(user_id)
        
        if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
            return jsonify({'error': 'Trade not found'}), 404
        
        config = user_trade_configs[chat_id][trade_id]
        trade_name = config.get_display_name() if hasattr(config, 'get_display_name') else config.name
        
        # Remove from configurations
        del user_trade_configs[chat_id][trade_id]
        
        # Remove from selected trade if it was selected
        if user_selected_trade.get(chat_id) == trade_id:
            if chat_id in user_selected_trade:
                del user_selected_trade[chat_id]
        
        return jsonify({
            'success': True,
            'message': f'Trade configuration "{trade_name}" deleted successfully'
        })
        
    except Exception as e:
        logging.error(f"Error deleting trade: {str(e)}")
        return jsonify({'error': 'Failed to delete trade'}), 500

def verify_telegram_webhook(data):
    """Verify that the webhook request is from Telegram"""
    try:
        # Get client IP for logging
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        logging.info(f"Webhook request from IP: {client_ip}")
        
        # Telegram IP ranges for validation (optional strict checking)
        telegram_ip_ranges = [
            "149.154.160.0/20",
            "91.108.4.0/22",
            "149.154.164.0/22", 
            "149.154.168.0/22",
            "149.154.172.0/22"
        ]
        
        # Check for secret token if configured
        secret_token = os.environ.get('WEBHOOK_SECRET_TOKEN')
        if secret_token:
            provided_token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
            if provided_token != secret_token:
                logging.warning(f"Invalid secret token in webhook request from {client_ip}")
                return False
            logging.info("Secret token verified successfully")
        
        # Verify the request structure looks like a valid Telegram update
        if not isinstance(data, dict):
            logging.warning(f"Invalid data structure from {client_ip}")
            return False
            
        # Should have either message or callback_query
        if not (data.get('message') or data.get('callback_query') or data.get('inline_query') or data.get('edited_message')):
            logging.warning(f"Invalid Telegram update structure from {client_ip}")
            return False
            
        # Basic structure validation for messages
        if data.get('message'):
            msg = data['message']
            if not msg.get('chat') or not msg['chat'].get('id'):
                logging.warning(f"Invalid message structure from {client_ip}")
                return False
        
        # Log successful verification
        logging.info(f"Webhook verification successful from {client_ip}")
        return True
        
    except Exception as e:
        logging.error(f"Webhook verification error: {e}")
        return False

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook with security verification"""
    try:
        # Get the JSON data from Telegram
        json_data = request.get_json()
        
        if not json_data:
            logging.warning("No JSON data received")
            return jsonify({'status': 'error', 'message': 'No JSON data'}), 400
        
        # Verify this is a legitimate Telegram webhook
        if not verify_telegram_webhook(json_data):
            logging.warning("Invalid webhook request rejected")
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
        
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
    
    elif text.startswith('/api') or text.startswith('/credentials'):
        return handle_api_setup_command(text, chat_id, user)
    

    
    elif text.startswith('/price'):
        parts = text.split()
        if len(parts) < 2:
            return "❌ Please provide a symbol. Example: /price BTCUSDT", None
        
        symbol = parts[1].upper()
        try:
            price = get_live_market_price(symbol)
            return f"💰 {symbol}: ${price:.4f} (Live Price)", None
        except Exception as e:
            logging.error(f"Error fetching live price for {symbol}: {e}")
            return f"❌ Could not fetch live price for {symbol}", None
    
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
        
        # Execute trade with live market price
        try:
            price = get_live_market_price(symbol)
        except Exception as e:
            logging.error(f"Error fetching live price for trade: {e}")
            return f"❌ Could not fetch live market price for {symbol}", None
        
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
        # Portfolio functionality is now handled via the positions tab in web interface
        return "📊 Check your portfolio in the positions tab of the web interface.", None
    
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
                    
                    # Check if we're expecting trailing stop percentage
                    if config.waiting_for_trail_percent:
                        config.trail_percentage = value
                        config.waiting_for_trail_percent = False
                        config.trailing_stop_enabled = True
                        return f"✅ Set trailing stop percentage to {value}%\n\nTrailing stop is now enabled!", get_trailing_stop_menu()
                    
                    # Check if we're expecting trailing stop activation price
                    elif config.waiting_for_trail_activation:
                        config.trail_activation_price = value
                        config.waiting_for_trail_activation = False
                        config.trailing_stop_enabled = True
                        return f"✅ Set activation price to ${value:.4f}\n\nTrailing stop will activate when price reaches this level!", get_trailing_stop_menu()
                    
                    # Check if we're expecting an amount input
                    elif config.amount <= 0:
                        config.amount = value
                        header = config.get_trade_header("Amount Set")
                        return f"{header}✅ Set trade amount to ${value}", get_trading_menu(chat_id)
                    
                    # Check if we're expecting a limit price
                    elif config.waiting_for_limit_price:
                        config.entry_price = value
                        config.waiting_for_limit_price = False
                        return f"✅ Set limit price to ${value:.4f}\n\n🎯 Now let's set your take profits:", get_tp_percentage_input_menu()
                    
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
                    elif config.stop_loss_percent <= 0:
                        config.stop_loss_percent = value
                        return f"✅ Set stop loss to {value}%\n\n🎯 Trade configuration complete!", get_trading_menu(chat_id)
                    
                except ValueError:
                    pass
        
        # Handle API setup text input
        if chat_id in user_api_setup_state:
            return handle_api_text_input(text, chat_id, user)
        
        return "🤔 I didn't understand that command. Use the menu buttons to navigate.", get_main_menu()

def handle_api_setup_command(text, chat_id, user):
    """Handle API setup commands"""
    if text.startswith('/api'):
        return show_api_menu(chat_id, user)
    elif text.startswith('/credentials'):
        return show_credentials_status(chat_id, user)
    
    return "🔑 Use /api to manage your exchange API credentials.", get_main_menu()

def show_api_menu(chat_id, user):
    """Show API credentials management menu"""
    try:
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(chat_id)).first()
        
        if user_creds and user_creds.has_credentials():
            status_text = f"""🔑 API Credentials Status

✅ Exchange: {user_creds.exchange_name.title()}
✅ API Key: Set (ending in ...{user_creds.get_api_key()[-4:] if user_creds.get_api_key() else 'N/A'})
✅ API Secret: Set
{"🧪 Mode: Testnet" if user_creds.testnet_mode else "🚀 Mode: Live Trading"}
📅 Added: {user_creds.created_at.strftime('%Y-%m-%d %H:%M')}

Choose an option:"""
        else:
            status_text = """🔑 API Credentials Setup

❌ No API credentials configured
⚠️ You need to add your exchange API credentials to enable live trading

Choose an option:"""
        
        return status_text, get_api_management_menu(user_creds is not None and user_creds.has_credentials())
    
    except Exception as e:
        logging.error(f"Error showing API menu: {str(e)}")
        return "❌ Error accessing credentials. Please try again.", get_main_menu()

def show_credentials_status(chat_id, user):
    """Show detailed credentials status"""
    try:
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(chat_id)).first()
        
        if not user_creds or not user_creds.has_credentials():
            return "❌ No API credentials found. Use /api to set up your credentials.", get_main_menu()
        
        # Get recent session info
        recent_session = UserTradingSession.query.filter_by(
            telegram_user_id=str(chat_id)
        ).order_by(UserTradingSession.session_start.desc()).first()
        
        status_text = f"""📊 Detailed API Status

🏢 Exchange: {user_creds.exchange_name.title()}
🔑 API Key: ...{user_creds.get_api_key()[-8:]}
{"🧪 Testnet Mode" if user_creds.testnet_mode else "🚀 Live Trading"}
📅 Created: {user_creds.created_at.strftime('%Y-%m-%d %H:%M')}
🕒 Last Used: {user_creds.last_used.strftime('%Y-%m-%d %H:%M') if user_creds.last_used else 'Never'}

"""
        
        if recent_session:
            status_text += f"""📈 Recent Session:
• Total Trades: {recent_session.total_trades}
• Successful: {recent_session.successful_trades}
• Failed: {recent_session.failed_trades}
• API Calls: {recent_session.api_calls_made}
• API Errors: {recent_session.api_errors}
"""
        
        return status_text, get_main_menu()
    
    except Exception as e:
        logging.error(f"Error showing credentials status: {str(e)}")
        return "❌ Error accessing credentials. Please try again.", get_main_menu()

def handle_api_text_input(text, chat_id, user):
    """Handle text input during API setup process"""
    if chat_id not in user_api_setup_state:
        return "❌ No active API setup. Use /api to start.", get_main_menu()
    
    state = user_api_setup_state[chat_id]
    step = state.get('step')
    exchange = state.get('exchange', 'toobit')
    
    try:
        # Get or create user credentials
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(chat_id)).first()
        if not user_creds:
            user_creds = UserCredentials()
            user_creds.telegram_user_id = str(chat_id)
            user_creds.telegram_username = user.get('username')
            user_creds.exchange_name = exchange
            db.session.add(user_creds)
        
        if step == 'api_key':
            # Validate API key format (basic check)
            if len(text.strip()) < 10:
                return "❌ API key seems too short. Please enter a valid API key:", None
            
            user_creds.set_api_key(text.strip())
            state['step'] = 'api_secret'
            
            return "✅ API key saved securely!\n\n🔐 Now enter your API Secret:", None
        
        elif step == 'api_secret':
            # Validate API secret format
            if len(text.strip()) < 10:
                return "❌ API secret seems too short. Please enter a valid API secret:", None
            
            user_creds.set_api_secret(text.strip())
            
            # Check if exchange needs passphrase
            if exchange.lower() in ['okx', 'okex', 'kucoin']:
                state['step'] = 'passphrase'
                return "✅ API secret saved securely!\n\n🔑 Enter your passphrase (if any, or type 'none'):", None
            else:
                # Save and complete setup
                db.session.commit()
                del user_api_setup_state[chat_id]
                
                return f"""✅ API credentials setup complete!

🏢 Exchange: {exchange.title()}
🔑 API Key: ...{user_creds.get_api_key()[-4:]}
🧪 Mode: Testnet (Safe for testing)

Your credentials are encrypted and stored securely. You can now use live trading features!""", get_main_menu()
        
        elif step == 'passphrase':
            if text.strip().lower() != 'none':
                user_creds.set_passphrase(text.strip())
            
            # Save and complete setup
            db.session.commit()
            del user_api_setup_state[chat_id]
            
            return f"""✅ API credentials setup complete!

🏢 Exchange: {exchange.title()}
🔑 API Key: ...{user_creds.get_api_key()[-4:]}
🧪 Mode: Testnet (Safe for testing)

Your credentials are encrypted and stored securely. You can now use live trading features!""", get_main_menu()
    
    except Exception as e:
        logging.error(f"Error handling API text input: {str(e)}")
        if chat_id in user_api_setup_state:
            del user_api_setup_state[chat_id]
        return "❌ Error saving credentials. Please try again with /api", get_main_menu()
    
    return "❌ Invalid step in API setup. Please restart with /api", get_main_menu()

def start_api_setup(chat_id, user, exchange):
    """Start API credentials setup process"""
    try:
        # Initialize user state for API setup
        user_api_setup_state[chat_id] = {
            'step': 'api_key',
            'exchange': exchange.lower()
        }
        
        exchange_name = exchange.title()
        return f"""🔑 Setting up {exchange_name} API Credentials

🔐 For security, your API credentials will be encrypted and stored safely.

⚠️ **IMPORTANT SECURITY TIPS:**
• Use API keys with ONLY trading permissions
• Never share your API secret with anyone
• Enable IP whitelist if possible
• Start with testnet for testing

📝 Please enter your {exchange_name} API Key:""", None
    
    except Exception as e:
        logging.error(f"Error starting API setup: {str(e)}")
        return "❌ Error starting API setup. Please try again.", get_main_menu()

def start_api_update(chat_id, user):
    """Start updating existing API credentials"""
    try:
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(chat_id)).first()
        if not user_creds or not user_creds.has_credentials():
            return "❌ No existing credentials found. Use setup instead.", get_api_management_menu(False)
        
        # Start update process
        user_api_setup_state[chat_id] = {
            'step': 'api_key',
            'exchange': user_creds.exchange_name,
            'updating': True
        }
        
        return f"""🔄 Updating {user_creds.exchange_name.title()} API Credentials

Current API Key: ...{user_creds.get_api_key()[-4:] if user_creds.get_api_key() else 'N/A'}

📝 Enter your new API Key:""", None
    
    except Exception as e:
        logging.error(f"Error starting API update: {str(e)}")
        return "❌ Error starting update. Please try again.", get_main_menu()

def toggle_api_mode(chat_id, user):
    """Toggle between testnet and live trading mode"""
    try:
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(chat_id)).first()
        if not user_creds or not user_creds.has_credentials():
            return "❌ No API credentials found. Set up credentials first.", get_api_management_menu(False)
        
        # Toggle mode
        user_creds.testnet_mode = not user_creds.testnet_mode
        db.session.commit()
        
        mode = "🧪 Testnet (Safe for testing)" if user_creds.testnet_mode else "🚀 Live Trading (Real money)"
        
        return f"""✅ Trading mode updated!

Current Mode: {mode}

{"⚠️ You are now in LIVE TRADING mode. Real money will be used!" if not user_creds.testnet_mode else "✅ Safe testing mode enabled."}""", get_api_management_menu(True)
    
    except Exception as e:
        logging.error(f"Error toggling API mode: {str(e)}")
        return "❌ Error updating mode. Please try again.", get_main_menu()

def delete_user_credentials(chat_id, user):
    """Delete user's API credentials"""
    try:
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(chat_id)).first()
        if not user_creds:
            return "❌ No credentials found to delete.", get_main_menu()
        
        # Delete credentials
        db.session.delete(user_creds)
        db.session.commit()
        
        # Clean up any active API setup state
        if chat_id in user_api_setup_state:
            del user_api_setup_state[chat_id]
        
        return """✅ API credentials deleted successfully!

🔐 All your encrypted credentials have been securely removed from our system.

You can add new credentials anytime using the setup option.""", get_api_management_menu(False)
    
    except Exception as e:
        logging.error(f"Error deleting credentials: {str(e)}")
        return "❌ Error deleting credentials. Please try again.", get_main_menu()

def get_live_market_price(symbol):
    """Get current market price for a symbol with multi-source fallback"""
    # First try Binance API
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
        
        price = float(data['price'])
        logging.info(f"Retrieved live price for {symbol}: ${price}")
        return price
    except Exception as e:
        logging.warning(f"Binance API failed for {symbol}: {str(e)}")
    
    # Try CoinGecko API as fallback (for non-Binance pairs)
    try:
        # Map symbol to CoinGecko ID
        symbol_map = {
            'BTCUSDT': 'bitcoin',
            'ETHUSDT': 'ethereum', 
            'BNBUSDT': 'binancecoin',
            'ADAUSDT': 'cardano',
            'DOGEUSDT': 'dogecoin',
            'SOLUSDT': 'solana',
            'DOTUSDT': 'polkadot',
            'LINKUSDT': 'chainlink',
            'LTCUSDT': 'litecoin',
            'MATICUSDT': 'matic-network',
            'AVAXUSDT': 'avalanche-2',
            'UNIUSDT': 'uniswap',
            'XRPUSDT': 'ripple'
        }
        
        coin_id = symbol_map.get(symbol)
        if coin_id:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
            
            price = float(data[coin_id]['usd'])
            logging.info(f"Retrieved live price from CoinGecko for {symbol}: ${price}")
            return price
    except Exception as e:
        logging.warning(f"CoinGecko API failed for {symbol}: {str(e)}")
    
    # Try CryptoCompare API as final fallback
    try:
        base_symbol = symbol.replace('USDT', '')
        url = f"https://min-api.cryptocompare.com/data/price?fsym={base_symbol}&tsyms=USD"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebObj/537.36')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
        
        if 'USD' in data:
            price = float(data['USD'])
            logging.info(f"Retrieved live price from CryptoCompare for {symbol}: ${price}")
            return price
    except Exception as e:
        logging.warning(f"CryptoCompare API failed for {symbol}: {str(e)}")
    
    # If all APIs fail, raise error
    raise Exception(f"Unable to fetch live market price for {symbol} from any source")

def update_all_positions_with_live_data():
    """Update all active positions with live market data for P&L calculation and check pending limit orders"""
    for user_id, trades in user_trade_configs.items():
        for trade_id, config in trades.items():
            if config.symbol:
                try:
                    # Always update current price with live data
                    config.current_price = get_live_market_price(config.symbol)
                    
                    # Check pending limit orders for execution
                    if config.status == "pending" and config.entry_type == "limit" and config.entry_price > 0:
                        should_execute = False
                        if config.side == "long":
                            if config.entry_price <= config.current_price:
                                # Long limit (buy limit): executes when market drops to or below limit price
                                should_execute = config.current_price <= config.entry_price
                            else:
                                # Long stop (buy stop): executes when market rises to or above stop price  
                                should_execute = config.current_price >= config.entry_price
                        elif config.side == "short":
                            if config.entry_price >= config.current_price:
                                # Short limit (sell limit): executes when market rises to or above limit price
                                should_execute = config.current_price >= config.entry_price
                            else:
                                # Short stop (sell stop): executes when market drops to or below stop price
                                should_execute = config.current_price <= config.entry_price
                        
                        if should_execute:
                            # Execute the pending limit order
                            config.status = "active"
                            config.position_margin = calculate_position_margin(config.amount, config.leverage)
                            config.position_value = config.amount * config.leverage
                            config.position_size = config.position_value / config.entry_price
                            config.unrealized_pnl = 0.0
                            
                            logging.info(f"Limit order executed: {config.symbol} {config.side} at ${config.entry_price} (market reached: ${config.current_price})")
                            
                            # Log trade execution
                            bot_trades.append({
                                'id': len(bot_trades) + 1,
                                'user_id': str(user_id),
                                'trade_id': trade_id,
                                'symbol': config.symbol,
                                'side': config.side,
                                'amount': config.amount,
                                'leverage': config.leverage,
                                'entry_price': config.entry_price,
                                'timestamp': datetime.utcnow().isoformat(),
                                'status': 'executed'
                            })
                            
                            bot_status['total_trades'] += 1
                    
                    # Recalculate P&L for active positions
                    if config.status == "active" and config.entry_price and config.current_price:
                        config.unrealized_pnl = calculate_unrealized_pnl(
                            config.entry_price, config.current_price,
                            config.amount, config.leverage, config.side
                        )
                        
                except Exception as e:
                    logging.warning(f"Failed to update live data for {config.symbol} (user {user_id}): {e}")
                    # Keep existing current_price as fallback



def calculate_position_margin(amount, leverage):
    """
    Calculate margin required for a position
    In futures trading: margin = position_value / leverage
    Where position_value = amount (the USDT amount to use for the position)
    """
    if leverage <= 0:
        leverage = 1
    # Amount IS the margin - this is what user puts up
    # Position value = margin * leverage
    return amount  # The amount user specifies IS the margin they want to use

def calculate_unrealized_pnl(entry_price, current_price, margin, leverage, side):
    """
    Calculate unrealized P&L for a leveraged position
    Leverage amplifies the percentage change, not the margin amount
    P&L = (price_change_percentage * leverage * margin)
    """
    if not entry_price or not current_price or not margin or entry_price <= 0:
        return 0.0
    
    # Calculate percentage price change
    price_change_percentage = (current_price - entry_price) / entry_price
    
    # For short positions, profit when price goes down
    if side == "short":
        price_change_percentage = -price_change_percentage
    
    # P&L = price change % * leverage * margin
    # Leverage amplifies the percentage move, applied to the margin put up
    return price_change_percentage * leverage * margin

def calculate_tp_sl_prices_and_amounts(config):
    """Calculate actual TP/SL prices and profit/loss amounts"""
    if not config.entry_price or config.entry_price <= 0:
        return {}
    
    result = {
        'take_profits': [],
        'stop_loss': {}
    }
    
    # Calculate actual margin used for this position
    actual_margin = calculate_position_margin(config.amount, config.leverage)
    
    # Calculate Take Profit levels
    for i, tp in enumerate(config.take_profits or []):
        tp_percentage = tp.get('percentage', 0) if isinstance(tp, dict) else tp
        allocation = tp.get('allocation', 100) if isinstance(tp, dict) else 100
        
        if tp_percentage > 0:
            # TP percentage is the desired profit on margin (what user risks), not price movement
            # For leveraged trading: required price movement = tp_percentage / leverage
            required_price_movement = tp_percentage / config.leverage / 100
            
            if config.side == "long":
                tp_price = config.entry_price * (1 + required_price_movement)
            else:  # short
                tp_price = config.entry_price * (1 - required_price_movement)
            
            # Profit amount = tp_percentage of margin (what user risks) * allocation
            # User risks $100 margin, 10% TP = $10 profit, not $100
            profit_amount = (tp_percentage / 100) * actual_margin * (allocation / 100)
            
            result['take_profits'].append({
                'level': i + 1,
                'percentage': tp_percentage,
                'allocation': allocation,
                'price': tp_price,
                'profit_amount': profit_amount
            })
    
    # Calculate Stop Loss
    if config.stop_loss_percent > 0:
        # SL percentage is the desired loss on margin (what user risks), not price movement
        # For leveraged trading: required price movement = sl_percentage / leverage
        required_price_movement = config.stop_loss_percent / config.leverage / 100
        
        if config.side == "long":
            sl_price = config.entry_price * (1 - required_price_movement)
        else:  # short
            sl_price = config.entry_price * (1 + required_price_movement)
        
        # Loss amount = sl_percentage of margin (what user risks)
        # User risks $100 margin, 10% SL = $10 loss, not $100
        loss_amount = (config.stop_loss_percent / 100) * actual_margin
        
        result['stop_loss'] = {
            'percentage': config.stop_loss_percent,
            'price': sl_price,
            'loss_amount': loss_amount
        }
    
    return result

def get_margin_summary(chat_id):
    """Get comprehensive margin summary for a user"""
    user_trades = user_trade_configs.get(chat_id, {})
    
    # Account totals - each user gets 1000 USDT trial fund
    account_balance = 1000.0  # Individual trial fund per user
    total_position_margin = 0.0
    total_unrealized_pnl = 0.0
    
    # Calculate totals from active positions
    for config in user_trades.values():
        if config.status == "active" and config.amount:
            # Update position data with current prices
            # Update current price with live market data for active positions
            if config.symbol:
                try:
                    config.current_price = get_live_market_price(config.symbol)
                except Exception as e:
                    logging.error(f"Failed to get live price for {config.symbol}: {e}")
                    config.current_price = config.entry_price  # Fallback to entry price
            config.position_margin = calculate_position_margin(config.amount, config.leverage)
            
            if config.entry_price and config.amount:
                # Calculate position details properly
                config.position_value = config.amount * config.leverage
                config.position_size = config.position_value / config.entry_price
                config.unrealized_pnl = calculate_unrealized_pnl(
                    config.entry_price, config.current_price, 
                    config.amount, config.leverage, config.side
                )
            
            total_position_margin += config.position_margin
            total_unrealized_pnl += config.unrealized_pnl
    
    # Include unrealized P&L in account balance to reflect current account value
    account_balance = 1000.0 + total_unrealized_pnl
    free_margin = account_balance - total_position_margin
    
    return {
        'account_balance': account_balance,
        'total_margin': total_position_margin,
        'free_margin': free_margin,
        'unrealized_pnl': total_unrealized_pnl,
        'margin_level': (account_balance + total_unrealized_pnl) / total_position_margin * 100 if total_position_margin > 0 else 0
    }

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

def get_current_trade_config(chat_id):
    """Get the current trade configuration for a user"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            return user_trade_configs[chat_id][trade_id]
    return None

def get_main_menu():
    """Get main menu keyboard"""
    return {
        "inline_keyboard": [
            [{"text": "🔄 Positions Manager", "callback_data": "menu_positions"}],
            [{"text": "📊 Trading", "callback_data": "menu_trading"}],
            [{"text": "💼 Portfolio & Analytics", "callback_data": "menu_portfolio"}],
            [{"text": "🔑 API Credentials", "callback_data": "api_menu"}],
            [{"text": "📈 Quick Price Check", "callback_data": "quick_price"}]
        ]
    }

def get_api_management_menu(has_credentials=False):
    """Get API credentials management menu"""
    if has_credentials:
        return {
            'inline_keyboard': [
                [{'text': '🔄 Update Credentials', 'callback_data': 'api_update'}],
                [{'text': '🧪 Toggle Test/Live Mode', 'callback_data': 'api_toggle_mode'}],
                [{'text': '📊 View Status', 'callback_data': 'api_status'}],
                [{'text': '🗑️ Delete Credentials', 'callback_data': 'api_delete'}],
                [{'text': '⬅️ Back to Main Menu', 'callback_data': 'main_menu'}]
            ]
        }
    else:
        return {
            'inline_keyboard': [
                [{'text': '🔑 Add Toobit Credentials', 'callback_data': 'api_setup_toobit'}],
                [{'text': '🔑 Add Binance Credentials', 'callback_data': 'api_setup_binance'}],
                [{'text': '🔑 Add OKX Credentials', 'callback_data': 'api_setup_okx'}],
                [{'text': '⬅️ Back to Main Menu', 'callback_data': 'main_menu'}]
            ]
        }

def get_positions_menu(user_id):
    """Get positions management menu"""
    user_trades = user_trade_configs.get(user_id, {})
    
    keyboard = [
        [{"text": "📋 View All Positions", "callback_data": "positions_list"}],
        [{"text": "➕ Create New Position", "callback_data": "positions_new"}],
    ]
    
    if user_trades:
        keyboard.extend([
            [{"text": "🎯 Select Position", "callback_data": "positions_select"}],
            [{"text": "🚀 Start Selected Position", "callback_data": "positions_start"}],
            [{"text": "⏹️ Stop All Positions", "callback_data": "positions_stop_all"}],
        ])
    
    keyboard.extend([
        [{"text": "📊 Positions Status", "callback_data": "positions_status"}],
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
        [{"text": "📊 Set Leverage", "callback_data": "set_leverage"},
         {"text": "💰 Set Amount", "callback_data": "set_amount"}],
        [{"text": "🎯 Set Entry Price", "callback_data": "set_entry"},
         {"text": "🎯 Set Take Profits", "callback_data": "set_takeprofit"}],
        [{"text": "🛑 Set Stop Loss", "callback_data": "set_stoploss"},
         {"text": "⚖️ Break-even Settings", "callback_data": "set_breakeven"}],
        [{"text": "📈 Trailing Stop", "callback_data": "set_trailstop"}],
    ]
    
    # Add trade execution button if config is complete
    if config and config.is_complete():
        keyboard.append([{"text": "🚀 Execute Trade", "callback_data": "execute_trade"}])
    
    keyboard.append([{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}])
    return {"inline_keyboard": keyboard}



def get_portfolio_menu():
    """Get portfolio menu keyboard"""
    return {
        "inline_keyboard": [
            [{"text": "📊 Portfolio & Margin Overview", "callback_data": "portfolio_overview"}],
            [{"text": "📈 Recent Trades", "callback_data": "recent_trades"}],
            [{"text": "💹 Performance Analytics", "callback_data": "performance"}],
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
        status_emoji = {
            "active": "🟢",
            "pending": "🔵",
            "configured": "🟡", 
            "stopped": "🔴"
        }.get(config.status, "⚪")
        button_text = f"{status_emoji} {config.get_display_name()}"
        keyboard.append([{"text": button_text, "callback_data": f"select_position_{trade_id}"}])
    
    keyboard.append([{"text": "🏠 Back to Positions", "callback_data": "menu_positions"}])
    return {"inline_keyboard": keyboard}

def get_trade_actions_menu(trade_id):
    """Get actions menu for a specific trade"""
    return {
        "inline_keyboard": [
            [{"text": "✏️ Edit Trade", "callback_data": f"edit_trade_{trade_id}"}],
            [{"text": "🚀 Start Trade", "callback_data": f"start_trade_{trade_id}"}],
            [{"text": "⏹️ Stop Trade", "callback_data": f"stop_trade_{trade_id}"}],
            [{"text": "🗑️ Delete Trade", "callback_data": f"delete_trade_{trade_id}"}],
            [{"text": "🏠 Back to List", "callback_data": "positions_list"}]
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
            config = get_current_trade_config(chat_id)
            if config:
                header = config.get_trade_header("Trading Menu")
                return f"{header}📊 Trading Menu:", get_trading_menu(chat_id)
            else:
                return "📊 Trading Menu:\n\nNo trade selected. Please create or select a trade first.", get_trading_menu(chat_id)
        elif callback_data == "menu_portfolio":
            return "💼 Portfolio & Analytics:", get_portfolio_menu()
        elif callback_data == "select_pair":
            return "💱 Select a trading pair:", get_pairs_menu()
        
        # API credentials management callbacks
        elif callback_data.startswith("api_setup_"):
            exchange = callback_data.replace("api_setup_", "")
            return start_api_setup(chat_id, user, exchange)
        elif callback_data == "api_update":
            return start_api_update(chat_id, user)
        elif callback_data == "api_toggle_mode":
            return toggle_api_mode(chat_id, user)
        elif callback_data == "api_status":
            return show_credentials_status(chat_id, user)
        elif callback_data == "api_delete":
            return delete_user_credentials(chat_id, user)
        elif callback_data == "api_menu":
            return show_api_menu(chat_id, user)

        
        # Trading pair selection
        elif callback_data.startswith("pair_"):
            pair = callback_data.replace("pair_", "").replace("_", "/")
            symbol = pair.replace("/", "")
            try:
                price = get_live_market_price(symbol)
            except Exception as e:
                logging.error(f"Error fetching live price for {symbol}: {e}")
                return f"❌ Could not fetch live price for {pair}. Please try again.", get_pairs_menu()
            
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
        
        # Portfolio handlers - Unified Portfolio & Margin Overview
        elif callback_data == "portfolio_overview":
            # Update all positions with live market data before displaying
            update_all_positions_with_live_data()
            
            user_trades = user_trade_configs.get(chat_id, {})
            margin_data = get_margin_summary(chat_id)
            
            response = "📊 **PORTFOLIO & MARGIN OVERVIEW**\n"
            response += "=" * 40 + "\n\n"
            
            # Account Summary - Comprehensive View
            response += "💼 **ACCOUNT SUMMARY**\n"
            response += f"Account Balance: ${margin_data['account_balance']:,.2f}\n"
            response += f"Total Margin Used: ${margin_data['total_margin']:,.2f}\n"
            response += f"Free Margin: ${margin_data['free_margin']:,.2f}\n"
            response += f"Floating P&L: ${margin_data['unrealized_pnl']:+,.2f}\n"
            
            if margin_data['margin_level'] > 0:
                response += f"Margin Level: {margin_data['margin_level']:.1f}%\n"
            else:
                response += f"Margin Level: ∞ (No positions)\n"
            response += "\n"
            
            # Risk Assessment
            response += "⚠️ **RISK ASSESSMENT**\n"
            if margin_data['total_margin'] > 0:
                margin_ratio = margin_data['total_margin'] / margin_data['account_balance'] * 100
                response += f"Margin Utilization: {margin_ratio:.1f}%\n"
                
                if margin_ratio > 80:
                    response += "Risk Level: 🔴 HIGH RISK - Consider reducing positions\n"
                elif margin_ratio > 50:
                    response += "Risk Level: 🟡 MEDIUM RISK - Monitor closely\n"
                else:
                    response += "Risk Level: 🟢 LOW RISK - Safe margin levels\n"
            else:
                response += "Risk Level: 🟢 MINIMAL (No active positions)\n"
            response += "\n"
            
            # Holdings & Position Details
            active_positions = [config for config in user_trades.values() if config.status == "active"]
            configured_positions = [config for config in user_trades.values() if config.status == "configured"]
            closed_positions = [config for config in user_trades.values() if config.status == "stopped"]
            
            response += "📊 **ACTIVE POSITIONS**\n"
            if active_positions:
                total_value = sum(config.amount or 0 for config in active_positions)
                response += f"Count: {len(active_positions)} | Total Value: ${total_value:,.2f}\n"
                response += "-" * 35 + "\n"
                
                for config in active_positions:
                    if config.symbol and config.amount:
                        pnl_emoji = "🟢" if config.unrealized_pnl >= 0 else "🔴"
                        response += f"{pnl_emoji} {config.symbol} {config.side.upper()}\n"
                        response += f"   Amount: ${config.amount:,.2f} | Leverage: {config.leverage}x\n"
                        response += f"   Margin Used: ${config.position_margin:,.2f}\n"
                        response += f"   Entry: ${config.entry_price or 0:.4f} | Current: ${config.current_price:.4f}\n"
                        response += f"   P&L: ${config.unrealized_pnl:+,.2f}\n\n"
            else:
                response += "No active positions\n\n"
            
            # Configured Positions Summary
            if configured_positions:
                response += "📋 **CONFIGURED POSITIONS**\n"
                response += f"Ready to Execute: {len(configured_positions)}\n"
                for config in configured_positions:
                    if config.symbol:
                        response += f"• {config.symbol} {config.side or 'N/A'}: ${config.amount or 0:,.2f}\n"
                response += "\n"
            
            # Closed Positions History
            if closed_positions:
                response += "📚 **CLOSED POSITIONS HISTORY**\n"
                response += f"Total Closed: {len(closed_positions)}\n"
                response += "-" * 35 + "\n"
                
                for config in closed_positions[-5:]:  # Show last 5 closed positions
                    if config.symbol and config.amount:
                        # Get final PnL from bot_trades
                        closed_trade = next((trade for trade in bot_trades if trade.get('trade_id') == config.trade_id and trade.get('user_id') == str(chat_id)), None)
                        final_pnl = closed_trade.get('final_pnl', 0) if closed_trade else 0
                        pnl_emoji = "🟢" if final_pnl >= 0 else "🔴"
                        
                        response += f"{pnl_emoji} {config.symbol} {config.side.upper()}\n"
                        response += f"   Amount: ${config.amount:,.2f} | Leverage: {config.leverage}x\n"
                        response += f"   Entry: ${config.entry_price or 0:.4f}\n"
                        response += f"   Final P&L: ${final_pnl:+,.2f}\n"
                        
                        # Add timestamp if available
                        if closed_trade and 'timestamp' in closed_trade:
                            timestamp = datetime.fromisoformat(closed_trade['timestamp'])
                            response += f"   Closed: {timestamp.strftime('%Y-%m-%d %H:%M')}\n"
                        response += "\n"
                
                if len(closed_positions) > 5:
                    response += f"... and {len(closed_positions) - 5} more closed positions\n\n"
            else:
                response += "📚 **CLOSED POSITIONS HISTORY**\n"
                response += "No closed positions yet\n\n"
            
            # Portfolio Statistics
            all_positions = len(user_trades)
            if all_positions > 0:
                response += "📈 **PORTFOLIO STATISTICS**\n"
                response += f"Total Positions: {all_positions} | Active: {len(active_positions)} | Configured: {len(configured_positions)}\n"
                
                # Calculate portfolio diversity
                symbols = set(config.symbol for config in user_trades.values() if config.symbol)
                response += f"Unique Symbols: {len(symbols)}\n"
                
                # Symbol breakdown for active positions
                if active_positions:
                    symbol_breakdown = {}
                    for config in active_positions:
                        if config.symbol:
                            if config.symbol not in symbol_breakdown:
                                symbol_breakdown[config.symbol] = 0
                            symbol_breakdown[config.symbol] += 1
                    
                    if len(symbol_breakdown) > 1:
                        response += "Symbol Distribution: "
                        response += " | ".join([f"{sym}({count})" for sym, count in sorted(symbol_breakdown.items())])
                        response += "\n"
            
            return response, get_portfolio_menu()
        elif callback_data == "recent_trades":
            user_trades = user_trade_configs.get(chat_id, {})
            executed_trades = [t for t in bot_trades if t['user_id'] == str(user.get('id', 'unknown'))]
            
            response = "📈 **RECENT TRADING ACTIVITY**\n"
            response += "=" * 35 + "\n\n"
            
            # Show executed trades from bot_trades
            if executed_trades:
                response += "✅ **EXECUTED TRADES**\n"
                for trade in executed_trades[-5:]:  # Last 5 executed
                    status_emoji = "✅" if trade['status'] == "executed" else "⏳"
                    response += f"{status_emoji} {trade['action'].upper()} {trade['symbol']}\n"
                    response += f"   Quantity: {trade['quantity']:.4f}\n"
                    response += f"   Price: ${trade['price']:.4f}\n"
                    if 'leverage' in trade:
                        response += f"   Leverage: {trade['leverage']}x\n"
                    timestamp = datetime.fromisoformat(trade['timestamp'])
                    response += f"   Time: {timestamp.strftime('%Y-%m-%d %H:%M')}\n\n"
            
            # Show current position status
            if user_trades:
                response += "📊 **CURRENT POSITIONS**\n"
                active_positions = [config for config in user_trades.values() if config.status == "active"]
                configured_positions = [config for config in user_trades.values() if config.status == "configured"]
                
                if active_positions:
                    response += f"🟢 Active ({len(active_positions)}):\n"
                    for config in active_positions:
                        if config.symbol:
                            pnl_info = ""
                            if hasattr(config, 'unrealized_pnl') and config.unrealized_pnl != 0:
                                pnl_emoji = "📈" if config.unrealized_pnl >= 0 else "📉"
                                pnl_info = f" {pnl_emoji} ${config.unrealized_pnl:+.2f}"
                            response += f"   • {config.symbol} {config.side.upper()}: ${config.amount or 0:,.2f}{pnl_info}\n"
                    response += "\n"
                
                if configured_positions:
                    response += f"🟡 Ready to Execute ({len(configured_positions)}):\n"
                    for config in configured_positions:
                        if config.symbol:
                            response += f"   • {config.symbol} {config.side or 'N/A'}: ${config.amount or 0:,.2f}\n"
                    response += "\n"
            
            # Trading summary
            total_executed = len(executed_trades)
            total_positions = len(user_trades)
            
            response += "📋 **TRADING SUMMARY**\n"
            response += f"Total Executed Trades: {total_executed}\n"
            response += f"Total Positions Created: {total_positions}\n"
            
            if total_executed == 0 and total_positions == 0:
                response += "\n💡 No trading activity yet. Create your first position to get started!"
            
            return response, get_portfolio_menu()
        elif callback_data == "performance":
            user_trades = user_trade_configs.get(chat_id, {})
            executed_trades = [t for t in bot_trades if t['user_id'] == str(user.get('id', 'unknown'))]
            margin_data = get_margin_summary(chat_id)
            
            response = "💹 **PERFORMANCE ANALYTICS**\n"
            response += "=" * 35 + "\n\n"
            
            # Trading Activity
            response += "📊 **TRADING ACTIVITY**\n"
            response += f"Total Positions Created: {len(user_trades)}\n"
            response += f"Executed Trades: {len(executed_trades)}\n"
            
            active_count = sum(1 for config in user_trades.values() if config.status == "active")
            response += f"Active Positions: {active_count}\n\n"
            
            # P&L Analysis
            response += "💰 **P&L ANALYSIS**\n"
            total_unrealized = margin_data['unrealized_pnl']
            response += f"Current Floating P&L: ${total_unrealized:+,.2f}\n"
            
            # Calculate realized P&L from executed trades (simplified)
            realized_pnl = 0.0  # In a real system, this would track closed positions
            response += f"Total Realized P&L: ${realized_pnl:+,.2f}\n"
            response += f"Total P&L: ${total_unrealized + realized_pnl:+,.2f}\n\n"
            
            # Position Analysis
            if user_trades:
                response += "📈 **POSITION ANALYSIS**\n"
                
                # Analyze by side (long/short)
                long_positions = [c for c in user_trades.values() if c.side == "long" and c.status == "active"]
                short_positions = [c for c in user_trades.values() if c.side == "short" and c.status == "active"]
                
                response += f"Long Positions: {len(long_positions)}\n"
                response += f"Short Positions: {len(short_positions)}\n"
                
                # Analyze by symbol
                symbols = {}
                for config in user_trades.values():
                    if config.symbol and config.status == "active":
                        if config.symbol not in symbols:
                            symbols[config.symbol] = 0
                        symbols[config.symbol] += 1
                
                if symbols:
                    response += f"\n🎯 **SYMBOL BREAKDOWN**\n"
                    for symbol, count in sorted(symbols.items()):
                        response += f"{symbol}: {count} position(s)\n"
                
                # Risk Analysis
                response += f"\n⚠️ **RISK METRICS**\n"
                if margin_data['total_margin'] > 0:
                    utilization = margin_data['total_margin'] / margin_data['account_balance'] * 100
                    response += f"Margin Utilization: {utilization:.1f}%\n"
                    
                    if utilization > 80:
                        response += "Risk Level: 🔴 HIGH\n"
                    elif utilization > 50:
                        response += "Risk Level: 🟡 MEDIUM\n"
                    else:
                        response += "Risk Level: 🟢 LOW\n"
                else:
                    response += "Risk Level: 🟢 MINIMAL (No active positions)\n"
                    
                # Performance Score (simplified calculation)
                if total_unrealized >= 0:
                    performance_emoji = "📈"
                    performance_status = "POSITIVE"
                else:
                    performance_emoji = "📉"
                    performance_status = "NEGATIVE"
                
                response += f"\n{performance_emoji} **OVERALL PERFORMANCE**\n"
                response += f"Current Trend: {performance_status}\n"
                
            else:
                response += "📊 No positions created yet.\n"
                response += "Start trading to see detailed performance metrics!\n"
            
            return response, get_portfolio_menu()
        
        # Quick price check
        elif callback_data == "quick_price":
            response = "💰 Live Price Check:\n\n"
            symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT"]
            for symbol in symbols:
                try:
                    price = get_live_market_price(symbol)
                    response += f"{symbol}: ${price:.4f}\n"
                except Exception as e:
                    logging.error(f"Error fetching live price for {symbol}: {e}")
                    response += f"{symbol}: ❌ Price unavailable\n"
            
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔄 Refresh Prices", "callback_data": "quick_price"}],
                    [{"text": "💱 Select Pair for Trading", "callback_data": "select_pair"}],
                    [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
                ]
            }
            return response, keyboard
        
        # Multi-trade management handlers
        elif callback_data == "menu_positions":
            user_trades = user_trade_configs.get(chat_id, {})
            summary = f"🔄 Positions Manager\n\n"
            summary += f"Total Positions: {len(user_trades)}\n"
            if user_trades:
                active_count = sum(1 for config in user_trades.values() if config.status == "active")
                pending_count = sum(1 for config in user_trades.values() if config.status == "pending")
                summary += f"Active: {active_count}\n"
                if pending_count > 0:
                    summary += f"Pending: {pending_count}\n"
                if chat_id in user_selected_trade:
                    selected_trade = user_trade_configs[chat_id].get(user_selected_trade[chat_id])
                    if selected_trade:
                        summary += f"Selected: {selected_trade.get_display_name()}\n"
            return summary, get_positions_menu(chat_id)
            

            
        # Multi-trade specific handlers
        elif callback_data == "positions_new":
            global trade_counter
            trade_counter += 1
            trade_id = f"trade_{trade_counter}"
            
            if chat_id not in user_trade_configs:
                user_trade_configs[chat_id] = {}
            
            new_trade = TradeConfig(trade_id, f"Position #{trade_counter}")
            user_trade_configs[chat_id][trade_id] = new_trade
            user_selected_trade[chat_id] = trade_id
            
            return f"✅ Created new position: {new_trade.get_display_name()}", get_positions_menu(chat_id)
            
        elif callback_data == "positions_list":
            user_trades = user_trade_configs.get(chat_id, {})
            if not user_trades:
                return "📋 No positions configured yet.", get_positions_menu(chat_id)
            
            response = "📋 Your Position Configurations:\n\n"
            for trade_id, config in user_trades.items():
                status_emoji = {
                    "active": "🟢",
                    "pending": "🔵", 
                    "configured": "🟡",
                    "stopped": "🔴"
                }.get(config.status, "⚪")
                response += f"{status_emoji} {config.get_display_name()}\n"
                response += f"   {config.symbol or 'No symbol'} | {config.side or 'No side'}\n\n"
            
            keyboard = {"inline_keyboard": []}
            for trade_id, config in list(user_trades.items())[:5]:  # Show first 5 positions
                status_emoji = {
                    "active": "🟢",
                    "pending": "🔵",
                    "configured": "🟡", 
                    "stopped": "🔴"
                }.get(config.status, "⚪")
                button_text = f"{status_emoji} {config.name}"
                keyboard["inline_keyboard"].append([{"text": button_text, "callback_data": f"select_position_{trade_id}"}])
            
            keyboard["inline_keyboard"].append([{"text": "🏠 Back to Positions", "callback_data": "menu_positions"}])
            return response, keyboard
            
        elif callback_data == "positions_select":
            return "🎯 Select a position to configure:", get_trade_selection_menu(chat_id)
            
        elif callback_data.startswith("select_position_"):
            trade_id = callback_data.replace("select_position_", "")
            if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                user_selected_trade[chat_id] = trade_id
                config = user_trade_configs[chat_id][trade_id]
                response = f"✅ Selected Position: {config.get_display_name()}\n\n{config.get_config_summary()}"
                return response, get_trade_actions_menu(trade_id)
            return "❌ Position not found.", get_positions_menu(chat_id)
            
        elif callback_data == "positions_start":
            if chat_id not in user_selected_trade:
                return "❌ No position selected. Please select a position first.", get_positions_menu(chat_id)
                
            trade_id = user_selected_trade[chat_id]
            config = user_trade_configs[chat_id][trade_id]
            
            if not config.is_complete():
                return "❌ Position configuration incomplete. Please set symbol, side, and amount.", get_positions_menu(chat_id)
                
            config.status = "active"
            return f"🚀 Started position: {config.get_display_name()}", get_positions_menu(chat_id)
            
        elif callback_data == "positions_stop_all":
            user_trades = user_trade_configs.get(chat_id, {})
            stopped_count = 0
            for config in user_trades.values():
                if config.status == "active":
                    config.status = "stopped"
                    stopped_count += 1
            return f"⏹️ Stopped {stopped_count} active positions.", get_positions_menu(chat_id)
            
        elif callback_data == "positions_status":
            user_trades = user_trade_configs.get(chat_id, {})
            if not user_trades:
                return "📊 No positions to show status for.", get_positions_menu(chat_id)
                
            response = "📊 Positions Status:\n\n"
            for config in user_trades.values():
                status_emoji = "🟢" if config.status == "active" else "🟡" if config.status == "configured" else "🔴"
                response += f"{status_emoji} {config.get_display_name()}\n"
                response += f"   Status: {config.status.title()}\n"
                if config.symbol:
                    response += f"   {config.symbol} {config.side or 'N/A'}\n"
                response += "\n"
            
            return response, get_positions_menu(chat_id)
        
        # Configuration handlers
        elif callback_data == "set_breakeven":
            config = get_current_trade_config(chat_id)
            header = config.get_trade_header("Break-even Settings") if config else ""
            return f"{header}⚖️ Break-even Settings\n\nChoose when to move stop loss to break-even:", get_breakeven_menu()
        elif callback_data.startswith("breakeven_"):
            breakeven_mode = callback_data.replace("breakeven_", "")
            return handle_set_breakeven(chat_id, breakeven_mode)
        elif callback_data == "set_trailstop":
            config = get_current_trade_config(chat_id)
            header = config.get_trade_header("Trailing Stop") if config else ""
            return f"{header}📈 Trailing Stop Settings\n\nConfigure your trailing stop:", get_trailing_stop_menu()
        elif callback_data == "trail_set_percent":
            return handle_trail_percent_request(chat_id)
        elif callback_data == "trail_set_activation":
            return handle_trail_activation_request(chat_id)
        elif callback_data == "trail_disable":
            return handle_trailing_stop_disable(chat_id)


            
        # Trading configuration handlers
        elif callback_data == "set_side_long":
            return handle_set_side(chat_id, "long")
        elif callback_data == "set_side_short":
            return handle_set_side(chat_id, "short")
        elif callback_data == "set_leverage":
            config = get_current_trade_config(chat_id)
            header = config.get_trade_header("Set Leverage") if config else ""
            return f"{header}📊 Select leverage for this trade:", get_leverage_menu()
        elif callback_data.startswith("leverage_"):
            leverage = int(callback_data.replace("leverage_", ""))
            return handle_set_leverage_wizard(chat_id, leverage)
        elif callback_data == "set_amount":
            config = get_current_trade_config(chat_id)
            header = config.get_trade_header("Set Amount") if config else ""
            return f"{header}💰 Set the trade amount (e.g., 100 USDT)\n\nPlease type the amount you want to trade.", get_trading_menu(chat_id)
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
                    header = config.get_trade_header("Set Take Profits")
                    return f"{header}🎯 Take Profit Setup\n\nFirst, set your take profit percentages.\nEnter percentage for TP1 (e.g., 10 for 10% profit):", get_tp_percentage_input_menu()
            return "❌ No trade selected.", get_trading_menu(chat_id)
        elif callback_data == "set_stoploss":
            config = get_current_trade_config(chat_id)
            header = config.get_trade_header("Set Stop Loss") if config else ""
            return f"{header}🛑 Stop Loss Settings\n\nSet your stop loss percentage (e.g., 5 for 5%):", get_stoploss_menu()
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
        
        elif callback_data == "tp_reset_all_alloc":
            if chat_id in user_selected_trade:
                trade_id = user_selected_trade[chat_id]
                if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                    config = user_trade_configs[chat_id][trade_id]
                    for tp in config.take_profits:
                        tp["allocation"] = None
                    return "🔄 Reset all allocations\n\n📊 Set allocation for TP1:", get_tp_allocation_menu(chat_id)
            return "❌ No trade selected.", get_trading_menu(chat_id)
        
        elif callback_data == "tp_reset_last_alloc":
            if chat_id in user_selected_trade:
                trade_id = user_selected_trade[chat_id]
                if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                    config = user_trade_configs[chat_id][trade_id]
                    # Find the last TP with an allocation and reset it
                    for tp in reversed(config.take_profits):
                        if tp["allocation"] is not None:
                            tp["allocation"] = None
                            tp_num = config.take_profits.index(tp) + 1
                            return f"🔄 Reset TP{tp_num} allocation\n\n📊 Set allocation for TP{tp_num}:", get_tp_allocation_menu(chat_id)
                    return "❌ No allocations to reset.", get_tp_allocation_menu(chat_id)
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
            return handle_set_entry_price(chat_id, "limit")
        
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
            [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
        ]
    }

def get_trailing_stop_menu():
    """Get trailing stop configuration menu - Clean implementation"""
    return {
        "inline_keyboard": [
            [{"text": "📉 Set Trail Percentage", "callback_data": "trail_set_percent"}],
            [{"text": "🎯 Set Activation Price", "callback_data": "trail_set_activation"}], 
            [{"text": "❌ Disable Trailing Stop", "callback_data": "trail_disable"}],
            [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
        ]
    }



def handle_set_side(chat_id, side):
    """Handle setting trade side (long/short)"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            config.side = side
            header = config.get_trade_header("Side Set")
            return f"{header}✅ Set position to {side.upper()}", get_trading_menu(chat_id)
    return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)

def handle_set_leverage(chat_id, leverage):
    """Handle setting leverage"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            config.leverage = leverage
            header = config.get_trade_header("Leverage Set")
            return f"{header}✅ Set leverage to {leverage}x", get_trading_menu(chat_id)
    return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)

def handle_execute_trade(chat_id, user):
    """Handle trade execution"""
    if chat_id not in user_selected_trade:
        return "❌ No trade selected.", get_trading_menu(chat_id)
        
    trade_id = user_selected_trade[chat_id]
    config = user_trade_configs[chat_id][trade_id]
    
    if not config.is_complete():
        return "❌ Trade configuration incomplete. Please set symbol, side, and amount.", get_trading_menu(chat_id)
    
    # Determine execution price based on order type
    logging.info(f"Executing trade: entry_type={config.entry_type}, entry_price={config.entry_price}")
    
    if config.entry_type == "limit" and config.entry_price:
        # For limit orders, use the specified limit price
        price = config.entry_price
        order_type = "LIMIT"
        logging.info(f"Using LIMIT order with price: ${price}")
    else:
        # For market orders, use current market price
        price = get_live_market_price(config.symbol)
        order_type = "MARKET"
        logging.info(f"Using MARKET order with price: ${price}")
        
    if price:
        trade = {
            'id': len(bot_trades) + 1,
            'user_id': str(user.get('id', 'unknown')),
            'symbol': config.symbol,
            'action': config.side,
            'quantity': config.amount / price if config.amount else 0.001,
            'price': price,
            'leverage': config.leverage,
            'order_type': order_type,
            'status': 'executed',
            'timestamp': datetime.utcnow().isoformat()
        }
        bot_trades.append(trade)
        bot_status['total_trades'] += 1
        config.status = "active"
        
        response = f"🚀 {order_type} Order Executed!\n\n"
        response += f"Symbol: {config.symbol}\n"
        response += f"Side: {config.side.upper()}\n"
        response += f"Amount: {config.amount} USDT\n"
        response += f"Leverage: {config.leverage}x\n"
        response += f"Entry Price: ${price:.4f}\n"
        response += f"Order Type: {order_type}\n"
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
            return f"🚀 Started position: {config.get_display_name()}", get_trade_actions_menu(trade_id)
        else:
            return "❌ Position configuration incomplete.", get_trade_actions_menu(trade_id)
    return "❌ Position not found.", get_positions_menu(chat_id)

def handle_stop_trade(chat_id, trade_id):
    """Handle stopping a specific trade"""
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        config = user_trade_configs[chat_id][trade_id]
        config.status = "stopped"
        return f"⏹️ Stopped position: {config.get_display_name()}", get_trade_actions_menu(trade_id)
    return "❌ Position not found.", get_positions_menu(chat_id)

def handle_delete_trade(chat_id, trade_id):
    """Handle deleting a specific trade"""
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        config = user_trade_configs[chat_id][trade_id]
        trade_name = config.get_display_name()
        del user_trade_configs[chat_id][trade_id]
        if user_selected_trade.get(chat_id) == trade_id:
            del user_selected_trade[chat_id]
        return f"🗑️ Deleted position: {trade_name}", get_positions_menu(chat_id)
    return "❌ Position not found.", get_positions_menu(chat_id)



def handle_edit_trade(chat_id, trade_id):
    """Handle editing a specific trade"""
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        user_selected_trade[chat_id] = trade_id
        config = user_trade_configs[chat_id][trade_id]
        response = f"✏️ Editing: {config.get_display_name()}\n\n{config.get_config_summary()}"
        return response, get_trading_menu(chat_id)
    return "❌ Position not found.", get_positions_menu(chat_id)

def handle_set_stoploss(chat_id, sl_percent):
    """Handle setting stop loss percentage"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            
            # Log modification for active trades
            if config.status in ['active', 'pending']:
                logging.info(f"Updated stop loss for active trade {trade_id}: {sl_percent}%")
            
            config.stop_loss_percent = sl_percent
            header = config.get_trade_header("Stop Loss Set")
            return f"{header}✅ Set stop loss to {sl_percent}%", get_trading_menu(chat_id)
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
                config.entry_type = "market"
                config.waiting_for_limit_price = False
                # Continue wizard to take profits
                return f"✅ Set entry to Market Price\n\n🎯 Now let's set your take profits:", get_tp_percentage_input_menu()
            elif entry_type == "limit":
                config.entry_type = "limit"
                config.waiting_for_limit_price = True
                return f"🎯 Enter your limit price (e.g., 45000.50):", None
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
    mode_map = {
        "tp1": "After TP1",
        "tp2": "After TP2", 
        "tp3": "After TP3",
        "off": "Disabled"
    }
    
    # Set breakeven on the current trade configuration instead of global user config
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            
            # Log modification for active trades
            if config.status in ['active', 'pending']:
                logging.info(f"Updated break-even setting for active trade {trade_id}: {mode_map.get(mode, 'After TP1')}")
            
            config.breakeven_after = mode_map.get(mode, "After TP1")
            header = config.get_trade_header("Break-even Set")
            return f"{header}✅ Break-even set to: {mode_map.get(mode, 'After TP1')}", get_trading_menu(chat_id)
    
    return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)

def handle_trailing_stop_disable(chat_id):
    """Handle disabling trailing stop - Clean implementation"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            # Reset all trailing stop settings
            config.trailing_stop_enabled = False
            config.trail_percentage = None
            config.trail_activation_price = None
            config.waiting_for_trail_percent = False
            config.waiting_for_trail_activation = False
            header = config.get_trade_header("Trailing Stop Disabled")
            return f"{header}✅ Trailing stop disabled for current trade", get_trading_menu(chat_id)
    return "❌ No trade selected", get_main_menu()

def handle_trail_percent_request(chat_id):
    """Handle request to set trailing stop percentage"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            # Reset other waiting states
            config.waiting_for_trail_activation = False
            config.waiting_for_trail_percent = True
            return "📉 Enter trailing stop percentage (e.g., 2 for 2%):\n\nThis will move your stop loss when price moves favorably.", None
    return "❌ No trade selected", get_main_menu()

def handle_trail_activation_request(chat_id):
    """Handle request to set trailing stop activation price"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            # Reset other waiting states  
            config.waiting_for_trail_percent = False
            config.waiting_for_trail_activation = True
            return "🎯 Enter activation price (e.g., 45500):\n\nTrailing stop will activate when price reaches this level.", None
    return "❌ No trade selected", get_main_menu()





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
            
            # Log modification for active trades
            if config.status in ['active', 'pending']:
                logging.info(f"Updated TP{tp_level} for active trade {trade_id}: {tp_percent}%")
            
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

# Utility functions for mini-app

if __name__ == "__main__":
    # Setup webhook on startup  
    setup_webhook()
    app.run(host="0.0.0.0", port=5000, debug=True)