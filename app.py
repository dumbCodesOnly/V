import os
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
from bot import TelegramBot
import secrets
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(16))

# Store start time for uptime calculation
app_start_time = datetime.now()

# Initialize Telegram bot
telegram_bot = TelegramBot()

# HTML template for dashboard
DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Toobit Multi-Trade Telegram Bot</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        .dashboard-container {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 20px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
        }
        .stat-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 15px;
            padding: 1.5rem;
            margin-bottom: 1rem;
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3);
            transition: transform 0.3s ease;
        }
        .stat-card:hover {
            transform: translateY(-5px);
        }
        .feature-card {
            background: white;
            border-radius: 15px;
            padding: 1.5rem;
            margin-bottom: 1rem;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.08);
            border: none;
            transition: transform 0.3s ease;
        }
        .feature-card:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            padding: 0.5rem 1rem;
            border-radius: 50px;
            font-weight: 500;
            font-size: 0.875rem;
        }
        .status-online {
            background: #d1ecf1;
            color: #0c5460;
        }
        .bot-title {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            font-weight: 700;
            font-size: 2.5rem;
        }
        .refresh-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border: none;
            color: white;
            font-size: 1.5rem;
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3);
            transition: transform 0.3s ease;
        }
        .refresh-btn:hover {
            transform: scale(1.1);
        }
        .trade-list {
            max-height: 400px;
            overflow-y: auto;
        }
        .trade-item {
            padding: 10px;
            margin: 5px 0;
            border-radius: 8px;
            background: #f8f9fa;
            border-left: 4px solid #667eea;
        }
        .trade-item.active {
            border-left-color: #28a745;
            background: #d4edda;
        }
        .trade-item.paused {
            border-left-color: #ffc107;
            background: #fff3cd;
        }
    </style>
