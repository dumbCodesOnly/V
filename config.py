"""
Centralized Configuration for Toobit Trading Bot
All constants, magic numbers, timeouts, and API endpoints are defined here
"""
import os
import logging
from typing import Optional, Dict, Any, Union
from urllib.parse import urlparse

# =============================================================================
# API ENDPOINTS AND URLS
# =============================================================================
class APIConfig:
    # Toobit Exchange - Verified working API structure
    TOOBIT_BASE_URL = "https://api.toobit.com"
    TOOBIT_API_VERSION = "v1"
    
    # Public market data API (no authentication required)
    # Endpoint: https://api.toobit.com/quote/v1/ticker/24hr?symbol=BTCUSDT
    TOOBIT_QUOTE_PATH = "/quote/v1"
    
    # Private trading API (authentication required) 
    # Endpoint: https://api.toobit.com/api/v1/futures/*
    TOOBIT_FUTURES_PATH = "/api/v1/futures"
    
    # LBank Exchange - Official Perpetual Futures API structure
    LBANK_BASE_URL = "https://lbkperp.lbank.com"
    LBANK_API_VERSION = "v1"
    
    # LBank Perpetual Futures API paths
    LBANK_PUBLIC_PATH = "/cfd/openApi/v1/pub"    # Public market data
    LBANK_PRIVATE_PATH = "/cfd/openApi/v1/prv"   # Private account/trading
    LBANK_FUTURES_PATH = "/cfd/openApi/v1/prv"   # Futures trading compatibility
    LBANK_SPOT_PATH = "/cfd/openApi/v1/prv"      # Account operations
    
    # Hyperliquid DEX - Official API structure
    HYPERLIQUID_MAINNET_URL = "https://api.hyperliquid.xyz"
    HYPERLIQUID_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
    HYPERLIQUID_API_VERSION = "v1"
    
    # Fallback Price APIs  
    BINANCE_BASE_URL = "https://api.binance.com"
    COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
    CRYPTOCOMPARE_BASE_URL = "https://min-api.cryptocompare.com/data"
    
    # HTTP Headers
    USER_AGENT = "TradingExpert/1.0"


# =============================================================================
# TIMEOUTS AND INTERVALS
# =============================================================================
class TimeConfig:
    # API Request Timeouts
    DEFAULT_API_TIMEOUT = 30  # seconds
    BATCH_API_TIMEOUT = 20    # seconds for batch operations
    PRICE_API_TIMEOUT = 15    # seconds for price fetching
    QUICK_API_TIMEOUT = 10    # seconds for quick operations
    FAST_API_TIMEOUT = 5      # seconds for very fast operations
    EXTENDED_API_TIMEOUT = 8  # seconds for extended operations
    
    # Cache TTL
    PRICE_CACHE_TTL = 10      # seconds - how long to cache price data
    USER_DATA_CACHE_TTL = 30  # seconds - how long to cache user data
    
    # Sync Intervals - COST OPTIMIZED FOR RENDER
    EXCHANGE_SYNC_INTERVAL = 15    # seconds - background sync for Replit (faster for paper trading TP/SL)
    VERCEL_SYNC_COOLDOWN = 30      # seconds - cooldown between syncs for Vercel
    RENDER_SYNC_INTERVAL = 12      # seconds - optimized sync for Render (reduced from 5s)
    
    # Health Ping Boost - Extended monitoring after health checks (COST OPTIMIZED)
    HEALTH_PING_BOOST_DURATION = 120  # seconds (2 minutes) - reduced from 3 minutes
    HEALTH_PING_BOOST_INTERVAL = 8    # seconds - optimized from 10s (still faster than normal)
    
    # UI Update Intervals
    PRICE_UPDATE_INTERVAL = 10000  # milliseconds - frontend price updates
    PORTFOLIO_REFRESH_INTERVAL = 30000  # milliseconds - portfolio auto-refresh
    
    # Bot Activity Check
    BOT_HEARTBEAT_TIMEOUT = 300    # seconds (5 minutes) - bot activity check
    
    # Emergency Data Age Threshold
    EMERGENCY_DATA_AGE_LIMIT = 1800  # seconds (30 minutes) - use stale data in emergencies




