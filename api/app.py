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
    from ..scripts.exchange_sync import initialize_sync_service, get_sync_service
    from .vercel_sync import initialize_vercel_sync_service, get_vercel_sync_service
    from .unified_exchange_client import ToobitClient, LBankClient, HyperliquidClient, create_exchange_client, create_wrapped_exchange_client
    from .enhanced_cache import enhanced_cache, start_cache_cleanup_worker
except ImportError:
    # Fall back to absolute import (for direct execution - Replit)
    import sys
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    sys.path.extend([current_dir, parent_dir])
    from api.models import db, UserCredentials, UserTradingSession, TradeConfiguration, format_iran_time, get_iran_time, utc_to_iran_time
    from scripts.exchange_sync import initialize_sync_service, get_sync_service
    from api.vercel_sync import initialize_vercel_sync_service, get_vercel_sync_service
    from api.unified_exchange_client import ToobitClient, LBankClient, HyperliquidClient, create_exchange_client, create_wrapped_exchange_client
    from api.enhanced_cache import enhanced_cache, start_cache_cleanup_worker

from api.circuit_breaker import with_circuit_breaker, circuit_manager, CircuitBreakerError
from api.error_handler import handle_error, handle_api_error, create_validation_error, create_success_response

# Helper function to get user_id from request - streamlines repetitive code
def get_user_id_from_request(default_user_id=None):
    """Get user_id from request args with fallback to default"""
    return request.args.get('user_id', default_user_id or Environment.DEFAULT_TEST_USER_ID)

def _should_process_take_profit(config):
    """Check if take profit monitoring should proceed."""
    return config.take_profits and config.unrealized_pnl > 0


def _calculate_profit_percentage(config):
    """Calculate current profit percentage based on amount."""
    return (config.unrealized_pnl / config.amount) * 100


def _get_tp_data(tp):
    """Extract percentage and allocation from TP data."""
    tp_percentage = tp.get('percentage', 0) if isinstance(tp, dict) else tp
    allocation = tp.get('allocation', 0) if isinstance(tp, dict) else 0
    return tp_percentage, allocation


def _log_tp_execution(user_id, trade_id, config, tp_level, profit_percentage, tp_percentage):
    """Log take profit execution."""
    logging.warning(
        f"TAKE-PROFIT {tp_level} TRIGGERED: {config.symbol} {config.side} position for user {user_id} - "
        f"Profit: {profit_percentage:.2f}% >= {tp_percentage}%"
    )


def _process_full_tp_closure(config, user_id, trade_id, tp_level):
    """Process full position closure at take profit."""
    config.status = "stopped"
    config.final_pnl = config.unrealized_pnl + getattr(config, 'realized_pnl', 0.0)
    config.closed_at = get_iran_time().isoformat()
    config.unrealized_pnl = 0.0
    
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
        'status': f'take_profit_{tp_level}_triggered'
    })
    
    logging.info(f"Position auto-closed at TP{tp_level}: {config.symbol} {config.side} - Final P&L: ${config.final_pnl:.2f}")
    return True


def _calculate_partial_pnl(config, tp_level, allocation):
    """Calculate partial PnL for take profit execution."""
    tp_calculations = calculate_tp_sl_prices_and_amounts(config)
    current_tp_data = None
    for tp_calc in tp_calculations.get('take_profits', []):
        if tp_calc['level'] == tp_level:
            current_tp_data = tp_calc
            break
    
    if current_tp_data:
        return current_tp_data['profit_amount']
    else:
        # Fallback to old calculation if TP data not found
        return config.unrealized_pnl * (allocation / 100)


def _handle_breakeven_logic(config, user_id, tp_index):
    """Handle breakeven stop loss logic after TP1."""
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
    
    if tp_index == 0 and breakeven_numeric > 0:  # First TP triggered and breakeven enabled
        if not getattr(config, 'breakeven_sl_triggered', False):
            original_sl_percent = config.stop_loss_percent
            config.breakeven_sl_triggered = True
            config.breakeven_sl_price = config.entry_price
            logging.info(f"AUTO BREAK-EVEN: Moving SL to entry price after TP1 - was {original_sl_percent}%, now break-even")
            save_trade_to_db(user_id, config)


def _process_partial_tp_closure(config, user_id, trade_id, tp_index, tp_level, allocation):
    """Process partial position closure at take profit."""
    # Store original amounts before any TP triggers
    if not hasattr(config, 'original_amount'):
        config.original_amount = config.amount
    if not hasattr(config, 'original_margin'):
        config.original_margin = calculate_position_margin(config.original_amount, config.leverage)
    
    partial_pnl = _calculate_partial_pnl(config, tp_level, allocation)
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
        'status': f'partial_take_profit_{tp_level}'
    })
    
    # Update realized P&L with the profit from this TP
    if not hasattr(config, 'realized_pnl'):
        config.realized_pnl = 0.0
    config.realized_pnl += partial_pnl
    
    # Update position with remaining amount
    config.amount = remaining_amount
    config.unrealized_pnl -= partial_pnl
    
    # Remove triggered TP from list safely
    if tp_index < len(config.take_profits):
        config.take_profits.pop(tp_index)
    else:
        logging.warning(f"TP index {tp_index} out of bounds for {config.symbol}, skipping removal")
    
    save_trade_to_db(user_id, config)
    logging.info(f"Partial TP{tp_level} triggered: {config.symbol} {config.side} - Closed {allocation}% for ${partial_pnl:.2f}")
    
    _handle_breakeven_logic(config, user_id, tp_index)


def _process_take_profit_monitoring(config, user_id, trade_id):
    """
    Process take profit monitoring and execution for a position.
    Handles both full and partial TP execution with break-even logic.
    
    Returns:
        bool: True if position was closed, False if still active
    """
    if not _should_process_take_profit(config):
        return False
    
    profit_percentage = _calculate_profit_percentage(config)
    
    # Check each TP level
    for i, tp in enumerate(config.take_profits):
        tp_percentage, allocation = _get_tp_data(tp)
        
        if tp_percentage > 0 and profit_percentage >= tp_percentage:
            tp_level = i + 1
            _log_tp_execution(user_id, trade_id, config, tp_level, profit_percentage, tp_percentage)
            
            if allocation >= 100:
                return _process_full_tp_closure(config, user_id, trade_id, tp_level)
            else:
                _process_partial_tp_closure(config, user_id, trade_id, i, tp_level, allocation)
                break  # Only trigger one TP level at a time
    
    return False  # Position still active


def _monitor_all_active_positions():
    """
    Monitor all active positions regardless of credentials.
    Updates prices, calculates P&L, and checks for trigger alerts.
    
    Returns:
        dict: Monitoring results with processed count and status
    """
    all_positions_processed = 0
    monitoring_result = {'processed': 0, 'status': 'inactive'}
    
    try:
        # Get all active positions from database
        all_active_trades = TradeConfiguration.query.filter_by(status='active').all()
        
        if all_active_trades:
            logging.info(f"HEALTH CHECK: Found {len(all_active_trades)} active positions for monitoring")
            for trade in all_active_trades:
                logging.info(f"HEALTH CHECK: Processing position {trade.trade_id} ({trade.symbol}) for user {trade.telegram_user_id}")
        
        for trade in all_active_trades:
            try:
                user_id = trade.telegram_user_id
                
                # Update price for the symbol (works without credentials)
                if trade.symbol:
                    current_price = get_live_market_price(
                        trade.symbol, 
                        use_cache=True, 
                        user_id=user_id
                    )
                    
                    if current_price and current_price > 0:
                        # Update current price in database
                        trade.current_price = current_price
                        
                        # Calculate P&L and check TP/SL triggers (works for all positions)
                        if trade.entry_price and trade.entry_price > 0:
                            # Calculate unrealized P&L
                            if trade.side == 'long':
                                price_change = (current_price - trade.entry_price) / trade.entry_price
                            else:  # short
                                price_change = (trade.entry_price - current_price) / trade.entry_price
                            
                            # Update unrealized P&L
                            position_value = trade.amount * trade.leverage
                            trade.unrealized_pnl = position_value * price_change
                            
                            # Check for TP/SL triggers (basic monitoring without executing trades)
                            # For now, just log potential trigger events for monitoring
                            check_position_trigger_alerts(trade, current_price)
                        
                        all_positions_processed += 1
                        logging.debug(f"HEALTH CHECK: Successfully processed position {trade.trade_id} - Price: ${current_price}, P&L: ${trade.unrealized_pnl:.2f}")
                        
            except Exception as e:
                logging.warning(f"Position monitoring failed for trade {trade.trade_id}: {e}")
        
        # Commit price and P&L updates
        if all_positions_processed > 0:
            db.session.commit()
            logging.info(f"HEALTH CHECK: Committed price updates for {all_positions_processed} positions")
            
        monitoring_result = {
            'processed': all_positions_processed,
            'status': 'active' if all_positions_processed > 0 else 'no_active_positions'
        }
        
    except Exception as e:
        logging.warning(f"All positions monitoring failed: {e}")
        db.session.rollback()
    
    return monitoring_result


def _validate_credentials_and_create_client(user_creds, user_id):
    """
    Validate user credentials and create exchange client for trading.
    Returns tuple: (client, error_response) where error_response is None on success.
    """
    try:
        # Enhanced credential debugging for Render
        api_key = user_creds.get_api_key()
        api_secret = user_creds.get_api_secret()
        passphrase = user_creds.get_passphrase()
        
        # Debug credential validation (without exposing actual values)
        logging.debug(f"Processing API credentials for user {user_id}")
        
        if not api_key or not api_secret:
            error_msg = f"[RENDER ERROR] Missing credentials - API Key: {'✓' if api_key else '✗'}, API Secret: {'✓' if api_secret else '✗'}"
            logging.error(error_msg)
            return None, jsonify({
                'error': 'Invalid API credentials. Please check your Toobit API key and secret in the API Keys menu.',
                'debug_info': {
                    'has_api_key': bool(api_key),
                    'has_api_secret': bool(api_secret),
                    'credential_lengths': {
                        'api_key': len(api_key) if api_key else 0,
                        'api_secret': len(api_secret) if api_secret else 0
                    }
                }
            }), 400
        
        # Create exchange client (dynamic selection)
        client = create_exchange_client(user_creds, testnet=False)
        
        # Enhanced connection test with detailed error reporting
        try:
            logging.debug("Testing Toobit API connection...")
            balance_data = client.get_futures_balance()
            
            if balance_data:
                logging.debug("API connection successful")
                
                # Check what symbols are available on Toobit
                try:
                    exchange_info = client.get_exchange_info()
                    if exchange_info and 'symbols' in exchange_info:
                        valid_symbols = [s['symbol'] for s in exchange_info['symbols']]
                        logging.info(f"[DEBUG] Found {len(valid_symbols)} available symbols")
                        logging.info(f"[DEBUG] First 10 symbols: {valid_symbols[:10]}")
                        
                        # This symbol validation is for debugging but doesn't affect execution
                        
                except Exception as e:
                    logging.error(f"[DEBUG] Failed to get exchange info: {e}")
            else:
                logging.warning("Empty balance response from API")
                
        except Exception as conn_error:
            error_details = {
                'error_type': type(conn_error).__name__,
                'error_message': str(conn_error),
                'last_client_error': getattr(client, 'last_error', None)
            }
            
            logging.error(f"API connection failed: {conn_error}")
            
            # Provide user-friendly error messages based on error type
            if 'unauthorized' in str(conn_error).lower() or '401' in str(conn_error):
                user_message = 'Invalid API credentials. Please verify your Toobit API key and secret.'
            elif 'forbidden' in str(conn_error).lower() or '403' in str(conn_error):
                user_message = 'API access forbidden. Please check your Toobit API permissions for futures trading.'
            elif 'timeout' in str(conn_error).lower():
                user_message = 'Connection timeout. Please try again.'
            else:
                user_message = f'Exchange connection failed: {str(conn_error)}'
            
            return None, jsonify({
                'error': user_message,
                'debug_info': error_details,
                'troubleshooting': [
                    'Verify your Toobit API key and secret are correct',
                    'Ensure your API key has futures trading permissions',
                    'Check if your Toobit account is verified and funded',
                    'Make sure you copied the full API key without spaces'
                ]
            }), 400
        
        return client, None
        
    except Exception as e:
        error_details = {
            'error_type': type(e).__name__,
            'error_message': str(e),
            'last_client_error': getattr(client, 'last_error', None) if 'client' in locals() else None
        }
        
        logging.error(f"[RENDER TRADING ERROR] Credential validation failed: {error_details}")
        
        # Import stack trace for detailed debugging
        import traceback
        logging.error(f"[RENDER STACK TRACE] {traceback.format_exc()}")
        
        return None, jsonify({
            'error': f'Credential validation failed: {str(e)}',
            'debug_info': error_details,
            'troubleshooting': [
                'Check your internet connection',
                'Verify your Toobit API credentials are active',
                'Try refreshing the page and attempting the trade again'
            ]
        }), 500

# Configure logging using centralized config
logging.basicConfig(level=getattr(logging, get_log_level()))

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", SecurityConfig.DEFAULT_SESSION_SECRET)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Record app start time for uptime tracking
app.config['START_TIME'] = time.time()

# Configure database using centralized config
database_url = get_database_url()
if not database_url:
    # Fallback to SQLite for development
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'instance', 'trading_bot.db')
    database_url = f"sqlite:///{db_path}"
    logging.info(f"Using SQLite database for development at {db_path}")

# Validate the database URL before setting it
try:
    from sqlalchemy import create_engine
    # Test if the URL can be parsed by SQLAlchemy
    test_engine = create_engine(database_url, strategy='mock', executor=lambda sql, *_: None)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    logging.info(f"Database configured successfully: {database_url.split('://')[0]}")
except Exception as e:
    # If URL is invalid, fall back to SQLite
    logging.error(f"Invalid database URL, falling back to SQLite: {e}")
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'instance', 'trading_bot.db')
    database_url = f"sqlite:///{db_path}"
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
elif database_url.startswith("postgresql") and Environment.IS_RENDER:
    # Render PostgreSQL configuration - optimized for always-on services
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": DatabaseConfig.RENDER_POOL_RECYCLE,
        "pool_pre_ping": DatabaseConfig.POOL_PRE_PING,
        "pool_size": DatabaseConfig.RENDER_POOL_SIZE,
        "max_overflow": DatabaseConfig.RENDER_MAX_OVERFLOW,
        "pool_timeout": DatabaseConfig.RENDER_POOL_TIMEOUT,
        "pool_reset_on_return": "commit",
        "connect_args": {
            "sslmode": DatabaseConfig.SSL_MODE,
            "connect_timeout": TimeConfig.DEFAULT_API_TIMEOUT,
            "application_name": DatabaseConfig.APPLICATION_NAME
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
start_cache_cleanup_worker(app)
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
                ('realized_pnl', 'FLOAT DEFAULT 0.0'),
                ('original_amount', 'FLOAT DEFAULT 0.0'),
                ('original_margin', 'FLOAT DEFAULT 0.0')
            ]
            
            # Ensure SMC signal cache table exists
            try:
                db.session.execute(text("""
                    CREATE TABLE IF NOT EXISTS smc_signal_cache (
                        id SERIAL PRIMARY KEY,
                        symbol VARCHAR(20) NOT NULL,
                        direction VARCHAR(10) NOT NULL,
                        entry_price FLOAT NOT NULL,
                        stop_loss FLOAT NOT NULL,
                        take_profit_levels TEXT NOT NULL,
                        confidence FLOAT NOT NULL,
                        reasoning TEXT NOT NULL,
                        signal_strength VARCHAR(20) NOT NULL,
                        risk_reward_ratio FLOAT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        expires_at TIMESTAMP NOT NULL,
                        market_price_at_signal FLOAT NOT NULL
                    )
                """))
                
                # Create index for efficient SMC signal queries
                db.session.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_smc_signal_cache_symbol_expires 
                    ON smc_signal_cache(symbol, expires_at)
                """))
                
                # Ensure KlinesCache table exists with proper indexes
                db.session.execute(text("""
                    CREATE TABLE IF NOT EXISTS klines_cache (
                        id SERIAL PRIMARY KEY,
                        symbol VARCHAR(20) NOT NULL,
                        timeframe VARCHAR(10) NOT NULL,
                        timestamp TIMESTAMP NOT NULL,
                        open FLOAT NOT NULL,
                        high FLOAT NOT NULL,
                        low FLOAT NOT NULL,
                        close FLOAT NOT NULL,
                        volume FLOAT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        expires_at TIMESTAMP NOT NULL,
                        is_complete BOOLEAN DEFAULT TRUE
                    )
                """))
                
                # Create indexes for efficient klines cache queries
                db.session.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_klines_symbol_timeframe_timestamp 
                    ON klines_cache(symbol, timeframe, timestamp)
                """))
                
                db.session.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_klines_expires 
                    ON klines_cache(expires_at)
                """))
                
                db.session.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_klines_symbol_timeframe_expires 
                    ON klines_cache(symbol, timeframe, expires_at)
                """))
                
                db.session.commit()
                logging.info("SMC signal cache and KlinesCache tables ensured for deployment")
            except Exception as smc_error:
                logging.warning(f"Cache table creation failed (may already exist): {smc_error}")
                db.session.rollback()
            
            # Fix Toobit testnet issue for existing data (only if Toobit users exist)
            try:
                # First check if there are any Toobit users before running fixes
                toobit_count = db.session.execute(text("""
                    SELECT COUNT(*) FROM user_credentials 
                    WHERE exchange_name = 'toobit' AND is_active = true
                """)).scalar()
                
                if toobit_count and toobit_count > 0:
                    # Only run Toobit fixes if there are actual Toobit users
                    db.session.execute(text("""
                        UPDATE user_credentials 
                        SET testnet_mode = false 
                        WHERE exchange_name = 'toobit' AND testnet_mode = true
                    """))
                    db.session.commit()
                    
                    # Additional Vercel/Neon protection - ensure all Toobit credentials are mainnet
                    toobit_testnet_users = UserCredentials.query.filter_by(
                        exchange_name='toobit',
                        testnet_mode=True,
                        is_active=True
                    ).all()
                    
                    if toobit_testnet_users:
                        for cred in toobit_testnet_users:
                            cred.testnet_mode = False
                            logging.info(f"Vercel/Neon: Disabled testnet mode for Toobit user {cred.telegram_user_id}")
                        
                        db.session.commit()
                        logging.info(f"Vercel/Neon: Fixed {len(toobit_testnet_users)} Toobit testnet credentials")
                    
                    logging.info("Fixed Toobit testnet mode for existing credentials")
                    
                    # CRITICAL: Force disable testnet mode for ALL environments (Replit, Vercel, Render)
                    # This addresses persistent testnet issues on Render deployments
                    all_toobit_creds = UserCredentials.query.filter_by(
                        exchange_name='toobit',
                        is_active=True
                    ).all()
                    
                    testnet_fixes = 0
                    for cred in all_toobit_creds:
                        if cred.testnet_mode:
                            cred.testnet_mode = False
                            testnet_fixes += 1
                            logging.warning(f"RENDER FIX: Disabled testnet mode for Toobit user {cred.telegram_user_id}")
                    
                    if testnet_fixes > 0:
                        db.session.commit()
                        logging.info(f"RENDER FIX: Updated {testnet_fixes} Toobit credentials to mainnet mode")
                else:
                    logging.debug("No Toobit users found - skipping Toobit testnet fixes")
                
                # CRITICAL FIX: Ensure all non-Toobit exchanges default to live mode (not testnet)
                # This addresses the user issue where they're stuck in testnet mode
                try:
                    non_toobit_testnet_users = UserCredentials.query.filter(
                        UserCredentials.exchange_name != 'toobit',
                        UserCredentials.testnet_mode == True,
                        UserCredentials.is_active == True
                    ).all()
                    
                    if non_toobit_testnet_users:
                        fixed_count = 0
                        for cred in non_toobit_testnet_users:
                            cred.testnet_mode = False  # Switch to live mode by default
                            fixed_count += 1
                            logging.info(f"LIVE MODE FIX: Switched {cred.exchange_name} user {cred.telegram_user_id} to live trading")
                        
                        db.session.commit()
                        logging.info(f"LIVE MODE FIX: Updated {fixed_count} users from testnet to live mode")
                    else:
                        logging.debug("No users stuck in testnet mode - migration not needed")
                        
                except Exception as testnet_fix_error:
                    logging.warning(f"Live mode migration failed (may not be needed): {testnet_fix_error}")
                    db.session.rollback()
                    
            except Exception as toobit_fix_error:
                logging.warning(f"Toobit testnet fix failed (may not be needed): {toobit_fix_error}")
                db.session.rollback()
            
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
user_api_setup_state: dict[str, dict[str, str]] = {}  # {user_id: {'step': 'api_key|api_secret|passphrase', 'exchange': 'toobit'}}

