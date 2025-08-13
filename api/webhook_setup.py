#!/usr/bin/env python3
"""
Vercel Webhook Setup Script
This script should be called during Vercel deployment to set up the Telegram webhook
"""

import os
import sys
import json
import urllib.request
import urllib.parse

def main():
    """Set up webhook for Vercel deployment"""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    vercel_url = os.environ.get("VERCEL_URL")
    
    if not bot_token:
        print("Error: TELEGRAM_BOT_TOKEN not found")
        sys.exit(1)
    
    if not vercel_url:
        print("Error: VERCEL_URL not found")
        sys.exit(1)
    
    webhook_url = f"https://{vercel_url}/webhook"
    
    try:
        # Set the webhook
        api_url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
        data = urllib.parse.urlencode({'url': webhook_url}).encode('utf-8')
        
        req = urllib.request.Request(api_url, data=data, method='POST')
        response = urllib.request.urlopen(req, timeout=30)
        
        if response.getcode() == 200:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('ok'):
                print(f"✅ Webhook set successfully to {webhook_url}")
                sys.exit(0)
            else:
                print(f"❌ Telegram API error: {result.get('description')}")
                sys.exit(1)
        else:
            print(f"❌ HTTP error {response.getcode()}")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Error setting webhook: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()