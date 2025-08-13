# Quick Deployment Instructions

## Option 1: Automated Script (EASIEST)

```bash
# Run the automated deployment script
./setup_vercel_deployment.sh
```

This script will:
- Install Vercel CLI if needed
- Handle login
- Choose configuration 
- Deploy your app
- Set environment variables
- Configure webhook automatically

## Option 2: Manual Steps

### 1. Replace vercel.json
```bash
cp vercel_secure_webhook.json vercel.json
```

### 2. Deploy
```bash
vercel --prod
```

### 3. Set Environment Variables
Go to Vercel Dashboard → Your Project → Settings → Environment Variables:
- Add `TELEGRAM_BOT_TOKEN` = your bot token

### 4. Set Webhook
Replace YOUR_URL with your Vercel deployment URL:
```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -d "url=https://YOUR_URL.vercel.app/webhook"
```

## Option 3: Browser Method

1. Deploy to Vercel through their web interface
2. Set environment variables in dashboard
3. Visit this URL in browser (replace tokens):
```
https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=https://YOUR_DEPLOYMENT_URL/webhook
```

## Test Your Bot

Send `/start` to your bot on Telegram - it should respond immediately!