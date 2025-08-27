#!/usr/bin/env python3
"""
Render.com Performance Optimization Script
Optimizes the trading bot specifically for Render's infrastructure
"""
import os
import logging
from api.app import app
from config import Environment, DatabaseConfig

# Force Render environment detection
os.environ['RENDER'] = '1'
os.environ['FLASK_ENV'] = 'production'

# Set up optimized logging for Render
logging.basicConfig(
    level=logging.WARNING,  # Reduced logging for better performance
    format='%(levelname)s: %(message)s'  # Simplified format
)

# Optimize Flask app for Render
app.config.update({
    'JSON_SORT_KEYS': False,  # Disable JSON sorting for speed
    'JSONIFY_PRETTYPRINT_REGULAR': False,  # Disable pretty printing
    'PRESERVE_CONTEXT_ON_EXCEPTION': False,  # Disable context preservation
    'EXPLAIN_TEMPLATE_LOADING': False,  # Disable template loading explanation
})

# Database optimizations for Render
if app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("postgresql"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"].update({
        "pool_size": DatabaseConfig.RENDER_POOL_SIZE,
        "max_overflow": DatabaseConfig.RENDER_MAX_OVERFLOW,
        "pool_timeout": DatabaseConfig.RENDER_POOL_TIMEOUT,
        "pool_recycle": DatabaseConfig.RENDER_POOL_RECYCLE,
        "echo": False,  # Disable SQL echo for performance
        "pool_reset_on_return": "commit",
        "connect_args": {
            "sslmode": "require",
            "connect_timeout": 10,
            "application_name": "trading-bot-render",
            "tcp_keepalives_idle": "30",
            "tcp_keepalives_interval": "5",
            "tcp_keepalives_count": "3"
        }
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    logging.warning(f"Trading Bot optimized for Render - Port: {port}")
    logging.warning(f"Environment: IS_RENDER={Environment.IS_RENDER}, IS_PRODUCTION={Environment.IS_PRODUCTION}")
    
    # Run with optimized settings for Render
    app.run(
        host="0.0.0.0", 
        port=port, 
        debug=False,
        use_reloader=False,
        threaded=True,
        processes=1  # Single process for memory efficiency
    )