</head>
<body>
    <div class="container py-5">
        <div class="dashboard-container p-4">
            <!-- Header -->
            <div class="text-center mb-5">
                <h1 class="bot-title">
                    <i class="fas fa-robot me-3"></i>
                    Toobit Multi-Trade Bot
                </h1>
                <p class="lead text-muted">Advanced Telegram Trading Bot Dashboard</p>
                <div class="status-badge status-online">
                    <i class="fas fa-circle me-2"></i>
                    Bot Online & Ready
                </div>
            </div>

            <!-- Stats Row -->
            <div class="row mb-4">
                <div class="col-md-3">
                    <div class="stat-card text-center">
                        <i class="fas fa-users fa-2x mb-3"></i>
                        <h4 class="mb-1">{{ total_users }}</h4>
                        <p class="mb-0">Active Users</p>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="stat-card text-center">
                        <i class="fas fa-exchange-alt fa-2x mb-3"></i>
                        <h4 class="mb-1">{{ total_trades }}</h4>
                        <p class="mb-0">Total Trades</p>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="stat-card text-center">
                        <i class="fas fa-play-circle fa-2x mb-3"></i>
                        <h4 class="mb-1">{{ active_trades }}</h4>
                        <p class="mb-0">Active Trades</p>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="stat-card text-center">
                        <i class="fas fa-robot fa-2x mb-3"></i>
                        <h4 class="mb-1">{{ running_bots }}</h4>
                        <p class="mb-0">Running Bots</p>
                    </div>
                </div>
            </div>

            <!-- Recent Trades -->
            {% if recent_trades %}
            <div class="row mb-4">
                <div class="col-12">
                    <div class="feature-card">
                        <h5 class="mb-3">
                            <i class="fas fa-clock text-primary me-2"></i>
                            Recent Trades
                        </h5>
                        <div class="trade-list">
                            {% for trade in recent_trades %}
                            <div class="trade-item {{ trade.status }}">
                                <div class="d-flex justify-content-between align-items-center">
                                    <div>
                                        <strong>{{ trade.display_name }}</strong>
                                        <br>
                                        <small class="text-muted">{{ trade.symbol }} | {{ trade.side }} | {{ trade.status }}</small>
                                    </div>
                                    <div class="text-end">
                                        <div class="badge bg-{{ 'success' if trade.status == 'active' else 'warning' if trade.status == 'configured' else 'secondary' }}">
                                            {{ trade.status.title() }}
                                        </div>
                                        <br>
                                        <small class="text-muted">{{ trade.created_at }}</small>
                                    </div>
                                </div>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                </div>
            </div>
            {% endif %}

            <!-- Features Grid -->
            <div class="row">
                <div class="col-md-6 col-lg-4">
                    <div class="feature-card">
                        <div class="d-flex align-items-center mb-3">
                            <i class="fas fa-layer-group fa-2x text-primary me-3"></i>
                            <h5 class="mb-0">Multi-Trade Management</h5>
                        </div>
                        <p class="text-muted mb-0">Create and manage multiple trading configurations simultaneously with independent monitoring.</p>
                    </div>
                </div>
                <div class="col-md-6 col-lg-4">
                    <div class="feature-card">
                        <div class="d-flex align-items-center mb-3">
                            <i class="fas fa-bullseye fa-2x text-success me-3"></i>
                            <h5 class="mb-0">Advanced Take Profits</h5>
                        </div>
                        <p class="text-muted mb-0">Set up to 3 take profit levels with custom position sizing and automated execution.</p>
                    </div>
                </div>
                <div class="col-md-6 col-lg-4">
                    <div class="feature-card">
                        <div class="d-flex align-items-center mb-3">
                            <i class="fas fa-shield-alt fa-2x text-warning me-3"></i>
                            <h5 class="mb-0">Smart Risk Management</h5>
                        </div>
                        <p class="text-muted mb-0">Trailing stops, break-even automation, and intelligent stop loss management.</p>
                    </div>
                </div>
                <div class="col-md-6 col-lg-4">
                    <div class="feature-card">
                        <div class="d-flex align-items-center mb-3">
                            <i class="fas fa-chart-area fa-2x text-info me-3"></i>
                            <h5 class="mb-0">Portfolio Tracking</h5>
                        </div>
                        <p class="text-muted mb-0">Comprehensive P&L tracking, performance analytics, and trade history.</p>
                    </div>
                </div>
                <div class="col-md-6 col-lg-4">
                    <div class="feature-card">
                        <div class="d-flex align-items-center mb-3">
                            <i class="fas fa-bell fa-2x text-danger me-3"></i>
                            <h5 class="mb-0">Real-time Notifications</h5>
                        </div>
                        <p class="text-muted mb-0">Instant Telegram notifications for trade executions, profit targets, and alerts.</p>
                    </div>
                </div>
                <div class="col-md-6 col-lg-4">
                    <div class="feature-card">
                        <div class="d-flex align-items-center mb-3">
                            <i class="fas fa-flask fa-2x text-secondary me-3"></i>
                            <h5 class="mb-0">Testing Features</h5>
                        </div>
                        <p class="text-muted mb-0">Dry run mode and testnet support for safe strategy testing.</p>
                    </div>
                </div>
            </div>

            <!-- System Info -->
            <div class="row mt-4">
                <div class="col-12">
                    <div class="feature-card">
                        <h5 class="mb-3">
                            <i class="fas fa-info-circle text-primary me-2"></i>
                            System Information
                        </h5>
                        <div class="row">
                            <div class="col-md-6">
                                <p><strong>Bot Status:</strong> <span class="text-success">Online</span></p>
                                <p><strong>Version:</strong> 2.0.0</p>
                                <p><strong>Exchange:</strong> Toobit USDT-M Futures</p>
                            </div>
                            <div class="col-md-6">
                                <p><strong>Uptime:</strong> {{ uptime }}</p>
                                <p><strong>Last Updated:</strong> {{ last_update }}</p>
                                <p><strong>Environment:</strong> {{ environment }}</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Getting Started -->
            <div class="row mt-4">
                <div class="col-12">
                    <div class="feature-card text-center">
                        <h5 class="mb-3">
                            <i class="fas fa-rocket text-primary me-2"></i>
                            Getting Started
                        </h5>
                        <p class="text-muted mb-3">Start your trading journey with our advanced multi-trade bot</p>
                        <div class="d-flex justify-content-center gap-3 flex-wrap">
                            <a href="https://t.me/{{ bot_username }}" class="btn btn-primary" target="_blank">
                                <i class="fab fa-telegram me-2"></i>Open in Telegram
                            </a>
                            <button class="btn btn-outline-primary" onclick="location.reload()">
                                <i class="fas fa-sync-alt me-2"></i>Refresh Dashboard
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Floating Refresh Button -->
    <button class="refresh-btn" onclick="location.reload()" title="Refresh Dashboard">
        <i class="fas fa-sync-alt"></i>
    </button>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Auto-refresh every 30 seconds
        setTimeout(() => {
            location.reload();
        }, 30000);
    </script>
