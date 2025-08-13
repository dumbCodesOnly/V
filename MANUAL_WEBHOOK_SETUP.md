# Manual Webhook Setup for Vercel

## Step 1: Deploy Your App to Vercel

1. **Use the secure configuration:**
```bash
cp vercel_secure_webhook.json vercel.json
```

2. **Deploy to Vercel:**
```bash
vercel --prod
```

3. **Get your deployment URL** (something like `https://your-app-name.vercel.app`)

## Step 2: Set Environment Variables in Vercel

1. **Go to Vercel Dashboard:**
   - Visit https://vercel.com/dashboard
   - Select your project

2. **Add Environment Variables:**
   - Go to Settings → Environment Variables
   - Add `TELEGRAM_BOT_TOKEN` = your bot token from BotFather
   - Optional: Add `WEBHOOK_SECRET_TOKEN` for extra security

3. **Redeploy after adding variables:**
   - Go to Deployments tab
   - Click "Redeploy" on the latest deployment

## Step 3: Set Telegram Webhook Manually

**Method 1: Using curl (recommended)**
```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://your-app-name.vercel.app/webhook"}'
```

**Method 2: Using browser**
Visit this URL (replace with your actual tokens):
```
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://your-app-name.vercel.app/webhook
```

**Method 3: Using Python script**
```python
import requests

bot_token = "YOUR_BOT_TOKEN"
webhook_url = "https://your-app-name.vercel.app/webhook"

response = requests.post(
    f"https://api.telegram.org/bot{bot_token}/setWebhook",
    json={"url": webhook_url}
)

print(response.json())
```

## Step 4: Verify Setup

**Check webhook status:**
```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

Should return something like:
```json
{
  "ok": true,
  "result": {
    "url": "https://your-app-name.vercel.app/webhook",
    "has_custom_certificate": false,
    "pending_update_count": 0
  }
}
```

## Step 5: Test Your Bot

1. Send `/start` to your bot on Telegram
2. Check Vercel function logs in dashboard
3. Bot should respond with the main menu

## Troubleshooting

**Bot not responding:**
- Verify bot token is correct in Vercel environment variables
- Check webhook URL is set correctly
- Look at Vercel function logs for errors

**Webhook errors:**
- Ensure deployment protection is disabled OR using secure config
- Verify the webhook URL is accessible
- Check Telegram webhook info for errors

## Security Notes

- Keep your bot token secure in environment variables
- Consider using webhook secret tokens for production
- Monitor Vercel function logs for suspicious activity
- The secure configuration includes request validation

Your bot should now work with manual webhook configuration!