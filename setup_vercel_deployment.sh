#!/bin/bash

# Vercel Deployment Setup Script for Telegram Bot
# This script handles the complete deployment process

echo "🚀 Telegram Bot Vercel Deployment Setup"
echo "========================================"

# Check if vercel CLI is installed
if ! command -v vercel &> /dev/null; then
    echo "📦 Installing Vercel CLI..."
    npm install -g vercel
fi

# Check if user is logged in
echo "🔐 Checking Vercel authentication..."
if ! vercel whoami &> /dev/null; then
    echo "Please login to Vercel:"
    vercel login
fi

# Choose configuration
echo ""
echo "📋 Choose your deployment configuration:"
echo "1) Secure webhook (recommended)"
echo "2) Bypass configuration" 
echo "3) Keep current vercel.json"
read -p "Enter choice (1-3): " config_choice

case $config_choice in
    1)
        echo "Using secure webhook configuration..."
        cp vercel_secure_webhook.json vercel.json
        ;;
    2)
        echo "Using bypass configuration..."
        cp vercel_bypass.json vercel.json
        ;;
    3)
        echo "Keeping current vercel.json..."
        ;;
    *)
        echo "Invalid choice, using secure webhook configuration..."
        cp vercel_secure_webhook.json vercel.json
        ;;
esac

# Get bot token
echo ""
echo "🤖 Bot Token Setup"
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    read -p "Enter your Telegram bot token: " bot_token
    export TELEGRAM_BOT_TOKEN="$bot_token"
else
    echo "Using bot token from environment variable"
fi

# Optional secret token
echo ""
read -p "🔒 Do you want to use a webhook secret token for extra security? (y/n): " use_secret

if [[ $use_secret =~ ^[Yy]$ ]]; then
    if [ -z "$WEBHOOK_SECRET_TOKEN" ]; then
        # Generate a random secret token
        secret_token=$(openssl rand -hex 32)
        export WEBHOOK_SECRET_TOKEN="$secret_token"
        echo "Generated secret token: $secret_token"
    else
        echo "Using secret token from environment variable"
    fi
fi

# Deploy to Vercel
echo ""
echo "🚀 Deploying to Vercel..."
vercel --prod

# Get deployment URL
deployment_url=$(vercel ls --limit=1 | grep -o 'https://[^ ]*' | head -1)

if [ -z "$deployment_url" ]; then
    echo "❌ Could not determine deployment URL"
    echo "Please get your URL from Vercel dashboard and run:"
    echo "curl -X POST \"https://api.telegram.org/bot\$TELEGRAM_BOT_TOKEN/setWebhook\" -d \"url=YOUR_URL/webhook\""
    exit 1
fi

echo "✅ Deployment successful: $deployment_url"

# Set environment variables on Vercel
echo ""
echo "🔧 Setting environment variables..."

echo "$TELEGRAM_BOT_TOKEN" | vercel env add TELEGRAM_BOT_TOKEN production

if [ ! -z "$WEBHOOK_SECRET_TOKEN" ]; then
    echo "$WEBHOOK_SECRET_TOKEN" | vercel env add WEBHOOK_SECRET_TOKEN production
fi

# Redeploy with environment variables
echo "🔄 Redeploying with environment variables..."
vercel --prod

# Set webhook
webhook_url="${deployment_url}/webhook"
echo ""
echo "🔗 Setting webhook to: $webhook_url"

if [ ! -z "$WEBHOOK_SECRET_TOKEN" ]; then
    # Set webhook with secret token
    webhook_response=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
        -H "Content-Type: application/json" \
        -d "{\"url\": \"$webhook_url\", \"secret_token\": \"$WEBHOOK_SECRET_TOKEN\"}")
else
    # Set webhook without secret token  
    webhook_response=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
        -H "Content-Type: application/json" \
        -d "{\"url\": \"$webhook_url\"}")
fi

# Check webhook response
if echo "$webhook_response" | grep -q '"ok":true'; then
    echo "✅ Webhook set successfully!"
else
    echo "❌ Webhook setup failed:"
    echo "$webhook_response"
    exit 1
fi

# Verify webhook
echo ""
echo "🔍 Verifying webhook..."
webhook_info=$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo")
echo "$webhook_info" | jq '.'

echo ""
echo "🎉 Deployment Complete!"
echo "========================================"
echo "Bot URL: $deployment_url"
echo "Webhook URL: $webhook_url"
echo ""
echo "Next steps:"
echo "1. Send /start to your bot on Telegram"
echo "2. Monitor logs in Vercel dashboard"
echo "3. Your bot should respond securely!"

if [ ! -z "$WEBHOOK_SECRET_TOKEN" ]; then
    echo ""
    echo "🔒 Security Note:"
    echo "Your webhook secret token: $WEBHOOK_SECRET_TOKEN"
    echo "Keep this token secure!"
fi