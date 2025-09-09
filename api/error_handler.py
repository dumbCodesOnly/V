"""
Comprehensive Error Classification and User-Friendly Error Messaging System
Provides centralized error handling with clear, user-friendly messages
"""

import logging
from enum import Enum
from typing import Dict, Optional, Tuple, Any
import json
from datetime import datetime

# Import configuration constants
try:
    from config import ErrorConfig
except ImportError:
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import ErrorConfig


class ErrorCategory(Enum):
    """Main error categories for classification"""
    API_ERROR = "api_error"
    TRADING_ERROR = "trading_error"
    VALIDATION_ERROR = "validation_error"
    NETWORK_ERROR = "network_error"
    DATABASE_ERROR = "database_error"
    AUTHENTICATION_ERROR = "authentication_error"
    MARKET_ERROR = "market_error"
    SYSTEM_ERROR = "system_error"
    USER_INPUT_ERROR = "user_input_error"
    RATE_LIMIT_ERROR = "rate_limit_error"


class ErrorSeverity(Enum):
    """Error severity levels"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class TradingError:
    """Enhanced error class with user-friendly messaging"""
    
    def __init__(
        self,
        category: ErrorCategory,
        severity: ErrorSeverity,
        technical_message: str,
        user_message: str,
        error_code: Optional[str] = None,
        suggestions: Optional[list] = None,
        retry_after: Optional[int] = None,
        original_exception: Optional[Exception] = None
    ):
        self.category = category
        self.severity = severity
        self.technical_message = technical_message
        self.user_message = user_message
        self.error_code = error_code or f"{category.value}_{int(datetime.utcnow().timestamp())}"
        self.suggestions = suggestions or []
        self.retry_after = retry_after
        self.original_exception = original_exception
        self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> dict:
        """Convert error to dictionary for API responses"""
        return {
            'error': True,
            'category': self.category.value,
            'severity': self.severity.value,
            'message': self.user_message,
            'error_code': self.error_code,
            'suggestions': self.suggestions,
            'retry_after': self.retry_after,
            'timestamp': self.timestamp
        }
    
    def to_telegram_message(self) -> str:
        """Format error for Telegram messaging"""
        icon = self._get_severity_icon()
        message = f"{icon} {self.user_message}"
        
        if self.suggestions:
            message += "\n\n💡 Suggestions:"
            for suggestion in self.suggestions:
                message += f"\n• {suggestion}"
        
        if self.retry_after:
            message += f"\n\n⏱️ Please try again in {self.retry_after} seconds."
        
        return message
    
    def _get_severity_icon(self) -> str:
        """Get emoji icon based on severity"""
        icons = {
            ErrorSeverity.CRITICAL: "🚨",
            ErrorSeverity.HIGH: "⚠️",
            ErrorSeverity.MEDIUM: "⚡",
            ErrorSeverity.LOW: "ℹ️",
            ErrorSeverity.INFO: "💡"
        }
        return icons.get(self.severity, "❗")


class ErrorClassifier:
    """Classify and translate technical errors to user-friendly messages"""
    
    def __init__(self):
        self.error_patterns = self._initialize_error_patterns()
        self.user_message_templates = self._initialize_user_messages()
    
    def _initialize_error_patterns(self) -> Dict[str, Dict]:
        """Initialize error pattern matching rules"""
        return {
            # API Errors
            'api_key_invalid': {
                'patterns': ['invalid api key', 'authentication failed', 'api key not found', 'unauthorized', f'{ErrorConfig.HTTP_UNAUTHORIZED}'],
                'category': ErrorCategory.AUTHENTICATION_ERROR,
                'severity': ErrorSeverity.HIGH
            },
            'api_rate_limit': {
                'patterns': ['rate limit', 'too many requests', 'quota exceeded', f'{ErrorConfig.HTTP_RATE_LIMITED}'],
                'category': ErrorCategory.RATE_LIMIT_ERROR,
                'severity': ErrorSeverity.MEDIUM
            },
            'api_server_error': {
                'patterns': ['internal server error', f'{ErrorConfig.HTTP_INTERNAL_ERROR}', 'service unavailable', f'{ErrorConfig.HTTP_SERVICE_UNAVAILABLE}'],
                'category': ErrorCategory.API_ERROR,
                'severity': ErrorSeverity.HIGH
            },
            'api_timeout': {
                'patterns': ['timeout', 'connection timeout', 'read timeout'],
                'category': ErrorCategory.NETWORK_ERROR,
                'severity': ErrorSeverity.MEDIUM
            },
            
            # Trading Errors
            'insufficient_balance': {
                'patterns': ['insufficient balance', 'not enough funds', 'balance too low'],
                'category': ErrorCategory.TRADING_ERROR,
                'severity': ErrorSeverity.HIGH
            },
            'invalid_symbol': {
                'patterns': ['symbol not found', 'invalid symbol', 'unknown symbol'],
                'category': ErrorCategory.MARKET_ERROR,
                'severity': ErrorSeverity.MEDIUM
            },
            'position_size_invalid': {
                'patterns': ['position size', 'minimum size', 'maximum size', 'size too small', 'size too large'],
                'category': ErrorCategory.TRADING_ERROR,
                'severity': ErrorSeverity.MEDIUM
            },
            'market_closed': {
                'patterns': ['market closed', 'trading suspended', 'market not available'],
                'category': ErrorCategory.MARKET_ERROR,
                'severity': ErrorSeverity.MEDIUM
            },
            
            # Database Errors
            'database_connection': {
                'patterns': ['database connection', 'connection refused', 'database unavailable'],
                'category': ErrorCategory.DATABASE_ERROR,
                'severity': ErrorSeverity.CRITICAL
            },
            'database_timeout': {
                'patterns': ['database timeout', 'query timeout', 'statement timeout'],
                'category': ErrorCategory.DATABASE_ERROR,
                'severity': ErrorSeverity.HIGH
            },
            
            # Network Errors
            'network_connection': {
                'patterns': ['connection error', 'network unreachable', 'dns resolution', 'no internet'],
                'category': ErrorCategory.NETWORK_ERROR,
                'severity': ErrorSeverity.HIGH
            },
            
            # Validation Errors
            'missing_field': {
                'patterns': ['required field', 'missing parameter', 'field is required'],
                'category': ErrorCategory.VALIDATION_ERROR,
                'severity': ErrorSeverity.LOW
            },
            'invalid_format': {
                'patterns': ['invalid format', 'format error', 'parsing error'],
                'category': ErrorCategory.VALIDATION_ERROR,
                'severity': ErrorSeverity.LOW
            }
        }
    
    def _initialize_user_messages(self) -> Dict[str, Dict]:
        """Initialize user-friendly message templates"""
        return {
            'api_key_invalid': {
                'message': "Your API credentials are invalid or have expired. Please check your API key and secret.",
                'suggestions': [
                    "Verify your API key and secret are correct",
                    "Check if your API credentials have expired",
                    "Ensure you're using the right exchange (testnet vs mainnet)",
                    "Contact your exchange if the problem persists"
                ]
            },
            'api_rate_limit': {
                'message': "Too many requests to the exchange. Please wait a moment before trying again.",
                'suggestions': [
                    "Wait a few minutes before making another request",
                    "Reduce the frequency of your trades",
                    "Contact support if this happens frequently"
                ],
                'retry_after': ErrorConfig.API_KEY_RETRY_TIMEOUT
            },
            'api_server_error': {
                'message': "The exchange is experiencing technical issues. This is temporary.",
                'suggestions': [
                    "Try again in a few minutes",
                    "Check the exchange status page",
                    "Use testnet mode while mainnet is unavailable"
                ],
                'retry_after': ErrorConfig.RATE_LIMIT_RETRY_TIMEOUT
            },
            'api_timeout': {
                'message': "Connection to the exchange timed out. Please check your internet connection.",
                'suggestions': [
                    "Check your internet connection",
                    "Try again in a few moments",
                    "Contact support if this persists"
                ],
                'retry_after': ErrorConfig.NETWORK_RETRY_TIMEOUT
            },
            'insufficient_balance': {
                'message': "You don't have enough balance to place this trade.",
                'suggestions': [
                    "Check your account balance",
                    "Reduce the trade size",
                    "Deposit more funds to your account",
                    "Close other positions to free up margin"
                ]
            },
            'invalid_symbol': {
                'message': "The trading symbol you entered is not available.",
                'suggestions': [
                    "Check the symbol name (e.g., BTCUSDT, ETHUSDT)",
                    "Make sure the symbol is supported on your exchange",
                    "Try a different trading pair"
                ]
            },
            'position_size_invalid': {
                'message': "The position size is outside the allowed range.",
                'suggestions': [
                    "Check the minimum and maximum position sizes",
                    "Adjust your trade amount",
                    "Check the exchange's trading rules for this symbol"
                ]
            },
            'market_closed': {
                'message': "Trading is currently suspended for this market.",
                'suggestions': [
                    "Check market hours",
                    "Try a different trading pair",
                    "Wait for the market to reopen"
                ]
            },
            'database_connection': {
                'message': "We're experiencing database issues. Your data is safe.",
                'suggestions': [
                    "Try refreshing the page",
                    "Wait a few minutes and try again",
                    "Contact support if this persists"
                ]
            },
            'database_timeout': {
                'message': "The request took too long to process. Please try again.",
                'suggestions': [
                    "Try again in a moment",
                    "Simplify your request if possible",
                    "Contact support if this happens repeatedly"
                ]
            },
            'network_connection': {
                'message': "Unable to connect to our servers. Please check your internet connection.",
                'suggestions': [
                    "Check your internet connection",
                    "Try refreshing the page",
                    "Contact your internet provider if issues persist"
                ]
            },
            'missing_field': {
                'message': "Some required information is missing.",
                'suggestions': [
                    "Fill in all required fields",
                    "Check that all inputs are complete",
                    "Try again with complete information"
                ]
            },
            'invalid_format': {
                'message': "The information you entered is in an invalid format.",
                'suggestions': [
                    "Check the format of your input",
                    "Use numbers only for amounts",
                    "Follow the format examples provided"
                ]
            },
            'circuit_breaker_open': {
                'message': "This service is temporarily unavailable due to repeated errors.",
                'suggestions': [
                    "Wait a few minutes for the service to recover",
                    "Try using a different exchange if available",
                    "Contact support if this persists"
                ],
                'retry_after': ErrorConfig.SERVER_ERROR_RETRY_TIMEOUT
            },
            'generic_error': {
                'message': "An unexpected error occurred. Our team has been notified.",
                'suggestions': [
                    "Try refreshing the page",
                    "Wait a moment and try again",
                    "Contact support if the problem continues"
                ]
            }
        }
    
    def classify_error(self, error: Exception, context: str = "") -> TradingError:
        """Classify an error and return user-friendly TradingError"""
        error_message = str(error).lower()
        context_lower = context.lower()
        combined_text = f"{error_message} {context_lower}"
        
        # Check for circuit breaker errors first
        if "circuit breaker" in error_message and "open" in error_message:
            return self._create_trading_error('circuit_breaker_open', error, error_message)
        
        # Pattern matching for known error types
        for error_type, config in self.error_patterns.items():
            for pattern in config['patterns']:
                if pattern in combined_text:
                    return self._create_trading_error(error_type, error, error_message)
        
        # Default to generic error
        return self._create_trading_error('generic_error', error, error_message)
    
    def _create_trading_error(self, error_type: str, original_error: Exception, technical_message: str) -> TradingError:
        """Create a TradingError from error type"""
        pattern_config = self.error_patterns.get(error_type, {})
        message_config = self.user_message_templates.get(error_type, self.user_message_templates['generic_error'])
        
        category = pattern_config.get('category', ErrorCategory.SYSTEM_ERROR)
        severity = pattern_config.get('severity', ErrorSeverity.MEDIUM)
        
        return TradingError(
            category=category,
            severity=severity,
            technical_message=technical_message,
            user_message=message_config['message'],
            suggestions=message_config.get('suggestions', []),
            retry_after=message_config.get('retry_after'),
            original_exception=original_error
        )
    
    def handle_api_error(self, response_data: dict, status_code: Optional[int] = None) -> TradingError:
        """Handle API-specific errors with response data"""
        if not response_data:
            return self.classify_error(Exception("Unknown API error"))
        
        # Extract error information from API response
        error_msg = response_data.get('msg', response_data.get('message', 'Unknown API error'))
        error_code = response_data.get('code', status_code)
        
        # Specific API error handling
        if status_code == ErrorConfig.HTTP_UNAUTHORIZED or 'unauthorized' in error_msg.lower():
            return self._create_trading_error('api_key_invalid', Exception(error_msg), error_msg)
        elif status_code == ErrorConfig.HTTP_RATE_LIMITED or 'rate limit' in error_msg.lower():
            return self._create_trading_error('api_rate_limit', Exception(error_msg), error_msg)
        elif status_code and status_code >= ErrorConfig.HTTP_SERVER_ERROR_MIN:
            return self._create_trading_error('api_server_error', Exception(error_msg), error_msg)
        
        # Fallback to pattern matching
        return self.classify_error(Exception(error_msg))


# Global error classifier instance
error_classifier = ErrorClassifier()


def handle_error(error: Exception, context: str = "", log_error: bool = True) -> dict:
    """
    Main error handling function that returns user-friendly error response
    
    Args:
        error: The exception that occurred
        context: Additional context about where the error occurred
        log_error: Whether to log the error
    
    Returns:
        Dictionary suitable for JSON response
    """
    classified_error = error_classifier.classify_error(error, context)
    
    if log_error:
        logging.error(f"[{classified_error.category.value}] {classified_error.technical_message}")
        if context:
            logging.error(f"Context: {context}")
        if classified_error.original_exception:
            logging.exception("Original exception:", exc_info=classified_error.original_exception)
    
    return classified_error.to_dict()


def handle_api_error(response_data: dict, status_code: Optional[int] = None, context: str = "") -> dict:
    """
    Handle API-specific errors
    
    Args:
        response_data: Response data from API
        status_code: HTTP status code
        context: Additional context
    
    Returns:
        Dictionary suitable for JSON response
    """
    classified_error = error_classifier.handle_api_error(response_data, status_code)
    
    logging.error(f"[API Error] {classified_error.technical_message}")
    if context:
        logging.error(f"Context: {context}")
    
    return classified_error.to_dict()


def create_validation_error(field: str, value: Any = None, expected: Optional[str] = None) -> dict:
    """
    Create a validation error with specific field information
    
    Args:
        field: The field that failed validation
        value: The invalid value (optional)
        expected: What was expected (optional)
    
    Returns:
        Dictionary suitable for JSON response
    """
    message = f"Invalid {field}"
    if expected:
        message += f": {expected}"
    
    suggestions = [f"Please provide a valid {field}"]
    if expected:
        suggestions.append(f"Expected format: {expected}")
    
    error = TradingError(
        category=ErrorCategory.VALIDATION_ERROR,
        severity=ErrorSeverity.LOW,
        technical_message=f"Validation failed for field '{field}': {value}",
        user_message=message,
        suggestions=suggestions
    )
    
    return error.to_dict()


def create_success_response(message: str, data: Optional[dict] = None) -> dict:
    """
    Create a standardized success response
    
    Args:
        message: Success message
        data: Additional data to include
    
    Returns:
        Dictionary suitable for JSON response
    """
    response = {
        'success': True,
        'message': message,
        'timestamp': datetime.utcnow().isoformat()
    }
    
    if data:
        response.update(data)
    
    return response
