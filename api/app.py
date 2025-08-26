import os
import logging
import hmac
import hashlib
import uuid
from flask import Flask, request, jsonify, render_template, has_app_context
from datetime import datetime, timedelta
import urllib.request
import urllib.parse
import json
import random
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from werkzeug.middleware.proxy_fix import ProxyFix
from config import (
    APIConfig, TimeConfig, DatabaseConfig, TradingConfig, 
    LoggingConfig, SecurityConfig, Environment,
    get_cache_ttl, get_log_level, get_database_url
)
try:
    # Try relative import first (for module import - Vercel/main.py)
    from .models import db, UserCredentials, UserTradingSession, TradeConfiguration, format_iran_time, get_iran_time, utc_to_iran_time
    from exchange_sync import initialize_sync_service, get_sync_service
    from .vercel_sync import initialize_vercel_sync_service, get_vercel_sync_service
    from .toobit_client import ToobitClient
    from .enhanced_cache import enhanced_cache, start_cache_cleanup_worker
except ImportError:
    # Fall back to absolute import (for direct execution - Replit)
    import sys
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    sys.path.extend([current_dir, parent_dir])
    from api.models import db, UserCredentials, UserTradingSession, TradeConfiguration, format_iran_time, get_iran_time, utc_to_iran_time
    from exchange_sync import initialize_sync_service, get_sync_service
    from api.vercel_sync import initialize_vercel_sync_service, get_vercel_sync_service
    from api.toobit_client import ToobitClient
    from api.enhanced_cache import enhanced_cache, start_cache_cleanup_worker
from api.circuit_breaker import with_circuit_breaker, circuit_manager, CircuitBreakerError
from api.error_handler import handle_error, handle_api_error, create_validation_error, create_success_response

# Helper function to get user_id from request - streamlines repetitive code
def get_user_id_from_request(default_user_id=None):
    """Get user_id from request args with fallback to default"""
    return request.args.get('user_id', default_user_id or Environment.DEFAULT_TEST_USER_ID)

# Configure logging using centralized config
logging.basicConfig(level=getattr(logging, get_log_level()))

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", SecurityConfig.DEFAULT_SESSION_SECRET)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configure database using centralized config
database_url = get_database_url()
if not database_url:
    # Fallback to SQLite for development
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'instance', 'trading_bot.db')
    database_url = f"sqlite:///{db_path}"
    logging.info(f"Using SQLite database for development at {db_path}")

app.config["SQLALCHEMY_DATABASE_URI"] = database_url

# Database engine configuration based on database type
if database_url.startswith("sqlite"):
    # SQLite configuration for development
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": DatabaseConfig.POOL_PRE_PING,
        "pool_recycle": DatabaseConfig.STANDARD_POOL_RECYCLE
    }
elif database_url.startswith("postgresql") and (Environment.IS_VERCEL or "neon" in database_url.lower()):
    # Neon PostgreSQL serverless configuration - optimized for connection handling
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": DatabaseConfig.POOL_RECYCLE,
        "pool_pre_ping": DatabaseConfig.POOL_PRE_PING,
        "pool_size": DatabaseConfig.SERVERLESS_POOL_SIZE,
        "max_overflow": DatabaseConfig.SERVERLESS_MAX_OVERFLOW,
        "pool_timeout": DatabaseConfig.SERVERLESS_POOL_TIMEOUT,
        "pool_reset_on_return": "commit",
        "connect_args": {
            "sslmode": DatabaseConfig.SSL_MODE,
            "connect_timeout": TimeConfig.DEFAULT_API_TIMEOUT,
            "application_name": DatabaseConfig.APPLICATION_NAME,
            "keepalives_idle": DatabaseConfig.KEEPALIVES_IDLE,
            "keepalives_interval": DatabaseConfig.KEEPALIVES_INTERVAL,
            "keepalives_count": DatabaseConfig.KEEPALIVES_COUNT
        }
    }
elif database_url.startswith("postgresql"):
    # Standard PostgreSQL configuration (Replit or other)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": DatabaseConfig.STANDARD_POOL_RECYCLE,
        "pool_pre_ping": DatabaseConfig.POOL_PRE_PING,
        "pool_size": DatabaseConfig.STANDARD_POOL_SIZE,
        "max_overflow": DatabaseConfig.STANDARD_MAX_OVERFLOW
    }
else:
    # Fallback configuration
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": DatabaseConfig.POOL_PRE_PING,
        "pool_recycle": DatabaseConfig.STANDARD_POOL_RECYCLE
    }

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize database
db.init_app(app)

# Initialize enhanced caching system
start_cache_cleanup_worker()
logging.info("Enhanced caching system initialized with smart volatility-based TTL")

# Database migration helper
def run_database_migrations():
    """Run database migrations to ensure schema compatibility"""
    try:
        with app.app_context():
            from sqlalchemy import text
            migrations_needed = []
            
            # Check for missing columns
            required_columns = [
                ('breakeven_sl_triggered', 'BOOLEAN DEFAULT FALSE'),
                ('realized_pnl', 'FLOAT DEFAULT 0.0')
            ]
            
            try:
                # Check database type for proper column checking
                is_sqlite = database_url.startswith("sqlite")
                
                for column_name, column_def in required_columns:
                    if is_sqlite:
                        # SQLite column checking
                        result = db.session.execute(text("""
                            PRAGMA table_info(trade_configurations)
                        """))
                        columns = [row[1] for row in result.fetchall()]  # row[1] is the column name
                        if column_name not in columns:
                            migrations_needed.append((column_name, column_def))
                    else:
                        # PostgreSQL column checking
                        result = db.session.execute(text("""
                            SELECT column_name FROM information_schema.columns 
                            WHERE table_name = 'trade_configurations' 
                            AND column_name = :column_name
                        """), {"column_name": column_name})
                        
                        if not result.fetchone():
                            migrations_needed.append((column_name, column_def))
                
                # Apply migrations
                for column_name, column_def in migrations_needed:
                    logging.info(f"Adding missing {column_name} column")
                    db.session.execute(text(f"""
                        ALTER TABLE trade_configurations 
                        ADD COLUMN {column_name} {column_def}
                    """))
                
                if migrations_needed:
                    db.session.commit()
                    logging.info(f"Database migration completed successfully - added {len(migrations_needed)} columns")
                    
            except Exception as migration_error:
                logging.warning(f"Migration check failed (table may not exist yet): {migration_error}")
                db.session.rollback()
    except Exception as e:
        logging.error(f"Database migration error: {e}")

# Create tables only if not in serverless environment or if explicitly needed
def init_database():
    """Initialize database tables safely"""
    try:
        with app.app_context():
            db.create_all()
            logging.info("Database tables created successfully")
            # Run migrations after table creation
            run_database_migrations()
    except Exception as e:
        logging.error(f"Database initialization error: {e}")


# Initialize database conditionally
if not os.environ.get("VERCEL"):
    init_database()
    # Initialize background exchange sync service for Replit
    exchange_sync_service = initialize_sync_service(app, db)
    vercel_sync_service = None
else:
    # For Vercel, initialize on first request using newer Flask syntax
    initialized = False
    exchange_sync_service = None
    vercel_sync_service = None
    
    @app.before_request
    def create_tables():
        global initialized, vercel_sync_service
        if not initialized:
            init_database()
            # Initialize on-demand sync service for Vercel (no background processes)
            vercel_sync_service = initialize_vercel_sync_service(app, db)
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
            webhook_response = urllib.request.urlopen(webhook_req, timeout=SecurityConfig.WEBHOOK_SETUP_TIMEOUT)
            
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
    'last_heartbeat': get_iran_time().isoformat()
}

# User state tracking for API setup
user_api_setup_state = {}  # {user_id: {'step': 'api_key|api_secret|passphrase', 'exchange': 'toobit'}}

# Multi-trade management storage
user_trade_configs = {}  # {user_id: {trade_id: TradeConfig}}
user_selected_trade = {}  # {user_id: trade_id}

# Paper trading balance tracking
user_paper_balances = {}  # {user_id: balance_amount}

# Manual paper trading mode preferences
user_paper_trading_preferences = {}  # {user_id: True/False}

# Cache for database loads to prevent frequent database hits
user_data_cache = {}  # {user_id: {'data': trades_data, 'timestamp': last_load_time, 'version': data_version}}
cache_ttl = get_cache_ttl("user")  # Cache TTL in seconds for Vercel optimization
trade_counter = 0

# Initialize clean user environment
def initialize_user_environment(user_id, force_reload=False):
    """Initialize trading environment for a user, loading from database only when necessary"""
    user_id = int(user_id)
    user_id_str = str(user_id)
    
    # Check enhanced cache first for user trade configurations
    cached_result = enhanced_cache.get_user_trade_configs(user_id_str)
    if not force_reload and cached_result:
        trade_configs, cache_info = cached_result
        user_trade_configs[user_id] = trade_configs
        # Initialize user's selected trade if not exists
        if user_id not in user_selected_trade:
            user_selected_trade[user_id] = None
# Cache hit - removed excessive debug logging for cleaner output
        return
    
    # Only load from database if user has no data in memory or force_reload is True
    # This prevents unnecessary database calls during frequent price updates
    if user_id not in user_trade_configs or force_reload:
        user_trade_configs[user_id] = load_user_trades_from_db(user_id)
    
    # Initialize user's selected trade if not exists
    if user_id not in user_selected_trade:
        user_selected_trade[user_id] = None



class TradeConfig:
    def __init__(self, trade_id, name="New Trade"):
        self.trade_id = trade_id
        self.name = name
        self.symbol = ""
        self.side = ""  # 'long' or 'short'
        self.amount = 0.0
        self.leverage = TradingConfig.DEFAULT_LEVERAGE
        self.entry_price = 0.0
        self.entry_type = ""  # 'market' or 'limit'
        self.waiting_for_limit_price = False  # Track if waiting for limit price input
        # Take profit system - percentages and allocations
        self.take_profits = []  # List of {percentage: float, allocation: float}
        self.tp_config_step = "percentages"  # "percentages" or "allocations"
        self.stop_loss_percent = 0.0
        self.breakeven_after = 0.0
        self.breakeven_sl_triggered = False  # Track if breakeven stop loss has been triggered
        self.breakeven_sl_price = 0.0  # Price at which break-even stop loss triggers
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
        self.realized_pnl = 0.0     # Realized P&L from triggered take profits
        self.final_pnl = 0.0        # Final P&L when position is closed
        self.closed_at = ""         # Timestamp when position was closed
        self.notes = ""             # Additional notes for the trade
        
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
        tp_sl_data = calculate_tp_sl_prices_and_amounts(self) if self.entry_price > 0 else {}
        
        if tp_sl_data.get('stop_loss'):
            sl_calc = tp_sl_data['stop_loss']
            if sl_calc.get('is_breakeven'):
                summary += f"Stop Loss: ${sl_calc['price']:.4f} (Break-even)\n"
            else:
                summary += f"Stop Loss: ${sl_calc['price']:.4f} (-${sl_calc['loss_amount']:.2f}) [{self.stop_loss_percent}%]\n"
        elif self.stop_loss_percent > 0:
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
            "Stop Loss": "✅" if self.stop_loss_percent > 0 else ("⚖️" if self.stop_loss_percent == 0.0 and hasattr(self, 'status') and self.status == 'active' else "⏳")
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
        elif self.stop_loss_percent == 0.0 and hasattr(self, 'status') and self.status == 'active':
            header += f"   ⚖️ Stop Loss: Break-even\n"
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

# Database helper functions for trade persistence
def load_user_trades_from_db(user_id, force_reload=False):
    """Load all trade configurations for a user from database with enhanced caching"""
    user_id_str = str(user_id)
    
    # Check enhanced cache first
    if not force_reload:
        cached_result = enhanced_cache.get_user_trade_configs(user_id_str)
        if cached_result:
            trade_configs, cache_info = cached_result
# Retrieved trades from cache - removed debug log for cleaner output
            return trade_configs
    
    max_retries = 2
    retry_delay = 0.3
    
    for attempt in range(max_retries):
        try:
            with app.app_context():
                # Ensure database is properly initialized
                if not hasattr(db.engine, 'table_names'):
                    db.create_all()
                    
                # Use read-committed isolation for Neon
                db_trades = TradeConfiguration.query.filter_by(
                    telegram_user_id=user_id_str
                ).order_by(TradeConfiguration.created_at.desc()).all()
                
                user_trades = {}
                for db_trade in db_trades:
                    trade_config = db_trade.to_trade_config()
                    user_trades[db_trade.trade_id] = trade_config
                
                # Update enhanced cache with fresh data
                enhanced_cache.set_user_trade_configs(user_id_str, user_trades)
                
                # Only log when debugging or significant cache operations
                debug_mode = os.environ.get("DEBUG") or os.environ.get("FLASK_DEBUG")
                if debug_mode or (force_reload and len(user_trades) > 0):
                    logging.info(f"Loaded {len(user_trades)} trades for user {user_id} from database (cache {'refresh' if force_reload else 'miss'})")
                return user_trades
                
        except Exception as e:
            logging.warning(f"Database load attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                logging.error(f"Failed to load trades for user {user_id} after {max_retries} attempts: {e}")
                # Return cached data if available, even if stale
                cached_result = enhanced_cache.get_user_trade_configs(user_id_str)
                if cached_result:
                    trade_configs, _ = cached_result
                    logging.info(f"Returning cached data for user {user_id} after DB failure")
                    return trade_configs
                return {}
    
    return {}

def save_trade_to_db(user_id, trade_config):
    """Save or update a trade configuration in the database"""
    max_retries = 3
    retry_delay = 0.5
    
    for attempt in range(max_retries):
        try:
            with app.app_context():
                # Ensure database is properly initialized
                if not hasattr(db.engine, 'table_names'):
                    db.create_all()
                
                # Check if trade already exists in database
                existing_trade = TradeConfiguration.query.filter_by(
                    telegram_user_id=str(user_id),
                    trade_id=trade_config.trade_id
                ).first()
                
                if existing_trade:
                    # Update existing trade
                    db_trade = TradeConfiguration.from_trade_config(user_id, trade_config)
                    existing_trade.name = db_trade.name
                    existing_trade.symbol = db_trade.symbol
                    existing_trade.side = db_trade.side
                    existing_trade.amount = db_trade.amount
                    existing_trade.leverage = db_trade.leverage
                    existing_trade.entry_type = db_trade.entry_type
                    existing_trade.entry_price = db_trade.entry_price
                    existing_trade.take_profits = db_trade.take_profits
                    existing_trade.stop_loss_percent = db_trade.stop_loss_percent
                    existing_trade.breakeven_after = db_trade.breakeven_after
                    existing_trade.trailing_stop_enabled = db_trade.trailing_stop_enabled
                    existing_trade.trail_percentage = db_trade.trail_percentage
                    existing_trade.trail_activation_price = db_trade.trail_activation_price
                    existing_trade.status = db_trade.status
                    existing_trade.position_margin = db_trade.position_margin
                    existing_trade.unrealized_pnl = db_trade.unrealized_pnl
                    existing_trade.current_price = db_trade.current_price
                    existing_trade.position_size = db_trade.position_size
                    existing_trade.position_value = db_trade.position_value
                    existing_trade.final_pnl = db_trade.final_pnl
                    existing_trade.closed_at = db_trade.closed_at
                    existing_trade.updated_at = get_iran_time().replace(tzinfo=None)
                else:
                    # Create new trade
                    db_trade = TradeConfiguration.from_trade_config(user_id, trade_config)
                    db.session.add(db_trade)
                
                # Neon-optimized commit process
                db.session.flush()
                db.session.commit()
                
                # Invalidate cache when data changes
                user_id_str = str(user_id)
                if user_id_str in user_data_cache:
                    del user_data_cache[user_id_str]
                
                # Only log saves in development or for error debugging
                if not os.environ.get("VERCEL"):
                    logging.info(f"Saved trade {trade_config.trade_id} to database for user {user_id}")
                return True
                
        except Exception as e:
            logging.warning(f"Database save attempt {attempt + 1} failed: {e}")
            try:
                db.session.rollback()
            except:
                pass
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logging.error(f"Failed to save trade {trade_config.trade_id} after {max_retries} attempts: {e}")
                return False
    
    return False

def delete_trade_from_db(user_id, trade_id):
    """Delete a trade configuration from the database"""
    try:
        with app.app_context():
            trade = TradeConfiguration.query.filter_by(
                telegram_user_id=str(user_id),
                trade_id=trade_id
            ).first()
            
            if trade:
                db.session.delete(trade)
                db.session.flush()
                db.session.commit()
                logging.info(f"Deleted trade {trade_id} from database for user {user_id}")
                return True
            return False
    except Exception as e:
        logging.error(f"Error deleting trade {trade_id} from database: {e}")
        try:
            db.session.rollback()
        except:
            pass
        return False

@app.route('/')
def mini_app():
    """Telegram Mini App interface - Main route"""
    return render_template('mini_app.html', 
                         price_update_interval=TimeConfig.PRICE_UPDATE_INTERVAL,
                         portfolio_refresh_interval=TimeConfig.PORTFOLIO_REFRESH_INTERVAL)

@app.route('/miniapp')
def mini_app_alias():
    """Telegram Mini App interface - Alias route"""
    return render_template('mini_app.html', 
                         price_update_interval=TimeConfig.PRICE_UPDATE_INTERVAL,
                         portfolio_refresh_interval=TimeConfig.PORTFOLIO_REFRESH_INTERVAL)

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': get_iran_time().isoformat()
    })

