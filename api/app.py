import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

from flask import Flask, has_app_context, jsonify, render_template, request, session, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import (
    APIConfig,
    DatabaseConfig,
    Environment,
    LoggingConfig,
    SecurityConfig,
    TimeConfig,
    TradingConfig,
    get_cache_ttl,
    get_database_url,
    get_log_level,
)

try:
    # Try relative import first (for module import - Vercel/main.py)
    from ..scripts.exchange_sync import get_sync_service, initialize_sync_service
    from .unified_data_sync_service import enhanced_cache, start_unified_data_sync_service
    from .models import (
        TradeConfiguration,
        UserCredentials,
        UserTradingSession,
        UserWhitelist,
        db,
        format_iran_time,
        get_iran_time,
        utc_to_iran_time,
    )
    from .unified_exchange_client import (
        HyperliquidClient,
        LBankClient,
        ToobitClient,
        create_exchange_client,
        create_wrapped_exchange_client,
    )
    from .vercel_sync import get_vercel_sync_service, initialize_vercel_sync_service
except ImportError:
    # Fall back to absolute import (for direct execution - Replit)
    import sys

    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    sys.path.extend([current_dir, parent_dir])
    from api.models import (
        db,
        UserCredentials,
        UserTradingSession,
        UserWhitelist,
        TradeConfiguration,
        format_iran_time,
        get_iran_time,
        utc_to_iran_time,
    )
    from scripts.exchange_sync import initialize_sync_service, get_sync_service
    from api.vercel_sync import initialize_vercel_sync_service, get_vercel_sync_service
    from api.unified_exchange_client import (
        ToobitClient,
        LBankClient,
        HyperliquidClient,
        create_exchange_client,
        create_wrapped_exchange_client,
    )
    from api.unified_data_sync_service import enhanced_cache, start_unified_data_sync_service

from api.circuit_breaker import (
    CircuitBreakerError,
    circuit_manager,
    with_circuit_breaker,
)
from api.error_handler import (
    create_success_response,
    create_validation_error,
    handle_api_error,
    handle_error,
)