</body>
</html>
"""


@app.route('/')
def dashboard():
    """Main dashboard"""
    try:
        # Calculate statistics
        total_users = len(telegram_bot.multi_trade_manager.user_trades)
        total_trades = sum(len(trades) for trades in telegram_bot.multi_trade_manager.user_trades.values())
        active_trades = sum(len([t for t in trades.values() if t.status == 'active']) 
                          for trades in telegram_bot.multi_trade_manager.user_trades.values())
        running_bots = len(telegram_bot.multi_trade_manager.active_bots)
        
        # Get recent trades for display
        recent_trades = []
        for user_trades in telegram_bot.multi_trade_manager.user_trades.values():
            for trade in user_trades.values():
                recent_trades.append({
                    'display_name': trade.get_display_name(),
                    'symbol': trade.symbol or 'N/A',
                    'side': trade.side or 'N/A',
                    'status': trade.status,
                    'created_at': trade.created_at.strftime('%m/%d %H:%M')
                })
        
        # Sort by creation time and limit to 10 most recent
        recent_trades.sort(key=lambda x: x['created_at'], reverse=True)
        recent_trades = recent_trades[:10]
        
        # Calculate uptime
        uptime_delta = datetime.now() - app_start_time
        hours = int(uptime_delta.total_seconds() // 3600)
        minutes = int((uptime_delta.total_seconds() % 3600) // 60)
        uptime = f"{hours}h {minutes}m"
        
        # Get bot username (would normally be retrieved from Telegram API)
        bot_username = os.getenv('BOT_USERNAME', 'toobit_trading_bot')
        
        # Get current time
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Render dashboard
        return render_template_string(
            DASHBOARD_TEMPLATE,
            total_users=total_users,
            total_trades=total_trades,
            active_trades=active_trades,
            running_bots=running_bots,
            recent_trades=recent_trades,
            uptime=uptime,
            last_update=current_time,
            environment="Production" if not os.getenv('DEBUG') else "Development",
            bot_username=bot_username
        )
        
    except Exception as e:
        logger.error(f"Error rendering dashboard: {e}")
        return jsonify({'error': 'Dashboard error', 'message': str(e)}), 500


@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook"""
    try:
        update = request.get_json()
        if not update:
            return jsonify({'status': 'error', 'message': 'No data received'}), 400
        
        # Process the update
        telegram_bot.process_update(update)
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'uptime': str(datetime.now() - app_start_time),
        'users': len(telegram_bot.multi_trade_manager.user_trades),
        'active_bots': len(telegram_bot.multi_trade_manager.active_bots)
    })


@app.route('/stats')
def stats():
    """Get bot statistics as JSON"""
    try:
        total_users = len(telegram_bot.multi_trade_manager.user_trades)
        total_trades = sum(len(trades) for trades in telegram_bot.multi_trade_manager.user_trades.values())
        active_trades = sum(len([t for t in trades.values() if t.status == 'active']) 
                          for trades in telegram_bot.multi_trade_manager.user_trades.values())
        running_bots = len(telegram_bot.multi_trade_manager.active_bots)
        
        return jsonify({
            'total_users': total_users,
            'total_trades': total_trades,
            'active_trades': active_trades,
            'running_bots': running_bots,
            'uptime': str(datetime.now() - app_start_time),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '0.0.0.0')
    debug = os.getenv('DEBUG', 'false').lower() == 'true'
    
    logger.info(f"Starting Toobit Multi-Trade Bot on {host}:{port}")
    logger.info(f"Debug mode: {debug}")
    
    app.run(host=host, port=port, debug=debug)