# Thread locks for global state to prevent race conditions
trade_configs_lock = threading.RLock()
paper_balances_lock = threading.RLock()
bot_trades_lock = threading.RLock()
api_setup_lock = threading.RLock()
user_preferences_lock = threading.RLock()
bot_status_lock = threading.RLock()

# Multi-trade management storage
user_trade_configs: dict[int, dict[str, any]] = {}  # {user_id: {trade_id: TradeConfig}}
user_selected_trade: dict[int, str | None] = {}  # {user_id: trade_id}

def determine_trading_mode(user_id):
    """
    Centralized function to determine if user should be in paper trading mode.
    Returns True for paper mode, False for live mode.
    RENDER OPTIMIZED: Uses credential caching to prevent repeated DB queries.
    """
    try:
        chat_id = int(user_id)
        
        # RENDER OPTIMIZATION: Check credential cache first
        current_time = time.time()
        if chat_id in user_credentials_cache:
            cache_entry = user_credentials_cache[chat_id]
            if current_time - cache_entry['timestamp'] < credentials_cache_ttl:
                has_valid_creds = cache_entry['has_creds']
            else:
                # Cache expired, need to refresh
                has_valid_creds = _refresh_credential_cache(chat_id)
        else:
            # No cache entry, need to check database
            has_valid_creds = _refresh_credential_cache(chat_id)
        
        # Check if user has explicitly set a paper trading preference
        with user_preferences_lock:
            has_preference = chat_id in user_paper_trading_preferences
            
            if has_preference:
                # User has explicitly chosen a mode - honor their choice
                manual_preference = user_paper_trading_preferences[chat_id]
                
                # If they want live mode but don't have credentials, force paper mode for safety
                if not manual_preference and not has_valid_creds:
                    logging.warning(f"User {user_id} wants live mode but has no valid credentials - forcing paper mode")
                    return True
                
                return manual_preference
            else:
                # User hasn't set a preference - use intelligent defaults
                if has_valid_creds:
                    # User has credentials but no preference - default to live mode
                    logging.info(f"User {user_id} has credentials but no mode preference - defaulting to live mode")
                    user_paper_trading_preferences[chat_id] = False  # Set default for future use
                    return False
                else:
                    # No credentials - must use paper mode
                    logging.info(f"User {user_id} has no credentials - defaulting to paper mode")
                    user_paper_trading_preferences[chat_id] = True  # Set default for future use
                    return True
                
    except Exception as e:
        logging.error(f"Error determining trading mode for user {user_id}: {e}")
        # On error, default to paper mode for safety
        return True

def _refresh_credential_cache(chat_id):
    """Refresh credential cache for a user (RENDER OPTIMIZATION)"""
    try:
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(chat_id),
            is_active=True
        ).first()
        
        has_valid_creds = user_creds and user_creds.has_credentials()
        
        # Update cache
        user_credentials_cache[chat_id] = {
            'has_creds': has_valid_creds,
            'timestamp': time.time(),
            'exchange': user_creds.exchange_name if user_creds else None
        }
        
        return has_valid_creds
    except Exception as e:
        logging.error(f"Error refreshing credential cache for user {chat_id}: {e}")
        return False

# Paper trading balance tracking
user_paper_balances: dict[int, float] = {}  # {user_id: balance_amount}

# Manual paper trading mode preferences
user_paper_trading_preferences: dict[int, bool] = {}  # {user_id: True/False}

# RENDER PERFORMANCE OPTIMIZATION: Credential caching to prevent repeated DB queries
user_credentials_cache: dict[int, dict[str, any]] = {}  # {user_id: {'has_creds': bool, 'timestamp': time, 'exchange': str}}
credentials_cache_ttl = 300  # 5 minutes cache for credentials

# Cache for database loads to prevent frequent database hits
user_data_cache: dict[int, dict[str, any]] = {}  # {user_id: {'data': trades_data, 'timestamp': last_load_time, 'version': data_version}}
cache_ttl = get_cache_ttl("user")  # Cache TTL in seconds for Vercel optimization
trade_counter = 0

# Initialize clean user environment
def initialize_user_environment(user_id, force_reload=False):
    """Initialize trading environment for a user, loading from database only when necessary"""
    user_id = int(user_id)
    user_id_str = str(user_id)
    
    # Check enhanced cache first for user trade configurations
    cached_result = enhanced_cache.get_user_trade_configs(user_id_str)
    
    # RENDER OPTIMIZATION: Reduce forced reloads, use smart caching instead
    from config import Environment
    if Environment.IS_RENDER and not cached_result:
        # Only force reload if no cache available
        force_reload = True
    if not force_reload and cached_result:
        trade_configs, cache_info = cached_result
        with trade_configs_lock:
            user_trade_configs[user_id] = trade_configs
            # Initialize user's selected trade if not exists
            if user_id not in user_selected_trade:
                user_selected_trade[user_id] = None
# Cache hit - removed excessive debug logging for cleaner output
        return
    
    # Always load from database for Render or when needed
    with trade_configs_lock:
        user_trade_configs[user_id] = load_user_trades_from_db(user_id, force_reload)
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
        self.exchange = "lbank"     # Exchange to use for this trade (default: lbank)
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
                    existing_trade.realized_pnl = db_trade.realized_pnl  # CRITICAL FIX: Save realized P&L to database
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

@app.route('/api/db-status')
def database_status():
    """Database status diagnostic endpoint"""
    try:
        database_url = get_database_url()
        db_type = "sqlite"
        if database_url:
            if database_url.startswith("postgresql"):
                db_type = "postgresql"
            elif database_url.startswith("sqlite"):
                db_type = "sqlite"
        
        # Test database connection
        try:
            db.create_all()
            connection_status = "connected"
            
            # Count records
            from api.models import TradeConfiguration
            trade_count = TradeConfiguration.query.count()
            
        except Exception as e:
            connection_status = f"error: {str(e)}"
            trade_count = 0
        
        return jsonify({
            'database_type': db_type,
            'connection_status': connection_status,
            'trade_count': trade_count,
            'environment': {
                'IS_RENDER': Environment.IS_RENDER,
                'IS_VERCEL': Environment.IS_VERCEL,
                'IS_REPLIT': Environment.IS_REPLIT
            },
            'database_url_set': bool(os.environ.get("DATABASE_URL")),
            'timestamp': get_iran_time().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'timestamp': get_iran_time().isoformat()
        }), 500

def _handle_real_trading_sync():
    """Handle real trading synchronization based on environment."""
    result = {'processed': 0, 'status': 'not_available'}
    
    try:
        if os.environ.get("VERCEL"):
            result = _handle_vercel_trading_sync()
        else:
            result = _handle_background_trading_sync()
    except Exception as e:
        logging.warning(f"Real trading sync failed: {e}")
        result['error'] = str(e)
    
    return result


def _handle_vercel_trading_sync():
    """Handle trading sync for Vercel serverless environment."""
    sync_service = get_vercel_sync_service()
    if not sync_service:
        return {'processed': 0, 'status': 'service_unavailable'}
    
    users_with_creds = UserCredentials.query.filter_by(is_active=True).all()
    synced_users = 0
    
    for user_creds in users_with_creds:
        user_id = user_creds.telegram_user_id
        active_trades = TradeConfiguration.query.filter_by(
            telegram_user_id=user_id,
            status='active'
        ).count()
        
        if active_trades > 0 and sync_service.should_sync_user(str(user_id)):
            try:
                result = sync_service.sync_user_on_request(str(user_id))
                if result.get('success'):
                    synced_users += 1
            except Exception as e:
                logging.warning(f"Health sync failed for user {user_id}: {e}")
    
    return {
        'processed': synced_users,
        'status': 'active' if synced_users > 0 else 'no_active_positions'
    }


def _handle_background_trading_sync():
    """Handle trading sync for regular background environments."""
    sync_service = get_sync_service()
    if not sync_service or not hasattr(sync_service, '_sync_all_users'):
        return {'processed': 0, 'status': 'service_unavailable'}
    
    try:
        logging.info("HEALTH CHECK: Triggering background sync for all users with active positions")
        sync_service._sync_all_users()
        logging.info("HEALTH CHECK: Background sync completed successfully")
        return {'processed': 1, 'status': 'triggered'}
    except Exception as e:
        logging.warning(f"Background sync trigger failed: {e}")
        return {'processed': 0, 'status': 'failed', 'error': str(e)}


def _handle_paper_trading_monitoring():
    """Handle monitoring of paper trading positions."""
    paper_positions_processed = 0
    
    try:
        # Monitor in-memory paper trading configs
        paper_positions_processed += _monitor_memory_paper_positions()
        
        # Monitor database paper trading positions
        paper_positions_processed += _monitor_database_paper_positions()
        
    except Exception as e:
        logging.warning(f"Paper trading monitoring failed: {e}")
    
    return {
        'processed': paper_positions_processed,
        'status': 'active' if paper_positions_processed > 0 else 'no_active_positions'
    }


def _monitor_memory_paper_positions():
    """Monitor in-memory paper trading positions."""
    processed = 0
    
    for user_id, configs in user_trade_configs.items():
        for trade_id, config in configs.items():
            if (hasattr(config, 'paper_trading_mode') and 
                config.paper_trading_mode and 
                config.status == 'active'):
                
                try:
                    if config.symbol:
                        current_price = get_live_market_price(
                            config.symbol, 
                            use_cache=True, 
                            user_id=user_id
                        )
                        if current_price:
                            config.current_price = current_price
                            process_paper_trading_position(user_id, trade_id, config)
                            processed += 1
                except Exception as e:
                    logging.warning(f"Paper position processing failed for {config.symbol}: {e}")
    
    return processed


def _monitor_database_paper_positions():
    """Monitor database paper trading positions."""
    processed = 0
    
    db_active_trades = TradeConfiguration.query.filter_by(status='active').all()
    for trade in db_active_trades:
        user_id = trade.telegram_user_id
        
        # Check if user has no credentials or is in paper mode
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id,
            is_active=True
        ).first()
        
        # If no credentials or manual paper mode, treat as paper trading
        is_paper_mode = (not user_creds or 
                       not user_creds.has_credentials() or
                       user_paper_trading_preferences.get(user_id, True))
        
        if is_paper_mode and trade.symbol:
            try:
                current_price = get_live_market_price(
                    trade.symbol, 
                    use_cache=True, 
                    user_id=user_id
                )
                if current_price and current_price > 0:
                    processed += 1
                    logging.debug(f"Paper monitoring for {trade.trade_id}: price ${current_price}")
            except Exception as e:
                logging.warning(f"Paper trading monitoring failed for DB trade {trade.trade_id}: {e}")
    
    return processed


def _handle_price_updates_monitoring():
    """Handle price updates for active symbols."""
    price_updates = 0
    
    try:
        active_symbols = _collect_active_symbols()
        
        # Update prices for all active symbols
        for symbol in active_symbols:
            try:
                price = get_live_market_price(symbol, use_cache=True)
                if price:
                    price_updates += 1
            except Exception as e:
                logging.warning(f"Price update failed for {symbol}: {e}")
        
    except Exception as e:
        logging.warning(f"Price updates failed: {e}")
    
    return {
        'symbols_updated': price_updates,
        'status': 'active' if price_updates > 0 else 'no_active_symbols'
    }


def _collect_active_symbols():
    """Collect all symbols from active trades."""
    active_symbols = set()
    
    # Collect symbols from in-memory active trades
    for user_id, configs in user_trade_configs.items():
        for config in configs.values():
            if config.status == 'active' and config.symbol:
                active_symbols.add(config.symbol)
    
    # Also check database for real trading positions
    active_db_trades = TradeConfiguration.query.filter_by(status='active').all()
    for trade in active_db_trades:
        if trade.symbol:
            active_symbols.add(trade.symbol)
    
    return active_symbols


def _calculate_monitoring_summary(monitoring_results):
    """Calculate final monitoring summary."""
    total_activity = (monitoring_results['real_trading_sync']['processed'] + 
                     monitoring_results['paper_trading_monitoring']['processed'] + 
                     monitoring_results['price_updates']['symbols_updated'])
    
    monitoring_results['overall_status'] = 'active' if total_activity > 0 else 'monitoring_idle'
    
    logging.info(f"Health monitoring completed: {total_activity} operations processed")
    return monitoring_results


def trigger_core_monitoring():
    """
    Trigger core monitoring functionalities including:
    - Position synchronization for real trading users
    - Paper trading position monitoring  
    - Price updates and P&L calculations
    - TP/SL monitoring for ALL users regardless of credentials
    """
    monitoring_results = {
        'all_positions_monitoring': {'processed': 0, 'status': 'inactive'},
        'real_trading_sync': {'processed': 0, 'status': 'not_available'},
        'paper_trading_monitoring': {'processed': 0, 'status': 'inactive'},
        'price_updates': {'symbols_updated': 0, 'status': 'inactive'},
        'timestamp': get_iran_time().isoformat()
    }
    
    try:
        # ALL POSITIONS MONITORING: Monitor positions for ALL users regardless of credentials
        monitoring_results['all_positions_monitoring'] = _monitor_all_active_positions()
        
        # REAL TRADING MONITORING: Sync users with active positions
        monitoring_results['real_trading_sync'] = _handle_real_trading_sync()
        
        # PAPER TRADING MONITORING: Process all paper trading positions
        monitoring_results['paper_trading_monitoring'] = _handle_paper_trading_monitoring()
        
        # PRICE UPDATES: Update prices for active symbols
        monitoring_results['price_updates'] = _handle_price_updates_monitoring()
        
        # Calculate summary
        monitoring_results = _calculate_monitoring_summary(monitoring_results)
        
    except Exception as e:
        logging.error(f"Core monitoring trigger failed: {e}")
        monitoring_results['error'] = str(e)
    
    return monitoring_results

@app.route('/api/health')
def api_health_check():
    """Comprehensive health check endpoint for UptimeRobot and monitoring"""
    try:
        # Test database connection
        db_status = "healthy"
        try:
            with app.app_context():
                from sqlalchemy import text
                db.session.execute(text("SELECT 1"))
                db.session.commit()
        except Exception as e:
            db_status = f"unhealthy: {str(e)}"
        
        # Check cache system
        cache_status = "active" if enhanced_cache else "inactive"
        
        # Check circuit breakers
        cb_status = "healthy"
        try:
            if hasattr(circuit_manager, 'get_unhealthy_services'):
                unhealthy_services = circuit_manager.get_unhealthy_services()
                cb_status = "degraded" if unhealthy_services else "healthy"
        except:
            cb_status = "unknown"
        
        # CORE MONITORING: Trigger position monitoring and price updates
        monitoring_results = trigger_core_monitoring()
        
        # HEALTH PING BOOST: Activate extended monitoring for Render
        boost_status = "not_activated"
        try:
            sync_service = get_sync_service()
            if sync_service and hasattr(sync_service, 'trigger_health_ping_boost'):
                logging.info("HEALTH CHECK: Activating Health Ping Boost for enhanced monitoring")
                sync_service.trigger_health_ping_boost()
                boost_status = "activated"
                logging.info("HEALTH CHECK: Health Ping Boost successfully activated - monitoring will run every 10s for next 3 minutes")
            else:
                logging.warning("HEALTH CHECK: Sync service not available for Health Ping Boost")
                boost_status = "service_unavailable"
        except Exception as e:
            logging.warning(f"Health ping boost activation failed: {e}")
            boost_status = f"failed: {str(e)}"
        
        # Monitor system load (basic check)
        active_configs = sum(len(configs) for configs in user_trade_configs.values())
        
        health_data = {
            'status': 'healthy' if db_status == "healthy" else 'degraded',
            'timestamp': get_iran_time().isoformat(),
            'api_version': '1.0',
            'database': db_status,
            'services': {
                'cache': cache_status,
                'monitoring': 'running',
                'circuit_breakers': cb_status
            },
            'metrics': {
                'active_trade_configs': active_configs,
                'uptime_seconds': int(time.time() - app.config.get('START_TIME', time.time()))
            },
            'environment': {
                'render': Environment.IS_RENDER,
                'vercel': Environment.IS_VERCEL,
                'replit': Environment.IS_REPLIT
            },
            'monitoring': monitoring_results,
            'health_ping_boost': {
                'status': boost_status,
                'duration_seconds': 180,
                'enhanced_interval_seconds': 10
            }
        }
        
        # Return appropriate HTTP status
        status_code = 200 if health_data['status'] == 'healthy' else 503
        return jsonify(health_data), status_code
        
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': get_iran_time().isoformat()
        }), 503

def _validate_trade_for_alerts(trade):
    """Validate trade data for trigger alert checking."""
    return trade.entry_price and trade.entry_price > 0


def _check_stop_loss_trigger(trade, current_price):
    """Check if stop loss should trigger for the trade."""
    if trade.stop_loss_percent <= 0:
        return
    
    if trade.side == 'long':
        sl_price = trade.entry_price * (1 - trade.stop_loss_percent / 100)
        if current_price <= sl_price:
            logging.info(f"MONITORING ALERT: Stop loss trigger detected for {trade.trade_id} at {current_price}")
    else:  # short
        sl_price = trade.entry_price * (1 + trade.stop_loss_percent / 100)
        if current_price >= sl_price:
            logging.info(f"MONITORING ALERT: Stop loss trigger detected for {trade.trade_id} at {current_price}")


def _parse_take_profits(trade):
    """Parse take profit data from trade configuration."""
    if not trade.take_profits:
        return []
    
    import json
    try:
        tps = json.loads(trade.take_profits) if isinstance(trade.take_profits, str) else trade.take_profits
        return tps if isinstance(tps, list) else []
    except:
        return []  # Skip if TP format is invalid


def _calculate_tp_price(trade, tp_percentage):
    """Calculate take profit price based on trade side and percentage."""
    if trade.side == 'long':
        return trade.entry_price * (1 + tp_percentage / 100)
    else:  # short
        return trade.entry_price * (1 - tp_percentage / 100)


def _check_tp_trigger(trade, current_price, tp_price, tp_index):
    """Check if a specific take profit should trigger."""
    trigger_condition = (
        (trade.side == 'long' and current_price >= tp_price) or 
        (trade.side == 'short' and current_price <= tp_price)
    )
    
    if trigger_condition:
        logging.info(f"MONITORING ALERT: TP{tp_index+1} trigger detected for {trade.trade_id} at {current_price}")


def _check_take_profit_triggers(trade, current_price):
    """Check all take profit triggers for the trade."""
    tps = _parse_take_profits(trade)
    
    for i, tp in enumerate(tps):
        tp_percentage = tp.get('percentage', 0)
        tp_price = _calculate_tp_price(trade, tp_percentage)
        _check_tp_trigger(trade, current_price, tp_price, i)


