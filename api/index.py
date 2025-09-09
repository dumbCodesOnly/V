"""
Vercel serverless function entry point - Main deployment
This imports the full Flask application with all trading bot functionality
"""

# Import the complete Flask application
from .app import app