# =============================================================================
# DATABASE CONFIGURATION
# =============================================================================
class DatabaseConfig:
    # PostgreSQL Connection Pool Settings
    POOL_RECYCLE = 1800  # seconds (30 minutes) - connection recycling for serverless
    STANDARD_POOL_RECYCLE = 300  # seconds (5 minutes) - standard pool recycle
    RENDER_POOL_RECYCLE = 600    # seconds (10 minutes) - Render managed PostgreSQL
    POOL_PRE_PING = True
    
    # Connection Pool Size Settings
    SERVERLESS_POOL_SIZE = 5     # Pool size for serverless environments (Vercel/Neon)
    SERVERLESS_MAX_OVERFLOW = 10  # Max overflow for serverless
    SERVERLESS_POOL_TIMEOUT = 60  # Pool timeout for serverless (seconds)
    
    RENDER_POOL_SIZE = 3         # Reduced pool size for Render starter plan
    RENDER_MAX_OVERFLOW = 5      # Reduced overflow for memory efficiency
    RENDER_POOL_TIMEOUT = 20     # Shorter timeout for faster responses
    
    STANDARD_POOL_SIZE = 5       # Standard pool size for Replit
    STANDARD_MAX_OVERFLOW = 10   # Standard max overflow
    
    # PostgreSQL Keep-Alive Settings (for Neon/Vercel)
    KEEPALIVES_IDLE = "30"     # seconds - keep connections alive
    KEEPALIVES_INTERVAL = "5"  # seconds - check interval
    KEEPALIVES_COUNT = "3"     # failed checks before disconnect
    
    # SSL Configuration
    SSL_MODE = "require"
    APPLICATION_NAME = "toobit-trading-bot"


# =============================================================================
# TRADING CONSTANTS
# =============================================================================
class TradingConfig:
    # Default Trading Values
    DEFAULT_LEVERAGE = 1          # 1x leverage
    DEFAULT_TRIAL_BALANCE = 10000  # USDT - initial paper trading balance
    
    # Risk Management Defaults
    DEFAULT_STOP_LOSS_PERCENT = 5.0    # 5% stop loss
    DEFAULT_TAKE_PROFIT_PERCENT = 10.0 # 10% take profit
    DEFAULT_TRAIL_PERCENTAGE = 2.0     # 2% trailing stop
    
    # Position Management
    MIN_POSITION_SIZE = 10     # USDT minimum position size
    MAX_LEVERAGE = 100         # Maximum allowed leverage
    
    # API Limits
    MAX_SYMBOLS_BATCH = 20     # Maximum symbols in batch price requests
    DEFAULT_TRADE_HISTORY_LIMIT = 100  # Default limit for trade history queries
    
    # Trading Symbols
    DEFAULT_SYMBOL = "BTCUSDT"
    SUPPORTED_SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT",
        "SOLUSDT", "DOTUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT"
    ]
    
    # Toobit Futures Symbol Mapping (Standard format -> Toobit format)
    TOOBIT_SYMBOL_MAP = {
        "BTCUSDT": "BTC-SWAP-USDT",
        "ETHUSDT": "ETH-SWAP-USDT", 
        "BNBUSDT": "BNB-SWAP-USDT",
        "ADAUSDT": "ADA-SWAP-USDT",
        "XRPUSDT": "XRP-SWAP-USDT",
        "SOLUSDT": "SOL-SWAP-USDT",
        "DOTUSDT": "DOT-SWAP-USDT", 
        "DOGEUSDT": "DOGE-SWAP-USDT",
        "AVAXUSDT": "AVAX-SWAP-USDT",
        "LINKUSDT": "LINK-SWAP-USDT"
    }
    
    # LBank Symbol Mapping (Standard format -> LBank format)
    LBANK_SYMBOL_MAP = {
        "BTCUSDT": "btc_usdt",
        "ETHUSDT": "eth_usdt",
        "BNBUSDT": "bnb_usdt", 
        "ADAUSDT": "ada_usdt",
        "XRPUSDT": "xrp_usdt",
        "SOLUSDT": "sol_usdt",
        "DOTUSDT": "dot_usdt",
        "DOGEUSDT": "doge_usdt",
        "AVAXUSDT": "avax_usdt",
        "LINKUSDT": "link_usdt"
    }
    
    # Hyperliquid Symbol Mapping (Standard format -> Hyperliquid format)
    HYPERLIQUID_SYMBOL_MAP = {
        "BTCUSDT": "BTC",
        "ETHUSDT": "ETH",
        "BNBUSDT": "BNB",
        "ADAUSDT": "ADA",
        "XRPUSDT": "XRP",
        "SOLUSDT": "SOL",
        "DOTUSDT": "DOT",
        "DOGEUSDT": "DOGE",
        "AVAXUSDT": "AVAX",
        "LINKUSDT": "LINK"
    }
    
    # Supported exchanges
    SUPPORTED_EXCHANGES = ["toobit", "lbank", "hyperliquid"]
    DEFAULT_EXCHANGE = "lbank"


# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================
class LoggingConfig:
    # Log Levels
    REPLIT_LOG_LEVEL = "DEBUG"
    VERCEL_LOG_LEVEL = "INFO"
    
    # Log Format
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# =============================================================================
# SECURITY CONFIGURATION
# =============================================================================
class SecurityConfig:
    # Session Configuration
    DEFAULT_SESSION_SECRET = "replit-default-secret-key-12345"  # Only used in development
    
    # Webhook Security
    WEBHOOK_TIMEOUT = 30  # seconds
    WEBHOOK_SETUP_TIMEOUT = 10  # seconds for webhook setup calls
    
    # Telegram webhook IP ranges for validation
    TELEGRAM_IP_RANGES = [
        "149.154.160.0/20",
        "91.108.4.0/22",
        "149.154.164.0/22", 
        "149.154.168.0/22",
        "149.154.172.0/22"
    ]
    
    # Rate Limiting
    MAX_REQUESTS_PER_MINUTE = 100
    MAX_TRADES_PER_HOUR = 20


# =============================================================================
# CIRCUIT BREAKER CONFIGURATION
# =============================================================================
class CircuitBreakerConfig:
    # Default Circuit Breaker Settings
    DEFAULT_FAILURE_THRESHOLD = 5     # Number of failures before opening circuit
    DEFAULT_RECOVERY_TIMEOUT = 60     # Seconds to wait in OPEN state before trying HALF_OPEN
    DEFAULT_SUCCESS_THRESHOLD = 2     # Successful calls needed to close circuit from half-open
    MAX_STATE_CHANGES = 10             # Maximum state changes to keep in history
    LAST_STATE_CHANGES_DISPLAY = 5     # Number of recent state changes to show in stats
    
    # API-Specific Circuit Breaker Settings
    BINANCE_FAILURE_THRESHOLD = 3     # More sensitive for critical price data
    BINANCE_RECOVERY_TIMEOUT = 30     # Faster recovery for price APIs
    
    TOOBIT_FAILURE_THRESHOLD = 3      # More sensitive for exchange operations
    TOOBIT_RECOVERY_TIMEOUT = 60      # Longer recovery for exchange APIs
    
    COINGECKO_FAILURE_THRESHOLD = 4   # Less sensitive for fallback APIs
    COINGECKO_RECOVERY_TIMEOUT = 45   # Moderate recovery time
    
    CRYPTOCOMPARE_FAILURE_THRESHOLD = 4  # Less sensitive for fallback APIs
    CRYPTOCOMPARE_RECOVERY_TIMEOUT = 45  # Moderate recovery time
    
    HYPERLIQUID_FAILURE_THRESHOLD = 3    # More sensitive for exchange operations
    HYPERLIQUID_RECOVERY_TIMEOUT = 60    # Longer recovery for exchange APIs


# =============================================================================
# SMC ANALYSIS CONFIGURATION
# =============================================================================
class SMCConfig:
    # Market Structure Analysis
    MIN_CANDLESTICKS_FOR_STRUCTURE = 20  # Minimum candles needed for market structure analysis
    MIN_SWING_POINTS = 2                 # Minimum swing highs/lows needed for consolidation check
    
    # Swing Point Detection
    DEFAULT_LOOKBACK_PERIOD = 5          # Default lookback period for swing highs/lows
    CONTINUATION_LOOKAHEAD = 4           # Candles to look ahead for continuation strength
    
    # Fair Value Gap (FVG) Detection
    MIN_CANDLESTICKS_FOR_FVG = 3         # Minimum candles needed for FVG detection
    FVG_ATR_MULTIPLIER = 0.2             # Minimum FVG size as percentage of ATR (20% of ATR)
    FVG_MAX_AGE_CANDLES = 50             # Maximum age for FVG validity
    
    # Order Block Enhancement
    OB_IMPULSIVE_MOVE_THRESHOLD = 1.5    # Minimum displacement ratio for impulsive exit
    OB_VOLUME_MULTIPLIER = 1.2           # Minimum volume multiplier vs average
    OB_MAX_RETEST_COUNT = 2              # Maximum retests before OB becomes invalid
    OB_DISPLACEMENT_CANDLES = 3          # Candles to check for impulsive displacement
    
    # Liquidity Pool Analysis
    RECENT_SWING_LOOKBACK = 5            # Number of recent swing points to analyze for liquidity
    LIQUIDITY_SWEEP_WICK_RATIO = 0.3     # Minimum wick size vs candle body for sweep detection
    LIQUIDITY_CONFIRMATION_CANDLES = 2   # Candles needed for structural confirmation after sweep
    
    # Volume and Range Analysis
    VOLUME_RANGE_LOOKBACK = 10           # Candles to look back for volume/range calculations
    AVG_RANGE_PERIOD = 20                # Period for calculating average price range
    HIGH_VOLUME_THRESHOLD = 1.5          # Volume threshold for high volume validation
    
    # Trend Analysis
    MIN_PRICES_FOR_TREND = 2             # Minimum price points needed for trend analysis
    
    # Multi-Timeframe Confluence
    TIMEFRAME_ALIGNMENT_REQUIRED = True   # Enforce H1/H4 structure alignment
    DAILY_BIAS_WEIGHT = 2.0              # Weight multiplier for daily directional bias
    CONFLUENCE_MIN_SCORE = 3.0           # Minimum confluence score for signal generation
    
    # ATR Calculation
    ATR_PERIOD = 14                      # Period for Average True Range calculation
    ATR_SMOOTHING_FACTOR = 2.0           # EMA smoothing factor for ATR calculation