# SECURITY: Telegram WebApp Authentication Functions
def verify_telegram_webapp_data(init_data: str, bot_token: str) -> Optional[Dict[str, Any]]:
    """
    Verify Telegram WebApp initData integrity using bot token hash validation.
    Returns parsed user data if valid, None if invalid.
    
    This implements the official Telegram WebApp authentication protocol:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    try:
        if not init_data or not bot_token:
            return None
            
        # Parse URL-encoded init data
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        
        # Extract hash and remove it from data for verification
        received_hash = parsed_data.pop('hash', None)
        if not received_hash:
            logging.warning("Missing hash in Telegram WebApp data")
            return None
            
        # Create data check string by sorting keys alphabetically
        data_check_arr = []
        for key in sorted(parsed_data.keys()):
            data_check_arr.append(f"{key}={parsed_data[key]}")
        data_check_string = '\n'.join(data_check_arr)
        
        # Create secret key using bot token (FIXED: Correct HMAC parameter order per Telegram spec)
        secret_key = hmac.new(
            key=b"WebAppData",
            msg=bot_token.encode('utf-8'), 
            digestmod=hashlib.sha256
        ).digest()
        
        # Calculate expected hash
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        # Verify hash matches
        if not hmac.compare_digest(expected_hash, received_hash):
            logging.warning("Invalid hash in Telegram WebApp data")
            return None
            
        # Check auth_date to prevent replay attacks (24 hour window)
        auth_date = parsed_data.get('auth_date')
        if auth_date:
            try:
                auth_timestamp = int(auth_date)
                current_timestamp = int(time.time())
                # Allow 24 hours for auth data validity
                if current_timestamp - auth_timestamp > 86400:
                    logging.warning("Expired Telegram WebApp data")
                    return None
            except ValueError:
                logging.warning("Invalid auth_date in Telegram WebApp data")
                return None
                
        # Parse user data if present
        user_data = None
        if 'user' in parsed_data:
            try:
                user_data = json.loads(parsed_data['user'])
            except json.JSONDecodeError:
                logging.warning("Invalid user JSON in Telegram WebApp data")
                return None
                
        return {
            'user': user_data,
            'auth_date': auth_date,
            'query_id': parsed_data.get('query_id'),
            'start_param': parsed_data.get('start_param'),
            'chat_type': parsed_data.get('chat_type'),
            'chat_instance': parsed_data.get('chat_instance')
        }
        
    except Exception as e:
        logging.error(f"Error verifying Telegram WebApp data: {e}")
        return None


def parse_telegram_init_data(init_data: str) -> Optional[Dict[str, Any]]:
    """
    Parse Telegram WebApp initData without signature verification.
    Used for development mode when bot token is not configured.
    
    Returns:
        dict: Parsed user data if valid, None if invalid
    """
    try:
        if not init_data:
            return None
            
        # Parse URL-encoded init data
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        
        # Parse user data if present
        user_data = None
        if 'user' in parsed_data:
            try:
                user_data = json.loads(parsed_data['user'])
            except json.JSONDecodeError:
                logging.warning("Invalid user JSON in Telegram WebApp data")
                return None
                
        # Check auth_date for basic validity (allow 24 hour window)
        auth_date = parsed_data.get('auth_date')
        if auth_date:
            try:
                auth_timestamp = int(auth_date)
                current_timestamp = int(time.time())
                if current_timestamp - auth_timestamp > 86400:
                    logging.warning("Expired Telegram WebApp data (development mode)")
                    # Don't reject in development mode, but log warning
            except ValueError:
                logging.warning("Invalid auth_date in Telegram WebApp data")
                
        return {
            'user': user_data,
            'auth_date': auth_date,
            'query_id': parsed_data.get('query_id'),
            'start_param': parsed_data.get('start_param'),
            'chat_type': parsed_data.get('chat_type'),
            'chat_instance': parsed_data.get('chat_instance'),
            'verified': False  # Mark as unverified for development mode
        }
        
    except Exception as e:
        logging.error(f"Error parsing Telegram WebApp data: {e}")
        return None


def get_authenticated_user_id() -> Optional[str]:
    """
    SECURE: Get authenticated user ID from verified Telegram WebApp data.
    
    This function replaces the vulnerable get_user_id_from_request() and only
    returns user_id if the Telegram WebApp authentication is valid.
    
    In development mode (no bot token), it will parse initData without verification.
    
    Returns:
        str: Verified user_id if authentication is valid
        None: If authentication fails or is missing
    """
    try:
        # Check for Telegram WebApp initData in request
        init_data = None
        
        # Try multiple sources for initData
        if request.method == 'GET':
            init_data = request.args.get('initData')
        elif request.method == 'POST':
            if request.is_json:
                request_data = request.get_json() or {}
                init_data = request_data.get('initData')
            else:
                init_data = request.form.get('initData')
        
        # Also check headers for initData
        if not init_data:
            init_data = request.headers.get('X-Telegram-Init-Data')
            
        # Check URL parameters for tg_init_data (frontend auth reload)
        if not init_data:
            init_data = request.args.get('tg_init_data')
            if init_data:
                # URL decode the initData since it's encoded in the URL
                init_data = urllib.parse.unquote(init_data)
            
        if not init_data:
            # Check if this is a Telegram browser request without initData
            user_agent = request.headers.get('User-Agent', '')
            if 'Telegram' in user_agent:
                logging.warning("Telegram WebApp request detected but no initData found")
                # Only log detailed info in development to avoid leaking sensitive data
                if not Environment.IS_PRODUCTION:
                    logging.debug(f"User-Agent: {user_agent}")
            else:
                logging.warning(f"No Telegram WebApp initData found in request")
            
            # Only log sensitive request details in development
            if not Environment.IS_PRODUCTION:
                logging.debug(f"Request headers: {dict(request.headers)}")
                logging.debug(f"Request args: {dict(request.args)}")
                logging.debug(f"Request URL: {request.url}")
            return None
            
        # Verify initData using bot token (production mode)
        if BOT_TOKEN:
            verified_data = verify_telegram_webapp_data(init_data, BOT_TOKEN)
            if not verified_data:
                logging.warning("Telegram WebApp authentication failed")
                return None
            
            # Extract user ID from verified data
            user_data = verified_data.get('user')
            if not user_data or 'id' not in user_data:
                logging.warning("No user ID in verified Telegram WebApp data")
                return None
                
            user_id = str(user_data['id'])
            logging.info(f"Successfully authenticated Telegram user: {user_id} (verified)")
            return user_id
            
        else:
            # Development mode - parse without verification
            if Environment.IS_DEVELOPMENT or Environment.IS_REPLIT:
                parsed_data = parse_telegram_init_data(init_data)
                if not parsed_data:
                    logging.error("Failed to parse Telegram WebApp data in development mode")
                    return None
                    
                # Extract user ID from parsed data
                user_data = parsed_data.get('user')
                if not user_data or 'id' not in user_data:
                    logging.warning("No user ID in parsed Telegram WebApp data (development mode)")
                    return None
                    
                user_id = str(user_data['id'])
                logging.warning(f"Development mode: Using unverified Telegram user: {user_id}")
                return user_id
            else:
                logging.error("Bot token not configured - cannot verify authentication in production")
                return None
        
    except Exception as e:
        logging.error(f"Error in get_authenticated_user_id: {e}")
        return None


# SECURE: Session Management Functions for Telegram WebApp Authentication
def establish_user_session(user_id: str, user_data: Optional[Dict[str, Any]] = None) -> bool:
    """
    Establish a secure Flask session after successful authentication.
    
    Args:
        user_id: Verified Telegram user ID
        user_data: Optional additional user data from verification
        
    Returns:
        bool: True if session was established successfully
    """
    try:
        session.permanent = True
        session['user_id'] = user_id
        session['authenticated'] = True
        session['auth_timestamp'] = int(time.time())
        
        # Store additional user info if available
        if user_data:
            session['username'] = user_data.get('username')
            session['first_name'] = user_data.get('first_name')
            session['last_name'] = user_data.get('last_name')
        
        logging.info(f"Session established for user: {user_id}")
        return True
        
    except Exception as e:
        logging.error(f"Error establishing session for user {user_id}: {e}")
        return False


def get_user_from_session() -> Optional[str]:
    """
    Get authenticated user ID from existing Flask session.
    
    Returns:
        str: User ID if session is valid and not expired
        None: If no valid session exists
    """
    try:
        if not session.get('authenticated'):
            return None
            
        user_id = session.get('user_id')
        auth_timestamp = session.get('auth_timestamp', 0)
        current_time = int(time.time())
        
        # Check if session is expired (24 hours)
        if current_time - auth_timestamp > 86400:
            logging.info(f"Session expired for user {user_id}")
            clear_user_session()
            return None
            
        return user_id
        
    except Exception as e:
        logging.error(f"Error getting user from session: {e}")
        return None


def clear_user_session() -> None:
    """Clear user session data."""
    try:
        session.clear()
        logging.info("User session cleared")
    except Exception as e:
        logging.error(f"Error clearing session: {e}")


def get_authenticated_user() -> Optional[str]:
    """
    Get authenticated user ID with session-first approach.
    
    This function first checks for an existing valid session, then falls back
    to verifying Telegram WebApp data for new authentications.
    
    Returns:
        str: Verified user_id if authentication is valid
        None: If authentication fails or is missing
    """
    # First, try to get user from existing session
    user_id = get_user_from_session()
    if user_id:
        return user_id
    
    # If no valid session, try to authenticate via Telegram WebApp data
    user_id = get_authenticated_user_id()
    if user_id:
        # Get additional user data for session
        try:
            init_data = None
            if request.method == 'GET':
                init_data = request.args.get('initData') or request.args.get('tg_init_data')
            elif request.method == 'POST':
                if request.is_json:
                    request_data = request.get_json() or {}
                    init_data = request_data.get('initData')
                else:
                    init_data = request.form.get('initData')
            
            if not init_data:
                init_data = request.headers.get('X-Telegram-Init-Data')
            
            if init_data and BOT_TOKEN:
                if request.args.get('tg_init_data'):
                    init_data = urllib.parse.unquote(init_data)
                
                verified_data = verify_telegram_webapp_data(init_data, BOT_TOKEN)
                user_data = verified_data.get('user', {}) if verified_data else {}
                
                # Establish session for future requests
                establish_user_session(user_id, user_data)
        except Exception as e:
            logging.error(f"Error establishing session after authentication: {e}")
    
    return user_id


def generate_csrf_token() -> str:
    """Generate a secure CSRF token for the current session."""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def validate_csrf_token(token: Optional[str]) -> bool:
    """Validate CSRF token against the session token."""
    if not token:
        return False
    session_token = session.get('csrf_token')
    if not session_token:
        return False
    return session_token == token


def require_authentication(f):
    """
    Decorator for routes that require authentication.
    Redirects to authentication if user is not logged in.
    """
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = get_authenticated_user()
        if not user_id:
            # Return access wall for unauthenticated users
            return render_template(
                "access_wall.html",
                access_data={
                    "title": "🔒 Authentication Required",
                    "message": "Please access this app through the official Telegram bot.",
                    "status": "auth_required",
                    "show_request_button": False
                },
                user_id=None
            )
        return f(*args, **kwargs)
    return decorated_function


def require_csrf_token(f):
    """
    Decorator for routes that require CSRF token validation for POST requests.
    """
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == 'POST':
            token = None
            if request.is_json:
                data = request.get_json(silent=True) or {}
                token = data.get('csrf_token')
            else:
                token = request.form.get('csrf_token')
            
            if not validate_csrf_token(token):
                return jsonify({
                    'error': 'Invalid CSRF token',
                    'message': 'Security validation failed. Please refresh and try again.'
                }), 403
        
        return f(*args, **kwargs)
    return decorated_function


# DEPRECATED: Legacy function for backward compatibility only
# WARNING: This function is insecure and should not be used for authentication
def get_user_id_from_request(default_user_id=None):
    """
    DEPRECATED: This function is vulnerable to privilege escalation attacks.
    Use get_authenticated_user_id() instead for secure authentication.
    
    This function is kept only for backward compatibility with non-authenticated endpoints.
    """
    logging.warning("SECURITY WARNING: Using deprecated get_user_id_from_request() - use get_authenticated_user_id() instead")
    return request.args.get(
        "user_id", default_user_id or Environment.DEFAULT_TEST_USER_ID
    )


def _should_process_take_profit(config):
    """Check if take profit monitoring should proceed."""
    return config.take_profits and config.unrealized_pnl > 0


def _calculate_profit_percentage(config):
    """Calculate current profit percentage based on amount."""
    return (config.unrealized_pnl / config.amount) * 100


def _get_tp_data(tp):
    """Extract percentage and allocation from TP data."""
    tp_percentage = tp.get("percentage", 0) if isinstance(tp, dict) else tp
    allocation = tp.get("allocation", 0) if isinstance(tp, dict) else 0
    return tp_percentage, allocation


def _log_tp_execution(
    user_id, trade_id, config, tp_level, profit_percentage, tp_percentage
):
    """Log take profit execution."""
    logging.warning(
        f"TAKE-PROFIT {tp_level} TRIGGERED: {config.symbol} {config.side} position for user {user_id} - "
        f"Profit: {profit_percentage:.2f}% >= {tp_percentage}%"
    )


def _process_full_tp_closure(config, user_id, trade_id, tp_level):
    """Process full position closure at take profit."""
    config.status = "stopped"
    config.final_pnl = config.unrealized_pnl + getattr(config, "realized_pnl", 0.0)
    config.closed_at = get_iran_time().isoformat()
    config.unrealized_pnl = 0.0

    save_trade_to_db(user_id, config)

    # Trade closure now logged via database save_trade_to_db()

    logging.info(
        f"Position auto-closed at TP{tp_level}: {config.symbol} {config.side} - Final P&L: ${config.final_pnl:.2f}"
    )
    return True


def _calculate_partial_pnl(config, tp_level, allocation):
    """Calculate partial PnL for take profit execution."""
    tp_calculations = calculate_tp_sl_prices_and_amounts(config)
    current_tp_data = None
    for tp_calc in tp_calculations.get("take_profits", []):
        if tp_calc["level"] == tp_level:
            current_tp_data = tp_calc
            break

    if current_tp_data:
        return current_tp_data["profit_amount"]
    else:
        # Fallback to old calculation if TP data not found
        return config.unrealized_pnl * (allocation / 100)


def _handle_breakeven_logic(config, user_id, tp_index):
    """Handle breakeven stop loss logic after TP1."""
    breakeven_numeric = 0.0
    if hasattr(config, "breakeven_after"):
        if config.breakeven_after == "tp1":
            breakeven_numeric = 1.0
        elif config.breakeven_after == "tp2":
            breakeven_numeric = 2.0
        elif config.breakeven_after == "tp3":
            breakeven_numeric = 3.0
        elif isinstance(config.breakeven_after, (int, float)):
            breakeven_numeric = float(config.breakeven_after)

    if (
        tp_index == 0 and breakeven_numeric > 0
    ):  # First TP triggered and breakeven enabled
        if not getattr(config, "breakeven_sl_triggered", False):
            original_sl_percent = config.stop_loss_percent
            config.breakeven_sl_triggered = True
            config.breakeven_sl_price = config.entry_price
            logging.info(
                f"AUTO BREAK-EVEN: Moving SL to entry price after TP1 - was {original_sl_percent}%, now break-even"
            )
            save_trade_to_db(user_id, config)


def _process_partial_tp_closure(
    config, user_id, trade_id, tp_index, tp_level, allocation
):
    """Process partial position closure at take profit."""
    # Store original amounts before any TP triggers
    if not hasattr(config, "original_amount"):
        config.original_amount = config.amount
    if not hasattr(config, "original_margin"):
        config.original_margin = calculate_position_margin(
            config.original_amount, config.leverage
        )

    partial_pnl = _calculate_partial_pnl(config, tp_level, allocation)
    remaining_amount = config.amount * ((100 - allocation) / 100)

    # Partial closure now logged via database save_trade_to_db()

    # Update realized P&L with the profit from this TP
    if not hasattr(config, "realized_pnl"):
        config.realized_pnl = 0.0
    config.realized_pnl += partial_pnl

    # Update position with remaining amount
    config.amount = remaining_amount
    
    # Recalculate unrealized P&L based on new position size
    if config.current_price and config.entry_price:
        config.unrealized_pnl = calculate_unrealized_pnl(
            config.entry_price,
            config.current_price,
            config.amount,
            config.leverage,
            config.side,
        )

    # Remove triggered TP from list safely
    if tp_index < len(config.take_profits):
        config.take_profits.pop(tp_index)
    else:
        logging.warning(
            f"TP index {tp_index} out of bounds for {config.symbol}, skipping removal"
        )

    save_trade_to_db(user_id, config)
    logging.info(
        f"Partial TP{tp_level} triggered: {config.symbol} {config.side} - Closed {allocation}% for ${partial_pnl:.2f}"
    )

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

    # Check each TP level (iterate backwards to avoid index shifting issues)
    for i in range(len(config.take_profits) - 1, -1, -1):
        tp = config.take_profits[i]
        tp_percentage, allocation = _get_tp_data(tp)

        if tp_percentage > 0 and profit_percentage >= tp_percentage:
            tp_level = i + 1
            _log_tp_execution(
                user_id, trade_id, config, tp_level, profit_percentage, tp_percentage
            )

            if allocation >= 100:
                return _process_full_tp_closure(config, user_id, trade_id, tp_level)
            else:
                _process_partial_tp_closure(
                    config, user_id, trade_id, i, tp_level, allocation
                )
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
    monitoring_result = {"processed": 0, "status": "inactive"}

    try:
        # Get all active positions from database
        all_active_trades = TradeConfiguration.query.filter_by(status="active").all()

        if all_active_trades:
            logging.info(
                f"HEALTH CHECK: Found {len(all_active_trades)} active positions for monitoring"
            )
            for trade in all_active_trades:
                logging.info(
                    f"HEALTH CHECK: Processing position {trade.trade_id} ({trade.symbol}) for user {trade.telegram_user_id}"
                )

        for trade in all_active_trades:
            try:
                user_id = trade.telegram_user_id

                # Update price for the symbol (works without credentials)
                if trade.symbol:
                    current_price = get_live_market_price(
                        trade.symbol, use_cache=True, user_id=user_id
                    )

                    if current_price and current_price > 0:
                        # Update current price in database
                        trade.current_price = current_price

                        # Calculate P&L and check TP/SL triggers (works for all positions)
                        if trade.entry_price and trade.entry_price > 0:
                            # Calculate unrealized P&L
                            if trade.side == "long":
                                price_change = (
                                    current_price - trade.entry_price
                                ) / trade.entry_price
                            else:  # short
                                price_change = (
                                    trade.entry_price - current_price
                                ) / trade.entry_price

                            # Update unrealized P&L
                            position_value = trade.amount * trade.leverage
                            trade.unrealized_pnl = position_value * price_change

                            # Check for TP/SL triggers (basic monitoring without executing trades)
                            # For now, just log potential trigger events for monitoring
                            check_position_trigger_alerts(trade, current_price)

                        all_positions_processed += 1
                        logging.debug(
                            f"HEALTH CHECK: Successfully processed position {trade.trade_id} - Price: ${current_price}, P&L: ${trade.unrealized_pnl:.2f}"
                        )

            except Exception as e:
                logging.warning(
                    f"Position monitoring failed for trade {trade.trade_id}: {e}"
                )

        # Commit price and P&L updates
        if all_positions_processed > 0:
            db.session.commit()
            logging.info(
                f"HEALTH CHECK: Committed price updates for {all_positions_processed} positions"
            )

        monitoring_result = {
            "processed": all_positions_processed,
            "status": (
                "active" if all_positions_processed > 0 else "no_active_positions"
            ),
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
    client = None  # Initialize client to avoid unbound variable error
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
            return (
                None,
                jsonify(
                    {
                        "error": "Invalid API credentials. Please check your Toobit API key and secret in the API Keys menu.",
                        "debug_info": {
                            "has_api_key": bool(api_key),
                            "has_api_secret": bool(api_secret),
                            "credential_lengths": {
                                "api_key": len(api_key) if api_key else 0,
                                "api_secret": len(api_secret) if api_secret else 0,
                            },
                        },
                    }
                ),
                400,
            )

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
                    if exchange_info and "symbols" in exchange_info:
                        valid_symbols = [s["symbol"] for s in exchange_info["symbols"]]
                        logging.info(
                            f"[DEBUG] Found {len(valid_symbols)} available symbols"
                        )
                        logging.info(f"[DEBUG] First 10 symbols: {valid_symbols[:10]}")

                        # This symbol validation is for debugging but doesn't affect execution

                except Exception as e:
                    logging.error(f"[DEBUG] Failed to get exchange info: {e}")
            else:
                logging.warning("Empty balance response from API")

        except Exception as conn_error:
            error_details = {
                "error_type": type(conn_error).__name__,
                "error_message": str(conn_error),
                "last_client_error": getattr(client, "last_error", None),
            }

            logging.error(f"API connection failed: {conn_error}")

            # Provide user-friendly error messages based on error type
            if "unauthorized" in str(conn_error).lower() or "401" in str(conn_error):
                user_message = "Invalid API credentials. Please verify your Toobit API key and secret."
            elif "forbidden" in str(conn_error).lower() or "403" in str(conn_error):
                user_message = "API access forbidden. Please check your Toobit API permissions for futures trading."
            elif "timeout" in str(conn_error).lower():
                user_message = "Connection timeout. Please try again."
            else:
                user_message = f"Exchange connection failed: {str(conn_error)}"

            return (
                None,
                jsonify(
                    {
                        "error": user_message,
                        "debug_info": error_details,
                        "troubleshooting": [
                            "Verify your Toobit API key and secret are correct",
                            "Ensure your API key has futures trading permissions",
                            "Check if your Toobit account is verified and funded",
                            "Make sure you copied the full API key without spaces",
                        ],
                    }
                ),
                400,
            )

        return client, None

    except Exception as e:
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "last_client_error": (
                getattr(client, "last_error", None) if "client" in locals() else None
            ),
        }

        logging.error(
            f"[RENDER TRADING ERROR] Credential validation failed: {error_details}"
        )

        # Import stack trace for detailed debugging
        import traceback

        logging.error(f"[RENDER STACK TRACE] {traceback.format_exc()}")

        return (
            None,
            jsonify(
                {
                    "error": f"Credential validation failed: {str(e)}",
                    "debug_info": error_details,
                    "troubleshooting": [
                        "Check your internet connection",
                        "Verify your Toobit API credentials are active",
                        "Try refreshing the page and attempting the trade again",
                    ],
                }
            ),
            500,
        )


# Configure logging using centralized config
logging.basicConfig(level=getattr(logging, get_log_level()))

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")
if not app.secret_key:
    raise ValueError("SESSION_SECRET environment variable is required")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Record app start time for uptime tracking
app.config["START_TIME"] = time.time()

# Configure secure session settings
app.config.update(
    SESSION_COOKIE_SECURE=Environment.IS_PRODUCTION,  # HTTPS only in production
    SESSION_COOKIE_HTTPONLY=True,  # Prevent XSS attacks
    SESSION_COOKIE_SAMESITE='Strict',  # CSRF protection
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24),  # 24 hour session timeout
)

# Production environment validation - ensure critical database configuration
if Environment.IS_PRODUCTION:
    # DATABASE_URL must be PostgreSQL in production (not SQLite)
    database_url_check = os.environ.get("DATABASE_URL")
    if not database_url_check or database_url_check.startswith("sqlite"):
        raise ValueError(
            "DATABASE_URL must be set to a PostgreSQL URL in production. "
            "SQLite is not suitable for multi-worker deployments."
        )
    
    logging.info("Production environment validation passed - DATABASE_URL is properly configured")
else:
    logging.warning("Running in development mode - some security checks are relaxed")

# Configure database using centralized config
database_url = get_database_url()
if not database_url:
    # Fallback to SQLite for development
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "instance",
        "trading_bot.db",
    )
    database_url = f"sqlite:///{db_path}"
    logging.info(f"Using SQLite database for development at {db_path}")

# Validate the database URL before setting it
try:
    from sqlalchemy import create_engine

    # Test if the URL can be parsed by SQLAlchemy
    test_engine = create_engine(
        database_url, strategy="mock", executor=lambda sql, *_: None
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    logging.info(f"Database configured successfully: {database_url.split('://')[0]}")
except Exception as e:
    # If URL is invalid, fall back to SQLite
    logging.error(f"Invalid database URL, falling back to SQLite: {e}")
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "instance",
        "trading_bot.db",
    )
    database_url = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url

# Database engine configuration based on database type
if database_url.startswith("sqlite"):
    # SQLite configuration for development
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": DatabaseConfig.POOL_PRE_PING,
        "pool_recycle": DatabaseConfig.STANDARD_POOL_RECYCLE,
    }
elif database_url.startswith("postgresql") and (
    Environment.IS_VERCEL or "neon" in database_url.lower()
):
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
            "keepalives_count": DatabaseConfig.KEEPALIVES_COUNT,
        },
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
            "application_name": DatabaseConfig.APPLICATION_NAME,
        },
    }
elif database_url.startswith("postgresql"):
    # Standard PostgreSQL configuration (Replit or other)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": DatabaseConfig.STANDARD_POOL_RECYCLE,
        "pool_pre_ping": DatabaseConfig.POOL_PRE_PING,
        "pool_size": DatabaseConfig.STANDARD_POOL_SIZE,
        "max_overflow": DatabaseConfig.STANDARD_MAX_OVERFLOW,
    }
else:
    # Fallback configuration
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": DatabaseConfig.POOL_PRE_PING,
        "pool_recycle": DatabaseConfig.STANDARD_POOL_RECYCLE,
    }

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize database
db.init_app(app)

# Initialize unified data sync service (combines cache cleanup and klines workers)
start_unified_data_sync_service(app)
logging.info("Unified data sync service initialized with coordinated cache cleanup and klines management")


# Database migration helpers
def _create_cache_tables():
    """Create SMC signal cache and KlinesCache tables with indexes."""
    from sqlalchemy import text

    try:
        # Ensure SMC signal cache table exists
        db.session.execute(
            text(
                """
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
        """
            )
        )

        # Create index for efficient SMC signal queries
        db.session.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS idx_smc_signal_cache_symbol_expires 
            ON smc_signal_cache(symbol, expires_at)
        """
            )
        )

        # Ensure KlinesCache table exists with proper indexes
        db.session.execute(
            text(
                """
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
        """
            )
        )

        # Create indexes for efficient klines cache queries
        db.session.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS idx_klines_symbol_timeframe_timestamp 
            ON klines_cache(symbol, timeframe, timestamp)
        """
            )
        )

        db.session.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS idx_klines_expires 
            ON klines_cache(expires_at)
        """
            )
        )

        db.session.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS idx_klines_symbol_timeframe_expires 
            ON klines_cache(symbol, timeframe, expires_at)
        """
            )
        )

        db.session.commit()
        logging.info("SMC signal cache and KlinesCache tables ensured for deployment")
    except Exception as smc_error:
        logging.warning(f"Cache table creation failed (may already exist): {smc_error}")
        db.session.rollback()


def _fix_toobit_testnet_issues():
    """Fix Toobit testnet issues for existing data."""
    from sqlalchemy import text

    try:
        # First check if there are any Toobit users before running fixes
        toobit_count = db.session.execute(
            text(
                """
            SELECT COUNT(*) FROM user_credentials 
            WHERE exchange_name = 'toobit' AND is_active = true
        """
            )
        ).scalar()

        if toobit_count and toobit_count > 0:
            # Only run Toobit fixes if there are actual Toobit users
            db.session.execute(
                text(
                    """
                UPDATE user_credentials 
                SET testnet_mode = false 
                WHERE exchange_name = 'toobit' AND testnet_mode = true
            """
                )
            )
            db.session.commit()

            # Additional Vercel/Neon protection - ensure all Toobit credentials are mainnet
            toobit_testnet_users = UserCredentials.query.filter_by(
                exchange_name="toobit", testnet_mode=True, is_active=True
            ).all()

            if toobit_testnet_users:
                for cred in toobit_testnet_users:
                    cred.testnet_mode = False
                    logging.info(
                        f"Vercel/Neon: Disabled testnet mode for Toobit user {cred.telegram_user_id}"
                    )

                db.session.commit()
                logging.info(
                    f"Vercel/Neon: Fixed {len(toobit_testnet_users)} Toobit testnet credentials"
                )

            logging.info("Fixed Toobit testnet mode for existing credentials")

            # CRITICAL: Force disable testnet mode for ALL environments (Replit, Vercel, Render)
            all_toobit_creds = UserCredentials.query.filter_by(
                exchange_name="toobit", is_active=True
            ).all()

            testnet_fixes = 0
            for cred in all_toobit_creds:
                if cred.testnet_mode:
                    cred.testnet_mode = False
                    testnet_fixes += 1
                    logging.warning(
                        f"RENDER FIX: Disabled testnet mode for Toobit user {cred.telegram_user_id}"
                    )

            if testnet_fixes > 0:
                db.session.commit()
                logging.info(
                    f"RENDER FIX: Updated {testnet_fixes} Toobit credentials to mainnet mode"
                )
        else:
            logging.debug("No Toobit users found - skipping Toobit testnet fixes")

        # CRITICAL FIX: Ensure all non-Toobit exchanges default to live mode (not testnet)
        try:
            non_toobit_testnet_users = UserCredentials.query.filter(
                UserCredentials.exchange_name != "toobit",
                UserCredentials.testnet_mode.is_(True),
                UserCredentials.is_active.is_(True),
            ).all()

            if non_toobit_testnet_users:
                fixed_count = 0
                for cred in non_toobit_testnet_users:
                    cred.testnet_mode = False  # Switch to live mode by default
                    fixed_count += 1
                    logging.info(
                        f"LIVE MODE FIX: Switched {cred.exchange_name} user {cred.telegram_user_id} to live trading"
                    )

                db.session.commit()
                logging.info(
                    f"LIVE MODE FIX: Updated {fixed_count} users from testnet to live mode"
                )
            else:
                logging.debug("No users stuck in testnet mode - migration not needed")

        except Exception as testnet_fix_error:
            logging.warning(
                f"Live mode migration failed (may not be needed): {testnet_fix_error}"
            )
            db.session.rollback()

    except Exception as toobit_fix_error:
        logging.warning(
            f"Toobit testnet fix failed (may not be needed): {toobit_fix_error}"
        )
        db.session.rollback()


def _migrate_database_columns():
    """Migrate database columns for trade configurations."""
    from sqlalchemy import text

    try:
        # Check for missing columns
        required_columns = [
            ("breakeven_sl_triggered", "BOOLEAN DEFAULT FALSE"),
            ("realized_pnl", "FLOAT DEFAULT 0.0"),
            ("original_amount", "FLOAT DEFAULT 0.0"),
            ("original_margin", "FLOAT DEFAULT 0.0"),
        ]

        migrations_needed = []
        # Check database type for proper column checking
        is_sqlite = database_url.startswith("sqlite")

        for column_name, column_def in required_columns:
            if is_sqlite:
                # SQLite column checking
                result = db.session.execute(
                    text(
                        """
                    PRAGMA table_info(trade_configurations)
                """
                    )
                )
                columns = [
                    row[1] for row in result.fetchall()
                ]  # row[1] is the column name
                if column_name not in columns:
                    migrations_needed.append((column_name, column_def))
            else:
                # PostgreSQL column checking
                result = db.session.execute(
                    text(
                        """
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'trade_configurations' 
                    AND column_name = :column_name
                """
                    ),
                    {"column_name": column_name},
                )

                if not result.fetchone():
                    migrations_needed.append((column_name, column_def))

        # Apply migrations
        for column_name, column_def in migrations_needed:
            logging.info(f"Adding missing {column_name} column")
            db.session.execute(
                text(
                    f"""
                ALTER TABLE trade_configurations 
                ADD COLUMN {column_name} {column_def}
            """
                )
            )

        if migrations_needed:
            db.session.commit()
            logging.info(
                f"Database migration completed successfully - added {len(migrations_needed)} columns"
            )

    except Exception as migration_error:
        logging.warning(
            f"Migration check failed (table may not exist yet): {migration_error}"
        )
        db.session.rollback()


def run_database_migrations():
    """Run database migrations to ensure schema compatibility"""
    try:
        with app.app_context():
            # Run all migration steps using helper functions
            _create_cache_tables()
            _fix_toobit_testnet_issues()
            _migrate_database_columns()
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


# Bot token and webhook URL from environment with proper validation
def get_bot_token() -> Optional[str]:
    """Get bot token from environment with proper error handling for production."""
    bot_token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    
    if not bot_token:
        if Environment.IS_PRODUCTION:
            logging.error("CRITICAL: BOT_TOKEN or TELEGRAM_BOT_TOKEN environment variable is required for production")
            raise ValueError("BOT_TOKEN or TELEGRAM_BOT_TOKEN environment variable is required for production")
        else:
            logging.warning("BOT_TOKEN not configured - Telegram authentication will be disabled in development")
    
    return bot_token

BOT_TOKEN = get_bot_token()

# Trading data is stored in the database through the web interface
# API setup is handled through the web interface

# Thread locks for global state to prevent race conditions
trade_configs_lock = threading.RLock()
paper_balances_lock = threading.RLock()
api_setup_lock = threading.RLock()
user_preferences_lock = threading.RLock()

# Multi-trade management storage
user_trade_configs: Dict[int, Dict[str, Any]] = {}  # {user_id: {trade_id: TradeConfig}}
user_selected_trade: Dict[int, Optional[str]] = {}  # {user_id: trade_id}

# Whitelist configuration
BOT_OWNER_ID = os.environ.get("BOT_OWNER_ID", Environment.DEFAULT_TEST_USER_ID)  # Bot owner's telegram user ID
WHITELIST_ENABLED = os.environ.get("WHITELIST_ENABLED", "true").lower() == "true"

# Whitelist functions
def is_user_whitelisted(user_id: str) -> bool:
    """Check if user is whitelisted for access"""
    if not WHITELIST_ENABLED:
        return True
    
    # Bot owner always has access
    if str(user_id) == str(BOT_OWNER_ID):
        return True
    
    try:
        user_whitelist = UserWhitelist.query.filter_by(telegram_user_id=str(user_id)).first()
        return bool(user_whitelist and user_whitelist.is_approved())
    except Exception as e:
        logging.error(f"Error checking whitelist status for user {user_id}: {e}")
        return False

def is_bot_owner(user_id: str) -> bool:
    """Check if user is the bot owner"""
    return str(user_id) == str(BOT_OWNER_ID)

def register_user_for_whitelist(user_id: str, username: Optional[str] = None, first_name: Optional[str] = None, last_name: Optional[str] = None):
    """Register a new user for whitelist approval"""
    try:
        # Check if user already exists
        existing_user = UserWhitelist.query.filter_by(telegram_user_id=str(user_id)).first()
        if existing_user:
            if existing_user.is_approved():
                return {"status": "already_approved", "message": "You are already approved for access."}
            elif existing_user.is_pending():
                return {"status": "pending", "message": "Your request is pending approval."}
            elif existing_user.is_rejected():
                return {"status": "rejected", "message": "Your access request was rejected."}
            elif existing_user.is_banned():
                return {"status": "banned", "message": "You are banned from accessing the system."}
        
        # Create new whitelist entry
        new_user = UserWhitelist()
        new_user.telegram_user_id = str(user_id)
        new_user.telegram_username = username or ""
        new_user.first_name = first_name or ""
        new_user.last_name = last_name or ""
        new_user.status = "pending"
        
        db.session.add(new_user)
        db.session.commit()
        
        logging.info(f"New user registered for whitelist: {user_id} ({username})")
        return {"status": "registered", "message": "Your access request has been submitted and is pending approval."}
        
    except Exception as e:
        logging.error(f"Error registering user for whitelist: {e}")
        db.session.rollback()
        return {"status": "error", "message": "An error occurred while processing your request."}

def record_user_access(user_id: str):
    """Record user access for tracking"""
    try:
        user_whitelist = UserWhitelist.query.filter_by(telegram_user_id=str(user_id)).first()
        if user_whitelist:
            user_whitelist.record_access()
            db.session.commit()
    except Exception as e:
        logging.error(f"Error recording access for user {user_id}: {e}")

def get_access_wall_message(user_id: str) -> dict:
    """Get appropriate message for users hitting the access wall"""
    try:
        user_whitelist = UserWhitelist.query.filter_by(telegram_user_id=str(user_id)).first()
        
        if not user_whitelist:
            return {
                "title": "🚫 Access Required",
                "message": "This bot requires approval to access. Please click 'Request Access' to submit your request.",
                "status": "not_registered",
                "show_request_button": True
            }
        elif user_whitelist.is_pending():
            return {
                "title": "⏳ Pending Approval",
                "message": f"Your access request is pending approval. Requested on {format_iran_time(user_whitelist.requested_at)}.",
                "status": "pending",
                "show_request_button": False
            }
        elif user_whitelist.is_rejected():
            return {
                "title": "❌ Access Denied",
                "message": f"Your access request was rejected. Reason: {user_whitelist.review_notes or 'No reason provided.'}",
                "status": "rejected",
                "show_request_button": False
            }
        elif user_whitelist.is_banned():
            return {
                "title": "🚫 Banned",
                "message": f"You have been banned from accessing this bot. Reason: {user_whitelist.review_notes or 'No reason provided.'}",
                "status": "banned",
                "show_request_button": False
            }
        else:
            return {
                "title": "✅ Access Granted",
                "message": "You have access to the bot.",
                "status": "approved",
                "show_request_button": False
            }
    except Exception as e:
        logging.error(f"Error getting access wall message for user {user_id}: {e}")
        return {
            "title": "❗ Error",
            "message": "An error occurred while checking your access status.",
            "status": "error",
            "show_request_button": False
        }


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
            if current_time - cache_entry["timestamp"] < credentials_cache_ttl:
                has_valid_creds = cache_entry["has_creds"]
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
                    logging.warning(
                        f"User {user_id} wants live mode but has no valid credentials - forcing paper mode"
                    )
                    return True

                return manual_preference
            else:
                # User hasn't set a preference - use intelligent defaults
                if has_valid_creds:
                    # User has credentials but no preference - default to live mode
                    logging.info(
                        f"User {user_id} has credentials but no mode preference - defaulting to live mode"
                    )
                    user_paper_trading_preferences[chat_id] = (
                        False  # Set default for future use
                    )
                    return False
                else:
                    # No credentials - must use paper mode
                    logging.info(
                        f"User {user_id} has no credentials - defaulting to paper mode"
                    )
                    user_paper_trading_preferences[chat_id] = (
                        True  # Set default for future use
                    )
                    return True

    except Exception as e:
        logging.error(f"Error determining trading mode for user {user_id}: {e}")
        # On error, default to paper mode for safety
        return True


def _refresh_credential_cache(chat_id):
    """Refresh credential cache for a user (RENDER OPTIMIZATION)"""
    try:
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(chat_id), is_active=True
        ).first()

        has_valid_creds = user_creds and user_creds.has_credentials()

        # Update cache
        user_credentials_cache[chat_id] = {
            "has_creds": has_valid_creds,
            "timestamp": time.time(),
            "exchange": user_creds.exchange_name if user_creds else None,
        }

        return has_valid_creds
    except Exception as e:
        logging.error(f"Error refreshing credential cache for user {chat_id}: {e}")
        return False


# Paper trading balance tracking
user_paper_balances: Dict[int, float] = {}  # {user_id: balance_amount}

# Manual paper trading mode preferences
user_paper_trading_preferences: Dict[int, bool] = {}  # {user_id: True/False}

# RENDER PERFORMANCE OPTIMIZATION: Credential caching to prevent repeated DB queries
user_credentials_cache: Dict[int, Dict[str, Any]] = (
    {}
)  # {user_id: {'has_creds': bool, 'timestamp': time, 'exchange': str}}
credentials_cache_ttl = 300  # 5 minutes cache for credentials

# Cache for database loads to prevent frequent database hits
user_data_cache: Dict[int, Dict[str, Any]] = (
    {}
)  # {user_id: {'data': trades_data, 'timestamp': last_load_time, 'version': data_version}}
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
        self.breakeven_sl_triggered = (
            False  # Track if breakeven stop loss has been triggered
        )
        self.breakeven_sl_price = 0.0  # Price at which break-even stop loss triggers
        # Trailing Stop System - Clean Implementation
        self.trailing_stop_enabled = False
        self.trail_percentage = 0.0  # Percentage for trailing stop
        self.trail_activation_price = 0.0  # Price level to activate trailing stop
        self.waiting_for_trail_percent = (
            False  # Track if waiting for trail percentage input
        )
        self.waiting_for_trail_activation = (
            False  # Track if waiting for trail activation price
        )
        self.status = "configured"  # configured, pending, active, stopped
        # Margin tracking
        self.position_margin = 0.0  # Margin used for this position
        self.unrealized_pnl = 0.0  # Current floating P&L
        self.current_price = 0.0  # Current market price
        self.position_size = 0.0  # Actual position size in contracts
        self.position_value = 0.0  # Total position value
        self.realized_pnl = 0.0  # Realized P&L from triggered take profits
        self.final_pnl = 0.0  # Final P&L when position is closed
        self.closed_at = ""  # Timestamp when position was closed
        self.notes = ""  # Additional notes for the trade
        self.exchange = "lbank"  # Exchange to use for this trade (default: lbank)

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
            summary += "Entry: Market Price\n"

        # Show take profits with prices if entry price is available
        if self.take_profits:
            summary += "Take Profits:\n"
            tp_sl_data = (
                calculate_tp_sl_prices_and_amounts(self) if self.entry_price > 0 else {}
            )

            for i, tp in enumerate(self.take_profits, 1):
                tp_percentage = tp.get("percentage", 0)
                tp_allocation = tp.get("allocation", 0)

                if (
                    tp_sl_data.get("take_profits")
                    and len(tp_sl_data["take_profits"]) >= i
                ):
                    tp_calc = tp_sl_data["take_profits"][i - 1]
                    summary += f"  TP{i}: ${tp_calc['price']:.4f} (+${tp_calc['profit_amount']:.2f}) [{tp_percentage}% - {tp_allocation}%]\n"
                else:
                    summary += f"  TP{i}: {tp_percentage}% ({tp_allocation}%)\n"
        else:
            summary += "Take Profits: Not set\n"

        # Show stop loss with price if entry price is available
        tp_sl_data = (
            calculate_tp_sl_prices_and_amounts(self) if self.entry_price > 0 else {}
        )

        if tp_sl_data.get("stop_loss"):
            sl_calc = tp_sl_data["stop_loss"]
            if sl_calc.get("is_breakeven"):
                summary += f"Stop Loss: ${sl_calc['price']:.4f} (Break-even)\n"
            else:
                summary += f"Stop Loss: ${sl_calc['price']:.4f} (-${sl_calc['loss_amount']:.2f}) [{self.stop_loss_percent}%]\n"
        elif self.stop_loss_percent > 0:
            summary += f"Stop Loss: {self.stop_loss_percent}%\n"
        else:
            summary += "Stop Loss: Not set\n"

        # Show trailing stop status
        if self.trailing_stop_enabled:
            summary += "Trailing Stop: Enabled\n"
            if self.trail_percentage > 0:
                summary += f"  Trail %: {self.trail_percentage}%\n"
            if self.trail_activation_price > 0:
                summary += f"  Activation: ${self.trail_activation_price:.4f}\n"
        else:
            summary += "Trailing Stop: Disabled\n"

        summary += f"Status: {self.status.title()}\n"
        return summary

    def get_progress_indicator(self):
        """Get a visual progress indicator showing configuration completion"""
        steps = {
            "Symbol": "✅" if self.symbol else "⏳",
            "Side": "✅" if self.side else "⏳",
            "Amount": "✅" if self.amount > 0 else "⏳",
            "Entry": (
                "✅"
                if (
                    self.entry_type == "market"
                    or (self.entry_type == "limit" and self.entry_price > 0)
                )
                else "⏳"
            ),
            "Take Profits": "✅" if self.take_profits else "⏳",
            "Stop Loss": (
                "✅"
                if self.stop_loss_percent > 0
                else (
                    "⚖️"
                    if self.stop_loss_percent == 0.0
                    and hasattr(self, "status")
                    and self.status == "active"
                    else "⏳"
                )
            ),
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
            header += "   🎯 Entry: Market Price\n"
        else:
            header += "   🎯 Entry: Not set\n"

        if self.take_profits:
            header += f"   🎯 Take Profits: {len(self.take_profits)} levels\n"
        else:
            header += "   🎯 Take Profits: Not set\n"

        if self.stop_loss_percent > 0:
            header += f"   🛑 Stop Loss: {self.stop_loss_percent}%\n"
        elif (
            self.stop_loss_percent == 0.0
            and hasattr(self, "status")
            and self.status == "active"
        ):
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
                if not hasattr(db.engine, "table_names"):
                    db.create_all()

                # Use read-committed isolation for Neon
                db_trades = (
                    TradeConfiguration.query.filter_by(telegram_user_id=user_id_str)
                    .order_by(TradeConfiguration.created_at.desc())
                    .all()
                )

                user_trades = {}
                for db_trade in db_trades:
                    trade_config = db_trade.to_trade_config()
                    user_trades[db_trade.trade_id] = trade_config

                # Update enhanced cache with fresh data
                enhanced_cache.set_user_trade_configs(user_id_str, user_trades)

                # Only log when debugging or significant cache operations
                debug_mode = os.environ.get("DEBUG") or os.environ.get("FLASK_DEBUG")
                if debug_mode or (force_reload and len(user_trades) > 0):
                    logging.info(
                        f"Loaded {len(user_trades)} trades for user {user_id} from database (cache {'refresh' if force_reload else 'miss'})"
                    )
                return user_trades

        except Exception as e:
            logging.warning(f"Database load attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                logging.error(
                    f"Failed to load trades for user {user_id} after {max_retries} attempts: {e}"
                )
                # Return cached data if available, even if stale
                cached_result = enhanced_cache.get_user_trade_configs(user_id_str)
                if cached_result:
                    trade_configs, _ = cached_result
                    logging.info(
                        f"Returning cached data for user {user_id} after DB failure"
                    )
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
                if not hasattr(db.engine, "table_names"):
                    db.create_all()

                # Check if trade already exists in database
                existing_trade = TradeConfiguration.query.filter_by(
                    telegram_user_id=str(user_id), trade_id=trade_config.trade_id
                ).first()

                if existing_trade:
                    # Update existing trade
                    db_trade = TradeConfiguration.from_trade_config(
                        user_id, trade_config
                    )
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
                    existing_trade.trailing_stop_enabled = (
                        db_trade.trailing_stop_enabled
                    )
                    existing_trade.trail_percentage = db_trade.trail_percentage
                    existing_trade.trail_activation_price = (
                        db_trade.trail_activation_price
                    )
                    existing_trade.status = db_trade.status
                    existing_trade.position_margin = db_trade.position_margin
                    existing_trade.unrealized_pnl = db_trade.unrealized_pnl
                    existing_trade.current_price = db_trade.current_price
                    existing_trade.position_size = db_trade.position_size
                    existing_trade.position_value = db_trade.position_value
                    existing_trade.realized_pnl = (
                        db_trade.realized_pnl
                    )  # CRITICAL FIX: Save realized P&L to database
                    existing_trade.final_pnl = db_trade.final_pnl
                    existing_trade.closed_at = db_trade.closed_at
                    existing_trade.updated_at = get_iran_time().replace(tzinfo=None)
                else:
                    # Create new trade
                    db_trade = TradeConfiguration.from_trade_config(
                        user_id, trade_config
                    )
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
                    logging.info(
                        f"Saved trade {trade_config.trade_id} to database for user {user_id}"
                    )
                return True

        except Exception as e:
            logging.warning(f"Database save attempt {attempt + 1} failed: {e}")
            try:
                db.session.rollback()
            except Exception as rollback_error:  # nosec B110
                logging.error(f"Failed to rollback database session: {rollback_error}")

            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logging.error(
                    f"Failed to save trade {trade_config.trade_id} after {max_retries} attempts: {e}"
                )
                return False

    return False


def delete_trade_from_db(user_id, trade_id):
    """Delete a trade configuration from the database"""
    try:
        with app.app_context():
            trade = TradeConfiguration.query.filter_by(
                telegram_user_id=str(user_id), trade_id=trade_id
            ).first()

            if trade:
                db.session.delete(trade)
                db.session.flush()
                db.session.commit()
                logging.info(
                    f"Deleted trade {trade_id} from database for user {user_id}"
                )
                return True
            return False
    except Exception as e:
        logging.error(f"Error deleting trade {trade_id} from database: {e}")
        try:
            db.session.rollback()
        except Exception as rollback_error:  # nosec B110
            logging.error(f"Failed to rollback database session: {rollback_error}")
        return False


@app.route("/auth", methods=["GET", "POST"])
def authenticate():
    """
    Handle authentication with clean URL redirect.
    This route processes Telegram WebApp initData and establishes a session,
    then redirects to a clean URL without sensitive parameters.
    """
    try:
        # Check if user is already authenticated via session
        existing_user = get_user_from_session()
        if existing_user:
            # Redirect to clean URL without any parameters
            return redirect(url_for('mini_app', _external=False))
        
        # Try to authenticate via Telegram WebApp data
        user_id = get_authenticated_user_id()
        if not user_id:
            return render_template(
                "access_wall.html",
                access_data={
                    "title": "🔒 Authentication Failed",
                    "message": "Could not verify your Telegram identity. Please access this app through the official Telegram bot.",
                    "status": "auth_failed",
                    "show_request_button": False
                },
                user_id=None
            )
        
        # Get user data for session establishment
        user_data = None
        try:
            init_data = None
            if request.method == 'GET':
                init_data = request.args.get('initData') or request.args.get('tg_init_data')
                if init_data and request.args.get('tg_init_data'):
                    init_data = urllib.parse.unquote(init_data)
            elif request.method == 'POST':
                init_data = request.form.get('tg_init_data')
            
            if init_data and BOT_TOKEN:
                
                verified_data = verify_telegram_webapp_data(init_data, BOT_TOKEN)
                user_data = verified_data.get('user', {}) if verified_data else {}
        except Exception as e:
            logging.error(f"Error extracting user data for session: {e}")
        
        # Establish session
        if establish_user_session(user_id, user_data or {}):
            logging.info(f"Authentication successful, session established for user {user_id}")
            # For AJAX requests, return JSON success
            if request.method == 'POST':
                return jsonify({"success": True, "message": "Authentication successful"})
            # For GET requests, redirect to clean URL without sensitive parameters
            return redirect(url_for('mini_app', _external=False))
        else:
            logging.error(f"Failed to establish session for user {user_id}")
            return render_template(
                "access_wall.html",
                access_data={
                    "title": "🔒 Session Error",
                    "message": "Authentication succeeded but session could not be established. Please try again.",
                    "status": "session_error",
                    "show_request_button": False
                },
                user_id=None
            )
            
    except Exception as e:
        logging.error(f"Authentication error: {e}")
        return render_template(
            "access_wall.html",
            access_data={
                "title": "🔒 Authentication Error",
                "message": "An error occurred during authentication. Please try again.",
                "status": "auth_error",
                "show_request_button": False
            },
            user_id=None
        )


@app.route("/logout")
def logout():
    """Clear user session and redirect to access wall."""
    clear_user_session()
    return render_template(
        "access_wall.html",
        access_data={
            "title": "👋 Logged Out",
            "message": "You have been logged out successfully. Please access this app through the official Telegram bot to log in again.",
            "status": "logged_out",
            "show_request_button": False
        },
        user_id=None
    )


@app.route("/")
def mini_app():
    """Telegram Mini App interface - Main route with session-based authentication"""
    # Check for Telegram WebApp initData in URL (new authentication)
    # Skip if this is already a processed auth request to avoid loops
    has_auth_data = request.args.get('tg_init_data') or request.args.get('initData')
    is_auth_processed = request.args.get('tg_auth_processed')
    
    if has_auth_data and not is_auth_processed:
        # Only pass the authentication data, not all URL parameters to avoid redirect loops
        auth_params = {}
        if request.args.get('tg_init_data'):
            auth_params['tg_init_data'] = request.args.get('tg_init_data')
        if request.args.get('initData'):
            auth_params['initData'] = request.args.get('initData')
        # Redirect to auth route for proper session establishment
        return redirect(url_for('authenticate', **auth_params))
    
    # Try to get user from existing session first
    user_id = get_user_from_session()
    
    # If no session, try one-time authentication verification
    if not user_id:
        user_id = get_authenticated_user_id()
        if user_id:
            # Authentication successful but no session - redirect to auth route
            auth_params = {}
            if request.args.get('tg_init_data'):
                auth_params['tg_init_data'] = request.args.get('tg_init_data')
            if request.args.get('initData'):
                auth_params['initData'] = request.args.get('initData')
            return redirect(url_for('authenticate', **auth_params))
    
    # If still no authentication, show access wall
    if not user_id:
        return render_template(
            "access_wall.html",
            access_data={
                "title": "🔒 Authentication Required",
                "message": "Please access this app through the official Telegram bot.",
                "status": "auth_required",
                "show_request_button": False
            },
            user_id=None
        )
    
    # Check if whitelist is enabled
    if not WHITELIST_ENABLED:
        record_user_access(user_id)
        return render_template(
            "mini_app.html",
            price_update_interval=TimeConfig.PRICE_UPDATE_INTERVAL,
            portfolio_refresh_interval=TimeConfig.PORTFOLIO_REFRESH_INTERVAL,
            csrf_token=generate_csrf_token()
        )
    
    # Check if user is whitelisted or is bot owner
    if not is_user_whitelisted(user_id):
        # Show access wall for non-whitelisted users
        access_wall_data = get_access_wall_message(user_id)
        return render_template(
            "access_wall.html",
            access_data=access_wall_data,
            user_id=user_id
        )
    
    # User is whitelisted - record access and show main app
    record_user_access(user_id)
    is_owner = is_bot_owner(user_id)
    
    return render_template(
        "mini_app.html",
        price_update_interval=TimeConfig.PRICE_UPDATE_INTERVAL,
        portfolio_refresh_interval=TimeConfig.PORTFOLIO_REFRESH_INTERVAL,
        is_bot_owner=is_owner,
        user_id=user_id,
        csrf_token=generate_csrf_token()
    )


@app.route("/miniapp")
def mini_app_alias():
    """Telegram Mini App interface - Alias route that redirects to main route"""
    # Simply redirect to the main route to avoid code duplication
    # Only pass specific authentication parameters
    auth_params = {}
    if request.args.get('tg_init_data'):
        auth_params['tg_init_data'] = request.args.get('tg_init_data')
    if request.args.get('initData'):
        auth_params['initData'] = request.args.get('initData')
    return redirect(url_for('mini_app', **auth_params))


@app.route("/health")
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "timestamp": get_iran_time().isoformat()})


@app.route("/api/db-status")
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

        return jsonify(
            {
                "database_type": db_type,
                "connection_status": connection_status,
                "trade_count": trade_count,
                "environment": {
                    "IS_RENDER": Environment.IS_RENDER,
                    "IS_VERCEL": Environment.IS_VERCEL,
                    "IS_REPLIT": Environment.IS_REPLIT,
                },
                "database_url_set": bool(os.environ.get("DATABASE_URL")),
                "timestamp": get_iran_time().isoformat(),
            }
        )

    except Exception as e:
        return jsonify({"error": str(e), "timestamp": get_iran_time().isoformat()}), 500


def _handle_real_trading_sync():
    """Handle real trading synchronization based on environment."""
    result = {"processed": 0, "status": "not_available"}

    try:
        if os.environ.get("VERCEL"):
            result = _handle_vercel_trading_sync()
        else:
            result = _handle_background_trading_sync()
    except Exception as e:
        logging.warning(f"Real trading sync failed: {e}")
        result["error"] = str(e)

    return result


def _handle_vercel_trading_sync():
    """Handle trading sync for Vercel serverless environment."""
    sync_service = get_vercel_sync_service()
    if not sync_service:
        return {"processed": 0, "status": "service_unavailable"}

    users_with_creds = UserCredentials.query.filter_by(is_active=True).all()
    synced_users = 0

    for user_creds in users_with_creds:
        user_id = user_creds.telegram_user_id
        active_trades = TradeConfiguration.query.filter_by(
            telegram_user_id=user_id, status="active"
        ).count()

        if active_trades > 0 and sync_service.should_sync_user(str(user_id)):
            try:
                result = sync_service.sync_user_on_request(str(user_id))
                if result.get("success"):
                    synced_users += 1
            except Exception as e:
                logging.warning(f"Health sync failed for user {user_id}: {e}")

    return {
        "processed": synced_users,
        "status": "active" if synced_users > 0 else "no_active_positions",
    }


def _handle_background_trading_sync():
    """Handle trading sync for regular background environments."""
    sync_service = get_sync_service()
    if not sync_service or not hasattr(sync_service, "_sync_all_users"):
        return {"processed": 0, "status": "service_unavailable"}

    try:
        logging.info(
            "HEALTH CHECK: Triggering background sync for all users with active positions"
        )
        sync_service._sync_all_users()
        logging.info("HEALTH CHECK: Background sync completed successfully")
        return {"processed": 1, "status": "triggered"}
    except Exception as e:
        logging.warning(f"Background sync trigger failed: {e}")
        return {"processed": 0, "status": "failed", "error": str(e)}


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
        "processed": paper_positions_processed,
        "status": "active" if paper_positions_processed > 0 else "no_active_positions",
    }


def _monitor_memory_paper_positions():
    """Monitor in-memory paper trading positions."""
    processed = 0

    for user_id, configs in user_trade_configs.items():
        for trade_id, config in configs.items():
            if (
                hasattr(config, "paper_trading_mode")
                and config.paper_trading_mode
                and config.status in ["active", "pending"]  # Monitor both active and pending limit orders
            ):

                try:
                    if config.symbol:
                        current_price = get_live_market_price(
                            config.symbol, use_cache=True, user_id=user_id
                        )
                        if current_price:
                            config.current_price = current_price
                            
                            # For pending limit orders, check if they should be executed
                            if config.status == "pending" and config.entry_type == "limit":
                                limit_executed = _process_pending_limit_orders(user_id, trade_id, config)
                                if limit_executed:
                                    processed += 1
                            
                            # For active positions, process TP/SL monitoring
                            if config.status == "active":
                                process_paper_trading_position(user_id, trade_id, config)
                                processed += 1
                            
                except Exception as e:
                    logging.warning(
                        f"Paper position processing failed for {config.symbol}: {e}"
                    )

    return processed


def _monitor_database_paper_positions():
    """Monitor database paper trading positions."""
    processed = 0

    db_active_trades = TradeConfiguration.query.filter_by(status="active").all()
    for trade in db_active_trades:
        user_id = trade.telegram_user_id

        # Check if user has no credentials or is in paper mode
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id, is_active=True
        ).first()

        # If no credentials or manual paper mode, treat as paper trading
        is_paper_mode = (
            not user_creds
            or not user_creds.has_credentials()
            or user_paper_trading_preferences.get(user_id, True)
        )

        if is_paper_mode and trade.symbol:
            try:
                current_price = get_live_market_price(
                    trade.symbol, use_cache=True, user_id=user_id
                )
                if current_price and current_price > 0:
                    processed += 1
                    logging.debug(
                        f"Paper monitoring for {trade.trade_id}: price ${current_price}"
                    )
            except Exception as e:
                logging.warning(
                    f"Paper trading monitoring failed for DB trade {trade.trade_id}: {e}"
                )

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
        "symbols_updated": price_updates,
        "status": "active" if price_updates > 0 else "no_active_symbols",
    }


def _collect_active_symbols():
    """Collect all symbols from active trades."""
    active_symbols = set()

    # Collect symbols from in-memory active trades
    for user_id, configs in user_trade_configs.items():
        for config in configs.values():
            if config.status == "active" and config.symbol:
                active_symbols.add(config.symbol)

    # Also check database for real trading positions
    active_db_trades = TradeConfiguration.query.filter_by(status="active").all()
    for trade in active_db_trades:
        if trade.symbol:
            active_symbols.add(trade.symbol)

    return active_symbols


def _calculate_monitoring_summary(monitoring_results):
    """Calculate final monitoring summary."""
    total_activity = (
        monitoring_results["real_trading_sync"]["processed"]
        + monitoring_results["paper_trading_monitoring"]["processed"]
        + monitoring_results["price_updates"]["symbols_updated"]
    )

    monitoring_results["overall_status"] = (
        "active" if total_activity > 0 else "monitoring_idle"
    )

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
        "all_positions_monitoring": {"processed": 0, "status": "inactive"},
        "real_trading_sync": {"processed": 0, "status": "not_available"},
        "paper_trading_monitoring": {"processed": 0, "status": "inactive"},
        "price_updates": {"symbols_updated": 0, "status": "inactive"},
        "timestamp": get_iran_time().isoformat(),
    }

    try:
        # ALL POSITIONS MONITORING: Monitor positions for ALL users regardless of credentials
        monitoring_results["all_positions_monitoring"] = _monitor_all_active_positions()

        # REAL TRADING MONITORING: Sync users with active positions
        monitoring_results["real_trading_sync"] = _handle_real_trading_sync()

        # PAPER TRADING MONITORING: Process all paper trading positions
        monitoring_results["paper_trading_monitoring"] = (
            _handle_paper_trading_monitoring()
        )

        # PRICE UPDATES: Update prices for active symbols
        monitoring_results["price_updates"] = _handle_price_updates_monitoring()

        # Calculate summary
        monitoring_results = _calculate_monitoring_summary(monitoring_results)

    except Exception as e:
        logging.error(f"Core monitoring trigger failed: {e}")
        monitoring_results["error"] = str(e)

    return monitoring_results


@app.route("/api/health")
def api_health_check():
    """Comprehensive health check endpoint for UptimeRobot and monitoring"""
    start_time = time.time()
    try:
        # RENDER LOG: Health check started
        print(f"[RENDER-HEALTH] Health check started at {get_iran_time().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"[RENDER-HEALTH] Health check started at {get_iran_time().strftime('%Y-%m-%d %H:%M:%S')}")
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
            if hasattr(circuit_manager, "get_unhealthy_services"):
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
            if sync_service and hasattr(sync_service, "trigger_health_ping_boost"):
                print(f"[RENDER-HEALTH] Activating Health Ping Boost for enhanced monitoring")
                logging.info("[RENDER-HEALTH] Activating Health Ping Boost for enhanced monitoring")
                sync_service.trigger_health_ping_boost()
                boost_status = "activated"
                print(f"[RENDER-HEALTH] Health Ping Boost activated - monitoring every 10s for 3 minutes")
                logging.info("[RENDER-HEALTH] Health Ping Boost activated - monitoring every 10s for 3 minutes")
            else:
                logging.warning(
                    "HEALTH CHECK: Sync service not available for Health Ping Boost"
                )
                boost_status = "service_unavailable"
        except Exception as e:
            logging.warning(f"Health ping boost activation failed: {e}")
            boost_status = f"failed: {str(e)}"

        # KLINES RESTART: Prevent gaps by also restarting unified data sync service
        klines_restart_status = "not_attempted"
        try:
            from .unified_data_sync_service import restart_unified_data_sync_service, get_unified_service_status
            print(f"[RENDER-KLINES] Starting klines service restart to prevent gaps")
            logging.info("[RENDER-KLINES] Starting klines service restart to prevent gaps")
            
            # Get status before restart
            pre_restart_status = get_unified_service_status()
            restart_success = restart_unified_data_sync_service(app)
            
            if restart_success:
                klines_restart_status = "restarted"
                post_restart_status = get_unified_service_status()
                print(f"[RENDER-KLINES] SUCCESS: Klines service restarted - service_running: {post_restart_status.get('service_running', 'unknown')}")
                logging.info(f"[RENDER-KLINES] SUCCESS: Klines service restarted - service_running: {post_restart_status.get('service_running', 'unknown')}")
            else:
                klines_restart_status = "restart_failed"
                print(f"[RENDER-KLINES] WARNING: Klines service restart returned False")
                logging.warning("[RENDER-KLINES] WARNING: Klines service restart returned False")
        except Exception as e:
            print(f"[RENDER-KLINES] ERROR: Klines restart failed - {str(e)}")
            logging.warning(f"[RENDER-KLINES] ERROR: Klines restart failed - {str(e)}")
            klines_restart_status = f"failed: {str(e)}"

        # Monitor system load (basic check)
        active_configs = sum(len(configs) for configs in user_trade_configs.values())

        health_data = {
            "status": "healthy" if db_status == "healthy" else "degraded",
            "timestamp": get_iran_time().isoformat(),
            "api_version": "1.0",
            "database": db_status,
            "services": {
                "cache": cache_status,
                "monitoring": "running",
                "circuit_breakers": cb_status,
            },
            "metrics": {
                "active_trade_configs": active_configs,
                "uptime_seconds": int(
                    time.time() - app.config.get("START_TIME", time.time())
                ),
            },
            "environment": {
                "render": Environment.IS_RENDER,
                "vercel": Environment.IS_VERCEL,
                "replit": Environment.IS_REPLIT,
            },
            "monitoring": monitoring_results,
            "health_ping_boost": {
                "status": boost_status,
                "duration_seconds": 180,
                "enhanced_interval_seconds": 10,
            },
            "klines_restart": {
                "status": klines_restart_status,
                "purpose": "prevent_gaps_when_offline",
            },
        }

        # RENDER LOG: Health check completed
        execution_time = time.time() - start_time
        print(f"[RENDER-HEALTH] Health check completed in {execution_time:.2f}s - Status: {health_data['status']}, Boost: {boost_status}, Klines: {klines_restart_status}")
        logging.info(f"[RENDER-HEALTH] Health check completed in {execution_time:.2f}s - Status: {health_data['status']}, Boost: {boost_status}, Klines: {klines_restart_status}")

        # Return appropriate HTTP status
        status_code = 200 if health_data["status"] == "healthy" else 503
        return jsonify(health_data), status_code

    except Exception as e:
        return (
            jsonify(
                {
                    "status": "unhealthy",
                    "error": str(e),
                    "timestamp": get_iran_time().isoformat(),
                }
            ),
            503,
        )


def _validate_trade_for_alerts(trade):
    """Validate trade data for trigger alert checking."""
    return trade.entry_price and trade.entry_price > 0


def _check_stop_loss_trigger(trade, current_price):
    """Check if stop loss should trigger for the trade using consistent P&L-based calculation."""
    if trade.stop_loss_percent <= 0:
        return
    
    # Calculate unrealized P&L
    if trade.side == "long":
        price_change = (current_price - trade.entry_price) / trade.entry_price
    else:  # short
        price_change = (trade.entry_price - current_price) / trade.entry_price
    
    position_value = trade.amount * trade.leverage
    unrealized_pnl = position_value * price_change
    
    # Check if loss threshold reached
    if unrealized_pnl < 0:
        loss_percentage = abs(unrealized_pnl / position_value) * 100
        if loss_percentage >= trade.stop_loss_percent:
            logging.info(
                f"MONITORING ALERT: Stop loss trigger detected for {trade.trade_id} - Loss: {loss_percentage:.2f}% >= {trade.stop_loss_percent}%"
            )


def _parse_take_profits(trade):
    """Parse take profit data from trade configuration."""
    if not trade.take_profits:
        return []

    import json

    try:
        tps = (
            json.loads(trade.take_profits)
            if isinstance(trade.take_profits, str)
            else trade.take_profits
        )
        return tps if isinstance(tps, list) else []
    except:
        return []  # Skip if TP format is invalid


def _calculate_tp_price(trade, tp_percentage):
    """Calculate take profit price based on trade side and percentage."""
    if trade.side == "long":
        return trade.entry_price * (1 + tp_percentage / 100)
    else:  # short
        return trade.entry_price * (1 - tp_percentage / 100)


def _check_tp_trigger(trade, current_price, tp_price, tp_index):
    """Check if a specific take profit should trigger."""
    trigger_condition = (trade.side == "long" and current_price >= tp_price) or (
        trade.side == "short" and current_price <= tp_price
    )

    if trigger_condition:
        logging.info(
            f"MONITORING ALERT: TP{tp_index+1} trigger detected for {trade.trade_id} at {current_price}"
        )


def _check_take_profit_triggers(trade, current_price):
    """Check all take profit triggers for the trade."""
    tps = _parse_take_profits(trade)

    for i, tp in enumerate(tps):
        tp_percentage = tp.get("percentage", 0)
        tp_price = _calculate_tp_price(trade, tp_percentage)
        _check_tp_trigger(trade, current_price, tp_price, i)


def _calculate_trade_profit_percentage(trade, current_price):
    """Calculate current profit percentage for the trade (monitoring)."""
    if trade.side == "long":
        return ((current_price - trade.entry_price) / trade.entry_price) * 100
    else:  # short
        return ((trade.entry_price - current_price) / trade.entry_price) * 100


def _check_breakeven_trigger(trade, current_price):
    """Check if break-even should trigger for the trade."""
    if not (trade.breakeven_after > 0 and not trade.breakeven_sl_triggered):
        return

    profit_percent = _calculate_trade_profit_percentage(trade, current_price)

    if profit_percent >= trade.breakeven_after:
        logging.info(
            f"MONITORING ALERT: Break-even trigger detected for {trade.trade_id} - profit: {profit_percent:.2f}%"
        )


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
        logging.warning(
            f"Position trigger alert check failed for {trade.trade_id}: {e}"
        )


# Exchange Synchronization Endpoints
@app.route("/api/exchange/sync-status")
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
        return jsonify({"error": "Exchange sync service not available"}), 503


@app.route("/api/exchange/force-sync", methods=["POST"])
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
            return jsonify({"error": "Vercel sync service not available"}), 503
    else:
        sync_service = get_sync_service()
        if sync_service:
            success = sync_service.force_sync_user(user_id)
            if success:
                return jsonify(
                    {"success": True, "message": "Synchronization completed"}
                )
            else:
                return (
                    jsonify({"success": False, "message": "Synchronization failed"}),
                    500,
                )
        else:
            return jsonify({"error": "Exchange sync service not available"}), 503


@app.route("/api/exchange/test-connection", methods=["POST"])
def test_exchange_connection():
    """Test connection to Toobit exchange"""
    user_id = get_user_id_from_request()

    try:
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id, is_active=True
        ).first()

        if not user_creds or not user_creds.has_credentials():
            return (
                jsonify({"success": False, "message": "No API credentials found"}),
                400,
            )

        # Create client and test connection - Dynamic exchange selection
        client = create_exchange_client(user_creds, testnet=False)

        is_connected = client.test_connectivity()
        message = "Connected successfully" if is_connected else "Connection failed"

        return jsonify(
            {
                "success": is_connected,
                "message": message,
                "testnet": user_creds.testnet_mode,
            }
        )

    except Exception as e:
        logging.error(f"Error testing exchange connection: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/v1/futures/leverage", methods=["POST"])
def set_futures_leverage():
    """Set leverage for futures trading on a specific symbol"""
    try:
        data = request.get_json()
        symbol = data.get("symbol", "").upper()
        leverage = data.get("leverage")
        user_id = get_user_id_from_request()

        # Validation
        if not symbol:
            return jsonify({"success": False, "message": "Symbol is required"}), 400

        if not leverage:
            return jsonify({"success": False, "message": "Leverage is required"}), 400

        try:
            leverage = int(leverage)
            if leverage < 1 or leverage > 125:  # Toobit typical range
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": "Leverage must be between 1 and 125",
                        }
                    ),
                    400,
                )
        except ValueError:
            return jsonify({"success": False, "message": "Invalid leverage value"}), 400

        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id, is_active=True
        ).first()

        if not user_creds or not user_creds.has_credentials():
            return (
                jsonify({"success": False, "message": "No API credentials found"}),
                400,
            )

        # Check if in paper trading mode
        chat_id = int(user_id)
        is_paper_mode = determine_trading_mode(chat_id)

        if is_paper_mode:
            # In paper mode, just store the preference and return success
            logging.info(
                f"Paper mode: Set leverage for {symbol} to {leverage}x for user {chat_id}"
            )
            return jsonify(
                {
                    "success": True,
                    "message": f"Leverage set to {leverage}x for {symbol} (paper trading mode)",
                    "symbol": symbol,
                    "leverage": leverage,
                    "paper_trading_mode": True,
                }
            )

        # Live trading mode - make actual API call with dynamic exchange
        client = create_exchange_client(user_creds, testnet=False)

        result = client.change_leverage(symbol, leverage)

        if result:
            logging.info(
                f"Successfully set leverage for {symbol} to {leverage}x for user {chat_id}"
            )
            return jsonify(
                {
                    "success": True,
                    "message": f"Leverage set to {leverage}x for {symbol}",
                    "symbol": symbol,
                    "leverage": leverage,
                    "paper_trading_mode": False,
                    "exchange_response": result,
                }
            )
        else:
            error_msg = client.get_last_error() or "Failed to set leverage"
            return jsonify({"success": False, "message": error_msg}), 500

    except Exception as e:
        logging.error(f"Error setting leverage: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/exchange/balance")
def get_exchange_balance():
    """Get account balance - returns paper balance in paper mode, live balance in live mode"""
    user_id = get_user_id_from_request()

    try:
        chat_id = int(user_id)

        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id, is_active=True
        ).first()

        # Use centralized trading mode detection
        is_paper_mode = determine_trading_mode(chat_id)

        # If in paper trading mode, return virtual paper balance
        if is_paper_mode:
            paper_balance = user_paper_balances.get(
                chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE
            )
            return jsonify(
                {
                    "success": True,
                    "paper_trading_mode": True,
                    "testnet_mode": False,
                    "balance": {
                        "total_balance": paper_balance,
                        "available_balance": paper_balance,
                        "used_margin": 0.0,
                        "position_margin": 0.0,
                        "order_margin": 0.0,
                        "unrealized_pnl": 0.0,
                        "margin_ratio": 0.0,
                        "asset": "USDT",
                    },
                    "message": "Paper trading balance (virtual funds)",
                    "timestamp": get_iran_time().isoformat(),
                }
            )

        # Live trading mode - make actual API calls
        if not user_creds or not user_creds.has_credentials():
            return (
                jsonify(
                    {
                        "error": "No API credentials found for live trading",
                        "testnet_mode": False,
                    }
                ),
                400,
            )

        # Create client and get comprehensive balance - Dynamic exchange selection
        try:
            client = create_exchange_client(user_creds, testnet=False)
            if not client:
                return (
                    jsonify(
                        {
                            "error": "Failed to create exchange client",
                            "testnet_mode": False,
                        }
                    ),
                    500,
                )

            # Get perpetual futures balance
            balance_data = client.get_futures_balance()
            logging.info(
                f"Perpetual futures balance fetched: {len(balance_data) if balance_data else 0} assets"
            )

        except Exception as client_error:
            logging.error(f"Error creating client or getting balance: {client_error}")
            return (
                jsonify(
                    {
                        "error": f"Exchange connection failed: {str(client_error)}",
                        "testnet_mode": False,
                    }
                ),
                500,
            )

        if balance_data and isinstance(balance_data, list) and len(balance_data) > 0:
            # Extract USDT balance info from Toobit response
            usdt_balance = balance_data[0]  # Toobit returns array with USDT info

            total_balance = float(usdt_balance.get("balance", "0"))
            available_balance = float(usdt_balance.get("availableBalance", "0"))
            position_margin = float(usdt_balance.get("positionMargin", "0"))
            order_margin = float(usdt_balance.get("orderMargin", "0"))
            unrealized_pnl = float(usdt_balance.get("crossUnRealizedPnl", "0"))

            # Calculate used margin and margin ratio
            used_margin = position_margin + order_margin
            margin_ratio = (
                (used_margin / total_balance * 100) if total_balance > 0 else 0
            )

            # Determine balance type for user information
            balance_type = "perpetual_futures"
            balance_source = (
                f"{user_creds.exchange_name.upper()} Perpetual Futures"
                if user_creds.exchange_name
                else "Perpetual Futures"
            )

            return jsonify(
                {
                    "success": True,
                    "testnet_mode": user_creds.testnet_mode,
                    "balance_type": balance_type,
                    "balance_source": balance_source,
                    "balance": {
                        "total_balance": total_balance,
                        "available_balance": available_balance,
                        "used_margin": used_margin,
                        "position_margin": position_margin,
                        "order_margin": order_margin,
                        "unrealized_pnl": unrealized_pnl,
                        "margin_ratio": round(margin_ratio, 2),
                        "asset": usdt_balance.get("asset", "USDT"),
                    },
                    "balance_summary": {
                        "futures_assets": len(balance_data) if balance_data else 0,
                        "primary_balance": balance_type,
                    },
                    "raw_data": balance_data,
                    "timestamp": get_iran_time().isoformat(),
                }
            )
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No balance data received from exchange",
                        "testnet_mode": user_creds.testnet_mode,
                    }
                ),
                500,
            )

    except Exception as e:
        logging.error(f"Error getting exchange balance: {e}")
        return jsonify({"success": False, "error": str(e), "testnet_mode": False}), 500


@app.route("/api/exchange/api-restrictions")
def get_api_restrictions():
    """Get API restrictions for the current user's exchange credentials"""
    user_id = get_user_id_from_request()

    try:
        chat_id = int(user_id)

        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id, is_active=True
        ).first()

        if not user_creds or not user_creds.has_credentials():
            return jsonify({"error": "No API credentials found", "success": False}), 400

        # Create client and get API restrictions - Dynamic exchange selection
        try:
            client = create_exchange_client(user_creds, testnet=False)
            if not client:
                return (
                    jsonify(
                        {"error": "Failed to create exchange client", "success": False}
                    ),
                    500,
                )

            # Check if the client has the get_api_restrictions method
            if hasattr(client, "get_api_restrictions"):
                restrictions_data = client.get_api_restrictions()
            else:
                return (
                    jsonify(
                        {
                            "error": "API restrictions not supported for this exchange",
                            "success": False,
                        }
                    ),
                    400,
                )

        except Exception as client_error:
            logging.error(
                f"Error creating client or getting API restrictions: {client_error}"
            )
            return (
                jsonify(
                    {
                        "error": f"Exchange connection failed: {str(client_error)}",
                        "success": False,
                    }
                ),
                500,
            )

        if restrictions_data:
            return jsonify(
                {
                    "success": True,
                    "restrictions": restrictions_data,
                    "exchange": user_creds.exchange_name,
                    "timestamp": get_iran_time().isoformat(),
                }
            )
        else:
            error_msg = (
                client.get_last_error()
                if hasattr(client, "get_last_error")
                else "No API restrictions data received"
            )
            return jsonify({"error": error_msg, "success": False}), 500

    except Exception as e:
        logging.error(f"API restrictions error: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route("/api/exchange/positions")
def get_exchange_positions():
    """Get positions directly from Toobit exchange"""
    user_id = get_user_id_from_request()

    try:
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id, is_active=True
        ).first()

        if not user_creds or not user_creds.has_credentials():
            return jsonify({"error": "No API credentials found"}), 400

        # Create client and get positions - Dynamic exchange selection
        client = create_exchange_client(user_creds, testnet=False)

        positions = client.get_positions()

        return jsonify(
            {
                "success": True,
                "positions": positions,
                "timestamp": get_iran_time().isoformat(),
            }
        )

    except Exception as e:
        logging.error(f"Error getting exchange positions: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/exchange/orders")
