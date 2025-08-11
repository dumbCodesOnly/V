#!/usr/bin/env python3
"""
Telegram Mini-App Setup Automation
Automatically generates the correct Mini-App URL for current Replit deployment
"""

import os
import sys
import urllib.request
import urllib.parse
import json
import logging

def get_current_domain():
    """Get the current Replit domain"""
    # Primary method: environment variable
    domains = os.environ.get('REPLIT_DOMAINS')
    if domains:
        # Take the first domain if multiple
        return domains.split(',')[0].strip()
    
    # Fallback: construct from REPL_ID
    repl_id = os.environ.get('REPL_ID')
    if repl_id:
        # This is a simplified construction - actual domains may vary
        return f"{repl_id}.repl.co"
    
    return None

def get_miniapp_url():
    """Get the complete Mini-App URL"""
    domain = get_current_domain()
    if not domain:
        return None
    
    # Ensure HTTPS
    if not domain.startswith('http'):
        domain = f"https://{domain}"
    
    # Add trailing slash if not present
    if not domain.endswith('/'):
        domain += '/'
    
    return domain

def setup_telegram_webhook(bot_token, webhook_url):
    """Automatically set up Telegram webhook"""
    if not bot_token or not webhook_url:
        return False, "Missing bot token or webhook URL"
    
    try:
        webhook_endpoint = f"{webhook_url}webhook"
        data = urllib.parse.urlencode({"url": webhook_endpoint}).encode('utf-8')
        
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/setWebhook",
            data=data,
            method='POST'
        )
        
        response = urllib.request.urlopen(req, timeout=10)
        result = json.loads(response.read().decode())
        
        if response.getcode() == 200 and result.get('ok'):
            return True, f"Webhook set successfully to {webhook_endpoint}"
        else:
            return False, f"Failed to set webhook: {result.get('description', 'Unknown error')}"
            
    except Exception as e:
        return False, f"Error setting webhook: {str(e)}"

def print_setup_instructions(miniapp_url):
    """Print setup instructions for Telegram Mini-App"""
    print("\n" + "="*60)
    print("🤖 TELEGRAM MINI-APP SETUP INSTRUCTIONS")
    print("="*60)
    print(f"\n📱 Mini-App URL: {miniapp_url}")
    print("\n📋 Setup Steps:")
    print("1. Open Telegram and go to @BotFather")
    print("2. Send command: /myapps")
    print("3. Select your trading bot")
    print("4. Choose 'Edit Mini App' or 'Create Mini App'")
    print(f"5. Paste this URL: {miniapp_url}")
    print("6. Set Short Name (e.g., 'ToobitTrader')")
    print("7. Upload icon (512x512 PNG recommended)")
    print("8. Add description about your trading bot")
    print("\n✅ Your Mini-App will be accessible via bot menu!")
    print("="*60)

def print_webhook_setup():
    """Print webhook setup information"""
    miniapp_url = get_miniapp_url()
    if not miniapp_url:
        print("❌ Cannot determine current domain")
        return
    
    webhook_url = f"{miniapp_url}webhook"
    
    print("\n" + "="*60)
    print("🔗 TELEGRAM WEBHOOK SETUP")
    print("="*60)
    print(f"\n📡 Webhook URL: {webhook_url}")
    print("\n🔑 Environment Variables Needed:")
    print("   TELEGRAM_BOT_TOKEN=your_bot_token_here")
    print("   WEBHOOK_URL=your_domain_here")
    print("\n📋 Manual Setup (if auto-setup fails):")
    print("   curl -X POST \\")
    print(f'     "https://api.telegram.org/bot{{YOUR_BOT_TOKEN}}/setWebhook" \\')
    print(f'     -d "url={webhook_url}"')
    print("="*60)

def auto_setup():
    """Perform automatic setup if credentials are available"""
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    miniapp_url = get_miniapp_url()
    
    if not miniapp_url:
        print("❌ Cannot determine current Replit domain")
        return False
    
    print(f"🌐 Detected domain: {get_current_domain()}")
    print(f"📱 Mini-App URL: {miniapp_url}")
    
    # Set up webhook if bot token is available
    if bot_token:
        print("\n🔄 Setting up webhook automatically...")
        success, message = setup_telegram_webhook(bot_token, miniapp_url)
        print(f"{'✅' if success else '❌'} {message}")
        
        if success:
            print("🎉 Webhook setup complete!")
        else:
            print_webhook_setup()
    else:
        print("⚠️  TELEGRAM_BOT_TOKEN not found - webhook setup skipped")
        print_webhook_setup()
    
    # Always show Mini-App setup instructions
    print_setup_instructions(miniapp_url)
    
    return True

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--webhook-only":
        print_webhook_setup()
    elif len(sys.argv) > 1 and sys.argv[1] == "--url-only":
        url = get_miniapp_url()
        if url:
            print(url)
        else:
            print("Cannot determine URL", file=sys.stderr)
            sys.exit(1)
    else:
        auto_setup()