# =============================================================================
# CACHE CONFIGURATION
# =============================================================================
class CacheConfig:
    # Volatility Tracker Settings
    VOLATILITY_WINDOW_SIZE = 10          # Number of price points for volatility calculation
    VOLATILITY_CALCULATION_MULTIPLIER = 100  # Multiplier for volatility percentage calculation
    HIGH_VOLATILITY_THRESHOLD = 2.0     # Threshold for high volatility detection
    
    # Cache TTL Settings (seconds) - COST OPTIMIZED FOR RENDER
    BASE_PRICE_TTL = 8                   # Increased from 5s for fewer API calls
    MIN_PRICE_TTL = 2                    # Increased from 1s for high volatility assets
    MAX_PRICE_TTL = 20                   # Increased from 15s for better caching
    USER_DATA_TTL = 120                  # Increased to 2 minutes (was 1 minute)
    CREDENTIALS_TTL = 1800               # Increased to 30 minutes (was 15 minutes)
    PREFERENCES_TTL = 3600               # Increased to 1 hour (was 30 minutes)
    
    # TTL Multiplier Calculations
    MIN_TTL_MULTIPLIER = 0.2             # Minimum multiplier for high volatility
    MAX_TTL_MULTIPLIER = 3.0             # Maximum multiplier for low volatility
    VOLATILITY_DIVISOR = 10.0            # Divisor for volatility-based TTL calculation
    STABILITY_MULTIPLIER = 2.0           # Multiplier for stability calculation
    MIN_VOLATILITY_THRESHOLD = 0.1       # Minimum volatility threshold to prevent division by zero
    
    # Cleanup Settings - COST OPTIMIZED
    CLEANUP_INTERVAL = 120               # Reduced cleanup frequency (was 60s)
    
    # Hit Rate Calculation
    HIT_RATE_PERCENTAGE_MULTIPLIER = 100 # Multiplier for hit rate percentage calculation
    
    # Klines Cache Settings (minutes)
    KLINES_1H_CACHE_TTL = 15             # 15 minutes cache for 1h timeframe
    KLINES_4H_CACHE_TTL = 60             # 1 hour cache for 4h timeframe  
    KLINES_1D_CACHE_TTL = 240            # 4 hours cache for 1d timeframe
    KLINES_DATA_RETENTION_DAYS = 7       # Keep klines data for 7 days
    KLINES_CLEANUP_INTERVAL = 3600       # Clean up klines cache every hour (seconds)


# =============================================================================
# ERROR HANDLER CONFIGURATION
# =============================================================================
class ErrorConfig:
    # Retry Timeouts (seconds)
    API_KEY_RETRY_TIMEOUT = 60           # Retry timeout for API key errors
    RATE_LIMIT_RETRY_TIMEOUT = 300       # Retry timeout for rate limiting (5 minutes)
    NETWORK_RETRY_TIMEOUT = 30           # Retry timeout for network errors
    SERVER_ERROR_RETRY_TIMEOUT = 120     # Retry timeout for server errors (2 minutes)
    
    # HTTP Status Codes
    HTTP_UNAUTHORIZED = 401              # Unauthorized access
    HTTP_RATE_LIMITED = 429              # Rate limit exceeded
    HTTP_INTERNAL_ERROR = 500            # Internal server error
    HTTP_SERVICE_UNAVAILABLE = 503       # Service unavailable
    HTTP_SERVER_ERROR_MIN = 500          # Minimum server error code