def get_exchange_orders():
    """Get orders directly from Toobit exchange"""
    user_id = get_user_id_from_request()
    symbol = request.args.get("symbol")
    status = request.args.get("status")

    try:
        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id, is_active=True
        ).first()

        if not user_creds or not user_creds.has_credentials():
            return jsonify({"error": "No API credentials found"}), 400

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

        return jsonify(
            {
                "success": True,
                "orders": orders,
                "timestamp": get_iran_time().isoformat(),
            }
        )

    except Exception as e:
        logging.error(f"Error getting exchange orders: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/webhook/toobit", methods=["POST"])
def toobit_webhook():
    """Handle Toobit exchange webhooks for real-time updates"""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Verify webhook signature if configured
        webhook_secret = os.environ.get("TOOBIT_WEBHOOK_SECRET")
        if webhook_secret:
            signature = request.headers.get("X-Toobit-Signature")
            if not signature:
                return jsonify({"error": "Missing signature"}), 401

            # Verify signature (implementation depends on Toobit's webhook format)
            # This is a placeholder - adjust based on actual Toobit webhook specification
            expected_signature = hmac.new(
                webhook_secret.encode(), request.data, hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_signature):
                return jsonify({"error": "Invalid signature"}), 401

        # Process webhook data
        event_type = data.get("eventType")
        user_id = data.get("userId")

        if event_type and user_id:
            # Process different webhook events
            if event_type == "ORDER_UPDATE":
                handle_order_update_webhook(data)
            elif event_type == "POSITION_UPDATE":
                handle_position_update_webhook(data)
            elif event_type == "BALANCE_UPDATE":
                handle_balance_update_webhook(data)

            logging.info(f"Processed Toobit webhook: {event_type} for user {user_id}")

        return jsonify({"success": True})

    except Exception as e:
        logging.error(f"Error processing Toobit webhook: {e}")
        return jsonify({"error": str(e)}), 500


def handle_order_update_webhook(data):
    """Handle order update webhook from Toobit"""
    try:
        user_id = data.get("userId")
        order_data = data.get("orderData", {})

        # Find corresponding local trade
        symbol = order_data.get("symbol")
        order_status = order_data.get("status")

        if order_status == "filled":
            # Update local trade records
            trade = TradeConfiguration.query.filter_by(
                telegram_user_id=str(user_id), symbol=symbol, status="active"
            ).first()

            if trade:
                # Calculate final P&L and update trade
                fill_price = float(order_data.get("avgPrice", 0))
                fill_quantity = float(order_data.get("executedQty", 0))

                if trade.side == "long":
                    final_pnl = (fill_price - trade.entry_price) * fill_quantity
                else:
                    final_pnl = (trade.entry_price - fill_price) * fill_quantity

                trade.status = "stopped"
                trade.final_pnl = final_pnl
                trade.closed_at = get_iran_time().replace(tzinfo=None)

                db.session.commit()
                logging.info(f"Updated trade {trade.trade_id} from webhook")

    except Exception as e:
        logging.error(f"Error handling order update webhook: {e}")


def handle_position_update_webhook(data):
    """Handle position update webhook from Toobit"""
    try:
        user_id = data.get("userId")
        position_data = data.get("positionData", {})

        # Update local trade records with real-time position data
        symbol = position_data.get("symbol")
        unrealized_pnl = float(position_data.get("unrealizedPnl", 0))
        mark_price = float(position_data.get("markPrice", 0))

        trades = TradeConfiguration.query.filter_by(
            telegram_user_id=str(user_id), symbol=symbol, status="active"
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
        user_id = data.get("userId")
        balance_data = data.get("balanceData", {})

        # Update user session with new balance information
        session = UserTradingSession.query.filter_by(
            telegram_user_id=str(user_id)
        ).first()

        if session:
            new_balance = float(balance_data.get("balance", session.account_balance))
            session.account_balance = new_balance
            db.session.commit()

    except Exception as e:
        logging.error(f"Error handling balance update webhook: {e}")


@app.route("/api/toggle-paper-trading", methods=["POST"])
def toggle_paper_trading():
    """Toggle paper trading mode for a user"""
    user_id = None
    try:
        data = request.get_json()
        user_id = data.get("user_id")

        logging.info(f"Toggle paper trading request received for user: {user_id}")

        if not user_id:
            logging.error("Toggle paper trading failed: No user ID provided")
            return jsonify({"success": False, "message": "User ID required"}), 400

        try:
            chat_id = int(user_id)
        except ValueError:
            logging.error(
                f"Toggle paper trading failed: Invalid user ID format: {user_id}"
            )
            return jsonify({"success": False, "message": "Invalid user ID format"}), 400

        # RENDER OPTIMIZATION: Fast in-memory mode switching
        current_paper_mode = user_paper_trading_preferences.get(
            chat_id, True
        )  # Default to paper trading
        new_paper_mode = not current_paper_mode

        # Update preference immediately in memory with locking
        with user_preferences_lock:
            user_paper_trading_preferences[chat_id] = new_paper_mode

        # Initialize/ensure paper balance exists (optimize for both modes)
        with paper_balances_lock:
            if chat_id not in user_paper_balances or user_paper_balances[chat_id] <= 0:
                user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
                logging.info(
                    f"[RENDER OPTIMIZATION] Set paper balance for user {chat_id}: ${TradingConfig.DEFAULT_TRIAL_BALANCE:,.2f}"
                )

        # RENDER OPTIMIZATION: Clear enhanced cache instead of globals
        enhanced_cache.invalidate_user_data(str(chat_id))

        # Clear credential cache to force refresh on next check
        if chat_id in user_credentials_cache:
            del user_credentials_cache[chat_id]

        # Log the mode change
        mode_text = "ENABLED" if new_paper_mode else "DISABLED"
        logging.info(f"🔄 Paper Trading {mode_text} for user {chat_id}")
        logging.info(
            f"📊 Current paper balance: ${user_paper_balances.get(chat_id, 0):,.2f}"
        )

        response_data = {
            "success": True,
            "paper_trading_active": new_paper_mode,
            "paper_balance": (
                user_paper_balances.get(chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE)
                if new_paper_mode
                else None
            ),
            "message": f'Paper trading {"enabled" if new_paper_mode else "disabled"}',
        }

        logging.info(f"Toggle paper trading successful: {response_data}")
        return jsonify(response_data)

    except Exception as e:
        logging.error(
            f"Error toggling paper trading for user {user_id or 'unknown'}: {str(e)}",
            exc_info=True,
        )
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@app.route("/api/paper-trading-status")
def get_paper_trading_status():
    """Get current paper trading status for a user"""
    try:
        user_id = request.args.get("user_id")

        if not user_id:
            return jsonify({"success": False, "message": "User ID required"}), 400

        try:
            chat_id = int(user_id)
        except ValueError:
            return jsonify({"success": False, "message": "Invalid user ID format"}), 400

        # Get user credentials (optional for paper trading)
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(user_id), is_active=True
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
            "success": True,
            "paper_trading_active": is_paper_mode,
            "manual_paper_mode": manual_paper_mode,
            "mode_reason": mode_reason,
            "paper_balance": (
                user_paper_balances.get(chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE)
                if is_paper_mode
                else None
            ),
            "testnet_mode": user_creds.testnet_mode if user_creds else False,
            "has_credentials": user_creds.has_credentials() if user_creds else False,
            "can_toggle_manual": user_creds
            and user_creds.has_credentials()
            and not user_creds.testnet_mode,
            "message": f'Paper trading {"active" if is_paper_mode else "inactive"}',
        }

        logging.info(f"Paper trading status for user {chat_id}: {response_data}")
        return jsonify(response_data)

    except Exception as e:
        logging.error(f"Error getting paper trading status: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/status")
def get_system_status():
    """Get system status with API performance metrics (bot commands removed)"""
    current_time = get_iran_time()
    
    # Create system status response
    system_status = {
        "status": "active",
        "timestamp": current_time.isoformat(),
        "service": "telegram_mini_app",
        "api_performance": {},
        "cache_stats": {}
    }

    # Add API performance metrics
    for api_name, metrics in api_performance_metrics.items():
        if metrics["requests"] is not None and metrics["requests"] > 0:
            success_rate = (metrics["successes"] / metrics["requests"]) * 100
            avg_response_time = metrics.get("avg_response_time", 0)
            last_success = metrics.get("last_success")
            system_status["api_performance"][api_name] = {
                "success_rate": round(success_rate, 2),
                "avg_response_time": round(avg_response_time, 3),
                "total_requests": metrics["requests"],
                "last_success": last_success.isoformat() if last_success else None,
            }
        else:
            system_status["api_performance"][api_name] = {
                "success_rate": 0,
                "avg_response_time": 0,
                "total_requests": 0,
                "last_success": None,
            }

    # Add enhanced cache statistics
    cache_stats = enhanced_cache.get_cache_stats()
    system_status["cache_stats"] = cache_stats

    return jsonify(system_status)


@app.route("/api/cache/stats")
def cache_statistics():
    """Get comprehensive cache statistics and performance metrics"""
    return jsonify(enhanced_cache.get_cache_stats())


@app.route("/api/cache/invalidate", methods=["POST"])
def invalidate_cache():
    """Invalidate cache entries based on parameters"""
    try:
        data = request.get_json() or {}
        cache_type = data.get("type", "all")  # 'price', 'user', or 'all'
        symbol = data.get("symbol")
        user_id = data.get("user_id")

        if cache_type == "price":
            enhanced_cache.invalidate_price(symbol)
        elif cache_type == "user":
            enhanced_cache.invalidate_user_data(user_id)
        else:
            enhanced_cache.invalidate_price()
            enhanced_cache.invalidate_user_data()

        return jsonify(
            {"success": True, "message": f"Cache invalidated for type: {cache_type}"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/circuit-breakers/stats")
def circuit_breaker_stats():
    """Get statistics for all circuit breakers"""
    return jsonify(circuit_manager.get_all_stats())


@app.route("/api/circuit-breakers/reset", methods=["POST"])
def reset_circuit_breakers():
    """Reset circuit breakers (all or specific service)"""
    try:
        data = request.get_json() or {}
        service = data.get("service")

        if service:
            breaker = circuit_manager.get_breaker(service)
            breaker.reset()
            return jsonify(
                {"success": True, "message": f"Circuit breaker for {service} reset"}
            )
        else:
            circuit_manager.reset_all()
            return jsonify({"success": True, "message": "All circuit breakers reset"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/circuit-breakers/health")
def circuit_breaker_health():
    """Get health status of all services"""
    healthy = circuit_manager.get_healthy_services()
    unhealthy = circuit_manager.get_unhealthy_services()

    return jsonify(
        {
            "healthy_services": healthy,
            "unhealthy_services": unhealthy,
            "total_services": len(healthy) + len(unhealthy),
            "health_percentage": (len(healthy) / max(1, len(healthy) + len(unhealthy)))
            * 100,
        }
    )


@app.route("/api/klines-worker/status")
def klines_worker_status():
    """Get klines background worker status and statistics (unified service)"""
    try:
        from .unified_data_sync_service import get_unified_service_status
        status = get_unified_service_status()
        
        # Transform to match expected klines worker format
        klines_status = {
            "service_running": status.get("service_running", False),
            "klines_tracking": status.get("klines_tracking", {}),
            "cache_statistics": status.get("cache_statistics", {}),
            "circuit_breaker_status": status.get("circuit_breaker_status", {}),
            "last_cache_cleanup": status.get("last_cache_cleanup", "never"),
            "status": "running" if status.get("service_running", False) else "stopped"
        }
        
        return jsonify(klines_status)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "status": "error"
        }), 500


@app.route("/api/admin/klines-debug")
def admin_klines_debug():
    """Get comprehensive klines debugging information for Admin panel"""
    try:
        from .models import KlinesCache, db
        from datetime import datetime, timedelta
        from config import RollingWindowConfig, TradingConfig
        from sqlalchemy import func, and_, desc
        
        current_time = datetime.utcnow()
        
        # Get all unique symbol/timeframe combinations with statistics
        symbol_stats = []
        combinations = db.session.query(
            KlinesCache.symbol,
            KlinesCache.timeframe,
            func.count(KlinesCache.id).label('total_candles'),
            func.min(KlinesCache.timestamp).label('oldest_candle'),
            func.max(KlinesCache.timestamp).label('newest_candle'),
            func.count(KlinesCache.id).filter(KlinesCache.is_complete == True).label('complete_candles'),
            func.count(KlinesCache.id).filter(KlinesCache.is_complete == False).label('incomplete_candles'),
            func.count(KlinesCache.id).filter(KlinesCache.expires_at <= current_time + timedelta(hours=24)).label('expiring_soon')
        ).group_by(KlinesCache.symbol, KlinesCache.timeframe).all()
        
        for combo in combinations:
            symbol = combo.symbol
            timeframe = combo.timeframe
            
            # Get cleanup thresholds from config
            target_candles = RollingWindowConfig.get_target_candles(timeframe)
            cleanup_threshold = RollingWindowConfig.get_cleanup_threshold(timeframe)
            max_candles = RollingWindowConfig.get_max_candles(timeframe)
            
            # Gap detection - check for missing timestamps in sequence
            gaps = []
            if combo.total_candles > 0:
                # Get all timestamps for this symbol/timeframe
                timestamps_query = db.session.query(
                    KlinesCache.timestamp
                ).filter(
                    KlinesCache.symbol == symbol,
                    KlinesCache.timeframe == timeframe
                ).order_by(KlinesCache.timestamp).all()
                
                timestamps = [t.timestamp for t in timestamps_query]
                
                # Check for gaps based on timeframe
                if timeframe == "1h":
                    expected_delta = timedelta(hours=1)
                elif timeframe == "4h":
                    expected_delta = timedelta(hours=4)
                elif timeframe == "1d":
                    expected_delta = timedelta(days=1)
                else:
                    expected_delta = timedelta(hours=1)  # default
                
                # Find gaps (only check recent data to avoid too many historical gaps)
                recent_timestamps = timestamps[-50:] if len(timestamps) > 50 else timestamps
                for i in range(1, len(recent_timestamps)):
                    actual_delta = recent_timestamps[i] - recent_timestamps[i-1]
                    if actual_delta > expected_delta * 1.5:  # Allow some tolerance
                        gaps.append({
                            "start": recent_timestamps[i-1].isoformat(),
                            "end": recent_timestamps[i].isoformat(),
                            "missing_periods": int(actual_delta.total_seconds() / expected_delta.total_seconds()) - 1
                        })
            
            # Calculate cleanup status
            cleanup_status = "no_cleanup_needed"
            if combo.total_candles > cleanup_threshold:
                cleanup_status = "cleanup_eligible"
            elif combo.total_candles > max_candles:
                cleanup_status = "cleanup_recommended"
            
            symbol_stats.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "total_candles": combo.total_candles,
                "complete_candles": combo.complete_candles,
                "incomplete_candles": combo.incomplete_candles,
                "oldest_candle": combo.oldest_candle.isoformat() if combo.oldest_candle else None,
                "newest_candle": combo.newest_candle.isoformat() if combo.newest_candle else None,
                "expiring_soon": combo.expiring_soon,
                "gaps": gaps[:5],  # Limit to 5 most recent gaps
                "gap_count": len(gaps),
                "config": {
                    "target_candles": target_candles,
                    "cleanup_threshold": cleanup_threshold,
                    "max_candles": max_candles
                },
                "cleanup_status": cleanup_status
            })
        
        # Get recent candles expiring soon across all symbols
        expiring_soon = db.session.query(
            KlinesCache.symbol,
            KlinesCache.timeframe,
            KlinesCache.timestamp,
            KlinesCache.expires_at,
            KlinesCache.is_complete
        ).filter(
            KlinesCache.expires_at <= current_time + timedelta(hours=24)
        ).order_by(KlinesCache.expires_at).limit(20).all()
        
        expiring_candles = []
        for candle in expiring_soon:
            time_to_expire = (candle.expires_at - current_time).total_seconds() / 3600  # hours
            expiring_candles.append({
                "symbol": candle.symbol,
                "timeframe": candle.timeframe,
                "timestamp": candle.timestamp.isoformat(),
                "expires_at": candle.expires_at.isoformat(),
                "hours_until_expiry": round(time_to_expire, 2),
                "is_complete": candle.is_complete
            })
        
        # Get overall statistics
        total_candles = db.session.query(func.count(KlinesCache.id)).scalar()
        total_complete = db.session.query(func.count(KlinesCache.id)).filter(KlinesCache.is_complete == True).scalar()
        total_incomplete = db.session.query(func.count(KlinesCache.id)).filter(KlinesCache.is_complete == False).scalar()
        total_expired = db.session.query(func.count(KlinesCache.id)).filter(KlinesCache.expires_at <= current_time).scalar()
        
        # Get oldest and newest candles globally
        oldest_global = db.session.query(func.min(KlinesCache.timestamp)).scalar()
        newest_global = db.session.query(func.max(KlinesCache.timestamp)).scalar()
        
        return jsonify({
            "status": "success",
            "timestamp": current_time.isoformat(),
            "summary": {
                "total_candles": total_candles,
                "total_complete": total_complete,
                "total_incomplete": total_incomplete,
                "total_expired": total_expired,
                "unique_combinations": len(symbol_stats),
                "oldest_candle": oldest_global.isoformat() if oldest_global else None,
                "newest_candle": newest_global.isoformat() if newest_global else None
            },
            "symbol_statistics": symbol_stats,
            "expiring_candles": expiring_candles,
            "supported_symbols": TradingConfig.SUPPORTED_SYMBOLS,
            "timeframes": ["1h", "4h", "1d"]
        })
        
    except Exception as e:
        logging.error(f"Error in admin klines debug: {e}")
        error_time = get_iran_time()
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": error_time.isoformat()
        }), 500


