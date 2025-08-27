# Deployment Guide: Replit Agent to Standard Replit + Vercel

## Overview
This guide covers deploying the Toobit Multi-Trade Telegram Bot to both Replit (development) and Vercel (production) environments with full exchange synchronization support.

## Architecture

### Replit Environment (Development)
- **Background Services**: Full `ExchangeSyncService` with 60-second polling
- **Database**: PostgreSQL with connection pooling
- **Sync Method**: Continuous background monitoring
- **Performance**: Optimized for persistent server environment

### Vercel Environment (Production)
- **On-Demand Services**: `VercelSyncService` with smart cooldown (30s)
- **Database**: Neon PostgreSQL with serverless optimization
- **SMC Signal Caching**: Database-backed caching for stable entry prices (15-min TTL)
- **Sync Method**: Triggered by user requests and webhooks
- **Performance**: Optimized for serverless cold starts

## Environment Variables Required

### Core Application
```bash
# Database
DATABASE_URL=postgresql://user:pass@host:port/db
NEON_DATABASE_URL=postgresql://user:pass@neon-host/db  # For Vercel

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
WEBHOOK_URL=https://your-domain.com/webhook
WEBHOOK_SECRET_TOKEN=your_webhook_secret

# Encryption
SESSION_SECRET=your_session_secret_key

# Toobit Exchange (Optional - for webhook security)
TOOBIT_WEBHOOK_SECRET=your_toobit_webhook_secret
```

### Environment Detection
```bash
# Automatically set by platforms
VERCEL=1          # Set by Vercel
REPLIT_DOMAIN=... # Set by Replit
```

## Exchange Synchronization Features

### API Endpoints
- `/api/exchange/sync-status` - Get sync service status
- `/api/exchange/force-sync` - Force immediate sync
- `/api/exchange/test-connection` - Test Toobit API connection
- `/api/exchange/positions` - Get live exchange positions
- `/api/exchange/orders` - Get exchange orders
- `/webhook/toobit` - Handle Toobit webhooks

### Replit Sync Behavior
```python
# Background service runs continuously
- Polls exchange every 60 seconds
- Monitors all users with active positions
- Updates database automatically
- Handles connection retries
```

### Vercel Sync Behavior
```python
# On-demand service triggered by:
- User API requests (with 30s cooldown)
- Force sync requests
- Webhook events
- Position live updates
```

## Deployment Steps

### 1. Replit Deployment
```bash
# Environment is already configured
# Service runs automatically via gunicorn
# Background sync starts on app initialization
```

### 2. Vercel Deployment
```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
vercel --prod

# Set environment variables
vercel env add DATABASE_URL
vercel env add TELEGRAM_BOT_TOKEN
vercel env add SESSION_SECRET
# ... add other required variables
```

### 3. Webhook Configuration

#### Telegram Webhook
```bash
curl -X POST "https://api.telegram.org/bot{BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-domain.com/webhook",
    "secret_token": "your_webhook_secret"
  }'
```

#### Toobit Webhook (Optional)
```bash
# Configure in Toobit dashboard
URL: https://your-domain.com/webhook/toobit
Secret: your_toobit_webhook_secret
Events: ORDER_UPDATE, POSITION_UPDATE, BALANCE_UPDATE
```

## Database Optimization

### Neon PostgreSQL for Vercel
```python
# Optimized connection settings
{
    "pool_recycle": 3600,
    "pool_pre_ping": True,
    "pool_size": 1,
    "max_overflow": 0,
    "pool_timeout": 30,
    "connect_args": {
        "sslmode": "require",
        "connect_timeout": 10,
        "application_name": "trading_bot_vercel"
    }
}
```

### Standard PostgreSQL for Replit
```python
# Standard connection settings
{
    "pool_recycle": 300,
    "pool_pre_ping": True,
    "pool_size": 5,
    "max_overflow": 10
}
```

## Performance Considerations

### Replit (Always-On Server)
- Background sync service runs continuously
- Full real-time monitoring
- Immediate position updates
- Higher resource usage but better responsiveness

### Vercel (Serverless Functions)
- On-demand sync with intelligent cooldown
- Webhook-driven updates for real-time events
- Cold start optimization
- Lower resource usage, slight delay in sync

## Monitoring and Debugging

### Check Sync Status
```bash
# Get sync service status
GET /api/exchange/sync-status?user_id=123456789

# Force sync (useful for testing)
POST /api/exchange/force-sync?user_id=123456789
```

### Test Exchange Connection
```bash
POST /api/exchange/test-connection?user_id=123456789
```

### View Logs
- **Replit**: Check workflow console logs
- **Vercel**: Check function logs in Vercel dashboard

## Security Best Practices

1. **API Credentials**: Encrypted in database using Fernet
2. **Webhook Security**: Secret token validation
3. **Environment Variables**: Secure storage of sensitive data
4. **Database**: SSL connections required for production
5. **Rate Limiting**: Built-in cooldown for sync operations

## Troubleshooting

### Common Issues
1. **Connection Timeouts**: Check database URL and credentials
2. **Sync Not Working**: Verify API credentials are set up
3. **Webhook Failures**: Check secret tokens and URL configuration
4. **Cold Start Delays**: Normal for Vercel, use force sync if needed

### Debug Commands
```bash
# Check if services are running
curl https://your-domain.com/api/health

# Test database connection
curl https://your-domain.com/api/positions

# Check exchange integration
curl -X POST https://your-domain.com/api/exchange/test-connection
```

This dual-environment setup provides the best of both worlds: real-time monitoring for development and optimized serverless performance for production.