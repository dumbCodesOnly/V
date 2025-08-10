from app import db
from datetime import datetime

class BotMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(64), nullable=False)
    username = db.Column(db.String(64))
    message = db.Column(db.Text, nullable=False)
    response = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    command_type = db.Column(db.String(32))

class Trade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(64), nullable=False)
    symbol = db.Column(db.String(16), nullable=False)
    action = db.Column(db.String(8), nullable=False)  # 'buy' or 'sell'
    quantity = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float)
    status = db.Column(db.String(16), default='pending')  # 'pending', 'executed', 'failed'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    error_message = db.Column(db.Text)

class BotStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(16), default='active')  # 'active', 'inactive', 'error'
    last_heartbeat = db.Column(db.DateTime, default=datetime.utcnow)
    total_messages = db.Column(db.Integer, default=0)
    total_trades = db.Column(db.Integer, default=0)
    error_count = db.Column(db.Integer, default=0)
