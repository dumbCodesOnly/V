#!/usr/bin/env python3
"""
Simple entry point for Telegram Trading Bot workflow
This script allows the workflow to run api/app.py directly
"""

if __name__ == "__main__":
    # Import and run the Flask app from api directory
    import sys
    import os
    
    # Add the current directory to path for imports
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    # Import the Flask app
    from api.app import app
    
    # Run the Flask app
    app.run(host="0.0.0.0", port=5000, debug=True)