@app.route('/api/health')
def api_health_check():
    """API Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': get_iran_time().isoformat(),
        'api_version': '1.0'
    })

# Exchange Synchronization Endpoints
@app.route('/api/exchange/sync-status')
def exchange_sync_status():
    """Get exchange synchronization status"""
    user_id = get_user_id_from_request()
    
    # Use appropriate sync service based on environment
    if os.environ.get("VERCEL"):
        sync_service = get_vercel_sync_service()
    else:
        sync_service = get_sync_service()
    
    if sync_service:
        status = sync_service.get_sync_status(user_id)
        return jsonify(status)
    else:
        return jsonify({'error': 'Exchange sync service not available'}), 503

@app.route('/api/exchange/force-sync', methods=['POST'])
def force_exchange_sync():
    """Force immediate synchronization with Toobit exchange"""
    user_id = get_user_id_from_request()
    
    # Use appropriate sync service based on environment
    if os.environ.get("VERCEL"):
        sync_service = get_vercel_sync_service()
        if sync_service:
            result = sync_service.sync_user_on_request(user_id, force=True)
            return jsonify(result)
        else:
            return jsonify({'error': 'Vercel sync service not available'}), 503
    else:
        sync_service = get_sync_service()
        if sync_service:
            success = sync_service.force_sync_user(user_id)
            if success:
                return jsonify({'success': True, 'message': 'Synchronization completed'})
            else:
                return jsonify({'success': False, 'message': 'Synchronization failed'}), 500
        else:
            return jsonify({'error': 'Exchange sync service not available'}), 503

@app.route('/api/exchange/test-connection', methods=['POST'])
def test_exchange_connection():
    """Test connection to Toobit exchange"""
    user_id = get_user_id_from_request()
    
    try:
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id,
            is_active=True
        ).first()
        
        if not user_creds or not user_creds.has_credentials():
            return jsonify({'success': False, 'message': 'No API credentials found'}), 400
        
        # Create client and test connection
        client = ToobitClient(
            api_key=user_creds.get_api_key(),
            api_secret=user_creds.get_api_secret(),
            passphrase=user_creds.get_passphrase(),
            testnet=user_creds.testnet_mode
        )
        
        is_connected, message = client.test_connection()
        
        return jsonify({
            'success': is_connected,
            'message': message,
            'testnet': user_creds.testnet_mode
        })
        
    except Exception as e:
        logging.error(f"Error testing exchange connection: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/exchange/balance')
def get_exchange_balance():
    """Get real account balance from Toobit exchange"""
    user_id = get_user_id_from_request()
    
    try:
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id,
            is_active=True
        ).first()
        
        if not user_creds or not user_creds.has_credentials():
            return jsonify({'error': 'No API credentials found', 'testnet_mode': True}), 400
        
        # Create client and get balance
        client = ToobitClient(
            api_key=user_creds.get_api_key(),
            api_secret=user_creds.get_api_secret(),
            passphrase=user_creds.get_passphrase(),
            testnet=user_creds.testnet_mode
        )
        
        balance_data = client.get_account_balance()
        
        if balance_data and isinstance(balance_data, list) and len(balance_data) > 0:
            # Extract USDT balance info from Toobit response
            usdt_balance = balance_data[0]  # Toobit returns array with USDT info
            
            total_balance = float(usdt_balance.get('balance', '0'))
            available_balance = float(usdt_balance.get('availableBalance', '0'))
            position_margin = float(usdt_balance.get('positionMargin', '0'))
            order_margin = float(usdt_balance.get('orderMargin', '0'))
            unrealized_pnl = float(usdt_balance.get('crossUnRealizedPnl', '0'))
            
            # Calculate used margin and margin ratio
            used_margin = position_margin + order_margin
            margin_ratio = (used_margin / total_balance * 100) if total_balance > 0 else 0
            
            return jsonify({
                'success': True,
                'testnet_mode': user_creds.testnet_mode,
                'balance': {
                    'total_balance': total_balance,
                    'available_balance': available_balance,
                    'used_margin': used_margin,
                    'position_margin': position_margin,
                    'order_margin': order_margin,
                    'unrealized_pnl': unrealized_pnl,
                    'margin_ratio': round(margin_ratio, 2),
                    'asset': usdt_balance.get('asset', 'USDT')
                },
                'raw_data': balance_data,
                'timestamp': get_iran_time().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No balance data received from exchange',
                'testnet_mode': user_creds.testnet_mode
            }), 500
        
    except Exception as e:
        logging.error(f"Error getting exchange balance: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'testnet_mode': True
        }), 500

@app.route('/api/exchange/positions')
def get_exchange_positions():
    """Get positions directly from Toobit exchange"""
    user_id = get_user_id_from_request()
    
    try:
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id,
            is_active=True
        ).first()
        
        if not user_creds or not user_creds.has_credentials():
            return jsonify({'error': 'No API credentials found'}), 400
        
        # Create client and get positions
        client = ToobitClient(
            api_key=user_creds.get_api_key(),
            api_secret=user_creds.get_api_secret(),
            passphrase=user_creds.get_passphrase(),
            testnet=user_creds.testnet_mode
        )
        
        positions = client.get_positions()
        
        return jsonify({
            'success': True,
            'positions': positions,
            'timestamp': get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error getting exchange positions: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/exchange/orders')  
def get_exchange_orders():
    """Get orders directly from Toobit exchange"""
    user_id = get_user_id_from_request()
    symbol = request.args.get('symbol')
    status = request.args.get('status')
    
    try:
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id,
            is_active=True
        ).first()
        
        if not user_creds or not user_creds.has_credentials():
            return jsonify({'error': 'No API credentials found'}), 400
        
        # Create client and get orders
        client = ToobitClient(
            api_key=user_creds.get_api_key(),
            api_secret=user_creds.get_api_secret(),
            passphrase=user_creds.get_passphrase(),
            testnet=user_creds.testnet_mode
        )
        
        orders = client.get_orders(symbol=symbol, status=status)
        
        return jsonify({
            'success': True,
            'orders': orders,
            'timestamp': get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error getting exchange orders: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/webhook/toobit', methods=['POST'])
def toobit_webhook():
    """Handle Toobit exchange webhooks for real-time updates"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Verify webhook signature if configured
        webhook_secret = os.environ.get('TOOBIT_WEBHOOK_SECRET')
        if webhook_secret:
            signature = request.headers.get('X-Toobit-Signature')
            if not signature:
                return jsonify({'error': 'Missing signature'}), 401
            
            # Verify signature (implementation depends on Toobit's webhook format)
            # This is a placeholder - adjust based on actual Toobit webhook specification
            expected_signature = hmac.new(
                webhook_secret.encode(),
                request.data,
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(signature, expected_signature):
                return jsonify({'error': 'Invalid signature'}), 401
        
        # Process webhook data
        event_type = data.get('eventType')
        user_id = data.get('userId')
        
        if event_type and user_id:
            # Process different webhook events
            if event_type == 'ORDER_UPDATE':
                handle_order_update_webhook(data)
            elif event_type == 'POSITION_UPDATE':
                handle_position_update_webhook(data)
            elif event_type == 'BALANCE_UPDATE':
                handle_balance_update_webhook(data)
            
            logging.info(f"Processed Toobit webhook: {event_type} for user {user_id}")
        
        return jsonify({'success': True})
        
    except Exception as e:
        logging.error(f"Error processing Toobit webhook: {e}")
        return jsonify({'error': str(e)}), 500

def handle_order_update_webhook(data):
    """Handle order update webhook from Toobit"""
    try:
        user_id = data.get('userId')
        order_data = data.get('orderData', {})
        
        # Find corresponding local trade
        symbol = order_data.get('symbol')
        order_status = order_data.get('status')
        
        if order_status == 'filled':
            # Update local trade records
            trade = TradeConfiguration.query.filter_by(
                telegram_user_id=str(user_id),
                symbol=symbol,
                status='active'
            ).first()
            
            if trade:
                # Calculate final P&L and update trade
                fill_price = float(order_data.get('avgPrice', 0))
                fill_quantity = float(order_data.get('executedQty', 0))
                
                if trade.side == 'long':
                    final_pnl = (fill_price - trade.entry_price) * fill_quantity
                else:
                    final_pnl = (trade.entry_price - fill_price) * fill_quantity
                
                trade.status = 'stopped'
                trade.final_pnl = final_pnl
                trade.closed_at = get_iran_time().replace(tzinfo=None)
                
                db.session.commit()
                logging.info(f"Updated trade {trade.trade_id} from webhook")
        
    except Exception as e:
        logging.error(f"Error handling order update webhook: {e}")

def handle_position_update_webhook(data):
    """Handle position update webhook from Toobit"""
    try:
        user_id = data.get('userId')
        position_data = data.get('positionData', {})
        
        # Update local trade records with real-time position data
        symbol = position_data.get('symbol')
        unrealized_pnl = float(position_data.get('unrealizedPnl', 0))
        mark_price = float(position_data.get('markPrice', 0))
        
        trades = TradeConfiguration.query.filter_by(
            telegram_user_id=str(user_id),
            symbol=symbol,
            status='active'
        ).all()
        
        for trade in trades:
            trade.current_price = mark_price
            trade.unrealized_pnl = unrealized_pnl
        
        db.session.commit()
        
    except Exception as e:
        logging.error(f"Error handling position update webhook: {e}")

def handle_balance_update_webhook(data):
    """Handle balance update webhook from Toobit"""
    try:
        user_id = data.get('userId')
        balance_data = data.get('balanceData', {})
        
        # Update user session with new balance information
        session = UserTradingSession.query.filter_by(
            telegram_user_id=str(user_id)
        ).first()
        
        if session:
            new_balance = float(balance_data.get('balance', session.account_balance))
            session.account_balance = new_balance
            db.session.commit()
        
    except Exception as e:
        logging.error(f"Error handling balance update webhook: {e}")





@app.route('/api/toggle-paper-trading', methods=['POST'])
def toggle_paper_trading():
    """Toggle paper trading mode for a user"""
    user_id = None
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        logging.info(f"Toggle paper trading request received for user: {user_id}")
        
        if not user_id:
            logging.error("Toggle paper trading failed: No user ID provided")
            return jsonify({'success': False, 'message': 'User ID required'}), 400
        
        try:
            chat_id = int(user_id)
        except ValueError:
            logging.error(f"Toggle paper trading failed: Invalid user ID format: {user_id}")
            return jsonify({'success': False, 'message': 'Invalid user ID format'}), 400
        
        # Simple toggle for paper trading preference
        current_paper_mode = user_paper_trading_preferences.get(chat_id, True)  # Default to paper trading
        new_paper_mode = not current_paper_mode
        user_paper_trading_preferences[chat_id] = new_paper_mode
        
        # Initialize paper balance if switching to paper mode
        if new_paper_mode and chat_id not in user_paper_balances:
            user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
            logging.info(f"Initialized paper balance for user {chat_id}: ${TradingConfig.DEFAULT_TRIAL_BALANCE:,.2f}")
        
        # Log the mode change
        mode_text = "ENABLED" if new_paper_mode else "DISABLED"
        logging.info(f"🔄 Paper Trading {mode_text} for user {chat_id}")
        logging.info(f"📊 Current paper balance: ${user_paper_balances.get(chat_id, 0):,.2f}")
        
        response_data = {
            'success': True,
            'paper_trading_active': new_paper_mode,
            'paper_balance': user_paper_balances.get(chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE) if new_paper_mode else None,
            'message': f'Paper trading {"enabled" if new_paper_mode else "disabled"}'
        }
        
        logging.info(f"Toggle paper trading successful: {response_data}")
        return jsonify(response_data)
        
    except Exception as e:
        logging.error(f"Error toggling paper trading for user {user_id or 'unknown'}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': f'Server error: {str(e)}'}), 500


@app.route('/api/paper-trading-status')
def get_paper_trading_status():
    """Get current paper trading status for a user"""
    try:
        user_id = request.args.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'message': 'User ID required'}), 400
        
        try:
            chat_id = int(user_id)
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid user ID format'}), 400
        
        # Get user credentials (optional for paper trading)
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(user_id),
            is_active=True
        ).first()
        
        # Check for manual paper trading preference
        manual_paper_mode = user_paper_trading_preferences.get(chat_id, True)  # Default to paper trading
        
        # Determine if paper trading is active (same logic as toggle endpoint)
        is_paper_mode = (manual_paper_mode or 
                        not user_creds or 
                        (user_creds and user_creds.testnet_mode) or 
                        (user_creds and not user_creds.has_credentials()))
        
        # Determine the reason for the current mode
        if manual_paper_mode:
            mode_reason = "Manual paper trading enabled"
        elif not user_creds or not user_creds.has_credentials():
            mode_reason = "No API credentials configured"
        elif user_creds and user_creds.testnet_mode:
            mode_reason = "Testnet mode enabled"
        else:
            mode_reason = "Live trading with credentials"
        
        response_data = {
            'success': True,
            'paper_trading_active': is_paper_mode,
            'manual_paper_mode': manual_paper_mode,
            'mode_reason': mode_reason,
            'paper_balance': user_paper_balances.get(chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE) if is_paper_mode else None,
            'testnet_mode': user_creds.testnet_mode if user_creds else False,
            'has_credentials': user_creds.has_credentials() if user_creds else False,
            'can_toggle_manual': user_creds and user_creds.has_credentials() and not user_creds.testnet_mode,
            'message': f'Paper trading {"active" if is_paper_mode else "inactive"}'
        }
        
        logging.info(f"Paper trading status for user {chat_id}: {response_data}")
        return jsonify(response_data)
        
    except Exception as e:
        logging.error(f"Error getting paper trading status: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/status')
def get_bot_status():
    """Get bot status with API performance metrics"""
    # Check if bot is active (heartbeat within last 5 minutes)
    if bot_status['last_heartbeat']:
        current_time = get_iran_time().replace(tzinfo=None)  # Remove timezone for comparison
        last_heartbeat = datetime.fromisoformat(bot_status['last_heartbeat']).replace(tzinfo=None)
        time_diff = current_time - last_heartbeat
        is_active = time_diff.total_seconds() < TimeConfig.BOT_HEARTBEAT_TIMEOUT
        bot_status['status'] = 'active' if is_active else 'inactive'
    
    # Add API performance metrics
    bot_status['api_performance'] = {}
    for api_name, metrics in api_performance_metrics.items():
        if metrics['requests'] > 0:
            success_rate = (metrics['successes'] / metrics['requests']) * 100
            bot_status['api_performance'][api_name] = {
                'success_rate': round(success_rate, 2),
                'avg_response_time': round(metrics['avg_response_time'], 3),
                'total_requests': metrics['requests'],
                'last_success': metrics['last_success'].isoformat() if metrics['last_success'] else None
            }
        else:
            bot_status['api_performance'][api_name] = {
                'success_rate': 0,
                'avg_response_time': 0,
                'total_requests': 0,
                'last_success': None
            }
    
    # Add enhanced cache statistics
    cache_stats = enhanced_cache.get_cache_stats()
    bot_status['cache_stats'] = cache_stats
    
    return jsonify(bot_status)

@app.route('/api/cache/stats')
def cache_statistics():
    """Get comprehensive cache statistics and performance metrics"""
    return jsonify(enhanced_cache.get_cache_stats())

@app.route('/api/cache/invalidate', methods=['POST'])
def invalidate_cache():
    """Invalidate cache entries based on parameters"""
    try:
        data = request.get_json() or {}
        cache_type = data.get('type', 'all')  # 'price', 'user', or 'all'
        symbol = data.get('symbol')
        user_id = data.get('user_id')
        
        if cache_type == 'price':
            enhanced_cache.invalidate_price(symbol)
        elif cache_type == 'user':
            enhanced_cache.invalidate_user_data(user_id)
        else:
            enhanced_cache.invalidate_price()
            enhanced_cache.invalidate_user_data()
        
        return jsonify({'success': True, 'message': f'Cache invalidated for type: {cache_type}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/circuit-breakers/stats')
def circuit_breaker_stats():
    """Get statistics for all circuit breakers"""
    return jsonify(circuit_manager.get_all_stats())

@app.route('/api/circuit-breakers/reset', methods=['POST'])
def reset_circuit_breakers():
    """Reset circuit breakers (all or specific service)"""
    try:
        data = request.get_json() or {}
        service = data.get('service')
        
        if service:
            breaker = circuit_manager.get_breaker(service)
            breaker.reset()
            return jsonify({'success': True, 'message': f'Circuit breaker for {service} reset'})
        else:
            circuit_manager.reset_all()
            return jsonify({'success': True, 'message': 'All circuit breakers reset'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/circuit-breakers/health')
def circuit_breaker_health():
    """Get health status of all services"""
    healthy = circuit_manager.get_healthy_services()
    unhealthy = circuit_manager.get_unhealthy_services()
    
    return jsonify({
        'healthy_services': healthy,
        'unhealthy_services': unhealthy,
        'total_services': len(healthy) + len(unhealthy),
        'health_percentage': (len(healthy) / max(1, len(healthy) + len(unhealthy))) * 100
    })

@app.route('/api/price/<symbol>')
def get_symbol_price(symbol):
    """Get live price for a specific symbol with caching info"""
    try:
        symbol = symbol.upper()
        
        # Check enhanced cache for existing data
        cached_result = enhanced_cache.get_price(symbol)
        if cached_result:
            price, price_source, cache_info = cached_result
        else:
            price = get_live_market_price(symbol, prefer_exchange=True)
            # Get fresh cache info after fetching
            fresh_cached_result = enhanced_cache.get_price(symbol)
            if fresh_cached_result:
                _, price_source, cache_info = fresh_cached_result
            else:
                price_source = 'unknown'
                cache_info = {'cached': False}
        
        return jsonify({
            'symbol': symbol,
            'price': price,
            'price_source': price_source,
            'timestamp': get_iran_time().isoformat(),
            'cache_info': cache_info
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/prices', methods=['POST'])
def get_multiple_prices():
    """Get live prices for multiple symbols efficiently"""
    try:
        data = request.get_json()
        symbols = data.get('symbols', [])
        
        if not symbols or not isinstance(symbols, list):
            return jsonify({'error': 'Symbols array required'}), 400
        
        # Limit to prevent abuse
        if len(symbols) > TradingConfig.MAX_SYMBOLS_BATCH:
            return jsonify({'error': f'Maximum {TradingConfig.MAX_SYMBOLS_BATCH} symbols allowed'}), 400
        
        symbols = [s.upper() for s in symbols]
        
        # Batch fetch prices
        futures = {}
        for symbol in symbols:
            future = price_executor.submit(get_live_market_price, symbol, True)
            futures[future] = symbol
        
        results = {}
        for future in as_completed(futures, timeout=TimeConfig.DEFAULT_API_TIMEOUT):
            symbol = futures[future]
            try:
                price = future.result()
                results[symbol] = {
                    'price': price,
                    'status': 'success'
                }
            except Exception as e:
                results[symbol] = {
                    'price': None,
                    'status': 'error',
                    'error': str(e)
                }
        
        return jsonify({
            'results': results,
            'timestamp': get_iran_time().isoformat(),
            'total_symbols': len(symbols),
            'successful': len([r for r in results.values() if r['status'] == 'success'])
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/smc-analysis/<symbol>')
def get_smc_analysis(symbol):
    """Get Smart Money Concepts analysis for a specific symbol"""
    try:
        from .smc_analyzer import SMCAnalyzer
        
        analyzer = SMCAnalyzer()
        signal = analyzer.generate_trade_signal(symbol.upper())
        
        if signal:
            return jsonify({
                'symbol': signal.symbol,
                'direction': signal.direction,
                'entry_price': signal.entry_price,
                'stop_loss': signal.stop_loss,
                'take_profit_levels': signal.take_profit_levels,
                'confidence': signal.confidence,
                'reasoning': signal.reasoning,
                'signal_strength': signal.signal_strength.value,
                'risk_reward_ratio': signal.risk_reward_ratio,
                'timestamp': signal.timestamp.isoformat(),
                'status': 'signal_generated'
            })
        else:
            return jsonify({
                'symbol': symbol.upper(),
                'status': 'no_signal',
                'message': 'No strong SMC signal detected at this time',
                'timestamp': get_iran_time().isoformat()
            })
            
    except Exception as e:
        logging.error(f"Error in SMC analysis for {symbol}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/smc-signals')
def get_multiple_smc_signals():
    """Get SMC signals for multiple popular trading symbols"""
    try:
        from .smc_analyzer import SMCAnalyzer
        
        # Analyze popular trading pairs
        symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT', 'SOLUSDT']
        analyzer = SMCAnalyzer()
        
        signals = {}
        for symbol in symbols:
            try:
                signal = analyzer.generate_trade_signal(symbol)
                if signal:
                    signals[symbol] = {
                        'direction': signal.direction,
                        'entry_price': signal.entry_price,
                        'stop_loss': signal.stop_loss,
                        'take_profit_levels': signal.take_profit_levels,
                        'confidence': signal.confidence,
                        'reasoning': signal.reasoning[:3],  # Limit reasoning for summary
                        'signal_strength': signal.signal_strength.value,
                        'risk_reward_ratio': signal.risk_reward_ratio,
                        'timestamp': signal.timestamp.isoformat()
                    }
                else:
                    signals[symbol] = {
                        'status': 'no_signal',
                        'message': 'No strong signal detected'
                    }
            except Exception as e:
                signals[symbol] = {
                    'status': 'error',
                    'message': str(e)
                }
        
        return jsonify({
            'signals': signals,
            'timestamp': get_iran_time().isoformat(),
            'total_analyzed': len(symbols),
            'signals_found': len([s for s in signals.values() if 'direction' in s])
        })
        
    except Exception as e:
        logging.error(f"Error getting multiple SMC signals: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/smc-auto-trade', methods=['POST'])
def create_auto_trade_from_smc():
    """Create a trade configuration automatically based on SMC analysis"""
    try:
        data = request.get_json()
        symbol = data.get('symbol', '').upper()
        user_id = data.get('user_id')
        margin_amount = float(data.get('margin_amount', 100))
        
        if not symbol or not user_id:
            return jsonify({'error': 'Symbol and user_id required'}), 400
        
        from .smc_analyzer import SMCAnalyzer
        
        analyzer = SMCAnalyzer()
        signal = analyzer.generate_trade_signal(symbol)
        
        if not signal:
            return jsonify({
                'error': 'No SMC signal available for this symbol',
                'symbol': symbol
            }), 400
        
        # Only proceed with strong signals
        if signal.confidence < 0.7:
            return jsonify({
                'error': 'SMC signal confidence too low for auto-trading',
                'confidence': signal.confidence,
                'minimum_required': 0.7
            }), 400
        
        # Generate trade ID
        trade_id = f"smc_{symbol}_{int(datetime.now().timestamp())}"
        
        # Create trade configuration
        trade_config = TradeConfig(trade_id, f"SMC Auto-Trade {symbol}")
        trade_config.symbol = symbol
        trade_config.side = signal.direction
        trade_config.amount = margin_amount
        trade_config.leverage = 5  # Conservative leverage for auto-trades
        trade_config.entry_type = "market"  # Market entry for immediate execution
        trade_config.entry_price = signal.entry_price
        
        # Calculate stop loss percentage
        if signal.direction == 'long':
            sl_percent = ((signal.entry_price - signal.stop_loss) / signal.entry_price) * 100
        else:
            sl_percent = ((signal.stop_loss - signal.entry_price) / signal.entry_price) * 100
        
        trade_config.stop_loss_percent = min(sl_percent, 5.0)  # Cap at 5% for safety
        
        # Set up take profit levels with proper allocation logic
        tp_levels = []
        num_tp_levels = min(len(signal.take_profit_levels), 3)  # Max 3 TP levels
        
        # Define allocation strategies based on number of TP levels
        allocation_strategies = {
            1: [100],           # Single TP: close full position
            2: [60, 40],        # Two TPs: 60% then 40%
            3: [50, 30, 20]     # Three TPs: 50%, 30%, 20%
        }
        
        allocations = allocation_strategies.get(num_tp_levels, [100])
        
        for i, tp_price in enumerate(signal.take_profit_levels[:3]):
            if signal.direction == 'long':
                tp_percent = ((tp_price - signal.entry_price) / signal.entry_price) * 100
            else:
                tp_percent = ((signal.entry_price - tp_price) / signal.entry_price) * 100
            
            allocation = allocations[i] if i < len(allocations) else 10
            
            tp_levels.append({
                'percentage': tp_percent,
                'allocation': allocation,
                'triggered': False
            })
        
        trade_config.take_profits = tp_levels
        
        # Add SMC analysis details to notes
        trade_config.notes = f"SMC Auto-Trade | Confidence: {signal.confidence:.1%} | " + \
                           f"Signal Strength: {signal.signal_strength.value} | " + \
                           f"R:R = 1:{signal.risk_reward_ratio:.1f}"
        
        # Store the trade configuration (use integer user_id for consistency)
        user_id_int = int(user_id)
        if user_id_int not in user_trade_configs:
            user_trade_configs[user_id_int] = {}
        
        user_trade_configs[user_id_int][trade_id] = trade_config
        
        # Save to database
        save_trade_to_db(user_id, trade_config)
        
        return jsonify({
            'success': True,
            'trade_id': trade_id,
            'trade_config': {
                'symbol': trade_config.symbol,
                'side': trade_config.side,
                'amount': trade_config.amount,
                'leverage': trade_config.leverage,
                'entry_price': trade_config.entry_price,
                'stop_loss_percent': trade_config.stop_loss_percent,
                'take_profits': trade_config.take_profits,
                'smc_analysis': {
                    'confidence': signal.confidence,
                    'signal_strength': signal.signal_strength.value,
                    'reasoning': signal.reasoning,
                    'risk_reward_ratio': signal.risk_reward_ratio
                }
            },
            'message': f'SMC-based trade configuration created for {symbol}',
            'timestamp': get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error creating auto-trade from SMC: {e}")
        return jsonify({'error': str(e)}), 500

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
        user_id = Environment.DEFAULT_TEST_USER_ID
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({'error': 'Invalid user ID format'}), 400
    
    # Initialize user environment (uses cache to prevent DB hits)
    initialize_user_environment(chat_id, force_reload=False)
    
    # Update all positions with live market data from Toobit exchange
    # Use optimized lightweight monitoring - only checks break-even positions
    update_positions_lightweight()
    
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
        'timestamp': get_iran_time().isoformat()
    })

@app.route('/api/positions')
def api_positions():
    """Get positions for the web app - alias for margin-data"""
    return margin_data()

@app.route('/api/positions/live-update')
def live_position_update():
    """Get only current prices and P&L for active positions (lightweight update)"""
    user_id = request.args.get('user_id')
    if not user_id or user_id == 'undefined':
        user_id = Environment.DEFAULT_TEST_USER_ID
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({'error': 'Invalid user ID format'}), 400
    
    # For Vercel: Trigger on-demand sync if needed
    if os.environ.get("VERCEL"):
        sync_service = get_vercel_sync_service()
        if sync_service:
            sync_result = sync_service.sync_user_on_request(user_id)
            # Continue with regular live update regardless of sync result
    
    # For live updates, ensure user is initialized from cache (no DB hit)
    initialize_user_environment(chat_id, force_reload=False)
    
    # Only proceed if user has trades loaded
    if chat_id not in user_trade_configs:
        return jsonify({
            'positions': {},
            'total_unrealized_pnl': 0.0,
            'active_positions_count': 0,
            'timestamp': get_iran_time().isoformat(),
            'update_type': 'live_prices'
        })
    
    # Check if user has paper trading positions that need full monitoring for TP/SL triggers
    has_paper_trades = False
    for trade_id, config in user_trade_configs.get(chat_id, {}).items():
        if config.status == "active" and getattr(config, 'paper_trading_mode', False):
            has_paper_trades = True
            break
    
    if has_paper_trades:
        # Run full position updates for paper trading (includes TP/SL monitoring)
        update_all_positions_with_live_data(chat_id)
    else:
        # Use optimized lightweight monitoring - only checks break-even positions  
        update_positions_lightweight()
    
    # Return only essential price and P&L data for fast updates
    live_data = {}
    total_unrealized_pnl = 0.0
    active_positions_count = 0
    
    if chat_id in user_trade_configs:
        for trade_id, config in user_trade_configs[chat_id].items():
            if config.status in ["active", "pending"] and config.symbol:
                # Calculate percentage change and ROE
                roe_percentage = 0.0
                price_change_percentage = 0.0
                
                if config.entry_price and config.current_price and config.entry_price > 0:
                    raw_change = (config.current_price - config.entry_price) / config.entry_price
                    price_change_percentage = raw_change * 100
                    
                    # Apply side adjustment for ROE calculation
                    if config.side == "short":
                        roe_percentage = -raw_change * config.leverage * 100
                    else:
                        roe_percentage = raw_change * config.leverage * 100
                
                live_data[trade_id] = {
                    'current_price': config.current_price,
                    'unrealized_pnl': config.unrealized_pnl,
                    'realized_pnl': getattr(config, 'realized_pnl', 0) or 0,
                    'total_pnl': (config.unrealized_pnl or 0) + (getattr(config, 'realized_pnl', 0) or 0),
                    'roe_percentage': round(roe_percentage, 2),
                    'price_change_percentage': round(price_change_percentage, 2),
                    'status': config.status
                }
                
                if config.status == "active":
                    total_unrealized_pnl += config.unrealized_pnl
                    active_positions_count += 1
                elif config.status == "pending":
                    active_positions_count += 1
    
    # Calculate total realized P&L from closed positions for comprehensive total
    total_realized_pnl = 0.0
    if chat_id in user_trade_configs:
        for config in user_trade_configs[chat_id].values():
            if config.status == "stopped" and hasattr(config, 'final_pnl') and config.final_pnl is not None:
                total_realized_pnl += config.final_pnl
            # Also include partial realized P&L from active positions (from partial TPs)
            elif config.status == "active" and hasattr(config, 'realized_pnl') and config.realized_pnl is not None:
                total_realized_pnl += config.realized_pnl
    
    # Calculate total P&L (realized + unrealized)
    total_pnl = total_realized_pnl + total_unrealized_pnl

    return jsonify({
        'positions': live_data,
        'total_unrealized_pnl': total_unrealized_pnl,
        'total_realized_pnl': total_realized_pnl,
        'total_pnl': total_pnl,
        'active_positions_count': active_positions_count,
        'timestamp': get_iran_time().isoformat(),
        'update_type': 'live_prices'
    })

@app.route('/api/trading/new')
def api_trading_new():
    """Create new trading configuration"""
    user_id = get_user_id_from_request()
    
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
        user_id = Environment.DEFAULT_TEST_USER_ID
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({'error': 'Invalid user ID format'}), 400
    
    # Initialize user environment (will use cache if available)
    initialize_user_environment(chat_id)
    
    user_trade_list = []
    
    # Get user configs from memory (already loaded by initialize_user_environment)
    user_configs = user_trade_configs.get(chat_id, {})
    
    if user_configs:
        for trade_id, config in user_configs.items():
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
                'realized_pnl': getattr(config, 'realized_pnl', 0.0),  # Include realized P&L from triggered TPs
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
    
    # Use cached initialization for both Vercel and Replit
    initialize_user_environment(chat_id)
    user_configs = user_trade_configs.get(chat_id, {})
    
    if user_configs and trade_id in user_configs:
        config = user_configs[trade_id]
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
            if 'amount' in trade_data and abs(float(trade_data['amount']) - float(config.amount)) > 0.0001:
                core_param_changes.append('amount')
            if 'leverage' in trade_data and int(trade_data['leverage']) != int(config.leverage):
                core_param_changes.append('leverage')
            if 'entry_type' in trade_data and trade_data['entry_type'] != config.entry_type:
                core_param_changes.append('entry_type')
            if 'entry_price' in trade_data:
                new_entry_price = float(trade_data['entry_price']) if trade_data['entry_price'] else 0.0
                current_entry_price = float(config.entry_price) if config.entry_price else 0.0
                # Use a small tolerance for float comparison to avoid precision issues
                if abs(new_entry_price - current_entry_price) > 0.0001:
                    logging.debug(f"Entry price change detected for trade {trade_id}: {current_entry_price} -> {new_entry_price}")
                    core_param_changes.append('entry_price')
            
            if core_param_changes:
                logging.warning(f"Attempted to modify core parameters {core_param_changes} for active trade {trade_id}. Changes rejected for safety.")
                return jsonify({
                    'error': f"Cannot modify core trade parameters ({', '.join(core_param_changes)}) for active trades. Only take profits, stop loss, break-even, and trailing stop can be modified.",
                    'active_trade': True,
                    'rejected_changes': core_param_changes,
                    'message': 'For active positions, you can only edit risk management settings (TP/SL levels, breakeven, trailing stop).'
                }), 400
            
            logging.info(f"Allowing risk management parameter modifications for active trade {trade_id} (core parameters unchanged)")
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
        
        # Save to database
        save_trade_to_db(chat_id, config)
        
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
        
        if not user_id:
            return jsonify(create_validation_error("User ID", None, "A valid user ID is required")), 400
        
        if not trade_id:
            return jsonify(create_validation_error("Trade ID", None, "A valid trade ID is required")), 400
        
        chat_id = int(user_id)
        
        if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
            from api.error_handler import TradingError, ErrorCategory, ErrorSeverity
            error = TradingError(
                category=ErrorCategory.VALIDATION_ERROR,
                severity=ErrorSeverity.MEDIUM,
                technical_message=f"Trade {trade_id} not found for user {chat_id}",
                user_message="The trade configuration you're trying to execute was not found.",
                suggestions=[
                    "Check that the trade ID is correct",
                    "Refresh the page to reload your trades",
                    "Create a new trade configuration if needed"
                ]
            )
            return jsonify(error.to_dict()), 404
        
        config = user_trade_configs[chat_id][trade_id]
        
        # Validate configuration
        if not config.is_complete():
            from api.error_handler import TradingError, ErrorCategory, ErrorSeverity
            error = TradingError(
                category=ErrorCategory.VALIDATION_ERROR,
                severity=ErrorSeverity.HIGH,
                technical_message=f"Incomplete trade configuration for {config.symbol}",
                user_message="Your trade setup is missing some important information.",
                suggestions=[
                    "Check that you've set the trading symbol",
                    "Verify you've selected long or short direction",
                    "Make sure you've set the trade amount",
                    "Ensure take profit and stop loss are configured"
                ]
            )
            return jsonify(error.to_dict()), 400
        
        # Get current market price from Toobit exchange (where trade will be executed)
        current_market_price = get_live_market_price(config.symbol, user_id=chat_id, prefer_exchange=True)
        
        # For limit orders, we'll place them directly on the exchange and let the exchange handle execution
        # No need to monitor prices manually - the exchange will execute when price is reached
        
        # Check if user is in paper trading mode
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(chat_id),
            is_active=True
        ).first()
        
        # Default to paper mode if no credentials exist
        # Check for manual paper trading preference
        manual_paper_mode = user_paper_trading_preferences.get(chat_id, False)
        
        is_paper_mode = (manual_paper_mode or 
                       not user_creds or 
                       user_creds.testnet_mode or 
                       not user_creds.has_credentials())
        
        execution_success = False
        client = None  # Initialize client variable
        
        if is_paper_mode:
            # PAPER TRADING MODE - Simulate execution with real price monitoring
            logging.info(f"Paper Trading: Executing simulated trade for user {chat_id}: {config.symbol} {config.side}")
            execution_success = True
            
            # Simulate order placement with paper trading IDs
            mock_order_id = f"paper_{uuid.uuid4().hex[:8]}"
            config.exchange_order_id = mock_order_id
            config.exchange_client_order_id = f"paper_client_{mock_order_id}"
            
        else:
            # REAL TRADING - Execute on Toobit exchange
            if not user_creds or not user_creds.has_credentials():
                return jsonify({'error': 'API credentials required for real trading. Please set up your Toobit API keys.'}), 400
            
            try:
                # Create Toobit client
                client = ToobitClient(
                    api_key=user_creds.get_api_key(),
                    api_secret=user_creds.get_api_secret(),
                    passphrase=user_creds.get_passphrase(),
                    testnet=user_creds.testnet_mode
                )
                
                # Test connection first - balance endpoint works even if ticker endpoints don't
                try:
                    balance_data = client.get_account_balance()
                    logging.info(f"Toobit connection test successful. Balance data: {balance_data}")
                except Exception as conn_error:
                    logging.error(f"Toobit connection test failed: {conn_error}")
                    return jsonify({'error': f'Exchange connection failed: {str(conn_error)}'}), 400
                
                # Calculate position size for order
                position_value = config.amount * config.leverage
                position_size = position_value / current_market_price
                
                # Determine order type and parameters
                order_type = "market" if config.entry_type == "market" else "limit"
                order_side = "buy" if config.side == "long" else "sell"
                order_price = config.entry_price if config.entry_type == "limit" else None
                
                # Place the main position order on exchange
                order_result = client.place_order(
                    symbol=config.symbol,
                    side=order_side,
                    order_type=order_type,
                    quantity=str(position_size),
                    price=str(order_price) if order_price else None,
                    timeInForce="GTC",
                    leverage=config.leverage
                )
                
                if not order_result:
                    return jsonify({'error': 'Failed to place order on exchange. Please check your API keys and try again.'}), 500
                
                execution_success = True
                logging.info(f"Order placed on Toobit: {order_result}")
                
                # Store exchange order ID
                config.exchange_order_id = order_result.get('orderId')
                config.exchange_client_order_id = order_result.get('clientOrderId')
                
            except Exception as e:
                logging.error(f"Exchange order placement failed: {e}")
                return jsonify({'error': f'Exchange order failed: {str(e)}'}), 500
        
        # Calculate common values needed for both paper and real trading
        position_value = config.amount * config.leverage
        position_size = position_value / current_market_price
        order_side = "buy" if config.side == "long" else "sell"
        
        # Update trade configuration - status depends on order type
        if config.entry_type == "limit":
            # Limit orders start as pending until filled by exchange
            config.status = "pending"
        else:
            # Market orders are immediately active
            config.status = "active"
        
        # Mark as paper trading if in paper mode and initialize monitoring
        if is_paper_mode:
            config.paper_trading_mode = True
            # Initialize paper trading monitoring for market orders immediately
            if config.entry_type == "market":
                initialize_paper_trading_monitoring(config)
            logging.info(f"Paper Trading: Position opened for {config.symbol} {config.side} - Real-time monitoring enabled")
            
        # CRITICAL FIX: Store original amounts when trade is first executed
        # This ensures TP profit calculations remain accurate even after partial closures
        if not hasattr(config, 'original_amount'):
            config.original_amount = config.amount
        if not hasattr(config, 'original_margin'):
            config.original_margin = calculate_position_margin(config.original_amount, config.leverage)
            
        config.position_margin = calculate_position_margin(config.amount, config.leverage)
        config.position_value = position_value
        config.position_size = position_size
        
        # NOTE: Exchange-native TP/SL orders are handled below in lines 1899-1960
        # This enables the optimized lightweight monitoring system
        
        if config.entry_type == "market" or config.entry_price is None:
            config.current_price = current_market_price
            config.entry_price = current_market_price
        else:
            config.current_price = current_market_price
        
        config.unrealized_pnl = 0.0
        
        # Save to database
        save_trade_to_db(chat_id, config)
        
        # Place TP/SL orders - handle differently for market vs limit orders
        if execution_success:
            try:
                tp_sl_orders = []
                
                # Calculate TP/SL prices
                tp_sl_data = calculate_tp_sl_prices_and_amounts(config)
                
                if is_paper_mode:
                    # PAPER TRADING MODE - Simulate TP/SL orders with real price monitoring
                    if config.take_profits and tp_sl_data.get('take_profits'):
                        mock_tp_sl_orders = []
                        # Store detailed TP/SL data for paper trading monitoring
                        config.paper_tp_levels = []
                        for i, tp_data in enumerate(tp_sl_data['take_profits']):
                            mock_order_id = f"paper_tp_{i+1}_{uuid.uuid4().hex[:6]}"
                            mock_tp_sl_orders.append(mock_order_id)
                            # Store TP level details for monitoring
                            config.paper_tp_levels.append({
                                'order_id': mock_order_id,
                                'level': i + 1,
                                'price': tp_data['price'],
                                'percentage': tp_data['percentage'],
                                'allocation': tp_data['allocation'],
                                'triggered': False
                            })
                        
                        if config.stop_loss_percent > 0:
                            sl_order_id = f"paper_sl_{uuid.uuid4().hex[:6]}"
                            mock_tp_sl_orders.append(sl_order_id)
                            # Store SL details for monitoring
                            config.paper_sl_data = {
                                'order_id': sl_order_id,
                                'price': tp_sl_data['stop_loss']['price'],
                                'percentage': config.stop_loss_percent,
                                'triggered': False
                            }
                        
                        config.exchange_tp_sl_orders = mock_tp_sl_orders
                        config.paper_trading_mode = True  # Flag for paper trading monitoring
                        logging.info(f"Paper Trading: Simulated {len(mock_tp_sl_orders)} TP/SL orders with real-time monitoring")
                else:
                    # Real TP/SL orders on exchange
                    if config.take_profits and tp_sl_data.get('take_profits'):
                        tp_orders_to_place = []
                        for tp_data in tp_sl_data['take_profits']:
                            tp_quantity = position_size * (tp_data['allocation'] / 100)
                            tp_orders_to_place.append({
                                'price': tp_data['price'],
                                'quantity': tp_quantity,
                                'percentage': tp_data['percentage'],
                                'allocation': tp_data['allocation']
                            })
                        
                        sl_price = None
                        if config.stop_loss_percent > 0 and tp_sl_data.get('stop_loss'):
                            sl_price = str(tp_sl_data['stop_loss']['price'])
                        
                        # For limit orders, place TP/SL as conditional orders that activate when main order fills
                        # For market orders, place TP/SL immediately since position is already open
                        if not is_paper_mode and client is not None:
                            if config.entry_type == "limit":
                                # For limit orders, TP/SL will be placed once the main order is filled
                                # Store the TP/SL data to place later when order fills
                                config.pending_tp_sl_data = {
                                    'take_profits': tp_orders_to_place,
                                    'stop_loss_price': sl_price
                                }
                                logging.info(f"TP/SL orders configured to place when limit order fills")
                            else:
                                # For market orders, place TP/SL immediately
                                tp_sl_orders = client.place_multiple_tp_sl_orders(
                                    symbol=config.symbol,
                                    side=order_side,
                                    total_quantity=str(position_size),
                                    take_profits=tp_orders_to_place,
                                    stop_loss_price=sl_price
                                )
                                
                                config.exchange_tp_sl_orders = tp_sl_orders
                                logging.info(f"Placed {len(tp_sl_orders)} TP/SL orders on exchange")
                
            except Exception as e:
                logging.error(f"Failed to place TP/SL orders: {e}")
                # Continue execution - main position was successful
        
        logging.info(f"Trade executed: {config.symbol} {config.side} at ${config.entry_price} (entry type: {config.entry_type})")
        
        # Initialize paper trading balance if needed
        if is_paper_mode:
            if chat_id not in user_paper_balances:
                user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
                logging.info(f"Paper Trading: Initialized balance of ${TradingConfig.DEFAULT_TRIAL_BALANCE:,.2f} for user {chat_id}")
            
            # Check if user has sufficient paper balance
            if user_paper_balances[chat_id] < config.amount:
                return jsonify({
                    'error': f'Insufficient paper trading balance. Available: ${user_paper_balances[chat_id]:,.2f}, Required: ${config.amount:,.2f}'
                }), 400
            
            # Deduct margin from paper balance
            user_paper_balances[chat_id] -= config.amount
            logging.info(f"Paper Trading: Deducted ${config.amount:,.2f} margin. Remaining balance: ${user_paper_balances[chat_id]:,.2f}")
        
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
            'timestamp': get_iran_time().isoformat(),
            'status': f'executed_{"paper" if is_paper_mode else "live"}',
            'trading_mode': 'paper' if is_paper_mode else 'live'
        })
        
        bot_status['total_trades'] += 1
        
        trade_mode = "Paper Trade" if is_paper_mode else "Live Trade"
        
        # Create appropriate message based on order type
        if config.entry_type == "limit":
            message = f'{trade_mode} limit order placed successfully: {config.symbol} {config.side.upper()} at ${config.entry_price:.4f}. Will execute when market reaches this price.'
        else:
            message = f'{trade_mode} executed successfully: {config.symbol} {config.side.upper()}'
        
        return jsonify({
            'success': True,
            'message': message,
            'paper_mode': is_paper_mode,
            'trade': {
                'trade_id': trade_id,
                'symbol': config.symbol,
                'side': config.side,
                'amount': config.amount,
                'leverage': config.leverage,
                'entry_price': config.entry_price,
                'current_price': config.current_price,
                'position_margin': config.position_margin,
                'position_size': config.position_size,
                'status': config.status,
                'exchange_order_id': getattr(config, 'exchange_order_id', None),
                'take_profits': config.take_profits,
                'stop_loss_percent': config.stop_loss_percent
            }
        })
        
    except Exception as e:
        # Handle specific error types with user-friendly messages
        error_str = str(e).lower()
        from api.error_handler import TradingError, ErrorCategory, ErrorSeverity
        
        if "insufficient balance" in error_str or "not enough funds" in error_str:
            error = TradingError(
                category=ErrorCategory.TRADING_ERROR,
                severity=ErrorSeverity.HIGH,
                technical_message=str(e),
                user_message="You don't have enough balance to place this trade.",
                suggestions=[
                    "Check your account balance",
                    "Reduce the trade amount or leverage",
                    "Deposit more funds to your account",
                    "Close other positions to free up margin"
                ]
            )
            return jsonify(error.to_dict()), 400
        elif "api key" in error_str or "unauthorized" in error_str or "authentication" in error_str:
            error = TradingError(
                category=ErrorCategory.AUTHENTICATION_ERROR,
                severity=ErrorSeverity.HIGH,
                technical_message=str(e),
                user_message="Your API credentials are invalid or have expired.",
                suggestions=[
                    "Check your API key and secret in Settings",
                    "Verify your credentials are still active",
                    "Make sure you're using the correct exchange",
                    "Contact your exchange if the problem persists"
                ]
            )
            return jsonify(error.to_dict()), 401
        elif "symbol" in error_str and ("not found" in error_str or "invalid" in error_str):
            error = TradingError(
                category=ErrorCategory.MARKET_ERROR,
                severity=ErrorSeverity.MEDIUM,
                technical_message=str(e),
                user_message="The trading symbol is not available or invalid.",
                suggestions=[
                    "Check the symbol name (e.g., BTCUSDT, ETHUSDT)",
                    "Make sure the symbol is supported on your exchange",
                    "Try a different trading pair",
                    "Refresh the symbol list"
                ]
            )
            return jsonify(error.to_dict()), 400
        else:
            return jsonify(handle_error(e, "executing trade")), 500

@app.route('/api/user-credentials')
@app.route('/api/credentials-status')
def get_user_credentials():
    """Get user API credentials status"""
    user_id = request.args.get('user_id')
    if not user_id or user_id == 'undefined':
        user_id = Environment.DEFAULT_TEST_USER_ID  # Demo user
    
    try:
        # Check enhanced cache first for user credentials
        cached_result = enhanced_cache.get_user_credentials(str(user_id))
        if cached_result:
            cached_creds, cache_info = cached_result
            # Re-attach the cached object to current session to prevent session binding errors
            user_creds = db.session.merge(cached_creds)
            # Retrieved credentials from cache - removed debug log for cleaner output
        else:
            # Cache miss - load from database
            user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
            # Update cache with fresh data
            if user_creds:
                enhanced_cache.set_user_credentials(str(user_id), user_creds)
                # Credentials cached - removed debug log for cleaner output
        
        if user_creds:
            api_key = user_creds.get_api_key()
            api_key_preview = f"{api_key[:8]}...{api_key[-4:]}" if api_key and len(api_key) > 12 else "****"
            
            return jsonify({
                'has_credentials': user_creds.has_credentials(),
                'exchange': user_creds.exchange_name,
                'api_key_preview': api_key_preview,
                'testnet_mode': user_creds.testnet_mode,
                'supports_testnet': user_creds.exchange_name.lower() != 'toobit',  # Toobit doesn't support testnet
                'is_active': user_creds.is_active,
                'last_used': user_creds.last_used.isoformat() if user_creds.last_used else None,
                'created_at': user_creds.created_at.isoformat()
            })
        else:
            return jsonify({
                'has_credentials': False,
                'exchange': None,
                'api_key_preview': None,
                'testnet_mode': True,
                'supports_testnet': True,  # Default to true for unknown exchanges
                'is_active': False,
                'last_used': None,
                'created_at': None
            })
    except Exception as e:
        logging.error(f"Error getting user credentials: {str(e)}")
        return jsonify(handle_error(e, "getting user credentials")), 500

@app.route('/api/save-credentials', methods=['POST'])
def save_credentials():
    """Save user API credentials"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
        user_id = data.get('user_id', Environment.DEFAULT_TEST_USER_ID)
        exchange = data.get('exchange', 'toobit')
        api_key = (data.get('api_key') or '').strip()
        api_secret = (data.get('api_secret') or '').strip()
        passphrase = (data.get('passphrase') or '').strip()
        
        if not api_key or not api_secret:
            return jsonify(create_validation_error(
                "API credentials", 
                "Both API key and secret are required",
                "Valid API key and secret from your exchange"
            )), 400
        
        if len(api_key) < 10 or len(api_secret) < 10:
            return jsonify(create_validation_error(
                "API credentials",
                "API credentials seem too short", 
                "API key and secret should be at least 10 characters"
            )), 400
        
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
        
        # Handle testnet mode setting - Toobit doesn't support testnet
        if exchange.lower() == 'toobit':
            user_creds.testnet_mode = False  # Toobit only supports mainnet
        elif 'testnet_mode' in data:
            user_creds.testnet_mode = bool(data['testnet_mode'])
        else:
            # Default to testnet for other exchanges that support it
            user_creds.testnet_mode = True
        
        db.session.commit()
        
        # Invalidate cache to ensure fresh data on next request
        enhanced_cache.set_user_credentials(str(user_id), user_creds)
        # Credentials cache updated - removed debug log for cleaner output
        
        return jsonify(create_success_response(
            'Credentials saved successfully',
            {
                'exchange': exchange,
                'testnet_mode': user_creds.testnet_mode
            }
        ))
        
    except Exception as e:
        logging.error(f"Error saving credentials: {str(e)}")
        db.session.rollback()
        return jsonify(handle_error(e, "saving credentials")), 500

@app.route('/api/delete-credentials', methods=['POST'])
def delete_credentials():
    """Delete user API credentials"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
        user_id = data.get('user_id', Environment.DEFAULT_TEST_USER_ID)
        
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        if not user_creds:
            return jsonify({'error': 'No credentials found'}), 404
        
        db.session.delete(user_creds)
        db.session.commit()
        
        # Invalidate cache after deletion
        enhanced_cache.invalidate_user_data(str(user_id))
        # Cache invalidated - removed debug log for cleaner output
        
        return jsonify({
            'success': True,
            'message': 'Credentials deleted successfully'
        })
        
    except Exception as e:
        logging.error(f"Error deleting credentials: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to delete credentials'}), 500

@app.route('/api/toggle-testnet', methods=['POST'])
def toggle_testnet():
    """Toggle between testnet and mainnet modes"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
        user_id = data.get('user_id', Environment.DEFAULT_TEST_USER_ID)
        testnet_mode = bool(data.get('testnet_mode', True))
        
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        if not user_creds:
            return jsonify({'error': 'No credentials found. Please set up API keys first.'}), 404
        
        # Don't allow testnet mode for Toobit since it doesn't support it
        if user_creds.exchange_name.lower() == 'toobit' and testnet_mode:
            return jsonify({'error': 'Toobit exchange does not support testnet mode. Only live trading is available.'}), 400
        
        user_creds.testnet_mode = testnet_mode
        db.session.commit()
        
        # Update cache with modified credentials
        enhanced_cache.set_user_credentials(str(user_id), user_creds)
        # Updated credentials cache after testnet toggle - removed debug log for cleaner output
        
        mode_text = "testnet" if testnet_mode else "mainnet (REAL TRADING)"
        warning = ""
        if not testnet_mode:
            warning = "⚠️ WARNING: You are now in MAINNET mode. Real money will be used for trades!"
        
        return jsonify({
            'success': True,
            'message': f'Successfully switched to {mode_text}',
            'testnet_mode': testnet_mode,
            'warning': warning
        })
        
    except Exception as e:
        logging.error(f"Error toggling testnet mode: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to toggle testnet mode'}), 500

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
        
        # Get user credentials to determine if we're in paper mode or real trading
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(chat_id),
            is_active=True
        ).first()
        
        # Default to paper mode if no credentials exist
        # Check for manual paper trading preference
        manual_paper_mode = user_paper_trading_preferences.get(chat_id, False)
        
        is_paper_mode = (manual_paper_mode or 
                       not user_creds or 
                       user_creds.testnet_mode or 
                       not user_creds.has_credentials())
        
        if is_paper_mode:
            # PAPER TRADING - Simulate closing the position
            logging.info(f"Closing paper trade for user {chat_id}: {config.symbol} {config.side}")
            
            # Simulate cancelling paper TP/SL orders
            if hasattr(config, 'exchange_tp_sl_orders') and config.exchange_tp_sl_orders:
                cancelled_orders = len(config.exchange_tp_sl_orders)
                logging.info(f"Simulated cancellation of {cancelled_orders} TP/SL orders in paper mode")
                
        else:
            # REAL TRADING - Close position on Toobit exchange
            try:
                # Verify credentials are available
                if not user_creds or not user_creds.has_credentials():
                    return jsonify({'error': 'API credentials not available for live trading'}), 400
                
                # Create Toobit client
                client = ToobitClient(
                    api_key=user_creds.get_api_key(),
                    api_secret=user_creds.get_api_secret(),
                    passphrase=user_creds.get_passphrase(),
                    testnet=user_creds.testnet_mode
                )
                
                # Close position on exchange
                close_side = "sell" if config.side == "long" else "buy"
                close_order = client.place_order(
                    symbol=config.symbol,
                    side=close_side,
                    order_type="market",
                    quantity=str(config.position_size),
                    reduceOnly=True
                )
                
                if not close_order:
                    return jsonify({'error': 'Failed to close position on exchange. Please check your connection and try again.'}), 500
                
                logging.info(f"Position closed on Toobit: {close_order}")
                
                # Cancel any remaining TP/SL orders on exchange
                if hasattr(config, 'exchange_tp_sl_orders') and config.exchange_tp_sl_orders:
                    for tp_sl_order in config.exchange_tp_sl_orders:
                        order_id = tp_sl_order.get('order', {}).get('orderId')
                        if order_id:
                            try:
                                client.cancel_order(str(order_id))
                                logging.info(f"Cancelled TP/SL order: {order_id}")
                            except Exception as cancel_error:
                                logging.warning(f"Failed to cancel order {order_id}: {cancel_error}")
                
            except Exception as e:
                logging.error(f"Exchange position closure failed: {e}")
                return jsonify({'error': f'Exchange closure failed: {str(e)}'}), 500
        
        # Update trade configuration
        final_pnl = config.unrealized_pnl + getattr(config, 'realized_pnl', 0.0)
        config.status = "stopped"
        config.final_pnl = final_pnl
        config.closed_at = get_iran_time().isoformat()
        config.unrealized_pnl = 0.0
        
        # Save updated status to database
        save_trade_to_db(chat_id, config)
        
        # Log trade closure
        bot_trades.append({
            'id': len(bot_trades) + 1,
            'user_id': str(chat_id),
            'trade_id': trade_id,
            'symbol': config.symbol,
            'side': config.side,
            'amount': config.amount,
            'final_pnl': final_pnl,
            'timestamp': get_iran_time().isoformat(),
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

@app.route('/api/close-all-trades', methods=['POST'])
def close_all_trades():
    """Close all active trades for a user"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'error': 'User ID required'}), 400
        
        chat_id = int(user_id)
        
        if chat_id not in user_trade_configs:
            return jsonify({'success': True, 'message': 'No trades to close', 'closed_count': 0})
        
        # Find all active trades
        active_trades = []
        for trade_id, config in user_trade_configs[chat_id].items():
            if config.status == "active":
                active_trades.append((trade_id, config))
        
        if not active_trades:
            return jsonify({'success': True, 'message': 'No active trades to close', 'closed_count': 0})
        
        closed_count = 0
        total_final_pnl = 0.0
        
        # Get user credentials to determine if we're in paper mode or real trading
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(chat_id),
            is_active=True
        ).first()
        
        # Default to paper mode if no credentials exist
        # Check for manual paper trading preference
        manual_paper_mode = user_paper_trading_preferences.get(chat_id, False)
        
        is_paper_mode = (manual_paper_mode or 
                       not user_creds or 
                       user_creds.testnet_mode or 
                       not user_creds.has_credentials())
        
        client = None
        if not is_paper_mode and user_creds and user_creds.has_credentials():
            # Create Toobit client for real trading
            client = ToobitClient(
                api_key=user_creds.get_api_key(),
                api_secret=user_creds.get_api_secret(),
                passphrase=user_creds.get_passphrase(),
                testnet=user_creds.testnet_mode
            )
        
        # Close each active trade
        for trade_id, config in active_trades:
            try:
                if is_paper_mode:
                    # PAPER TRADING - Simulate closing the position
                    logging.info(f"Closing paper trade {trade_id} for user {chat_id}: {config.symbol} {config.side}")
                    
                    # Simulate cancelling paper TP/SL orders
                    if hasattr(config, 'exchange_tp_sl_orders') and config.exchange_tp_sl_orders:
                        cancelled_orders = len(config.exchange_tp_sl_orders)
                        logging.info(f"Simulated cancellation of {cancelled_orders} TP/SL orders for trade {trade_id} in paper mode")
                else:
                    # REAL TRADING - Close position on exchange
                    if client is None:
                        logging.warning(f"No client available for trade {trade_id} - falling back to paper mode")
                        continue
                    
                    close_side = "sell" if config.side == "long" else "buy"
                    close_order = client.place_order(
                        symbol=config.symbol,
                        side=close_side,
                        order_type="market",
                        quantity=str(config.position_size),
                        reduceOnly=True
                    )
                    
                    if close_order:
                        logging.info(f"Position closed on Toobit: {close_order}")
                        
                        # Cancel any remaining TP/SL orders on exchange
                        if client and hasattr(config, 'exchange_tp_sl_orders') and config.exchange_tp_sl_orders:
                            for tp_sl_order in config.exchange_tp_sl_orders:
                                order_id = tp_sl_order.get('order', {}).get('orderId')
                                if order_id:
                                    try:
                                        client.cancel_order(str(order_id))
                                    except Exception as cancel_error:
                                        logging.warning(f"Failed to cancel order {order_id}: {cancel_error}")
                    else:
                        logging.warning(f"Failed to close position for trade {trade_id} - exchange order failed")
                        continue
                
                # Update trade configuration
                final_pnl = config.unrealized_pnl + getattr(config, 'realized_pnl', 0.0)
                config.status = "stopped"
                config.final_pnl = final_pnl
                config.closed_at = get_iran_time().isoformat()
                config.unrealized_pnl = 0.0
                
                # Save updated status to database
                save_trade_to_db(chat_id, config)
                
                # Log trade closure
                bot_trades.append({
                    'id': len(bot_trades) + 1,
                    'user_id': str(chat_id),
                    'trade_id': trade_id,
                    'symbol': config.symbol,
                    'side': config.side,
                    'amount': config.amount,
                    'final_pnl': final_pnl,
                    'timestamp': get_iran_time().isoformat(),
                    'status': 'closed'
                })
                
                closed_count += 1
                total_final_pnl += final_pnl
                
            except Exception as trade_error:
                logging.error(f"Error closing trade {trade_id}: {str(trade_error)}")
                continue
        
        return jsonify({
            'success': True,
            'message': f'Successfully closed {closed_count} trades',
            'closed_count': closed_count,
            'total_final_pnl': total_final_pnl
        })
        
    except Exception as e:
        logging.error(f"Error closing all trades: {str(e)}")
        return jsonify({'error': 'Failed to close all trades'}), 500

@app.route('/api/delete-trade', methods=['POST'])
def delete_trade():
    """Delete a trade configuration"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        trade_id = data.get('trade_id')
        
        if not user_id:
            return jsonify(create_validation_error("User ID", None, "A valid user ID is required")), 400
        
        if not trade_id:
            return jsonify(create_validation_error("Trade ID", None, "A valid trade ID is required")), 400
        
        chat_id = int(user_id)
        
        if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
            from api.error_handler import TradingError, ErrorCategory, ErrorSeverity
            error = TradingError(
                category=ErrorCategory.VALIDATION_ERROR,
                severity=ErrorSeverity.MEDIUM,
                technical_message=f"Trade {trade_id} not found for user {chat_id}",
                user_message="The trade you're trying to delete was not found.",
                suggestions=[
                    "Check that the trade ID is correct",
                    "The trade may have already been deleted",
                    "Refresh the page to see current trades"
                ]
            )
            return jsonify(error.to_dict()), 404
        
        config = user_trade_configs[chat_id][trade_id]
        trade_name = config.get_display_name() if hasattr(config, 'get_display_name') else config.name
        
        # Remove from database first
        delete_trade_from_db(chat_id, trade_id)
        
        # Remove from configurations
        del user_trade_configs[chat_id][trade_id]
        
        # Remove from selected trade if it was selected
        if user_selected_trade.get(chat_id) == trade_id:
            if chat_id in user_selected_trade:
                del user_selected_trade[chat_id]
        
        return jsonify(create_success_response(
            f'Trade configuration "{trade_name}" deleted successfully',
            {'trade_id': trade_id, 'trade_name': trade_name}
        ))
        
    except Exception as e:
        # Handle specific database errors
        error_str = str(e).lower()
        from api.error_handler import TradingError, ErrorCategory, ErrorSeverity
        
        if "database" in error_str or "connection" in error_str:
            error = TradingError(
                category=ErrorCategory.DATABASE_ERROR,
                severity=ErrorSeverity.HIGH,
                technical_message=str(e),
                user_message="There was an issue accessing the database while deleting your trade.",
                suggestions=[
                    "Try again in a moment",
                    "Refresh the page to check if the trade was deleted",
                    "Contact support if this persists"
                ],
                retry_after=30
            )
            return jsonify(error.to_dict()), 500
        else:
            return jsonify(handle_error(e, "deleting trade")), 500

@app.route('/api/reset-history', methods=['POST'])
def reset_trade_history():
    """Reset all trade history and P&L for a user (keeps credentials)"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'error': 'User ID is required'}), 400
            
        chat_id = int(user_id)
        
        # Initialize user environment
        initialize_user_environment(chat_id)
        
        # Clear all trade configurations and history
        with app.app_context():
            # Delete all trade configurations from database (correct field name)
            TradeConfiguration.query.filter_by(telegram_user_id=str(chat_id)).delete()
            
            # Reset user trading session (keeps credentials but resets balance)
            session = UserTradingSession.query.filter_by(telegram_user_id=str(chat_id)).first()
            if session:
                # Reset session metrics but keep the existing session
                session.total_trades = 0
                session.successful_trades = 0
                session.failed_trades = 0
                session.total_volume = 0.0
                session.session_start = get_iran_time()
                session.session_end = None
            else:
                # Create new session if doesn't exist
                session = UserTradingSession()
                session.telegram_user_id = str(chat_id)
                session.session_start = get_iran_time()
                db.session.add(session)
            
            # Commit changes to database
            db.session.commit()
        
        # Clear in-memory data
        if chat_id in user_trade_configs:
            user_trade_configs[chat_id].clear()
        if chat_id in user_selected_trade:
            del user_selected_trade[chat_id]
        
        # Clear trade history from bot_trades list
        global bot_trades
        bot_trades = [trade for trade in bot_trades if trade.get('user_id') != str(chat_id)]
        
        logging.info(f"Trade history reset successfully for user {chat_id}")
        
        return jsonify({
            'success': True, 
            'message': 'Trade history and P&L reset successfully. Credentials preserved.'
        })
        
    except Exception as e:
        logging.error(f"Error resetting trade history: {e}")
        return jsonify({'error': 'Failed to reset trade history'}), 500

def verify_telegram_webhook(data):
    """Verify that the webhook request is from Telegram"""
    try:
        # Get client IP for logging
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        logging.info(f"Webhook request from IP: {client_ip}")
        
        # Telegram IP ranges for validation (optional strict checking)
        from config import SecurityConfig
        telegram_ip_ranges = SecurityConfig.TELEGRAM_IP_RANGES
        
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

@app.route('/paper-balance', methods=['GET'])
def get_paper_balance():
    """Get current paper trading balance for user"""
    user_id = get_user_id_from_request()
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({'error': 'Invalid user ID format'}), 400
    
    # Initialize balance if not exists
    if chat_id not in user_paper_balances:
        user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
    
    return jsonify({
        'paper_balance': user_paper_balances[chat_id],
        'initial_balance': TradingConfig.DEFAULT_TRIAL_BALANCE,
        'currency': 'USDT',
        'timestamp': get_iran_time().isoformat()
    })

@app.route('/reset-paper-balance', methods=['POST'])
def reset_paper_balance():
    """Reset paper trading balance to initial amount"""
    user_id = get_user_id_from_request()
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({'error': 'Invalid user ID format'}), 400
    
    # Reset to initial balance
    user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
    
    return jsonify({
        'success': True,
        'paper_balance': user_paper_balances[chat_id],
        'message': f'Paper trading balance reset to ${TradingConfig.DEFAULT_TRIAL_BALANCE:,.2f}',
        'timestamp': get_iran_time().isoformat()
    })



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
            bot_status['last_heartbeat'] = get_iran_time().isoformat()
            bot_status['total_messages'] += 1
            
            # Log the message
            bot_messages.append({
                'id': len(bot_messages) + 1,
                'user_id': str(user.get('id', 'unknown')),
                'username': user.get('username', 'Unknown'),
                'message': text,
                'timestamp': get_iran_time().isoformat(),
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
            bot_status['last_heartbeat'] = get_iran_time().isoformat()
            
            # Log the callback
            bot_messages.append({
                'id': len(bot_messages) + 1,
                'user_id': str(user.get('id', 'unknown')),
                'username': user.get('username', 'Unknown'),
                'message': f"[CALLBACK] {callback_data}",
                'timestamp': get_iran_time().isoformat(),
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
            start_time = time.time()
            # For price commands, try Toobit first, then fallback to other sources
            price = get_live_market_price(symbol, prefer_exchange=True)
            fetch_time = (time.time() - start_time) * 1000  # Convert to milliseconds
            
            # Get enhanced cache info
            cache_info = ""
            cached_result = enhanced_cache.get_price(symbol)
            if cached_result:
                _, source, cache_meta = cached_result
                if cache_meta.get('cached', False):
                    cache_info = f" (cached, {source})"
                else:
                    cache_info = f" ({source})"
            else:
                cache_info = " (live)"
            
            return f"💰 {symbol}: ${price:.4f}{cache_info}\n⚡ Fetched in {fetch_time:.0f}ms", None
        except Exception as e:
            logging.error(f"Error fetching live price for {symbol}: {e}")
            return f"❌ Could not fetch live price for {symbol}\nError: {str(e)}", None
    
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

# Enhanced caching system replaces basic price cache
# price_cache, cache_lock, and cache_ttl now handled by enhanced_cache
api_performance_metrics = {
    'binance': {'requests': 0, 'successes': 0, 'avg_response_time': 0, 'last_success': None},
    'coingecko': {'requests': 0, 'successes': 0, 'avg_response_time': 0, 'last_success': None},
    'cryptocompare': {'requests': 0, 'successes': 0, 'avg_response_time': 0, 'last_success': None}
}

# Thread pool for concurrent API requests
price_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="price_api")

def update_api_metrics(api_name, success, response_time):
    """Update API performance metrics"""
    metrics = api_performance_metrics[api_name]
    metrics['requests'] += 1
    if success:
        metrics['successes'] += 1
        metrics['last_success'] = datetime.utcnow()
        # Update rolling average response time
        if metrics['avg_response_time'] == 0:
            metrics['avg_response_time'] = response_time
        else:
            metrics['avg_response_time'] = (metrics['avg_response_time'] * 0.8) + (response_time * 0.2)

def get_api_priority():
    """Get API priority based on performance metrics"""
    apis = []
    for api_name, metrics in api_performance_metrics.items():
        if metrics['requests'] > 0:
            success_rate = metrics['successes'] / metrics['requests']
            score = success_rate * 100 - metrics['avg_response_time']
            apis.append((api_name, score))
        else:
            apis.append((api_name, 50))  # Default score for untested APIs
    
    # Sort by score (higher is better)
    apis.sort(key=lambda x: x[1], reverse=True)
    return [api[0] for api in apis]

@with_circuit_breaker('binance_api', failure_threshold=3, recovery_timeout=30)
def fetch_binance_price(symbol):
    """Fetch price from Binance API with circuit breaker protection"""
    start_time = time.time()
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        req.add_header('Accept', 'application/json')
        
        with urllib.request.urlopen(req, timeout=TimeConfig.FAST_API_TIMEOUT) as response:
            data = json.loads(response.read().decode())
        
        response_time = time.time() - start_time
        update_api_metrics('binance', True, response_time)
        
        price = float(data['price'])
        return price, 'binance'
    except Exception as e:
        response_time = time.time() - start_time
        update_api_metrics('binance', False, response_time)
        raise e

@with_circuit_breaker('coingecko_api', failure_threshold=4, recovery_timeout=45)
def fetch_coingecko_price(symbol):
    """Fetch price from CoinGecko API with circuit breaker protection"""
    start_time = time.time()
    try:
        # Extended symbol mapping with more pairs
        symbol_map = {
            'BTCUSDT': 'bitcoin', 'ETHUSDT': 'ethereum', 'BNBUSDT': 'binancecoin',
            'ADAUSDT': 'cardano', 'DOGEUSDT': 'dogecoin', 'SOLUSDT': 'solana',
            'DOTUSDT': 'polkadot', 'LINKUSDT': 'chainlink', 'LTCUSDT': 'litecoin',
            'MATICUSDT': 'matic-network', 'AVAXUSDT': 'avalanche-2', 'UNIUSDT': 'uniswap',
            'XRPUSDT': 'ripple', 'ALGOUSDT': 'algorand', 'ATOMUSDT': 'cosmos',
            'FTMUSDT': 'fantom', 'MANAUSDT': 'decentraland', 'SANDUSDT': 'the-sandbox',
            'AXSUSDT': 'axie-infinity', 'CHZUSDT': 'chiliz', 'ENJUSDT': 'enjincoin',
            'GMTUSDT': 'stepn', 'APTUSDT': 'aptos', 'NEARUSDT': 'near'
        }
        
        coin_id = symbol_map.get(symbol)
        if not coin_id:
            raise Exception(f"Symbol {symbol} not supported by CoinGecko")
            
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        req.add_header('Accept', 'application/json')
        
        with urllib.request.urlopen(req, timeout=TimeConfig.EXTENDED_API_TIMEOUT) as response:
            data = json.loads(response.read().decode())
        
        response_time = time.time() - start_time
        update_api_metrics('coingecko', True, response_time)
        
        price = float(data[coin_id]['usd'])
        return price, 'coingecko'
    except Exception as e:
        response_time = time.time() - start_time
        update_api_metrics('coingecko', False, response_time)
        raise e

@with_circuit_breaker('cryptocompare_api', failure_threshold=4, recovery_timeout=45)
def fetch_cryptocompare_price(symbol):
    """Fetch price from CryptoCompare API with circuit breaker protection"""
    start_time = time.time()
    try:
        base_symbol = symbol.replace('USDT', '').replace('BUSD', '').replace('USDC', '')
        url = f"https://min-api.cryptocompare.com/data/price?fsym={base_symbol}&tsyms=USD"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        req.add_header('Accept', 'application/json')
        
        with urllib.request.urlopen(req, timeout=TimeConfig.EXTENDED_API_TIMEOUT) as response:
            data = json.loads(response.read().decode())
        
        response_time = time.time() - start_time
        
        if 'USD' not in data:
            raise Exception(f"USD price not available for {base_symbol}")
            
        update_api_metrics('cryptocompare', True, response_time)
        
        price = float(data['USD'])
        return price, 'cryptocompare'
    except Exception as e:
        response_time = time.time() - start_time
        update_api_metrics('cryptocompare', False, response_time)
        raise e

@with_circuit_breaker('toobit_api', failure_threshold=3, recovery_timeout=60)
def get_toobit_price(symbol, user_id=None):
    """Get live price directly from Toobit exchange with circuit breaker protection"""
    try:
        # Ensure we're in Flask application context
        if not has_app_context():
            with app.app_context():
                return get_toobit_price(symbol, user_id)
        
        # Try to get user credentials to use their exchange connection
        if user_id:
            user_creds = UserCredentials.query.filter_by(
                telegram_user_id=str(user_id),
                is_active=True
            ).first()
            
            if user_creds and user_creds.has_credentials():
                client = ToobitClient(
                    api_key=user_creds.get_api_key(),
                    api_secret=user_creds.get_api_secret(),
                    passphrase=user_creds.get_passphrase(),
                    testnet=user_creds.testnet_mode
                )
                
                toobit_price = client.get_ticker_price(symbol)
                if toobit_price:
                    return toobit_price, 'toobit'
        
        # Fallback: Create anonymous client for public market data
        anonymous_client = ToobitClient("", "", "", testnet=False)
        toobit_price = anonymous_client.get_ticker_price(symbol)
        if toobit_price:
            return toobit_price, 'toobit'
            
        return None, None
    except Exception as e:
        logging.warning(f"Failed to get Toobit price for {symbol}: {e}")
        return None, None

def get_live_market_price(symbol, use_cache=True, user_id=None, prefer_exchange=True):
    """
    Enhanced price fetching that prioritizes the actual trading exchange (Toobit)
    """
    # Check enhanced cache first
    if use_cache:
        cached_result = enhanced_cache.get_price(symbol)
        if cached_result:
            price, source, cache_info = cached_result
# Using cached price - removed debug log for cleaner output
            return price
    
    # PRIORITY 1: Try Toobit exchange first (where trades are actually executed)
    if prefer_exchange:
        toobit_price, source = get_toobit_price(symbol, user_id)
        if toobit_price:
            # Cache the Toobit price using enhanced cache
            if use_cache:
                enhanced_cache.set_price(symbol, toobit_price, 'toobit')
            logging.info(f"Retrieved live price for {symbol} from Toobit exchange: ${toobit_price}")
            return toobit_price
    
    # Get optimal API order based on performance
    api_priority = get_api_priority()
    
    # Define API functions mapping
    api_functions = {
        'binance': fetch_binance_price,
        'coingecko': fetch_coingecko_price,
        'cryptocompare': fetch_cryptocompare_price
    }
    
    # Try concurrent requests for faster response
    futures = {}
    
    # Submit requests to top 2 performing APIs concurrently
    for api_name in api_priority[:2]:
        if api_name in api_functions:
            future = price_executor.submit(api_functions[api_name], symbol)
            futures[future] = api_name
    
    # Wait for first successful response
    success_price = None
    success_source = None
    
    try:
        for future in as_completed(futures, timeout=TimeConfig.QUICK_API_TIMEOUT):
            try:
                price, source = future.result()
                success_price = price
                success_source = source
                break
            except CircuitBreakerError as e:
                logging.warning(f"{futures[future]} circuit breaker is open: {str(e)}")
                continue
            except Exception as e:
                logging.warning(f"{futures[future]} API failed for {symbol}: {str(e)}")
                continue
    except Exception as e:
        logging.warning(f"Concurrent API requests timed out for {symbol}")
    
    # If concurrent requests failed, try remaining APIs sequentially
    if success_price is None:
        for api_name in api_priority[2:]:
            if api_name in api_functions:
                try:
                    price_result = api_functions[api_name](symbol)
                    if price_result and len(price_result) == 2:
                        success_price, success_source = price_result
                        break
                except CircuitBreakerError as e:
                    logging.warning(f"{api_name} circuit breaker is open: {str(e)}")
                    continue
                except Exception as e:
                    logging.warning(f"{api_name} API failed for {symbol}: {str(e)}")
                    continue
    
    if success_price is None:
        # No emergency fallback needed - enhanced cache handles stale data automatically
        raise Exception(f"Unable to fetch live market price for {symbol} from any source")
    
    # Cache the successful result using enhanced cache
    if use_cache and success_source:
        enhanced_cache.set_price(symbol, success_price, success_source)
    
    logging.info(f"Retrieved live price for {symbol} from {success_source}: ${success_price}")
    return success_price

def update_all_positions_with_live_data(user_id=None):
    """Enhanced batch update using Toobit exchange prices for accurate trading data"""
    # Collect unique symbols for batch processing - ONLY for active positions
    symbols_to_update = set()
    position_configs = []
    paper_trading_configs = []  # Separate tracking for paper trades
    
    for uid, trades in user_trade_configs.items():
        for trade_id, config in trades.items():
            # Include both real and paper trading positions for monitoring
            if config.symbol and (config.status == "active" or config.status == "configured" or config.status == "pending"):
                symbols_to_update.add(config.symbol)
                position_configs.append((uid, trade_id, config))
                
                # Track paper trading positions separately for enhanced monitoring
                if getattr(config, 'paper_trading_mode', False):
                    paper_trading_configs.append((uid, trade_id, config))
    
    # Batch fetch prices for all symbols concurrently from Toobit exchange
    symbol_prices = {}
    if symbols_to_update:
        futures = {}
        for symbol in symbols_to_update:
            # Prioritize Toobit exchange for accurate trading prices
            future = price_executor.submit(get_live_market_price, symbol, True, user_id, True)
            futures[future] = symbol
        
        # Collect results with timeout
        for future in as_completed(futures, timeout=TimeConfig.PRICE_API_TIMEOUT):
            symbol = futures[future]
            try:
                price = future.result()
                symbol_prices[symbol] = price
            except Exception as e:
                logging.warning(f"Failed to update price for {symbol}: {e}")
                # Use cached price if available from enhanced cache
                cached_result = enhanced_cache.get_price(symbol)
                if cached_result:
                    symbol_prices[symbol] = cached_result[0]  # Get price from cache result
    
    # Update all positions with fetched prices
    for user_id, trade_id, config in position_configs:
        if config.symbol in symbol_prices:
            try:
                config.current_price = symbol_prices[config.symbol]
                
                # PAPER TRADING: Enhanced monitoring for simulated trades
                if getattr(config, 'paper_trading_mode', False):
                    process_paper_trading_position(user_id, trade_id, config)
                
                # Check pending limit orders for execution (both real and paper)
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
                        
                        trading_mode = "Paper" if getattr(config, 'paper_trading_mode', False) else "Live"
                        logging.info(f"{trading_mode} Trading: Limit order executed: {config.symbol} {config.side} at ${config.entry_price} (market reached: ${config.current_price})")
                        
                        # For paper trading, initialize TP/SL monitoring after limit order execution
                        if getattr(config, 'paper_trading_mode', False):
                            initialize_paper_trading_monitoring(config)
                        
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
                            'timestamp': get_iran_time().isoformat(),
                            'status': f'executed_{"paper" if getattr(config, "paper_trading_mode", False) else "live"}',
                            'trading_mode': trading_mode.lower()
                        })
                        
                        bot_status['total_trades'] += 1
                
                # Recalculate P&L for active positions and configured trades with entry prices
                # Skip comprehensive monitoring for paper trades as they have dedicated processing
                if ((config.status in ["active", "configured"]) and config.entry_price and config.current_price and 
                    not getattr(config, 'paper_trading_mode', False)):
                    config.unrealized_pnl = calculate_unrealized_pnl(
                        config.entry_price, config.current_price,
                        config.amount, config.leverage, config.side
                    )
                    
                    # Check stop-loss threshold (Enhanced with break-even logic)
                    stop_loss_triggered = False
                    
                    # Check break-even stop loss first
                    if hasattr(config, 'breakeven_sl_triggered') and config.breakeven_sl_triggered and hasattr(config, 'breakeven_sl_price'):
                        # Break-even stop loss - trigger when price moves against position from entry price
                        if config.side == "long" and config.current_price <= config.breakeven_sl_price:
                            stop_loss_triggered = True
                            logging.warning(f"BREAK-EVEN STOP-LOSS TRIGGERED: {config.symbol} {config.side} position for user {user_id} - Price ${config.current_price} <= Break-even ${config.breakeven_sl_price}")
                        elif config.side == "short" and config.current_price >= config.breakeven_sl_price:
                            stop_loss_triggered = True
                            logging.warning(f"BREAK-EVEN STOP-LOSS TRIGGERED: {config.symbol} {config.side} position for user {user_id} - Price ${config.current_price} >= Break-even ${config.breakeven_sl_price}")
                    
                    # Check regular stop loss if break-even not triggered
                    elif config.stop_loss_percent > 0 and config.unrealized_pnl < 0:
                        # Calculate current loss percentage based on margin
                        loss_percentage = abs(config.unrealized_pnl / config.amount) * 100
                        
                        if loss_percentage >= config.stop_loss_percent:
                            stop_loss_triggered = True
                            logging.warning(f"STOP-LOSS TRIGGERED: {config.symbol} {config.side} position for user {user_id} - Loss: {loss_percentage:.2f}% >= {config.stop_loss_percent}%")
                    
                    if stop_loss_triggered:
                        # Close the position
                        config.status = "stopped"
                        # Include both unrealized P&L and any realized P&L from partial TPs
                        config.final_pnl = config.unrealized_pnl + getattr(config, 'realized_pnl', 0.0)
                        config.closed_at = get_iran_time().isoformat()
                        config.unrealized_pnl = 0.0
                        
                        # Save to database
                        save_trade_to_db(user_id, config)
                        
                        # Log trade closure
                        bot_trades.append({
                            'id': len(bot_trades) + 1,
                            'user_id': str(user_id),
                            'trade_id': trade_id,
                            'symbol': config.symbol,
                            'side': config.side,
                            'amount': config.amount,
                            'final_pnl': config.final_pnl,
                            'timestamp': get_iran_time().isoformat(),
                            'status': 'stop_loss_triggered'
                        })
                        
                        logging.info(f"Position auto-closed: {config.symbol} {config.side} - Final P&L: ${config.final_pnl:.2f}")
                    
                    # Check take profit targets (ALSO MISSING LOGIC)
                    elif config.take_profits and config.unrealized_pnl > 0:
                        # Calculate current profit percentage based on margin
                        profit_percentage = (config.unrealized_pnl / config.amount) * 100
                        
                        # Check each TP level
                        for i, tp in enumerate(config.take_profits):
                            tp_percentage = tp.get('percentage', 0) if isinstance(tp, dict) else tp
                            allocation = tp.get('allocation', 0) if isinstance(tp, dict) else 0
                            
                            if tp_percentage > 0 and profit_percentage >= tp_percentage:
                                # Take profit target hit!
                                logging.warning(f"TAKE-PROFIT {i+1} TRIGGERED: {config.symbol} {config.side} position for user {user_id} - Profit: {profit_percentage:.2f}% >= {tp_percentage}%")
                                
                                if allocation >= 100:
                                    # Full position close
                                    config.status = "stopped"
                                    # Include both unrealized P&L and any realized P&L from partial TPs
                                    config.final_pnl = config.unrealized_pnl + getattr(config, 'realized_pnl', 0.0)
                                    config.closed_at = get_iran_time().isoformat()
                                    config.unrealized_pnl = 0.0
                                    
                                    # Save to database
                                    save_trade_to_db(user_id, config)
                                    
                                    # Log trade closure
                                    bot_trades.append({
                                        'id': len(bot_trades) + 1,
                                        'user_id': str(user_id),
                                        'trade_id': trade_id,
                                        'symbol': config.symbol,
                                        'side': config.side,
                                        'amount': config.amount,
                                        'final_pnl': config.final_pnl,
                                        'timestamp': get_iran_time().isoformat(),
                                        'status': f'take_profit_{i+1}_triggered'
                                    })
                                    
                                    logging.info(f"Position auto-closed at TP{i+1}: {config.symbol} {config.side} - Final P&L: ${config.final_pnl:.2f}")
                                    break  # Position closed, no need to check other TPs
                                else:
                                    # Partial close - CRITICAL FIX: Store original amounts before any TP triggers
                                    if not hasattr(config, 'original_amount'):
                                        config.original_amount = config.amount
                                    if not hasattr(config, 'original_margin'):
                                        config.original_margin = calculate_position_margin(config.original_amount, config.leverage)
                                        
                                    # FIXED: Calculate partial profit based on original position and correct allocation
                                    # The old logic was: partial_pnl = config.unrealized_pnl * (allocation / 100)
                                    # This was wrong because unrealized_pnl changes after each TP trigger
                                    # 
                                    # Correct calculation: Use the profit amount from TP calculations based on original position
                                    tp_calculations = calculate_tp_sl_prices_and_amounts(config)
                                    current_tp_data = None
                                    for tp_calc in tp_calculations.get('take_profits', []):
                                        if tp_calc['level'] == i + 1:
                                            current_tp_data = tp_calc
                                            break
                                    
                                    if current_tp_data:
                                        partial_pnl = current_tp_data['profit_amount']
                                    else:
                                        # Fallback to old calculation if TP data not found
                                        partial_pnl = config.unrealized_pnl * (allocation / 100)
                                        
                                    remaining_amount = config.amount * ((100 - allocation) / 100)
                                    
                                    # Log partial closure
                                    bot_trades.append({
                                        'id': len(bot_trades) + 1,
                                        'user_id': str(user_id),
                                        'trade_id': trade_id,
                                        'symbol': config.symbol,
                                        'side': config.side,
                                        'amount': config.amount * (allocation / 100),
                                        'final_pnl': partial_pnl,
                                        'timestamp': get_iran_time().isoformat(),
                                        'status': f'partial_take_profit_{i+1}'
                                    })
                                    
                                    # Update realized P&L with the profit from this TP
                                    if not hasattr(config, 'realized_pnl'):
                                        config.realized_pnl = 0.0
                                    config.realized_pnl += partial_pnl
                                    
                                    # Update position with remaining amount
                                    config.amount = remaining_amount
                                    config.unrealized_pnl -= partial_pnl
                                    
                                    # Remove triggered TP from list safely
                                    if i < len(config.take_profits):
                                        config.take_profits.pop(i)
                                    else:
                                        # TP already removed or index out of bounds
                                        logging.warning(f"TP index {i} out of bounds for {config.symbol}, skipping removal")
                                    
                                    # Save partial closure to database
                                    save_trade_to_db(user_id, config)
                                    
                                    logging.info(f"Partial TP{i+1} triggered: {config.symbol} {config.side} - Closed {allocation}% for ${partial_pnl:.2f}")
                                    
                                    # Auto move SL to break-even after first TP (TP1) if enabled
                                    # Convert string breakeven values to numeric for comparison
                                    breakeven_numeric = 0.0
                                    if hasattr(config, 'breakeven_after'):
                                        if config.breakeven_after == "tp1":
                                            breakeven_numeric = 1.0
                                        elif config.breakeven_after == "tp2":
                                            breakeven_numeric = 2.0  
                                        elif config.breakeven_after == "tp3":
                                            breakeven_numeric = 3.0
                                        elif isinstance(config.breakeven_after, (int, float)):
                                            breakeven_numeric = float(config.breakeven_after)
                                    
                                    if i == 0 and breakeven_numeric > 0:  # First TP triggered and breakeven enabled
                                        if not getattr(config, 'breakeven_sl_triggered', False):
                                            # Move stop loss to entry price (break-even)
                                            original_sl_percent = config.stop_loss_percent
                                            # Set a special flag to indicate break-even stop loss
                                            config.breakeven_sl_triggered = True
                                            config.breakeven_sl_price = config.entry_price
                                            logging.info(f"AUTO BREAK-EVEN: Moving SL to entry price after TP1 - was {original_sl_percent}%, now break-even")
                                            save_trade_to_db(user_id, config)
                                    
                                    break  # Only trigger one TP level at a time
                    
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
    

    
    # Calculate Take Profit levels with proper sequential allocation handling
    cumulative_allocation_closed = 0  # Track how much of original position has been closed
    
    for i, tp in enumerate(config.take_profits or []):
        tp_percentage = tp.get('percentage', 0) if isinstance(tp, dict) else tp
        allocation = tp.get('allocation', 0) if isinstance(tp, dict) else 0
        
        if tp_percentage > 0:
            # TP percentage is the desired profit on margin (what user risks), not price movement
            # For leveraged trading: required price movement = tp_percentage / leverage
            required_price_movement = tp_percentage / config.leverage / 100
            
            if config.side == "long":
                tp_price = config.entry_price * (1 + required_price_movement)
            else:  # short
                tp_price = config.entry_price * (1 - required_price_movement)
            
            # CRITICAL FIX: Calculate profit based on ORIGINAL position margin, not current reduced amount
            # The issue was: After TP1 triggers, config.amount gets reduced, causing wrong TP2/TP3 calculations
            # 
            # Correct logic: Each TP should calculate profit based on its allocation of the ORIGINAL position
            # TP1: 2% profit on 50% allocation = 2% * (50% of original margin) = 1% of original margin
            # TP2: 3.5% profit on 30% allocation = 3.5% * (30% of original margin) = 1.05% of original margin  
            # TP3: 5% profit on 20% allocation = 5% * (20% of original margin) = 1% of original margin
            #
            # Get original position margin - either from config or calculate it fresh
            original_margin = getattr(config, 'original_margin', None) or calculate_position_margin(
                getattr(config, 'original_amount', config.amount), config.leverage
            )
            
            profit_amount = (tp_percentage / 100) * original_margin * (allocation / 100)
            
            # Calculate position size to close based on the profit amount and price difference
            price_difference = abs(tp_price - config.entry_price)
            if price_difference > 0:
                position_size_to_close = profit_amount / price_difference
            else:
                position_size_to_close = 0
            
            result['take_profits'].append({
                'level': i + 1,
                'percentage': tp_percentage,
                'allocation': allocation,
                'price': tp_price,
                'profit_amount': profit_amount,
                'position_size_to_close': position_size_to_close
            })
            
            # Track cumulative allocation for future sequential TP handling
            cumulative_allocation_closed += allocation
    
    # Calculate Stop Loss
    if hasattr(config, 'breakeven_sl_triggered') and config.breakeven_sl_triggered:
        # Break-even stop loss - set to entry price
        sl_price = config.entry_price
        result['stop_loss'] = {
            'percentage': 0.0,  # 0% = break-even
            'price': sl_price,
            'loss_amount': 0.0,  # No loss at entry price
            'is_breakeven': True
        }
    elif config.stop_loss_percent > 0:
        # Regular stop loss calculation
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
            'loss_amount': loss_amount,
            'is_breakeven': False
        }
    
    return result

def get_margin_summary(chat_id):
    """Get comprehensive margin summary for a user"""
    user_trades = user_trade_configs.get(chat_id, {})
    
    # Account totals - use paper trading balance
    initial_balance = user_paper_balances.get(chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE)
    total_position_margin = 0.0
    total_unrealized_pnl = 0.0
    total_realized_pnl = 0.0
    
    # Calculate realized P&L from closed positions
    for config in user_trades.values():
        if config.status == "stopped" and hasattr(config, 'final_pnl') and config.final_pnl is not None:
            total_realized_pnl += config.final_pnl
    
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
    
    # Calculate account balance including realized P&L and unrealized P&L from active positions
    account_balance = initial_balance + total_realized_pnl + total_unrealized_pnl
    free_margin = account_balance - total_position_margin
    
    return {
        'account_balance': account_balance,
        'total_margin': total_position_margin,
        'free_margin': free_margin,
        'unrealized_pnl': total_unrealized_pnl,
        'realized_pnl': total_realized_pnl,
        'margin_level': account_balance / total_position_margin * 100 if total_position_margin > 0 else 0
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
        response = urllib.request.urlopen(req, timeout=TimeConfig.QUICK_API_TIMEOUT)
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
        response = urllib.request.urlopen(req, timeout=TimeConfig.QUICK_API_TIMEOUT)
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
        response = urllib.request.urlopen(req, timeout=TimeConfig.QUICK_API_TIMEOUT)
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
            response = urllib.request.urlopen(req, timeout=TimeConfig.QUICK_API_TIMEOUT)
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
            # Use optimized lightweight monitoring - dramatically reduces server load
            update_positions_lightweight()
            
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
                            iran_time = utc_to_iran_time(timestamp)
                            if iran_time:
                                response += f"   Closed: {iran_time.strftime('%Y-%m-%d %H:%M')} GMT+3:30\n"
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
                    iran_time = utc_to_iran_time(timestamp)
                    if iran_time:
                        response += f"   Time: {iran_time.strftime('%Y-%m-%d %H:%M')} GMT+3:30\n\n"
            
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
            
            # Store the internal code (tp1, tp2, tp3, off) not the display name
            config.breakeven_after = mode if mode != "off" else "disabled"
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

# ============================================================================
# OPTIMIZED TRADING SYSTEM - Exchange-Native Orders with Lightweight Monitoring
# ============================================================================

def process_paper_trading_position(user_id, trade_id, config):
    """Enhanced paper trading monitoring with real price-based TP/SL simulation"""
    try:
        if not config.entry_price or not config.current_price:
            return
        
        # Calculate unrealized P&L
        config.unrealized_pnl = calculate_unrealized_pnl(
            config.entry_price, config.current_price,
            config.amount, config.leverage, config.side
        )
        
        # Check paper trading stop loss
        if hasattr(config, 'paper_sl_data') and not config.paper_sl_data.get('triggered', False):
            stop_loss_triggered = False
            
            # Check break-even stop loss first
            if hasattr(config, 'breakeven_sl_triggered') and config.breakeven_sl_triggered:
                if config.side == "long" and config.current_price <= config.entry_price:
                    stop_loss_triggered = True
                elif config.side == "short" and config.current_price >= config.entry_price:
                    stop_loss_triggered = True
            # Check regular stop loss
            elif config.stop_loss_percent > 0 and config.unrealized_pnl < 0:
                loss_percentage = abs(config.unrealized_pnl / config.amount) * 100
                if loss_percentage >= config.stop_loss_percent:
                    stop_loss_triggered = True
            
            if stop_loss_triggered:
                execute_paper_stop_loss(user_id, trade_id, config)
                return  # Position closed, no further processing
        
        # Check paper trading take profits
        if hasattr(config, 'paper_tp_levels') and config.unrealized_pnl > 0:
            profit_percentage = (config.unrealized_pnl / config.amount) * 100
            
            # Check each TP level (in order)
            for i, tp_level in enumerate(config.paper_tp_levels):
                if not tp_level.get('triggered', False) and profit_percentage >= tp_level['percentage']:
                    execute_paper_take_profit(user_id, trade_id, config, i, tp_level)
                    break  # Only trigger one TP at a time
        
        # Check break-even trigger for paper trades
        if (hasattr(config, 'breakeven_after') and config.breakeven_after and 
            not getattr(config, 'breakeven_sl_triggered', False) and config.unrealized_pnl > 0):
            
            profit_percentage = (config.unrealized_pnl / config.amount) * 100
            breakeven_threshold = 0
            
            if isinstance(config.breakeven_after, (int, float)):
                breakeven_threshold = config.breakeven_after
            elif config.breakeven_after == "tp1":
                breakeven_threshold = config.take_profits[0].get('percentage', 0) if config.take_profits else 0
            
            if breakeven_threshold > 0 and profit_percentage >= breakeven_threshold:
                config.breakeven_sl_triggered = True
                config.breakeven_sl_price = config.entry_price
                save_trade_to_db(user_id, config)
                logging.info(f"Paper Trading: Break-even triggered for {config.symbol} {config.side} - SL moved to entry price")
        
    except Exception as e:
        logging.error(f"Paper trading position processing failed for {config.symbol}: {e}")

def execute_paper_stop_loss(user_id, trade_id, config):
    """Execute paper trading stop loss"""
    config.status = "stopped"
    config.final_pnl = config.unrealized_pnl + getattr(config, 'realized_pnl', 0.0)
    config.closed_at = get_iran_time().isoformat()
    config.unrealized_pnl = 0.0
    
    # Mark SL as triggered
    if hasattr(config, 'paper_sl_data'):
        config.paper_sl_data['triggered'] = True
    
    # Update paper trading balance
    if user_id in user_paper_balances:
        # Return margin plus final P&L to balance
        balance_change = config.amount + config.final_pnl
        user_paper_balances[user_id] += balance_change
        logging.info(f"Paper Trading: Balance updated +${balance_change:.2f}. New balance: ${user_paper_balances[user_id]:,.2f}")
    
    save_trade_to_db(user_id, config)
    
    # Log paper trade closure
    bot_trades.append({
        'id': len(bot_trades) + 1,
        'user_id': str(user_id),
        'trade_id': trade_id,
        'symbol': config.symbol,
        'side': config.side,
        'amount': config.amount,
        'final_pnl': config.final_pnl,
        'timestamp': get_iran_time().isoformat(),
        'status': 'paper_stop_loss_triggered',
        'trading_mode': 'paper'
    })
    
    logging.info(f"Paper Trading: Stop loss triggered - {config.symbol} {config.side} closed with P&L: ${config.final_pnl:.2f}")

def execute_paper_take_profit(user_id, trade_id, config, tp_index, tp_level):
    """Execute paper trading take profit"""
    allocation = tp_level['allocation']
    
    if allocation >= 100:
        # Full position close
        config.status = "stopped"
        config.final_pnl = config.unrealized_pnl + getattr(config, 'realized_pnl', 0.0)
        config.closed_at = get_iran_time().isoformat()
        config.unrealized_pnl = 0.0
        
        # Mark TP as triggered
        tp_level['triggered'] = True
        
        save_trade_to_db(user_id, config)
        
        # Update paper trading balance
        if user_id in user_paper_balances:
            # Return margin plus final P&L to balance
            balance_change = config.amount + config.final_pnl
            user_paper_balances[user_id] += balance_change
            logging.info(f"Paper Trading: Balance updated +${balance_change:.2f}. New balance: ${user_paper_balances[user_id]:,.2f}")
        
        # Log paper trade closure
        bot_trades.append({
            'id': len(bot_trades) + 1,
            'user_id': str(user_id),
            'trade_id': trade_id,
            'symbol': config.symbol,
            'side': config.side,
            'amount': config.amount,
            'final_pnl': config.final_pnl,
            'timestamp': get_iran_time().isoformat(),
            'status': f'paper_take_profit_{tp_level["level"]}_triggered',
            'trading_mode': 'paper'
        })
        
        logging.info(f"Paper Trading: TP{tp_level['level']} triggered - {config.symbol} {config.side} closed with P&L: ${config.final_pnl:.2f}")
    else:
        # Partial close
        # CRITICAL FIX: Store original amounts before any TP triggers to preserve correct calculations
        if not hasattr(config, 'original_amount'):
            config.original_amount = config.amount
        if not hasattr(config, 'original_margin'):
            config.original_margin = calculate_position_margin(config.original_amount, config.leverage)
        
        partial_pnl = config.unrealized_pnl * (allocation / 100)
        remaining_amount = config.amount * ((100 - allocation) / 100)
        
        # Update realized P&L
        if not hasattr(config, 'realized_pnl'):
            config.realized_pnl = 0.0
        config.realized_pnl += partial_pnl
        
        # Update position with remaining amount  
        config.amount = remaining_amount
        config.unrealized_pnl -= partial_pnl
        
        # Mark TP as triggered
        tp_level['triggered'] = True
        
        # Remove triggered TP from list safely
        if tp_index < len(config.take_profits):
            config.take_profits.pop(tp_index)
        else:
            # TP already removed, find and remove by level instead
            config.take_profits = [tp for tp in config.take_profits if not (isinstance(tp, dict) and tp.get('level') == tp_level.get('level'))]
        
        save_trade_to_db(user_id, config)
        
        # Update paper trading balance for partial closure
        if user_id in user_paper_balances:
            # Return partial margin plus partial P&L to balance
            partial_margin_return = (config.amount / (100 - allocation)) * allocation  # Original partial margin
            balance_change = partial_margin_return + partial_pnl
            user_paper_balances[user_id] += balance_change
            logging.info(f"Paper Trading: Balance updated +${balance_change:.2f}. New balance: ${user_paper_balances[user_id]:,.2f}")
        
        # Log partial closure
        bot_trades.append({
            'id': len(bot_trades) + 1,
            'user_id': str(user_id),
            'trade_id': trade_id,
            'symbol': config.symbol,
            'side': config.side,
            'amount': config.amount * (allocation / 100),
            'final_pnl': partial_pnl,
            'timestamp': get_iran_time().isoformat(),
            'status': f'paper_partial_take_profit_{tp_level["level"]}',
            'trading_mode': 'paper'
        })
        
        logging.info(f"Paper Trading: Partial TP{tp_level['level']} triggered - {config.symbol} {config.side} closed {allocation}% for ${partial_pnl:.2f}")
        
        # Auto-trigger break-even after first TP if configured
        if tp_level['level'] == 1 and hasattr(config, 'breakeven_after') and config.breakeven_after == "tp1":
            if not getattr(config, 'breakeven_sl_triggered', False):
                config.breakeven_sl_triggered = True
                config.breakeven_sl_price = config.entry_price
                save_trade_to_db(user_id, config)
                logging.info(f"Paper Trading: Auto break-even triggered after TP1 - SL moved to entry price")

def initialize_paper_trading_monitoring(config):
    """Initialize paper trading monitoring after position opens"""
    if not getattr(config, 'paper_trading_mode', False):
        return
    
    # Recalculate TP/SL data with actual entry price
    tp_sl_data = calculate_tp_sl_prices_and_amounts(config)
    
    # Update paper TP levels with actual prices
    if hasattr(config, 'paper_tp_levels') and tp_sl_data.get('take_profits'):
        for i, (paper_tp, calc_tp) in enumerate(zip(config.paper_tp_levels, tp_sl_data['take_profits'])):
            paper_tp['price'] = calc_tp['price']
    
    # Update paper SL with actual price
    if hasattr(config, 'paper_sl_data') and tp_sl_data.get('stop_loss'):
        config.paper_sl_data['price'] = tp_sl_data['stop_loss']['price']
    
    logging.info(f"Paper Trading: Monitoring initialized for {config.symbol} {config.side} with {len(getattr(config, 'paper_tp_levels', []))} TP levels")

def update_positions_lightweight():
    """OPTIMIZED: Lightweight position updates - only for break-even monitoring"""
    # Only collect positions that need break-even monitoring
    breakeven_positions = []
    symbols_needed = set()
    
    # Debug: Log all positions for troubleshooting
    total_positions = 0
    active_positions = 0
    
    for user_id, trades in user_trade_configs.items():
        for trade_id, config in trades.items():
            total_positions += 1
            
            # Debug logging for breakeven analysis
            logging.debug(f"Checking position {trade_id}: status={config.status}, symbol={config.symbol}, "
                         f"breakeven_after={config.breakeven_after} (type: {type(config.breakeven_after)}), "
                         f"breakeven_sl_triggered={getattr(config, 'breakeven_sl_triggered', 'not_set')}")
            
            if config.status == "active":
                active_positions += 1
                
            # Only monitor active positions with break-even enabled and not yet triggered
            breakeven_enabled = False
            if hasattr(config, 'breakeven_after') and config.breakeven_after:
                # Handle both string values (tp1, tp2, tp3) and numeric values (1.0, 2.0, 3.0)
                if isinstance(config.breakeven_after, str):
                    breakeven_enabled = config.breakeven_after in ["tp1", "tp2", "tp3"]
                elif isinstance(config.breakeven_after, (int, float)):
                    breakeven_enabled = config.breakeven_after > 0
            
            if (config.status == "active" and config.symbol and breakeven_enabled and
                not getattr(config, 'breakeven_sl_triggered', False)):
                symbols_needed.add(config.symbol)
                breakeven_positions.append((user_id, trade_id, config))
    
    # Enhanced debug logging
    logging.debug(f"Monitoring scan: {total_positions} total positions, {active_positions} active, {len(breakeven_positions)} need break-even monitoring")
    
    # If no positions need break-even monitoring, skip entirely
    if not breakeven_positions:
        logging.debug("No positions need break-even monitoring - skipping lightweight update")
        return
    
    logging.info(f"Lightweight monitoring: Only {len(breakeven_positions)} positions need break-even checks (vs {sum(len(trades) for trades in user_trade_configs.values())} total)")
    
    # Fetch prices only for symbols that need break-even monitoring
    symbol_prices = {}
    if symbols_needed:
        futures = {}
        for symbol in symbols_needed:
            future = price_executor.submit(get_live_market_price, symbol, True)
            futures[future] = symbol
        
        for future in as_completed(futures, timeout=TimeConfig.QUICK_API_TIMEOUT):
            symbol = futures[future]
            try:
                price = future.result()
                symbol_prices[symbol] = price
            except Exception as e:
                logging.warning(f"Failed to get price for break-even check {symbol}: {e}")
    
    # Process break-even monitoring ONLY
    for user_id, trade_id, config in breakeven_positions:
        if config.symbol in symbol_prices:
            try:
                config.current_price = symbol_prices[config.symbol]
                
                if config.entry_price and config.current_price:
                    config.unrealized_pnl = calculate_unrealized_pnl(
                        config.entry_price, config.current_price,
                        config.amount, config.leverage, config.side
                    )
                    
                    # Check ONLY break-even (everything else handled by exchange)
                    if config.unrealized_pnl > 0:
                        profit_percentage = (config.unrealized_pnl / config.amount) * 100
                        
                        # Ensure breakeven_after is numeric before comparison
                        if (isinstance(config.breakeven_after, (int, float)) and 
                            profit_percentage >= config.breakeven_after):
                            logging.info(f"BREAK-EVEN TRIGGERED: {config.symbol} {config.side} - Moving SL to entry price")
                            
                            # Mark as triggered to stop monitoring
                            config.breakeven_sl_triggered = True
                            save_trade_to_db(user_id, config)
                            
                            # Move exchange SL to entry price using ToobitClient
                            try:
                                user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
                                if user_creds and user_creds.has_credentials():
                                    client = ToobitClient(
                                        api_key=user_creds.get_api_key(),
                                        api_secret=user_creds.get_api_secret(),
                                        testnet=user_creds.testnet_mode
                                    )
                                    # Move stop loss to entry price (break-even)
                                    config.breakeven_sl_price = config.entry_price
                                    config.breakeven_sl_triggered = True
                                    logging.info(f"Break-even stop loss set to entry price: ${config.entry_price}")
                            except Exception as be_error:
                                logging.error(f"Failed to move SL to break-even: {be_error}")
                            
            except Exception as e:
                logging.warning(f"Break-even check failed for {config.symbol}: {e}")


def place_exchange_native_orders(config, user_id):
    """Place all TP/SL orders directly on exchange after position opens"""
    try:
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        if not user_creds or not user_creds.has_credentials():
            logging.info("No credentials found - skipping exchange-native orders (using paper mode)")
            return False
            
        client = ToobitClient(
            api_key=user_creds.get_api_key(),
            api_secret=user_creds.get_api_secret(),
            testnet=user_creds.testnet_mode
        )
        
        # Calculate position size and prices
        position_size = config.amount * config.leverage
        
        # Prepare take profit orders
        tp_orders = []
        if config.take_profits:
            tp_calc = calculate_tp_sl_prices_and_amounts(config)
            for i, tp_data in enumerate(tp_calc.get('take_profits', [])):
                tp_quantity = position_size * (tp_data['allocation'] / 100)
                tp_orders.append({
                    'price': tp_data['price'],
                    'quantity': str(tp_quantity),
                    'percentage': tp_data['percentage'],
                    'allocation': tp_data['allocation']
                })
        
        # Determine stop loss strategy
        sl_price = None
        trailing_stop = None
        
        # Check if trailing stop is enabled
        if hasattr(config, 'trailing_stop_enabled') and config.trailing_stop_enabled:
            # Use exchange-native trailing stop instead of bot monitoring
            callback_rate = getattr(config, 'trail_percentage', 1.0)  # Default 1%
            activation_price = getattr(config, 'trail_activation_price', None)
            
            trailing_stop = {
                'callback_rate': callback_rate,
                'activation_price': activation_price
            }
            logging.info(f"Using exchange-native trailing stop: {callback_rate}% callback")
            
        elif config.stop_loss_percent > 0:
            # Use regular stop loss
            sl_calc = calculate_tp_sl_prices_and_amounts(config)
            sl_price = str(sl_calc.get('stop_loss', {}).get('price', 0))
        
        # Place all orders on exchange
        if trailing_stop:
            # For trailing stops, use a different approach or API endpoint
            logging.info(f"Trailing stop configuration: {trailing_stop}")
            # TODO: Implement exchange-native trailing stop placement
            orders_placed = []
        else:
            # Place regular TP/SL orders
            orders_placed = client.place_multiple_tp_sl_orders(
                symbol=config.symbol,
                side=config.side,
                total_quantity=str(position_size),
                take_profits=tp_orders,
                stop_loss_price=sl_price
            )
        
        logging.info(f"Placed {len(orders_placed)} exchange-native orders for {config.symbol}")
        
        # If using trailing stop, no bot monitoring needed at all!
        if trailing_stop:
            logging.info(f"Exchange-native trailing stop active - NO bot monitoring required!")
        
        return True
        
    except Exception as e:
        logging.error(f"Failed to place exchange-native orders: {e}")
        return False


if __name__ == "__main__":
    # This file is part of the main web application
    # Bot functionality is available via webhooks, no separate execution needed
    print("Note: This API module is part of the main web application.")
    print("Use 'python main.py' or the main workflow to start the application.")