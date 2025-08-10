import os
import logging
import requests
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import request, jsonify
from app import app, db
from models import BotMessage, BotStatus, Trade
from trading import TradingService
from datetime import datetime

# Configure logging
logger = logging.getLogger(__name__)

# Initialize bot
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable is required")
    raise ValueError("TELEGRAM_BOT_TOKEN is required")

bot = Bot(token=BOT_TOKEN)
trading_service = TradingService()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    welcome_message = f"""
🤖 Welcome to Trading Bot, {user.first_name}!

Available commands:
/start - Show this welcome message
/help - Get help with commands
/price <symbol> - Get current price for a symbol
/buy <symbol> <quantity> - Place a buy order
/sell <symbol> <quantity> - Place a sell order
/portfolio - View your portfolio
/trades - View your recent trades

Example: /price BTCUSDT
Example: /buy ETHUSDT 0.1
"""
    
    await update.message.reply_text(welcome_message)
    
    # Log the interaction
    log_message(user.id, user.username, "/start", welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    user = update.effective_user
    help_message = """
📚 Trading Bot Help

Commands:
• /price <symbol> - Get current market price
  Example: /price BTCUSDT

• /buy <symbol> <quantity> - Place buy order
  Example: /buy ETHUSDT 0.1

• /sell <symbol> <quantity> - Place sell order
  Example: /sell BTCUSDT 0.001

• /portfolio - View your current holdings

• /trades - View your trading history

⚠️ Note: This is a mock trading environment for demonstration purposes.
"""
    
    await update.message.reply_text(help_message)
    log_message(user.id, user.username, "/help", help_message)

async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /price command"""
    user = update.effective_user
    
    if not context.args:
        response = "❌ Please provide a symbol. Example: /price BTCUSDT"
        await update.message.reply_text(response)
        log_message(user.id, user.username, "/price", response)
        return
    
    symbol = context.args[0].upper()
    
    try:
        price_data = await trading_service.get_price(symbol)
        if price_data:
            response = f"💰 {symbol}: ${price_data['price']:.4f}"
        else:
            response = f"❌ Could not fetch price for {symbol}"
    except Exception as e:
        logger.error(f"Error fetching price for {symbol}: {e}")
        response = f"❌ Error fetching price for {symbol}: {str(e)}"
    
    await update.message.reply_text(response)
    log_message(user.id, user.username, f"/price {symbol}", response)

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /buy command"""
    user = update.effective_user
    
    if len(context.args) < 2:
        response = "❌ Please provide symbol and quantity. Example: /buy BTCUSDT 0.001"
        await update.message.reply_text(response)
        log_message(user.id, user.username, "/buy", response)
        return
    
    symbol = context.args[0].upper()
    try:
        quantity = float(context.args[1])
    except ValueError:
        response = "❌ Invalid quantity. Please provide a valid number."
        await update.message.reply_text(response)
        log_message(user.id, user.username, f"/buy {symbol}", response)
        return
    
    try:
        result = await trading_service.place_order(str(user.id), symbol, "buy", quantity)
        if result['success']:
            response = f"✅ Buy order placed: {quantity} {symbol} at ${result['price']:.4f}"
        else:
            response = f"❌ Buy order failed: {result['error']}"
    except Exception as e:
        logger.error(f"Error placing buy order: {e}")
        response = f"❌ Error placing buy order: {str(e)}"
    
    await update.message.reply_text(response)
    log_message(user.id, user.username, f"/buy {symbol} {quantity}", response)

async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sell command"""
    user = update.effective_user
    
    if len(context.args) < 2:
        response = "❌ Please provide symbol and quantity. Example: /sell BTCUSDT 0.001"
        await update.message.reply_text(response)
        log_message(user.id, user.username, "/sell", response)
        return
    
    symbol = context.args[0].upper()
    try:
        quantity = float(context.args[1])
    except ValueError:
        response = "❌ Invalid quantity. Please provide a valid number."
        await update.message.reply_text(response)
        log_message(user.id, user.username, f"/sell {symbol}", response)
        return
    
    try:
        result = await trading_service.place_order(str(user.id), symbol, "sell", quantity)
        if result['success']:
            response = f"✅ Sell order placed: {quantity} {symbol} at ${result['price']:.4f}"
        else:
            response = f"❌ Sell order failed: {result['error']}"
    except Exception as e:
        logger.error(f"Error placing sell order: {e}")
        response = f"❌ Error placing sell order: {str(e)}"
    
    await update.message.reply_text(response)
    log_message(user.id, user.username, f"/sell {symbol} {quantity}", response)

async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /portfolio command"""
    user = update.effective_user
    
    try:
        portfolio = trading_service.get_portfolio(str(user.id))
        if not portfolio:
            response = "📊 Your portfolio is empty. Start trading to see your holdings!"
        else:
            response = "📊 Your Portfolio:\n\n"
            for holding in portfolio:
                response += f"• {holding['symbol']}: {holding['quantity']:.6f} (${holding['value']:.2f})\n"
    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}")
        response = f"❌ Error fetching portfolio: {str(e)}"
    
    await update.message.reply_text(response)
    log_message(user.id, user.username, "/portfolio", response)

