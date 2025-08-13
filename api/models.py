import os
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from cryptography.fernet import Fernet
import base64
import hashlib

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