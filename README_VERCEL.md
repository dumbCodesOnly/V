# Vercel Deployment Guide

## Deployment Steps

### Option 1: GitHub Integration (Recommended)
1. **Push your code to GitHub**
2. **Connect GitHub repo to Vercel**:
   - Go to [vercel.com](https://vercel.com)
   - Click "New Project"
   - Import your GitHub repository
   - Vercel will automatically detect the Flask app

### Option 2: Vercel CLI
1. **Install Vercel CLI**:
   ```bash
   npm i -g vercel
   ```

2. **Deploy to Vercel**:
   ```bash
   vercel --prod
   ```

3. **Set Environment Variables in Vercel Dashboard**:
   - `SESSION_SECRET`: Your Flask secret key
   - `DATABASE_URL`: Your PostgreSQL database connection string
   - `TELEGRAM_BOT_TOKEN`: Your Telegram bot token
   - `WEBHOOK_URL`: Your Vercel domain webhook URL (https://your-app.vercel.app/webhook)

## Required Environment Variables

### Essential
- `SESSION_SECRET`: Flask session secret (generate a secure random string)
- `DATABASE_URL`: PostgreSQL connection string (recommend using Supabase, Neon, or Railway)
- `VERCEL`: Set to "1" (automatically set by Vercel)

### Optional (for Telegram Bot)
- `TELEGRAM_BOT_TOKEN`: Your bot token from @BotFather
- `WEBHOOK_URL`: Full webhook URL (https://your-vercel-domain.vercel.app/webhook)

## Fixed Issues in This Version

- ✅ Fixed serverless function crashes
- ✅ Proper database initialization for serverless environments
- ✅ Corrected Vercel configuration (removed conflicting builds/functions)
- ✅ Added proper WSGI entry point
- ✅ Reduced logging verbosity for production
- ✅ Optimized for cold starts
- ✅ Fixed runtime version specification error
- ✅ Updated to use proper @vercel/python runtime
- ✅ Corrected Python version to 3.9 for Vercel compatibility

## Database Setup

For production deployment, use a managed PostgreSQL service:
- **Supabase**: Free tier available
- **Neon**: Serverless PostgreSQL
- **Railway**: PostgreSQL hosting
- **PlanetScale**: MySQL alternative

## Post-Deployment

1. Set up your database and run migrations
2. Configure your Telegram bot webhook (if using)
3. Test all API endpoints
4. Monitor logs in Vercel dashboard

## Files Created for Vercel

- `vercel.json`: Vercel configuration
- `runtime.txt`: Python version specification
- `Procfile`: Process configuration
- `vercel_requirements.txt`: Python dependencies
- `.gitignore`: Git ignore rules