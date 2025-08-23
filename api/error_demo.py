"""
Error Handling Demonstration Module
Shows examples of different error types and their user-friendly messages
"""

from flask import Blueprint, jsonify, request
from api.error_handler import (
    handle_error, 
    create_validation_error, 
    create_success_response,
    TradingError, 
    ErrorCategory, 
    ErrorSeverity
)

error_demo_bp = Blueprint('error_demo', __name__)

@error_demo_bp.route('/api/error-examples', methods=['GET'])
def error_examples():
    """Demonstrate different error types and their user-friendly messages"""
    error_type = request.args.get('type', 'validation')
    
    if error_type == 'validation':
        return jsonify(create_validation_error(
            "Trading Symbol", 
            "INVALID123", 
            "A valid trading pair like BTCUSDT or ETHUSDT"
        )), 400
    
    elif error_type == 'trading':
        error = TradingError(
            category=ErrorCategory.TRADING_ERROR,
            severity=ErrorSeverity.HIGH,
            technical_message="Insufficient balance: required 1000 USDT, available 250 USDT",
            user_message="You don't have enough balance to place this trade.",
            suggestions=[
                "Check your account balance",
                "Reduce the trade amount or leverage",
                "Deposit more funds to your account",
                "Close other positions to free up margin"
            ]
        )
        return jsonify(error.to_dict()), 400
    
    elif error_type == 'authentication':
        error = TradingError(
            category=ErrorCategory.AUTHENTICATION_ERROR,
            severity=ErrorSeverity.HIGH,
            technical_message="API key authentication failed: Invalid signature",
            user_message="Your API credentials are invalid or have expired.",
            suggestions=[
                "Check your API key and secret in Settings",
                "Verify your credentials are still active",
                "Make sure you're using the correct exchange",
                "Contact your exchange if the problem persists"
            ]
        )
        return jsonify(error.to_dict()), 401
    
    elif error_type == 'market':
        error = TradingError(
            category=ErrorCategory.MARKET_ERROR,
            severity=ErrorSeverity.MEDIUM,
            technical_message="Symbol INVALID not found in market data",
            user_message="The trading symbol is not available or invalid.",
            suggestions=[
                "Check the symbol name (e.g., BTCUSDT, ETHUSDT)",
                "Make sure the symbol is supported on your exchange",
                "Try a different trading pair",
                "Refresh the symbol list"
            ]
        )
        return jsonify(error.to_dict()), 400
    
    elif error_type == 'rate_limit':
        error = TradingError(
            category=ErrorCategory.RATE_LIMIT_ERROR,
            severity=ErrorSeverity.MEDIUM,
            technical_message="Rate limit exceeded: 1200 requests per minute",
            user_message="Too many requests to the exchange. Please wait a moment before trying again.",
            suggestions=[
                "Wait a few minutes before making another request",
                "Reduce the frequency of your trades",
                "Contact support if this happens frequently"
            ],
            retry_after=60
        )
        return jsonify(error.to_dict()), 429
    
    elif error_type == 'database':
        error = TradingError(
            category=ErrorCategory.DATABASE_ERROR,
            severity=ErrorSeverity.CRITICAL,
            technical_message="Connection timeout: Could not connect to database after 30 seconds",
            user_message="We're experiencing database issues. Your data is safe.",
            suggestions=[
                "Try refreshing the page",
                "Wait a few minutes and try again",
                "Contact support if this persists"
            ]
        )
        return jsonify(error.to_dict()), 500
    
    elif error_type == 'network':
        error = TradingError(
            category=ErrorCategory.NETWORK_ERROR,
            severity=ErrorSeverity.HIGH,
            technical_message="Connection timeout: api.toobit.com",
            user_message="Unable to connect to our servers. Please check your internet connection.",
            suggestions=[
                "Check your internet connection",
                "Try refreshing the page",
                "Contact your internet provider if issues persist"
            ]
        )
        return jsonify(error.to_dict()), 503
    
    elif error_type == 'success':
        return jsonify(create_success_response(
            "Trade executed successfully",
            {
                "trade_id": "demo_123",
                "symbol": "BTCUSDT",
                "side": "long",
                "amount": 100.0
            }
        ))
    
    else:
        return jsonify(create_validation_error(
            "Error Type",
            error_type,
            "One of: validation, trading, authentication, market, rate_limit, database, network, success"
        )), 400

@error_demo_bp.route('/api/error-stats', methods=['GET'])
def error_stats():
    """Get error handling statistics"""
    from api.error_handler import error_classifier
    
    # This would normally come from your monitoring system
    demo_stats = {
        "error_classification_system": "active",
        "total_error_patterns": len(error_classifier.error_patterns),
        "user_message_templates": len(error_classifier.user_message_templates),
        "supported_categories": [category.value for category in ErrorCategory],
        "supported_severities": [severity.value for severity in ErrorSeverity],
        "features": [
            "Smart error classification",
            "User-friendly messages",
            "Contextual suggestions",
            "Retry timing guidance",
            "Severity-based icons",
            "Telegram-formatted messages"
        ]
    }
    
    return jsonify(create_success_response(
        "Error handling system is operational",
        demo_stats
    ))