async def trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /trades command"""
    user = update.effective_user
    
    try:
        trades = trading_service.get_recent_trades(str(user.id))
        if not trades:
            response = "📈 No recent trades found."
        else:
            response = "📈 Recent Trades:\n\n"
            for trade in trades[-5:]:  # Show last 5 trades
                status_emoji = "✅" if trade.status == "executed" else "⏳" if trade.status == "pending" else "❌"
                response += f"{status_emoji} {trade.action.upper()} {trade.quantity} {trade.symbol}"
                if trade.price:
                    response += f" @ ${trade.price:.4f}"
                response += f"\n   {trade.timestamp.strftime('%Y-%m-%d %H:%M')}\n\n"
    except Exception as e:
        logger.error(f"Error fetching trades: {e}")
        response = f"❌ Error fetching trades: {str(e)}"
    
    await update.message.reply_text(response)
    log_message(user.id, user.username, "/trades", response)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle non-command messages"""
    user = update.effective_user
    message_text = update.message.text
    
    response = "🤔 I didn't understand that command. Type /help to see available commands."
    await update.message.reply_text(response)
    log_message(user.id, user.username, message_text, response)

def log_message(user_id, username, message, response):
    """Log bot interaction to database"""
    try:
        with app.app_context():
            bot_message = BotMessage(
                user_id=str(user_id),
                username=username,
                message=message,
                response=response,
                command_type=message.split()[0] if message.startswith('/') else 'message'
            )
            db.session.add(bot_message)
            
            # Update bot status
            status = BotStatus.query.first()
            if not status:
                status = BotStatus()
                db.session.add(status)
            
            status.last_heartbeat = datetime.utcnow()
            status.total_messages += 1
            
            db.session.commit()
    except Exception as e:
        logger.error(f"Error logging message: {e}")

def setup_webhook():
    """Setup webhook for the bot"""
    if WEBHOOK_URL:
        try:
            webhook_url = f"{WEBHOOK_URL}/webhook"
            response = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                data={"url": webhook_url}
            )
            if response.status_code == 200:
                logger.info(f"Webhook set successfully to {webhook_url}")
            else:
                logger.error(f"Failed to set webhook: {response.text}")
        except Exception as e:
            logger.error(f"Error setting webhook: {e}")
    else:
        logger.warning("WEBHOOK_URL not provided, webhook not set")

# Initialize application
application = Application.builder().token(BOT_TOKEN).build()

# Add handlers
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("price", price_command))
application.add_handler(CommandHandler("buy", buy_command))
application.add_handler(CommandHandler("sell", sell_command))
application.add_handler(CommandHandler("portfolio", portfolio_command))
application.add_handler(CommandHandler("trades", trades_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
