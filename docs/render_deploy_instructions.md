# Render Deployment Instructions for Trading Bot

## Critical Issue: Database Configuration Required

Your Render deployment is currently using SQLite instead of PostgreSQL, which causes positions to disappear because SQLite doesn't share data between multiple Gunicorn workers.

## Fix: Add PostgreSQL Database to Render

### Method 1: Using Render Dashboard (Recommended)

1. **Go to your Render Dashboard**
2. **Select your trading-bot service**
3. **Go to Environment tab**
4. **Click "Add Database"**
5. **Choose PostgreSQL**
6. **Name it: `trading-bot-db`**
7. **This automatically sets DATABASE_URL environment variable**

### Method 2: Using render.yaml (Alternative)

The render.yaml file has been updated to include PostgreSQL configuration. If you redeploy with the updated render.yaml, it will:

- Create a PostgreSQL database service
- Automatically set the DATABASE_URL environment variable
- Configure proper database connection pooling for Render

### Environment Variables Needed

Make sure these are set in Render:

```
DATABASE_URL=postgresql://... (auto-set when you add database)
RENDER=1
SESSION_SECRET=your-secret-key
```

### After Adding Database

1. **Redeploy your service** (it will detect PostgreSQL and use proper configuration)
2. **Test the database**: Visit `/api/db-status` endpoint to verify PostgreSQL connection
3. **Create a position**: Positions will now persist across worker restarts

## Verification Steps

After adding PostgreSQL:

1. Check `/api/db-status` shows `"database_type": "postgresql"`
2. Create a test position
3. Switch tabs multiple times - position should remain visible
4. Check `/api/user-trades` shows persistent data

## Current Status

- ❌ SQLite (causes position disappearing)
- ✅ After fix: PostgreSQL (positions persist)

The database configuration in your code is already optimized for Render with proper connection pooling and timeouts.