#!/usr/bin/env python3
"""
Quick Setup for Telegram Mini-App
Run this script whenever you deploy to get the current Mini-App URL
"""

import os

def main():
    print("🚀 Toobit Trading Bot - Quick Setup")
    print("=" * 50)
    
    # Get current domain
    domain = os.environ.get('REPLIT_DOMAINS', '').split(',')[0].strip()
    
    if not domain:
        print("❌ Cannot detect Replit domain")
        return
    
    # Construct Mini-App URL
    miniapp_url = f"https://{domain}/"
    
    print(f"📱 Your Mini-App URL: {miniapp_url}")
    print("\n📋 Quick Setup:")
    print("1. Copy the URL above")
    print("2. Go to @BotFather in Telegram")
    print("3. Send: /myapps")
    print("4. Select your bot")
    print("5. Choose 'Edit Mini App'")
    print("6. Paste the URL")
    print("\n✅ Done! Your trading bot Mini-App is ready!")

if __name__ == "__main__":
    main()