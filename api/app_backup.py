import os
import logging
from flask import Flask, request, jsonify, render_template
from datetime import datetime, timedelta
import urllib.request
import urllib.parse
import json
import random
import time
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, UserCredentials, UserTradingSession

# Configure logging - reduce verbosity for serverless
log_level = logging.INFO if os.environ.get("VERCEL") else logging.DEBUG
logging.basicConfig(level=log_level)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configure database - optimized for serverless deployment
database_url = os.environ.get("DATABASE_URL", "sqlite:///trading_bot.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
    "pool_size": 5,
    "max_overflow": 10
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize database
db.init_app(app)

# Create tables only if not in serverless environment or if explicitly needed
def init_database():
    """Initialize database tables safely"""
    try:
        with app.app_context():
            db.create_all()
            logging.info("Database tables created successfully")
    except Exception as e:
        logging.error(f"Database initialization error: {e}")

# Initialize database conditionally
if not os.environ.get("VERCEL"):
    init_database()
else:
    # For Vercel, initialize on first request using newer Flask syntax
    initialized = False
    
    @app.before_request
    def create_tables():
        global initialized
        if not initialized:
            init_database()
            initialized = True

# Bot token and webhook URL from environment
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# Simple in-memory storage for the bot (replace with database in production)
bot_messages = []
bot_trades = []
bot_status = {
    'status': 'active',
    'total_messages': 5,
    'total_trades': 2,
    'error_count': 0,
    'last_heartbeat': datetime.utcnow().isoformat()
}

# User state tracking for API setup
user_api_setup_state = {}  # {user_id: {'step': 'api_key|api_secret|passphrase', 'exchange': 'toobit'}}

# Multi-trade management storage
user_trade_configs = {}  # {user_id: {trade_id: TradeConfig}}
user_selected_trade = {}  # {user_id: trade_id}
trade_counter = 0

# Initialize clean user environment
def initialize_user_environment(user_id):
    """Initialize a clean trading environment for a new user"""
    user_id = int(user_id)
    
    # Create empty trade configurations for user
    if user_id not in user_trade_configs:
        user_trade_configs[user_id] = {}
    
    # Initialize user's selected trade if not exists
    if user_id not in user_selected_trade:
        user_selected_trade[user_id] = None

# Incomplete version - needs full functionality from main app.py