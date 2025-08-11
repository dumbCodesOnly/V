#!/bin/bash
# Automated Deployment Script for Toobit Trading Bot
# Handles Mini-App URL setup and webhook configuration

set -e  # Exit on any error

echo "🚀 Starting Toobit Trading Bot Deployment..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Python setup script exists
if [ ! -f "setup_miniapp.py" ]; then
    print_error "setup_miniapp.py not found!"
    exit 1
fi

# Make setup script executable
chmod +x setup_miniapp.py

print_status "Checking environment..."

# Check for required environment variables
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    print_warning "TELEGRAM_BOT_TOKEN not set - manual webhook setup required"
fi

if [ -z "$REPLIT_DOMAINS" ]; then
    print_warning "REPLIT_DOMAINS not detected - using fallback method"
fi

print_status "Generating Mini-App setup instructions..."

# Run the automated setup
python3 setup_miniapp.py

print_success "Deployment setup complete!"

# Additional checks
print_status "Performing health checks..."

# Check if Flask app is running
if curl -s -f "http://localhost:5000/health" > /dev/null 2>&1; then
    print_success "Flask application is running"
else
    print_warning "Flask application may not be running on port 5000"
fi

# Check database
if [ -f "instance/trading_bot.db" ]; then
    print_success "Database file exists"
else
    print_warning "Database file not found - will be created on first run"
fi

echo ""
print_success "🎉 Deployment automation complete!"
echo ""
print_status "Next steps:"
echo "  1. Copy the Mini-App URL from above"
echo "  2. Configure it in @BotFather as shown in instructions"
echo "  3. Test your bot by sending /start to your Telegram bot"
echo ""