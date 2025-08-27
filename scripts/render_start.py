#!/usr/bin/env python3
"""
Render.com startup script for the Trading Bot
This script ensures the app starts correctly with proper configuration for Render's environment
"""
import os
import logging
from api.app import app
from config import Environment

# Set up production logging for Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Ensure environment is detected correctly
os.environ['RENDER'] = '1'

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))  # Render default port
    
    logging.info(f"Starting Trading Bot on Render - Port: {port}")
    logging.info(f"Environment detection - IS_RENDER: {Environment.IS_RENDER}")
    logging.info(f"Environment detection - IS_PRODUCTION: {Environment.IS_PRODUCTION}")
    
    # Production mode for Render
    app.run(
        host="0.0.0.0", 
        port=port, 
        debug=False,
        use_reloader=False,
        threaded=True
    )