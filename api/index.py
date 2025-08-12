"""
Vercel serverless function entry point
"""
import sys
import os

# Add the parent directory to the path so we can import our app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set Vercel environment variable
os.environ["VERCEL"] = "1"

from main import app

# Export the Flask app directly for Vercel
application = app