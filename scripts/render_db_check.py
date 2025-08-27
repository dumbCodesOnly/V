#!/usr/bin/env python3
"""
Render Database Diagnostic Tool
Check database connection and configuration on Render
"""
import os
import logging
from api.app import app, db
from config import get_database_url, Environment

def check_render_database():
    """Check database configuration on Render"""
    with app.app_context():
        print(f"Environment Detection:")
        print(f"  IS_RENDER: {Environment.IS_RENDER}")
        print(f"  IS_VERCEL: {Environment.IS_VERCEL}")
        print(f"  IS_REPLIT: {Environment.IS_REPLIT}")
        print()
        
        # Check environment variables
        print("Environment Variables:")
        database_url = os.environ.get("DATABASE_URL")
        print(f"  DATABASE_URL: {'SET' if database_url else 'NOT SET'}")
        if database_url:
            # Don't print the full URL for security, just the prefix
            print(f"  URL Type: {database_url.split('://')[0] if '://' in database_url else 'unknown'}")
        print()
        
        # Check configured database
        configured_url = get_database_url()
        print(f"Configured Database:")
        print(f"  URL: {'SET' if configured_url else 'NOT SET'}")
        print(f"  Type: {configured_url.split('://')[0] if configured_url and '://' in configured_url else 'sqlite (fallback)'}")
        print(f"  SQLAlchemy URI: {app.config.get('SQLALCHEMY_DATABASE_URI', 'Not set')[:20]}...")
        print()
        
        # Test database connection
        try:
            db.create_all()
            print("✅ Database connection successful")
            
            # Check if tables exist
            from api.models import TradeConfiguration
            count = TradeConfiguration.query.count()
            print(f"✅ TradeConfiguration table exists with {count} records")
            
        except Exception as e:
            print(f"❌ Database connection failed: {e}")

if __name__ == "__main__":
    check_render_database()