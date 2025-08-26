"""
Centralized Configuration for Toobit Trading Bot
All constants, magic numbers, timeouts, and API endpoints are defined here
"""
import os

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
    
    # Fallback Price APIs  
    BINANCE_BASE_URL = "https://api.binance.com"
    COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
    CRYPTOCOMPARE_BASE_URL = "https://min-api.cryptocompare.com/data"


# =============================================================================
# TIMEOUTS AND INTERVALS
# =============================================================================
class TimeConfig:
    # API Request Timeouts
    DEFAULT_API_TIMEOUT = 30  # seconds
    PRICE_API_TIMEOUT = 15    # seconds for price fetching
    
    # Cache TTL
    PRICE_CACHE_TTL = 10      # seconds - how long to cache price data
    USER_DATA_CACHE_TTL = 30  # seconds - how long to cache user data
    
    # Sync Intervals
    EXCHANGE_SYNC_INTERVAL = 60    # seconds - background sync for Replit
    VERCEL_SYNC_COOLDOWN = 30      # seconds - cooldown between syncs for Vercel
    
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
    POOL_PRE_PING = True
    
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
    DEFAULT_SESSION_SECRET = "dev-secret-key"  # Only used in development
    
    # Webhook Security
    WEBHOOK_TIMEOUT = 30  # seconds
    WEBHOOK_SETUP_TIMEOUT = 10  # seconds for webhook setup calls
    
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
    
    # API-Specific Circuit Breaker Settings
    BINANCE_FAILURE_THRESHOLD = 3     # More sensitive for critical price data
    BINANCE_RECOVERY_TIMEOUT = 30     # Faster recovery for price APIs
    
    TOOBIT_FAILURE_THRESHOLD = 3      # More sensitive for exchange operations
    TOOBIT_RECOVERY_TIMEOUT = 60      # Longer recovery for exchange APIs
    
    COINGECKO_FAILURE_THRESHOLD = 4   # Less sensitive for fallback APIs
    COINGECKO_RECOVERY_TIMEOUT = 45   # Moderate recovery time
    
    CRYPTOCOMPARE_FAILURE_THRESHOLD = 4  # Less sensitive for fallback APIs
    CRYPTOCOMPARE_RECOVERY_TIMEOUT = 45  # Moderate recovery time


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
    
    # Liquidity Pool Analysis
    RECENT_SWING_LOOKBACK = 5            # Number of recent swing points to analyze for liquidity
    
    # Volume and Range Analysis
    VOLUME_RANGE_LOOKBACK = 10           # Candles to look back for volume/range calculations
    AVG_RANGE_PERIOD = 20                # Period for calculating average price range
    
    # Trend Analysis
    MIN_PRICES_FOR_TREND = 2             # Minimum price points needed for trend analysis


# =============================================================================
# CACHE CONFIGURATION
# =============================================================================
class CacheConfig:
    # Volatility Tracker Settings
    VOLATILITY_WINDOW_SIZE = 10          # Number of price points for volatility calculation
    VOLATILITY_CALCULATION_MULTIPLIER = 100  # Multiplier for volatility percentage calculation
    HIGH_VOLATILITY_THRESHOLD = 2.0     # Threshold for high volatility detection
    
    # Cache TTL Settings (seconds)
    BASE_PRICE_TTL = 10                  # Base TTL for price data
    MIN_PRICE_TTL = 2                    # Minimum TTL for high volatility assets
    MAX_PRICE_TTL = 30                   # Maximum TTL for stable assets
    USER_DATA_TTL = 300                  # User data cache TTL (5 minutes)
    CREDENTIALS_TTL = 1800               # API credentials TTL (30 minutes)
    PREFERENCES_TTL = 3600               # User preferences TTL (1 hour)
    
    # TTL Multiplier Calculations
    MIN_TTL_MULTIPLIER = 0.2             # Minimum multiplier for high volatility
    MAX_TTL_MULTIPLIER = 3.0             # Maximum multiplier for low volatility
    VOLATILITY_DIVISOR = 10.0            # Divisor for volatility-based TTL calculation
    STABILITY_MULTIPLIER = 2.0           # Multiplier for stability calculation
    MIN_VOLATILITY_THRESHOLD = 0.1       # Minimum volatility threshold to prevent division by zero
    
    # Cleanup Settings
    CLEANUP_INTERVAL = 60                # Cache cleanup interval in seconds
    
    # Hit Rate Calculation
    HIT_RATE_PERCENTAGE_MULTIPLIER = 100 # Multiplier for hit rate percentage calculation


# =============================================================================
# ERROR HANDLER CONFIGURATION
# =============================================================================
class ErrorConfig:
    # Retry Timeouts (seconds)
    API_KEY_RETRY_TIMEOUT = 60           # Retry timeout for API key errors
    RATE_LIMIT_RETRY_TIMEOUT = 300       # Retry timeout for rate limiting (5 minutes)
    NETWORK_RETRY_TIMEOUT = 30           # Retry timeout for network errors
    SERVER_ERROR_RETRY_TIMEOUT = 120     # Retry timeout for server errors (2 minutes)


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
    IS_REPLIT = not IS_VERCEL
    IS_DEVELOPMENT = os.environ.get("FLASK_ENV") == "development"
    
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
def get_api_timeout(api_type="default"):
    """Get timeout for specific API type"""
    timeouts = {
        "default": TimeConfig.DEFAULT_API_TIMEOUT,
        "price": TimeConfig.PRICE_API_TIMEOUT,
    }
    return timeouts.get(api_type, TimeConfig.DEFAULT_API_TIMEOUT)

def get_cache_ttl(cache_type="price"):
    """Get cache TTL for specific cache type"""
    ttls = {
        "price": TimeConfig.PRICE_CACHE_TTL,
        "user": TimeConfig.USER_DATA_CACHE_TTL,
    }
    return ttls.get(cache_type, TimeConfig.PRICE_CACHE_TTL)

def get_database_url():
    """Get database URL with proper formatting"""
    database_url = os.environ.get("DATABASE_URL")
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    return database_url

def get_log_level():
    """Get appropriate log level for current environment"""
    if Environment.IS_VERCEL:
        return LoggingConfig.VERCEL_LOG_LEVEL
    return LoggingConfig.REPLIT_LOG_LEVEL