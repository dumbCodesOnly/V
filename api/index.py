"""
Vercel serverless function entry point - Import complete app without VERCEL flag
"""
# Import the complete application from app.py (without setting VERCEL environment)
from .app import app