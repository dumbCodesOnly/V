# Secure Telegram Bot Webhook Setup

## 🔐 SECURE SETUP (RECOMMENDED)

Instead of disabling Vercel deployment protection, use our secure webhook system:

### Step 1: Generate Secret Token
```bash
python setup_secure_webhook.py
```

This will:
- Generate a secure 64-character secret token
- Set your webhook with the secret token
- Give you the token to add to Vercel

### Step 2: Add Secret to Vercel
1. Go to Vercel Dashboard → Your Project → Settings → Environment Variables
2. Add: `WEBHOOK_SECRET_TOKEN=your_generated_token`
3. Redeploy your project

### Step 3: Re-enable Protection
1. Go to Vercel Dashboard → Settings → Deployment Protection
2. Enable "Vercel Authentication"
3. Your webhook will still work securely!

## How It Works

✅ **Secret Token Verification**: Every webhook request must include the correct secret token
✅ **Request Structure Validation**: Verifies the data looks like a real Telegram update  
✅ **Automatic Setup**: Detects Vercel deployment and configures webhook automatically
✅ **Error Logging**: Tracks and blocks invalid requests

## Alternative Methods

### Manual Secret Token Setup:
```bash
# Generate token
openssl rand -hex 32

# Set webhook with token
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://your-app.vercel.app/webhook", "secret_token": "your_token"}'
```

### IP Whitelist (Advanced):
Configure Vercel to only allow Telegram IPs:
- 149.154.160.0/20
- 91.108.4.0/22

## Emergency Fallback

If you need the bot working immediately:
1. Temporarily disable deployment protection
2. Set up the secure method above
3. Re-enable protection

## Testing Your Setup

```bash
# Check webhook status
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"

# Should show your URL and "has_custom_certificate": false
```

Send `/start` to your bot - it should respond with the main menu.