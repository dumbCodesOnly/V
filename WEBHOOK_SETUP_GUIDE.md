# Telegram Bot Webhook Setup for Vercel

## Quick Setup Steps

### 1. Environment Variables in Vercel
Add these environment variables in your Vercel dashboard:

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
WEBHOOK_URL=https://v0-033-pi.vercel.app/webhook
SESSION_SECRET=your_secure_random_string
```

### 2. Set Webhook Manually
Run this command with your bot token:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
     -d "url=https://v0-033-pi.vercel.app/webhook"
```

### 3. Verify Webhook
Check webhook status:

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

### 4. Test Bot
Send `/start` to your bot in Telegram to test.

## Troubleshooting

### If Bot Doesn't Respond:
1. Check Vercel function logs
2. Verify environment variables are set
3. Ensure webhook URL is correct
4. Test webhook endpoint: `curl https://v0-033-pi.vercel.app/webhook`

### Reset Webhook:
```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/deleteWebhook"
```

## Automatic Setup Script
Run the setup script if environment variables are configured:
```bash
python setup_webhook_vercel.py
```

## Security Features
- Request validation for Telegram webhooks
- User-Agent verification  
- Content-Type checking
- Structured data validation
- Rate limiting ready