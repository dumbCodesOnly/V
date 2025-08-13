"""
Vercel serverless function entry point - Import complete app
"""
import os

# Set Vercel environment variable
os.environ["VERCEL"] = "1"

# Import the complete application from app.py
from .app import app