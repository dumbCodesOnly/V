#!/usr/bin/env python3
"""
Automatic Webhook Setup for Vercel Deployment
This script automatically detects the deployment URL and sets up the Telegram webhook
"""

import os
import requests
import logging
import time

def get_deployment_url():
    """
    Detect the current deployment URL based on environment variables
    """
    # Check for Vercel deployment
    if os.environ.get("VERCEL"):
        vercel_url = os.environ.get("VERCEL_URL")
        if vercel_url:
            # VERCEL_URL doesn't include protocol
            return f"https://{vercel_url}"
    
    # Check for Replit deployment
    replit_domain = os.environ.get("REPLIT_DOMAIN")
    if replit_domain:
        return f"https://{replit_domain}"
    
    # Check for custom domain
    custom_domain = os.environ.get("WEBHOOK_DOMAIN")
    if custom_domain:
        return f"https://{custom_domain}"
    
    # Fallback to environment variable
    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url and webhook_url != "":
        # Extract base URL if it includes /webhook
        if webhook_url.endswith("/webhook"):
            return webhook_url[:-8]  # Remove /webhook
        return webhook_url
    
    return None

def set_telegram_webhook(bot_token, webhook_url):
    """
    Set the Telegram webhook URL
    """
    if not bot_token:
        logging.error("TELEGRAM_BOT_TOKEN not found in environment variables")
        return False
    
    if not webhook_url:
        logging.error("No webhook URL could be determined")
        return False
    
    # Ensure webhook URL ends with /webhook
    if not webhook_url.endswith("/webhook"):
        webhook_url = f"{webhook_url}/webhook"
    
    try:
        # Set the webhook
        api_url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
        response = requests.post(api_url, data={'url': webhook_url}, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                logging.info(f"Webhook set successfully to {webhook_url}")
                return True
            else:
                logging.error(f"Telegram API error: {result.get('description', 'Unknown error')}")
                return False
        else:
            logging.error(f"HTTP error {response.status_code}: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error setting webhook: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error setting webhook: {e}")
        return False

def verify_webhook(bot_token):
    """
    Verify the current webhook status
    """
    if not bot_token:
        return None
    
    try:
        api_url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
        response = requests.get(api_url, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                return result.get('result', {})
        
        return None
        
    except Exception as e:
        logging.error(f"Error verifying webhook: {e}")
        return None

def setup_webhook_automatically():
    """
    Main function to automatically set up webhook
    """
    logging.basicConfig(level=logging.INFO)
    
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logging.error("TELEGRAM_BOT_TOKEN environment variable is required")
        return False
    
    # Get current webhook info
    current_webhook = verify_webhook(bot_token)
    if current_webhook:
        current_url = current_webhook.get('url', '')
        pending_updates = current_webhook.get('pending_update_count', 0)
        last_error = current_webhook.get('last_error_message', '')
        
        logging.info(f"Current webhook: {current_url}")
        logging.info(f"Pending updates: {pending_updates}")
        if last_error:
            logging.warning(f"Last error: {last_error}")
    
    # Detect deployment URL
    deployment_url = get_deployment_url()
    if not deployment_url:
        logging.error("Could not detect deployment URL. Set WEBHOOK_URL, VERCEL_URL, or REPLIT_DOMAIN")
        return False
    
    logging.info(f"Detected deployment URL: {deployment_url}")
    
    # Check if webhook needs updating
    webhook_url = f"{deployment_url}/webhook"
    if current_webhook and current_webhook.get('url') == webhook_url:
        if not current_webhook.get('last_error_message'):
            logging.info("Webhook is already correctly configured")
            return True
        else:
            logging.info("Webhook configured but has errors, updating...")
    
    # Set the webhook
    success = set_telegram_webhook(bot_token, deployment_url)
    
    if success:
        # Wait a moment and verify
        time.sleep(2)
        updated_webhook = verify_webhook(bot_token)
        if updated_webhook:
            logging.info(f"Webhook verification - URL: {updated_webhook.get('url')}")
            logging.info(f"Pending updates: {updated_webhook.get('pending_update_count', 0)}")
        
        return True
    
    return False

if __name__ == "__main__":
    setup_webhook_automatically()