@app.route("/api/price/<symbol>")
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
                price_source = "unknown"
                cache_info = {"cached": False}

        return jsonify(
            {
                "symbol": symbol,
                "price": price,
                "price_source": price_source,
                "timestamp": get_iran_time().isoformat(),
                "cache_info": cache_info,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prices", methods=["POST"])
def get_multiple_prices():
    """Get live prices for multiple symbols efficiently"""
    try:
        data = request.get_json()
        symbols = data.get("symbols", [])

        if not symbols or not isinstance(symbols, list):
            return jsonify({"error": "Symbols array required"}), 400

        # Limit to prevent abuse
        if len(symbols) > TradingConfig.MAX_SYMBOLS_BATCH:
            return (
                jsonify(
                    {
                        "error": f"Maximum {TradingConfig.MAX_SYMBOLS_BATCH} symbols allowed"
                    }
                ),
                400,
            )

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
                results[symbol] = {"price": price, "status": "success"}
            except Exception as e:
                results[symbol] = {"price": None, "status": "error", "error": str(e)}

        return jsonify(
            {
                "results": results,
                "timestamp": get_iran_time().isoformat(),
                "total_symbols": len(symbols),
                "successful": len(
                    [r for r in results.values() if r["status"] == "success"]
                ),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/smc-analysis/<symbol>")
def get_smc_analysis(symbol):
    """Get Smart Money Concepts analysis for a specific symbol with database caching"""
    try:
        from .models import SMCSignalCache, db
        from .smc_analyzer import SMCAnalyzer

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
            return jsonify(
                {
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit_levels": signal.take_profit_levels,
                    "confidence": signal.confidence,
                    "reasoning": signal.reasoning,
                    "signal_strength": signal.signal_strength.value,
                    "risk_reward_ratio": signal.risk_reward_ratio,
                    "timestamp": signal.timestamp.isoformat(),
                    "status": "cached_signal",
                    "cache_source": True,
                }
            )

        # No valid cached signal, generate new one
        analyzer = SMCAnalyzer()
        signal = analyzer.generate_trade_signal(symbol)

        if signal:
            # Cache the new signal with dynamic duration based on signal strength
            cache_entry = SMCSignalCache.from_smc_signal(signal)
            db.session.add(cache_entry)
            db.session.commit()

            return jsonify(
                {
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit_levels": signal.take_profit_levels,
                    "confidence": signal.confidence,
                    "reasoning": signal.reasoning,
                    "signal_strength": signal.signal_strength.value,
                    "risk_reward_ratio": signal.risk_reward_ratio,
                    "timestamp": signal.timestamp.isoformat(),
                    "status": "new_signal_generated",
                    "cache_source": False,
                }
            )
        else:
            return jsonify(
                {
                    "symbol": symbol,
                    "status": "no_signal",
                    "message": "No strong SMC signal detected at this time",
                    "timestamp": get_iran_time().isoformat(),
                    "cache_source": False,
                }
            )

    except Exception as e:
        logging.error(f"Error in SMC analysis for {symbol}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/smc-signals")
def get_multiple_smc_signals():
    """Get SMC signals for multiple popular trading symbols with caching"""
    try:
        from .models import SMCSignalCache, db
        from .smc_analyzer import SMCAnalyzer

        # Analyze popular trading pairs
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT", "SOLUSDT"]
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
                        "direction": signal.direction,
                        "entry_price": signal.entry_price,
                        "stop_loss": signal.stop_loss,
                        "take_profit_levels": signal.take_profit_levels,
                        "confidence": signal.confidence,
                        "reasoning": signal.reasoning[
                            :3
                        ],  # Limit reasoning for summary
                        "signal_strength": signal.signal_strength.value,
                        "risk_reward_ratio": signal.risk_reward_ratio,
                        "timestamp": signal.timestamp.isoformat(),
                        "cache_source": True,
                    }
                else:
                    # Generate new signal
                    signal = analyzer.generate_trade_signal(symbol)
                    if signal:
                        # Cache the new signal with dynamic duration based on signal strength
                        cache_entry = SMCSignalCache.from_smc_signal(signal)
                        db.session.add(cache_entry)
                        new_signals_generated += 1

                        signals[symbol] = {
                            "direction": signal.direction,
                            "entry_price": signal.entry_price,
                            "stop_loss": signal.stop_loss,
                            "take_profit_levels": signal.take_profit_levels,
                            "confidence": signal.confidence,
                            "reasoning": signal.reasoning[
                                :3
                            ],  # Limit reasoning for summary
                            "signal_strength": signal.signal_strength.value,
                            "risk_reward_ratio": signal.risk_reward_ratio,
                            "timestamp": signal.timestamp.isoformat(),
                            "cache_source": False,
                        }
                    else:
                        signals[symbol] = {
                            "status": "no_signal",
                            "message": "No strong signal detected",
                            "cache_source": False,
                        }
            except Exception as e:
                signals[symbol] = {
                    "status": "error",
                    "message": str(e),
                    "cache_source": False,
                }

        # Commit any new cache entries
        if new_signals_generated > 0:
            db.session.commit()

        return jsonify(
            {
                "signals": signals,
                "timestamp": get_iran_time().isoformat(),
                "total_analyzed": len(symbols),
                "signals_found": len([s for s in signals.values() if "direction" in s]),
                "cache_hits": cache_hits,
                "new_signals_generated": new_signals_generated,
                "cache_efficiency": (
                    f"{(cache_hits / len(symbols) * 100):.1f}%" if symbols else "0%"
                ),
            }
        )

    except Exception as e:
        logging.error(f"Error getting multiple SMC signals: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/smc-chart-data/<symbol>", methods=["GET"])
def get_smc_chart_data(symbol: str):
    """Get candlestick data with SMC analysis overlays for chart visualization"""
    try:
        from .smc_analyzer import SMCAnalyzer
        
        symbol = symbol.upper()
        analyzer = SMCAnalyzer()
        
        # Get multi-timeframe candlestick data
        timeframe_data = analyzer.get_multi_timeframe_data(symbol)
        
        h1_data = timeframe_data.get("1h", [])
        h4_data = timeframe_data.get("4h", [])
        
        if not h1_data:
            return jsonify({"error": "No candlestick data available"}), 404
            
        # Analyze market structure and key SMC elements
        h1_structure = analyzer.detect_market_structure(h1_data)
        h4_structure = analyzer.detect_market_structure(h4_data) if h4_data else None
        
        # Find SMC elements for visualization
        order_blocks = analyzer.find_order_blocks(h1_data)
        fvgs = analyzer.find_fair_value_gaps(h1_data)
        liquidity_pools = analyzer.find_liquidity_pools(h4_data) if h4_data else []
        
        # Format candlestick data for chart.js
        candlesticks = []
        for candle in h1_data[-100:]:  # Last 100 candles for chart
            candlesticks.append({
                "time": int(candle["timestamp"].timestamp() * 1000),  # Convert to milliseconds
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle["volume"])
            })
        
        # Format order blocks for visualization
        order_blocks_data = []
        for ob in order_blocks[-10:]:  # Last 10 order blocks
            order_blocks_data.append({
                "high": float(ob.price_high),
                "low": float(ob.price_low),
                "time": int(ob.timestamp.timestamp() * 1000),
                "direction": ob.direction,
                "strength": float(ob.strength),
                "tested": ob.tested,
                "mitigated": ob.mitigated
            })
        
        # Format FVGs for visualization  
        fvgs_data = []
        for fvg in fvgs[-15:]:  # Last 15 FVGs
            fvgs_data.append({
                "high": float(fvg.gap_high),
                "low": float(fvg.gap_low),
                "time": int(fvg.timestamp.timestamp() * 1000),
                "direction": fvg.direction,
                "filled": fvg.filled,
                "age_candles": fvg.age_candles
            })
        
        # Format liquidity pools
        liquidity_data = []
        for lp in liquidity_pools[-8:]:  # Last 8 liquidity pools
            liquidity_data.append({
                "price": float(lp.price),
                "type": lp.type,  # 'buy_side' or 'sell_side'
                "strength": float(lp.strength),
                "swept": lp.swept
            })
        
        # Market structure information
        structure_info = {
            "h1_structure": h1_structure.value if h1_structure else None,
            "h4_structure": h4_structure.value if h4_structure else None,
            "current_price": float(h1_data[-1]["close"]) if h1_data else 0
        }
        
        # Get generated SMC signal for transparent chart overlay
        signal_overlay = None
        try:
            # Generate or get cached SMC signal for this symbol
            signal = analyzer.generate_trade_signal(symbol)
            if signal and signal.get("direction") and signal.get("direction") != "hold":
                current_price = float(h1_data[-1]["close"]) if h1_data else 0
                
                # Calculate entry, TP, SL levels from signal
                entry_price = signal.get("entry_price", current_price)
                stop_loss_price = signal.get("stop_loss_price")
                take_profit_price = signal.get("take_profit_price")
                
                signal_overlay = {
                    "direction": signal.get("direction"),  # 'long' or 'short'
                    "entry_price": float(entry_price) if entry_price else None,
                    "stop_loss_price": float(stop_loss_price) if stop_loss_price else None,
                    "take_profit_price": float(take_profit_price) if take_profit_price else None,
                    "confidence": signal.get("confidence", 0),
                    "signal_strength": signal.get("signal_strength", "medium"),
                    "entry_zone_high": signal.get("entry_zone_high"),
                    "entry_zone_low": signal.get("entry_zone_low"),
                    "timestamp": signal.get("timestamp", get_iran_time().isoformat())
                }
        except Exception as e:
            logging.error(f"Error fetching SMC signal for chart overlay: {e}")
            # Continue without signal overlay if there's an error
        
        return jsonify({
            "symbol": symbol,
            "candlesticks": candlesticks,
            "order_blocks": order_blocks_data,
            "fair_value_gaps": fvgs_data,
            "liquidity_pools": liquidity_data,
            "market_structure": structure_info,
            "signal_overlay": signal_overlay,  # New: SMC signal with entry/TP/SL for overlay
            "timestamp": get_iran_time().isoformat(),
            "total_candles": len(candlesticks)
        })
        
    except Exception as e:
        logging.error(f"Error getting SMC chart data for {symbol}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/smc-auto-trade", methods=["POST"])
def create_auto_trade_from_smc():
    """Create a trade configuration automatically based on SMC analysis
    
    Parameters:
    - symbol: Trading symbol (required)
    - user_id: User ID (required)  
    - margin_amount: Margin amount for the trade (default: 100)
    - entry_type: Order type preference - 'market' or 'limit' (optional)
                 If not provided, automatically determined based on price difference
    """
    try:
        data = request.get_json()
        symbol = data.get("symbol", "").upper()
        user_id = data.get("user_id")
        margin_amount = float(data.get("margin_amount", 100))
        user_entry_type = data.get("entry_type")  # Optional user preference for order type

        if not symbol or not user_id:
            return jsonify({"error": "Symbol and user_id required"}), 400
        
        # Validate entry_type if provided
        if user_entry_type and user_entry_type.lower() not in ["market", "limit"]:
            return jsonify({"error": f"Invalid entry_type '{user_entry_type}'. Must be 'market' or 'limit'"}), 400

        from .smc_analyzer import SMCAnalyzer

        analyzer = SMCAnalyzer()
        signal = analyzer.generate_trade_signal(symbol)

        if not signal:
            return (
                jsonify(
                    {
                        "error": "No SMC signal available for this symbol",
                        "symbol": symbol,
                    }
                ),
                400,
            )

        # Only proceed with strong signals
        if signal.confidence < 0.7:
            return (
                jsonify(
                    {
                        "error": "SMC signal confidence too low for auto-trading",
                        "confidence": signal.confidence,
                        "minimum_required": 0.7,
                    }
                ),
                400,
            )

        # Generate trade ID
        trade_id = f"smc_{symbol}_{int(datetime.now().timestamp())}"

        # Get current market price for comparison with error handling
        try:
            current_market_price = get_live_market_price(symbol, user_id=user_id, prefer_exchange=True)
            if not current_market_price:
                raise ValueError("Unable to fetch current market price")
            current_price_float = float(current_market_price)
            signal_entry_float = float(signal.entry_price)
        except Exception as e:
            logging.error(f"Failed to fetch/parse market price for SMC signal: {e}")
            return (
                jsonify(
                    {
                        "error": "Unable to fetch current market price for SMC signal validation",
                        "symbol": symbol,
                        "details": str(e),
                        "retry": "Please try again in a few moments"
                    }
                ),
                500,
            )
        
        # Calculate price difference percentage
        price_diff_percent = abs(signal_entry_float - current_price_float) / current_price_float * 100
        
        # Create trade configuration
        trade_config = TradeConfig(trade_id, f"SMC Auto-Trade {symbol}")
        trade_config.symbol = symbol
        trade_config.side = signal.direction
        trade_config.amount = margin_amount
        trade_config.leverage = 5  # Conservative leverage for auto-trades
        trade_config.entry_price = signal.entry_price
        
        # SMC order type logic - Respect user preference or auto-determine based on price difference
        if user_entry_type and user_entry_type.lower() in ["market", "limit"]:
            # Use user-specified order type
            trade_config.entry_type = user_entry_type.lower()
            if trade_config.entry_type == "market":
                trade_config.entry_price = current_price_float  # Use current price for market execution
            logging.info(f"SMC {signal.direction.upper()} {trade_config.entry_type.upper()} order (user specified): entry={signal_entry_float:.4f}, current={current_price_float:.4f}, diff={price_diff_percent:.2f}%")
        else:
            # Auto-determine order type based on price difference (legacy behavior)
            if price_diff_percent > 0.5:  # Price difference threshold for limit vs market order
                # Significant price difference - use limit order to wait for better price
                trade_config.entry_type = "limit"
                logging.info(f"SMC {signal.direction.upper()} LIMIT order (auto): entry={signal_entry_float:.4f}, current={current_price_float:.4f}, diff={price_diff_percent:.2f}%")
            else:
                # Entry price close to current price - use market for immediate execution
                trade_config.entry_type = "market"
                trade_config.entry_price = current_price_float  # Use current price for market execution
                logging.info(f"SMC {signal.direction.upper()} MARKET order (auto): entry={signal_entry_float:.4f}, current={current_price_float:.4f}, diff={price_diff_percent:.2f}%")

        # FIXED: Calculate stop loss percentage on margin for system compatibility
        # The monitoring system expects margin-based percentages for trigger comparison
        if signal.direction == "long":
            sl_price_movement_percent = (
                (signal.entry_price - signal.stop_loss) / signal.entry_price
            ) * 100
        else:
            sl_price_movement_percent = (
                (signal.stop_loss - signal.entry_price) / signal.entry_price
            ) * 100

        # Convert to margin percentage for system compatibility while preserving SMC intent
        sl_percent_on_margin = sl_price_movement_percent * trade_config.leverage
        trade_config.stop_loss_percent = min(
            sl_percent_on_margin, 25.0
        )  # Cap at 25% margin loss for safety
        
        # Store SMC references for debugging/analysis (internal use)
        setattr(trade_config, '_smc_stop_loss_price', signal.stop_loss)
        setattr(trade_config, '_smc_price_movement', sl_price_movement_percent)

        # Set up take profit levels with proper allocation logic
        tp_levels = []
        num_tp_levels = min(len(signal.take_profit_levels), 3)  # Max 3 TP levels

        # Define allocation strategies based on number of TP levels
        # CORRECTED: More conservative and standard allocation strategies
        allocation_strategies = {
            1: [100],  # Single TP: close full position
            2: [50, 50],  # Two TPs: 50% each (more balanced)
            3: [
                40,
                35,
                25,
            ],  # Three TPs: 40%, 35%, 25% (ensures all allocations add to 100%)
        }

        allocations = allocation_strategies.get(num_tp_levels, [100])

        for i, tp_price in enumerate(signal.take_profit_levels[:3]):
            # FIXED: Calculate TP percentage on margin for system compatibility while preserving SMC intent
            # The monitoring system expects margin-based percentages for trigger comparison

            if signal.direction == "long":
                price_movement_percent = (
                    (tp_price - signal.entry_price) / signal.entry_price
                ) * 100
            else:
                price_movement_percent = (
                    (signal.entry_price - tp_price) / signal.entry_price
                ) * 100

            # Convert to margin percentage for system compatibility
            # This maintains proper trigger levels while preserving SMC accuracy
            tp_percent_on_margin = price_movement_percent * trade_config.leverage
            allocation = allocations[i] if i < len(allocations) else 10

            tp_levels.append(
                {
                    "percentage": tp_percent_on_margin,  # Store margin percentage for trigger compatibility
                    "allocation": allocation,
                    "triggered": False,
                    "_smc_price_target": tp_price,  # Internal reference to original SMC price
                    "_smc_price_movement": price_movement_percent,  # Internal reference to price movement
                }
            )

        trade_config.take_profits = tp_levels

        # Add SMC analysis details to notes
        trade_config.notes = (
            f"SMC Auto-Trade | Confidence: {signal.confidence:.1%} | "
            + f"Signal Strength: {signal.signal_strength.value} | "
            + f"R:R = 1:{signal.risk_reward_ratio:.1f}"
        )

        # Store the trade configuration (use integer user_id for consistency)
        user_id_int = int(user_id)
        with trade_configs_lock:
            if user_id_int not in user_trade_configs:
                user_trade_configs[user_id_int] = {}
            user_trade_configs[user_id_int][trade_id] = trade_config

        # Save to database
        save_trade_to_db(user_id, trade_config)

        return jsonify(
            {
                "success": True,
                "trade_id": trade_id,
                "trade_config": {
                    "symbol": trade_config.symbol,
                    "side": trade_config.side,
                    "amount": trade_config.amount,
                    "leverage": trade_config.leverage,
                    "entry_price": trade_config.entry_price,
                    "stop_loss_percent": trade_config.stop_loss_percent,
                    "take_profits": trade_config.take_profits,
                    "smc_analysis": {
                        "confidence": signal.confidence,
                        "signal_strength": signal.signal_strength.value,
                        "reasoning": signal.reasoning,
                        "risk_reward_ratio": signal.risk_reward_ratio,
                    },
                },
                "message": f"SMC-based trade configuration created for {symbol}",
                "timestamp": get_iran_time().isoformat(),
            }
        )

    except Exception as e:
        logging.error(f"Error creating auto-trade from SMC: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/recent-trades")
def recent_trades():
    """Get recent trades from database (bot functionality removed)"""
    try:
        # Get recent trades from database instead of bot_trades array
        recent_trade_configs = TradeConfiguration.query.order_by(
            TradeConfiguration.created_at.desc()
        ).limit(10).all()
        
        trades_data = []
        for trade in recent_trade_configs:
            trades_data.append({
                "id": trade.trade_id,
                "user_id": trade.telegram_user_id,
                "symbol": trade.symbol,
                "side": trade.side,
                "amount": trade.amount,
                "leverage": trade.leverage,
                "status": trade.status,
                "entry_price": trade.entry_price,
                "unrealized_pnl": trade.unrealized_pnl,
                "timestamp": trade.created_at.isoformat() if trade.created_at else None,
            })
        
        return jsonify(trades_data)
    except Exception as e:
        logging.error(f"Error getting recent trades: {e}")
        return jsonify({"error": "Unable to fetch recent trades"}), 500


@app.route("/api/debug/paper-trading-status")
def debug_paper_trading_status():
    """Debug endpoint for paper trading status and diagnostics"""
    user_id = get_user_id_from_request()

    try:
        chat_id = int(user_id)

        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=user_id, is_active=True
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
            if hasattr(config, "paper_trading_mode") and config.paper_trading_mode:
                paper_trades.append(
                    {
                        "trade_id": trade_id,
                        "symbol": config.symbol,
                        "status": config.status,
                        "entry_price": getattr(config, "entry_price", None),
                        "current_price": getattr(config, "current_price", None),
                        "unrealized_pnl": getattr(config, "unrealized_pnl", 0),
                        "has_paper_sl_data": hasattr(config, "paper_sl_data"),
                        "has_paper_tp_levels": hasattr(config, "paper_tp_levels"),
                        "breakeven_triggered": getattr(
                            config, "breakeven_sl_triggered", False
                        ),
                    }
                )

        return jsonify(
            {
                "success": True,
                "user_id": user_id,
                "manual_paper_mode": user_paper_trading_preferences.get(chat_id, True),
                "is_paper_mode": is_paper_mode,
                "has_credentials": (
                    user_creds is not None and user_creds.has_credentials()
                    if user_creds
                    else False
                ),
                "testnet_mode": user_creds.testnet_mode if user_creds else False,
                "paper_balance": user_paper_balances.get(
                    chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE
                ),
                "total_trades": len(trades),
                "paper_trades": paper_trades,
                "paper_trades_count": len(paper_trades),
                "environment": {
                    "is_render": Environment.IS_RENDER,
                    "is_vercel": Environment.IS_VERCEL,
                    "is_replit": Environment.IS_REPLIT,
                },
                "timestamp": get_iran_time().isoformat(),
            }
        )

    except Exception as e:
        logging.error(f"Error in paper trading debug endpoint: {e}")
        return (
            jsonify({"error": "Failed to get paper trading status", "details": str(e)}),
            500,
        )


@app.route("/api/debug/position-close-test")
def debug_position_close_test():
    """Debug endpoint to test position closing functionality on Render"""
    user_id = get_user_id_from_request()

    # Get user credentials
    user_creds = UserCredentials.query.filter_by(
        telegram_user_id=str(user_id), is_active=True
    ).first()

    debug_info = {
        "timestamp": get_iran_time().isoformat(),
        "user_id": user_id,
        "has_credentials": False,
        "testnet_mode": False,
        "api_connection_test": "Not tested",
        "active_trades_count": 0,
        "paper_mode_active": True,
        "last_error": None,
        "exchange_client_status": "Not created",
    }

    if user_creds and user_creds.has_credentials():
        debug_info["has_credentials"] = True
        debug_info["testnet_mode"] = user_creds.testnet_mode

        # Test API connection
        try:
            logging.debug(f"Creating exchange client for user {user_id}")
            client = create_exchange_client(user_creds, testnet=False)
            debug_info["exchange_client_status"] = "Created successfully"

            # Test basic connection
            logging.debug(f"Testing API connection for user {user_id}")
            balance = client.get_futures_balance()
            if balance:
                debug_info["api_connection_test"] = "Success"
                debug_info["paper_mode_active"] = False
                debug_info["account_balance"] = balance
            else:
                debug_info["api_connection_test"] = "Failed - No balance data"
                debug_info["last_error"] = client.get_last_error()

        except Exception as e:
            debug_info["api_connection_test"] = f"Failed - Exception: {str(e)}"
            debug_info["last_error"] = str(e)
            debug_info["exchange_client_status"] = f"Creation failed: {str(e)}"
            logging.error(f"ToobitClient creation failed for user {user_id}: {e}")

    # Count active trades
    active_count = 0
    active_trades = []
    for trade_id, config in user_trade_configs.get(user_id, {}).items():
        if config.status == "active":
            active_count += 1
            active_trades.append(
                {
                    "trade_id": trade_id,
                    "symbol": config.symbol,
                    "side": config.side,
                    "position_size": getattr(config, "position_size", 0),
                    "unrealized_pnl": getattr(config, "unrealized_pnl", 0),
                }
            )

    debug_info["active_trades_count"] = active_count
    debug_info["active_trades"] = active_trades

    logging.debug(f"Position close test for user {user_id}")
    return jsonify(debug_info)


@app.route("/api/margin-data")
def margin_data():
    """Get comprehensive margin data for a specific user"""
    user_id = request.args.get("user_id")
    if not user_id or user_id == "undefined":
        # For testing outside Telegram, use a demo user
        user_id = Environment.DEFAULT_TEST_USER_ID

    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400

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

                user_positions.append(
                    {
                        "trade_id": trade_id,
                        "symbol": config.symbol,
                        "side": config.side,
                        "amount": config.amount,  # This is the margin
                        "position_size": config.amount
                        * config.leverage,  # This is the actual position size
                        "leverage": config.leverage,
                        "margin_used": config.position_margin,
                        "entry_price": config.entry_price,
                        "current_price": config.current_price,
                        "unrealized_pnl": config.unrealized_pnl,
                        "realized_pnl": getattr(
                            config, "realized_pnl", 0.0
                        ),  # Include realized P&L from triggered TPs
                        "total_pnl": (config.unrealized_pnl or 0)
                        + (getattr(config, "realized_pnl", 0) or 0),  # Combined P&L
                        "status": config.status,
                        "take_profits": config.take_profits,
                        "stop_loss_percent": config.stop_loss_percent,
                        "tp_sl_calculations": tp_sl_data,
                    }
                )

    # Calculate total realized P&L from closed positions AND partial TP closures from active positions
    total_realized_pnl = 0.0
    with trade_configs_lock:
        user_configs = user_trade_configs.get(chat_id, {})
        for config in user_configs.values():
            if (
                config.status == "stopped"
                and hasattr(config, "final_pnl")
                and config.final_pnl is not None
            ):
                total_realized_pnl += config.final_pnl
            # Also include partial realized P&L from active positions (from partial TPs)
            elif (
                config.status == "active"
                and hasattr(config, "realized_pnl")
                and config.realized_pnl is not None
            ):
                total_realized_pnl += config.realized_pnl

    return jsonify(
        {
            "user_id": user_id,
            "summary": {
                "account_balance": margin_summary["account_balance"],
                "total_margin_used": margin_summary["total_margin"],
                "free_margin": margin_summary["free_margin"],
                "unrealized_pnl": margin_summary["unrealized_pnl"],
                "realized_pnl": total_realized_pnl,
                "total_pnl": margin_summary["unrealized_pnl"] + total_realized_pnl,
                "margin_utilization": (
                    (
                        margin_summary["total_margin"]
                        / margin_summary["account_balance"]
                        * 100
                    )
                    if margin_summary["account_balance"] > 0
                    else 0
                ),
                "total_positions": len(user_positions),
            },
            "positions": user_positions,
            "timestamp": get_iran_time().isoformat(),
        }
    )


@app.route("/api/positions")
def api_positions():
    """Get positions for the web app - alias for margin-data"""
    return margin_data()


def _validate_and_get_user_id():
    """Validate and extract user ID from request."""
    user_id = request.args.get("user_id")
    if not user_id or user_id == "undefined":
        user_id = Environment.DEFAULT_TEST_USER_ID

    try:
        chat_id = int(user_id)
        return chat_id, None
    except ValueError:
        return None, jsonify({"error": "Invalid user ID format"}), 400

def _validate_authenticated_user():
    """Validate user is authenticated and whitelisted for API access."""
    # Get authenticated user from session or Telegram WebApp data
    user_id = get_authenticated_user()
    if not user_id:
        return None, jsonify({"error": "Authentication required"}), 401
    
    # Check whitelist if enabled
    if WHITELIST_ENABLED and not is_user_whitelisted(user_id):
        return None, jsonify({"error": "Access not authorized"}), 403
    
    try:
        chat_id = int(user_id)
        return chat_id, None
    except ValueError:
        return None, jsonify({"error": "Invalid user ID format"}), 400


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
        if config.status == "active" and getattr(config, "paper_trading_mode", False):
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
            roe_percentage, price_change_percentage = _calculate_position_metrics(
                config
            )

            live_data[trade_id] = {
                "current_price": config.current_price,
                "unrealized_pnl": config.unrealized_pnl,
                "realized_pnl": getattr(config, "realized_pnl", 0) or 0,
                "total_pnl": (config.unrealized_pnl or 0)
                + (getattr(config, "realized_pnl", 0) or 0),
                "roe_percentage": roe_percentage,
                "price_change_percentage": price_change_percentage,
                "status": config.status,
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
        if (
            config.status == "stopped"
            and hasattr(config, "final_pnl")
            and config.final_pnl is not None
        ):
            total_realized_pnl += config.final_pnl
        # Also include partial realized P&L from active positions (from partial TPs)
        elif (
            config.status == "active"
            and hasattr(config, "realized_pnl")
            and config.realized_pnl is not None
        ):
            total_realized_pnl += config.realized_pnl

    return total_realized_pnl


@app.route("/api/positions/live-update")
def live_position_update():
    """Get only current prices and P&L for active positions (lightweight update)"""
    # Validate authentication and whitelist
    chat_id, error_response = _validate_authenticated_user()
    if error_response:
        return error_response

    # Handle environment-specific synchronization
    _handle_environment_sync(str(chat_id), chat_id)

    # Check if user has trades loaded
    if chat_id not in user_trade_configs:
        return jsonify(
            {
                "positions": {},
                "total_unrealized_pnl": 0.0,
                "active_positions_count": 0,
                "timestamp": get_iran_time().isoformat(),
                "update_type": "live_prices",
            }
        )

    # Select appropriate update strategy
    _select_update_strategy(chat_id)

    # Build live position data
    live_data, total_unrealized_pnl, active_positions_count = _build_live_position_data(
        chat_id
    )

    # Calculate total realized P&L
    total_realized_pnl = _calculate_total_realized_pnl(chat_id)

    # Calculate total P&L (realized + unrealized)
    total_pnl = total_realized_pnl + total_unrealized_pnl

    return jsonify(
        {
            "positions": live_data,
            "total_unrealized_pnl": total_unrealized_pnl,
            "total_realized_pnl": total_realized_pnl,
            "total_pnl": total_pnl,
            "active_positions_count": active_positions_count,
            "timestamp": get_iran_time().isoformat(),
            "update_type": "live_prices",
        }
    )


@app.route("/api/trading/new")
def api_trading_new():
    """Create new trading configuration"""
    user_id = get_user_id_from_request()

    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400

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

    return jsonify(
        {
            "success": True,
            "trade_id": trade_id,
            "trade_name": new_trade.name,
            "message": f"Created new position: {new_trade.get_display_name()}",
        }
    )


@app.route("/api/user-trades")
def user_trades():
    """Get all trades for a specific user"""
    user_id = request.args.get("user_id")
    if not user_id or user_id == "undefined":
        # For testing outside Telegram, use a demo user
        user_id = Environment.DEFAULT_TEST_USER_ID

    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400

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
                if hasattr(config, "final_pnl") and config.final_pnl is not None:
                    final_pnl = config.final_pnl
                    closed_at = getattr(config, "closed_at", None)
                else:
                    # Use stored values from config as fallback
                    final_pnl = getattr(config, "unrealized_pnl", 0)
                    closed_at = getattr(config, "closed_at", None)

            user_trade_list.append(
                {
                    "trade_id": trade_id,
                    "name": config.name,
                    "symbol": config.symbol,
                    "side": config.side,
                    "amount": config.amount,  # This is the margin
                    "position_size": config.amount
                    * config.leverage,  # This is the actual position size
                    "leverage": config.leverage,
                    "entry_type": config.entry_type,
                    "entry_price": config.entry_price,
                    "take_profits": config.take_profits,
                    "stop_loss_percent": config.stop_loss_percent,
                    "status": config.status,
                    "position_margin": config.position_margin,
                    "unrealized_pnl": config.unrealized_pnl,
                    "realized_pnl": getattr(
                        config, "realized_pnl", 0.0
                    ),  # Include realized P&L from triggered TPs
                    "current_price": config.current_price,
                    "breakeven_after": config.breakeven_after,
                    "trailing_stop_enabled": config.trailing_stop_enabled,
                    "trail_percentage": config.trail_percentage,
                    "trail_activation_price": config.trail_activation_price,
                    "tp_sl_calculations": tp_sl_data,
                    "final_pnl": final_pnl,  # Include final P&L for closed positions
                    "closed_at": closed_at,  # Include closure timestamp
                }
            )

    return jsonify(
        {
            "user_id": user_id,
            "trades": user_trade_list,
            "total_trades": len(user_trade_list),
            "timestamp": datetime.utcnow().isoformat(),
        }
    )


@app.route("/api/trade-config")
def trade_config():
    """Get specific trade configuration"""
    trade_id = request.args.get("trade_id")
    user_id = request.headers.get("X-Telegram-User-ID")

    if not trade_id or not user_id:
        return jsonify({"error": "Trade ID and User ID required"}), 400

    chat_id = int(user_id)

    # Use cached initialization for both Vercel and Replit
    initialize_user_environment(chat_id)
    user_configs = user_trade_configs.get(chat_id, {})

    if user_configs and trade_id in user_configs:
        config = user_configs[trade_id]
        return jsonify(
            {
                "trade_id": trade_id,
                "name": config.name,
                "symbol": config.symbol,
                "side": config.side,
                "amount": config.amount,  # This is the margin
                "position_size": config.amount
                * config.leverage,  # This is the actual position size
                "leverage": config.leverage,
                "entry_type": config.entry_type,
                "entry_price": config.entry_price,
                "take_profits": config.take_profits,
                "stop_loss_percent": config.stop_loss_percent,
                "status": config.status,
                "breakeven_after": config.breakeven_after,
                "trailing_stop_enabled": config.trailing_stop_enabled,
                "trail_percentage": config.trail_percentage,
                "trail_activation_price": config.trail_activation_price,
            }
        )

    return jsonify({"error": "Trade not found"}), 404


def _validate_save_trade_request(data):
    """Validate save trade request"""
    user_id = data.get("user_id")
    trade_data = data.get("trade")

    if not user_id or not trade_data:
        return None, None, None, {"error": "User ID and trade data required"}, 400

    chat_id = int(user_id)
    trade_id = trade_data.get("trade_id")

    # Create or update trade config
    if trade_id.startswith("new_"):
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
            user_trade_configs[chat_id][trade_id] = TradeConfig(
                trade_id, trade_data.get("name", "New Trade")
            )

    return user_trade_configs[chat_id][trade_id]


def _check_active_trade_restrictions(config, trade_data, trade_id):
    """Check if core parameters are being modified for active trades"""
    is_active_trade = config.status in ["active", "pending"]

    if not is_active_trade:
        return False, []

    core_param_changes = []

    if "symbol" in trade_data and trade_data["symbol"] != config.symbol:
        core_param_changes.append("symbol")
    if "side" in trade_data and trade_data["side"] != config.side:
        core_param_changes.append("side")
    if (
        "amount" in trade_data
        and abs(float(trade_data["amount"]) - float(config.amount)) > 0.0001
    ):
        core_param_changes.append("amount")
    if "leverage" in trade_data and int(trade_data["leverage"]) != int(config.leverage):
        core_param_changes.append("leverage")
    if "entry_type" in trade_data and trade_data["entry_type"] != config.entry_type:
        core_param_changes.append("entry_type")
    if "entry_price" in trade_data:
        new_entry_price = (
            float(trade_data["entry_price"]) if trade_data["entry_price"] else 0.0
        )
        current_entry_price = float(config.entry_price) if config.entry_price else 0.0
        if abs(new_entry_price - current_entry_price) > 0.0001:
            core_param_changes.append("entry_price")

    return is_active_trade, core_param_changes


def _update_core_parameters(config, trade_data):
    """Update core trading parameters for non-active trades"""
    if "symbol" in trade_data:
        config.symbol = trade_data["symbol"]
    if "side" in trade_data:
        config.side = trade_data["side"]
    if "amount" in trade_data:
        config.amount = float(trade_data["amount"])
    if "leverage" in trade_data:
        config.leverage = int(trade_data["leverage"])
    if "entry_type" in trade_data:
        config.entry_type = trade_data["entry_type"]
    if "entry_price" in trade_data:
        config.entry_price = (
            float(trade_data["entry_price"]) if trade_data["entry_price"] else 0.0
        )


def _update_risk_parameters(config, trade_data):
    """Update risk management parameters (always allowed)"""
    risk_params_updated = []

    if "take_profits" in trade_data:
        config.take_profits = trade_data["take_profits"]
        risk_params_updated.append("take_profits")
    if "stop_loss_percent" in trade_data:
        config.stop_loss_percent = (
            float(trade_data["stop_loss_percent"])
            if trade_data["stop_loss_percent"]
            else 0.0
        )
        risk_params_updated.append("stop_loss")
    if "breakeven_after" in trade_data:
        config.breakeven_after = trade_data["breakeven_after"]
        risk_params_updated.append("breakeven")
    if "trailing_stop_enabled" in trade_data:
        config.trailing_stop_enabled = bool(trade_data["trailing_stop_enabled"])
        risk_params_updated.append("trailing_stop")
    if "trail_percentage" in trade_data:
        config.trail_percentage = (
            float(trade_data["trail_percentage"])
            if trade_data["trail_percentage"]
            else 0.0
        )
        risk_params_updated.append("trailing_percentage")
    if "trail_activation_price" in trade_data:
        config.trail_activation_price = (
            float(trade_data["trail_activation_price"])
            if trade_data["trail_activation_price"]
            else 0.0
        )
        risk_params_updated.append("trailing_activation")

    return risk_params_updated


@app.route("/api/save-trade", methods=["POST"])
def save_trade():
    """Save or update trade configuration - refactored for better maintainability"""
    try:
        data = request.get_json()

        # Validate request
        chat_id, trade_id, trade_data, error, status_code = (
            _validate_save_trade_request(data)
        )
        if error:
            return jsonify(error), status_code

        # Ensure trade config exists
        config = _ensure_trade_config_exists(chat_id, trade_id, trade_data)

        # Check active trade restrictions
        is_active_trade, core_param_changes = _check_active_trade_restrictions(
            config, trade_data, trade_id
        )

        if is_active_trade and core_param_changes:
            logging.warning(
                f"Attempted to modify core parameters {core_param_changes} for active trade {trade_id}. Changes rejected for safety."
            )
            return (
                jsonify(
                    {
                        "error": f"Cannot modify core trade parameters ({', '.join(core_param_changes)}) for active trades. Only take profits, stop loss, break-even, and trailing stop can be modified.",
                        "active_trade": True,
                        "rejected_changes": core_param_changes,
                        "message": "For active positions, you can only edit risk management settings (TP/SL levels, breakeven, trailing stop).",
                    }
                ),
                400,
            )

        # Update parameters based on trade status
        if not is_active_trade:
            _update_core_parameters(config, trade_data)

        risk_params_updated = _update_risk_parameters(config, trade_data)

        # Log risk management updates for active trades
        if is_active_trade and risk_params_updated:
            logging.info(
                f"Updated risk management parameters for active trade {trade_id}: {', '.join(risk_params_updated)}"
            )

        # Set as selected trade for user
        with trade_configs_lock:
            user_selected_trade[chat_id] = trade_id

        # Save to database
        save_trade_to_db(chat_id, config)

        success_message = "Trade configuration saved successfully"
        if is_active_trade and risk_params_updated:
            success_message = f"Risk management parameters updated for active trade: {', '.join(risk_params_updated)}"

        return jsonify(
            {
                "success": True,
                "trade_id": trade_id,
                "message": success_message,
                "active_trade": is_active_trade,
                "risk_params_updated": risk_params_updated if is_active_trade else [],
            }
        )

    except Exception as e:
        logging.error(f"Error saving trade: {str(e)}")
        return jsonify({"error": "Failed to save trade configuration"}), 500


def _validate_execute_trade_request(data):
    """Validate execute trade request data and extract user_id and trade_id."""
    user_id = data.get("user_id")
    trade_id = data.get("trade_id")

    if not user_id:
        return (
            None,
            None,
            jsonify(
                create_validation_error("User ID", None, "A valid user ID is required")
            ),
            400,
        )

    if not trade_id:
        return (
            None,
            None,
            jsonify(
                create_validation_error(
                    "Trade ID", None, "A valid trade ID is required"
                )
            ),
            400,
        )

    return user_id, trade_id, None, None


def _get_and_validate_trade_config(chat_id, trade_id):
    """Get and validate trade configuration exists and is complete."""
    if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
        from api.error_handler import ErrorCategory, ErrorSeverity, TradingError

        error = TradingError(
            category=ErrorCategory.VALIDATION_ERROR,
            severity=ErrorSeverity.MEDIUM,
            technical_message=f"Trade {trade_id} not found for user {chat_id}",
            user_message="The trade configuration you're trying to execute was not found.",
            suggestions=[
                "Check that the trade ID is correct",
                "Refresh the page to reload your trades",
                "Create a new trade configuration if needed",
            ],
        )
        return None, jsonify(error.to_dict()), 404

    config = user_trade_configs[chat_id][trade_id]

    # Validate configuration completeness
    if not config.is_complete():
        from api.error_handler import ErrorCategory, ErrorSeverity, TradingError

        error = TradingError(
            category=ErrorCategory.VALIDATION_ERROR,
            severity=ErrorSeverity.HIGH,
            technical_message=f"Incomplete trade configuration for {config.symbol}",
            user_message="Your trade setup is missing some important information.",
            suggestions=[
                "Check that you've set the trading symbol",
                "Verify you've selected long or short direction",
                "Make sure you've set the trade amount",
                "Ensure take profit and stop loss are configured",
            ],
        )
        return None, jsonify(error.to_dict()), 400

    return config, None, None


def _setup_exchange_client_and_mode(chat_id, config):
    """Set up exchange client and determine trading mode."""
    # Check if user is in paper trading mode
    user_creds = UserCredentials.query.filter_by(
        telegram_user_id=str(chat_id), is_active=True
    ).first()

    # Use centralized trading mode detection
    is_paper_mode = determine_trading_mode(chat_id)

    if is_paper_mode:
        # Set exchange for paper trading (default to lbank if no credentials)
        config.exchange = user_creds.exchange_name if user_creds else "lbank"
        return None, is_paper_mode, user_creds, None, None
    else:
        # REAL TRADING - Validate credentials and create exchange client
        if not user_creds or not user_creds.has_credentials():
            return (
                None,
                is_paper_mode,
                user_creds,
                jsonify(
                    {
                        "error": "API credentials required for real trading. Please set up your Toobit API keys."
                    }
                ),
                400,
            )

        # Validate credentials and create exchange client
        client, error_response = _validate_credentials_and_create_client(
            user_creds, chat_id
        )
        if error_response is not None:
            return None, is_paper_mode, user_creds, error_response, None

        # Set the exchange name in the config for proper order routing
        config.exchange = user_creds.exchange_name or "toobit"
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
            error_msg = client.get_last_error() or "Failed to set leverage"
            logging.error(f"Failed to set leverage: {error_msg}")
            return (
                False,
                jsonify(
                    {
                        "error": f"Failed to set leverage: {error_msg}",
                        "troubleshooting": [
                            "Check if the symbol supports the specified leverage",
                            "Verify you have no open positions that would conflict",
                            "Ensure your account has sufficient margin",
                        ],
                    }
                ),
                500,
            )
        else:
            logging.info(
                f"Successfully set leverage {config.leverage}x for {config.symbol}"
            )

        # Determine order type and parameters based on exchange
        if config.exchange == "lbank":
            # LBank uses simple buy/sell for perpetual futures
            order_side = "buy" if config.side == "long" else "sell"
            order_type = "limit"
        else:  # toobit
            # Toobit futures requires specific position actions: BUY_OPEN/SELL_OPEN for opening positions
            order_side = "BUY_OPEN" if config.side == "long" else "SELL_OPEN"
            order_type = "limit"

        # Convert symbol to exchange format for proper API call
        if config.exchange == "lbank":
            exchange_symbol = getattr(client, "convert_to_lbank_symbol", lambda x: x)(
                config.symbol
            )
            endpoint_info = "/cfd/openApi/v1/prv/order"
        else:  # toobit
            exchange_symbol = getattr(client, "convert_to_toobit_symbol", lambda x: x)(
                config.symbol
            )
            endpoint_info = "/api/v1/futures/order"

        logging.info(
            f"[DEBUG] Will send {config.exchange.upper()} order to: {endpoint_info} with symbol={exchange_symbol}, side={order_side}, quantity={position_size} contracts"
        )

        # Prepare order parameters based on entry type
        if config.entry_type == "market":
            # For market execution, use LIMIT order at market price with buffer
            market_price_float = float(current_market_price)
            if order_side == "BUY":
                exec_price = market_price_float * 1.001  # Slightly above market
            else:
                exec_price = market_price_float * 0.999  # Slightly below market

            order_params = {
                "symbol": config.symbol,
                "side": order_side,
                "order_type": order_type,
                "quantity": str(position_size),
                "price": str(exec_price),
                "timeInForce": "IOC",  # Immediate or Cancel for market-like behavior
            }
        else:
            # Standard limit order
            order_params = {
                "symbol": config.symbol,
                "side": order_side,
                "order_type": order_type,
                "quantity": str(position_size),
                "price": str(config.entry_price),
                "timeInForce": "GTC",
            }

        order_result = client.place_order(**order_params)

        if not order_result:
            error_details = {
                "client_last_error": getattr(client, "last_error", None),
                "order_params": {
                    "symbol": config.symbol,
                    "side": order_side,
                    "type": order_type,
                    "quantity_calculated": round(position_size, 6),
                },
            }

            logging.error(
                f"Order placement failed: {error_details.get('client_last_error', 'Unknown error')}"
            )

            return (
                False,
                jsonify(
                    {
                        "error": "Failed to place order on exchange. Please check the details below.",
                        "debug_info": error_details,
                        "troubleshooting": [
                            "Check if you have sufficient balance for this trade",
                            "Verify the symbol is supported on Toobit futures",
                            "Ensure your position size meets minimum requirements",
                            "Check if there are any trading restrictions on your account",
                        ],
                    }
                ),
                500,
            )

        logging.info(f"Order placed on exchange: {order_result}")

        # Store exchange order ID
        config.exchange_order_id = order_result.get("orderId")
        config.exchange_client_order_id = order_result.get("clientOrderId")

        return True, None, None

    except Exception as e:
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "client_last_error": (
                getattr(client, "last_error", None) if client else None
            ),
            "trade_config": {
                "symbol": config.symbol,
                "side": config.side,
                "amount": config.amount,
                "leverage": config.leverage,
                "entry_type": config.entry_type,
            },
        }

        logging.error(
            f"[RENDER TRADING ERROR] Exchange order placement failed: {error_details}"
        )

        # Import stack trace for detailed debugging
        import traceback

        logging.error(f"[RENDER STACK TRACE] {traceback.format_exc()}")

        return (
            False,
            jsonify(
                {
                    "error": f"Trading execution failed: {str(e)}",
                    "debug_info": error_details,
                    "troubleshooting": [
                        "Check your internet connection",
                        "Verify your exchange API credentials are active",
                        "Ensure you have sufficient account balance",
                        "Try refreshing the page and attempting the trade again",
                    ],
                }
            ),
            500,
        )


def _handle_paper_trading_execution(config, chat_id):
    """Handle paper trading execution setup."""
    logging.info(
        f"Paper Trading: Executing simulated trade for user {chat_id}: {config.symbol} {config.side}"
    )

    # Simulate order placement with paper trading IDs
    mock_order_id = f"paper_{uuid.uuid4().hex[:8]}"
    config.exchange_order_id = mock_order_id
    config.exchange_client_order_id = f"paper_client_{mock_order_id}"

    # Mark as paper trading and initialize monitoring
    config.paper_trading_mode = True
    
    if config.entry_type == "market":
        # Market orders are immediately active - initialize full monitoring
        initialize_paper_trading_monitoring(config)
        logging.info(
            f"Paper Trading: Market position opened for {config.symbol} {config.side} - Real-time monitoring enabled"
        )
    else:
        # Limit orders need special monitoring to check when limit price is hit
        _initialize_limit_order_monitoring(config, chat_id)
        logging.info(
            f"Paper Trading: Limit order placed for {config.symbol} {config.side} at ${config.entry_price:.4f} - Monitoring until filled"
        )

    return True


def _initialize_limit_order_monitoring(config, user_id):
    """Initialize monitoring for paper trading limit orders."""
    # Explicitly set status to pending for limit orders
    config.status = "pending"
    
    # Set up paper trading attributes
    config.paper_trading_mode = True
    
    # Ensure the position has the required attributes for monitoring
    if not hasattr(config, 'current_price'):
        config.current_price = 0.0
    
    # Set up TP/SL data structure for when the limit order gets filled
    if config.take_profits:
        tp_sl_data = calculate_tp_sl_prices_and_amounts(config)
        config.paper_tp_levels = []
        
        if tp_sl_data.get("take_profits"):
            for i, tp_data in enumerate(tp_sl_data["take_profits"]):
                config.paper_tp_levels.append({
                    "order_id": f"paper_tp_{i+1}_{uuid.uuid4().hex[:6]}",
                    "level": i + 1,
                    "price": tp_data["price"],
                    "percentage": tp_data["percentage"],
                    "allocation": tp_data["allocation"],
                    "triggered": False,
                })
        
        if config.stop_loss_percent > 0 and tp_sl_data.get("stop_loss"):
            config.paper_sl_data = {
                "order_id": f"paper_sl_{uuid.uuid4().hex[:6]}",
                "price": tp_sl_data["stop_loss"]["price"],
                "percentage": config.stop_loss_percent,
                "triggered": False,
            }
    
    # Persist the pending status to database
    save_trade_to_db(user_id, config)
    
    logging.info(
        f"Paper Trading: Limit order monitoring initialized for {config.symbol} {config.side} at ${config.entry_price:.4f} - Status: {config.status}"
    )


def _configure_trade_position(config, current_market_price):
    """Configure trade position details and pricing."""
    # Calculate common values needed for both paper and real trading
    position_value = config.amount * config.leverage
    position_size = round(position_value / current_market_price, 6)

    # Update trade configuration - status depends on order type
    if config.entry_type == "limit":
        config.status = "pending"  # Limit orders start as pending until filled
    else:
        config.status = "active"  # Market orders are immediately active

    # Store original amounts when trade is first executed for TP calculations
    if not hasattr(config, "original_amount"):
        config.original_amount = config.amount
    if not hasattr(config, "original_margin"):
        config.original_margin = calculate_position_margin(
            config.original_amount, config.leverage
        )

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
    if not config.take_profits or not tp_sl_data.get("take_profits"):
        return []

    mock_tp_sl_orders = []
    config.paper_tp_levels = []

    for i, tp_data in enumerate(tp_sl_data["take_profits"]):
        mock_order_id = f"paper_tp_{i+1}_{uuid.uuid4().hex[:6]}"
        mock_tp_sl_orders.append(mock_order_id)
        config.paper_tp_levels.append(
            {
                "order_id": mock_order_id,
                "level": i + 1,
                "price": tp_data["price"],
                "percentage": tp_data["percentage"],
                "allocation": tp_data["allocation"],
                "triggered": False,
            }
        )

    if config.stop_loss_percent > 0:
        sl_order_id = f"paper_sl_{uuid.uuid4().hex[:6]}"
        mock_tp_sl_orders.append(sl_order_id)
        config.paper_sl_data = {
            "order_id": sl_order_id,
            "price": tp_sl_data["stop_loss"]["price"],
            "percentage": config.stop_loss_percent,
            "triggered": False,
        }

    return mock_tp_sl_orders


def _place_real_tp_sl_orders(client, config, tp_sl_data, position_size):
    """Place real TP/SL orders on exchange."""
    if not config.take_profits or not tp_sl_data.get("take_profits"):
        return []

    tp_orders_to_place = []
    for tp_data in tp_sl_data["take_profits"]:
        tp_quantity = position_size * (tp_data["allocation"] / 100)
        tp_orders_to_place.append(
            {
                "price": tp_data["price"],
                "quantity": tp_quantity,
                "percentage": tp_data["percentage"],
                "allocation": tp_data["allocation"],
            }
        )

    sl_price = None
    if config.stop_loss_percent > 0 and tp_sl_data.get("stop_loss"):
        sl_price = str(tp_sl_data["stop_loss"]["price"])

    if config.entry_type == "limit":
        # Store TP/SL data to place later when order fills
        config.pending_tp_sl_data = {
            "take_profits": tp_orders_to_place,
            "stop_loss_price": sl_price,
        }
        logging.info(f"TP/SL orders configured to place when limit order fills")
        return []
    else:
        # Place TP/SL immediately for market orders
        return _execute_tp_sl_orders(
            client, config, tp_orders_to_place, sl_price, position_size
        )


def _execute_tp_sl_orders(client, config, tp_orders_to_place, sl_price, position_size):
    """Execute TP/SL orders on exchange."""
    from api.unified_exchange_client import OrderParameterAdapter

    client_type = type(client).__name__.lower()
    exchange_name = "toobit"
    if "lbank" in client_type:
        exchange_name = "lbank"
    elif "hyperliquid" in client_type:
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
        stop_loss_price=sl_price,
    )

    # Call exchange-specific TP/SL placement
    if "hyperliquid" in client_type:
        return client.place_multiple_tp_sl_orders(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            amount=unified_params["amount"],
            entry_price=float(config.entry_price),
            tp_levels=tp_orders_to_place,
        )
    elif "lbank" in client_type:
        return client.place_multiple_tp_sl_orders(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            total_quantity=str(unified_params["amount"]),
            take_profits=tp_orders_to_place,
            stop_loss_price=sl_price,
        )
    else:
        # ToobitClient
        return client.place_multiple_tp_sl_orders(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            total_quantity=str(unified_params["quantity"]),
            take_profits=tp_orders_to_place,
            stop_loss_price=sl_price,
        )


def _handle_paper_trading_balance(chat_id, config):
    """Handle paper trading balance management."""
    with paper_balances_lock:
        if chat_id not in user_paper_balances:
            user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
            logging.info(
                f"Paper Trading: Initialized balance of ${TradingConfig.DEFAULT_TRIAL_BALANCE:,.2f} for user {chat_id}"
            )

        # Check if user has sufficient paper balance
        if user_paper_balances[chat_id] < config.amount:
            return (
                jsonify(
                    {
                        "error": f"Insufficient paper trading balance. Available: ${user_paper_balances[chat_id]:,.2f}, Required: ${config.amount:,.2f}"
                    }
                ),
                400,
            )

    # Deduct margin from paper balance
    with paper_balances_lock:
        user_paper_balances[chat_id] -= config.amount
        logging.info(
            f"Paper Trading: Deducted ${config.amount:,.2f} margin. Remaining balance: ${user_paper_balances[chat_id]:,.2f}"
        )

    return None, None


def _log_trade_execution(chat_id, trade_id, config, is_paper_mode):
    """Trade execution logging removed - data tracked in database."""
    # Trade execution now tracked through database via trade configuration updates
    pass


def _create_execution_response(trade_id, config, is_paper_mode):
    """Create the final execution response."""
    trade_mode = "Paper Trade" if is_paper_mode else "Live Trade"

    if config.entry_type == "limit":
        message = f"{trade_mode} limit order placed successfully: {config.symbol} {config.side.upper()} at ${config.entry_price:.4f}. Will execute when market reaches this price."
    else:
        message = (
            f"{trade_mode} executed successfully: {config.symbol} {config.side.upper()}"
        )

    return jsonify(
        {
            "success": True,
            "message": message,
            "paper_mode": is_paper_mode,
            "trade": {
                "trade_id": trade_id,
                "symbol": config.symbol,
                "side": config.side,
                "amount": config.amount,
                "leverage": config.leverage,
                "entry_price": config.entry_price,
                "current_price": config.current_price,
                "position_margin": config.position_margin,
                "position_size": config.position_size,
                "status": config.status,
                "exchange_order_id": getattr(config, "exchange_order_id", None),
                "take_profits": config.take_profits,
                "stop_loss_percent": config.stop_loss_percent,
            },
        }
    )


def _handle_trade_execution_errors(e):
    """Handle trade execution errors with user-friendly messages."""
    error_str = str(e).lower()
    from api.error_handler import ErrorCategory, ErrorSeverity, TradingError

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
                "Close other positions to free up margin",
            ],
        )
        return jsonify(error.to_dict()), 400
    elif (
        "api key" in error_str
        or "unauthorized" in error_str
        or "authentication" in error_str
    ):
        error = TradingError(
            category=ErrorCategory.AUTHENTICATION_ERROR,
            severity=ErrorSeverity.HIGH,
            technical_message=str(e),
            user_message="Your API credentials are invalid or have expired.",
            suggestions=[
                "Check your API key and secret in Settings",
                "Verify your credentials are still active",
                "Make sure you're using the correct exchange",
                "Contact your exchange if the problem persists",
            ],
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
                "Refresh the symbol list",
            ],
        )
        return jsonify(error.to_dict()), 400
    else:
        return jsonify(handle_error(e, "executing trade")), 500


