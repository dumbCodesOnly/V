# Complete Vercel Deployment Guide for Telegram Bot

## Step 1: Prepare for Deployment

### A. Choose Your Configuration

**Option 1: Use the secure webhook setup (RECOMMENDED)**
```bash
# Replace your vercel.json with the secure version
cp vercel_secure_webhook.json vercel.json
```

**Option 2: Use the bypass configuration**
```bash
# Replace your vercel.json with the bypass version  
cp vercel_bypass.json vercel.json
```

### B. Verify Environment Variables

Before deployment, ensure you have:
- `TELEGRAM_BOT_TOKEN` (from BotFather)
- `WEBHOOK_SECRET_TOKEN` (optional, for extra security)

## Step 2: Deploy to Vercel

### Method 1: Vercel CLI (Recommended)

1. **Install Vercel CLI** (if not installed):
```bash
npm i -g vercel
```

2. **Login to Vercel**:
```bash
vercel login
```

3. **Deploy**:
```bash
vercel --prod
```

4. **Set Environment Variables**:
```bash
# Set your bot token
vercel env add TELEGRAM_BOT_TOKEN

# Optional: Set webhook secret token for extra security
vercel env add WEBHOOK_SECRET_TOKEN
```

5. **Redeploy with Environment Variables**:
```bash
vercel --prod
```

### Method 2: Vercel Dashboard

1. **Connect Repository**:
   - Go to https://vercel.com/dashboard
   - Click "Add New" → "Project"
   - Import your Git repository

2. **Configure Environment Variables**:
   - In project settings → "Environment Variables"
   - Add `TELEGRAM_BOT_TOKEN` with your bot token
   - Add `WEBHOOK_SECRET_TOKEN` (optional)

3. **Deploy**: 
   - Click "Deploy"

## Step 3: Set Up Webhook

### A. Get Your Deployment URL

After deployment, you'll get a URL like:
`https://your-project-name.vercel.app`

### B. Set the Webhook

**Method 1: Using curl**
```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://your-project-name.vercel.app/webhook",
       "secret_token": "your-secret-token-if-using"
     }'
```

**Method 2: Using Python script**
```bash
python setup_secure_webhook.py
```

**Method 3: Browser (simple)**
Visit this URL in your browser (replace YOUR_BOT_TOKEN and YOUR_DEPLOYMENT_URL):
```
https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=https://YOUR_DEPLOYMENT_URL/webhook
```

### C. Verify Webhook

Check webhook status:
```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

## Step 4: Test Your Bot

1. **Send /start** to your bot on Telegram
2. **Check Vercel logs** for webhook activity:
   - Go to Vercel dashboard → Your project → Functions tab
   - Monitor real-time logs

## Step 5: Enable Deployment Protection (Optional)

1. **Go to Vercel Dashboard** → Your project → Settings → Deployment Protection
2. **Enable "Vercel Authentication"**
3. **Your webhook will still work** because it's configured to bypass auth

## Troubleshooting

### Common Issues:

**1. Bot not responding:**
- Check environment variables are set correctly
- Verify webhook URL in Telegram webhook info
- Check Vercel function logs for errors

**2. Deployment protection blocking webhook:**
- Use our secure webhook configuration
- Verify you're using `vercel_secure_webhook.json`

**3. Environment variables not working:**
- Redeploy after setting environment variables
- Check variable names are exactly correct

### Debug Commands:

```bash
# Check webhook status
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"

# Delete webhook (if needed to reset)
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook"

# Check Vercel deployment
vercel ls
```

## Security Notes

- Never commit your bot token to Git
- Use environment variables for all secrets
- The secure webhook configuration includes request validation
- Monitor Vercel function logs for suspicious activity

Your bot should now work securely with Vercel deployment protection enabled!