import os
from datetime import datetime, timedelta, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from cryptography.fernet import Fernet
import base64
import hashlib

# GMT+3:30 timezone (Iran Standard Time)
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

def get_iran_time():
    """Get current time in GMT+3:30 timezone"""
    return datetime.now(IRAN_TZ)

def utc_to_iran_time(utc_dt):
    """Convert UTC datetime to GMT+3:30 timezone"""
    if utc_dt is None:
        return None
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(IRAN_TZ)

def format_iran_time(dt, format_str='%Y-%m-%d %H:%M:%S'):
    """Format datetime in GMT+3:30 timezone"""
    if dt is None:
        return ""
    iran_time = utc_to_iran_time(dt) if dt.tzinfo is None or dt.tzinfo == timezone.utc else dt
    if iran_time is None:
        return ""
    return iran_time.strftime(format_str)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Encryption key for API credentials - generated from app secret
def get_encryption_key():
    """Generate encryption key from app secret for consistent encryption"""
    secret = os.environ.get("SESSION_SECRET", "dev-secret-key")
    key = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key)

def encrypt_data(data):
    """Encrypt sensitive data"""
    if not data:
        return ""
    fernet = Fernet(get_encryption_key())
    return fernet.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data):
    """Decrypt sensitive data"""
    if not encrypted_data:
        return ""
    try:
        fernet = Fernet(get_encryption_key())
        return fernet.decrypt(encrypted_data.encode()).decode()
    except:
        return ""

class UserCredentials(db.Model):
    """Store encrypted API credentials for each user"""
    __tablename__ = 'user_credentials'
    
    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    telegram_username = db.Column(db.String(100))
    exchange_name = db.Column(db.String(50), default="toobit")  # toobit, binance, etc
    
    # Encrypted API credentials
    api_key_encrypted = db.Column(db.Text)
    api_secret_encrypted = db.Column(db.Text)
    passphrase_encrypted = db.Column(db.Text)  # For some exchanges
    
    # API settings
    testnet_mode = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_used = db.Column(db.DateTime)
    
    def set_api_key(self, api_key):
        """Set encrypted API key"""
        self.api_key_encrypted = encrypt_data(api_key)
    
    def get_api_key(self):
        """Get decrypted API key"""
        return decrypt_data(self.api_key_encrypted)
    
    def set_api_secret(self, api_secret):
        """Set encrypted API secret"""
        self.api_secret_encrypted = encrypt_data(api_secret)
    
    def get_api_secret(self):
        """Get decrypted API secret"""
        return decrypt_data(self.api_secret_encrypted)
    
    def set_passphrase(self, passphrase):
        """Set encrypted passphrase"""
        self.passphrase_encrypted = encrypt_data(passphrase) if passphrase else ""
    
    def get_passphrase(self):
        """Get decrypted passphrase"""
        return decrypt_data(self.passphrase_encrypted)
    
    def has_credentials(self):
        """Check if user has valid API credentials"""
        return bool(self.api_key_encrypted and self.api_secret_encrypted)
    
    def __repr__(self):
        return f'<UserCredentials {self.telegram_user_id}:{self.exchange_name}>'

class UserTradingSession(db.Model):
    """Track user trading sessions and API usage"""
    __tablename__ = 'user_trading_sessions'
    
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
        return f'<UserTradingSession {self.telegram_user_id}:{self.session_start}>'

class TradeConfiguration(db.Model):
    """Persistent storage for trade configurations"""
    __tablename__ = 'trade_configurations'
    
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
    entry_type = db.Column(db.String(20), default='market')  # 'market' or 'limit'
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
    status = db.Column(db.String(20), default='configured')  # configured, pending, active, stopped
    position_margin = db.Column(db.Float, default=0.0)
    unrealized_pnl = db.Column(db.Float, default=0.0)
    current_price = db.Column(db.Float, default=0.0)
    position_size = db.Column(db.Float, default=0.0)
    position_value = db.Column(db.Float, default=0.0)
    realized_pnl = db.Column(db.Float, default=0.0)
    final_pnl = db.Column(db.Float, default=0.0)
    closed_at = db.Column(db.DateTime)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
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
        config.breakeven_after = self.breakeven_after
        config.breakeven_sl_triggered = getattr(self, 'breakeven_sl_triggered', False)
        config.trailing_stop_enabled = self.trailing_stop_enabled
        config.trail_percentage = self.trail_percentage
        config.trail_activation_price = self.trail_activation_price
        config.status = self.status
        config.position_margin = self.position_margin
        config.unrealized_pnl = self.unrealized_pnl
        config.current_price = self.current_price
        config.position_size = self.position_size
        config.position_value = self.position_value
        config.realized_pnl = getattr(self, 'realized_pnl', 0.0)
        config.final_pnl = self.final_pnl
        config.closed_at = format_iran_time(self.closed_at) if self.closed_at else ""
        
        # Parse take profits JSON
        if self.take_profits:
            try:
                import json
                config.take_profits = json.loads(self.take_profits)
            except:
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
        # Convert breakeven_after - handle "disabled" string case
        if hasattr(config, 'breakeven_after'):
            if config.breakeven_after == "disabled" or config.breakeven_after == 0:
                db_config.breakeven_after = 0.0
            else:
                try:
                    db_config.breakeven_after = float(config.breakeven_after)
                except (ValueError, TypeError):
                    db_config.breakeven_after = 0.0
        else:
            db_config.breakeven_after = 0.0
        db_config.breakeven_sl_triggered = getattr(config, 'breakeven_sl_triggered', False)
        db_config.trailing_stop_enabled = config.trailing_stop_enabled
        db_config.trail_percentage = config.trail_percentage
        db_config.trail_activation_price = config.trail_activation_price
        db_config.status = config.status
        db_config.position_margin = config.position_margin
        db_config.unrealized_pnl = config.unrealized_pnl
        db_config.current_price = config.current_price
        db_config.position_size = config.position_size
        db_config.position_value = config.position_value
        db_config.realized_pnl = getattr(config, 'realized_pnl', 0.0)
        db_config.final_pnl = config.final_pnl
        
        if config.closed_at and config.closed_at != "":
            try:
                from datetime import datetime
                db_config.closed_at = datetime.fromisoformat(config.closed_at.replace('Z', '+00:00'))
            except:
                pass
        
        return db_config
    
    def __repr__(self):
        return f'<TradeConfiguration {self.telegram_user_id}:{self.trade_id}>'