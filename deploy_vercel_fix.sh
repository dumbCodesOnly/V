#!/bin/bash

# Deploy script to fix Vercel live data issue
echo "🚀 Preparing Vercel deployment files..."

# Copy the working app.py to api directory
echo "📄 Copying main app.py to api/app.py..."
cp app.py api/app.py

# Update api/index.py to remove VERCEL environment variable 
echo "🔧 Updating api/index.py to remove VERCEL restriction..."
cat > api/index.py << 'EOF'
"""
Vercel serverless function entry point - Import complete app without VERCEL flag
"""
# Import the complete application from app.py (without setting VERCEL environment)
from .app import app
EOF

echo "✅ Files prepared for Vercel deployment!"
echo ""
echo "📋 Next steps:"
echo "1. Git add the updated files:"
echo "   git add api/app.py api/index.py"
echo "2. Commit the changes:"
echo "   git commit -m 'Fix: Use live market data in Vercel deployment'"
echo "3. Push to trigger Vercel redeployment:"
echo "   git push"
echo ""
echo "🔍 This will make Vercel use live Binance API data instead of fallback data."