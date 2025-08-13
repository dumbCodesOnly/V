#!/usr/bin/env python3
"""
Secure Webhook Setup Script for Telegram Bot

This script helps you set up a secure webhook with a secret token
for your Telegram bot deployment on Vercel.
"""

import os
import secrets
import requests
import sys

def generate_secret_token():
    """Generate a secure random token for webhook authentication"""
    return secrets.token_hex(32)

def set_webhook_with_token(bot_token, webhook_url, secret_token):
    """Set the Telegram webhook with secret token"""
    url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    
    data = {
        'url': webhook_url,
        'secret_token': secret_token
    }
    
    try:
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        
        if result.get('ok'):
            print(f"✅ Webhook set successfully!")
            print(f"   URL: {webhook_url}")
            print(f"   Secret token: {secret_token[:8]}...{secret_token[-8:]}")
            return True
        else:
            print(f"❌ Failed to set webhook: {result.get('description')}")
            return False
            
    except Exception as e:
        print(f"❌ Error setting webhook: {e}")
        return False

def main():
    print("🔐 Secure Telegram Webhook Setup")
    print("=" * 40)
    
    # Get bot token
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        bot_token = input("Enter your Telegram bot token: ").strip()
        if not bot_token:
            print("❌ Bot token is required!")
            sys.exit(1)
    
    # Get webhook URL
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        default_url = input("Enter your webhook URL (e.g., https://your-app.vercel.app/webhook): ").strip()
        if not default_url:
            print("❌ Webhook URL is required!")
            sys.exit(1)
        webhook_url = default_url
    
    # Generate secret token
    secret_token = generate_secret_token()
    
    print(f"\n📋 Configuration:")
    print(f"   Bot Token: {bot_token[:8]}...{bot_token[-8:]}")
    print(f"   Webhook URL: {webhook_url}")
    print(f"   Secret Token: {secret_token}")
    
    # Set the webhook
    print(f"\n🔧 Setting webhook...")
    success = set_webhook_with_token(bot_token, webhook_url, secret_token)
    
    if success:
        print(f"\n🎉 Setup complete! Next steps:")
        print(f"1. Add this environment variable to Vercel:")
        print(f"   WEBHOOK_SECRET_TOKEN={secret_token}")
        print(f"2. Re-enable deployment protection in Vercel")
        print(f"3. Your bot will now securely verify all webhook requests")
        print(f"\n⚠️  Keep the secret token safe - it's like a password!")
    else:
        print(f"\n❌ Setup failed. Please check your bot token and try again.")

if __name__ == "__main__":
    main()