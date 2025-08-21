#!/bin/bash

# Vercel Deployment Script - Toobit Trading Bot v2.3
# Latest Updates: Price source fix, Flask context improvements, enhanced error handling

echo "🚀 Deploying Toobit Trading Bot to Vercel/Neon..."
echo "📅 Version: v2.3 - Price Source Fix & Exchange Integration"
echo "==============================================="

# Check if required files exist
echo "🔍 Checking deployment files..."

required_files=(
    "api/app.py"
    "api/index.py" 
    "api/models.py"
    "api/toobit_client.py"
    "api/templates/mini_app.html"
    "api/requirements.txt"
    "api/create_vercel_schema.sql"
    "vercel.json"
)

missing_files=()
for file in "${required_files[@]}"; do
    if [[ ! -f "$file" ]]; then
        missing_files+=("$file")
    fi
done

if [[ ${#missing_files[@]} -gt 0 ]]; then
    echo "❌ Missing required files:"
    printf '%s\n' "${missing_files[@]}"
    exit 1
fi

echo "✅ All required files present"

# Validate environment variables
echo ""
echo "⚠️  IMPORTANT: Ensure these environment variables are set in Vercel dashboard:"
echo "   DATABASE_URL=<neon-postgresql-url>"
echo "   SESSION_SECRET=<random-secure-string>"
echo "   TELEGRAM_BOT_TOKEN=<telegram-bot-token>"
echo "   WEBHOOK_URL=<vercel-domain>/webhook"
echo "   VERCEL=1"
echo ""

# Check if vercel CLI is installed
if ! command -v vercel &> /dev/null; then
    echo "❌ Vercel CLI not found. Install with: npm i -g vercel"
    exit 1
fi

echo "✅ Vercel CLI found"

# Display latest updates
echo ""
echo "📋 Latest Updates in This Deployment:"
echo "   ✅ Fixed price source issue - now uses Toobit exchange prices first"
echo "   ✅ Enhanced fallback system (CoinGecko, Binance, CryptoCompare)"
echo "   ✅ Resolved Flask application context errors"
echo "   ✅ Improved Toobit API signature validation"
echo "   ✅ Enhanced error handling and diagnostics"
echo "   ✅ Serverless-optimized price fetching"
echo ""

# Confirm deployment
read -p "🚀 Ready to deploy to production? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Deployment cancelled"
    exit 1
fi

# Deploy to production
echo "🚀 Deploying to Vercel production..."
vercel --prod

if [[ $? -eq 0 ]]; then
    echo ""
    echo "🎉 Deployment successful!"
    echo ""
    echo "📋 Post-deployment checklist:"
    echo "   □ Verify Telegram WebView loads correctly"
    echo "   □ Test price data displays (should use fallback APIs)"
    echo "   □ Validate API key setup works"
    echo "   □ Check database connections in Vercel logs"
    echo "   □ Test trade configuration saving"
    echo ""
    echo "🔧 Monitoring:"
    echo "   • Check Vercel function logs for any Toobit API issues"
    echo "   • Monitor Neon database connection usage"
    echo "   • Verify price accuracy with fallback sources"
    echo ""
    echo "📖 Documentation: See VERCEL_DEPLOYMENT_STATUS.md for details"
else
    echo "❌ Deployment failed. Check Vercel logs for details."
    exit 1
fi