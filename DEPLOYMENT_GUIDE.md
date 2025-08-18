# Toobit Multi-Trade Telegram Bot - Complete Deployment Guide

## Overview
This guide covers both Replit development and Vercel production deployment for the Telegram Trading Bot.

## Replit Development Environment

### Quick Start
1. The Flask application runs automatically via the "Start application" workflow
2. Access the web interface at the provided Replit URL
3. The application uses SQLite database for development

### Environment Variables (Replit)
```bash
SESSION_SECRET=your-secure-random-string
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
DATABASE_URL=sqlite:///trading_bot.db
```

## Vercel Production Deployment

### Prerequisites
- Vercel account connected to your Git repository
- PostgreSQL database (recommended: Neon, PlanetScale, or Vercel Postgres)
- Telegram Bot Token

### Environment Variables (Vercel)
Set these in your Vercel dashboard:
```bash
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
SESSION_SECRET=your-secure-random-string
DATABASE_URL=your-neon-postgresql-connection-string
WEBHOOK_SECRET_TOKEN=optional-webhook-security-token
VERCEL=1
```

**CRITICAL**: The `DATABASE_URL` must be a valid Neon PostgreSQL connection string that starts with `postgresql://`. The application uses database persistence to ensure trades don't get deleted after execution. Without a proper database connection, all trades will be lost on serverless cold starts.

**Neon-Specific Configuration:**
- The connection string includes SSL by default (required by Neon)
- Optimized for serverless with minimal connection pooling
- Includes retry logic for Neon's occasional connection drops
- Application name is set for better logging in Neon dashboard

### Deployment Configuration
The project includes:
- `vercel.json` - Vercel deployment configuration
- `api/app.py` - Main Flask application
- `api/index.py` - Vercel serverless entry point
- `api/requirements.txt` - Python dependencies

### Manual Webhook Setup
After deployment, set up the Telegram webhook:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://your-vercel-app.vercel.app/webhook",
       "secret_token": "your-secret-token"
     }'
```

### Security Features
- Webhook request validation
- Secret token authentication
- IP-based filtering (optional)
- Encrypted API credential storage

## Database Schema
The application automatically creates required tables:
- `user_credentials` - Encrypted API keys
- `user_trading_sessions` - Trading session data
- `trade_configurations` - Persistent trade configurations and execution data

**Database Persistence Features:**
- All trade configurations are automatically saved to database
- Trade execution status and P&L are persisted across sessions
- Closed positions history is maintained permanently
- Serverless-optimized with explicit transaction handling
- Automatic database initialization on cold starts

## API Endpoints
- `/` - Main Telegram Mini-App interface
- `/webhook` - Telegram webhook handler
- `/api/market-data` - Live market data
- `/api/kline-data` - Chart data
- `/api/user-positions` - User trading positions
- `/api/save-trade` - Save trade configurations
- `/api/execute-trade` - Execute trades

## Troubleshooting

### Vercel 404 Error
Ensure `vercel.json` points to `api/app.py` and `api/index.py` imports correctly:
```python
from .app import app
```

### Database Connection Issues
- For Replit: Ensure SQLite file permissions
- For Vercel: Verify PostgreSQL connection string format

### Webhook Not Receiving Messages
1. Check webhook URL is publicly accessible
2. Verify bot token is correct
3. Ensure secret token matches (if used)

## Development vs Production
- **Replit**: SQLite database, debug mode enabled, local development
- **Vercel**: PostgreSQL database, production optimized, serverless deployment

## File Structure (After Consolidation)
```
├── api/
│   ├── app.py           # Main Flask application
│   ├── models.py        # Database models
│   ├── index.py         # Vercel entry point
│   ├── requirements.txt # Dependencies
│   └── templates/       # HTML templates
├── main.py              # Replit entry point
├── vercel.json          # Vercel configuration
├── requirements.txt     # Main dependencies
└── replit.md           # Project documentation
```

This consolidated structure eliminates redundancy while maintaining full functionality for both development and production environments.