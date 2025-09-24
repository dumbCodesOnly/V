import base64
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from cryptography.fernet import Fernet
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase

# GMT+3:30 timezone (Iran Standard Time)
try:
    from config import TimezoneConfig

    IRAN_TZ = timezone(
        timedelta(
            hours=TimezoneConfig.IRAN_TIMEZONE_HOURS,
            minutes=TimezoneConfig.IRAN_TIMEZONE_MINUTES,
        )
    )
except ImportError:
    # Fallback to hardcoded values if config not available
    IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def get_iran_time() -> datetime:
    """Get current time in GMT+3:30 timezone"""
    return datetime.now(IRAN_TZ)


def utc_to_iran_time(utc_dt: Optional[datetime]) -> Optional[datetime]:
    """Convert UTC datetime to GMT+3:30 timezone"""
    if utc_dt is None:
        return None
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(IRAN_TZ)


def get_utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime"""
    return datetime.now(timezone.utc)


def normalize_to_utc(dt: datetime) -> datetime:
    """Normalize datetime to timezone-aware UTC"""
    if dt.tzinfo is None:
        # Assume naive datetime is UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def floor_to_period(dt_utc: datetime, timeframe: str) -> datetime:
    """Floor datetime to start of trading period (timezone-aware UTC)"""
    dt_utc = normalize_to_utc(dt_utc)
    
    if timeframe == "1h":
        return dt_utc.replace(minute=0, second=0, microsecond=0)
    elif timeframe == "4h":
        hour = (dt_utc.hour // 4) * 4
        return dt_utc.replace(hour=hour, minute=0, second=0, microsecond=0)
    elif timeframe == "1d":
        return dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        # Default to hourly for unknown timeframes
        return dt_utc.replace(minute=0, second=0, microsecond=0)


def format_iran_time(
    dt: Optional[datetime], format_str: str = "%Y-%m-%d %H:%M:%S"
) -> str:
    """Format datetime in GMT+3:30 timezone"""
    if dt is None:
        return ""
    iran_time = (
        utc_to_iran_time(dt) if dt.tzinfo is None or dt.tzinfo == timezone.utc else dt
    )
    if iran_time is None:
        return ""
    return iran_time.strftime(format_str)


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


# Encryption key for API credentials - generated from app secret
def get_encryption_key() -> bytes:
    """Generate encryption key from app secret for consistent encryption"""
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise ValueError("SESSION_SECRET environment variable is required for encryption")
    key = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_data(data: Optional[str]) -> str:
    """Encrypt sensitive data"""
    if not data:
        return ""
    fernet = Fernet(get_encryption_key())
    return fernet.encrypt(data.encode()).decode()


def decrypt_data(encrypted_data: Optional[str]) -> str:
    """Decrypt sensitive data"""
    if not encrypted_data:
        return ""
    try:
        fernet = Fernet(get_encryption_key())
        return fernet.decrypt(encrypted_data.encode()).decode()
    except Exception:
        return ""


class UserCredentials(db.Model):
    """Store encrypted API credentials for each user"""

    __tablename__ = "user_credentials"

    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    telegram_username = db.Column(db.String(100))
    exchange_name = db.Column(
        db.String(50), default="lbank"
    )  # toobit, lbank, binance, etc

    # Encrypted API credentials
    api_key_encrypted = db.Column(db.Text)
    api_secret_encrypted = db.Column(db.Text)
    passphrase_encrypted = db.Column(db.Text)  # For some exchanges

    # API settings - Default to False for Toobit compatibility (no testnet support)
    testnet_mode = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_used = db.Column(db.DateTime)

    def set_api_key(self, api_key: Optional[str]) -> None:
        """Set encrypted API key"""
        self.api_key_encrypted = encrypt_data(api_key)

    def get_api_key(self) -> str:
        """Get decrypted API key"""
        return decrypt_data(self.api_key_encrypted)

    def set_api_secret(self, api_secret: Optional[str]) -> None:
        """Set encrypted API secret"""
        self.api_secret_encrypted = encrypt_data(api_secret)

    def get_api_secret(self) -> str:
        """Get decrypted API secret"""
        return decrypt_data(self.api_secret_encrypted)

    def set_passphrase(self, passphrase: Optional[str]) -> None:
        """Set encrypted passphrase"""
        self.passphrase_encrypted = encrypt_data(passphrase) if passphrase else ""

    def get_passphrase(self) -> str:
        """Get decrypted passphrase"""
        return decrypt_data(self.passphrase_encrypted)

    def has_credentials(self) -> bool:
        """Check if user has valid API credentials"""
        return bool(self.api_key_encrypted and self.api_secret_encrypted)

    def __repr__(self):
        return f"<UserCredentials {self.telegram_user_id}:{self.exchange_name}>"


class UserTradingSession(db.Model):
    """Track user trading sessions and API usage"""

    __tablename__ = "user_trading_sessions"

    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), nullable=False, index=True)
    session_start = db.Column(db.DateTime, default=datetime.utcnow)
    session_end = db.Column(db.DateTime)

    # Session metrics
    total_trades = db.Column(db.Integer, default=0)
    successful_trades = db.Column(db.Integer, default=0)
    failed_trades = db.Column(db.Integer, default=0)
    total_volume = db.Column(db.Float, default=0.0)

    # API status
    api_calls_made = db.Column(db.Integer, default=0)
    api_errors = db.Column(db.Integer, default=0)
    last_api_error = db.Column(db.Text)

    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<UserTradingSession {self.telegram_user_id}:{self.session_start}>"


class TradeConfiguration(db.Model):
    """Persistent storage for trade configurations"""

    __tablename__ = "trade_configurations"

    id = db.Column(db.Integer, primary_key=True)
    trade_id = db.Column(db.String(50), nullable=False, index=True)
    telegram_user_id = db.Column(db.String(50), nullable=False, index=True)

    # Basic trade info
    name = db.Column(db.String(200), nullable=False)
    symbol = db.Column(db.String(20), nullable=False)
    side = db.Column(db.String(10), nullable=False)  # 'long' or 'short'
    amount = db.Column(db.Float, nullable=False)  # Margin amount
    leverage = db.Column(db.Integer, default=1)

    # Entry configuration
    entry_type = db.Column(db.String(20), default="market")  # 'market' or 'limit'
    entry_price = db.Column(db.Float, default=0.0)

    # Risk management (stored as JSON)
    take_profits = db.Column(db.Text)  # JSON string of TP levels
    stop_loss_percent = db.Column(db.Float, default=0.0)
    breakeven_after = db.Column(db.Float, default=0.0)
    breakeven_sl_triggered = db.Column(db.Boolean, default=False)

    # Trailing stop configuration
    trailing_stop_enabled = db.Column(db.Boolean, default=False)
    trail_percentage = db.Column(db.Float, default=0.0)
    trail_activation_price = db.Column(db.Float, default=0.0)

    # Status and tracking
    status = db.Column(
        db.String(20), default="configured"
    )  # configured, pending, active, stopped
    position_margin = db.Column(db.Float, default=0.0)
    unrealized_pnl = db.Column(db.Float, default=0.0)
    current_price = db.Column(db.Float, default=0.0)
    position_size = db.Column(db.Float, default=0.0)
    position_value = db.Column(db.Float, default=0.0)
    realized_pnl = db.Column(db.Float, default=0.0)
    final_pnl = db.Column(db.Float, default=0.0)
    closed_at = db.Column(db.DateTime)

    # Original position tracking for partial closes
    original_amount = db.Column(db.Float, default=0.0)  # Original position size
    original_margin = db.Column(db.Float, default=0.0)  # Original margin used

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_trade_config(self):
        """Convert database model to TradeConfig object"""
        # Import locally to avoid circular dependency
        from . import app

        config = app.TradeConfig(self.trade_id, self.name)
        config.symbol = self.symbol
        config.side = self.side
        config.amount = self.amount
        config.leverage = self.leverage
        config.entry_type = self.entry_type
        config.entry_price = self.entry_price
        config.stop_loss_percent = self.stop_loss_percent
        # Convert breakeven_after back to expected format
        if isinstance(self.breakeven_after, str):
            if self.breakeven_after == "tp1":
                config.breakeven_after = 1.0
            elif self.breakeven_after == "tp2":
                config.breakeven_after = 2.0
            elif self.breakeven_after == "tp3":
                config.breakeven_after = 3.0
            elif self.breakeven_after == "disabled":
                config.breakeven_after = 0.0
            else:
                try:
                    config.breakeven_after = float(self.breakeven_after)
                except (ValueError, TypeError):
                    config.breakeven_after = 0.0
        else:
            config.breakeven_after = (
                float(self.breakeven_after) if self.breakeven_after else 0.0
            )
        config.breakeven_sl_triggered = getattr(self, "breakeven_sl_triggered", False)
        # Set breakeven SL price to entry price when breakeven is triggered
        config.breakeven_sl_price = (
            self.entry_price if config.breakeven_sl_triggered else 0.0
        )
        config.trailing_stop_enabled = self.trailing_stop_enabled
        config.trail_percentage = self.trail_percentage
        config.trail_activation_price = self.trail_activation_price
        config.status = self.status
        config.position_margin = self.position_margin
        config.unrealized_pnl = self.unrealized_pnl
        config.current_price = self.current_price
        config.position_size = self.position_size
        config.position_value = self.position_value
        config.realized_pnl = getattr(self, "realized_pnl", 0.0)
        config.final_pnl = self.final_pnl
        config.closed_at = format_iran_time(self.closed_at) if self.closed_at else ""

        # Set exchange (will be determined at execution time, default to lbank)
        config.exchange = getattr(self, "exchange", "lbank")

        # Parse take profits JSON
        import json

        if self.take_profits:
            try:
                config.take_profits = json.loads(self.take_profits)
            except (json.JSONDecodeError, ValueError):
                config.take_profits = []
        else:
            config.take_profits = []

        return config

    @staticmethod
    def from_trade_config(user_id, config):
        """Create database model from TradeConfig object"""
        import json

        db_config = TradeConfiguration()
        db_config.trade_id = config.trade_id
        db_config.telegram_user_id = str(user_id)
        db_config.name = config.name
        db_config.symbol = config.symbol
        db_config.side = config.side
        db_config.amount = config.amount
        db_config.leverage = config.leverage
        db_config.entry_type = config.entry_type
        db_config.entry_price = config.entry_price
        db_config.take_profits = json.dumps(config.take_profits)
        db_config.stop_loss_percent = config.stop_loss_percent
        # Convert breakeven_after - handle string values
        if hasattr(config, "breakeven_after"):
            if config.breakeven_after == "disabled" or config.breakeven_after == 0:
                db_config.breakeven_after = 0.0
            elif config.breakeven_after == "tp1":
                db_config.breakeven_after = 1.0
            elif config.breakeven_after == "tp2":
                db_config.breakeven_after = 2.0
            elif config.breakeven_after == "tp3":
                db_config.breakeven_after = 3.0
            else:
                try:
                    db_config.breakeven_after = float(config.breakeven_after)
                except (ValueError, TypeError):
                    db_config.breakeven_after = 0.0
        else:
            db_config.breakeven_after = 0.0
        db_config.breakeven_sl_triggered = getattr(
            config, "breakeven_sl_triggered", False
        )
        db_config.trailing_stop_enabled = config.trailing_stop_enabled
        db_config.trail_percentage = config.trail_percentage
        db_config.trail_activation_price = config.trail_activation_price
        db_config.status = config.status
        db_config.position_margin = config.position_margin
        db_config.unrealized_pnl = config.unrealized_pnl
        db_config.current_price = config.current_price
        db_config.position_size = config.position_size
        db_config.position_value = config.position_value
        db_config.realized_pnl = getattr(config, "realized_pnl", 0.0)
        db_config.final_pnl = config.final_pnl

        if config.closed_at and config.closed_at != "":
            try:
                from datetime import datetime

                db_config.closed_at = datetime.fromisoformat(
                    config.closed_at.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        return db_config

    def __repr__(self):
        return f"<TradeConfiguration {self.telegram_user_id}:{self.trade_id}>"


class SMCSignalCache(db.Model):
    """Cache SMC signals to reduce frequent recalculation and entry price changes"""

    __tablename__ = "smc_signal_cache"

    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), nullable=False, index=True)
    direction = db.Column(db.String(10), nullable=False)  # 'long' or 'short'
    entry_price = db.Column(db.Float, nullable=False)
    stop_loss = db.Column(db.Float, nullable=False)
    take_profit_levels = db.Column(db.Text, nullable=False)  # JSON array of TP levels
    confidence = db.Column(db.Float, nullable=False)
    reasoning = db.Column(db.Text, nullable=False)  # JSON array of reasoning
    signal_strength = db.Column(
        db.String(20), nullable=False
    )  # WEAK, MODERATE, STRONG, VERY_STRONG
    risk_reward_ratio = db.Column(db.Float, nullable=False)

    # Caching metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    market_price_at_signal = db.Column(
        db.Float, nullable=False
    )  # Price when signal was generated

    # Add index for efficient queries
    __table_args__ = (db.Index("idx_symbol_expires", "symbol", "expires_at"),)

    def is_expired(self):
        """Check if the signal has expired"""
        # Use naive UTC datetime for consistency with database storage
        now = datetime.utcnow()
        return now > self.expires_at

    def is_price_still_valid(self, current_price, tolerance_percent=2.0):
        """Check if current price is still within tolerance of signal price"""
        price_change = (
            abs(current_price - self.market_price_at_signal)
            / self.market_price_at_signal
            * 100
        )
        return price_change <= tolerance_percent
    
    def get_age_in_minutes(self):
        """Get the age of the signal in minutes"""
        # Use naive UTC datetime for consistency
        now = datetime.utcnow()
        # Assume created_at is already naive UTC (as stored in DB)
        return (now - self.created_at).total_seconds() / 60
    
    def get_adjusted_confidence(self, current_price=None):
        """Get confidence adjusted for signal age and price movement"""
        base_confidence = self.confidence
        
        # Age-based degradation: reduce confidence over time based on signal strength
        age_minutes = self.get_age_in_minutes()
        # Use signal strength to determine degradation window
        strength_to_duration = {
            "VERY_STRONG": 20,
            "STRONG": 15,
            "MODERATE": 10, 
            "WEAK": 5
        }
        degradation_window = strength_to_duration.get(self.signal_strength, 15)
        age_factor = max(0.3, 1.0 - (age_minutes / degradation_window))
        
        # Price movement penalty
        price_factor = 1.0
        if current_price is not None:
            price_change = abs(current_price - self.market_price_at_signal) / self.market_price_at_signal * 100
            if price_change > 2.0:  # If price moved more than 2%
                price_factor = max(0.3, 1.0 - (price_change - 2.0) / 10.0)  # Reduce confidence
        
        return base_confidence * age_factor * price_factor

    def to_smc_signal(self):
        """Convert database model to SMCSignal object"""
        import json

        from .smc_analyzer import SignalStrength, SMCSignal

        # Parse JSON fields
        take_profits = json.loads(self.take_profit_levels)
        reasoning_list = json.loads(self.reasoning)

        # Convert string to enum
        strength_map = {
            "WEAK": SignalStrength.WEAK,
            "MODERATE": SignalStrength.MODERATE,
            "STRONG": SignalStrength.STRONG,
            "VERY_STRONG": SignalStrength.VERY_STRONG,
        }
        signal_strength = strength_map.get(self.signal_strength, SignalStrength.WEAK)

        return SMCSignal(
            symbol=self.symbol,
            direction=self.direction,
            entry_price=self.entry_price,
            stop_loss=self.stop_loss,
            take_profit_levels=take_profits,
            confidence=self.confidence,
            reasoning=reasoning_list,
            signal_strength=signal_strength,
            risk_reward_ratio=self.risk_reward_ratio,
            timestamp=self.created_at,
        )

    @classmethod
    def from_smc_signal(cls, signal, cache_duration_minutes=None):
        """Create database model from SMCSignal object with dynamic cache duration"""
        import json
        
        # Dynamic cache duration based on signal strength and market conditions
        if cache_duration_minutes is None:
            # Use enum name comparison for robustness
            strength_name = signal.signal_strength.name if hasattr(signal.signal_strength, 'name') else str(signal.signal_strength)
            
            if strength_name == "VERY_STRONG":
                cache_duration_minutes = 30  # Longer for very strong signals (50% increase: 20→30)
            elif strength_name == "STRONG":
                cache_duration_minutes = 23  # Standard duration (50% increase: 15→23)
            elif strength_name == "MODERATE":
                cache_duration_minutes = 15  # Shorter for moderate signals (50% increase: 10→15)
            else:  # WEAK or unknown
                cache_duration_minutes = 8   # Very short for weak signals (50% increase: 5→8)

        # Use naive UTC datetime for consistent database storage
        expires_at = datetime.utcnow() + timedelta(minutes=cache_duration_minutes)

        return cls(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit_levels=json.dumps(signal.take_profit_levels),
            confidence=signal.confidence,
            reasoning=json.dumps(signal.reasoning),
            signal_strength=signal.signal_strength.name,  # Store enum name for consistency
            risk_reward_ratio=signal.risk_reward_ratio,
            expires_at=expires_at,
            market_price_at_signal=signal.entry_price,
        )

    @classmethod
    def get_valid_signal(cls, symbol, current_price=None, min_confidence=0.3):
        """Get a valid cached signal for symbol with enhanced validation"""
        # Use naive UTC datetime for consistent database comparison
        now = datetime.utcnow()
        
        # Get most recent non-expired signal
        signal = (
            cls.query.filter(cls.symbol == symbol, cls.expires_at > now)
            .order_by(cls.created_at.desc())
            .first()
        )

        if not signal:
            return None

        # Enhanced validation with multiple checks
        
        # 1. Price tolerance check (2% default)
        if current_price is not None:
            if not signal.is_price_still_valid(current_price, tolerance_percent=2.0):
                logging.info(f"Signal for {symbol} invalidated due to price change beyond 2% tolerance")
                return None
        
        # 2. Age-adjusted confidence check
        adjusted_confidence = signal.get_adjusted_confidence(current_price)
        if adjusted_confidence < min_confidence:
            logging.info(f"Signal for {symbol} invalidated due to low adjusted confidence: {adjusted_confidence:.2f}")
            return None
        
        # 3. Check if signal is too old (respect the original cache duration)
        age_minutes = signal.get_age_in_minutes()
        # Calculate original cache duration from signal strength
        strength_to_duration = {
            "VERY_STRONG": 20,
            "STRONG": 15, 
            "MODERATE": 10,
            "WEAK": 5
        }
        max_age_minutes = strength_to_duration.get(signal.signal_strength, 15)
        if age_minutes > max_age_minutes:
            logging.info(f"Signal for {symbol} invalidated due to age: {age_minutes:.1f} minutes (max: {max_age_minutes})")
            return None

        return signal

    @classmethod
    def cleanup_expired(cls):
        """Remove expired signals from cache"""
        # Use naive UTC datetime for consistency with database storage
        expired_count = cls.query.filter(cls.expires_at <= datetime.utcnow()).delete()
        db.session.commit()
        return expired_count

    def __repr__(self):
        return f"<SMCSignalCache {self.symbol}: {self.direction} @ {self.entry_price}>"


class UserWhitelist(db.Model):
    """Manage user whitelist for access control"""

    __tablename__ = "user_whitelist"

    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    telegram_username = db.Column(db.String(100))
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    
    # Whitelist status: pending, approved, rejected, banned
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    
    # Bot owner information (who approved/rejected)
    reviewed_by = db.Column(db.String(50))  # Bot owner's telegram_user_id
    review_notes = db.Column(db.Text)  # Optional notes from reviewer
    
    # Timestamps
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Access tracking
    last_access = db.Column(db.DateTime)
    access_count = db.Column(db.Integer, default=0)
    
    def is_approved(self) -> bool:
        """Check if user is approved for access"""
        return self.status == "approved"
    
    def is_pending(self) -> bool:
        """Check if user is pending approval"""
        return self.status == "pending"
    
    def is_rejected(self) -> bool:
        """Check if user is rejected"""
        return self.status == "rejected"
    
    def is_banned(self) -> bool:
        """Check if user is banned"""
        return self.status == "banned"
    
    def approve(self, reviewer_id: str, notes: Optional[str] = None):
        """Approve user access"""
        self.status = "approved"
        self.reviewed_by = reviewer_id
        self.review_notes = notes
        self.reviewed_at = datetime.utcnow()
    
    def reject(self, reviewer_id: str, notes: Optional[str] = None):
        """Reject user access"""
        self.status = "rejected"
        self.reviewed_by = reviewer_id
        self.review_notes = notes
        self.reviewed_at = datetime.utcnow()
    
    def ban(self, reviewer_id: str, notes: Optional[str] = None):
        """Ban user access"""
        self.status = "banned"
        self.reviewed_by = reviewer_id
        self.review_notes = notes
        self.reviewed_at = datetime.utcnow()
    
    def record_access(self):
        """Record user access"""
        self.last_access = datetime.utcnow()
        self.access_count = (self.access_count or 0) + 1
    
    def __repr__(self):
        return f"<UserWhitelist {self.telegram_user_id}:{self.status}>"


class KlinesCache(db.Model):
    """Cache candlestick (klines) data to reduce API calls and improve performance"""

    __tablename__ = "klines_cache"

    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), nullable=False, index=True)
    timeframe = db.Column(db.String(10), nullable=False, index=True)  # '1h', '4h', '1d'
    timestamp = db.Column(
        db.DateTime, nullable=False, index=True
    )  # Candlestick timestamp

    # OHLCV data
    open = db.Column(db.Float, nullable=False)
    high = db.Column(db.Float, nullable=False)
    low = db.Column(db.Float, nullable=False)
    close = db.Column(db.Float, nullable=False)
    volume = db.Column(db.Float, nullable=False)

    # Cache metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_complete = db.Column(
        db.Boolean, default=True
    )  # False for incomplete candles (current period)

    # Composite indexes and constraints for efficient queries and data integrity
    __table_args__ = (
        db.Index(
            "idx_klines_symbol_timeframe_timestamp", "symbol", "timeframe", "timestamp"
        ),
        db.Index("idx_klines_expires", "expires_at"),
        db.Index(
            "idx_klines_symbol_timeframe_expires", "symbol", "timeframe", "expires_at"
        ),
        db.UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_klines_symbol_tf_timestamp"),
    )

    def is_expired(self):
        """Check if the cached data has expired"""
        return datetime.utcnow() > self.expires_at

    def to_candlestick_dict(self):
        """Convert database model to candlestick dictionary format"""
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    @classmethod
    def get_cached_data(
        cls,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        include_incomplete: bool = True,
    ):
        """Get cached candlestick data for symbol and timeframe"""
        current_time = get_utc_now()
        query = cls.query.filter(
            cls.symbol == symbol,
            cls.timeframe == timeframe,
            cls.expires_at > current_time.replace(tzinfo=None),  # Store as naive UTC
        )

        if not include_incomplete:
            query = query.filter(cls.is_complete.is_(True))

        # Get the most recent data, ordered by timestamp
        cached_data = query.order_by(cls.timestamp.desc()).limit(limit).all()

        if not cached_data:
            return []

        # Convert to list of dictionaries and reverse to get chronological order
        candlesticks = [
            candle.to_candlestick_dict() for candle in reversed(cached_data)
        ]
        return candlesticks

    @classmethod
    def save_klines_batch(
        cls,
        symbol: str,
        timeframe: str,
        candlesticks: list,
        cache_ttl_minutes: int = 15,
    ):
        """Save a batch of candlestick data to cache with intelligent TTL"""
        from sqlalchemy import text

        current_time = get_utc_now()
        
        # Intelligent TTL based on candle completeness
        complete_candle_ttl_days = 21  # Complete candles cached for 21 days (aligned with retention)
        incomplete_candle_ttl_minutes = cache_ttl_minutes  # Incomplete candles use short TTL

        # Get current period start for proper completeness detection
        current_period_start = floor_to_period(current_time, timeframe)
        
        # Prepare batch data
        klines_to_insert = []

        for candle in candlesticks:
            # Parse and normalize timestamp to UTC
            candle_time = candle["timestamp"]
            if isinstance(candle_time, str):
                candle_time = datetime.fromisoformat(candle_time.replace("Z", "+00:00"))
            elif isinstance(candle_time, (int, float)):
                candle_time = datetime.fromtimestamp(candle_time / 1000, tz=timezone.utc)
            
            # Normalize to timezone-aware UTC
            candle_time = normalize_to_utc(candle_time)
            
            # Determine if candle is complete using proper period calculation
            candle_period_start = floor_to_period(candle_time, timeframe)
            is_complete = candle_period_start < current_period_start

            # Intelligent TTL: Complete candles get long cache time, incomplete get short
            if is_complete:
                expires_at = current_time + timedelta(days=complete_candle_ttl_days)
            else:
                expires_at = current_time + timedelta(minutes=incomplete_candle_ttl_minutes)

            klines_to_insert.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": candle_time,
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle["volume"]),
                    "expires_at": expires_at,
                    "is_complete": is_complete,
                    "created_at": current_time,
                }
            )

        if klines_to_insert:
            # Use PostgreSQL ON CONFLICT for atomic upsert
            try:
                from sqlalchemy.dialects.postgresql import insert
                from sqlalchemy import text
                
                # Batch insert with ON CONFLICT DO UPDATE
                stmt = insert(cls.__table__).values(klines_to_insert)  # type: ignore
                
                # Only update if not downgrading: never change complete to incomplete
                update_dict = {
                    'open': stmt.excluded.open,
                    'high': stmt.excluded.high,
                    'low': stmt.excluded.low,
                    'close': stmt.excluded.close,
                    'volume': stmt.excluded.volume,
                    'expires_at': stmt.excluded.expires_at,
                    'created_at': stmt.excluded.created_at,
                    # Only promote incomplete→complete, never downgrade
                    'is_complete': text('CASE WHEN klines_cache.is_complete = true THEN true ELSE excluded.is_complete END')
                }
                
                upsert_stmt = stmt.on_conflict_do_update(
                    constraint='uq_klines_symbol_tf_timestamp',
                    set_=update_dict,
                    where=text('klines_cache.is_complete = false OR excluded.is_complete = true')
                )
                
                result = db.session.execute(upsert_stmt)
                db.session.commit()
                
                logging.debug(f"Klines batch upsert completed: {len(klines_to_insert)} records processed")
                return len(klines_to_insert)
                
            except Exception as e:
                logging.warning(f"PostgreSQL upsert failed, falling back to individual operations: {e}")
                db.session.rollback()
                
                # Fallback to individual upsert operations
                inserted_count = 0
                updated_count = 0
                
                for kline_data in klines_to_insert:
                    try:
                        existing = cls.query.filter(
                            cls.symbol == kline_data["symbol"],
                            cls.timeframe == kline_data["timeframe"],
                            cls.timestamp == kline_data["timestamp"],
                        ).first()

                        if not existing:
                            new_kline = cls(**kline_data)
                            db.session.add(new_kline)
                            db.session.flush()  # Flush to catch IntegrityError early
                            inserted_count += 1
                        else:
                            # Never downgrade: only update if promoting or both incomplete
                            should_update = (
                                not existing.is_complete and kline_data["is_complete"]  # Promote incomplete→complete
                                or (not existing.is_complete and not kline_data["is_complete"])  # Update incomplete→incomplete
                            )
                            
                            if should_update:
                                existing.open = kline_data["open"]
                                existing.high = kline_data["high"]
                                existing.low = kline_data["low"]
                                existing.close = kline_data["close"]
                                existing.volume = kline_data["volume"]
                                existing.expires_at = kline_data["expires_at"]
                                existing.is_complete = kline_data["is_complete"]
                                existing.created_at = current_time
                                updated_count += 1

                    except Exception as inner_e:
                        logging.warning(f"Failed to upsert individual kline: {inner_e}")
                        db.session.rollback()
                        continue

                try:
                    db.session.commit()
                    logging.debug(f"Fallback upsert: {inserted_count} inserted, {updated_count} updated")
                    return inserted_count + updated_count
                except Exception as commit_e:
                    logging.error(f"Failed to commit klines batch: {commit_e}")
                    db.session.rollback()
                    return 0

        return 0

    @classmethod
    def update_open_candle(cls, symbol: str, timeframe: str, open_price: float, high: float, low: float, close: float, volume: float, timestamp: datetime, cache_ttl_minutes: int = 5) -> bool:
        """
        Efficiently update an existing open candle in place, or create if doesn't exist.
        This is much more efficient than deleting and refetching open candles.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe ('1h', '4h', '1d')
            open_price, high, low, close, volume: OHLCV data
            timestamp: Candle timestamp
            cache_ttl_minutes: TTL for the open candle
            
        Returns:
            True if successful, False otherwise
        """
        try:
            current_time = get_utc_now()
            
            # Normalize timestamp to UTC
            candle_time = normalize_to_utc(timestamp)
            
            # Determine if this candle is complete
            candle_period_start = floor_to_period(candle_time, timeframe)
            current_period_start = floor_to_period(current_time, timeframe)
            is_complete = candle_period_start < current_period_start
            
            # Set appropriate expiry time
            if is_complete:
                expires_at = current_time + timedelta(days=21)  # Long TTL for complete candles
            else:
                expires_at = current_time + timedelta(minutes=cache_ttl_minutes)  # Short TTL for open candles
            
            # Check if candle already exists
            existing_candle = cls.query.filter(
                cls.symbol == symbol,
                cls.timeframe == timeframe,
                cls.timestamp == candle_time
            ).first()
            
            if existing_candle:
                # Update existing candle in place (much more efficient)
                existing_candle.high = max(existing_candle.high, high)  # Keep highest high
                existing_candle.low = min(existing_candle.low, low)     # Keep lowest low
                existing_candle.close = close                           # Update current close
                existing_candle.volume = volume                         # Update current volume
                existing_candle.expires_at = expires_at               # Update expiry
                existing_candle.created_at = current_time              # Update timestamp
                
                # Only promote incomplete→complete, never downgrade
                if not existing_candle.is_complete and is_complete:
                    existing_candle.is_complete = True
                    
                logging.debug(f"Updated open candle for {symbol} {timeframe}: H:{high} L:{low} C:{close} V:{volume}")
                
            else:
                # Create new candle if doesn't exist
                new_candle = cls(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=candle_time,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    expires_at=expires_at,
                    is_complete=is_complete,
                    created_at=current_time
                )
                db.session.add(new_candle)
                logging.debug(f"Created new candle for {symbol} {timeframe}: O:{open_price} H:{high} L:{low} C:{close} V:{volume}")
            
            db.session.commit()
            return True
            
        except Exception as e:
            logging.error(f"Error updating open candle for {symbol} {timeframe}: {e}")
            db.session.rollback()
            return False

    @classmethod
    def get_current_open_candle(cls, symbol: str, timeframe: str) -> Optional['KlinesCache']:
        """
        Get the current open candle for a symbol/timeframe if it exists.
        This is used to check if we need to fetch new data or just update existing.
        
        Returns:
            KlinesCache object if open candle exists, None otherwise
        """
        try:
            current_time = get_utc_now()
            current_period_start = floor_to_period(current_time, timeframe)
            
            # Look for incomplete candle for the current period
            open_candle = cls.query.filter(
                cls.symbol == symbol,
                cls.timeframe == timeframe,
                cls.timestamp == current_period_start,
                cls.is_complete == False,
                cls.expires_at > current_time.replace(tzinfo=None)
            ).first()
            
            return open_candle
            
        except Exception as e:
            logging.error(f"Error getting current open candle for {symbol} {timeframe}: {e}")
            return None

    @classmethod
    def get_data_gaps(cls, symbol: str, timeframe: str, required_count: int):
        """Identify gaps in cached data to determine what needs fetching"""
        # Get the most recent cached complete data
        latest_complete = (
            cls.query.filter(
                cls.symbol == symbol,
                cls.timeframe == timeframe,
                cls.is_complete.is_(True),
                cls.expires_at > get_utc_now().replace(tzinfo=None),
            )
            .order_by(cls.timestamp.desc())
            .first()
        )

        if not latest_complete:
            # No cached data, need to fetch everything
            return {
                "needs_fetch": True,
                "fetch_count": required_count,
                "has_cached_data": False,
            }

        # Count available complete data and find latest timestamp
        from sqlalchemy import func
        complete_query = cls.query.filter(
            cls.symbol == symbol,
            cls.timeframe == timeframe,
            cls.is_complete.is_(True),
            cls.expires_at > datetime.utcnow(),
        )
        
        available_count = complete_query.with_entities(func.count(func.distinct(cls.timestamp))).scalar()
        latest_complete = complete_query.order_by(cls.timestamp.desc()).first()

        # Calculate time period in seconds
        period_seconds = {"1h": 3600, "4h": 14400, "1d": 86400}.get(timeframe, 3600)
        current_time = get_utc_now()
        current_period_start = floor_to_period(current_time, timeframe)
        
        if latest_complete:
            # Normalize latest complete timestamp to UTC
            latest_complete_utc = normalize_to_utc(latest_complete.timestamp)
            latest_complete_period = floor_to_period(latest_complete_utc, timeframe)
            
            # Calculate periods elapsed from period start to period start (correct math)
            time_diff = (current_period_start - latest_complete_period).total_seconds()
            periods_elapsed = max(0, int(time_diff // period_seconds))
            
            # Check if current period's incomplete candle exists
            current_incomplete = cls.query.filter(
                cls.symbol == symbol,
                cls.timeframe == timeframe,
                cls.timestamp == current_period_start.replace(tzinfo=None),  # Store as naive UTC
                cls.is_complete.is_(False),
                cls.expires_at > current_time.replace(tzinfo=None),
            ).first()
            
            # If no new periods elapsed and current incomplete exists, no fetch needed
            if periods_elapsed == 0 and current_incomplete:
                return {"needs_fetch": False, "fetch_count": 0, "has_cached_data": True}
            
            # Calculate how many new candles we need to fetch (fixed: no +1 over-fetching)
            if periods_elapsed == 0:
                # Only need current incomplete if it doesn't exist
                fetch_count = 1 if not current_incomplete else 0
            else:
                # Fetch the missing periods (no +1 to avoid over-fetching)
                fetch_count = min(required_count, periods_elapsed)
                # Add 1 for current incomplete only if it doesn't exist
                if not current_incomplete:
                    fetch_count = min(required_count, fetch_count + 1)
            
            if fetch_count == 0:
                return {"needs_fetch": False, "fetch_count": 0, "has_cached_data": True}
            
            return {
                "needs_fetch": True,
                "fetch_count": fetch_count,
                "has_cached_data": True,
                "from_timestamp": latest_complete.timestamp,
            }
        elif available_count >= required_count:
            # Have enough old data but no recent - fetch just recent periods
            fetch_count = min(required_count, 5)  # Fetch last 5 periods to get recent data
            return {
                "needs_fetch": True,
                "fetch_count": fetch_count,
                "has_cached_data": True,
            }
        else:
            # Need to fetch additional data
            missing_count = (
                required_count - available_count + 1
            )  # +1 for current incomplete candle
            return {
                "needs_fetch": True,
                "fetch_count": missing_count,
                "has_cached_data": True,
            }

    @classmethod
    def cleanup_expired(cls):
        """Remove expired klines cache entries"""
        expired_count = cls.query.filter(cls.expires_at <= datetime.utcnow()).delete()
        db.session.commit()
        return expired_count

    @classmethod
    def cleanup_old_data(cls, days_to_keep: int = 7):
        """Remove old klines data beyond retention period"""
        cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
        old_count = cls.query.filter(cls.created_at <= cutoff_date).delete()
        db.session.commit()
        return old_count

    @classmethod
    def cleanup_rolling_window(cls, symbol: str, timeframe: str, max_candles: int, batch_size: int = 10):
        """
        CONSERVATIVE rolling window cleanup - only delete oldest candles when we have significant buffer.
        This implements the user's requirement: keep recent candles, only delete very old ones.
        
        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            timeframe: Timeframe ('1h', '4h', '1d')
            max_candles: Maximum number of candles to keep after cleanup
            batch_size: Number of records to delete in each batch (small for gentle cleanup)
            
        Returns:
            int: Number of candles deleted
        """
        from config import RollingWindowConfig
        
        try:
            # Count ALL candles for this symbol/timeframe
            total_count = cls.query.filter(
                cls.symbol == symbol,
                cls.timeframe == timeframe
            ).count()
            
            # Get the cleanup threshold - we only start cleanup when we have MUCH more data
            cleanup_threshold = RollingWindowConfig.get_cleanup_threshold(timeframe)
            
            # CONSERVATIVE LOGIC: Only cleanup if we have significantly more candles than threshold
            if total_count <= cleanup_threshold:
                logging.debug(f"CONSERVATIVE_CLEANUP: {symbol}:{timeframe} has {total_count} candles, threshold is {cleanup_threshold} - no cleanup needed")
                return 0  # Don't cleanup until we have a significant buffer
            
            # Calculate how many to delete (keep max_candles + safety margin)
            target_after_cleanup = max_candles  # This includes safety margin already
            to_delete = total_count - target_after_cleanup
            
            # Additional safety check - never delete more than 25% in one cleanup cycle
            max_safe_deletion = max(1, int(total_count * 0.25))
            to_delete = min(to_delete, max_safe_deletion)
            
            if to_delete <= 0:
                return 0
            
            logging.info(f"CONSERVATIVE_CLEANUP: {symbol}:{timeframe} has {total_count} candles (threshold: {cleanup_threshold}), will delete {to_delete} oldest candles to reach target {target_after_cleanup}")
            
            deleted_count = 0
            
            # Delete in batches to avoid database locks
            while to_delete > 0:
                current_batch = min(batch_size, to_delete)
                
                # CONSERVATIVE DELETION: Only delete the OLDEST complete candles
                # NEVER delete incomplete (recent) candles - only update them
                # Priority: Only delete very old complete candles first
                oldest_candles = cls.query.filter(
                    cls.symbol == symbol,
                    cls.timeframe == timeframe,
                    cls.is_complete == True  # ONLY delete complete (old) candles
                ).order_by(
                    cls.timestamp.asc()  # Oldest first - keep newest candles safe
                ).limit(current_batch).all()
                
                # Safety check: Don't delete candles that are too recent
                current_time = get_utc_now()
                safe_candles = []
                for candle in oldest_candles:
                    # Additional safety: Don't delete candles from last 24 hours for 1h, 4 days for 4h, 7 days for 1d
                    if timeframe == "1h":
                        min_age_hours = 24  # Don't delete hourly candles less than 24h old
                    elif timeframe == "4h":
                        min_age_hours = 96  # Don't delete 4h candles less than 4 days old
                    else:  # 1d
                        min_age_hours = 168  # Don't delete daily candles less than 7 days old
                        
                    age_hours = (current_time - candle.timestamp).total_seconds() / 3600
                    if age_hours >= min_age_hours:
                        safe_candles.append(candle)
                        
                oldest_candles = safe_candles
                
                if not oldest_candles:
                    break  # No more candles to delete
                
                # Delete this batch
                for candle in oldest_candles:
                    db.session.delete(candle)
                
                db.session.commit()
                
                batch_deleted = len(oldest_candles)
                deleted_count += batch_deleted
                to_delete -= batch_deleted
                
                # Safety check - if we're not making progress, break
                if batch_deleted == 0:
                    break
            
            return deleted_count
            
        except Exception as e:
            logging.error(f"Error in rolling window cleanup for {symbol}:{timeframe}: {e}")
            db.session.rollback()
            return 0

    @classmethod 
    def cleanup_all_rolling_windows(cls, batch_size: int = 10):
        """
        Cleanup rolling windows for all symbols and timeframes.
        This ensures we maintain the configured window size for all data.
        
        Args:
            batch_size: Number of records to delete in each batch
            
        Returns:
            dict: Summary of cleanup results per symbol/timeframe
        """
        from config import RollingWindowConfig
        
        cleanup_results = {}
        total_deleted = 0
        
        try:
            # Get all unique symbol/timeframe combinations
            distinct_combinations = db.session.query(
                cls.symbol, cls.timeframe
            ).distinct().all()
            
            for symbol, timeframe in distinct_combinations:
                # Check if rolling window is enabled for this timeframe
                if not RollingWindowConfig.is_enabled(timeframe):
                    continue
                
                # Get the maximum candles for this timeframe and perform conservative cleanup
                max_candles = RollingWindowConfig.get_max_candles(timeframe)
                deleted = cls.cleanup_rolling_window(symbol, timeframe, max_candles, batch_size)
                
                if deleted > 0:
                    cleanup_results[f"{symbol}:{timeframe}"] = deleted
                    total_deleted += deleted
                    logging.info(f"Rolling window cleanup: {symbol}:{timeframe} - deleted {deleted} old candles")
            
            return {
                "total_deleted": total_deleted,
                "details": cleanup_results,
                "symbols_processed": len(distinct_combinations)
            }
            
        except Exception as e:
            logging.error(f"Error in cleanup_all_rolling_windows: {e}")
            return {"total_deleted": 0, "details": {}, "error": str(e)}

    @classmethod
    def get_rolling_window_stats(cls):
        """
        Get statistics about current rolling window status.
        
        Returns:
            dict: Statistics for each symbol/timeframe combination
        """
        from config import RollingWindowConfig
        
        stats = {}
        
        try:
            # Get all unique symbol/timeframe combinations with counts
            combinations = db.session.query(
                cls.symbol, 
                cls.timeframe,
                db.func.count().label('candle_count')
            ).filter(
                cls.is_complete == True
            ).group_by(cls.symbol, cls.timeframe).all()
            
            for symbol, timeframe, count in combinations:
                max_candles = RollingWindowConfig.get_max_candles(timeframe)
                is_enabled = RollingWindowConfig.is_enabled(timeframe)
                
                key = f"{symbol}:{timeframe}"
                stats[key] = {
                    "current_count": count,
                    "max_allowed": max_candles,
                    "over_limit": max(0, count - max_candles),
                    "rolling_window_enabled": is_enabled,
                    "needs_cleanup": is_enabled and count > max_candles
                }
            
            return stats
            
        except Exception as e:
            logging.error(f"Error getting rolling window stats: {e}")
            return {"error": str(e)}

    @classmethod
    def detect_gaps(cls, symbol: str, timeframe: str, days_back: int = 7) -> Dict:
        """
        Detect gaps in klines data for a specific symbol and timeframe.
        
        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            timeframe: Timeframe ('1h', '4h', '1d')
            days_back: Number of days to check back from now
            
        Returns:
            dict: Gap analysis results including detected gaps and statistics
        """
        try:
            # Calculate the expected interval in seconds
            interval_seconds = {
                "1h": 3600,      # 1 hour
                "4h": 14400,     # 4 hours  
                "1d": 86400,     # 1 day
            }.get(timeframe, 3600)
            
            # Get cutoff time for analysis
            cutoff_time = get_utc_now() - timedelta(days=days_back)
            
            # Get all completed candles for this symbol/timeframe, ordered by timestamp
            candles = db.session.query(cls).filter(
                cls.symbol == symbol,
                cls.timeframe == timeframe,
                cls.is_complete == True,
                cls.timestamp >= cutoff_time
            ).order_by(cls.timestamp.asc()).all()
            
            if len(candles) < 2:
                return {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "candle_count": len(candles),
                    "gaps": [],
                    "total_gaps": 0,
                    "largest_gap_hours": 0,
                    "data_coverage_percent": 0,
                    "analysis_period_days": days_back,
                    "warning": "Insufficient data for gap analysis"
                }
            
            # Detect gaps between consecutive candles
            gaps = []
            total_gap_duration = 0
            
            for i in range(1, len(candles)):
                prev_candle = candles[i-1]
                curr_candle = candles[i]
                
                # Calculate expected next timestamp
                expected_next = prev_candle.timestamp + timedelta(seconds=interval_seconds)
                
                # Check if there's a gap (allow 1 minute tolerance for timing)
                gap_duration = (curr_candle.timestamp - expected_next).total_seconds()
                
                if gap_duration > 60:  # More than 1 minute gap
                    gap_info = {
                        "start_time": expected_next.isoformat(),
                        "end_time": curr_candle.timestamp.isoformat(),
                        "duration_hours": gap_duration / 3600,
                        "missing_candles": int(gap_duration / interval_seconds),
                        "before_candle": prev_candle.timestamp.isoformat(),
                        "after_candle": curr_candle.timestamp.isoformat()
                    }
                    gaps.append(gap_info)
                    total_gap_duration += gap_duration
            
            # Calculate statistics
            analysis_duration = (candles[-1].timestamp - candles[0].timestamp).total_seconds()
            data_coverage_percent = ((analysis_duration - total_gap_duration) / analysis_duration * 100) if analysis_duration > 0 else 0
            largest_gap_hours = max([gap["duration_hours"] for gap in gaps]) if gaps else 0
            
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "candle_count": len(candles),
                "gaps": gaps,
                "total_gaps": len(gaps),
                "largest_gap_hours": largest_gap_hours,
                "total_gap_duration_hours": total_gap_duration / 3600,
                "data_coverage_percent": round(data_coverage_percent, 2),
                "analysis_period_days": days_back,
                "first_candle": candles[0].timestamp.isoformat() if candles else None,
                "last_candle": candles[-1].timestamp.isoformat() if candles else None,
                "status": "gaps_detected" if gaps else "no_gaps"
            }
            
        except Exception as e:
            logging.error(f"Error detecting gaps for {symbol}:{timeframe}: {e}")
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "error": str(e),
                "status": "error"
            }

    @classmethod
    def detect_all_gaps(cls, days_back: int = 7, symbols: "Optional[List[str]]" = None, timeframes: "Optional[List[str]]" = None) -> Dict:
        """
        Detect gaps across all symbols and timeframes.
        
        Args:
            days_back: Number of days to check back from now
            symbols: List of symbols to check (defaults to all symbols with data)
            timeframes: List of timeframes to check (defaults to all timeframes with data)
            
        Returns:
            dict: Comprehensive gap analysis for all symbol/timeframe combinations
        """
        try:
            from config import TradingConfig
            
            # Use provided symbols or get all symbols with data
            if symbols is None:
                symbols = TradingConfig.SUPPORTED_SYMBOLS
                
            # Use provided timeframes or get all timeframes with data
            if timeframes is None:
                timeframes = ["1h", "4h", "1d"]
            
            results = {}
            summary = {
                "total_combinations": 0,
                "combinations_with_gaps": 0,
                "total_gaps": 0,
                "worst_gap_hours": 0,
                "worst_gap_location": None,
                "analysis_timestamp": get_utc_now().isoformat()
            }
            
            for symbol in symbols:
                for timeframe in timeframes:
                    combo_key = f"{symbol}:{timeframe}"
                    gap_analysis = cls.detect_gaps(symbol, timeframe, days_back)
                    results[combo_key] = gap_analysis
                    
                    summary["total_combinations"] += 1
                    
                    if gap_analysis.get("total_gaps", 0) > 0:
                        summary["combinations_with_gaps"] += 1
                        summary["total_gaps"] += gap_analysis["total_gaps"]
                        
                        largest_gap = gap_analysis.get("largest_gap_hours", 0)
                        if largest_gap > summary["worst_gap_hours"]:
                            summary["worst_gap_hours"] = largest_gap
                            summary["worst_gap_location"] = combo_key
            
            return {
                "summary": summary,
                "detailed_results": results,
                "recommendations": cls._generate_gap_recommendations(summary, results)
            }
            
        except Exception as e:
            logging.error(f"Error in detect_all_gaps: {e}")
            return {"error": str(e), "status": "error"}

    @classmethod
    def _generate_gap_recommendations(cls, summary: Dict, results: Dict) -> "List[str]":
        """Generate recommendations based on gap analysis."""
        recommendations = []
        
        if summary["total_gaps"] == 0:
            recommendations.append("✅ No data gaps detected - klines data integrity is good")
        else:
            recommendations.append(f"⚠️ Found {summary['total_gaps']} gaps across {summary['combinations_with_gaps']} symbol/timeframe combinations")
            
            if summary["worst_gap_hours"] > 24:
                recommendations.append(f"🚨 Large gap detected: {summary['worst_gap_hours']:.1f} hours in {summary['worst_gap_location']}")
                recommendations.append("Consider running a full data refresh for affected symbols")
            
            # Find patterns in gaps
            gap_counts_1h = sum(1 for key, result in results.items() if ":1h" in key and result.get("total_gaps", 0) > 0)
            gap_counts_4h = sum(1 for key, result in results.items() if ":4h" in key and result.get("total_gaps", 0) > 0) 
            gap_counts_1d = sum(1 for key, result in results.items() if ":1d" in key and result.get("total_gaps", 0) > 0)
            
            if gap_counts_1h > gap_counts_4h and gap_counts_1h > gap_counts_1d:
                recommendations.append("📊 Most gaps are in 1h timeframe - check high-frequency data updates")
            elif gap_counts_4h > gap_counts_1d:
                recommendations.append("📊 Gaps detected in 4h timeframe - check medium-term data consistency")
                
        return recommendations

    def __repr__(self):
        return f"<KlinesCache {self.symbol}:{self.timeframe} @ {self.timestamp}>"