@app.route("/api/execute-trade", methods=["POST"])
def execute_trade():
    """Execute a trade configuration - refactored for better maintainability."""
    user_id = None
    try:
        data = request.get_json() or {}

        # Validate request data
        user_id, trade_id, error_response, status_code = (
            _validate_execute_trade_request(data)
        )
        if error_response:
            return error_response, status_code

        logging.info(f"Execute trade request: user_id={user_id}, trade_id={trade_id}")
        chat_id = int(user_id)

        # Get and validate trade configuration
        config, error_response, status_code = _get_and_validate_trade_config(
            chat_id, trade_id
        )
        if error_response:
            return error_response, status_code

        # At this point, config is guaranteed to not be None after validation
        if config is None:
            logging.error("Critical error: Config is None after validation")
            return jsonify({"error": "Internal configuration error"}), 500

        # Get current market price
        current_market_price = get_live_market_price(
            config.symbol, user_id=chat_id, prefer_exchange=True
        )

        # Set up exchange client and determine trading mode
        client, is_paper_mode, user_creds, error_response, status_code = (
            _setup_exchange_client_and_mode(chat_id, config)
        )
        if error_response:
            return error_response, status_code

        # Execute the trade order
        if is_paper_mode:
            execution_success = _handle_paper_trading_execution(config, chat_id)
        else:
            execution_success, error_response, status_code = (
                _execute_real_trading_order(client, config, current_market_price)
            )
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
                        logging.info(
                            f"Paper Trading: Simulated {len(mock_tp_sl_orders)} TP/SL orders with real-time monitoring"
                        )
                else:
                    tp_sl_orders = _place_real_tp_sl_orders(
                        client, config, tp_sl_data, position_size
                    )
                    config.exchange_tp_sl_orders = tp_sl_orders
                    if tp_sl_orders:
                        logging.info(
                            f"Placed {len(tp_sl_orders)} TP/SL orders on exchange"
                        )

            except Exception as e:
                logging.error(f"Failed to place TP/SL orders: {e}")
                # Continue execution - main position was successful

        logging.info(
            f"Trade executed: {config.symbol} {config.side} at ${config.entry_price} (entry type: {config.entry_type})"
        )

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


@app.route("/api/user-credentials")
@app.route("/api/credentials-status")
def get_user_credentials():
    """Get user API credentials status"""
    user_id = request.args.get("user_id")
    if not user_id or user_id == "undefined":
        user_id = Environment.DEFAULT_TEST_USER_ID  # Demo user

    try:
        # Use database session to prevent race conditions
        with db.session.begin():
            # Check enhanced cache first for user credentials
            cached_result = enhanced_cache.get_user_credentials(str(user_id))
            if cached_result:
                cached_creds, cache_info = cached_result
                # Query fresh data to avoid session binding errors in multi-worker environments
                user_creds = (
                    UserCredentials.query.filter_by(telegram_user_id=str(user_id))
                    .with_for_update()
                    .first()
                )
                # Retrieved credentials from cache - removed debug log for cleaner output
            else:
                # Cache miss - load from database with row-level locking
                user_creds = (
                    UserCredentials.query.filter_by(telegram_user_id=str(user_id))
                    .with_for_update()
                    .first()
                )
                # Update cache with fresh data
                if user_creds:
                    enhanced_cache.set_user_credentials(str(user_id), user_creds)
                    # Credentials cached - removed debug log for cleaner output

        if user_creds:
            api_key = user_creds.get_api_key()
            api_key_preview = (
                f"{api_key[:8]}...{api_key[-4:]}"
                if api_key and len(api_key) > 12
                else "****"
            )

            return jsonify(
                {
                    "has_credentials": user_creds.has_credentials(),
                    "exchange": user_creds.exchange_name,
                    "api_key_preview": api_key_preview,
                    "testnet_mode": user_creds.testnet_mode,
                    "supports_testnet": user_creds.exchange_name.lower()
                    != "toobit",  # Toobit doesn't support testnet
                    "is_active": user_creds.is_active,
                    "last_used": (
                        user_creds.last_used.isoformat()
                        if user_creds.last_used
                        else None
                    ),
                    "created_at": user_creds.created_at.isoformat(),
                }
            )
        else:
            return jsonify(
                {
                    "has_credentials": False,
                    "exchange": None,
                    "api_key_preview": None,
                    "testnet_mode": True,
                    "supports_testnet": True,  # Default to true for unknown exchanges
                    "is_active": False,
                    "last_used": None,
                    "created_at": None,
                }
            )
    except Exception as e:
        logging.error(f"Error getting user credentials: {str(e)}")
        return jsonify(handle_error(e, "getting user credentials")), 500


@app.route("/api/save-credentials", methods=["POST"])
def save_credentials():
    """Save user API credentials"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        user_id = data.get("user_id", Environment.DEFAULT_TEST_USER_ID)
        exchange = data.get("exchange", "toobit")
        api_key = (data.get("api_key") or "").strip()
        api_secret = (data.get("api_secret") or "").strip()
        passphrase = (data.get("passphrase") or "").strip()

        if not api_key or not api_secret:
            return (
                jsonify(
                    create_validation_error(
                        "API credentials",
                        "Both API key and secret are required",
                        "Valid API key and secret from your exchange",
                    )
                ),
                400,
            )

        if len(api_key) < 10 or len(api_secret) < 10:
            return (
                jsonify(
                    create_validation_error(
                        "API credentials",
                        "API credentials seem too short",
                        "API key and secret should be at least 10 characters",
                    )
                ),
                400,
            )

        # Get or create user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(user_id)
        ).first()
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
        if exchange.lower() == "toobit":
            user_creds.testnet_mode = False  # Toobit only supports mainnet
        elif "testnet_mode" in data:
            user_creds.testnet_mode = bool(data["testnet_mode"])
        else:
            # Default to live trading for better user experience
            user_creds.testnet_mode = False

        db.session.commit()

        # Invalidate cache to ensure fresh data on next request
        enhanced_cache.set_user_credentials(str(user_id), user_creds)
        # Credentials cache updated - removed debug log for cleaner output

        return jsonify(
            create_success_response(
                "Credentials saved successfully",
                {"exchange": exchange, "testnet_mode": user_creds.testnet_mode},
            )
        )

    except Exception as e:
        logging.error(f"Error saving credentials: {str(e)}")
        db.session.rollback()
        return jsonify(handle_error(e, "saving credentials")), 500


@app.route("/api/delete-credentials", methods=["POST"])
def delete_credentials():
    """Delete user API credentials"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        user_id = data.get("user_id", Environment.DEFAULT_TEST_USER_ID)

        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(user_id)
        ).first()
        if not user_creds:
            return jsonify({"error": "No credentials found"}), 404

        db.session.delete(user_creds)
        db.session.commit()

        # Invalidate cache after deletion
        enhanced_cache.invalidate_user_data(str(user_id))
        # Cache invalidated - removed debug log for cleaner output

        return jsonify({"success": True, "message": "Credentials deleted successfully"})

    except Exception as e:
        logging.error(f"Error deleting credentials: {str(e)}")
        db.session.rollback()
        return jsonify({"error": "Failed to delete credentials"}), 500