# =============================================================================
# TIMEZONE CONFIGURATION
# =============================================================================
class TimezoneConfig:
    # GMT+3:30 (Iran Standard Time)
    DEFAULT_TIMEZONE = "Asia/Tehran"
    IRAN_TIMEZONE_HOURS = 3          # GMT+3:30 timezone hours offset
    IRAN_TIMEZONE_MINUTES = 30       # GMT+3:30 timezone minutes offset


# =============================================================================
# ENVIRONMENT-SPECIFIC SETTINGS
# =============================================================================
class Environment:
    # Environment Detection
    IS_VERCEL = bool(os.environ.get("VERCEL"))
    IS_RENDER = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
    IS_REPLIT = not IS_VERCEL and not IS_RENDER
    IS_DEVELOPMENT = os.environ.get("FLASK_ENV") == "development"
    IS_PRODUCTION = IS_VERCEL or IS_RENDER
    
    # Server Configuration
    DEFAULT_PORT = 5000
    DEFAULT_TEST_USER_ID = "123456789"  # For development/testing
    
    # Timezone
    DEFAULT_TIMEZONE = "Asia/Tehran"  # GMT+3:30 (Iran Standard Time)
    IRAN_TIMEZONE_HOURS = 3          # GMT+3:30 timezone hours offset
    IRAN_TIMEZONE_MINUTES = 30       # GMT+3:30 timezone minutes offset


# =============================================================================
# APPLICATION DEFAULTS  
# =============================================================================
class AppDefaults:
    # Bot Status Initial Values
    BOT_INITIAL_MESSAGES = 5
    BOT_INITIAL_TRADES = 2
    BOT_INITIAL_ERROR_COUNT = 0
    
    # Default Take Profit Allocation
    DEFAULT_TP_ALLOCATION = 100  # 100% allocation for single TP


# =============================================================================
# CONVENIENCE ACCESS METHODS
# =============================================================================
def get_api_timeout(api_type: str = "default") -> int:
    """Get timeout for specific API type.
    
    Args:
        api_type: Type of API operation ("default", "price", etc.)
        
    Returns:
        Timeout value in seconds
    """
    timeouts: Dict[str, int] = {
        "default": TimeConfig.DEFAULT_API_TIMEOUT,
        "price": TimeConfig.PRICE_API_TIMEOUT,
    }
    return timeouts.get(api_type, TimeConfig.DEFAULT_API_TIMEOUT)


def get_cache_ttl(cache_type: str = "price") -> int:
    """Get cache TTL for specific cache type.
    
    Args:
        cache_type: Type of cache ("price", "user", etc.)
        
    Returns:
        TTL value in seconds
    """
    ttls: Dict[str, int] = {
        "price": TimeConfig.PRICE_CACHE_TTL,
        "user": TimeConfig.USER_DATA_CACHE_TTL,
    }
    return ttls.get(cache_type, TimeConfig.PRICE_CACHE_TTL)


def get_database_url() -> Optional[str]:
    """Get database URL with proper formatting and validation.
    
    Returns:
        Validated database URL string or None if invalid/missing
    """
    database_url: Optional[str] = os.environ.get("DATABASE_URL")
    
    if not database_url:
        return None
    
    # Clean up the URL - remove any extra whitespace or newlines
    database_url = database_url.strip()
    
    # Handle common URL format issues
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    
    # Validate the URL format
    try:
        parsed = urlparse(database_url)
        
        # Check if it's a valid database URL
        if not parsed.scheme or not parsed.netloc:
            logging.warning(f"Invalid database URL format: {database_url[:20]}...")
            return None
            
        # Ensure postgresql scheme
        if parsed.scheme not in ['postgresql', 'sqlite']:
            logging.warning(f"Unsupported database scheme: {parsed.scheme}")
            return None
            
        return database_url
        
    except Exception as e:
        logging.error(f"Error parsing database URL: {e}")
        return None


def get_log_level() -> str:
    """Get appropriate log level for current environment.
    
    Returns:
        Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    if Environment.IS_VERCEL or Environment.IS_RENDER:
        return LoggingConfig.VERCEL_LOG_LEVEL  # Use production log level for both
    return LoggingConfig.REPLIT_LOG_LEVEL