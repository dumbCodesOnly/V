# Vercel Deployment Guide

## Quick Setup

1. **Environment Variables**
   Set these in your Vercel dashboard:
   ```
   DATABASE_URL=<your-neon-postgresql-url>
   SESSION_SECRET=<random-string>
   TELEGRAM_BOT_TOKEN=<your-bot-token>
   WEBHOOK_URL=<your-vercel-url>/webhook
   VERCEL=1
   ```

2. **Database Schema**
   Run the SQL script in `api/create_vercel_schema.sql` in your Neon dashboard to ensure all required tables and columns exist.

3. **Deploy**
   ```bash
   vercel --prod
   ```

## Database Migration

The application automatically handles schema migrations on startup, but for fresh Neon databases, you can manually run:

```sql
-- Copy contents from api/create_vercel_schema.sql
```

## Features

✅ Automatic database migration system
✅ Neon PostgreSQL optimization  
✅ Serverless function compatibility
✅ On-demand sync service (no background processes)
✅ SSL connection handling
✅ Connection pooling for serverless

## Troubleshooting

- **Column not found errors**: Run the migration script from `api/create_vercel_schema.sql`
- **Connection issues**: Verify DATABASE_URL format and SSL settings
- **Timeout issues**: Check Neon connection limits and pool settings