@app.route("/api/toggle-testnet", methods=["POST"])
def toggle_testnet():
    """Toggle between testnet and mainnet modes"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        user_id = data.get("user_id", Environment.DEFAULT_TEST_USER_ID)
        testnet_mode = bool(data.get("testnet_mode", False))

        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(user_id)
        ).first()
        if not user_creds:
            return (
                jsonify(
                    {"error": "No credentials found. Please set up API keys first."}
                ),
                404,
            )

        # Don't allow testnet mode for Toobit since it doesn't support it
        if user_creds.exchange_name.lower() == "toobit" and testnet_mode:
            return (
                jsonify(
                    {
                        "error": "Toobit exchange does not support testnet mode. Only live trading is available."
                    }
                ),
                400,
            )

        user_creds.testnet_mode = testnet_mode
        db.session.commit()

        # Update cache with modified credentials
        enhanced_cache.set_user_credentials(str(user_id), user_creds)
        # Updated credentials cache after testnet toggle - removed debug log for cleaner output

        mode_text = "testnet" if testnet_mode else "mainnet (REAL TRADING)"
        warning = ""
        if not testnet_mode:
            warning = "⚠️ WARNING: You are now in MAINNET mode. Real money will be used for trades!"

        return jsonify(
            {
                "success": True,
                "message": f"Successfully switched to {mode_text}",
                "testnet_mode": testnet_mode,
                "warning": warning,
            }
        )

    except Exception as e:
        logging.error(f"Error toggling testnet mode: {str(e)}")
        db.session.rollback()
        return jsonify({"error": "Failed to toggle testnet mode"}), 500


def _validate_close_trade_request(data):
    """Validate close trade request data."""
    user_id = data.get("user_id")
    trade_id = data.get("trade_id")

    if not user_id or not trade_id:
        return None, None, jsonify({"error": "User ID and trade ID required"}), 400

    try:
        chat_id = int(user_id)
    except ValueError:
        return None, None, jsonify({"error": "Invalid user ID format"}), 400

    if chat_id not in user_trade_configs or trade_id not in user_trade_configs[chat_id]:
        return None, None, jsonify({"error": "Trade not found"}), 404

    config = user_trade_configs[chat_id][trade_id]

    if config.status != "active":
        return None, None, jsonify({"error": "Trade is not active"}), 400

    return chat_id, config, None, None


def _determine_trading_mode_for_closure(chat_id, config):
    """Determine if this trade should be closed in paper or real trading mode."""
    # Get user credentials to determine if we're in paper mode or real trading
    user_creds = UserCredentials.query.filter_by(
        telegram_user_id=str(chat_id), is_active=True
    ).first()

    # Use centralized trading mode detection
    is_paper_mode = determine_trading_mode(chat_id)

    # Check multiple indicators to determine if this is a paper trade
    paper_indicators = [
        is_paper_mode,
        not user_creds,
        (user_creds and user_creds.testnet_mode),
        (user_creds and not user_creds.has_credentials()),
        str(getattr(config, "exchange_order_id", "")).startswith("paper_"),
        getattr(config, "paper_trading_mode", False),
        hasattr(config, "paper_tp_levels"),
        hasattr(config, "paper_sl_data"),
    ]

    is_paper_mode = any(paper_indicators)

    # Log detailed paper trading detection for debugging
    logging.info(f"[RENDER CLOSE DEBUG] Paper mode detection for {config.symbol}:")
    logging.info(
        f"  Manual paper mode: {user_paper_trading_preferences.get(chat_id, True)}"
    )
    logging.info(f"  Has credentials: {user_creds is not None}")
    logging.info(
        f"  Paper order ID: {str(getattr(config, 'exchange_order_id', '')).startswith('paper_')}"
    )
    logging.info(f"  Final determination: Paper mode = {is_paper_mode}")

    return is_paper_mode, user_creds


def _close_paper_trade(chat_id, trade_id, config):
    """Handle paper trade closure logic."""
    logging.info(
        f"[RENDER PAPER] Closing paper trade for user {chat_id}: {config.symbol} {config.side}"
    )
    logging.info(
        f"[RENDER PAPER] Config details - Status: {config.status}, PnL: {getattr(config, 'unrealized_pnl', 0)}"
    )

    try:
        # Calculate final P&L
        final_pnl = config.unrealized_pnl + getattr(config, "realized_pnl", 0.0)

        # Update paper balance
        current_balance = user_paper_balances.get(
            chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE
        )
        new_balance = current_balance + final_pnl
        with paper_balances_lock:
            user_paper_balances[chat_id] = new_balance
            logging.info(
                f"[RENDER PAPER] Updated paper balance: ${current_balance:.2f} + ${final_pnl:.2f} = ${new_balance:.2f}"
            )

        # Update trade configuration immediately
        config.status = "stopped"
        config.final_pnl = final_pnl
        config.closed_at = get_iran_time().isoformat()
        config.unrealized_pnl = 0.0

        # Save to database immediately for paper trades
        save_trade_to_db(chat_id, config)

        # Trade closure is now logged via database save_trade_to_db()
        logging.info(
            f"Paper trade closed: {trade_id} for user {chat_id} with final P&L: ${final_pnl:.2f}"
        )

        # Simulate cancelling paper TP/SL orders
        if hasattr(config, "exchange_tp_sl_orders") and config.exchange_tp_sl_orders:
            cancelled_orders = len(config.exchange_tp_sl_orders)
            logging.info(
                f"[RENDER PAPER] Simulated cancellation of {cancelled_orders} TP/SL orders in paper mode"
            )

        return jsonify(
            {
                "success": True,
                "message": "Paper trade closed successfully",
                "final_pnl": final_pnl,
                "paper_balance": new_balance,
            }
        )

    except Exception as paper_error:
        logging.error(
            f"[RENDER PAPER ERROR] Failed to close paper trade: {paper_error}"
        )
        return (
            jsonify(
                {
                    "error": f"Failed to close paper trade: {str(paper_error)}",
                    "paper_trading": True,
                }
            ),
            500,
        )


def _prepare_real_trade_closure(chat_id, config, user_creds):
    """Prepare for real trade closure by validating credentials and creating client."""
    # Verify credentials are available
    if not user_creds or not user_creds.has_credentials():
        return (
            None,
            jsonify({"error": "API credentials not available for live trading"}),
            400,
        )

    # Create exchange client (dynamic selection)
    client = create_exchange_client(user_creds, testnet=False)

    # Calculate position closure parameters
    close_side = "sell" if config.side == "long" else "buy"

    # Enhanced logging for debugging
    logging.info(
        f"[RENDER CLOSE] User {chat_id} attempting to close {config.symbol} {config.side} position"
    )
    logging.info(
        f"[RENDER CLOSE] Position size: {config.position_size}, Close side: {close_side}"
    )

    # Better position size handling for closure
    position_size = getattr(config, "position_size", config.amount)
    if not position_size or position_size <= 0:
        # Calculate position size from remaining amount and leverage
        position_size = config.amount * config.leverage
        logging.warning(
            f"[RENDER CLOSE] Calculated position size: {position_size} from amount: {config.amount} * leverage: {config.leverage}"
        )

    return (
        {"client": client, "close_side": close_side, "position_size": position_size},
        None,
        None,
    )


def _execute_exchange_closure(client, config, close_side, position_size):
    """Execute the actual trade closure on the exchange."""
    # Use Protocol-based unified interface
    from api.unified_exchange_client import OrderParameterAdapter

    client_type = type(client).__name__.lower()
    exchange_name = "toobit"  # Default
    if "lbank" in client_type:
        exchange_name = "lbank"
    elif "hyperliquid" in client_type:
        exchange_name = "hyperliquid"

    unified_params = OrderParameterAdapter.to_exchange_params(
        exchange_name,
        symbol=config.symbol,
        side=close_side,
        quantity=float(position_size),
        order_type="market",
    )

    if "lbank" in client_type or "hyperliquid" in client_type:
        close_order = client.place_order(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            amount=unified_params["amount"],
            order_type=unified_params["order_type"],
            reduce_only=True,
        )
    else:
        # ToobitClient
        close_order = client.place_order(
            symbol=unified_params["symbol"],
            side=unified_params["side"],
            quantity=str(unified_params["quantity"]),
            order_type=unified_params["order_type"],
            reduce_only=True,
        )

    if not close_order:
        # Get specific error from client if available
        error_detail = client.get_last_error()
        logging.error(f"[RENDER CLOSE FAILED] {error_detail}")

        return (
            None,
            jsonify(
                {
                    "success": False,
                    "error": f"Failed to close {config.symbol} position: {error_detail}",
                    "technical_details": error_detail,
                    "symbol": config.symbol,
                    "side": config.side,
                    "suggestion": "This might be a paper trade or the position may have already been closed. Please refresh and try again.",
                }
            ),
            400,
        )

    logging.info(f"Position closed on exchange: {close_order}")
    return close_order, None, None


def _cancel_remaining_tp_sl_orders(client, config):
    """Cancel any remaining TP/SL orders on the exchange."""
    if hasattr(config, "exchange_tp_sl_orders") and config.exchange_tp_sl_orders:
        for tp_sl_order in config.exchange_tp_sl_orders:
            order_id = tp_sl_order.get("order", {}).get("orderId")
            if order_id:
                try:
                    client.cancel_order(symbol=config.symbol, order_id=str(order_id))
                    logging.info(f"Cancelled TP/SL order: {order_id}")
                except Exception as cancel_error:
                    logging.warning(
                        f"Failed to cancel order {order_id}: {cancel_error}"
                    )


def _close_real_trade(chat_id, config, user_creds):
    """Handle real trade closure logic."""
    try:
        # Prepare closure parameters
        closure_data, error_response, status_code = _prepare_real_trade_closure(
            chat_id, config, user_creds
        )
        if error_response:
            return error_response, status_code

        # Execute exchange closure
        close_order, error_response, status_code = _execute_exchange_closure(
            closure_data["client"],
            config,
            closure_data["close_side"],
            closure_data["position_size"],
        )
        if error_response:
            return error_response, status_code

        # Cancel remaining TP/SL orders
        _cancel_remaining_tp_sl_orders(closure_data["client"], config)

        return None, None

    except Exception as e:
        logging.error(f"[RENDER CLOSE EXCEPTION] Exchange position closure failed: {e}")
        logging.error(
            f"[RENDER CLOSE EXCEPTION] Config details: {config.symbol} {config.side}, User: {chat_id}"
        )

        return (
            jsonify(
                {
                    "error": f"Exchange closure failed for {config.symbol}: {str(e)}",
                    "technical_details": str(e),
                    "symbol": config.symbol,
                    "side": config.side,
                    "suggestion": "Check your API credentials and try again. If the problem persists, contact support.",
                }
            ),
            500,
        )


def _finalize_trade_closure(chat_id, trade_id, config):
    """Finalize trade closure by updating status and logging."""
    # Update trade configuration
    final_pnl = config.unrealized_pnl + getattr(config, "realized_pnl", 0.0)
    config.status = "stopped"
    config.final_pnl = final_pnl
    config.closed_at = get_iran_time().isoformat()
    config.unrealized_pnl = 0.0

    # Save updated status to database
    save_trade_to_db(chat_id, config)

    # Trade closure is now logged via database save_trade_to_db()
    logging.info(
        f"Trade closed: {trade_id} for user {chat_id} with final P&L: ${final_pnl:.2f}"
    )

    return final_pnl


@app.route("/api/close-trade", methods=["POST"])
def close_trade():
    """Close an active trade"""
    try:
        data = request.get_json()

        # Validate request
        chat_id, config, error_response, status_code = _validate_close_trade_request(
            data
        )
        if error_response:
            return error_response, status_code

        trade_id = data.get("trade_id")

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

        return jsonify(
            {
                "success": True,
                "message": "Trade closed successfully",
                "final_pnl": final_pnl,
            }
        )

    except Exception as e:
        logging.error(f"Error closing trade: {str(e)}")
        return jsonify({"error": "Failed to close trade"}), 500


def _validate_close_all_trades_request(data):
    """Validate close all trades request data."""
    user_id = data.get("user_id")

    if not user_id:
        return None, jsonify({"error": "User ID required"}), 400

    try:
        chat_id = int(user_id)
    except ValueError:
        return None, jsonify({"error": "Invalid user ID format"}), 400

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
        telegram_user_id=str(chat_id), is_active=True
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
    logging.info(
        f"Closing paper trade {trade_id} for user {chat_id}: {config.symbol} {config.side}"
    )

    # Simulate cancelling paper TP/SL orders
    if hasattr(config, "exchange_tp_sl_orders") and config.exchange_tp_sl_orders:
        cancelled_orders = len(config.exchange_tp_sl_orders)
        logging.info(
            f"Simulated cancellation of {cancelled_orders} TP/SL orders for trade {trade_id} in paper mode"
        )


def _close_individual_real_trade(client, trade_id, config):
    """Close a single real trade on the exchange."""
    if client is None:
        logging.warning(
            f"No client available for trade {trade_id} - falling back to paper mode"
        )
        return False

    close_side = "sell" if config.side == "long" else "buy"
    close_order = client.place_order(
        symbol=config.symbol,
        side=close_side,
        order_type="market",
        quantity=str(config.position_size),
        reduce_only=True,
    )

    if close_order:
        logging.info(f"Position closed on exchange: {close_order}")

        # Cancel any remaining TP/SL orders on exchange
        if hasattr(config, "exchange_tp_sl_orders") and config.exchange_tp_sl_orders:
            for tp_sl_order in config.exchange_tp_sl_orders:
                order_id = tp_sl_order.get("order", {}).get("orderId")
                if order_id:
                    try:
                        client.cancel_order(
                            symbol=config.symbol, order_id=str(order_id)
                        )
                    except Exception as cancel_error:
                        logging.warning(
                            f"Failed to cancel order {order_id}: {cancel_error}"
                        )
        return True
    else:
        logging.warning(
            f"Failed to close position for trade {trade_id} - exchange order failed"
        )
        return False


def _finalize_individual_trade_closure(chat_id, trade_id, config):
    """Finalize closure of an individual trade."""
    # Update trade configuration
    final_pnl = config.unrealized_pnl + getattr(config, "realized_pnl", 0.0)
    config.status = "stopped"
    config.final_pnl = final_pnl
    config.closed_at = get_iran_time().isoformat()
    config.unrealized_pnl = 0.0

    # Save updated status to database
    save_trade_to_db(chat_id, config)

    # Trade closure is now logged via database save_trade_to_db()
    logging.info(
        f"Trade closed: {trade_id} for user {chat_id} with final P&L: ${final_pnl:.2f}"
    )

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


@app.route("/api/close-all-trades", methods=["POST"])
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
            return jsonify(
                {
                    "success": True,
                    "message": "No active trades to close",
                    "closed_count": 0,
                }
            )

        # Prepare for bulk closure
        is_paper_mode, client = _prepare_bulk_trade_closure(chat_id)

        # Process all trade closures
        closed_count, total_final_pnl = _process_bulk_trade_closures(
            chat_id, active_trades, is_paper_mode, client
        )

        return jsonify(
            {
                "success": True,
                "message": f"Successfully closed {closed_count} trades",
                "closed_count": closed_count,
                "total_final_pnl": total_final_pnl,
            }
        )

    except Exception as e:
        logging.error(f"Error closing all trades: {str(e)}")
        return jsonify({"error": "Failed to close all trades"}), 500


@app.route("/api/delete-trade", methods=["POST"])
def delete_trade():
    """Delete a trade configuration"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        trade_id = data.get("trade_id")

        if not user_id:
            return (
                jsonify(
                    create_validation_error(
                        "User ID", None, "A valid user ID is required"
                    )
                ),
                400,
            )

        if not trade_id:
            return (
                jsonify(
                    create_validation_error(
                        "Trade ID", None, "A valid trade ID is required"
                    )
                ),
                400,
            )

        chat_id = int(user_id)

        if (
            chat_id not in user_trade_configs
            or trade_id not in user_trade_configs[chat_id]
        ):
            from api.error_handler import ErrorCategory, ErrorSeverity, TradingError

            error = TradingError(
                category=ErrorCategory.VALIDATION_ERROR,
                severity=ErrorSeverity.MEDIUM,
                technical_message=f"Trade {trade_id} not found for user {chat_id}",
                user_message="The trade you're trying to delete was not found.",
                suggestions=[
                    "Check that the trade ID is correct",
                    "The trade may have already been deleted",
                    "Refresh the page to see current trades",
                ],
            )
            return jsonify(error.to_dict()), 404

        config = user_trade_configs[chat_id][trade_id]
        trade_name = (
            config.get_display_name()
            if hasattr(config, "get_display_name")
            else config.name
        )

        # Remove from database first
        delete_trade_from_db(chat_id, trade_id)

        # Remove from configurations with proper locking
        with trade_configs_lock:
            del user_trade_configs[chat_id][trade_id]

            # Remove from selected trade if it was selected
            if user_selected_trade.get(chat_id) == trade_id:
                if chat_id in user_selected_trade:
                    del user_selected_trade[chat_id]

        return jsonify(
            create_success_response(
                f'Trade configuration "{trade_name}" deleted successfully',
                {"trade_id": trade_id, "trade_name": trade_name},
            )
        )

    except Exception as e:
        # Handle specific database errors
        error_str = str(e).lower()
        from api.error_handler import ErrorCategory, ErrorSeverity, TradingError

        if "database" in error_str or "connection" in error_str:
            error = TradingError(
                category=ErrorCategory.DATABASE_ERROR,
                severity=ErrorSeverity.HIGH,
                technical_message=str(e),
                user_message="There was an issue accessing the database while deleting your trade.",
                suggestions=[
                    "Try again in a moment",
                    "Refresh the page to check if the trade was deleted",
                    "Contact support if this persists",
                ],
                retry_after=30,
            )
            return jsonify(error.to_dict()), 500
        else:
            return jsonify(handle_error(e, "deleting trade")), 500


@app.route("/api/reset-history", methods=["POST"])
def reset_trade_history():
    """Reset all trade history and P&L for a user (keeps credentials)"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")

        if not user_id:
            return jsonify({"error": "User ID is required"}), 400

        chat_id = int(user_id)

        # Initialize user environment
        initialize_user_environment(chat_id)

        # Clear all trade configurations and history
        with app.app_context():
            # Delete all trade configurations from database (correct field name)
            TradeConfiguration.query.filter_by(telegram_user_id=str(chat_id)).delete()

            # Reset user trading session (keeps credentials but resets balance)
            session = UserTradingSession.query.filter_by(
                telegram_user_id=str(chat_id)
            ).first()
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

        # Bot trades list removed - trade history managed through database

        # Clear any cached portfolio data manually using enhanced cache
        try:
            # Clear user data cache for this user
            enhanced_cache.invalidate_user_data(str(chat_id))
        except Exception:
            # Cache clearing failed, continue without clearing cache
            pass

        logging.info(f"Trade history reset successfully for user {chat_id}")

        return jsonify(
            {
                "success": True,
                "message": "Trade history and P&L reset successfully. Credentials preserved.",
            }
        )

    except Exception as e:
        logging.error(f"Error resetting trade history: {e}")
        return jsonify({"error": "Failed to reset trade history"}), 500


# The app now uses only Telegram WebApp interface


@app.route("/paper-balance", methods=["GET"])
def get_paper_balance():
    """Get current paper trading balance for user"""
    user_id = get_user_id_from_request()

    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400

    # Initialize balance if not exists
    with paper_balances_lock:
        if chat_id not in user_paper_balances:
            user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE

        paper_balance = user_paper_balances[chat_id]

    return jsonify(
        {
            "paper_balance": paper_balance,
            "initial_balance": TradingConfig.DEFAULT_TRIAL_BALANCE,
            "currency": "USDT",
            "timestamp": get_iran_time().isoformat(),
        }
    )


@app.route("/reset-paper-balance", methods=["POST"])
def reset_paper_balance():
    """Reset paper trading balance to initial amount"""
    user_id = get_user_id_from_request()

    try:
        chat_id = int(user_id)
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400

    # Reset to initial balance
    with paper_balances_lock:
        user_paper_balances[chat_id] = TradingConfig.DEFAULT_TRIAL_BALANCE
        new_balance = user_paper_balances[chat_id]

    return jsonify(
        {
            "success": True,
            "paper_balance": new_balance,
            "message": f"Paper trading balance reset to ${TradingConfig.DEFAULT_TRIAL_BALANCE:,.2f}",
            "timestamp": get_iran_time().isoformat(),
        }
    )


# ====================================================================
# WHITELIST MANAGEMENT API ENDPOINTS
# ====================================================================

@app.route("/api/request-access", methods=["POST"])
def request_access():
    """Allow users to request access to the bot"""
    try:
        # Get authenticated user ID from Telegram WebApp authentication
        user_id = get_authenticated_user_id()
        if not user_id:
            return jsonify({"success": False, "message": "Authentication required. Please access this app through Telegram."}), 401
        
        data = request.get_json() or {}
        username = data.get("username", "")
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        
        # Register user for whitelist
        result = register_user_for_whitelist(user_id, username, first_name, last_name)
        
        return jsonify({
            "success": result["status"] in ["registered", "already_approved"],
            "status": result["status"],
            "message": result["message"]
        })
        
    except Exception as e:
        logging.error(f"Error in request_access: {e}")
        return jsonify({"success": False, "message": "Internal server error"}), 500

@app.route("/api/whitelist/status")
def whitelist_status():
    """Get whitelist status for the current user"""
    try:
        user_id = get_user_id_from_request()
        
        # Get access wall message which contains status info
        access_data = get_access_wall_message(user_id)
        
        return jsonify({
            "success": True,
            "user_id": user_id,
            "status": access_data["status"],
            "title": access_data["title"],
            "message": access_data["message"],
            "show_request_button": access_data["show_request_button"],
            "is_whitelisted": is_user_whitelisted(user_id),
            "is_bot_owner": is_bot_owner(user_id),
            "whitelist_enabled": WHITELIST_ENABLED
        })
        
    except Exception as e:
        logging.error(f"Error in whitelist_status: {e}")
        return jsonify({"success": False, "message": "Internal server error"}), 500





# ====================================================================
# TELEGRAM WEBHOOK REMOVED - USING MINI APP ONLY
# ====================================================================
# Note: Telegram bot commands have been removed in favor of Telegram Mini App
# Telegram WebApp authentication is still preserved in this file


# ====================================================================
# BOT COMMAND HANDLERS REMOVED - MINI APP ONLY
# ====================================================================
# Note: All bot command handlers (_handle_basic_commands, _handle_price_command, 
# _handle_trade_commands, process_command, etc.) have been removed.
# The trading functionality is now available exclusively through the Telegram Mini App web interface.


# Note: show_credentials_status() and handle_api_text_input() functions removed
# as they were part of the bot command system. API credentials are now managed
# through the web interface.

# Note: start_api_setup() and start_api_update() functions removed
# as they were part of the bot command system. API credentials are now managed
# through the web interface.




# Enhanced caching system replaces basic price cache
# price_cache, cache_lock, and cache_ttl now handled by enhanced_cache
api_performance_metrics = {
    "binance": {
        "requests": 0,
        "successes": 0,
        "avg_response_time": 0,
        "last_success": None,
    },
    "coingecko": {
        "requests": 0,
        "successes": 0,
        "avg_response_time": 0,
        "last_success": None,
    },
    "cryptocompare": {
        "requests": 0,
        "successes": 0,
        "avg_response_time": 0,
        "last_success": None,
    },
}

# Thread pool for concurrent API requests
price_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="price_api")


def update_api_metrics(api_name, success, response_time):
    """Update API performance metrics"""
    metrics = api_performance_metrics[api_name]
    current_requests = metrics.get("requests", 0)
    metrics["requests"] = current_requests + 1
    if success:
        current_successes = metrics.get("successes", 0)
        metrics["successes"] = current_successes + 1
        metrics["last_success"] = datetime.utcnow()
        # Update rolling average response time
        current_avg = metrics.get("avg_response_time", 0)
        if current_avg == 0:
            metrics["avg_response_time"] = response_time
        else:
            metrics["avg_response_time"] = (current_avg * 0.8) + (response_time * 0.2)


def get_api_priority():
    """Get API priority based on performance metrics"""
    apis = []
    for api_name, metrics in api_performance_metrics.items():
        requests_count = metrics.get("requests", 0)
        if requests_count is not None and requests_count > 0:
            successes_count = metrics.get("successes", 0)
            avg_response_time = metrics.get("avg_response_time", 0)
            success_rate = successes_count / requests_count
            score = success_rate * 100 - (avg_response_time or 0)
            apis.append((api_name, score))
        else:
            apis.append((api_name, 50))  # Default score for untested APIs

    # Sort by score (higher is better)
    apis.sort(key=lambda x: x[1], reverse=True)
    return [api[0] for api in apis]


@with_circuit_breaker("binance_api", failure_threshold=3, recovery_timeout=30)
def fetch_binance_price(symbol):
    """Fetch price from Binance API with circuit breaker protection"""
    start_time = time.time()
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        response = requests.get(
            url, headers=headers, timeout=TimeConfig.FAST_API_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

        response_time = time.time() - start_time
        update_api_metrics("binance", True, response_time)

        price = float(data["price"])
        return price, "binance"
    except Exception as e:
        response_time = time.time() - start_time
        update_api_metrics("binance", False, response_time)
        raise e


@with_circuit_breaker("coingecko_api", failure_threshold=4, recovery_timeout=45)
def fetch_coingecko_price(symbol):
    """Fetch price from CoinGecko API with circuit breaker protection"""
    start_time = time.time()
    try:
        # Extended symbol mapping with more pairs
        symbol_map = {
            "BTCUSDT": "bitcoin",
            "ETHUSDT": "ethereum",
            "BNBUSDT": "binancecoin",
            "ADAUSDT": "cardano",
            "DOGEUSDT": "dogecoin",
            "SOLUSDT": "solana",
            "DOTUSDT": "polkadot",
            "LINKUSDT": "chainlink",
            "LTCUSDT": "litecoin",
            "MATICUSDT": "matic-network",
            "AVAXUSDT": "avalanche-2",
            "UNIUSDT": "uniswap",
            "XRPUSDT": "ripple",
            "ALGOUSDT": "algorand",
            "ATOMUSDT": "cosmos",
            "FTMUSDT": "fantom",
            "MANAUSDT": "decentraland",
            "SANDUSDT": "the-sandbox",
            "AXSUSDT": "axie-infinity",
            "CHZUSDT": "chiliz",
            "ENJUSDT": "enjincoin",
            "GMTUSDT": "stepn",
            "APTUSDT": "aptos",
            "NEARUSDT": "near",
        }

        coin_id = symbol_map.get(symbol)
        if not coin_id:
            raise Exception(f"Symbol {symbol} not supported by CoinGecko")

        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        response = requests.get(
            url, headers=headers, timeout=TimeConfig.EXTENDED_API_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

        response_time = time.time() - start_time
        update_api_metrics("coingecko", True, response_time)

        price = float(data[coin_id]["usd"])
        return price, "coingecko"
    except Exception as e:
        response_time = time.time() - start_time
        update_api_metrics("coingecko", False, response_time)
        raise e


@with_circuit_breaker("cryptocompare_api", failure_threshold=4, recovery_timeout=45)
def fetch_cryptocompare_price(symbol):
    """Fetch price from CryptoCompare API with circuit breaker protection"""
    start_time = time.time()
    try:
        base_symbol = symbol.replace("USDT", "").replace("BUSD", "").replace("USDC", "")
        url = (
            f"https://min-api.cryptocompare.com/data/price?fsym={base_symbol}&tsyms=USD"
        )
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        response = requests.get(
            url, headers=headers, timeout=TimeConfig.EXTENDED_API_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

        response_time = time.time() - start_time

        if "USD" not in data:
            raise Exception(f"USD price not available for {base_symbol}")

        update_api_metrics("cryptocompare", True, response_time)

        price = float(data["USD"])
        return price, "cryptocompare"
    except Exception as e:
        response_time = time.time() - start_time
        update_api_metrics("cryptocompare", False, response_time)
        raise e


@with_circuit_breaker("toobit_api", failure_threshold=3, recovery_timeout=60)
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
                telegram_user_id=str(user_id), is_active=True
            ).first()

            if user_creds and user_creds.has_credentials():
                client = create_exchange_client(user_creds, testnet=False)

                toobit_price = client.get_ticker_price(symbol)
                if toobit_price:
                    return toobit_price, "toobit"

        # Fallback: Create anonymous client for public market data
        # Use wrapped anonymous client that can handle multiple exchanges
        anonymous_client = create_wrapped_exchange_client(
            exchange_name="toobit", testnet=False
        )
        toobit_price = anonymous_client.get_ticker_price(symbol)
        if toobit_price:
            return toobit_price, "toobit"

        return None, None
    except Exception as e:
        logging.warning(f"Failed to get Toobit price for {symbol}: {e}")
        return None, None


@with_circuit_breaker("hyperliquid_api", failure_threshold=3, recovery_timeout=60)
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
                telegram_user_id=str(user_id), is_active=True
            ).first()

            if (
                user_creds
                and user_creds.has_credentials()
                and user_creds.exchange_name == "hyperliquid"
            ):
                client = create_exchange_client(user_creds, testnet=False)

                hyperliquid_price = client.get_ticker_price(symbol)
                if hyperliquid_price:
                    return hyperliquid_price, "hyperliquid"

        # Fallback: Create anonymous client for public market data
        # Use wrapped anonymous client that can handle multiple exchanges
        anonymous_client = create_wrapped_exchange_client(
            exchange_name="hyperliquid", testnet=False
        )
        hyperliquid_price = anonymous_client.get_ticker_price(symbol)
        if hyperliquid_price:
            return hyperliquid_price, "hyperliquid"

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
                telegram_user_id=str(user_id), is_active=True
            ).first()
        else:
            # We're running in a background thread, need app context
            with app.app_context():
                return UserCredentials.query.filter_by(
                    telegram_user_id=str(user_id), is_active=True
                ).first()
    except Exception as context_error:
        logging.debug(f"Database query failed due to context issue: {context_error}")
        return None


def _try_preferred_exchange(symbol, user_id, use_cache):
    """Try to get price from user's preferred exchange"""
    user_creds = _get_user_credentials_safe(user_id)

    if not (user_creds and user_creds.exchange_name):
        return None

    if user_creds.exchange_name == "hyperliquid":
        hyperliquid_price, source = get_hyperliquid_price(symbol, user_id)
        if hyperliquid_price:
            if use_cache:
                enhanced_cache.set_price(symbol, hyperliquid_price, "hyperliquid")
            logging.info(
                f"Retrieved live price for {symbol} from Hyperliquid exchange: ${hyperliquid_price}"
            )
            return hyperliquid_price
    else:
        # Default to Toobit for other exchanges (backward compatibility)
        toobit_price, source = get_toobit_price(symbol, user_id)
        if toobit_price:
            if use_cache:
                enhanced_cache.set_price(symbol, toobit_price, "toobit")
            logging.info(
                f"Retrieved live price for {symbol} from Toobit exchange: ${toobit_price}"
            )
            return toobit_price

    return None


def _try_fallback_exchange(symbol, user_id, use_cache):
    """Try fallback to Toobit if no user_id provided"""
    toobit_price, source = get_toobit_price(symbol, user_id)
    if toobit_price:
        if use_cache:
            enhanced_cache.set_price(symbol, toobit_price, "toobit")
        logging.info(
            f"Retrieved live price for {symbol} from Toobit exchange: ${toobit_price}"
        )
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
        "binance": fetch_binance_price,
        "coingecko": fetch_coingecko_price,
        "cryptocompare": fetch_cryptocompare_price,
    }

    # Try concurrent requests for faster response
    success_price, success_source = _try_concurrent_apis(
        symbol, api_priority, api_functions
    )

    # If concurrent requests failed, try remaining APIs sequentially
    if success_price is None:
        success_price, success_source = _try_sequential_apis(
            symbol, api_priority, api_functions
        )

    if success_price is None:
        # No emergency fallback needed - enhanced cache handles stale data automatically
        raise Exception(
            f"Unable to fetch live market price for {symbol} from any source"
        )

    # Cache the successful result using enhanced cache
    if use_cache and success_source:
        enhanced_cache.set_price(symbol, success_price, success_source)

    logging.info(
        f"Retrieved live price for {symbol} from {success_source}: ${success_price}"
    )
    return success_price


def _collect_symbols_for_batch_update():
    """Collect unique symbols and position configs for batch processing."""
    symbols_to_update = set()
    position_configs = []
    paper_trading_configs = []

    for uid, trades in user_trade_configs.items():
        for trade_id, config in trades.items():
            # Include both real and paper trading positions for monitoring
            if config.symbol and (
                config.status == "active"
                or config.status == "configured"
                or config.status == "pending"
            ):
                symbols_to_update.add(config.symbol)
                position_configs.append((uid, trade_id, config))

                # Track paper trading positions separately for enhanced monitoring
                if getattr(config, "paper_trading_mode", False):
                    paper_trading_configs.append((uid, trade_id, config))

    return symbols_to_update, position_configs, paper_trading_configs


def _batch_fetch_symbol_prices(symbols_to_update, user_id=None):
    """Batch fetch prices for all symbols concurrently."""
    symbol_prices = {}
    if symbols_to_update:
        futures = {}
        for symbol in symbols_to_update:
            # Prioritize Toobit exchange for accurate trading prices
            future = price_executor.submit(
                get_live_market_price, symbol, True, user_id, True
            )
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
                    symbol_prices[symbol] = cached_result[
                        0
                    ]  # Get price from cache result

    return symbol_prices


def _process_pending_limit_orders(user_id, trade_id, config):
    """Process pending limit orders for execution."""
    if (
        config.status == "pending"
        and config.entry_type == "limit"
        and config.entry_price > 0
    ):
        should_execute = False
        if config.side == "long":
            # Long limit (buy limit): executes when market drops to or below limit price
            should_execute = config.current_price <= config.entry_price
        elif config.side == "short":
            # Short limit (sell limit): executes when market rises to or above limit price
            should_execute = config.current_price >= config.entry_price

        if should_execute:
            # Execute the pending limit order
            config.status = "active"
            config.position_margin = calculate_position_margin(
                config.amount, config.leverage
            )
            config.position_value = config.amount * config.leverage
            config.position_size = config.position_value / config.entry_price
            config.unrealized_pnl = 0.0

            trading_mode = (
                "Paper" if getattr(config, "paper_trading_mode", False) else "Live"
            )
            logging.info(
                f"{trading_mode} Trading: Limit order executed: {config.symbol} {config.side} at ${config.entry_price} (market reached: ${config.current_price})"
            )

            # For paper trading, initialize TP/SL monitoring after limit order execution
            if getattr(config, "paper_trading_mode", False):
                initialize_paper_trading_monitoring(config)

            # Trade execution is now logged via database save_trade_to_db()
            logging.info(
                f"{trading_mode} trade executed: {trade_id} for user {user_id} - {config.symbol} {config.side} at ${config.entry_price}"
            )

            return True
    return False


def _process_stop_loss_monitoring(user_id, trade_id, config):
    """Process stop loss monitoring for active positions."""
    if not (
        config.status in ["active", "configured"]
        and config.entry_price
        and config.current_price
    ):
        return False

    stop_loss_triggered = False

    # Check break-even stop loss first
    if (
        hasattr(config, "breakeven_sl_triggered")
        and config.breakeven_sl_triggered
        and hasattr(config, "breakeven_sl_price")
    ):
        # Break-even stop loss - ensure small profit while preventing losses
        # Set buffer of 0.25% to account for fees and slippage (configurable)
        buffer_percentage = getattr(config, 'breakeven_buffer', 0.0025)  # 0.25% default
        
        if config.side == "long":
            # For long positions, set SL above entry price to ensure small profit
            breakeven_trigger_price = config.breakeven_sl_price * (1 + buffer_percentage)
            if config.current_price <= breakeven_trigger_price:
                stop_loss_triggered = True
                logging.warning(
                    f"BREAK-EVEN STOP-LOSS TRIGGERED: {config.symbol} {config.side} position for user {user_id} - Price ${config.current_price} <= Break-even profit trigger ${breakeven_trigger_price} (entry + {buffer_percentage*100:.2f}%)"
                )
        else:  # short
            # For short positions, set SL below entry price to ensure small profit
            breakeven_trigger_price = config.breakeven_sl_price * (1 - buffer_percentage)
            if config.current_price >= breakeven_trigger_price:
                stop_loss_triggered = True
                logging.warning(
                    f"BREAK-EVEN STOP-LOSS TRIGGERED: {config.symbol} {config.side} position for user {user_id} - Price ${config.current_price} >= Break-even profit trigger ${breakeven_trigger_price} (entry - {buffer_percentage*100:.2f}%)"
                )

    # Check regular stop loss if break-even not triggered
    elif config.stop_loss_percent > 0 and config.unrealized_pnl < 0:
        # Calculate current loss percentage based on position value (margin * leverage)
        # Add safety guards for division by zero
        if config.amount > 0 and config.leverage > 0:
            position_value = config.amount * config.leverage
            loss_percentage = abs(config.unrealized_pnl / position_value) * 100

            if loss_percentage >= config.stop_loss_percent:
                stop_loss_triggered = True
                logging.warning(
                    f"STOP-LOSS TRIGGERED: {config.symbol} {config.side} position for user {user_id} - Loss: {loss_percentage:.2f}% >= {config.stop_loss_percent}%"
                )

    if stop_loss_triggered:
        # Close the position
        config.status = "stopped"
        # Include both unrealized P&L and any realized P&L from partial TPs
        config.final_pnl = config.unrealized_pnl + getattr(config, "realized_pnl", 0.0)
        config.closed_at = get_iran_time().isoformat()
        config.unrealized_pnl = 0.0

        # Save to database
        save_trade_to_db(user_id, config)

        # Trade closure is now logged via database save_trade_to_db()
        logging.info(
            f"Stop loss triggered: {trade_id} for user {user_id} with final P&L: ${config.final_pnl:.2f}"
        )

        logging.info(
            f"Position auto-closed: {config.symbol} {config.side} - Final P&L: ${config.final_pnl:.2f}"
        )
        return True

    return False


def _update_position_pnl(config):
    """Update unrealized P&L for active positions."""
    if (
        config.status in ["active", "configured"]
        and config.entry_price
        and config.current_price
    ):
        config.unrealized_pnl = calculate_unrealized_pnl(
            config.entry_price,
            config.current_price,
            config.amount,
            config.leverage,
            config.side,
        )


