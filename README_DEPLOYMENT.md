# Automated Telegram Mini-App Setup

This project includes automated tools to set up your Telegram Mini-App URL whenever you deploy to a new Replit environment.

## 🚀 Quick Setup (Recommended)

Run this command to get your current Mini-App URL:

```bash
python3 quick_setup.py
```

Then follow the simple instructions to configure your bot in @BotFather.

## 🔧 Advanced Setup

For complete automation including webhook setup:

```bash
python3 setup_miniapp.py
```

This will:
- Detect your current Replit domain
- Generate the correct Mini-App URL
- Show detailed setup instructions
- Automatically configure webhook (if TELEGRAM_BOT_TOKEN is set)

## 📋 Manual Commands

### Get just the URL:
```bash
python3 setup_miniapp.py --url-only
```

### Get webhook setup info:
```bash
python3 setup_miniapp.py --webhook-only
```

### Full deployment script:
```bash
./scripts/deploy.sh
```

## 🔑 Environment Variables

For automatic webhook setup, add these to your Replit Secrets:

- `TELEGRAM_BOT_TOKEN` - Your bot token from @BotFather
- `SESSION_SECRET` - Random string for session encryption

## 📱 BotFather Setup Steps

1. Open Telegram and go to @BotFather
2. Send: `/myapps`
3. Select your trading bot
4. Choose "Edit Mini App" or "Create Mini App"
5. Paste your generated URL
6. Set Short Name (e.g., "ToobitTrader")
7. Upload icon (512x512 PNG)
8. Add description

## 🔄 After Each Deployment

Simply run `python3 quick_setup.py` to get your new URL and update it in @BotFather if the domain changed.

## ✅ Verification

Test your setup by:
1. Sending `/start` to your Telegram bot
2. Opening the Mini-App from the bot menu
3. Checking that the web interface loads properly

## 🆘 Troubleshooting

**URL not working?**
- Ensure your Replit is running
- Check that port 5000 is accessible
- Verify the URL in your browser first

**Webhook issues?**
- Check TELEGRAM_BOT_TOKEN is set correctly
- Run webhook setup manually if needed
- Check bot logs for errors