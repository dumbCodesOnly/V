#!/usr/bin/env python3
"""
Clean webhook setup script for Vercel deployment
Run this to manually configure the Telegram webhook
"""

import os
import urllib.request
import urllib.parse
import json
import sys

def setup_telegram_webhook():
    """Set up Telegram webhook for Vercel deployment"""
    
    # Get bot token from environment
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("❌ Error: TELEGRAM_BOT_TOKEN environment variable not set")
        print("Please set your bot token in Vercel environment variables")
        return False
    
    # Webhook URL for your Vercel deployment
    webhook_url = "https://v0-033-pi.vercel.app/webhook"
    
    try:
        print(f"🔧 Setting up webhook for: {webhook_url}")
        
        # Set the webhook
        api_url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
        data = urllib.parse.urlencode({'url': webhook_url}).encode('utf-8')
        
        req = urllib.request.Request(api_url, data=data, method='POST')
        response = urllib.request.urlopen(req, timeout=10)
        
        if response.getcode() == 200:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('ok'):
                print("✅ Webhook set successfully!")
                print(f"📍 Webhook URL: {webhook_url}")
                
                # Get webhook info to verify
                info_url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
                info_req = urllib.request.Request(info_url)
                info_response = urllib.request.urlopen(info_req, timeout=10)
                
                if info_response.getcode() == 200:
                    info_result = json.loads(info_response.read().decode('utf-8'))
                    if info_result.get('ok'):
                        webhook_info = info_result['result']
                        print(f"🔗 Current webhook: {webhook_info.get('url', 'None')}")
                        print(f"📈 Pending updates: {webhook_info.get('pending_update_count', 0)}")
                        if webhook_info.get('last_error_message'):
                            print(f"⚠️ Last error: {webhook_info['last_error_message']}")
                
                return True
            else:
                print(f"❌ Webhook setup failed: {result.get('description', 'Unknown error')}")
                return False
        else:
            print(f"❌ HTTP request failed with status {response.getcode()}")
            return False
            
    except Exception as e:
        print(f"❌ Error setting up webhook: {e}")
        return False

def delete_webhook():
    """Delete the current webhook"""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("❌ Error: TELEGRAM_BOT_TOKEN environment variable not set")
        return False
    
    try:
        print("🗑️ Deleting current webhook...")
        
        api_url = f"https://api.telegram.org/bot{bot_token}/deleteWebhook"
        req = urllib.request.Request(api_url, method='POST')
        response = urllib.request.urlopen(req, timeout=10)
        
        if response.getcode() == 200:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('ok'):
                print("✅ Webhook deleted successfully!")
                return True
            else:
                print(f"❌ Webhook deletion failed: {result.get('description', 'Unknown error')}")
                return False
        else:
            print(f"❌ HTTP request failed with status {response.getcode()}")
            return False
            
    except Exception as e:
        print(f"❌ Error deleting webhook: {e}")
        return False

if __name__ == "__main__":
    print("🤖 Telegram Webhook Setup for Vercel")
    print("=" * 40)
    
    if len(sys.argv) > 1 and sys.argv[1] == "delete":
        delete_webhook()
    else:
        setup_telegram_webhook()
        
        print("\n📋 Next Steps:")
        print("1. Test your bot by sending /start to your bot in Telegram")
        print("2. Check Vercel function logs for webhook activity")
        print("3. Use 'python setup_webhook_vercel.py delete' to remove webhook if needed")