def update_all_positions_with_live_data(user_id=None):
    """Enhanced batch update using Toobit exchange prices for accurate trading data - refactored for better maintainability"""
    # Collect symbols and position configurations for batch processing
    symbols_to_update, position_configs, paper_trading_configs = (
        _collect_symbols_for_batch_update()
    )

    # Batch fetch prices for all symbols concurrently
    symbol_prices = _batch_fetch_symbol_prices(symbols_to_update, user_id)

    # Update all positions with fetched prices using focused helper functions
    for user_id, trade_id, config in position_configs:
        if config.symbol in symbol_prices:
            try:
                config.current_price = symbol_prices[config.symbol]

                # PAPER TRADING: Enhanced monitoring for simulated trades
                if getattr(config, "paper_trading_mode", False):
                    process_paper_trading_position(user_id, trade_id, config)

                # Process pending limit orders for execution
                if _process_pending_limit_orders(user_id, trade_id, config):
                    continue  # Skip further processing if limit order was executed

                # Update P&L for non-paper trading positions
                if not getattr(config, "paper_trading_mode", False):
                    _update_position_pnl(config)

                    # Process stop loss monitoring
                    if _process_stop_loss_monitoring(user_id, trade_id, config):
                        continue  # Position was closed by stop loss

                    # Check take profit targets
                    elif _process_take_profit_monitoring(config, user_id, trade_id):
                        continue  # Position was closed by take profit

            except Exception as e:
                logging.warning(
                    f"Failed to update live data for {config.symbol} (user {user_id}): {e}"
                )
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

    result = {"take_profits": [], "stop_loss": {}}

    # Calculate actual margin used for this position
    actual_margin = calculate_position_margin(config.amount, config.leverage)

    # Calculate Take Profit levels with proper sequential allocation handling
    cumulative_allocation_closed = (
        0  # Track how much of original position has been closed
    )

    for i, tp in enumerate(config.take_profits or []):
        tp_percentage = tp.get("percentage", 0) if isinstance(tp, dict) else tp
        allocation = tp.get("allocation", 0) if isinstance(tp, dict) else 0

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
            original_margin = getattr(
                config, "original_margin", None
            ) or calculate_position_margin(
                getattr(config, "original_amount", config.amount), config.leverage
            )

            profit_amount = (tp_percentage / 100) * original_margin * (allocation / 100)

            # CORRECTED: Calculate position size to close based on allocation percentage of original position
            # The position size should be a fraction of the original position, not based on profit amount
            original_amount = getattr(config, "original_amount", config.amount)
            position_size_to_close = original_amount * (allocation / 100)

            # Validate the profit calculation matches expected profit for this allocation
            actual_price_movement = (
                abs(tp_price - config.entry_price) / config.entry_price
            )
            expected_profit = (
                actual_price_movement * config.leverage * position_size_to_close
            )

            # Double-check: ensure profit_amount aligns with position size calculation
            if (
                abs(expected_profit - profit_amount) > 0.01
            ):  # Allow small floating point differences
                logging.warning(
                    f"TP{i+1} profit calculation mismatch: expected {expected_profit}, calculated {profit_amount}"
                )
                # Use the position-based calculation as it's more reliable
                profit_amount = expected_profit

            result["take_profits"].append(
                {
                    "level": i + 1,
                    "percentage": tp_percentage,
                    "allocation": allocation,
                    "price": tp_price,
                    "profit_amount": profit_amount,
                    "position_size_to_close": position_size_to_close,
                }
            )

            # Track cumulative allocation for future sequential TP handling
            cumulative_allocation_closed += allocation

    # Calculate Stop Loss
    if hasattr(config, "breakeven_sl_triggered") and config.breakeven_sl_triggered:
        # Break-even stop loss - set to entry price
        sl_price = config.entry_price
        result["stop_loss"] = {
            "percentage": 0.0,  # 0% = break-even
            "price": sl_price,
            "loss_amount": 0.0,  # No loss at entry price
            "is_breakeven": True,
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

        result["stop_loss"] = {
            "percentage": config.stop_loss_percent,
            "price": sl_price,
            "loss_amount": loss_amount,
            "is_breakeven": False,
        }

    return result


def get_margin_summary(chat_id):
    """Get comprehensive margin summary for a user"""
    with trade_configs_lock:
        user_trades = user_trade_configs.get(chat_id, {})

    # Account totals - use paper trading balance
    with paper_balances_lock:
        initial_balance = user_paper_balances.get(
            chat_id, TradingConfig.DEFAULT_TRIAL_BALANCE
        )
    total_position_margin = 0.0
    total_unrealized_pnl = 0.0
    total_realized_pnl = 0.0

    # Calculate realized P&L from closed positions
    for config in user_trades.values():
        if (
            config.status == "stopped"
            and hasattr(config, "final_pnl")
            and config.final_pnl is not None
        ):
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
            config.position_margin = calculate_position_margin(
                config.amount, config.leverage
            )

            if config.entry_price and config.amount:
                # Calculate position details properly
                config.position_value = config.amount * config.leverage
                config.position_size = config.position_value / config.entry_price
                config.unrealized_pnl = calculate_unrealized_pnl(
                    config.entry_price,
                    config.current_price,
                    config.amount,
                    config.leverage,
                    config.side,
                )

            total_position_margin += config.position_margin
            total_unrealized_pnl += config.unrealized_pnl

    # Calculate account balance including realized P&L and unrealized P&L from active positions
    account_balance = initial_balance + total_realized_pnl + total_unrealized_pnl
    free_margin = account_balance - total_position_margin

    return {
        "account_balance": account_balance,
        "total_margin": total_position_margin,
        "free_margin": free_margin,
        "unrealized_pnl": total_unrealized_pnl,
        "realized_pnl": total_realized_pnl,
        "margin_level": (
            account_balance / total_position_margin * 100
            if total_position_margin > 0
            else 0
        ),
    }




def get_current_trade_config(chat_id):
    """Get the current trade configuration for a user"""
    if chat_id in user_selected_trade:
        trade_id = user_selected_trade[chat_id]
        if chat_id in user_trade_configs and trade_id in user_trade_configs[chat_id]:
            return user_trade_configs[chat_id][trade_id]
    return None




# Old Telegram bot formatting and handler functions removed - now using mini app interface


# Utility functions for mini-app

# ============================================================================
# OPTIMIZED TRADING SYSTEM - Exchange-Native Orders with Lightweight Monitoring
# ============================================================================


def _validate_paper_trading_data(config, user_id, trade_id):
    """Validate paper trading position data."""
    logging.debug(
        f"[RENDER PAPER DEBUG] Processing position for user {user_id}, trade {trade_id}"
    )

    if not config.entry_price or not config.current_price:
        logging.warning(
            f"[RENDER PAPER] Missing price data - Entry: {getattr(config, 'entry_price', None)}, Current: {getattr(config, 'current_price', None)}"
        )
        return False

    return True


def _calculate_paper_pnl(config):
    """Calculate unrealized P&L for paper trading position."""
    try:
        config.unrealized_pnl = calculate_unrealized_pnl(
            config.entry_price,
            config.current_price,
            config.amount,
            config.leverage,
            config.side,
        )
        logging.debug(
            f"[RENDER PAPER] P&L calculated: ${config.unrealized_pnl:.2f} for {config.symbol}"
        )
        return True
    except Exception as pnl_error:
        logging.error(f"[RENDER PAPER ERROR] Failed to calculate P&L: {pnl_error}")
        return False


def _check_paper_breakeven_stop_loss(config):
    """Check if break-even stop loss should be triggered."""
    if not (
        hasattr(config, "breakeven_sl_triggered") and config.breakeven_sl_triggered
    ):
        return False

    price_tolerance = 0.0001  # 0.01% tolerance for floating point precision

    if config.side == "long" and config.current_price <= (
        config.entry_price * (1 - price_tolerance)
    ):
        logging.info(
            f"BREAKEVEN SL TRIGGERED: {config.symbol} LONG - Current: ${config.current_price:.4f} <= Entry: ${config.entry_price:.4f}"
        )
        return True
    elif config.side == "short" and config.current_price >= (
        config.entry_price * (1 + price_tolerance)
    ):
        logging.info(
            f"BREAKEVEN SL TRIGGERED: {config.symbol} SHORT - Current: ${config.current_price:.4f} >= Entry: ${config.entry_price:.4f}"
        )
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
    if not (
        hasattr(config, "paper_sl_data")
        and not config.paper_sl_data.get("triggered", False)
    ):
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
    if not (hasattr(config, "paper_tp_levels") and config.unrealized_pnl > 0):
        return

    profit_percentage = (config.unrealized_pnl / config.amount) * 100

    # Check each TP level (in order)
    for i, tp_level in enumerate(config.paper_tp_levels):
        if (
            not tp_level.get("triggered", False)
            and profit_percentage >= tp_level["percentage"]
        ):
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
        if hasattr(config, "paper_tp_levels") and config.paper_tp_levels:
            first_tp = config.paper_tp_levels[0]
            if first_tp.get("triggered", False):
                breakeven_threshold = first_tp.get("percentage", 0)
            else:
                # TP1 not triggered yet, don't activate breakeven
                breakeven_threshold = 0
        elif hasattr(config, "take_profits") and config.take_profits:
            # Fallback to original TP configuration
            breakeven_threshold = config.take_profits[0].get("percentage", 0)

    return breakeven_threshold


def _check_paper_breakeven_trigger(config, user_id):
    """Check if break-even should be triggered."""
    if not (
        hasattr(config, "breakeven_after")
        and config.breakeven_after
        and not getattr(config, "breakeven_sl_triggered", False)
        and config.unrealized_pnl > 0
    ):
        return

    profit_percentage = (config.unrealized_pnl / config.amount) * 100
    breakeven_threshold = _calculate_breakeven_threshold(config)

    if breakeven_threshold > 0 and profit_percentage >= breakeven_threshold:
        config.breakeven_sl_triggered = True
        config.breakeven_sl_price = config.entry_price
        save_trade_to_db(user_id, config)
        logging.info(
            f"Paper Trading: Break-even triggered for {config.symbol} {config.side} at {profit_percentage:.2f}% profit - SL moved to entry price"
        )


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
        logging.error(
            f"[RENDER PAPER ERROR] Paper trading position processing failed for {getattr(config, 'symbol', 'unknown')}: {e}"
        )
        logging.error(
            f"[RENDER PAPER ERROR] Config status: {getattr(config, 'status', 'unknown')}"
        )
        logging.error(f"[RENDER PAPER ERROR] User ID: {user_id}, Trade ID: {trade_id}")
        import traceback

        logging.error(f"[RENDER PAPER ERROR] Traceback: {traceback.format_exc()}")


def execute_paper_stop_loss(user_id, trade_id, config):
    """Execute paper trading stop loss"""
    config.status = "stopped"
    config.final_pnl = config.unrealized_pnl + getattr(config, "realized_pnl", 0.0)
    config.closed_at = get_iran_time().isoformat()
    config.unrealized_pnl = 0.0

    # Mark SL as triggered
    if hasattr(config, "paper_sl_data"):
        config.paper_sl_data["triggered"] = True

    # Update paper trading balance
    if user_id in user_paper_balances:
        # Return margin plus final P&L to balance
        balance_change = config.amount + config.final_pnl
        with paper_balances_lock:
            user_paper_balances[user_id] += balance_change
            logging.info(
                f"Paper Trading: Balance updated +${balance_change:.2f}. New balance: ${user_paper_balances[user_id]:,.2f}"
            )

    save_trade_to_db(user_id, config)

    # Paper trade closure is now logged via database save_trade_to_db()
    logging.info(
        f"Paper trading stop loss triggered: {trade_id} for user {user_id} with final P&L: ${config.final_pnl:.2f}"
    )

    logging.info(
        f"Paper Trading: Stop loss triggered - {config.symbol} {config.side} closed with P&L: ${config.final_pnl:.2f}"
    )


def _execute_full_paper_tp_closure(user_id, trade_id, config, tp_level):
    """Handle full position closure (100% allocation)."""
    config.status = "stopped"
    config.final_pnl = config.unrealized_pnl + getattr(config, "realized_pnl", 0.0)
    config.closed_at = get_iran_time().isoformat()
    config.unrealized_pnl = 0.0

    # Mark TP as triggered
    tp_level["triggered"] = True

    save_trade_to_db(user_id, config)

    # Update paper trading balance
    if user_id in user_paper_balances:
        # Return margin plus final P&L to balance
        balance_change = config.amount + config.final_pnl
        with paper_balances_lock:
            user_paper_balances[user_id] += balance_change
            logging.info(
                f"Paper Trading: Balance updated +${balance_change:.2f}. New balance: ${user_paper_balances[user_id]:,.2f}"
            )

    # Paper trade closure is now logged via database save_trade_to_db()
    logging.info(
        f"Paper trading TP{tp_level['level']} triggered: {trade_id} for user {user_id} with final P&L: ${config.final_pnl:.2f}"
    )

    logging.info(
        f"Paper Trading: TP{tp_level['level']} triggered - {config.symbol} {config.side} closed with P&L: ${config.final_pnl:.2f}"
    )


def _prepare_partial_paper_tp_closure(config):
    """Prepare configuration for partial position closure."""
    # Store original amounts before any TP triggers to preserve correct calculations
    if not hasattr(config, "original_amount"):
        config.original_amount = config.amount
    if not hasattr(config, "original_margin"):
        config.original_margin = calculate_position_margin(
            config.original_amount, config.leverage
        )


def _calculate_partial_tp_profit(config, tp_index, allocation):
    """Calculate partial profit based on TP calculations and allocation."""
    # Use TP calculation data for accurate profit amounts
    tp_calculations = calculate_tp_sl_prices_and_amounts(config)
    current_tp_data = None
    for tp_calc in tp_calculations.get("take_profits", []):
        if tp_calc["level"] == tp_index + 1:
            current_tp_data = tp_calc
            break

    if current_tp_data:
        partial_pnl = current_tp_data["profit_amount"]
    else:
        # Fallback calculation
        partial_pnl = config.unrealized_pnl * (allocation / 100)

    return partial_pnl


def _update_partial_tp_position(config, allocation, partial_pnl, tp_level, tp_index):
    """Update position configuration after partial TP execution."""
    remaining_amount = config.amount * ((100 - allocation) / 100)

    # Update realized P&L
    if not hasattr(config, "realized_pnl"):
        config.realized_pnl = 0.0
    config.realized_pnl += partial_pnl

    # Update position with remaining amount
    config.amount = remaining_amount
    config.unrealized_pnl -= partial_pnl

    # Mark TP as triggered
    tp_level["triggered"] = True

    # Remove triggered TP from list safely
    if tp_index < len(config.take_profits):
        config.take_profits.pop(tp_index)
    else:
        # TP already removed, find and remove by level instead
        config.take_profits = [
            tp
            for tp in config.take_profits
            if not (isinstance(tp, dict) and tp.get("level") == tp_level.get("level"))
        ]


def _update_paper_balance_partial_tp(user_id, config, allocation, partial_pnl):
    """Update paper balance for partial TP closure."""
    if user_id in user_paper_balances:
        # Use original margin amount for correct balance calculation
        original_margin = getattr(
            config, "original_margin", config.original_amount / config.leverage
        )
        partial_margin_return = original_margin * (allocation / 100)
        balance_change = partial_margin_return + partial_pnl
        with paper_balances_lock:
            user_paper_balances[user_id] += balance_change
            logging.info(
                f"Paper Trading: Balance updated +${balance_change:.2f}. New balance: ${user_paper_balances[user_id]:,.2f}"
            )


def _log_partial_tp_closure(
    user_id, trade_id, config, tp_level, allocation, partial_pnl
):
    """Log partial TP closure to bot trades."""
    # Use original position amount for allocation calculation
    closed_amount = config.original_amount * (allocation / 100)
    # Partial paper trade closure is now logged via database save_trade_to_db()
    logging.info(
        f"Paper trading partial TP{tp_level['level']} triggered: {trade_id} for user {user_id} - {allocation}% allocation with partial P&L: ${partial_pnl:.2f}"
    )

    logging.info(
        f"Paper Trading: Partial TP{tp_level['level']} triggered - {config.symbol} {config.side} closed {allocation}% (${closed_amount:.2f}) for ${partial_pnl:.2f}"
    )


def _determine_breakeven_trigger_level(config):
    """Determine break-even trigger level from configuration."""
    if not (hasattr(config, "breakeven_after") and config.breakeven_after):
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
    if (
        breakeven_trigger_level
        and tp_level["level"] == breakeven_trigger_level
        and not getattr(config, "breakeven_sl_triggered", False)
    ):
        config.breakeven_sl_triggered = True
        config.breakeven_sl_price = config.entry_price
        save_trade_to_db(user_id, config)
        logging.info(
            f"Paper Trading: Auto break-even triggered after TP{breakeven_trigger_level} - SL moved to entry price {config.entry_price}"
        )
        logging.info(
            f"Paper Trading: Break-even breakeven_after value was: {config.breakeven_after} (type: {type(config.breakeven_after)})"
        )


def _execute_partial_paper_tp_closure(
    user_id, trade_id, config, tp_index, tp_level, allocation
):
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
    _log_partial_tp_closure(
        user_id, trade_id, config, tp_level, allocation, partial_pnl
    )

    # Handle break-even trigger
    _handle_breakeven_trigger(user_id, config, tp_level)


def execute_paper_take_profit(user_id, trade_id, config, tp_index, tp_level):
    """Execute paper trading take profit"""
    allocation = tp_level["allocation"]

    if allocation >= 100:
        # Full position close
        _execute_full_paper_tp_closure(user_id, trade_id, config, tp_level)
    else:
        # Partial close
        _execute_partial_paper_tp_closure(
            user_id, trade_id, config, tp_index, tp_level, allocation
        )


def initialize_paper_trading_monitoring(config):
    """Initialize paper trading monitoring after position opens"""
    if not getattr(config, "paper_trading_mode", False):
        return

    # Recalculate TP/SL data with actual entry price
    tp_sl_data = calculate_tp_sl_prices_and_amounts(config)

    # Update paper TP levels with actual prices
    if hasattr(config, "paper_tp_levels") and tp_sl_data.get("take_profits"):
        for i, (paper_tp, calc_tp) in enumerate(
            zip(config.paper_tp_levels, tp_sl_data["take_profits"])
        ):
            paper_tp["price"] = calc_tp["price"]

    # Update paper SL with actual price
    if hasattr(config, "paper_sl_data") and tp_sl_data.get("stop_loss"):
        config.paper_sl_data["price"] = tp_sl_data["stop_loss"]["price"]

    logging.info(
        f"Paper Trading: Monitoring initialized for {config.symbol} {config.side} with {len(getattr(config, 'paper_tp_levels', []))} TP levels"
    )


def _is_breakeven_enabled(config):
    """Check if break-even is enabled for a position."""
    if not (hasattr(config, "breakeven_after") and config.breakeven_after):
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
            logging.debug(
                f"Checking position {trade_id}: status={config.status}, symbol={config.symbol}, "
                f"breakeven_after={config.breakeven_after} (type: {type(config.breakeven_after)}), "
                f"breakeven_sl_triggered={getattr(config, 'breakeven_sl_triggered', 'not_set')}"
            )

            if config.status == "active":
                active_positions += 1

            # Only monitor active positions with break-even enabled and not yet triggered
            if (
                config.status == "active"
                and config.symbol
                and _is_breakeven_enabled(config)
                and not getattr(config, "breakeven_sl_triggered", False)
            ):
                symbols_needed.add(config.symbol)
                breakeven_positions.append((user_id, trade_id, config))

    # Enhanced debug logging
    logging.debug(
        f"Monitoring scan: {total_positions} total positions, {active_positions} active, {len(breakeven_positions)} need break-even monitoring"
    )

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
        config.entry_price,
        config.current_price,
        config.amount,
        config.leverage,
        config.side,
    )


def _should_trigger_breakeven(config, profit_percentage):
    """Check if break-even should trigger based on profit percentage."""
    return (
        isinstance(config.breakeven_after, (int, float))
        and profit_percentage >= config.breakeven_after
    )


def _execute_breakeven_trigger(user_id, config):
    """Execute break-even trigger by moving SL to entry price."""
    logging.info(
        f"BREAK-EVEN TRIGGERED: {config.symbol} {config.side} - Moving SL to entry price"
    )

    # Mark as triggered to stop monitoring
    config.breakeven_sl_triggered = True
    save_trade_to_db(user_id, config)

    # Move exchange SL to entry price
    try:
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(user_id)
        ).first()
        if user_creds and user_creds.has_credentials():
            client = create_exchange_client(user_creds, testnet=False)
            # Move stop loss to entry price (break-even)
            config.breakeven_sl_price = config.entry_price
            config.breakeven_sl_triggered = True
            logging.info(
                f"Break-even stop loss set to entry price: ${config.entry_price}"
            )
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
        logging.debug(
            "No positions need break-even monitoring - skipping lightweight update"
        )
        return

    logging.info(
        f"Lightweight monitoring: Only {len(breakeven_positions)} positions need break-even checks (vs {sum(len(trades) for trades in user_trade_configs.values())} total)"
    )

    # Fetch prices only for symbols that need break-even monitoring
    symbol_prices = _fetch_breakeven_symbol_prices(symbols_needed)

    # Process break-even monitoring
    _process_breakeven_monitoring(breakeven_positions, symbol_prices)


def place_exchange_native_orders(config, user_id):
    """Place all TP/SL orders directly on exchange after position opens"""
    try:
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(user_id)
        ).first()
        if not user_creds or not user_creds.has_credentials():
            logging.info(
                "No credentials found - skipping exchange-native orders (using paper mode)"
            )
            return False

        client = create_exchange_client(user_creds, testnet=False)

        # Calculate position size and prices
        position_size = config.amount * config.leverage

        # Prepare take profit orders
        tp_orders = []
        if config.take_profits:
            tp_calc = calculate_tp_sl_prices_and_amounts(config)
            for i, tp_data in enumerate(tp_calc.get("take_profits", [])):
                tp_quantity = position_size * (tp_data["allocation"] / 100)
                tp_orders.append(
                    {
                        "price": tp_data["price"],
                        "quantity": str(tp_quantity),
                        "percentage": tp_data["percentage"],
                        "allocation": tp_data["allocation"],
                    }
                )

        # Determine stop loss strategy
        sl_price = None
        trailing_stop = None

        # Check if trailing stop is enabled
        if hasattr(config, "trailing_stop_enabled") and config.trailing_stop_enabled:
            # Use exchange-native trailing stop instead of bot monitoring
            callback_rate = getattr(config, "trail_percentage", 1.0)  # Default 1%
            activation_price = getattr(config, "trail_activation_price", None)

            trailing_stop = {
                "callback_rate": callback_rate,
                "activation_price": activation_price,
            }
            logging.info(
                f"Using exchange-native trailing stop: {callback_rate}% callback"
            )

        elif config.stop_loss_percent > 0:
            # Use regular stop loss
            sl_calc = calculate_tp_sl_prices_and_amounts(config)
            sl_val = sl_calc.get("stop_loss", {}).get("price")
            sl_price = float(sl_val) if sl_val else None

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
            if "lbank" in client_type:
                exchange_name = "lbank"
            elif "hyperliquid" in client_type:
                exchange_name = "hyperliquid"

            unified_params = OrderParameterAdapter.to_exchange_params(
                exchange_name,
                symbol=config.symbol,
                side=config.side,
                total_quantity=float(position_size),
                entry_price=float(config.entry_price),
                take_profits=tp_orders,
                stop_loss_price=sl_price,
            )

            if "hyperliquid" in client_type:
                orders_placed = client.place_multiple_tp_sl_orders(
                    symbol=config.symbol,
                    side=config.side,
                    total_quantity=float(position_size),
                    take_profits=tp_orders,
                    stop_loss_price=sl_price if sl_price else None,
                )
            elif "lbank" in client_type:
                orders_placed = client.place_multiple_tp_sl_orders(
                    symbol=config.symbol,
                    side=config.side,
                    amount=float(position_size),
                    entry_price=float(config.entry_price),
                    tp_levels=tp_orders,
                    stop_loss_price=str(sl_price) if sl_price else None,
                )
            else:
                # ToobitClient
                orders_placed = client.place_multiple_tp_sl_orders(
                    symbol=config.symbol,
                    side=config.side,
                    amount=float(position_size),
                    entry_price=float(config.entry_price),
                    tp_levels=tp_orders,
                    stop_loss_price=str(sl_price) if sl_price else None,
                )

        logging.info(
            f"Placed {len(orders_placed)} exchange-native orders for {config.symbol}"
        )

        # If using trailing stop, no bot monitoring needed at all!
        if trailing_stop:
            logging.info(
                f"Exchange-native trailing stop active - NO bot monitoring required!"
            )

        return True

    except Exception as e:
        logging.error(f"Failed to place exchange-native orders: {e}")
        return False


