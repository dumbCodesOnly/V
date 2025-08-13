# Telegram Bot Webhook Setup for Vercel

## Automatic Webhook Setup

Your Telegram bot now includes automatic webhook configuration that works on deployment. Here's what happens:

### On Vercel Deployment:
1. The app automatically detects it's running on Vercel (`VERCEL` environment variable)
2. It reads the `VERCEL_URL` environment variable to get the deployment URL
3. It automatically sets the Telegram webhook to `https://YOUR_VERCEL_URL/webhook`

### Required Environment Variables:
- `TELEGRAM_BOT_TOKEN` - Your bot token from BotFather
- `VERCEL_URL` - Automatically provided by Vercel
- `WEBHOOK_URL` - Optional override (if you want to use a custom URL)

### Manual Webhook Setup (if needed):

If you need to manually set up the webhook, you can use the provided scripts:

```bash
# Make the script executable
chmod +x setup_webhook.sh

# Run the webhook setup
./setup_webhook.sh
```

Or use the Python script:
```bash
python webhook_setup.py
```

### Testing the Webhook:

1. Deploy your app to Vercel
2. Check the deployment logs to see if webhook was set successfully
3. Send `/start` to your bot on Telegram
4. The bot should respond with the main menu

### Troubleshooting:

If the bot doesn't respond:
1. Check the Vercel function logs
2. Verify environment variables are set correctly
3. Use this command to check webhook status:
```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

The webhook should point to your Vercel deployment URL + `/webhook`.