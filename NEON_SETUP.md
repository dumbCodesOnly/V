# Neon PostgreSQL Setup Guide for Trading Bot

## Step 1: Create Neon Database

1. Sign up at [neon.tech](https://neon.tech)
2. Create a new project
3. Choose region closest to your Vercel deployment region (typically US East for better latency)
4. Note down your database credentials

## Step 2: Get Connection String

From your Neon dashboard, copy the connection string:
```
postgresql://username:password@ep-xxx-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require
```

## Step 3: Configure Vercel Environment Variables

In your Vercel dashboard → Project Settings → Environment Variables, add:

```bash
DATABASE_URL=postgresql://username:password@ep-xxx-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
SESSION_SECRET=your-secure-random-string
WEBHOOK_SECRET_TOKEN=optional-webhook-security-token
VERCEL=1
```

## Step 4: Verify Database Connection

Deploy to Vercel and check the function logs for:
- "Database tables created successfully"
- "Loaded X trades for user Y from database"

## Neon-Specific Optimizations Included

✅ **SSL Connection**: Required by Neon, automatically configured
✅ **Connection Pooling**: Optimized for serverless (pool_size=1)
✅ **Retry Logic**: Handles Neon's occasional connection drops
✅ **Connection Timeout**: 10-second timeout for reliable connections
✅ **Application Name**: Identifies your app in Neon logs as "trading_bot_vercel"
✅ **Long-lived Connections**: 1-hour pool recycle (Neon allows longer connections)

## Troubleshooting

**Connection Issues:**
- Ensure your Neon database is in "Active" state
- Check that the connection string includes `?sslmode=require`
- Verify the region matches your Vercel deployment region

**Performance Issues:**
- Monitor Neon dashboard for connection statistics
- Check Vercel function logs for database retry attempts
- Consider upgrading Neon plan if hitting connection limits

**Data Persistence:**
- All trades are automatically saved to Neon database
- Trades persist across Vercel deployments and cold starts
- Database tables are created automatically on first deployment