# SMC Signal Cache Management Routes
@app.route("/api/smc-cache-status")
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
                "direction": entry.direction,
                "confidence": entry.confidence,
                "signal_strength": entry.signal_strength,
                "expires_at": entry.expires_at.isoformat(),
                "age_minutes": int(
                    (datetime.utcnow() - entry.created_at).total_seconds() / 60
                ),
            }

        return jsonify(
            {
                "total_cached_signals": total_signals,
                "active_signals": active_signals,
                "expired_signals": expired_signals,
                "cache_efficiency": (
                    f"{(active_signals / total_signals * 100):.1f}%"
                    if total_signals > 0
                    else "0%"
                ),
                "symbols_cached": symbols_data,
                "timestamp": get_iran_time().isoformat(),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/smc-cache-cleanup", methods=["POST"])
def cleanup_smc_cache():
    """Manually trigger cleanup of expired SMC signals"""
    try:
        from .models import SMCSignalCache

        expired_count = SMCSignalCache.cleanup_expired()

        return jsonify(
            {
                "success": True,
                "expired_signals_removed": expired_count,
                "timestamp": get_iran_time().isoformat(),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug/trading-status", methods=["GET"])
def debug_trading_status():
    """Enhanced diagnostic endpoint for troubleshooting trading issues"""
    try:
        user_id = request.args.get("user_id", Environment.DEFAULT_TEST_USER_ID)

        # Get user credentials
        user_creds = UserCredentials.query.filter_by(
            telegram_user_id=str(user_id)
        ).first()

        # Paper trading status
        is_paper_mode = determine_trading_mode(user_id)
        manual_paper_mode = user_paper_trading_preferences.get(int(user_id), True)

        # Diagnostic information
        diagnostic_info = {
            "user_id": user_id,
            "environment": {
                "is_replit": Environment.IS_REPLIT,
                "is_render": Environment.IS_RENDER,
                "is_vercel": Environment.IS_VERCEL,
                "database_type": (
                    (get_database_url() or "").split("://")[0]
                    if get_database_url()
                    else "none"
                ),
            },
            "credentials": {
                "has_credentials_in_db": bool(user_creds),
                "has_api_key": bool(user_creds and user_creds.api_key_encrypted),
                "has_api_secret": bool(user_creds and user_creds.api_secret_encrypted),
                "has_passphrase": bool(user_creds and user_creds.passphrase_encrypted),
                "testnet_mode": user_creds.testnet_mode if user_creds else None,
                "is_active": user_creds.is_active if user_creds else None,
                "exchange_name": user_creds.exchange_name if user_creds else None,
            },
            "trading_mode": {
                "manual_paper_mode": manual_paper_mode,
                "paper_balance": user_paper_balances.get(
                    user_id, TradingConfig.DEFAULT_TRIAL_BALANCE
                ),
                "would_use_paper_mode": (
                    manual_paper_mode
                    or not user_creds
                    or (user_creds and user_creds.testnet_mode)
                    or (user_creds and not user_creds.has_credentials())
                ),
            },
            "active_trades": len(user_trade_configs.get(user_id, {})),
            "toobit_connection": None,
        }

        # Test Toobit connection if credentials exist
        if user_creds and user_creds.has_credentials():
            try:
                client = create_exchange_client(user_creds, testnet=False)

                # Test connection
                balance_data = client.get_futures_balance()
                diagnostic_info["toobit_connection"] = {
                    "status": "success",
                    "has_balance_data": bool(balance_data),
                    "balance_fields": (
                        len(balance_data) if isinstance(balance_data, dict) else 0
                    ),
                    "last_error": getattr(client, "last_error", None),
                }

            except Exception as conn_error:
                diagnostic_info["toobit_connection"] = {
                    "status": "failed",
                    "error": str(conn_error),
                    "error_type": type(conn_error).__name__,
                }

        logging.info(
            f"[RENDER DEBUG] Trading status diagnostic for user {user_id}: {diagnostic_info}"
        )
        return jsonify(diagnostic_info)

    except Exception as e:
        logging.error(f"[RENDER DEBUG] Diagnostic failed: {e}")
        return jsonify({"error": f"Diagnostic failed: {str(e)}"}), 500


# ============================================================================
# WHITELIST MANAGEMENT API ENDPOINTS
# ============================================================================

@app.route("/api/whitelist/status")
def get_whitelist_status():
    """Get whitelist status for a user"""
    user_id = get_user_id_from_request()
    
    try:
        # Check if user is whitelisted
        is_whitelisted = is_user_whitelisted(user_id)
        is_owner = is_bot_owner(user_id)
        
        # Get detailed status message
        wall_message = get_access_wall_message(user_id)
        
        return jsonify({
            "success": True,
            "user_id": user_id,
            "is_whitelisted": is_whitelisted,
            "is_bot_owner": is_owner,
            "whitelist_enabled": WHITELIST_ENABLED,
            "wall_message": wall_message,
            "timestamp": get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error getting whitelist status for user {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/whitelist/request", methods=["POST"])
def request_whitelist_access():
    """Request access to the whitelist"""
    try:
        data = request.get_json()
        user_id = data.get("user_id") or get_user_id_from_request()
        username = data.get("username")
        first_name = data.get("first_name")
        last_name = data.get("last_name")
        
        # Register user for whitelist
        result = register_user_for_whitelist(user_id, username, first_name, last_name)
        
        return jsonify({
            "success": True,
            "result": result,
            "timestamp": get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error requesting whitelist access: {e}")
        return jsonify({"error": str(e)}), 500












if __name__ == "__main__":
    # This file is part of the main web application
    # Bot functionality is available via webhooks, no separate execution needed
    print("Note: This API module is part of the main web application.")
    print("Use 'python main.py' or the main workflow to start the application.")

# ============================================================================
# ADMIN AUTHENTICATION SYSTEM  
# ============================================================================

# Admin configuration from environment variables
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

# Security check for admin credentials
if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    logging.error("SECURITY WARNING: ADMIN_USERNAME and ADMIN_PASSWORD environment variables must be set!")
    logging.error("Using temporary defaults for development only - THIS IS NOT SECURE!")
    ADMIN_USERNAME = ADMIN_USERNAME or "admin"
    ADMIN_PASSWORD = ADMIN_PASSWORD or "temp_dev_password_123"

def admin_login_required(f):
    """
    Decorator for routes that require admin authentication.
    """
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated_function

def verify_admin_credentials(username: str, password: str) -> bool:
    """Verify admin credentials against environment variables."""
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

def establish_admin_session():
    """Establish admin session."""
    session.permanent = True
    session["admin_authenticated"] = True
    session["admin_username"] = ADMIN_USERNAME
    session["admin_login_time"] = int(time.time())

def clear_admin_session():
    """Clear admin session."""
    session.pop("admin_authenticated", None)
    session.pop("admin_username", None)
    session.pop("admin_login_time", None)

# ============================================================================
# ADMIN ROUTES
# ============================================================================

@app.route("/admin")
@admin_login_required
def admin_dashboard():
    """Admin dashboard for whitelist management"""
    return render_template("admin.html")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Admin login page"""
    if session.get("admin_authenticated"):
        return redirect(url_for("admin_dashboard"))
    
    if request.method == "POST":
        # Verify CSRF token
        csrf_token = request.form.get("csrf_token")
        if not csrf_token or not validate_csrf_token(csrf_token):
            logging.warning("Admin login attempt with invalid CSRF token")
            return render_template("admin_login.html", error="Invalid security token. Please try again.", csrf_token=generate_csrf_token())
        
        username = request.form.get("username")
        password = request.form.get("password")
        
        if username and password and verify_admin_credentials(username, password):
            establish_admin_session()
            logging.info(f"Admin login successful for user: {username}")
            return redirect(url_for("admin_dashboard"))
        else:
            logging.warning(f"Failed admin login attempt for user: {username}")
            return render_template("admin_login.html", error="Invalid credentials", csrf_token=generate_csrf_token())
    
    return render_template("admin_login.html", csrf_token=generate_csrf_token())

@app.route("/admin/logout")
def admin_logout():
    """Admin logout"""
    clear_admin_session()
    logging.info("Admin logged out")
    return redirect(url_for("admin_login"))

# ============================================================================
# ADMIN API ENDPOINTS
# ============================================================================

@app.route("/api/admin/whitelist/stats")
@admin_login_required
def admin_whitelist_stats():
    """Get whitelist statistics"""
    try:
        # Get counts by status
        pending_count = UserWhitelist.query.filter_by(status="pending").count()
        approved_count = UserWhitelist.query.filter_by(status="approved").count()
        rejected_count = UserWhitelist.query.filter_by(status="rejected").count()
        banned_count = UserWhitelist.query.filter_by(status="banned").count()
        total_count = UserWhitelist.query.count()
        
        return jsonify({
            "success": True,
            "stats": {
                "pending": pending_count,
                "approved": approved_count,
                "rejected": rejected_count,
                "banned": banned_count,
                "total": total_count
            },
            "timestamp": get_iran_time().isoformat()
        })
    except Exception as e:
        logging.error(f"Error getting whitelist stats: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/whitelist/users")
@admin_login_required  
def admin_whitelist_users():
    """Get all whitelist users"""
    try:
        users = UserWhitelist.query.order_by(UserWhitelist.requested_at.desc()).all()
        
        users_data = []
        for user in users:
            users_data.append({
                "id": user.id,
                "telegram_user_id": user.telegram_user_id,
                "telegram_username": user.telegram_username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "status": user.status,
                "reviewed_by": user.reviewed_by,
                "review_notes": user.review_notes,
                "requested_at": format_iran_time(user.requested_at) if user.requested_at else None,
                "reviewed_at": format_iran_time(user.reviewed_at) if user.reviewed_at else None,
                "last_access": format_iran_time(user.last_access) if user.last_access else None,
                "access_count": user.access_count or 0
            })
        
        return jsonify({
            "success": True,
            "users": users_data,
            "timestamp": get_iran_time().isoformat()
        })
    except Exception as e:
        logging.error(f"Error getting whitelist users: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/whitelist/approve", methods=["POST"])
@admin_login_required
def admin_approve_user():
    """Approve a user"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        notes = data.get("notes", "")
        
        user = UserWhitelist.query.filter_by(telegram_user_id=str(user_id)).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Approve user
        admin_username = session.get("admin_username", "admin")
        user.approve(admin_username, notes)
        db.session.commit()
        
        logging.info(f"User {user_id} approved by admin {admin_username}")
        
        return jsonify({
            "success": True,
            "message": f"User {user_id} approved successfully",
            "timestamp": get_iran_time().isoformat()
        })
    except Exception as e:
        logging.error(f"Error approving user: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/whitelist/reject", methods=["POST"])
@admin_login_required
def admin_reject_user():
    """Reject a user"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        notes = data.get("notes", "")
        
        user = UserWhitelist.query.filter_by(telegram_user_id=str(user_id)).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Reject user
        admin_username = session.get("admin_username", "admin")
        user.reject(admin_username, notes)
        db.session.commit()
        
        logging.info(f"User {user_id} rejected by admin {admin_username}")
        
        return jsonify({
            "success": True,
            "message": f"User {user_id} rejected successfully",
            "timestamp": get_iran_time().isoformat()
        })
    except Exception as e:
        logging.error(f"Error rejecting user: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/whitelist/ban", methods=["POST"])
@admin_login_required
def admin_ban_user():
    """Ban a user"""
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        notes = data.get("notes", "")
        
        user = UserWhitelist.query.filter_by(telegram_user_id=str(user_id)).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Ban user
        admin_username = session.get("admin_username", "admin")
        user.ban(admin_username, notes)
        db.session.commit()
        
        logging.info(f"User {user_id} banned by admin {admin_username}")
        
        return jsonify({
            "success": True,
            "message": f"User {user_id} banned successfully",
            "timestamp": get_iran_time().isoformat()
        })
    except Exception as e:
        logging.error(f"Error banning user: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# DATABASE MANAGEMENT ROUTES
@app.route("/api/admin/database/stats")
@admin_login_required
def admin_database_stats():
    """Get database statistics and table information"""
    try:
        from sqlalchemy import inspect, text
        
        stats = {}
        
        # Get all table names and counts
        tables = {
            'UserCredentials': UserCredentials,
            'UserTradingSession': UserTradingSession, 
            'TradeConfiguration': TradeConfiguration,
            'UserWhitelist': UserWhitelist,
            'SMCSignalCache': 'smc_signal_cache',  # Direct table name
            'KlinesCache': 'klines_cache'          # Direct table name
        }
        
        for table_name, model_or_table in tables.items():
            try:
                if hasattr(model_or_table, 'query'):  # It's a model
                    count = db.session.query(model_or_table).count()
                    stats[table_name] = {
                        'count': count,
                        'status': 'healthy'
                    }
                else:
                    # It's a table name string, query directly
                    result = db.session.execute(text(f"SELECT COUNT(*) FROM {model_or_table};"))
                    count = result.scalar()
                    stats[table_name] = {
                        'count': count,
                        'status': 'healthy'
                    }
            except Exception as e:
                stats[table_name] = {
                    'count': 0,
                    'status': f'error: {str(e)}'
                }
        
        # Cache cleanup worker status
        from .unified_data_sync_service import get_unified_service_status
        worker_status = get_unified_service_status()
        
        # Get actual cache sizes from enhanced cache
        cache_stats = enhanced_cache.get_cache_stats()
        total_cache_items = sum(cache_stats['cache_sizes'].values())
        
        cache_status = {
            'enabled': worker_status.get('service_running', False),
            'last_cleanup': worker_status.get('last_cache_cleanup', 'Never'),
            'cache_size': total_cache_items,
            'thread_alive': worker_status.get('service_running', False),
            'details': cache_stats['cache_sizes']
        }
        
        # Database connection status
        try:
            db.session.execute(text("SELECT 1"))
            db_status = 'healthy'
        except Exception as e:
            db_status = f'error: {str(e)}'
        
        return jsonify({
            'success': True,
            'stats': {
                'tables': stats,
                'cache': cache_status,
                'database_status': db_status,
                'timestamp': get_iran_time().isoformat()
            }
        })
    except Exception as e:
        logging.error(f"Error getting database stats: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/database/table/<table_name>")
@admin_login_required
def admin_view_table(table_name):
    """View table contents"""
    try:
        from sqlalchemy import text, inspect
        
        # Security: Only allow predefined tables
        allowed_tables = {
            'usercredentials': 'user_credentials',
            'usertradingsession': 'user_trading_sessions', 
            'tradeconfiguration': 'trade_configurations',
            'userwhitelist': 'user_whitelist',
            'smcsignalcache': 'smc_signal_cache',
            'klinescache': 'klines_cache'
        }
        
        if table_name.lower() not in allowed_tables:
            return jsonify({"error": "Table not allowed"}), 403
            
        actual_table = allowed_tables[table_name.lower()]
        limit = min(int(request.args.get('limit', 50)), 100)  # Max 100 records
        offset = int(request.args.get('offset', 0))
        
        # Check if table exists first
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()
        
        if actual_table not in existing_tables:
            return jsonify({
                "error": f"Table '{actual_table}' does not exist in database",
                "suggestion": "Use the database migration function to create missing tables"
            }), 404
        
        # Get table structure
        try:
            columns = [col['name'] for col in inspector.get_columns(actual_table)]
        except Exception:
            # Fallback - try to get from a sample query
            result = db.session.execute(text(f"SELECT * FROM {actual_table} LIMIT 1"))
            columns = list(result.keys()) if result.rowcount > 0 else []
        
        # Get records
        query = f"SELECT * FROM {actual_table} ORDER BY id DESC LIMIT :limit OFFSET :offset"
        result = db.session.execute(text(query), {'limit': limit, 'offset': offset})
        
        records = []
        for row in result:
            record = {}
            for i, col in enumerate(columns):
                value = row[i] if i < len(row) else None
                # Mask sensitive data
                if col.lower() in ['api_key_encrypted', 'api_secret_encrypted', 'passphrase_encrypted']:
                    record[col] = '***ENCRYPTED***' if value else None
                else:
                    record[col] = str(value) if value is not None else None
            records.append(record)
        
        # Get total count
        count_result = db.session.execute(text(f"SELECT COUNT(*) FROM {actual_table}"))
        total_count = count_result.scalar()
        
        return jsonify({
            'success': True,
            'data': {
                'table_name': actual_table,
                'columns': columns,
                'records': records,
                'total_count': total_count,
                'limit': limit,
                'offset': offset,
                'timestamp': get_iran_time().isoformat()
            }
        })
    except Exception as e:
        logging.error(f"Error viewing table {table_name}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/database/cache/clear", methods=["POST"])
@admin_login_required
def admin_clear_cache():
    """Clear application cache"""
    try:
        data = request.get_json() or {}
        cache_type = data.get('cache_type', 'all')
        
        cleared_caches = []
        
        if cache_type in ['all', 'enhanced']:
            # Clear enhanced cache
            if hasattr(enhanced_cache, 'clear'):
                enhanced_cache.clear()
                cleared_caches.append('enhanced_cache')
            elif hasattr(enhanced_cache, '_cache'):
                enhanced_cache._cache.clear()
                cleared_caches.append('enhanced_cache')
        
        if cache_type in ['all', 'smc']:
            # Clear SMC cache using proper model methods
            try:
                from .models import SMCSignalCache
                # Clear expired entries first
                expired_count = SMCSignalCache.cleanup_expired()
                # Clear remaining entries if requested
                total_count = db.session.query(SMCSignalCache).count()
                if total_count > 0:
                    db.session.query(SMCSignalCache).delete()
                    db.session.commit()
                    cleared_caches.append(f'smc_signals ({total_count} total, {expired_count} expired)')
                else:
                    cleared_caches.append(f'smc_signals ({expired_count} expired, already clean)')
            except Exception as e:
                logging.warning(f"Could not clear SMC cache: {e}")
                db.session.rollback()
        
        if cache_type in ['all', 'klines']:
            # Clear all klines cache entries
            try:
                from .models import KlinesCache
                # Get current count before clearing
                total_count = db.session.query(KlinesCache).count()
                if total_count > 0:
                    # Delete all klines cache entries
                    db.session.query(KlinesCache).delete()
                    db.session.commit()
                    cleared_caches.append(f'klines_cache ({total_count} entries)')
                else:
                    cleared_caches.append('klines_cache (already empty)')
            except Exception as e:
                logging.warning(f"Could not clear klines cache: {e}")
                db.session.rollback()
        
        admin_username = session.get("admin_username", "admin")
        logging.info(f"Cache cleared by admin {admin_username}: {cleared_caches}")
        
        return jsonify({
            'success': True,
            'message': f"Cleared caches: {', '.join(cleared_caches)}",
            'cleared_caches': cleared_caches,
            'timestamp': get_iran_time().isoformat()
        })
    except Exception as e:
        logging.error(f"Error clearing cache: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/database/cleanup-worker/status")
@admin_login_required
def admin_cleanup_worker_status():
    """Get cache cleanup worker status (unified service)"""
    try:
        from .unified_data_sync_service import get_unified_service_status
        
        # Get unified service status
        unified_status = get_unified_service_status()
        
        # Get cache statistics from enhanced cache
        cache_stats = enhanced_cache.get_cache_stats()
        total_cache_items = sum(cache_stats['cache_sizes'].values())
        
        # Check for active threads related to unified service
        import threading
        active_threads = [t.name for t in threading.enumerate() if 'unified' in t.name.lower() or 'sync' in t.name.lower()]
        
        # Get database cache counts for better monitoring
        try:
            from .models import SMCSignalCache, KlinesCache
            smc_signals_count = db.session.query(SMCSignalCache).count()
            klines_cache_count = db.session.query(KlinesCache).count()
        except Exception as e:
            logging.debug(f"Could not get cache counts: {e}")
            smc_signals_count = 0
            klines_cache_count = 0
        
        # Transform unified service status to match expected admin format
        status = {
            'enabled': unified_status.get('service_running', False),
            'running': unified_status.get('service_running', False),
            'last_run': unified_status.get('last_cache_cleanup', 'never'),
            'cache_size': total_cache_items,
            'worker_thread': active_threads[0] if active_threads else 'UnifiedDataSyncService',
            'smc_signals_count': smc_signals_count,
            'klines_cache_count': klines_cache_count,
            'service_type': 'unified',
            'klines_tracking': unified_status.get('klines_tracking', {}),
            'cache_statistics': unified_status.get('cache_statistics', {})
        }
        
        return jsonify({
            'success': True,
            'status': status,
            'timestamp': get_iran_time().isoformat()
        })
    except Exception as e:
        logging.error(f"Error getting cleanup worker status: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/database/health")
@admin_login_required
def admin_database_health():
    """Get comprehensive database health check"""
    try:
        from sqlalchemy import text
        import psutil
        import time
        
        health = {
            'overall_status': 'healthy',
            'checks': {},
            'recommendations': []
        }
        
        # Database connection test
        try:
            start_time = time.time()
            db.session.execute(text("SELECT 1"))
            connection_time = time.time() - start_time
            health['checks']['database_connection'] = {
                'status': 'healthy',
                'response_time_ms': round(connection_time * 1000, 2)
            }
        except Exception as e:
            health['checks']['database_connection'] = {
                'status': 'error',
                'error': str(e)
            }
            health['overall_status'] = 'unhealthy'
        
        # Cache status
        cache_size = len(enhanced_cache._cache) if hasattr(enhanced_cache, '_cache') else 0
        health['checks']['cache_system'] = {
            'status': 'healthy' if cache_size < 1000 else 'warning',
            'cache_size': cache_size
        }
        
        if cache_size > 500:
            health['recommendations'].append("Consider clearing cache - size is getting large")
        
        # Memory usage
        try:
            process = psutil.Process()
            memory_mb = process.memory_info().rss / 1024 / 1024
            health['checks']['memory_usage'] = {
                'status': 'healthy' if memory_mb < 512 else 'warning',
                'memory_mb': round(memory_mb, 2)
            }
            
            if memory_mb > 400:
                health['recommendations'].append("High memory usage detected - consider restarting")
        except Exception:
            health['checks']['memory_usage'] = {'status': 'unknown'}
        
        # Table integrity
        table_issues = []
        try:
            # Check for orphaned records or inconsistencies
            result = db.session.execute(text("""
                SELECT 
                    COUNT(*) as orphaned_sessions
                FROM user_trading_sessions uts 
                WHERE uts.telegram_user_id NOT IN (
                    SELECT DISTINCT telegram_user_id FROM user_credentials
                )
            """))
            orphaned_sessions = result.scalar()
            
            if orphaned_sessions and orphaned_sessions > 0:
                table_issues.append(f"{orphaned_sessions} orphaned trading sessions")
                
        except Exception:
            pass
            
        health['checks']['table_integrity'] = {
            'status': 'healthy' if not table_issues else 'warning',
            'issues': table_issues
        }
        
        return jsonify({
            'success': True,
            'health': health,
            'timestamp': get_iran_time().isoformat()
        })
    except Exception as e:
        logging.error(f"Error getting database health: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/database/migrate", methods=["POST"])
@admin_login_required
def admin_database_migrate():
    """Migrate/update database schema to match current models"""
    try:
        from sqlalchemy import text
        
        migration_results = []
        admin_username = session.get("admin_username", "admin")
        
        # Drop and recreate all tables to match current models
        # This is for development - in production you'd want proper migrations
        try:
            # Get current tables
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            existing_tables = inspector.get_table_names()
            
            migration_results.append(f"Found {len(existing_tables)} existing tables")
            
            # Drop all tables except alembic version table (if it exists)
            for table_name in existing_tables:
                if table_name != 'alembic_version':  # Preserve migration history if exists
                    db.session.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
                    migration_results.append(f"Dropped table: {table_name}")
            
            db.session.commit()
            migration_results.append("All existing tables dropped successfully")
            
            # Recreate all tables based on current models
            db.create_all()
            migration_results.append("All tables recreated from current models")
            
            # Verify table creation
            new_inspector = inspect(db.engine)
            new_tables = new_inspector.get_table_names()
            migration_results.append(f"Created {len(new_tables)} tables: {', '.join(new_tables)}")
            
            logging.info(f"Database migration completed by admin {admin_username}")
            
            return jsonify({
                'success': True,
                'message': 'Database schema updated successfully',
                'results': migration_results,
                'tables_created': new_tables,
                'timestamp': get_iran_time().isoformat()
            })
            
        except Exception as e:
            db.session.rollback()
            if 'migration_results' in locals():
                migration_results.append(f"Migration failed: {str(e)}")
            else:
                migration_results = [f"Migration failed: {str(e)}"]
            raise e
            
    except Exception as e:
        logging.error(f"Database migration failed: {e}")
        db.session.rollback()
        return jsonify({
            "error": str(e),
            "results": migration_results if 'migration_results' in locals() else [],
            "suggestion": "Check logs for detailed error information"
        }), 500


@app.route("/api/admin/database/backup", methods=["POST"])
@admin_login_required  
def admin_database_backup():
    """Create a simple data backup before migration (development only)"""
    try:
        from sqlalchemy import text
        import json
        
        backup_data = {}
        admin_username = session.get("admin_username", "admin")
        
        # Simple backup - export data as JSON for small development databases
        tables_to_backup = ['user_credentials', 'user_whitelist', 'trade_configurations']
        
        for table_name in tables_to_backup:
            try:
                result = db.session.execute(text(f"SELECT * FROM {table_name}"))
                rows = []
                for row in result:
                    # Convert row to dict, handling various data types
                    row_dict = {}
                    for i, col in enumerate(result.keys()):
                        value = row[i]
                        # Convert datetime and other non-JSON serializable types to string
                        if hasattr(value, 'isoformat'):
                            value = value.isoformat()
                        elif value is not None:
                            value = str(value)
                        row_dict[col] = value
                    rows.append(row_dict)
                
                backup_data[table_name] = rows
                
            except Exception as e:
                backup_data[table_name] = f"Error backing up table: {str(e)}"
        
        logging.info(f"Database backup created by admin {admin_username}")
        
        return jsonify({
            'success': True,
            'message': f'Backup created for {len(backup_data)} tables',
            'backup_summary': {table: len(data) if isinstance(data, list) else data 
                             for table, data in backup_data.items()},
            'timestamp': get_iran_time().isoformat(),
            'note': 'This is a simple development backup - use proper database backups for production'
        })
        
    except Exception as e:
        logging.error(f"Database backup failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/database/cache/restart-worker", methods=["POST"])
@admin_login_required  
def admin_restart_cache_worker():
    """Restart the cache cleanup worker"""
    try:
        from .unified_data_sync_service import restart_unified_data_sync_service
        
        admin_username = session.get("admin_username", "admin")
        
        # Restart the unified data sync service
        success = restart_unified_data_sync_service(app)
        
        if success:
            logging.info(f"Cache cleanup worker restarted by admin {admin_username}")
            
            return jsonify({
                'success': True,
                'message': 'Cache cleanup worker restarted successfully',
                'timestamp': get_iran_time().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to restart cache cleanup worker'
            }), 500
            
    except Exception as e:
        logging.error(f"Error restarting cache cleanup worker: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/database/smc/clear", methods=["POST"])
@admin_login_required
def admin_clear_smc_signals():
    """Clear SMC signals from database specifically"""
    try:
        from .models import SMCSignalCache
        
        admin_username = session.get("admin_username", "admin")
        
        # Get current counts before clearing
        total_count_before = db.session.query(SMCSignalCache).count()
        
        # Clear expired entries first
        expired_count = SMCSignalCache.cleanup_expired()
        
        # Get remaining count after expired cleanup
        total_count_after_expired = db.session.query(SMCSignalCache).count()
        
        # Clear all remaining SMC signals
        remaining_cleared = 0
        if total_count_after_expired > 0:
            remaining_cleared = total_count_after_expired
            db.session.query(SMCSignalCache).delete()
            db.session.commit()
        
        total_cleared = expired_count + remaining_cleared
        
        logging.info(f"SMC signals cleared by admin {admin_username}: {total_cleared} total ({expired_count} expired, {remaining_cleared} remaining)")
        
        return jsonify({
            'success': True,
            'message': f'SMC signals cleared successfully',
            'cleared_count': total_cleared,
            'expired_count': expired_count,
            'remaining_count': remaining_cleared,
            'total_before': total_count_before,
            'timestamp': get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error clearing SMC signals: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/database/klines/clear", methods=["POST"])
@admin_login_required
def admin_clear_klines_cache():
    """Clear klines cache data from database specifically"""
    try:
        from .models import KlinesCache
        
        admin_username = session.get("admin_username", "admin")
        
        # Get current counts before clearing
        total_count_before = db.session.query(KlinesCache).count()
        
        # Clear expired entries first
        expired_count = KlinesCache.cleanup_expired()
        
        # Get remaining count after expired cleanup
        total_count_after_expired = db.session.query(KlinesCache).count()
        
        # Clear all remaining klines cache data
        remaining_cleared = 0
        if total_count_after_expired > 0:
            remaining_cleared = total_count_after_expired
            db.session.query(KlinesCache).delete()
            db.session.commit()
        
        total_cleared = expired_count + remaining_cleared
        
        logging.info(f"Klines cache cleared by admin {admin_username}: {total_cleared} total ({expired_count} expired, {remaining_cleared} remaining)")
        
        return jsonify({
            'success': True,
            'message': f'Klines cache cleared successfully',
            'cleared_count': total_cleared,
            'expired_count': expired_count,
            'remaining_count': remaining_cleared,
            'total_before': total_count_before,
            'timestamp': get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error clearing klines cache: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/datasync/stop", methods=["POST"])
@admin_login_required
def admin_stop_data_sync():
    """Stop the unified data sync service to prevent rate limiting"""
    try:
        from .unified_data_sync_service import stop_unified_data_sync_service
        
        admin_username = session.get("admin_username", "admin")
        
        # Stop the unified data sync service
        stop_unified_data_sync_service()
        
        logging.info(f"Data sync service stopped by admin {admin_username}")
        
        return jsonify({
            'success': True,
            'message': 'Data sync service stopped successfully',
            'timestamp': get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error stopping data sync service: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/datasync/start", methods=["POST"])
@admin_login_required
def admin_start_data_sync():
    """Start the unified data sync service"""
    try:
        from .unified_data_sync_service import start_unified_data_sync_service
        
        admin_username = session.get("admin_username", "admin")
        
        # Start the unified data sync service
        success = start_unified_data_sync_service(app)
        
        if success:
            logging.info(f"Data sync service started by admin {admin_username}")
            
            return jsonify({
                'success': True,
                'message': 'Data sync service started successfully',
                'timestamp': get_iran_time().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to start data sync service'
            }), 500
            
    except Exception as e:
        logging.error(f"Error starting data sync service: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/datasync/status")
@admin_login_required
def admin_data_sync_status():
    """Get data sync service status"""
    try:
        from .unified_data_sync_service import get_unified_service_status
        
        status = get_unified_service_status()
        
        return jsonify({
            'success': True,
            'status': status,
            'timestamp': get_iran_time().isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error getting data sync status: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/smc/diagnostic", methods=["POST"])
@admin_login_required
def admin_smc_diagnostic():
    """Run comprehensive SMC analysis diagnostic with step-by-step progress"""
    try:
        data = request.get_json() or {}
        symbol = data.get('symbol', 'BTCUSDT')
        
        # Initialize diagnostic results
        diagnostic = {
            'symbol': symbol,
            'timestamp': get_iran_time().isoformat(),
            'steps': [],
            'summary': {},
            'analysis_complete': False,
            'signal_generated': False,
            'signal': None,
            'errors': []
        }
        
        # Import SMC analyzer
        from .smc_analyzer import SMCAnalyzer
        analyzer = SMCAnalyzer()
        
        # Step 1: Data Acquisition
        step1 = {
            'step': 1,
            'name': 'Data Acquisition',
            'status': 'in_progress',
            'details': {},
            'error': None
        }
        
        try:
            step1['details']['message'] = f"Fetching multi-timeframe data for {symbol}..."
            timeframe_data = analyzer.get_multi_timeframe_data(symbol)
            
            step1['details']['timeframes_fetched'] = {}
            for tf, data in timeframe_data.items():
                step1['details']['timeframes_fetched'][tf] = {
                    'candles_count': len(data),
                    'date_range': {
                        'from': data[0]['timestamp'].isoformat() if data else None,
                        'to': data[-1]['timestamp'].isoformat() if data else None
                    } if data else None
                }
            
            step1['status'] = 'completed'
            step1['details']['message'] = f"Successfully fetched data for {len(timeframe_data)} timeframes"
            
        except Exception as e:
            step1['status'] = 'error'
            step1['error'] = str(e)
            step1['details']['message'] = f"Failed to fetch data: {str(e)}"
            diagnostic['errors'].append(f"Data acquisition failed: {str(e)}")
        
        diagnostic['steps'].append(step1)
        
        if step1['status'] == 'error':
            return jsonify(diagnostic), 200
        
        # Step 2: Market Structure Analysis
        step2 = {
            'step': 2,
            'name': 'Market Structure Analysis',
            'status': 'in_progress',
            'details': {},
            'error': None
        }
        
        try:
            h1_data = timeframe_data.get("1h", [])
            h4_data = timeframe_data.get("4h", [])
            d1_data = timeframe_data.get("1d", [])
            
            if not h1_data or not h4_data:
                step2['status'] = 'error'
                step2['error'] = 'Insufficient data for analysis'
                step2['details']['message'] = f"H1: {len(h1_data)} candles, H4: {len(h4_data)} candles"
            else:
                h1_structure = analyzer.detect_market_structure(h1_data)
                h4_structure = analyzer.detect_market_structure(h4_data)
                d1_structure = analyzer.detect_market_structure(d1_data) if d1_data else "No data"
                
                step2['details'] = {
                    'h1_structure': h1_structure.value if hasattr(h1_structure, 'value') else str(h1_structure),
                    'h4_structure': h4_structure.value if hasattr(h4_structure, 'value') else str(h4_structure),
                    'd1_structure': d1_structure.value if hasattr(d1_structure, 'value') else str(d1_structure),
                    'message': 'Market structure analysis completed'
                }
                step2['status'] = 'completed'
                
        except Exception as e:
            step2['status'] = 'error'
            step2['error'] = str(e)
            step2['details']['message'] = f"Market structure analysis failed: {str(e)}"
            diagnostic['errors'].append(f"Market structure analysis failed: {str(e)}")
        
        diagnostic['steps'].append(step2)
        
        # Step 3: Order Block Detection
        step3 = {
            'step': 3,
            'name': 'Order Block Detection',
            'status': 'in_progress',
            'details': {},
            'error': None
        }
        
        try:
            order_blocks = analyzer.find_order_blocks(h1_data)
            
            step3['details'] = {
                'total_order_blocks': len(order_blocks),
                'bullish_blocks': len([ob for ob in order_blocks if ob.direction == 'bullish']),
                'bearish_blocks': len([ob for ob in order_blocks if ob.direction == 'bearish']),
                'validated_blocks': len([ob for ob in order_blocks if ob.volume_confirmed]),
                'message': f"Found {len(order_blocks)} order blocks"
            }
            
            if order_blocks:
                step3['details']['sample_blocks'] = []
                for i, ob in enumerate(order_blocks[:3]):  # Show first 3 blocks
                    step3['details']['sample_blocks'].append({
                        'direction': ob.direction,
                        'price_range': f"{ob.price_low:.4f} - {ob.price_high:.4f}",
                        'strength': ob.strength,
                        'tested': ob.tested,
                        'volume_confirmed': ob.volume_confirmed
                    })
            
            step3['status'] = 'completed'
            
        except Exception as e:
            step3['status'] = 'error'
            step3['error'] = str(e)
            step3['details']['message'] = f"Order block detection failed: {str(e)}"
            diagnostic['errors'].append(f"Order block detection failed: {str(e)}")
        
        diagnostic['steps'].append(step3)
        
        # Step 4: Fair Value Gap Analysis
        step4 = {
            'step': 4,
            'name': 'Fair Value Gap Analysis',
            'status': 'in_progress',
            'details': {},
            'error': None
        }
        
        try:
            fvgs = analyzer.find_fair_value_gaps(h1_data)
            
            step4['details'] = {
                'total_fvgs': len(fvgs),
                'bullish_fvgs': len([fvg for fvg in fvgs if fvg.direction == 'bullish']),
                'bearish_fvgs': len([fvg for fvg in fvgs if fvg.direction == 'bearish']),
                'unfilled_fvgs': len([fvg for fvg in fvgs if not fvg.filled]),
                'message': f"Found {len(fvgs)} fair value gaps"
            }
            
            if fvgs:
                step4['details']['sample_fvgs'] = []
                for i, fvg in enumerate(fvgs[:3]):  # Show first 3 FVGs
                    step4['details']['sample_fvgs'].append({
                        'direction': fvg.direction,
                        'price_range': f"{fvg.gap_low:.4f} - {fvg.gap_high:.4f}",
                        'filled': fvg.filled,
                        'atr_size': fvg.atr_size,
                        'age_candles': fvg.age_candles
                    })
            
            step4['status'] = 'completed'
            
        except Exception as e:
            step4['status'] = 'error'
            step4['error'] = str(e)
            step4['details']['message'] = f"Fair value gap analysis failed: {str(e)}"
            diagnostic['errors'].append(f"Fair value gap analysis failed: {str(e)}")
        
        diagnostic['steps'].append(step4)
        
        # Step 5: Liquidity Pool Analysis
        step5 = {
            'step': 5,
            'name': 'Liquidity Pool Analysis',
            'status': 'in_progress',
            'details': {},
            'error': None
        }
        
        try:
            liquidity_pools = analyzer.find_liquidity_pools(h4_data)
            
            step5['details'] = {
                'total_pools': len(liquidity_pools),
                'buy_side_pools': len([lp for lp in liquidity_pools if lp.type == 'buy_side']),
                'sell_side_pools': len([lp for lp in liquidity_pools if lp.type == 'sell_side']),
                'swept_pools': len([lp for lp in liquidity_pools if lp.swept]),
                'message': f"Found {len(liquidity_pools)} liquidity pools"
            }
            
            if liquidity_pools:
                step5['details']['sample_pools'] = []
                for i, lp in enumerate(liquidity_pools[:3]):  # Show first 3 pools
                    step5['details']['sample_pools'].append({
                        'type': lp.type,
                        'price': lp.price,
                        'strength': lp.strength,
                        'swept': lp.swept
                    })
            
            step5['status'] = 'completed'
            
        except Exception as e:
            step5['status'] = 'error'
            step5['error'] = str(e)
            step5['details']['message'] = f"Liquidity pool analysis failed: {str(e)}"
            diagnostic['errors'].append(f"Liquidity pool analysis failed: {str(e)}")
        
        diagnostic['steps'].append(step5)
        
        # Step 6: Technical Indicators
        step6 = {
            'step': 6,
            'name': 'Technical Indicators',
            'status': 'in_progress',
            'details': {},
            'error': None
        }
        
        try:
            rsi = analyzer.calculate_rsi(h1_data)
            mas = analyzer.calculate_moving_averages(h1_data)
            
            current_price = h1_data[-1]["close"] if h1_data else 0
            
            step6['details'] = {
                'current_price': current_price,
                'rsi_current': rsi if rsi else None,
                'rsi_status': 'oversold' if rsi and rsi < 30 else 'overbought' if rsi and rsi > 70 else 'neutral',
                'moving_averages': {
                    'ma_20': mas.get('ma_20') if mas and 'ma_20' in mas else None,
                    'ma_50': mas.get('ma_50') if mas and 'ma_50' in mas else None,
                    'ema_20': mas.get('ema_20') if mas and 'ema_20' in mas else None
                },
                'message': 'Technical indicators calculated'
            }
            step6['status'] = 'completed'
            
        except Exception as e:
            step6['status'] = 'error'
            step6['error'] = str(e)
            step6['details']['message'] = f"Technical indicator calculation failed: {str(e)}"
            diagnostic['errors'].append(f"Technical indicator calculation failed: {str(e)}")
        
        diagnostic['steps'].append(step6)
        
        # Step 7: Signal Generation Attempt
        step7 = {
            'step': 7,
            'name': 'Signal Generation',
            'status': 'in_progress',
            'details': {},
            'error': None
        }
        
        try:
            signal = analyzer.generate_trade_signal(symbol)
            
            if signal:
                step7['details'] = {
                    'signal_generated': True,
                    'direction': signal.direction,
                    'entry_price': signal.entry_price,
                    'stop_loss': signal.stop_loss,
                    'take_profits': signal.take_profit_levels,
                    'confidence': signal.confidence,
                    'signal_strength': signal.signal_strength.value if hasattr(signal.signal_strength, 'value') else str(signal.signal_strength),
                    'risk_reward_ratio': signal.risk_reward_ratio,
                    'reasoning': signal.reasoning,
                    'message': f"Generated {signal.direction} signal with {signal.confidence:.2f} confidence"
                }
                diagnostic['signal_generated'] = True
                diagnostic['signal'] = {
                    'direction': signal.direction,
                    'entry_price': signal.entry_price,
                    'stop_loss': signal.stop_loss,
                    'take_profits': signal.take_profit_levels,
                    'confidence': signal.confidence,
                    'signal_strength': signal.signal_strength.value if hasattr(signal.signal_strength, 'value') else str(signal.signal_strength),
                    'risk_reward_ratio': signal.risk_reward_ratio,
                    'reasoning': signal.reasoning
                }
            else:
                step7['details'] = {
                    'signal_generated': False,
                    'reason': 'No valid signal conditions met',
                    'message': 'Analysis complete - No signal generated due to insufficient confluence or market conditions'
                }
            
            step7['status'] = 'completed'
            
        except Exception as e:
            step7['status'] = 'error'
            step7['error'] = str(e)
            step7['details']['message'] = f"Signal generation failed: {str(e)}"
            diagnostic['errors'].append(f"Signal generation failed: {str(e)}")
        
        diagnostic['steps'].append(step7)
        
        # Create analysis summary
        diagnostic['analysis_complete'] = True
        diagnostic['summary'] = {
            'total_steps': len(diagnostic['steps']),
            'successful_steps': len([s for s in diagnostic['steps'] if s['status'] == 'completed']),
            'failed_steps': len([s for s in diagnostic['steps'] if s['status'] == 'error']),
            'signal_generated': diagnostic['signal_generated'],
            'error_count': len(diagnostic['errors']),
            'analysis_successful': len(diagnostic['errors']) == 0
        }
        
        admin_username = session.get("admin_username", "admin")
        logging.info(f"SMC diagnostic analysis completed by admin {admin_username} for {symbol}")
        
        return jsonify(diagnostic)
        
    except Exception as e:
        logging.error(f"Error in SMC diagnostic analysis: {e}")
        return jsonify({
            'error': str(e),
            'analysis_complete': False,
            'timestamp': get_iran_time().isoformat()
        }), 500

