#!/bin/bash

echo "🚀 Deploying Telegram Trading Bot to Vercel"
echo "=========================================="

# Check if Vercel CLI is installed
if ! command -v vercel &> /dev/null; then
    echo "❌ Vercel CLI not found. Installing..."
    npm install -g vercel
fi

# Create requirements.txt from vercel_requirements.txt for Vercel
if [ -f "vercel_requirements.txt" ]; then
    echo "📦 Copying dependencies..."
    cp vercel_requirements.txt requirements.txt
fi

echo "🔧 Starting deployment..."
vercel --prod

echo "✅ Deployment complete!"
echo ""
echo "📋 Next steps:"
echo "1. Set environment variables in Vercel dashboard:"
echo "   - SESSION_SECRET"
echo "   - DATABASE_URL"
echo "   - TELEGRAM_BOT_TOKEN (optional)"
echo "   - WEBHOOK_URL (optional)"
echo ""
echo "2. Test your deployment at: https://your-project.vercel.app"
echo ""
echo "📖 See README_VERCEL.md for detailed setup instructions"