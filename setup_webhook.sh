#!/bin/bash
# Webhook setup script for Vercel deployment

echo "Setting up Telegram webhook for Vercel deployment..."

if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "Error: TELEGRAM_BOT_TOKEN environment variable not set"
    exit 1
fi

if [ -z "$VERCEL_URL" ]; then
    echo "Error: VERCEL_URL environment variable not set"
    exit 1
fi

WEBHOOK_URL="https://${VERCEL_URL}/webhook"

echo "Setting webhook to: $WEBHOOK_URL"

# Use curl to set the webhook
response=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
    -d "url=${WEBHOOK_URL}")

# Check if the response contains "ok":true
if echo "$response" | grep -q '"ok":true'; then
    echo "✅ Webhook set successfully!"
    echo "Response: $response"
else
    echo "❌ Failed to set webhook"
    echo "Response: $response"
    exit 1
fi