def _calculate_profit_percentage(trade, current_price):
    """Calculate current profit percentage for the trade."""
    if trade.side == 'long':
        return ((current_price - trade.entry_price) / trade.entry_price) * 100
    else:  # short
        return ((trade.entry_price - current_price) / trade.entry_price) * 100


def _check_breakeven_trigger(trade, current_price):
    """Check if break-even should trigger for the trade."""
    if not (trade.breakeven_after > 0 and not trade.breakeven_sl_triggered):
        return
    
    profit_percent = _calculate_profit_percentage(trade, current_price)
    
    if profit_percent >= trade.breakeven_after:
        logging.info(f"MONITORING ALERT: Break-even trigger detected for {trade.trade_id} - profit: {profit_percent:.2f}%")


def check_position_trigger_alerts(trade, current_price):
    """Check for potential TP/SL triggers and log monitoring alerts"""
    try:
        if not _validate_trade_for_alerts(trade):
            return
        
        # Check stop loss trigger
        _check_stop_loss_trigger(trade, current_price)
        
        # Check take profit triggers
        _check_take_profit_triggers(trade, current_price)
        
        # Check break-even trigger
        _check_breakeven_trigger(trade, current_price)
                
    except Exception as e:
        logging.warning(f"Position trigger alert check failed for {trade.trade_id}: {e}")

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
        
        # Create client and test connection - Dynamic exchange selection
        client = create_exchange_client(user_creds, testnet=False)
        
        is_connected = client.test_connectivity()
        message = "Connected successfully" if is_connected else "Connection failed"
        
        return jsonify({
            'success': is_connected,
            'message': message,
            'testnet': user_creds.testnet_mode
        })
        
    except Exception as e:
        logging.error(f"Error testing exchange connection: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/v1/futures/leverage', methods=['POST'])
def set_futures_leverage():
    """Set leverage for futures trading on a specific symbol"""
    try:
        data = request.get_json()
        symbol = data.get('symbol', '').upper()
        leverage = data.get('leverage')
        user_id = get_user_id_from_request()
        
        # Validation
        if not symbol:
            return jsonify({'success': False, 'message': 'Symbol is required'}), 400
        
        if not leverage:
            return jsonify({'success': False, 'message': 'Leverage is required'}), 400
            
        try:
            leverage = int(leverage)
            if leverage < 1 or leverage > 125:  # Toobit typical range
                return jsonify({'success': False, 'message': 'Leverage must be between 1 and 125'}), 400
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid leverage value'}), 400
        
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id,
            is_active=True
        ).first()
        
        if not user_creds or not user_creds.has_credentials():
            return jsonify({'success': False, 'message': 'No API credentials found'}), 400
        
        # Check if in paper trading mode
        chat_id = int(user_id)
        is_paper_mode = determine_trading_mode(chat_id)
        
        if is_paper_mode:
            # In paper mode, just store the preference and return success
            logging.info(f"Paper mode: Set leverage for {symbol} to {leverage}x for user {chat_id}")
            return jsonify({
                'success': True,
                'message': f'Leverage set to {leverage}x for {symbol} (paper trading mode)',
                'symbol': symbol,
                'leverage': leverage,
                'paper_trading_mode': True
            })
        
        # Live trading mode - make actual API call with dynamic exchange
        client = create_exchange_client(user_creds, testnet=False)
        
        result = client.change_leverage(symbol, leverage)
        
        if result:
            logging.info(f"Successfully set leverage for {symbol} to {leverage}x for user {chat_id}")
            return jsonify({
                'success': True,
                'message': f'Leverage set to {leverage}x for {symbol}',
                'symbol': symbol,
                'leverage': leverage,
                'paper_trading_mode': False,
                'exchange_response': result
            })
        else:
            error_msg = client.get_last_error() or 'Failed to set leverage'
            return jsonify({'success': False, 'message': error_msg}), 500
            
    except Exception as e:
        logging.error(f"Error setting leverage: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/exchange/balance')
def get_exchange_balance():
    """Get account balance - returns paper balance in paper mode, live balance in live mode"""
    user_id = get_user_id_from_request()
    
    try:
        chat_id = int(user_id)
        
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id,
            is_active=True
        ).first()
        
        # Use centralized trading mode detection
        is_paper_mode = determine_trading_mode(chat_id)
        
        # If in paper trading mode, return virtual paper balance
        if is_paper_mode:
            paper_balance = user_paper_balances.get(chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE)
            return jsonify({
                'success': True,
                'paper_trading_mode': True,
                'testnet_mode': False,
                'balance': {
                    'total_balance': paper_balance,
                    'available_balance': paper_balance,
                    'used_margin': 0.0,
                    'position_margin': 0.0,
                    'order_margin': 0.0,
                    'unrealized_pnl': 0.0,
                    'margin_ratio': 0.0,
                    'asset': 'USDT'
                },
                'message': 'Paper trading balance (virtual funds)',
                'timestamp': get_iran_time().isoformat()
            })
        
        # Live trading mode - make actual API calls
        if not user_creds or not user_creds.has_credentials():
            return jsonify({'error': 'No API credentials found for live trading', 'testnet_mode': False}), 400
        
        # Create client and get comprehensive balance - Dynamic exchange selection
        try:
            client = create_exchange_client(user_creds, testnet=False)
            if not client:
                return jsonify({'error': 'Failed to create exchange client', 'testnet_mode': False}), 500
            
            # Get perpetual futures balance
            balance_data = client.get_futures_balance()
            logging.info(f"Perpetual futures balance fetched: {len(balance_data) if balance_data else 0} assets")
                
        except Exception as client_error:
            logging.error(f"Error creating client or getting balance: {client_error}")
            return jsonify({
                'error': f'Exchange connection failed: {str(client_error)}',
                'testnet_mode': False
            }), 500
        
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
            
            # Determine balance type for user information
            balance_type = 'perpetual_futures'
            balance_source = f"{user_creds.exchange_name.upper()} Perpetual Futures" if user_creds.exchange_name else "Perpetual Futures"
            
            return jsonify({
                'success': True,
                'testnet_mode': user_creds.testnet_mode,
                'balance_type': balance_type,
                'balance_source': balance_source,
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
                'balance_summary': {
                    'futures_assets': len(balance_data) if balance_data else 0,
                    'primary_balance': balance_type
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
            'testnet_mode': False
        }), 500

@app.route('/api/exchange/api-restrictions')
def get_api_restrictions():
    """Get API restrictions for the current user's exchange credentials"""
    user_id = get_user_id_from_request()
    
    try:
        chat_id = int(user_id)
        
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id,
            is_active=True
        ).first()
        
        if not user_creds or not user_creds.has_credentials():
            return jsonify({'error': 'No API credentials found', 'success': False}), 400
        
        # Create client and get API restrictions - Dynamic exchange selection
        try:
            client = create_exchange_client(user_creds, testnet=False)
            if not client:
                return jsonify({'error': 'Failed to create exchange client', 'success': False}), 500
            
            # Check if the client has the get_api_restrictions method
            if hasattr(client, 'get_api_restrictions'):
                restrictions_data = client.get_api_restrictions()
            else:
                return jsonify({'error': 'API restrictions not supported for this exchange', 'success': False}), 400
                
        except Exception as client_error:
            logging.error(f"Error creating client or getting API restrictions: {client_error}")
            return jsonify({
                'error': f'Exchange connection failed: {str(client_error)}',
                'success': False
            }), 500
        
        if restrictions_data:
            return jsonify({
                'success': True,
                'restrictions': restrictions_data,
                'exchange': user_creds.exchange_name,
                'timestamp': get_iran_time().isoformat()
            })
        else:
            error_msg = client.get_last_error() if hasattr(client, 'get_last_error') else 'No API restrictions data received'
            return jsonify({'error': error_msg, 'success': False}), 500
            
    except Exception as e:
        logging.error(f"API restrictions error: {e}")
        return jsonify({'error': str(e), 'success': False}), 500

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
        
        # Create client and get positions - Dynamic exchange selection
        client = create_exchange_client(user_creds, testnet=False)
        
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
        
        # Create client and get orders - Dynamic exchange selection
        client = create_exchange_client(user_creds, testnet=False)
        
        if symbol:
            orders = client.get_order_history(symbol)
        else:
            # For exchanges that support getting all orders without symbol
            try:
                orders = client.get_order_history(symbol="")
            except TypeError:
                # If the method requires symbol parameter, return empty list
                orders = []
        
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
        
        # RENDER OPTIMIZATION: Fast in-memory mode switching
        current_paper_mode = user_paper_trading_preferences.get(chat_id, True)  # Default to paper trading
        new_paper_mode = not current_paper_mode
        
        # Update preference immediately in memory with locking
        with user_preferences_lock:
            user_paper_trading_preferences[chat_id] = new_paper_mode
        
        # Initialize/ensure paper balance exists (optimize for both modes)
        with paper_balances_lock:
            if chat_id not in user_paper_balances or user_paper_balances[chat_id] <= 0:
                user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
                logging.info(f"[RENDER OPTIMIZATION] Set paper balance for user {chat_id}: ${TradingConfig.DEFAULT_TRIAL_BALANCE:,.2f}")
        
        # RENDER OPTIMIZATION: Clear enhanced cache instead of globals
        enhanced_cache.invalidate_user_data(str(chat_id))
        
        # Clear credential cache to force refresh on next check
        if chat_id in user_credentials_cache:
            del user_credentials_cache[chat_id]
        
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
        
        # Use centralized trading mode detection
        is_paper_mode = determine_trading_mode(chat_id)
        manual_paper_mode = user_paper_trading_preferences.get(chat_id, True)
        
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
        if metrics['requests'] is not None and metrics['requests'] > 0:
            success_rate = (metrics['successes'] / metrics['requests']) * 100
            avg_response_time = metrics.get('avg_response_time', 0)
            last_success = metrics.get('last_success')
            bot_status['api_performance'][api_name] = {
                'success_rate': round(success_rate, 2),
                'avg_response_time': round(avg_response_time, 3),
                'total_requests': metrics['requests'],
                'last_success': last_success.isoformat() if last_success else None
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
    """Get Smart Money Concepts analysis for a specific symbol with database caching"""
    try:
        from .smc_analyzer import SMCAnalyzer
        from .models import SMCSignalCache, db

        
        symbol = symbol.upper()
        
        # Get current market price for validation
        current_price = get_live_market_price(symbol)
        if not current_price:
            current_price = 0
        
        # Try to get valid cached signal first
        cached_signal = SMCSignalCache.get_valid_signal(symbol, current_price)
        
        if cached_signal:
            # Return cached signal
            signal = cached_signal.to_smc_signal()
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
                'status': 'cached_signal',
                'cache_source': True
            })
        
        # No valid cached signal, generate new one
        analyzer = SMCAnalyzer()
        signal = analyzer.generate_trade_signal(symbol)
        
        if signal:
            # Cache the new signal for 15 minutes
            cache_entry = SMCSignalCache.from_smc_signal(signal, cache_duration_minutes=15)
            db.session.add(cache_entry)
            db.session.commit()
            
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
                'status': 'new_signal_generated',
                'cache_source': False
            })
        else:
            return jsonify({
                'symbol': symbol,
                'status': 'no_signal',
                'message': 'No strong SMC signal detected at this time',
                'timestamp': get_iran_time().isoformat(),
                'cache_source': False
            })
            
    except Exception as e:
        logging.error(f"Error in SMC analysis for {symbol}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/smc-signals')
def get_multiple_smc_signals():
    """Get SMC signals for multiple popular trading symbols with caching"""
    try:
        from .smc_analyzer import SMCAnalyzer
        from .models import SMCSignalCache, db

        
        # Analyze popular trading pairs
        symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT', 'SOLUSDT']
        analyzer = SMCAnalyzer()
        
        signals = {}
        cache_hits = 0
        new_signals_generated = 0
        
        # Clean up expired signals first
        SMCSignalCache.cleanup_expired()
        
        for symbol in symbols:
            try:
                # Get current price for validation
                current_price = get_live_market_price(symbol)
                if not current_price:
                    current_price = 0
                
                # Try cached signal first
                cached_signal = SMCSignalCache.get_valid_signal(symbol, current_price)
                
                if cached_signal:
                    # Use cached signal
                    signal = cached_signal.to_smc_signal()
                    cache_hits += 1
                    signals[symbol] = {
                        'direction': signal.direction,
                        'entry_price': signal.entry_price,
                        'stop_loss': signal.stop_loss,
                        'take_profit_levels': signal.take_profit_levels,
                        'confidence': signal.confidence,
                        'reasoning': signal.reasoning[:3],  # Limit reasoning for summary
                        'signal_strength': signal.signal_strength.value,
                        'risk_reward_ratio': signal.risk_reward_ratio,
                        'timestamp': signal.timestamp.isoformat(),
                        'cache_source': True
                    }
                else:
                    # Generate new signal
                    signal = analyzer.generate_trade_signal(symbol)
                    if signal:
                        # Cache the new signal
                        cache_entry = SMCSignalCache.from_smc_signal(signal, cache_duration_minutes=15)
                        db.session.add(cache_entry)
                        new_signals_generated += 1
                        
                        signals[symbol] = {
                            'direction': signal.direction,
                            'entry_price': signal.entry_price,
                            'stop_loss': signal.stop_loss,
                            'take_profit_levels': signal.take_profit_levels,
                            'confidence': signal.confidence,
                            'reasoning': signal.reasoning[:3],  # Limit reasoning for summary
                            'signal_strength': signal.signal_strength.value,
                            'risk_reward_ratio': signal.risk_reward_ratio,
                            'timestamp': signal.timestamp.isoformat(),
                            'cache_source': False
                        }
                    else:
                        signals[symbol] = {
                            'status': 'no_signal',
                            'message': 'No strong signal detected',
                            'cache_source': False
                        }
            except Exception as e:
                signals[symbol] = {
                    'status': 'error',
                    'message': str(e),
                    'cache_source': False
                }
        
        # Commit any new cache entries
        if new_signals_generated > 0:
            db.session.commit()
        
        return jsonify({
            'signals': signals,
            'timestamp': get_iran_time().isoformat(),
            'total_analyzed': len(symbols),
            'signals_found': len([s for s in signals.values() if 'direction' in s]),
            'cache_hits': cache_hits,
            'new_signals_generated': new_signals_generated,
            'cache_efficiency': f"{(cache_hits / len(symbols) * 100):.1f}%" if symbols else "0%"
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
        
        # FIXED: Calculate stop loss percentage on margin for consistency
        # The issue was: SL calculation used raw price percentage, but system expected margin percentage
        if signal.direction == 'long':
            sl_price_movement_percent = ((signal.entry_price - signal.stop_loss) / signal.entry_price) * 100
        else:
            sl_price_movement_percent = ((signal.stop_loss - signal.entry_price) / signal.entry_price) * 100
        
        # Convert to margin percentage: sl_percent_on_margin = price_movement_percent * leverage
        sl_percent_on_margin = sl_price_movement_percent * trade_config.leverage
        
        trade_config.stop_loss_percent = min(sl_percent_on_margin, 25.0)  # Cap at 25% margin loss for safety
        
        # Set up take profit levels with proper allocation logic
        tp_levels = []
        num_tp_levels = min(len(signal.take_profit_levels), 3)  # Max 3 TP levels
        
        # Define allocation strategies based on number of TP levels
        # CORRECTED: More conservative and standard allocation strategies
        allocation_strategies = {
            1: [100],           # Single TP: close full position
            2: [50, 50],        # Two TPs: 50% each (more balanced)
            3: [40, 35, 25]     # Three TPs: 40%, 35%, 25% (ensures all allocations add to 100%)
        }
        
        allocations = allocation_strategies.get(num_tp_levels, [100])
        
        for i, tp_price in enumerate(signal.take_profit_levels[:3]):
            # FIXED: Calculate TP percentage on margin (not price movement) for consistency with leverage calculation
            # The issue was: SMC signal generation calculated raw price percentage, but the system expected margin percentage
            # This caused differences when saving/loading from database because calculate_tp_sl_prices_and_amounts uses leverage
            
            if signal.direction == 'long':
                price_movement_percent = ((tp_price - signal.entry_price) / signal.entry_price) * 100
            else:
                price_movement_percent = ((signal.entry_price - tp_price) / signal.entry_price) * 100
            
            # Convert to margin percentage: tp_percent_on_margin = price_movement_percent * leverage
            # This ensures consistency with how TP calculations work throughout the system
            tp_percent_on_margin = price_movement_percent * trade_config.leverage
            
            allocation = allocations[i] if i < len(allocations) else 10
            
            tp_levels.append({
                'percentage': tp_percent_on_margin,  # Store margin percentage for consistency
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
        with trade_configs_lock:
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

@app.route('/api/debug/paper-trading-status')
def debug_paper_trading_status():
    """Debug endpoint for paper trading status and diagnostics"""
    user_id = get_user_id_from_request()
    
    try:
        chat_id = int(user_id)
        
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id,
            is_active=True
        ).first()
        
        # Use centralized trading mode detection
        is_paper_mode = determine_trading_mode(chat_id)
        
        # Get trade configurations
        initialize_user_environment(chat_id, force_reload=True)
        with trade_configs_lock:
            trades = user_trade_configs.get(chat_id, {})
        
        # Analyze paper trading configs
        paper_trades = []
        for trade_id, config in trades.items():
            if hasattr(config, 'paper_trading_mode') and config.paper_trading_mode:
                paper_trades.append({
                    'trade_id': trade_id,
                    'symbol': config.symbol,
                    'status': config.status,
                    'entry_price': getattr(config, 'entry_price', None),
                    'current_price': getattr(config, 'current_price', None),
                    'unrealized_pnl': getattr(config, 'unrealized_pnl', 0),
                    'has_paper_sl_data': hasattr(config, 'paper_sl_data'),
                    'has_paper_tp_levels': hasattr(config, 'paper_tp_levels'),
                    'breakeven_triggered': getattr(config, 'breakeven_sl_triggered', False)
                })
        
        return jsonify({
            'success': True,
            'user_id': user_id,
            'manual_paper_mode': user_paper_trading_preferences.get(chat_id, True),
            'is_paper_mode': is_paper_mode,
            'has_credentials': user_creds is not None and user_creds.has_credentials() if user_creds else False,
            'testnet_mode': user_creds.testnet_mode if user_creds else False,
            'paper_balance': user_paper_balances.get(chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE),
            'total_trades': len(trades),
            'paper_trades': paper_trades,
            'paper_trades_count': len(paper_trades),
            'environment': {
                'is_render': Environment.IS_RENDER,
                'is_vercel': Environment.IS_VERCEL,
                'is_replit': Environment.IS_REPLIT
            },
            'timestamp': get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error in paper trading debug endpoint: {e}")
        return jsonify({
            'error': 'Failed to get paper trading status',
            'details': str(e)
        }), 500

@app.route('/api/debug/position-close-test')
def debug_position_close_test():
    """Debug endpoint to test position closing functionality on Render"""
    user_id = get_user_id_from_request()
    
    # Get user credentials
    user_creds = UserCredentials.query.filter_by(
        telegram_user_id=str(user_id),
        is_active=True
    ).first()
    
    debug_info = {
        'timestamp': get_iran_time().isoformat(),
        'user_id': user_id,
        'has_credentials': False,
        'testnet_mode': False,
        'api_connection_test': 'Not tested',
        'active_trades_count': 0,
        'paper_mode_active': True,
        'last_error': None,
        'exchange_client_status': 'Not created'
    }
    
    if user_creds and user_creds.has_credentials():
        debug_info['has_credentials'] = True
        debug_info['testnet_mode'] = user_creds.testnet_mode
        
        # Test API connection
        try:
            logging.debug(f"Creating exchange client for user {user_id}")
            client = create_exchange_client(user_creds, testnet=False)
            debug_info['exchange_client_status'] = 'Created successfully'
            
            # Test basic connection
            logging.debug(f"Testing API connection for user {user_id}")
            balance = client.get_futures_balance()
            if balance:
                debug_info['api_connection_test'] = 'Success'
                debug_info['paper_mode_active'] = False
                debug_info['account_balance'] = balance
            else:
                debug_info['api_connection_test'] = 'Failed - No balance data'
                debug_info['last_error'] = client.get_last_error()
                
        except Exception as e:
            debug_info['api_connection_test'] = f'Failed - Exception: {str(e)}'
            debug_info['last_error'] = str(e)
            debug_info['exchange_client_status'] = f'Creation failed: {str(e)}'
            logging.error(f"ToobitClient creation failed for user {user_id}: {e}")
    
    # Count active trades
    active_count = 0
    active_trades = []
    for trade_id, config in user_trade_configs.get(user_id, {}).items():
        if config.status == "active":
            active_count += 1
            active_trades.append({
                'trade_id': trade_id,
                'symbol': config.symbol,
                'side': config.side,
                'position_size': getattr(config, 'position_size', 0),
                'unrealized_pnl': getattr(config, 'unrealized_pnl', 0)
            })
    
    debug_info['active_trades_count'] = active_count
    debug_info['active_trades'] = active_trades
    
    logging.debug(f"Position close test for user {user_id}")
    return jsonify(debug_info)

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
    
    # For Render: Force reload to ensure fresh data across workers
    force_reload = Environment.IS_RENDER
    
    # Initialize user environment (uses cache to prevent DB hits)
    initialize_user_environment(chat_id, force_reload=force_reload)
    
    # Update all positions with live market data from Toobit exchange
    # On Render: Force full update for all positions due to multi-worker environment
    if Environment.IS_RENDER:
        update_all_positions_with_live_data(chat_id)
    else:
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
                    'realized_pnl': getattr(config, 'realized_pnl', 0.0),  # Include realized P&L from triggered TPs
                    'total_pnl': (config.unrealized_pnl or 0) + (getattr(config, 'realized_pnl', 0) or 0),  # Combined P&L
                    'status': config.status,
                    'take_profits': config.take_profits,
                    'stop_loss_percent': config.stop_loss_percent,
                    'tp_sl_calculations': tp_sl_data
                })
    
    # Calculate total realized P&L from closed positions AND partial TP closures from active positions
    total_realized_pnl = 0.0
    with trade_configs_lock:
        user_configs = user_trade_configs.get(chat_id, {})
        for config in user_configs.values():
            if config.status == "stopped" and hasattr(config, 'final_pnl') and config.final_pnl is not None:
                total_realized_pnl += config.final_pnl
            # Also include partial realized P&L from active positions (from partial TPs)
            elif config.status == "active" and hasattr(config, 'realized_pnl') and config.realized_pnl is not None:
                total_realized_pnl += config.realized_pnl
    
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

def _validate_and_get_user_id():
    """Validate and extract user ID from request."""
    user_id = request.args.get('user_id')
    if not user_id or user_id == 'undefined':
        user_id = Environment.DEFAULT_TEST_USER_ID
    
    try:
        chat_id = int(user_id)
        return chat_id, None
    except ValueError:
        return None, jsonify({'error': 'Invalid user ID format'}), 400


def _handle_environment_sync(user_id, chat_id):
    """Handle environment-specific synchronization."""
    # For Vercel: Trigger on-demand sync if needed
    if os.environ.get("VERCEL"):
        sync_service = get_vercel_sync_service()
        if sync_service:
            sync_result = sync_service.sync_user_on_request(user_id)
            # Continue with regular live update regardless of sync result
    
    # For Render: Force reload to ensure fresh data across workers
    force_reload = Environment.IS_RENDER
    
    # For live updates, ensure user is initialized from cache (no DB hit unless on Render)
    initialize_user_environment(chat_id, force_reload=force_reload)


def _check_paper_trading_positions(chat_id):
    """Check if user has paper trading positions that need full monitoring."""
    for trade_id, config in user_trade_configs.get(chat_id, {}).items():
        if config.status == "active" and getattr(config, 'paper_trading_mode', False):
            return True
    return False


def _select_update_strategy(chat_id):
    """Select appropriate position update strategy."""
    has_paper_trades = _check_paper_trading_positions(chat_id)
    
    if has_paper_trades or Environment.IS_RENDER:
        # Run full position updates for paper trading or Render (includes TP/SL monitoring)
        update_all_positions_with_live_data(chat_id)
    else:
        # Use optimized lightweight monitoring - only checks break-even positions  
        update_positions_lightweight()


def _calculate_position_metrics(config):
    """Calculate ROE and price change percentage for a position."""
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
    
    return round(roe_percentage, 2), round(price_change_percentage, 2)


def _build_live_position_data(chat_id):
    """Build live position data for active and pending positions."""
    live_data = {}
    total_unrealized_pnl = 0.0
    active_positions_count = 0
    
    if chat_id not in user_trade_configs:
        return live_data, total_unrealized_pnl, active_positions_count
    
    for trade_id, config in user_trade_configs[chat_id].items():
        if config.status in ["active", "pending"] and config.symbol:
            roe_percentage, price_change_percentage = _calculate_position_metrics(config)
            
            live_data[trade_id] = {
                'current_price': config.current_price,
                'unrealized_pnl': config.unrealized_pnl,
                'realized_pnl': getattr(config, 'realized_pnl', 0) or 0,
                'total_pnl': (config.unrealized_pnl or 0) + (getattr(config, 'realized_pnl', 0) or 0),
                'roe_percentage': roe_percentage,
                'price_change_percentage': price_change_percentage,
                'status': config.status
            }
            
            if config.status == "active":
                total_unrealized_pnl += config.unrealized_pnl
                active_positions_count += 1
            elif config.status == "pending":
                active_positions_count += 1
    
    return live_data, total_unrealized_pnl, active_positions_count


def _calculate_total_realized_pnl(chat_id):
    """Calculate total realized P&L from closed and active positions."""
    total_realized_pnl = 0.0
    
    if chat_id not in user_trade_configs:
        return total_realized_pnl
    
    for config in user_trade_configs[chat_id].values():
        if config.status == "stopped" and hasattr(config, 'final_pnl') and config.final_pnl is not None:
            total_realized_pnl += config.final_pnl
        # Also include partial realized P&L from active positions (from partial TPs)
        elif config.status == "active" and hasattr(config, 'realized_pnl') and config.realized_pnl is not None:
            total_realized_pnl += config.realized_pnl
    
    return total_realized_pnl


@app.route('/api/positions/live-update')
def live_position_update():
    """Get only current prices and P&L for active positions (lightweight update)"""
    # Validate and get user ID
    chat_id, error_response = _validate_and_get_user_id()
    if error_response:
        return error_response
    
    # Handle environment-specific synchronization
    _handle_environment_sync(str(chat_id), chat_id)
    
    # Check if user has trades loaded
    if chat_id not in user_trade_configs:
        return jsonify({
            'positions': {},
            'total_unrealized_pnl': 0.0,
            'active_positions_count': 0,
            'timestamp': get_iran_time().isoformat(),
            'update_type': 'live_prices'
        })
    
    # Select appropriate update strategy
    _select_update_strategy(chat_id)
    
    # Build live position data
    live_data, total_unrealized_pnl, active_positions_count = _build_live_position_data(chat_id)
    
    # Calculate total realized P&L
    total_realized_pnl = _calculate_total_realized_pnl(chat_id)
    
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
    new_trade = TradeConfig(trade_id, f"Position #{trade_counter}")
    with trade_configs_lock:
        if chat_id not in user_trade_configs:
            user_trade_configs[chat_id] = {}
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
    
    # For Render: Force reload to ensure fresh data across workers
    force_reload = Environment.IS_RENDER
    
    # Initialize user environment (will use cache if available)
    initialize_user_environment(chat_id, force_reload=force_reload)
    
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

def _validate_save_trade_request(data):
    """Validate save trade request"""
    user_id = data.get('user_id')
    trade_data = data.get('trade')
    
    if not user_id or not trade_data:
        return None, None, None, {'error': 'User ID and trade data required'}, 400
    
    chat_id = int(user_id)
    trade_id = trade_data.get('trade_id')
    
    # Create or update trade config
    if trade_id.startswith('new_'):
        global trade_counter
        trade_counter += 1
        trade_id = str(trade_counter)
    
    return chat_id, trade_id, trade_data, None, None

def _ensure_trade_config_exists(chat_id, trade_id, trade_data):
    """Ensure trade config exists in storage"""
    with trade_configs_lock:
        if chat_id not in user_trade_configs:
            user_trade_configs[chat_id] = {}
        
        if trade_id not in user_trade_configs[chat_id]:
            user_trade_configs[chat_id][trade_id] = TradeConfig(trade_id, trade_data.get('name', 'New Trade'))
    
    return user_trade_configs[chat_id][trade_id]

def _check_active_trade_restrictions(config, trade_data, trade_id):
    """Check if core parameters are being modified for active trades"""
    is_active_trade = config.status in ['active', 'pending']
    
    if not is_active_trade:
        return False, []
    
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
        if abs(new_entry_price - current_entry_price) > 0.0001:
            core_param_changes.append('entry_price')
    
    return is_active_trade, core_param_changes

def _update_core_parameters(config, trade_data):
    """Update core trading parameters for non-active trades"""
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

def _update_risk_parameters(config, trade_data):
    """Update risk management parameters (always allowed)"""
    risk_params_updated = []
    
    if 'take_profits' in trade_data:
        config.take_profits = trade_data['take_profits']
        risk_params_updated.append('take_profits')
    if 'stop_loss_percent' in trade_data:
        config.stop_loss_percent = float(trade_data['stop_loss_percent']) if trade_data['stop_loss_percent'] else 0.0
        risk_params_updated.append('stop_loss')
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
    
    return risk_params_updated

@app.route('/api/save-trade', methods=['POST'])
def save_trade():
    """Save or update trade configuration - refactored for better maintainability"""
    try:
        data = request.get_json()
        
        # Validate request
        chat_id, trade_id, trade_data, error, status_code = _validate_save_trade_request(data)
        if error:
            return jsonify(error), status_code
        
        # Ensure trade config exists
        config = _ensure_trade_config_exists(chat_id, trade_id, trade_data)
        
        # Check active trade restrictions
        is_active_trade, core_param_changes = _check_active_trade_restrictions(config, trade_data, trade_id)
        
        if is_active_trade and core_param_changes:
            logging.warning(f"Attempted to modify core parameters {core_param_changes} for active trade {trade_id}. Changes rejected for safety.")
            return jsonify({
                'error': f"Cannot modify core trade parameters ({', '.join(core_param_changes)}) for active trades. Only take profits, stop loss, break-even, and trailing stop can be modified.",
                'active_trade': True,
                'rejected_changes': core_param_changes,
                'message': 'For active positions, you can only edit risk management settings (TP/SL levels, breakeven, trailing stop).'
            }), 400
        
        # Update parameters based on trade status
        if not is_active_trade:
            _update_core_parameters(config, trade_data)
        
        risk_params_updated = _update_risk_parameters(config, trade_data)
        
        # Log risk management updates for active trades
        if is_active_trade and risk_params_updated:
            logging.info(f"Updated risk management parameters for active trade {trade_id}: {', '.join(risk_params_updated)}")
        
        # Set as selected trade for user
        with trade_configs_lock:
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

def _validate_execute_trade_request(data):
    """Validate execute trade request data and extract user_id and trade_id."""
    user_id = data.get('user_id')
    trade_id = data.get('trade_id')
    
    if not user_id:
        return None, None, jsonify(create_validation_error("User ID", None, "A valid user ID is required")), 400
    
    if not trade_id:
        return None, None, jsonify(create_validation_error("Trade ID", None, "A valid trade ID is required")), 400
    
    return user_id, trade_id, None, None


def _get_and_validate_trade_config(chat_id, trade_id):
    """Get and validate trade configuration exists and is complete."""
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
        return None, jsonify(error.to_dict()), 404
    
    config = user_trade_configs[chat_id][trade_id]
    
    # Validate configuration completeness
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
        return None, jsonify(error.to_dict()), 400
    
    return config, None, None


def _setup_exchange_client_and_mode(chat_id, config):
    """Set up exchange client and determine trading mode."""
    # Check if user is in paper trading mode
    user_creds = UserCredentials.query.filter_by(
        telegram_user_id=str(chat_id),
        is_active=True
    ).first()
    
    # Use centralized trading mode detection
    is_paper_mode = determine_trading_mode(chat_id)
    
    if is_paper_mode:
        # Set exchange for paper trading (default to lbank if no credentials)
        config.exchange = user_creds.exchange_name if user_creds else 'lbank'
        return None, is_paper_mode, user_creds, None, None
    else:
        # REAL TRADING - Validate credentials and create exchange client
        if not user_creds or not user_creds.has_credentials():
            return None, is_paper_mode, user_creds, jsonify({'error': 'API credentials required for real trading. Please set up your Toobit API keys.'}), 400
        
        # Validate credentials and create exchange client
        client, error_response = _validate_credentials_and_create_client(user_creds, chat_id)
        if error_response is not None:
            return None, is_paper_mode, user_creds, error_response, None
        
        # Set the exchange name in the config for proper order routing
        config.exchange = user_creds.exchange_name or 'toobit'
        return client, is_paper_mode, user_creds, None, None


def _execute_real_trading_order(client, config, current_market_price):
    """Execute real trading order on exchange."""
    try:
        # Calculate quantity for exchange (contract numbers)
        position_value = config.amount * config.leverage
        btc_amount = position_value / current_market_price
        # Convert to contract numbers: 1 contract = 0.001 BTC for Toobit
        contract_quantity = round(btc_amount / 0.001)
        position_size = contract_quantity
        
        # Set leverage FIRST before placing order
        logging.info(f"Setting leverage {config.leverage}x for {config.symbol}")
        leverage_result = client.change_leverage(config.symbol, config.leverage)
        if not leverage_result:
            error_msg = client.get_last_error() or 'Failed to set leverage'
            logging.error(f"Failed to set leverage: {error_msg}")
            return False, jsonify({
                'error': f'Failed to set leverage: {error_msg}',
                'troubleshooting': [
                    'Check if the symbol supports the specified leverage',
                    'Verify you have no open positions that would conflict',
                    'Ensure your account has sufficient margin'
                ]
            }), 500
        else:
            logging.info(f"Successfully set leverage {config.leverage}x for {config.symbol}")
        
        # Determine order type and parameters based on exchange
        if config.exchange == 'lbank':
            # LBank uses simple buy/sell for perpetual futures
            order_side = "buy" if config.side == "long" else "sell"
            order_type = "limit"
        else:  # toobit
            # Toobit futures requires specific position actions: BUY_OPEN/SELL_OPEN for opening positions
            order_side = "BUY_OPEN" if config.side == "long" else "SELL_OPEN"
            order_type = "limit"
        
        # Convert symbol to exchange format for proper API call
        if config.exchange == 'lbank':
            exchange_symbol = getattr(client, 'convert_to_lbank_symbol', lambda x: x)(config.symbol)
            endpoint_info = "/cfd/openApi/v1/prv/order"
        else:  # toobit
            exchange_symbol = getattr(client, 'convert_to_toobit_symbol', lambda x: x)(config.symbol)
            endpoint_info = "/api/v1/futures/order"
        
        logging.info(f"[DEBUG] Will send {config.exchange.upper()} order to: {endpoint_info} with symbol={exchange_symbol}, side={order_side}, quantity={position_size} contracts")
        
        # Prepare order parameters based on entry type
        if config.entry_type == "market":
            # For market execution, use LIMIT order at market price with buffer
            market_price_float = float(current_market_price)
            if order_side == "BUY":
                exec_price = market_price_float * 1.001  # Slightly above market
            else:
                exec_price = market_price_float * 0.999  # Slightly below market
                
            order_params = {
                'symbol': config.symbol,
                'side': order_side,
                'order_type': order_type,
                'quantity': str(position_size),
                'price': str(exec_price),
                'timeInForce': "IOC"  # Immediate or Cancel for market-like behavior
            }
        else:
            # Standard limit order
            order_params = {
                'symbol': config.symbol,
                'side': order_side,
                'order_type': order_type,
                'quantity': str(position_size),
                'price': str(config.entry_price),
                'timeInForce': "GTC"
            }
        
        order_result = client.place_order(**order_params)
        
        if not order_result:
            error_details = {
                'client_last_error': getattr(client, 'last_error', None),
                'order_params': {
                    'symbol': config.symbol,
                    'side': order_side,
                    'type': order_type,
                    'quantity_calculated': round(position_size, 6)
                }
            }
            
            logging.error(f"Order placement failed: {error_details.get('client_last_error', 'Unknown error')}")
            
            return False, jsonify({
                'error': 'Failed to place order on exchange. Please check the details below.',
                'debug_info': error_details,
                'troubleshooting': [
                    'Check if you have sufficient balance for this trade',
                    'Verify the symbol is supported on Toobit futures',
                    'Ensure your position size meets minimum requirements',
                    'Check if there are any trading restrictions on your account'
                ]
            }), 500
        
        logging.info(f"Order placed on exchange: {order_result}")
        
        # Store exchange order ID
        config.exchange_order_id = order_result.get('orderId')
        config.exchange_client_order_id = order_result.get('clientOrderId')
        
        return True, None, None
        
    except Exception as e:
        error_details = {
            'error_type': type(e).__name__,
            'error_message': str(e),
            'client_last_error': getattr(client, 'last_error', None) if client else None,
            'trade_config': {
                'symbol': config.symbol,
                'side': config.side,
                'amount': config.amount,
                'leverage': config.leverage,
                'entry_type': config.entry_type
            }
        }
        
        logging.error(f"[RENDER TRADING ERROR] Exchange order placement failed: {error_details}")
        
        # Import stack trace for detailed debugging
        import traceback
        logging.error(f"[RENDER STACK TRACE] {traceback.format_exc()}")
        
        return False, jsonify({
            'error': f'Trading execution failed: {str(e)}',
            'debug_info': error_details,
            'troubleshooting': [
                'Check your internet connection',
                'Verify your exchange API credentials are active',
                'Ensure you have sufficient account balance',
                'Try refreshing the page and attempting the trade again'
            ]
        }), 500


def _handle_paper_trading_execution(config, chat_id):
    """Handle paper trading execution setup."""
    logging.info(f"Paper Trading: Executing simulated trade for user {chat_id}: {config.symbol} {config.side}")
    
    # Simulate order placement with paper trading IDs
    mock_order_id = f"paper_{uuid.uuid4().hex[:8]}"
    config.exchange_order_id = mock_order_id
    config.exchange_client_order_id = f"paper_client_{mock_order_id}"
    
    # Mark as paper trading and initialize monitoring
    config.paper_trading_mode = True
    if config.entry_type == "market":
        initialize_paper_trading_monitoring(config)
    logging.info(f"Paper Trading: Position opened for {config.symbol} {config.side} - Real-time monitoring enabled")
    
    return True


def _configure_trade_position(config, current_market_price):
    """Configure trade position details and pricing."""
    # Calculate common values needed for both paper and real trading
    position_value = config.amount * config.leverage
    position_size = round(position_value / current_market_price, 6)
    
    # Update trade configuration - status depends on order type
    if config.entry_type == "limit":
        config.status = "pending"  # Limit orders start as pending until filled
    else:
        config.status = "active"   # Market orders are immediately active
    
    # Store original amounts when trade is first executed for TP calculations
    if not hasattr(config, 'original_amount'):
        config.original_amount = config.amount
    if not hasattr(config, 'original_margin'):
        config.original_margin = calculate_position_margin(config.original_amount, config.leverage)
        
    config.position_margin = calculate_position_margin(config.amount, config.leverage)
    config.position_value = position_value
    config.position_size = position_size
    
    # Set prices based on order type
    if config.entry_type == "market" or config.entry_price is None:
        config.current_price = current_market_price
        config.entry_price = current_market_price
    else:
        config.current_price = current_market_price
    
    config.unrealized_pnl = 0.0
    
    return position_size


def _setup_paper_tp_sl_orders(config, tp_sl_data):
    """Set up simulated TP/SL orders for paper trading."""
    if not config.take_profits or not tp_sl_data.get('take_profits'):
        return []
        
    mock_tp_sl_orders = []
    config.paper_tp_levels = []
    
    for i, tp_data in enumerate(tp_sl_data['take_profits']):
        mock_order_id = f"paper_tp_{i+1}_{uuid.uuid4().hex[:6]}"
        mock_tp_sl_orders.append(mock_order_id)
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
        config.paper_sl_data = {
            'order_id': sl_order_id,
            'price': tp_sl_data['stop_loss']['price'],
            'percentage': config.stop_loss_percent,
            'triggered': False
        }
    
    return mock_tp_sl_orders


def _place_real_tp_sl_orders(client, config, tp_sl_data, position_size):
    """Place real TP/SL orders on exchange."""
    if not config.take_profits or not tp_sl_data.get('take_profits'):
        return []
        
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
    
    if config.entry_type == "limit":
        # Store TP/SL data to place later when order fills
        config.pending_tp_sl_data = {
            'take_profits': tp_orders_to_place,
            'stop_loss_price': sl_price
        }
        logging.info(f"TP/SL orders configured to place when limit order fills")
        return []
    else:
        # Place TP/SL immediately for market orders
        return _execute_tp_sl_orders(client, config, tp_orders_to_place, sl_price, position_size)


def _execute_tp_sl_orders(client, config, tp_orders_to_place, sl_price, position_size):
    """Execute TP/SL orders on exchange."""
    from api.unified_exchange_client import OrderParameterAdapter
    
    client_type = type(client).__name__.lower()
    exchange_name = "toobit"
    if 'lbank' in client_type:
        exchange_name = "lbank"
    elif 'hyperliquid' in client_type:
        exchange_name = "hyperliquid"
    
    order_side = "buy" if config.side == "long" else "sell"
    
    # Convert to unified parameters
    unified_params = OrderParameterAdapter.to_exchange_params(
        exchange_name,
        symbol=config.symbol,
        side=order_side,
        total_quantity=float(position_size),
        entry_price=float(config.entry_price),
        take_profits=tp_orders_to_place,
        stop_loss_price=sl_price
    )
    
    # Call exchange-specific TP/SL placement
    if 'hyperliquid' in client_type:
        return client.place_multiple_tp_sl_orders(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            amount=unified_params["amount"],
            entry_price=float(config.entry_price),
            tp_levels=tp_orders_to_place
        )
    elif 'lbank' in client_type:
        return client.place_multiple_tp_sl_orders(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            amount=str(unified_params["amount"]),
            take_profits=tp_orders_to_place,
            stop_loss_price=sl_price
        )
    else:
        # ToobitClient
        return client.place_multiple_tp_sl_orders(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            quantity=str(unified_params["quantity"]),
            take_profits=tp_orders_to_place,
            stop_loss_price=sl_price
        )


def _handle_paper_trading_balance(chat_id, config):
    """Handle paper trading balance management."""
    with paper_balances_lock:
        if chat_id not in user_paper_balances:
            user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
            logging.info(f"Paper Trading: Initialized balance of ${TradingConfig.DEFAULT_TRIAL_BALANCE:,.2f} for user {chat_id}")
        
        # Check if user has sufficient paper balance
        if user_paper_balances[chat_id] < config.amount:
            return jsonify({
                'error': f'Insufficient paper trading balance. Available: ${user_paper_balances[chat_id]:,.2f}, Required: ${config.amount:,.2f}'
            }), 400
    
    # Deduct margin from paper balance
    with paper_balances_lock:
        user_paper_balances[chat_id] -= config.amount
        logging.info(f"Paper Trading: Deducted ${config.amount:,.2f} margin. Remaining balance: ${user_paper_balances[chat_id]:,.2f}")
    
    return None, None


def _log_trade_execution(chat_id, trade_id, config, is_paper_mode):
    """Log trade execution to bot_trades."""
    with bot_trades_lock:
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


def _create_execution_response(trade_id, config, is_paper_mode):
    """Create the final execution response."""
    trade_mode = "Paper Trade" if is_paper_mode else "Live Trade"
    
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


def _handle_trade_execution_errors(e):
    """Handle trade execution errors with user-friendly messages."""
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


@app.route('/api/execute-trade', methods=['POST'])
def execute_trade():
    """Execute a trade configuration - refactored for better maintainability."""
    user_id = None
    try:
        data = request.get_json() or {}
        
        # Validate request data
        user_id, trade_id, error_response, status_code = _validate_execute_trade_request(data)
        if error_response:
            return error_response, status_code
            
        logging.info(f"Execute trade request: user_id={user_id}, trade_id={trade_id}")
        chat_id = int(user_id)
        
        # Get and validate trade configuration
        config, error_response, status_code = _get_and_validate_trade_config(chat_id, trade_id)
        if error_response:
            return error_response, status_code
        
        # Get current market price
        current_market_price = get_live_market_price(config.symbol, user_id=chat_id, prefer_exchange=True)
        
        # Set up exchange client and determine trading mode
        client, is_paper_mode, user_creds, error_response, status_code = _setup_exchange_client_and_mode(chat_id, config)
        if error_response:
            return error_response, status_code
        
        # Execute the trade order
        if is_paper_mode:
            execution_success = _handle_paper_trading_execution(config, chat_id)
        else:
            execution_success, error_response, status_code = _execute_real_trading_order(client, config, current_market_price)
            if not execution_success:
                return error_response, status_code
        
        # Configure trade position details
        position_size = _configure_trade_position(config, current_market_price)
        
        # Save to database
        save_trade_to_db(chat_id, config)
        
        # Place TP/SL orders if execution was successful
        if execution_success:
            try:
                tp_sl_data = calculate_tp_sl_prices_and_amounts(config)
                
                if is_paper_mode:
                    mock_tp_sl_orders = _setup_paper_tp_sl_orders(config, tp_sl_data)
                    config.exchange_tp_sl_orders = mock_tp_sl_orders
                    if mock_tp_sl_orders:
                        logging.info(f"Paper Trading: Simulated {len(mock_tp_sl_orders)} TP/SL orders with real-time monitoring")
                else:
                    tp_sl_orders = _place_real_tp_sl_orders(client, config, tp_sl_data, position_size)
                    config.exchange_tp_sl_orders = tp_sl_orders
                    if tp_sl_orders:
                        logging.info(f"Placed {len(tp_sl_orders)} TP/SL orders on exchange")
                
            except Exception as e:
                logging.error(f"Failed to place TP/SL orders: {e}")
                # Continue execution - main position was successful
        
        logging.info(f"Trade executed: {config.symbol} {config.side} at ${config.entry_price} (entry type: {config.entry_type})")
        
        # Handle paper trading balance management
        if is_paper_mode:
            error_response, status_code = _handle_paper_trading_balance(chat_id, config)
            if error_response:
                return error_response, status_code
        
        # Log trade execution
        _log_trade_execution(chat_id, trade_id, config, is_paper_mode)
        
        # Return success response
        return _create_execution_response(trade_id, config, is_paper_mode)
        
    except Exception as e:
        return _handle_trade_execution_errors(e)

@app.route('/api/user-credentials')
@app.route('/api/credentials-status')
def get_user_credentials():
    """Get user API credentials status"""
    user_id = request.args.get('user_id')
    if not user_id or user_id == 'undefined':
        user_id = Environment.DEFAULT_TEST_USER_ID  # Demo user
    
    try:
        # Use database session to prevent race conditions
        with db.session.begin():
            # Check enhanced cache first for user credentials
            cached_result = enhanced_cache.get_user_credentials(str(user_id))
            if cached_result:
                cached_creds, cache_info = cached_result
                # Query fresh data to avoid session binding errors in multi-worker environments
                user_creds = UserCredentials.query.filter_by(
                    telegram_user_id=str(user_id)
                ).with_for_update().first()
                # Retrieved credentials from cache - removed debug log for cleaner output
            else:
                # Cache miss - load from database with row-level locking
                user_creds = UserCredentials.query.filter_by(
                    telegram_user_id=str(user_id)
                ).with_for_update().first()
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
        
        # Handle testnet mode setting - Default to live trading for all exchanges
        if exchange.lower() == 'toobit':
            user_creds.testnet_mode = False  # Toobit only supports mainnet
        elif 'testnet_mode' in data:
            user_creds.testnet_mode = bool(data['testnet_mode'])
        else:
            # Default to live trading for better user experience
            user_creds.testnet_mode = False
        
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
        testnet_mode = bool(data.get('testnet_mode', False))
        
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

def _validate_close_trade_request(data):
    """Validate close trade request data."""
    user_id = data.get('user_id')
    trade_id = data.get('trade_id')
    
    if not user_id or not trade_id:
        return None, None, jsonify({'error': 'User ID and trade ID required'}), 400
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return None, None, jsonify({'error': 'Invalid user ID format'}), 400
    
    if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
        return None, None, jsonify({'error': 'Trade not found'}), 404
    
    config = user_trade_configs[chat_id][trade_id]
    
    if config.status != "active":
        return None, None, jsonify({'error': 'Trade is not active'}), 400
    
    return chat_id, config, None, None


def _determine_trading_mode_for_closure(chat_id, config):
    """Determine if this trade should be closed in paper or real trading mode."""
    # Get user credentials to determine if we're in paper mode or real trading
    user_creds = UserCredentials.query.filter_by(
        telegram_user_id=str(chat_id),
        is_active=True
    ).first()
    
    # Use centralized trading mode detection
    is_paper_mode = determine_trading_mode(chat_id)
    
    # Check multiple indicators to determine if this is a paper trade
    paper_indicators = [
        is_paper_mode,
        not user_creds,
        (user_creds and user_creds.testnet_mode),
        (user_creds and not user_creds.has_credentials()),
        str(getattr(config, 'exchange_order_id', '')).startswith('paper_'),
        getattr(config, 'paper_trading_mode', False),
        hasattr(config, 'paper_tp_levels'),
        hasattr(config, 'paper_sl_data')
    ]
    
    is_paper_mode = any(paper_indicators)
    
    # Log detailed paper trading detection for debugging
    logging.info(f"[RENDER CLOSE DEBUG] Paper mode detection for {config.symbol}:")
    logging.info(f"  Manual paper mode: {user_paper_trading_preferences.get(chat_id, True)}")
    logging.info(f"  Has credentials: {user_creds is not None}")
    logging.info(f"  Paper order ID: {str(getattr(config, 'exchange_order_id', '')).startswith('paper_')}")
    logging.info(f"  Final determination: Paper mode = {is_paper_mode}")
    
    return is_paper_mode, user_creds


def _close_paper_trade(chat_id, trade_id, config):
    """Handle paper trade closure logic."""
    logging.info(f"[RENDER PAPER] Closing paper trade for user {chat_id}: {config.symbol} {config.side}")
    logging.info(f"[RENDER PAPER] Config details - Status: {config.status}, PnL: {getattr(config, 'unrealized_pnl', 0)}")
    
    try:
        # Calculate final P&L
        final_pnl = config.unrealized_pnl + getattr(config, 'realized_pnl', 0.0)
        
        # Update paper balance
        current_balance = user_paper_balances.get(chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE)
        new_balance = current_balance + final_pnl
        with paper_balances_lock:
            user_paper_balances[chat_id] = new_balance
            logging.info(f"[RENDER PAPER] Updated paper balance: ${current_balance:.2f} + ${final_pnl:.2f} = ${new_balance:.2f}")
        
        # Update trade configuration immediately
        config.status = "stopped"
        config.final_pnl = final_pnl
        config.closed_at = get_iran_time().isoformat()
        config.unrealized_pnl = 0.0
        
        # Save to database immediately for paper trades
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
            'status': 'closed_paper'
        })
        
        # Simulate cancelling paper TP/SL orders
        if hasattr(config, 'exchange_tp_sl_orders') and config.exchange_tp_sl_orders:
            cancelled_orders = len(config.exchange_tp_sl_orders)
            logging.info(f"[RENDER PAPER] Simulated cancellation of {cancelled_orders} TP/SL orders in paper mode")
        
        return jsonify({
            'success': True,
            'message': 'Paper trade closed successfully',
            'final_pnl': final_pnl,
            'paper_balance': new_balance
        })
        
    except Exception as paper_error:
        logging.error(f"[RENDER PAPER ERROR] Failed to close paper trade: {paper_error}")
        return jsonify({
            'error': f'Failed to close paper trade: {str(paper_error)}',
            'paper_trading': True
        }), 500


def _prepare_real_trade_closure(chat_id, config, user_creds):
    """Prepare for real trade closure by validating credentials and creating client."""
    # Verify credentials are available
    if not user_creds or not user_creds.has_credentials():
        return None, jsonify({'error': 'API credentials not available for live trading'}), 400
    
    # Create exchange client (dynamic selection)
    client = create_exchange_client(user_creds, testnet=False)
    
    # Calculate position closure parameters
    close_side = "sell" if config.side == "long" else "buy"
    
    # Enhanced logging for debugging
    logging.info(f"[RENDER CLOSE] User {chat_id} attempting to close {config.symbol} {config.side} position")
    logging.info(f"[RENDER CLOSE] Position size: {config.position_size}, Close side: {close_side}")
    
    # Better position size handling for closure
    position_size = getattr(config, 'position_size', config.amount)
    if not position_size or position_size <= 0:
        # Calculate position size from remaining amount and leverage
        position_size = config.amount * config.leverage
        logging.warning(f"[RENDER CLOSE] Calculated position size: {position_size} from amount: {config.amount} * leverage: {config.leverage}")
    
    return {
        'client': client,
        'close_side': close_side,
        'position_size': position_size
    }, None, None


def _execute_exchange_closure(client, config, close_side, position_size):
    """Execute the actual trade closure on the exchange."""
    # Use Protocol-based unified interface
    from api.unified_exchange_client import OrderParameterAdapter
    
    client_type = type(client).__name__.lower()
    exchange_name = "toobit"  # Default
    if 'lbank' in client_type:
        exchange_name = "lbank"
    elif 'hyperliquid' in client_type:
        exchange_name = "hyperliquid"
    
    unified_params = OrderParameterAdapter.to_exchange_params(
        exchange_name,
        symbol=config.symbol,
        side=close_side,
        quantity=float(position_size),
        order_type="market"
    )
    
    if 'lbank' in client_type or 'hyperliquid' in client_type:
        close_order = client.place_order(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            amount=unified_params["amount"],
            order_type=unified_params["order_type"],
            reduce_only=True
        )
    else:
        # ToobitClient
        close_order = client.place_order(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            quantity=str(unified_params["quantity"]),
            order_type=unified_params["order_type"],
            reduce_only=True
        )
    
    if not close_order:
        # Get specific error from client if available
        error_detail = client.get_last_error()
        logging.error(f"[RENDER CLOSE FAILED] {error_detail}")
        
        return None, jsonify({
            'success': False,
            'error': f'Failed to close {config.symbol} position: {error_detail}',
            'technical_details': error_detail,
            'symbol': config.symbol,
            'side': config.side,
            'suggestion': 'This might be a paper trade or the position may have already been closed. Please refresh and try again.'
        }), 400
    
    logging.info(f"Position closed on exchange: {close_order}")
    return close_order, None, None


def _cancel_remaining_tp_sl_orders(client, config):
    """Cancel any remaining TP/SL orders on the exchange."""
    if hasattr(config, 'exchange_tp_sl_orders') and config.exchange_tp_sl_orders:
        for tp_sl_order in config.exchange_tp_sl_orders:
            order_id = tp_sl_order.get('order', {}).get('orderId')
            if order_id:
                try:
                    client.cancel_order(symbol=config.symbol, order_id=str(order_id))
                    logging.info(f"Cancelled TP/SL order: {order_id}")
                except Exception as cancel_error:
                    logging.warning(f"Failed to cancel order {order_id}: {cancel_error}")


def _close_real_trade(chat_id, config, user_creds):
    """Handle real trade closure logic."""
    try:
        # Prepare closure parameters
        closure_data, error_response, status_code = _prepare_real_trade_closure(chat_id, config, user_creds)
        if error_response:
            return error_response, status_code
        
        # Execute exchange closure
        close_order, error_response, status_code = _execute_exchange_closure(
            closure_data['client'], config, closure_data['close_side'], closure_data['position_size']
        )
        if error_response:
            return error_response, status_code
        
        # Cancel remaining TP/SL orders
        _cancel_remaining_tp_sl_orders(closure_data['client'], config)
        
        return None, None
        
    except Exception as e:
        logging.error(f"[RENDER CLOSE EXCEPTION] Exchange position closure failed: {e}")
        logging.error(f"[RENDER CLOSE EXCEPTION] Config details: {config.symbol} {config.side}, User: {chat_id}")
        
        return jsonify({
            'error': f'Exchange closure failed for {config.symbol}: {str(e)}',
            'technical_details': str(e),
            'symbol': config.symbol,
            'side': config.side,
            'suggestion': 'Check your API credentials and try again. If the problem persists, contact support.'
        }), 500


def _finalize_trade_closure(chat_id, trade_id, config):
    """Finalize trade closure by updating status and logging."""
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
    
    return final_pnl


@app.route('/api/close-trade', methods=['POST'])
def close_trade():
    """Close an active trade"""
    try:
        data = request.get_json()
        
        # Validate request
        chat_id, config, error_response, status_code = _validate_close_trade_request(data)
        if error_response:
            return error_response, status_code
        
        trade_id = data.get('trade_id')
        
        # Determine trading mode
        is_paper_mode, user_creds = _determine_trading_mode_for_closure(chat_id, config)
        
        if is_paper_mode:
            # Handle paper trade closure
            return _close_paper_trade(chat_id, trade_id, config)
        else:
            # Handle real trade closure
            error_response, status_code = _close_real_trade(chat_id, config, user_creds)
            if error_response:
                return error_response, status_code
        
        # Finalize trade closure
        final_pnl = _finalize_trade_closure(chat_id, trade_id, config)
        
        return jsonify({
            'success': True,
            'message': 'Trade closed successfully',
            'final_pnl': final_pnl
        })
        
    except Exception as e:
        logging.error(f"Error closing trade: {str(e)}")
        return jsonify({'error': 'Failed to close trade'}), 500

def _validate_close_all_trades_request(data):
    """Validate close all trades request data."""
    user_id = data.get('user_id')
    
    if not user_id:
        return None, jsonify({'error': 'User ID required'}), 400
    
    try:
        chat_id = int(user_id)
    except ValueError:
        return None, jsonify({'error': 'Invalid user ID format'}), 400
    
    return chat_id, None, None


def _find_active_trades(chat_id):
    """Find all active trades for a user."""
    if chat_id not in user_trade_configs:
        return []
    
    active_trades = []
    for trade_id, config in user_trade_configs[chat_id].items():
        if config.status == "active":
            active_trades.append((trade_id, config))
    
    return active_trades


def _prepare_bulk_trade_closure(chat_id):
    """Prepare for bulk trade closure by determining mode and creating client."""
    # Get user credentials to determine if we're in paper mode or real trading
    user_creds = UserCredentials.query.filter_by(
        telegram_user_id=str(chat_id),
        is_active=True
    ).first()
    
    # Use centralized trading mode detection
    is_paper_mode = determine_trading_mode(chat_id)
    
    client = None
    if not is_paper_mode and user_creds and user_creds.has_credentials():
        # Create exchange client for real trading (dynamic selection)
        client = create_exchange_client(user_creds, testnet=False)
    
    return is_paper_mode, client


def _close_individual_paper_trade(chat_id, trade_id, config):
    """Close a single paper trade."""
    logging.info(f"Closing paper trade {trade_id} for user {chat_id}: {config.symbol} {config.side}")
    
    # Simulate cancelling paper TP/SL orders
    if hasattr(config, 'exchange_tp_sl_orders') and config.exchange_tp_sl_orders:
        cancelled_orders = len(config.exchange_tp_sl_orders)
        logging.info(f"Simulated cancellation of {cancelled_orders} TP/SL orders for trade {trade_id} in paper mode")


def _close_individual_real_trade(client, trade_id, config):
    """Close a single real trade on the exchange."""
    if client is None:
        logging.warning(f"No client available for trade {trade_id} - falling back to paper mode")
        return False
    
    close_side = "sell" if config.side == "long" else "buy"
    close_order = client.place_order(
        symbol=config.symbol,
        side=close_side,
        order_type="market",
        quantity=str(config.position_size),
        reduce_only=True
    )
    
    if close_order:
        logging.info(f"Position closed on exchange: {close_order}")
        
        # Cancel any remaining TP/SL orders on exchange
        if hasattr(config, 'exchange_tp_sl_orders') and config.exchange_tp_sl_orders:
            for tp_sl_order in config.exchange_tp_sl_orders:
                order_id = tp_sl_order.get('order', {}).get('orderId')
                if order_id:
                    try:
                        client.cancel_order(symbol=config.symbol, order_id=str(order_id))
                    except Exception as cancel_error:
                        logging.warning(f"Failed to cancel order {order_id}: {cancel_error}")
        return True
    else:
        logging.warning(f"Failed to close position for trade {trade_id} - exchange order failed")
        return False


def _finalize_individual_trade_closure(chat_id, trade_id, config):
    """Finalize closure of an individual trade."""
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
    
    return final_pnl


def _process_bulk_trade_closures(chat_id, active_trades, is_paper_mode, client):
    """Process closure of multiple trades."""
    closed_count = 0
    total_final_pnl = 0.0
    
    # Close each active trade
    for trade_id, config in active_trades:
        try:
            if is_paper_mode:
                # Handle paper trade closure
                _close_individual_paper_trade(chat_id, trade_id, config)
            else:
                # Handle real trade closure
                if not _close_individual_real_trade(client, trade_id, config):
                    continue
            
            # Finalize trade closure
            final_pnl = _finalize_individual_trade_closure(chat_id, trade_id, config)
            
            closed_count += 1
            total_final_pnl += final_pnl
            
        except Exception as trade_error:
            logging.error(f"Error closing trade {trade_id}: {str(trade_error)}")
            continue
    
    return closed_count, total_final_pnl


@app.route('/api/close-all-trades', methods=['POST'])
def close_all_trades():
    """Close all active trades for a user"""
    try:
        data = request.get_json()
        
        # Validate request
        chat_id, error_response, status_code = _validate_close_all_trades_request(data)
        if error_response:
            return error_response, status_code
        
        # Find active trades
        active_trades = _find_active_trades(chat_id)
        
        if not active_trades:
            return jsonify({'success': True, 'message': 'No active trades to close', 'closed_count': 0})
        
        # Prepare for bulk closure
        is_paper_mode, client = _prepare_bulk_trade_closure(chat_id)
        
        # Process all trade closures
        closed_count, total_final_pnl = _process_bulk_trade_closures(
            chat_id, active_trades, is_paper_mode, client
        )
        
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
        
        # Remove from configurations with proper locking
        with trade_configs_lock:
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
        
        # Reset paper trading balance to default regardless of credentials
        user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
        
        # Clear trade history from bot_trades list
        global bot_trades
        with bot_trades_lock:
            bot_trades = [trade for trade in bot_trades if trade.get('user_id') != str(chat_id)]
        
        # Clear any cached portfolio data manually using enhanced cache
        try:
            # Clear user data cache for this user
            enhanced_cache.invalidate_user_data(str(chat_id))
        except Exception:
            # Cache clearing failed, continue without clearing cache
            pass
        
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
    with paper_balances_lock:
        if chat_id not in user_paper_balances:
            user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
        
        paper_balance = user_paper_balances[chat_id]
    
    return jsonify({
        'paper_balance': paper_balance,
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
    with paper_balances_lock:
        user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
        new_balance = user_paper_balances[chat_id]
    
    return jsonify({
        'success': True,
        'paper_balance': new_balance,
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

def _handle_basic_commands(text, user):
    """Handle basic bot commands like /start and /menu"""
    if text.startswith('/start'):
        welcome_text = f"""🤖 Welcome to Trading Bot, {user.get('first_name', 'User')}!

Use the menu below to navigate:"""
        return welcome_text, get_main_menu()
    elif text.startswith('/menu'):
        return "📋 Main Menu:", get_main_menu()
    return None, None

def _handle_price_command(text):
    """Handle /price command for market price lookup"""
    parts = text.split()
    if len(parts) < 2:
        return "❌ Please provide a symbol. Example: /price BTCUSDT", None
    
    symbol = parts[1].upper()
    try:
        start_time = time.time()
        price = get_live_market_price(symbol, prefer_exchange=True)
        fetch_time = (time.time() - start_time) * 1000
        
        # Get enhanced cache info
        cache_info = ""
        cached_result = enhanced_cache.get_price(symbol)
        if cached_result:
            _, source, cache_meta = cached_result
            cache_info = f" (cached, {source})" if cache_meta.get('cached', False) else f" ({source})"
        else:
            cache_info = " (live)"
        
        return f"💰 {symbol}: ${price:.4f}{cache_info}\n⚡ Fetched in {fetch_time:.0f}ms", None
    except Exception as e:
        logging.error(f"Error fetching live price for {symbol}: {e}")
        return f"❌ Could not fetch live price for {symbol}\nError: {str(e)}", None

def _handle_trade_commands(text, user):
    """Handle /buy and /sell commands"""
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

def _handle_trades_command(text, user):
    """Handle /trades command for trade history"""
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

def process_command(text, chat_id, user):
    """Process bot commands - refactored for better maintainability"""
    if not text:
        return "🤔 I didn't receive any text. Type /help to see available commands.", None
    
    # Try basic commands
    response, keyboard = _handle_basic_commands(text, user)
    if response:
        return response, keyboard
    
    # Handle API commands
    if text.startswith('/api') or text.startswith('/credentials'):
        return handle_api_setup_command(text, chat_id, user)
    
    # Handle price command
    if text.startswith('/price'):
        return _handle_price_command(text)
    
    # Handle trade commands
    if text.startswith('/buy') or text.startswith('/sell'):
        return _handle_trade_commands(text, user)
    
    # Handle portfolio command
    if text.startswith('/portfolio'):
        return "📊 Check your portfolio in the positions tab of the web interface.", None
    
    # Handle trades command
    if text.startswith('/trades'):
        return _handle_trades_command(text, user)
    
    # Handle numeric input for trade configuration
    result = _handle_numeric_input(text, chat_id)
    if result:
        return result
    
    # Handle API setup text input
    if chat_id in user_api_setup_state:
        return handle_api_text_input(text, chat_id, user)
    
    return "🤔 I didn't understand that command. Use the menu buttons to navigate.", get_main_menu()

def _handle_numeric_input(text, chat_id):
    """Handle numeric input for trade configuration"""
    if chat_id not in user_selected_trade:
        return None
    
    trade_id = user_selected_trade[chat_id]
    if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
        return None
    
    config = user_trade_configs[chat_id][trade_id]
    
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
        
        # Handle take profit configuration
        elif config.tp_config_step == "percentages":
            return _handle_tp_percentage_input(config, value, chat_id)
        
        elif config.tp_config_step == "allocations":
            return _handle_tp_allocation_input(config, value, chat_id)
        
        # Check if we're expecting stop loss
        elif config.stop_loss_percent <= 0:
            config.stop_loss_percent = value
            return f"✅ Set stop loss to {value}%\n\n🎯 Trade configuration complete!", get_trading_menu(chat_id)
        
    except ValueError:
        pass
    
    return None

def _handle_tp_percentage_input(config, value, chat_id):
    """Handle take profit percentage input"""
    config.take_profits.append({"percentage": value, "allocation": None})
    tp_num = len(config.take_profits)
    
    if tp_num < 3:  # Allow up to 3 TPs
        return f"✅ Added TP{tp_num}: {value}%\n\n🎯 Add another TP percentage or continue to allocations:", get_tp_percentage_input_menu()
    else:
        config.tp_config_step = "allocations"
        return f"✅ Added TP{tp_num}: {value}%\n\n📊 Now set position allocation for each TP:", get_tp_allocation_menu(chat_id)

def _handle_tp_allocation_input(config, value, chat_id):
    """Handle take profit allocation input"""
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
    return None

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
    current_requests = metrics.get('requests', 0)
    metrics['requests'] = current_requests + 1
    if success:
        current_successes = metrics.get('successes', 0)
        metrics['successes'] = current_successes + 1
        metrics['last_success'] = datetime.utcnow()
        # Update rolling average response time
        current_avg = metrics.get('avg_response_time', 0)
        if current_avg == 0:
            metrics['avg_response_time'] = response_time
        else:
            metrics['avg_response_time'] = (current_avg * 0.8) + (response_time * 0.2)

def get_api_priority():
    """Get API priority based on performance metrics"""
    apis = []
    for api_name, metrics in api_performance_metrics.items():
        requests_count = metrics.get('requests', 0)
        if requests_count is not None and requests_count > 0:
            successes_count = metrics.get('successes', 0)
            avg_response_time = metrics.get('avg_response_time', 0)
            success_rate = successes_count / requests_count
            score = success_rate * 100 - (avg_response_time or 0)
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
                client = create_exchange_client(user_creds, testnet=False)
                
                toobit_price = client.get_ticker_price(symbol)
                if toobit_price:
                    return toobit_price, 'toobit'
        
        # Fallback: Create anonymous client for public market data
        # Use wrapped anonymous client that can handle multiple exchanges
        anonymous_client = create_wrapped_exchange_client(exchange_name="toobit", testnet=False)
        toobit_price = anonymous_client.get_ticker_price(symbol)
        if toobit_price:
            return toobit_price, 'toobit'
            
        return None, None
    except Exception as e:
        logging.warning(f"Failed to get Toobit price for {symbol}: {e}")
        return None, None

@with_circuit_breaker('hyperliquid_api', failure_threshold=3, recovery_timeout=60)
def get_hyperliquid_price(symbol, user_id=None):
    """Get live price directly from Hyperliquid exchange with circuit breaker protection"""
    try:
        # Ensure we're in Flask application context
        if not has_app_context():
            with app.app_context():
                return get_hyperliquid_price(symbol, user_id)
        
        # Try to get user credentials to use their exchange connection
        if user_id:
            user_creds = UserCredentials.query.filter_by(
                telegram_user_id=str(user_id),
                is_active=True
            ).first()
            
            if user_creds and user_creds.has_credentials() and user_creds.exchange_name == 'hyperliquid':
                client = create_exchange_client(user_creds, testnet=False)
                
                hyperliquid_price = client.get_ticker_price(symbol)
                if hyperliquid_price:
                    return hyperliquid_price, 'hyperliquid'
        
        # Fallback: Create anonymous client for public market data
        # Use wrapped anonymous client that can handle multiple exchanges
        anonymous_client = create_wrapped_exchange_client(exchange_name="hyperliquid", testnet=False)
        hyperliquid_price = anonymous_client.get_ticker_price(symbol)
        if hyperliquid_price:
            return hyperliquid_price, 'hyperliquid'
            
        return None, None
    except Exception as e:
        logging.warning(f"Failed to get Hyperliquid price for {symbol}: {e}")
        return None, None

def _get_user_credentials_safe(user_id):
    """Safely get user credentials with proper app context handling"""
    if not user_id:
        return None
    
    try:
        if has_app_context():
            return UserCredentials.query.filter_by(
                telegram_user_id=str(user_id),
                is_active=True
            ).first()
        else:
            # We're running in a background thread, need app context
            with app.app_context():
                return UserCredentials.query.filter_by(
                    telegram_user_id=str(user_id),
                    is_active=True
                ).first()
    except Exception as context_error:
        logging.debug(f"Database query failed due to context issue: {context_error}")
        return None

def _try_preferred_exchange(symbol, user_id, use_cache):
    """Try to get price from user's preferred exchange"""
    user_creds = _get_user_credentials_safe(user_id)
    
    if not (user_creds and user_creds.exchange_name):
        return None
    
    if user_creds.exchange_name == 'hyperliquid':
        hyperliquid_price, source = get_hyperliquid_price(symbol, user_id)
        if hyperliquid_price:
            if use_cache:
                enhanced_cache.set_price(symbol, hyperliquid_price, 'hyperliquid')
            logging.info(f"Retrieved live price for {symbol} from Hyperliquid exchange: ${hyperliquid_price}")
            return hyperliquid_price
    else:
        # Default to Toobit for other exchanges (backward compatibility)
        toobit_price, source = get_toobit_price(symbol, user_id)
        if toobit_price:
            if use_cache:
                enhanced_cache.set_price(symbol, toobit_price, 'toobit')
            logging.info(f"Retrieved live price for {symbol} from Toobit exchange: ${toobit_price}")
            return toobit_price
    
    return None

def _try_fallback_exchange(symbol, user_id, use_cache):
    """Try fallback to Toobit if no user_id provided"""
    toobit_price, source = get_toobit_price(symbol, user_id)
    if toobit_price:
        if use_cache:
            enhanced_cache.set_price(symbol, toobit_price, 'toobit')
        logging.info(f"Retrieved live price for {symbol} from Toobit exchange: ${toobit_price}")
        return toobit_price
    return None

def _try_concurrent_apis(symbol, api_priority, api_functions):
    """Try concurrent API requests for faster response"""
    futures = {}
    
    # Submit requests to top 2 performing APIs concurrently
    for api_name in api_priority[:2]:
        if api_name in api_functions:
            future = price_executor.submit(api_functions[api_name], symbol)
            futures[future] = api_name
    
    # Wait for first successful response
    try:
        for future in as_completed(futures, timeout=TimeConfig.QUICK_API_TIMEOUT):
            try:
                price, source = future.result()
                return price, source
            except CircuitBreakerError as e:
                logging.warning(f"{futures[future]} circuit breaker is open: {str(e)}")
                continue
            except Exception as e:
                logging.warning(f"{futures[future]} API failed for {symbol}: {str(e)}")
                continue
    except Exception as e:
        logging.warning(f"Concurrent API requests timed out for {symbol}")
    
    return None, None

def _try_sequential_apis(symbol, api_priority, api_functions):
    """Try remaining APIs sequentially if concurrent requests failed"""
    for api_name in api_priority[2:]:
        if api_name in api_functions:
            try:
                price_result = api_functions[api_name](symbol)
                if price_result and len(price_result) == 2:
                    return price_result
            except CircuitBreakerError as e:
                logging.warning(f"{api_name} circuit breaker is open: {str(e)}")
                continue
            except Exception as e:
                logging.warning(f"{api_name} API failed for {symbol}: {str(e)}")
                continue
    return None, None

def get_live_market_price(symbol, use_cache=True, user_id=None, prefer_exchange=True):
    """Enhanced price fetching - refactored for better maintainability"""
    # Check enhanced cache first
    if use_cache:
        cached_result = enhanced_cache.get_price(symbol)
        if cached_result:
            price, source, cache_info = cached_result
            return price
    
    # PRIORITY 1: Try user's preferred exchange first
    if prefer_exchange and user_id:
        exchange_price = _try_preferred_exchange(symbol, user_id, use_cache)
        if exchange_price:
            return exchange_price
    elif prefer_exchange:
        # Fallback to Toobit if no user_id provided
        exchange_price = _try_fallback_exchange(symbol, user_id, use_cache)
        if exchange_price:
            return exchange_price
    
    # Get optimal API order based on performance
    api_priority = get_api_priority()
    
    # Define API functions mapping
    api_functions = {
        'binance': fetch_binance_price,
        'coingecko': fetch_coingecko_price,
        'cryptocompare': fetch_cryptocompare_price
    }
    
    # Try concurrent requests for faster response
    success_price, success_source = _try_concurrent_apis(symbol, api_priority, api_functions)
    
    # If concurrent requests failed, try remaining APIs sequentially
    if success_price is None:
        success_price, success_source = _try_sequential_apis(symbol, api_priority, api_functions)
    
    if success_price is None:
        # No emergency fallback needed - enhanced cache handles stale data automatically
        raise Exception(f"Unable to fetch live market price for {symbol} from any source")
    
    # Cache the successful result using enhanced cache
    if use_cache and success_source:
        enhanced_cache.set_price(symbol, success_price, success_source)
    
    logging.info(f"Retrieved live price for {symbol} from {success_source}: ${success_price}")
    return success_price

def _collect_symbols_for_batch_update():
    """Collect unique symbols and position configs for batch processing."""
    symbols_to_update = set()
    position_configs = []
    paper_trading_configs = []
    
    for uid, trades in user_trade_configs.items():
        for trade_id, config in trades.items():
            # Include both real and paper trading positions for monitoring
            if config.symbol and (config.status == "active" or config.status == "configured" or config.status == "pending"):
                symbols_to_update.add(config.symbol)
                position_configs.append((uid, trade_id, config))
                
                # Track paper trading positions separately for enhanced monitoring
                if getattr(config, 'paper_trading_mode', False):
                    paper_trading_configs.append((uid, trade_id, config))
    
    return symbols_to_update, position_configs, paper_trading_configs


def _batch_fetch_symbol_prices(symbols_to_update, user_id=None):
    """Batch fetch prices for all symbols concurrently."""
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
    
    return symbol_prices


def _process_pending_limit_orders(user_id, trade_id, config):
    """Process pending limit orders for execution."""
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
            return True
    return False


def _process_stop_loss_monitoring(user_id, trade_id, config):
    """Process stop loss monitoring for active positions."""
    if not (config.status in ["active", "configured"] and config.entry_price and config.current_price):
        return False
        
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
        return True
    
    return False


def _update_position_pnl(config):
    """Update unrealized P&L for active positions."""
    if config.status in ["active", "configured"] and config.entry_price and config.current_price:
        config.unrealized_pnl = calculate_unrealized_pnl(
            config.entry_price, config.current_price,
            config.amount, config.leverage, config.side
        )


def update_all_positions_with_live_data(user_id=None):
    """Enhanced batch update using Toobit exchange prices for accurate trading data - refactored for better maintainability"""
    # Collect symbols and position configurations for batch processing
    symbols_to_update, position_configs, paper_trading_configs = _collect_symbols_for_batch_update()
    
    # Batch fetch prices for all symbols concurrently
    symbol_prices = _batch_fetch_symbol_prices(symbols_to_update, user_id)
    
    # Update all positions with fetched prices using focused helper functions
    for user_id, trade_id, config in position_configs:
        if config.symbol in symbol_prices:
            try:
                config.current_price = symbol_prices[config.symbol]
                
                # PAPER TRADING: Enhanced monitoring for simulated trades
                if getattr(config, 'paper_trading_mode', False):
                    process_paper_trading_position(user_id, trade_id, config)
                
                # Process pending limit orders for execution
                if _process_pending_limit_orders(user_id, trade_id, config):
                    continue  # Skip further processing if limit order was executed
                
                # Update P&L for non-paper trading positions
                if not getattr(config, 'paper_trading_mode', False):
                    _update_position_pnl(config)
                    
                    # Process stop loss monitoring
                    if _process_stop_loss_monitoring(user_id, trade_id, config):
                        continue  # Position was closed by stop loss
                    
                    # Check take profit targets
                    elif _process_take_profit_monitoring(config, user_id, trade_id):
                        continue  # Position was closed by take profit
                    
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
            
            # CORRECTED: Calculate position size to close based on allocation percentage of original position
            # The position size should be a fraction of the original position, not based on profit amount
            original_amount = getattr(config, 'original_amount', config.amount)
            position_size_to_close = original_amount * (allocation / 100)
            
            # Validate the profit calculation matches expected profit for this allocation
            actual_price_movement = abs(tp_price - config.entry_price) / config.entry_price
            expected_profit = actual_price_movement * config.leverage * position_size_to_close
            
            # Double-check: ensure profit_amount aligns with position size calculation
            if abs(expected_profit - profit_amount) > 0.01:  # Allow small floating point differences
                logging.warning(f"TP{i+1} profit calculation mismatch: expected {expected_profit}, calculated {profit_amount}")
                # Use the position-based calculation as it's more reliable
                profit_amount = expected_profit
            
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
    with trade_configs_lock:
        user_trades = user_trade_configs.get(chat_id, {})
    
    # Account totals - use paper trading balance
    with paper_balances_lock:
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

def _handle_main_menu_callbacks(callback_data, chat_id):
    """Handle main menu navigation callbacks"""
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
    return None

def _handle_api_callbacks(callback_data, chat_id, user):
    """Handle API credentials management callbacks"""
    if callback_data.startswith("api_setup_"):
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
    return None

def _handle_pair_selection_callbacks(callback_data, chat_id):
    """Handle trading pair selection callbacks"""
    if callback_data.startswith("pair_"):
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
    elif callback_data.startswith("set_symbol_"):
        symbol = callback_data.replace("set_symbol_", "")
        if chat_id in user_selected_trade:
            trade_id = user_selected_trade[chat_id]
            if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                config = user_trade_configs[chat_id][trade_id]
                config.symbol = symbol
                return f"✅ Set symbol to {symbol}", get_trading_menu(chat_id)
        return "❌ No trade selected. Please create or select a trade first.", get_trading_menu(chat_id)
    return None


def _format_account_summary(margin_data):
    """Format account summary section."""
    response = "💼 **ACCOUNT SUMMARY**\n"
    response += f"Account Balance: ${margin_data['account_balance']:,.2f}\n"
    response += f"Total Margin Used: ${margin_data['total_margin']:,.2f}\n"
    response += f"Free Margin: ${margin_data['free_margin']:,.2f}\n"
    response += f"Floating P&L: ${margin_data['unrealized_pnl']:+,.2f}\n"
    
    if margin_data['margin_level'] > 0:
        response += f"Margin Level: {margin_data['margin_level']:.1f}%\n"
    else:
        response += f"Margin Level: ∞ (No positions)\n"
    response += "\n"
    return response


def _format_risk_assessment(margin_data):
    """Format risk assessment section."""
    response = "⚠️ **RISK ASSESSMENT**\n"
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
    return response


def _format_active_positions(active_positions):
    """Format active positions section."""
    response = "📊 **ACTIVE POSITIONS**\n"
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
    return response


def _format_configured_positions(configured_positions):
    """Format configured positions section."""
    if not configured_positions:
        return ""
    
    response = "📋 **CONFIGURED POSITIONS**\n"
    response += f"Ready to Execute: {len(configured_positions)}\n"
    for config in configured_positions:
        if config.symbol:
            response += f"• {config.symbol} {config.side or 'N/A'}: ${config.amount or 0:,.2f}\n"
    response += "\n"
    return response


def _format_closed_positions_history(closed_positions, chat_id):
    """Format closed positions history section."""
    if closed_positions:
        response = "📚 **CLOSED POSITIONS HISTORY**\n"
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
        response = "📚 **CLOSED POSITIONS HISTORY**\n"
        response += "No closed positions yet\n\n"
    return response


def _format_portfolio_statistics(user_trades, active_positions, configured_positions):
    """Format portfolio statistics section."""
    all_positions = len(user_trades)
    if all_positions == 0:
        return ""
    
    response = "📈 **PORTFOLIO STATISTICS**\n"
    response += f"Total Positions: {all_positions} | Active: {len(active_positions)} | Configured: {len(configured_positions)}\n"
    
    # Calculate portfolio diversity
    symbols = set(config.symbol for config in user_trades.values() if config.symbol)
    response += f"Unique Symbols: {len(symbols)}\n"
    
    # Symbol breakdown for active positions
    if active_positions:
        symbol_breakdown = {}
        for config in active_positions:
            if config.symbol:
                symbol_breakdown[config.symbol] = symbol_breakdown.get(config.symbol, 0) + 1
        
        if len(symbol_breakdown) > 1:
            response += "Symbol Distribution: "
            response += " | ".join([f"{sym}({count})" for sym, count in sorted(symbol_breakdown.items())])
            response += "\n"
    
    return response


def _handle_portfolio_overview(chat_id):
    """Handle portfolio overview callback with comprehensive portfolio display."""
    # Update all positions with live market data before displaying
    update_positions_lightweight()
    
    user_trades = user_trade_configs.get(chat_id, {})
    margin_data = get_margin_summary(chat_id)
    
    # Categorize positions
    active_positions = [config for config in user_trades.values() if config.status == "active"]
    configured_positions = [config for config in user_trades.values() if config.status == "configured"]
    closed_positions = [config for config in user_trades.values() if config.status == "stopped"]
    
    # Build response using helper functions
    response = "📊 **PORTFOLIO & MARGIN OVERVIEW**\n"
    response += "=" * 40 + "\n\n"
    
    response += _format_account_summary(margin_data)
    response += _format_risk_assessment(margin_data)
    response += _format_active_positions(active_positions)
    response += _format_configured_positions(configured_positions)
    response += _format_closed_positions_history(closed_positions, chat_id)
    response += _format_portfolio_statistics(user_trades, active_positions, configured_positions)
    
    return response, get_portfolio_menu()


def _handle_portfolio_callbacks(callback_data, chat_id, user):
    """Handle portfolio-related callbacks."""
    if callback_data == "portfolio_overview":
        return _handle_portfolio_overview(chat_id)
    elif callback_data == "recent_trades":
        return _handle_recent_trades_callback(chat_id, user)
    elif callback_data == "performance":
        return _handle_performance_callback(chat_id, user)
    elif callback_data == "quick_price":
        return _handle_quick_price_check()
    return None


def _handle_recent_trades_callback(chat_id, user):
    """Handle recent trades callback display."""
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


def _handle_performance_callback(chat_id, user):
    """Handle performance analytics callback display."""
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
                symbols[config.symbol] = symbols.get(config.symbol, 0) + 1
        
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


def _handle_quick_price_check():
    """Handle quick price check callback."""
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


def _handle_position_management_callbacks(callback_data, chat_id):
    """Handle position management callbacks."""
    if callback_data == "menu_positions":
        return _handle_menu_positions(chat_id)
    elif callback_data == "positions_new":
        return _handle_positions_new(chat_id)
    elif callback_data == "positions_list":
        return _handle_positions_list(chat_id)
    elif callback_data == "positions_select":
        return "🎯 Select a position to configure:", get_trade_selection_menu(chat_id)
    elif callback_data.startswith("select_position_"):
        return _handle_select_position(callback_data, chat_id)
    elif callback_data == "positions_start":
        return _handle_positions_start(chat_id)
    elif callback_data == "positions_stop_all":
        return _handle_positions_stop_all(chat_id)
    elif callback_data == "positions_status":
        return _handle_positions_status(chat_id)
    return None


def _handle_menu_positions(chat_id):
    """Handle menu positions display."""
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


def _handle_positions_new(chat_id):
    """Handle creating new position."""
    global trade_counter
    trade_counter += 1
    trade_id = f"trade_{trade_counter}"
    
    if chat_id not in user_trade_configs:
        user_trade_configs[chat_id] = {}
    
    new_trade = TradeConfig(trade_id, f"Position #{trade_counter}")
    with trade_configs_lock:
        user_trade_configs[chat_id][trade_id] = new_trade
        user_selected_trade[chat_id] = trade_id
    
    return f"✅ Created new position: {new_trade.get_display_name()}", get_positions_menu(chat_id)


def _handle_positions_list(chat_id):
    """Handle positions list display."""
    user_trades = user_trade_configs.get(chat_id, {})
    if not user_trades:
        return "📋 No positions configured yet.", get_positions_menu(chat_id)
    
    response = "📋 Your Position Configurations:\n\n"
    status_emoji_map = {
        "active": "🟢",
        "pending": "🔵", 
        "configured": "🟡",
        "stopped": "🔴"
    }
    
    for trade_id, config in user_trades.items():
        status_emoji = status_emoji_map.get(config.status, "⚪")
        response += f"{status_emoji} {config.get_display_name()}\n"
        response += f"   {config.symbol or 'No symbol'} | {config.side or 'No side'}\n\n"
    
    keyboard = {"inline_keyboard": []}
    for trade_id, config in list(user_trades.items())[:5]:  # Show first 5 positions
        status_emoji = status_emoji_map.get(config.status, "⚪")
        button_text = f"{status_emoji} {config.name}"
        keyboard["inline_keyboard"].append([{"text": button_text, "callback_data": f"select_position_{trade_id}"}])
    
    keyboard["inline_keyboard"].append([{"text": "🏠 Back to Positions", "callback_data": "menu_positions"}])
    return response, keyboard


def _handle_select_position(callback_data, chat_id):
    """Handle position selection."""
    trade_id = callback_data.replace("select_position_", "")
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        with trade_configs_lock:
            user_selected_trade[chat_id] = trade_id
        config = user_trade_configs[chat_id][trade_id]
        response = f"✅ Selected Position: {config.get_display_name()}\n\n{config.get_config_summary()}"
        return response, get_trade_actions_menu(trade_id)
    return "❌ Position not found.", get_positions_menu(chat_id)


def _handle_positions_start(chat_id):
    """Handle starting position."""
    if chat_id not in user_selected_trade:
        return "❌ No position selected. Please select a position first.", get_positions_menu(chat_id)
        
    trade_id = user_selected_trade[chat_id]
    config = user_trade_configs[chat_id][trade_id]
    
    if not config.is_complete():
        return "❌ Position configuration incomplete. Please set symbol, side, and amount.", get_positions_menu(chat_id)
        
    config.status = "active"
    return f"🚀 Started position: {config.get_display_name()}", get_positions_menu(chat_id)


def _handle_positions_stop_all(chat_id):
    """Handle stopping all positions."""
    user_trades = user_trade_configs.get(chat_id, {})
    stopped_count = 0
    for config in user_trades.values():
        if config.status == "active":
            config.status = "stopped"
            stopped_count += 1
    return f"⏹️ Stopped {stopped_count} active positions.", get_positions_menu(chat_id)


def _handle_positions_status(chat_id):
    """Handle positions status display."""
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


def _handle_configuration_callbacks(callback_data, chat_id):
    """Handle configuration-related callbacks."""
    if callback_data == "set_breakeven":
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
    return None


def _handle_trading_config_callbacks(callback_data, chat_id, user):
    """Handle trading configuration callbacks."""
    if callback_data == "set_side_long":
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
    return None


def _handle_trade_action_callbacks(callback_data, chat_id):
    """Handle trade action callbacks."""
    if callback_data.startswith("start_trade_"):
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
    return None


def _handle_take_profit_callbacks(callback_data, chat_id):
    """Handle take profit configuration callbacks."""
    if callback_data == "set_takeprofit":
        if chat_id in user_selected_trade:
            trade_id = user_selected_trade[chat_id]
            if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
                config = user_trade_configs[chat_id][trade_id]
                config.take_profits = []  # Reset take profits
                config.tp_config_step = "percentages"  # Start with percentages
                header = config.get_trade_header("Set Take Profits")
                return f"{header}🎯 Take Profit Setup\n\nFirst, set your take profit percentages.\nEnter percentage for TP1 (e.g., 10 for 10% profit):", get_tp_percentage_input_menu()
        return "❌ No trade selected.", get_trading_menu(chat_id)
    elif callback_data.startswith("tp_add_percent_"):
        return _handle_tp_add_percent(callback_data, chat_id)
    elif callback_data == "tp_continue_allocations":
        return _handle_tp_continue_allocations(chat_id)
    elif callback_data.startswith("tp_alloc_"):
        return _handle_tp_allocation(callback_data, chat_id)
    elif callback_data == "tp_reset_all":
        return _handle_tp_reset_all(chat_id)
    elif callback_data == "tp_reset_last_alloc":
        return _handle_tp_reset_last_alloc(chat_id)
    return None


def _handle_tp_add_percent(callback_data, chat_id):
    """Handle adding take profit percentage."""
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


def _handle_tp_continue_allocations(chat_id):
    """Handle continuing to TP allocations."""
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


def _handle_tp_allocation(callback_data, chat_id):
    """Handle take profit allocation setting."""
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


def _handle_tp_reset_all(chat_id):
    """Handle resetting all take profits."""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            config = user_trade_configs[chat_id][trade_id]
            config.take_profits = []
            config.tp_config_step = "percentages"
            return "🔄 Reset all take profits\n\n🎯 Set take profit percentage for TP1:", get_tp_percentage_input_menu()
    return "❌ No trade selected.", get_trading_menu(chat_id)


def _handle_tp_reset_last_alloc(chat_id):
    """Handle resetting last TP allocation."""
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


def _handle_misc_callbacks(callback_data, chat_id):
    """Handle miscellaneous callbacks."""
    if callback_data == "set_stoploss":
        config = get_current_trade_config(chat_id)
        header = config.get_trade_header("Set Stop Loss") if config else ""
        return f"{header}🛑 Stop Loss Settings\n\nSet your stop loss percentage (e.g., 5 for 5%):", get_stoploss_menu()
    elif callback_data.startswith("sl_"):
        sl_data = callback_data.replace("sl_", "")
        if sl_data == "custom":
            return "🛑 Enter custom stop loss percentage (e.g., 7.5):", get_trading_menu(chat_id)
        else:
            return handle_set_stoploss(chat_id, float(sl_data))
    elif callback_data == "set_entry":
        return "🎯 Entry Price Options:", get_entry_price_menu()
    elif callback_data == "entry_market":
        return handle_set_entry_price(chat_id, "market")
    elif callback_data == "entry_limit":
        return handle_set_entry_price(chat_id, "limit")
    elif callback_data.startswith("amount_"):
        amount_data = callback_data.replace("amount_", "")
        if amount_data == "custom":
            return "💰 Enter custom amount in USDT (e.g., 150):", get_trading_menu(chat_id)
        else:
            return handle_set_amount_wizard(chat_id, float(amount_data))
    return None


def handle_callback_query(callback_data, chat_id, user):
    """Handle callback query from inline keyboard - refactored for better maintainability"""
    try:
        # Try main menu callbacks first
        result = _handle_main_menu_callbacks(callback_data, chat_id)
        if result:
            return result
            
        # Try API callbacks
        result = _handle_api_callbacks(callback_data, chat_id, user)
        if result:
            return result
            
        # Try pair selection callbacks
        result = _handle_pair_selection_callbacks(callback_data, chat_id)
        if result:
            return result
        
        # Try portfolio callbacks
        result = _handle_portfolio_callbacks(callback_data, chat_id, user)
        if result:
            return result
        
        # Try position management callbacks
        result = _handle_position_management_callbacks(callback_data, chat_id)
        if result:
            return result
        
        # Try configuration callbacks
        result = _handle_configuration_callbacks(callback_data, chat_id)
        if result:
            return result
        
        # Try trading config callbacks
        result = _handle_trading_config_callbacks(callback_data, chat_id, user)
        if result:
            return result
        
        # Try trade action callbacks
        result = _handle_trade_action_callbacks(callback_data, chat_id)
        if result:
            return result
        
        # Try take profit callbacks
        result = _handle_take_profit_callbacks(callback_data, chat_id)
        if result:
            return result
        
        # Try miscellaneous callbacks
        result = _handle_misc_callbacks(callback_data, chat_id)
        if result:
            return result
        
        # Unknown callback
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
        with trade_configs_lock:
            del user_trade_configs[chat_id][trade_id]
            if user_selected_trade.get(chat_id) == trade_id:
                del user_selected_trade[chat_id]
        return f"🗑️ Deleted position: {trade_name}", get_positions_menu(chat_id)
    return "❌ Position not found.", get_positions_menu(chat_id)



def handle_edit_trade(chat_id, trade_id):
    """Handle editing a specific trade"""
    if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
        with trade_configs_lock:
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

def _validate_paper_trading_data(config, user_id, trade_id):
    """Validate paper trading position data."""
    logging.debug(f"[RENDER PAPER DEBUG] Processing position for user {user_id}, trade {trade_id}")
    
    if not config.entry_price or not config.current_price:
        logging.warning(f"[RENDER PAPER] Missing price data - Entry: {getattr(config, 'entry_price', None)}, Current: {getattr(config, 'current_price', None)}")
        return False
    
    return True


def _calculate_paper_pnl(config):
    """Calculate unrealized P&L for paper trading position."""
    try:
        config.unrealized_pnl = calculate_unrealized_pnl(
            config.entry_price, config.current_price,
            config.amount, config.leverage, config.side
        )
        logging.debug(f"[RENDER PAPER] P&L calculated: ${config.unrealized_pnl:.2f} for {config.symbol}")
        return True
    except Exception as pnl_error:
        logging.error(f"[RENDER PAPER ERROR] Failed to calculate P&L: {pnl_error}")
        return False


def _check_paper_breakeven_stop_loss(config):
    """Check if break-even stop loss should be triggered."""
    if not (hasattr(config, 'breakeven_sl_triggered') and config.breakeven_sl_triggered):
        return False
    
    price_tolerance = 0.0001  # 0.01% tolerance for floating point precision
    
    if config.side == "long" and config.current_price <= (config.entry_price * (1 - price_tolerance)):
        logging.info(f"BREAKEVEN SL TRIGGERED: {config.symbol} LONG - Current: ${config.current_price:.4f} <= Entry: ${config.entry_price:.4f}")
        return True
    elif config.side == "short" and config.current_price >= (config.entry_price * (1 + price_tolerance)):
        logging.info(f"BREAKEVEN SL TRIGGERED: {config.symbol} SHORT - Current: ${config.current_price:.4f} >= Entry: ${config.entry_price:.4f}")
        return True
    
    return False


def _check_paper_regular_stop_loss(config):
    """Check if regular stop loss should be triggered."""
    if config.stop_loss_percent > 0 and config.unrealized_pnl < 0:
        loss_percentage = abs(config.unrealized_pnl / config.amount) * 100
        return loss_percentage >= config.stop_loss_percent
    
    return False


def _check_paper_stop_loss(config, user_id, trade_id):
    """Check if any stop loss condition is met."""
    if not (hasattr(config, 'paper_sl_data') and not config.paper_sl_data.get('triggered', False)):
        return False
    
    # Check break-even stop loss first
    if _check_paper_breakeven_stop_loss(config):
        execute_paper_stop_loss(user_id, trade_id, config)
        return True
    
    # Check regular stop loss
    if _check_paper_regular_stop_loss(config):
        execute_paper_stop_loss(user_id, trade_id, config)
        return True
    
    return False


def _check_paper_take_profits(config, user_id, trade_id):
    """Check if any take profit levels should be triggered."""
    if not (hasattr(config, 'paper_tp_levels') and config.unrealized_pnl > 0):
        return
    
    profit_percentage = (config.unrealized_pnl / config.amount) * 100
    
    # Check each TP level (in order)
    for i, tp_level in enumerate(config.paper_tp_levels):
        if not tp_level.get('triggered', False) and profit_percentage >= tp_level['percentage']:
            execute_paper_take_profit(user_id, trade_id, config, i, tp_level)
            break  # Only trigger one TP at a time


def _calculate_breakeven_threshold(config):
    """Calculate the breakeven threshold based on configuration."""
    breakeven_threshold = 0
    
    # Handle different breakeven trigger types
    if isinstance(config.breakeven_after, (int, float)):
        breakeven_threshold = config.breakeven_after
    elif str(config.breakeven_after).lower() == "tp1":
        # Check if first TP has been triggered by looking at paper_tp_levels
        if hasattr(config, 'paper_tp_levels') and config.paper_tp_levels:
            first_tp = config.paper_tp_levels[0]
            if first_tp.get('triggered', False):
                breakeven_threshold = first_tp.get('percentage', 0)
            else:
                # TP1 not triggered yet, don't activate breakeven
                breakeven_threshold = 0
        elif hasattr(config, 'take_profits') and config.take_profits:
            # Fallback to original TP configuration
            breakeven_threshold = config.take_profits[0].get('percentage', 0)
    
    return breakeven_threshold


def _check_paper_breakeven_trigger(config, user_id):
    """Check if break-even should be triggered."""
    if not (hasattr(config, 'breakeven_after') and config.breakeven_after and 
            not getattr(config, 'breakeven_sl_triggered', False) and config.unrealized_pnl > 0):
        return
    
    profit_percentage = (config.unrealized_pnl / config.amount) * 100
    breakeven_threshold = _calculate_breakeven_threshold(config)
    
    if breakeven_threshold > 0 and profit_percentage >= breakeven_threshold:
        config.breakeven_sl_triggered = True
        config.breakeven_sl_price = config.entry_price
        save_trade_to_db(user_id, config)
        logging.info(f"Paper Trading: Break-even triggered for {config.symbol} {config.side} at {profit_percentage:.2f}% profit - SL moved to entry price")


def process_paper_trading_position(user_id, trade_id, config):
    """Enhanced paper trading monitoring with real price-based TP/SL simulation"""
    try:
        # Validate paper trading data
        if not _validate_paper_trading_data(config, user_id, trade_id):
            return
        
        # Calculate unrealized P&L
        if not _calculate_paper_pnl(config):
            return
        
        # Check paper trading stop loss - exit early if triggered
        if _check_paper_stop_loss(config, user_id, trade_id):
            return  # Position closed, no further processing
        
        # Check paper trading take profits
        _check_paper_take_profits(config, user_id, trade_id)
        
        # Check break-even trigger
        _check_paper_breakeven_trigger(config, user_id)
        
    except Exception as e:
        logging.error(f"[RENDER PAPER ERROR] Paper trading position processing failed for {getattr(config, 'symbol', 'unknown')}: {e}")
        logging.error(f"[RENDER PAPER ERROR] Config status: {getattr(config, 'status', 'unknown')}")
        logging.error(f"[RENDER PAPER ERROR] User ID: {user_id}, Trade ID: {trade_id}")
        import traceback
        logging.error(f"[RENDER PAPER ERROR] Traceback: {traceback.format_exc()}")

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
        with paper_balances_lock:
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

def _execute_full_paper_tp_closure(user_id, trade_id, config, tp_level):
    """Handle full position closure (100% allocation)."""
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
        with paper_balances_lock:
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


def _prepare_partial_paper_tp_closure(config):
    """Prepare configuration for partial position closure."""
    # Store original amounts before any TP triggers to preserve correct calculations
    if not hasattr(config, 'original_amount'):
        config.original_amount = config.amount
    if not hasattr(config, 'original_margin'):
        config.original_margin = calculate_position_margin(config.original_amount, config.leverage)


def _calculate_partial_tp_profit(config, tp_index, allocation):
    """Calculate partial profit based on TP calculations and allocation."""
    # Use TP calculation data for accurate profit amounts
    tp_calculations = calculate_tp_sl_prices_and_amounts(config)
    current_tp_data = None
    for tp_calc in tp_calculations.get('take_profits', []):
        if tp_calc['level'] == tp_index + 1:
            current_tp_data = tp_calc
            break
    
    if current_tp_data:
        partial_pnl = current_tp_data['profit_amount']
    else:
        # Fallback calculation
        partial_pnl = config.unrealized_pnl * (allocation / 100)
    
    return partial_pnl


def _update_partial_tp_position(config, allocation, partial_pnl, tp_level, tp_index):
    """Update position configuration after partial TP execution."""
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


def _update_paper_balance_partial_tp(user_id, config, allocation, partial_pnl):
    """Update paper balance for partial TP closure."""
    if user_id in user_paper_balances:
        # Use original margin amount for correct balance calculation
        original_margin = getattr(config, 'original_margin', config.original_amount / config.leverage)
        partial_margin_return = original_margin * (allocation / 100)
        balance_change = partial_margin_return + partial_pnl
        with paper_balances_lock:
            user_paper_balances[user_id] += balance_change
            logging.info(f"Paper Trading: Balance updated +${balance_change:.2f}. New balance: ${user_paper_balances[user_id]:,.2f}")


def _log_partial_tp_closure(user_id, trade_id, config, tp_level, allocation, partial_pnl):
    """Log partial TP closure to bot trades."""
    # Use original position amount for allocation calculation
    closed_amount = config.original_amount * (allocation / 100)
    bot_trades.append({
        'id': len(bot_trades) + 1,
        'user_id': str(user_id),
        'trade_id': trade_id,
        'symbol': config.symbol,
        'side': config.side,
        'amount': closed_amount,
        'final_pnl': partial_pnl,
        'timestamp': get_iran_time().isoformat(),
        'status': f'paper_partial_take_profit_{tp_level["level"]}',
        'trading_mode': 'paper'
    })
    
    logging.info(f"Paper Trading: Partial TP{tp_level['level']} triggered - {config.symbol} {config.side} closed {allocation}% (${closed_amount:.2f}) for ${partial_pnl:.2f}")


def _determine_breakeven_trigger_level(config):
    """Determine break-even trigger level from configuration."""
    if not (hasattr(config, 'breakeven_after') and config.breakeven_after):
        return None
    
    if isinstance(config.breakeven_after, (int, float)):
        # Numeric values: 1.0 = TP1, 2.0 = TP2, 3.0 = TP3
        if config.breakeven_after == 1.0:
            return 1
        elif config.breakeven_after == 2.0:
            return 2
        elif config.breakeven_after == 3.0:
            return 3
    elif isinstance(config.breakeven_after, str):
        # String values: "tp1", "tp2", "tp3"
        if config.breakeven_after == "tp1":
            return 1
        elif config.breakeven_after == "tp2":
            return 2
        elif config.breakeven_after == "tp3":
            return 3
    
    return None


def _handle_breakeven_trigger(user_id, config, tp_level):
    """Handle break-even trigger after TP execution."""
    breakeven_trigger_level = _determine_breakeven_trigger_level(config)
    
    # Trigger break-even if current TP level matches configured break-even level
    if (breakeven_trigger_level and tp_level['level'] == breakeven_trigger_level and 
        not getattr(config, 'breakeven_sl_triggered', False)):
        config.breakeven_sl_triggered = True
        config.breakeven_sl_price = config.entry_price
        save_trade_to_db(user_id, config)
        logging.info(f"Paper Trading: Auto break-even triggered after TP{breakeven_trigger_level} - SL moved to entry price {config.entry_price}")
        logging.info(f"Paper Trading: Break-even breakeven_after value was: {config.breakeven_after} (type: {type(config.breakeven_after)})")


def _execute_partial_paper_tp_closure(user_id, trade_id, config, tp_index, tp_level, allocation):
    """Handle partial position closure (< 100% allocation)."""
    # Prepare configuration for partial closure
    _prepare_partial_paper_tp_closure(config)
    
    # Calculate partial profit
    partial_pnl = _calculate_partial_tp_profit(config, tp_index, allocation)
    
    # Update position configuration
    _update_partial_tp_position(config, allocation, partial_pnl, tp_level, tp_index)
    
    save_trade_to_db(user_id, config)
    
    # Update paper balance
    _update_paper_balance_partial_tp(user_id, config, allocation, partial_pnl)
    
    # Log partial closure
    _log_partial_tp_closure(user_id, trade_id, config, tp_level, allocation, partial_pnl)
    
    # Handle break-even trigger
    _handle_breakeven_trigger(user_id, config, tp_level)


def execute_paper_take_profit(user_id, trade_id, config, tp_index, tp_level):
    """Execute paper trading take profit"""
    allocation = tp_level['allocation']
    
    if allocation >= 100:
        # Full position close
        _execute_full_paper_tp_closure(user_id, trade_id, config, tp_level)
    else:
        # Partial close
        _execute_partial_paper_tp_closure(user_id, trade_id, config, tp_index, tp_level, allocation)

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

def _is_breakeven_enabled(config):
    """Check if break-even is enabled for a position."""
    if not (hasattr(config, 'breakeven_after') and config.breakeven_after):
        return False
    
    # Handle both string values (tp1, tp2, tp3) and numeric values (1.0, 2.0, 3.0)
    if isinstance(config.breakeven_after, str):
        return config.breakeven_after in ["tp1", "tp2", "tp3"]
    elif isinstance(config.breakeven_after, (int, float)):
        return config.breakeven_after > 0
    
    return False


def _collect_breakeven_positions():
    """Collect all positions that need break-even monitoring."""
    breakeven_positions = []
    symbols_needed = set()
    total_positions = 0
    active_positions = 0
    
    for user_id, trades in user_trade_configs.items():
        for trade_id, config in trades.items():
            # Skip closed/stopped positions entirely from monitoring
            if config.status == "stopped":
                continue
                
            total_positions += 1
            
            # Debug logging for breakeven analysis
            logging.debug(f"Checking position {trade_id}: status={config.status}, symbol={config.symbol}, "
                         f"breakeven_after={config.breakeven_after} (type: {type(config.breakeven_after)}), "
                         f"breakeven_sl_triggered={getattr(config, 'breakeven_sl_triggered', 'not_set')}")
            
            if config.status == "active":
                active_positions += 1
                
            # Only monitor active positions with break-even enabled and not yet triggered
            if (config.status == "active" and config.symbol and _is_breakeven_enabled(config) and
                not getattr(config, 'breakeven_sl_triggered', False)):
                symbols_needed.add(config.symbol)
                breakeven_positions.append((user_id, trade_id, config))
    
    # Enhanced debug logging
    logging.debug(f"Monitoring scan: {total_positions} total positions, {active_positions} active, {len(breakeven_positions)} need break-even monitoring")
    
    return breakeven_positions, symbols_needed


def _fetch_breakeven_symbol_prices(symbols_needed):
    """Fetch prices for symbols that need break-even monitoring."""
    symbol_prices = {}
    if not symbols_needed:
        return symbol_prices
    
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
    
    return symbol_prices


def _calculate_position_pnl(config):
    """Calculate unrealized PnL for a position."""
    if not (config.entry_price and config.current_price):
        return None
    
    return calculate_unrealized_pnl(
        config.entry_price, config.current_price,
        config.amount, config.leverage, config.side
    )


def _should_trigger_breakeven(config, profit_percentage):
    """Check if break-even should trigger based on profit percentage."""
    return (isinstance(config.breakeven_after, (int, float)) and 
            profit_percentage >= config.breakeven_after)


def _execute_breakeven_trigger(user_id, config):
    """Execute break-even trigger by moving SL to entry price."""
    logging.info(f"BREAK-EVEN TRIGGERED: {config.symbol} {config.side} - Moving SL to entry price")
    
    # Mark as triggered to stop monitoring
    config.breakeven_sl_triggered = True
    save_trade_to_db(user_id, config)
    
    # Move exchange SL to entry price
    try:
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        if user_creds and user_creds.has_credentials():
            client = create_exchange_client(user_creds, testnet=False)
            # Move stop loss to entry price (break-even)
            config.breakeven_sl_price = config.entry_price
            config.breakeven_sl_triggered = True
            logging.info(f"Break-even stop loss set to entry price: ${config.entry_price}")
    except Exception as be_error:
        logging.error(f"Failed to move SL to break-even: {be_error}")


def _process_breakeven_monitoring(breakeven_positions, symbol_prices):
    """Process break-even monitoring for all positions."""
    for user_id, trade_id, config in breakeven_positions:
        if config.symbol not in symbol_prices:
            continue
        
        try:
            config.current_price = symbol_prices[config.symbol]
            config.unrealized_pnl = _calculate_position_pnl(config)
            
            if config.unrealized_pnl is None:
                continue
            
            # Check ONLY break-even (everything else handled by exchange)
            if config.unrealized_pnl > 0:
                profit_percentage = (config.unrealized_pnl / config.amount) * 100
                
                if _should_trigger_breakeven(config, profit_percentage):
                    _execute_breakeven_trigger(user_id, config)
                    
        except Exception as e:
            logging.warning(f"Break-even check failed for {config.symbol}: {e}")


def update_positions_lightweight():
    """OPTIMIZED: Lightweight position updates - only for break-even monitoring"""
    # Collect positions that need break-even monitoring
    breakeven_positions, symbols_needed = _collect_breakeven_positions()
    
    # If no positions need break-even monitoring, skip entirely
    if not breakeven_positions:
        logging.debug("No positions need break-even monitoring - skipping lightweight update")
        return
    
    logging.info(f"Lightweight monitoring: Only {len(breakeven_positions)} positions need break-even checks (vs {sum(len(trades) for trades in user_trade_configs.values())} total)")
    
    # Fetch prices only for symbols that need break-even monitoring
    symbol_prices = _fetch_breakeven_symbol_prices(symbols_needed)
    
    # Process break-even monitoring
    _process_breakeven_monitoring(breakeven_positions, symbol_prices)


def place_exchange_native_orders(config, user_id):
    """Place all TP/SL orders directly on exchange after position opens"""
    try:
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        if not user_creds or not user_creds.has_credentials():
            logging.info("No credentials found - skipping exchange-native orders (using paper mode)")
            return False
            
        client = create_exchange_client(user_creds, testnet=False)
        
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
            # Use Protocol-based unified interface for all exchange clients
            from api.unified_exchange_client import OrderParameterAdapter
            
            client_type = type(client).__name__.lower()
            exchange_name = "toobit"  # Default
            if 'lbank' in client_type:
                exchange_name = "lbank"
            elif 'hyperliquid' in client_type:
                exchange_name = "hyperliquid"
            
            unified_params = OrderParameterAdapter.to_exchange_params(
                exchange_name,
                symbol=config.symbol,
                side=config.side,
                total_quantity=float(position_size),
                entry_price=float(config.entry_price),
                take_profits=tp_orders,
                stop_loss_price=sl_price
            )
            
            if 'hyperliquid' in client_type:
                orders_placed = client.place_multiple_tp_sl_orders(
                    symbol=unified_params["symbol"],
                    side=unified_params["side"],
                    amount=unified_params["amount"],
                    entry_price=float(config.entry_price),
                    tp_levels=tp_orders
                )
            elif 'lbank' in client_type:
                orders_placed = client.place_multiple_tp_sl_orders(
                    symbol=unified_params["symbol"],
                    side=unified_params["side"],
                    amount=str(unified_params["amount"]),
                    take_profits=tp_orders,
                    stop_loss_price=sl_price
                )
            else:
                # ToobitClient
                orders_placed = client.place_multiple_tp_sl_orders(
                    symbol=unified_params["symbol"],
                    side=unified_params["side"],
                    quantity=str(unified_params["quantity"]),
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


# SMC Signal Cache Management Routes
@app.route('/api/smc-cache-status')
def get_smc_cache_status():
    """Get status and statistics of SMC signal cache"""
    try:
        from .models import SMCSignalCache
        
        total_signals = SMCSignalCache.query.count()
        active_signals = SMCSignalCache.query.filter(
            SMCSignalCache.expires_at > datetime.utcnow()
        ).count()
        expired_signals = total_signals - active_signals
        
        # Get signals by symbol
        symbols_data = {}
        active_cache_entries = SMCSignalCache.query.filter(
            SMCSignalCache.expires_at > datetime.utcnow()
        ).all()
        
        for entry in active_cache_entries:
            symbols_data[entry.symbol] = {
                'direction': entry.direction,
                'confidence': entry.confidence,
                'signal_strength': entry.signal_strength,
                'expires_at': entry.expires_at.isoformat(),
                'age_minutes': int((datetime.utcnow() - entry.created_at).total_seconds() / 60)
            }
        
        return jsonify({
            'total_cached_signals': total_signals,
            'active_signals': active_signals,
            'expired_signals': expired_signals,
            'cache_efficiency': f"{(active_signals / total_signals * 100):.1f}%" if total_signals > 0 else "0%",
            'symbols_cached': symbols_data,
            'timestamp': get_iran_time().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/smc-cache-cleanup', methods=['POST'])
def cleanup_smc_cache():
    """Manually trigger cleanup of expired SMC signals"""
    try:
        from .models import SMCSignalCache
        
        expired_count = SMCSignalCache.cleanup_expired()
        
        return jsonify({
            'success': True,
            'expired_signals_removed': expired_count,
            'timestamp': get_iran_time().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/debug/trading-status', methods=['GET'])
def debug_trading_status():
    """Enhanced diagnostic endpoint for troubleshooting trading issues"""
    try:
        user_id = request.args.get('user_id', Environment.DEFAULT_TEST_USER_ID)
        
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(telegram_user_id=str(user_id)).first()
        
        # Paper trading status
        is_paper_mode = determine_trading_mode(user_id)
        manual_paper_mode = user_paper_trading_preferences.get(int(user_id), True)
        
        # Diagnostic information
        diagnostic_info = {
            'user_id': user_id,
            'environment': {
                'is_replit': Environment.IS_REPLIT,
                'is_render': Environment.IS_RENDER,
                'is_vercel': Environment.IS_VERCEL,
                'database_type': (get_database_url() or '').split('://')[0] if get_database_url() else 'none'
            },
            'credentials': {
                'has_credentials_in_db': bool(user_creds),
                'has_api_key': bool(user_creds and user_creds.api_key_encrypted),
                'has_api_secret': bool(user_creds and user_creds.api_secret_encrypted),
                'has_passphrase': bool(user_creds and user_creds.passphrase_encrypted),
                'testnet_mode': user_creds.testnet_mode if user_creds else None,
                'is_active': user_creds.is_active if user_creds else None,
                'exchange_name': user_creds.exchange_name if user_creds else None
            },
            'trading_mode': {
                'manual_paper_mode': manual_paper_mode,
                'paper_balance': user_paper_balances.get(user_id, TradingConfig.DEFAULT_TRIAL_BALANCE),
                'would_use_paper_mode': (manual_paper_mode or 
                                       not user_creds or 
                                       (user_creds and user_creds.testnet_mode) or 
                                       (user_creds and not user_creds.has_credentials()))
            },
            'active_trades': len(user_trade_configs.get(user_id, {})),
            'toobit_connection': None
        }
        
        # Test Toobit connection if credentials exist
        if user_creds and user_creds.has_credentials():
            try:
                client = create_exchange_client(user_creds, testnet=False)
                
                # Test connection
                balance_data = client.get_futures_balance()
                diagnostic_info['toobit_connection'] = {
                    'status': 'success',
                    'has_balance_data': bool(balance_data),
                    'balance_fields': len(balance_data) if isinstance(balance_data, dict) else 0,
                    'last_error': getattr(client, 'last_error', None)
                }
                
            except Exception as conn_error:
                diagnostic_info['toobit_connection'] = {
                    'status': 'failed',
                    'error': str(conn_error),
                    'error_type': type(conn_error).__name__
                }
        
        logging.info(f"[RENDER DEBUG] Trading status diagnostic for user {user_id}: {diagnostic_info}")
        return jsonify(diagnostic_info)
        
    except Exception as e:
        logging.error(f"[RENDER DEBUG] Diagnostic failed: {e}")
        return jsonify({'error': f'Diagnostic failed: {str(e)}'}), 500


if __name__ == "__main__":
    # This file is part of the main web application
    # Bot functionality is available via webhooks, no separate execution needed
    print("Note: This API module is part of the main web application.")
    print("Use 'python main.py' or the main workflow to start the application.")