# Vercel Deployment Guide

## Quick Setup

1. **Environment Variables**
   Set these in your Vercel dashboard:
   ```
   DATABASE_URL=<your-neon-postgresql-url>
   SESSION_SECRET=<random-string>
   TELEGRAM_BOT_TOKEN=<your-bot-token>
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
✅ **Real Trading Integration**: Full Toobit exchange order placement
✅ **Risk Management**: Automatic TP/SL order placement on exchange
✅ **Position Management**: Real position closure and order cancellation
✅ **Testnet/Mainnet Toggle**: Safe testing before real money trading
✅ **Exchange Order Tracking**: Complete order lifecycle management
✅ **Price Source Fix**: Exchange-accurate pricing with intelligent fallbacks
✅ **Enhanced Error Handling**: Clear diagnostics for API issues
✅ **Flask Context Management**: Resolved serverless context issues
✅ **SMC Signal Caching**: Database-backed caching for stable entry prices (15-min TTL)

## Troubleshooting

- **Column not found errors**: Run the migration script from `api/create_vercel_schema.sql`
- **Connection issues**: Verify DATABASE_URL format and SSL settings
- **QueuePool timeout errors**: 
  - Check Neon connection limits in dashboard
  - Verify database isn't paused (auto-pause disabled)
  - Monitor concurrent connections in Neon logs
- **SSL/Connection errors**: Ensure `sslmode=require` in DATABASE_URL

## Neon-Specific Settings

For optimal performance with Neon:
- Disable auto-pause in Neon dashboard for production
- Monitor connection usage in Neon console
- Consider upgrading to higher connection limits if needed
- Use connection pooling (already configured in the app)