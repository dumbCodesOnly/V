import os
import logging
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
import requests
from trade_config import TradeConfig
from portfolio_tracker import PortfolioTracker
from multi_trade_manager import MultiTradeManager

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "your_bot_token")
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        
        # Multi-trade support
        self.multi_trade_manager = MultiTradeManager(self)
        self.portfolio_tracker = PortfolioTracker()
        
        # Legacy support - redirect to multi-trade manager
        self._configs: Dict[int, TradeConfig] = {}
        self._trading_bots: Dict[int, Any] = {}
    
    @property
    def configs(self) -> Dict[int, TradeConfig]:
        """Legacy configs property for backward compatibility"""
        return self._configs
    
    @property
    def trading_bots(self) -> Dict[int, Any]:
        """Legacy trading_bots property for backward compatibility"""
        return self._trading_bots
        
    def send_message(self, chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Send message to Telegram chat"""
        url = f"{self.base_url}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        
        try:
            response = requests.post(url, json=data, timeout=10)
            result = response.json()
            if not result.get('ok'):
                # Log specific error codes for debugging
                error_code = result.get('error_code')
                description = result.get('description', 'Unknown error')
                
                if error_code == 400 and 'chat not found' in description:
                    logger.debug(f"Chat {chat_id} not found (test/invalid chat)")
                else:
                    logger.error(f"Telegram API error [{error_code}]: {description}")
            return result
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return None

    def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Edit existing message"""
        url = f"{self.base_url}/editMessageText"
        data = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        
        try:
            response = requests.post(url, json=data, timeout=10)
            result = response.json()
            if not result.get('ok'):
                logger.error(f"Telegram API error (edit): {result}")
            return result
        except Exception as e:
            logger.error(f"Error editing message: {e}")
            return None

    def get_main_menu(self) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get main menu keyboard"""
        return {
            "inline_keyboard": [
                [{"text": "🔄 Multi-Trade Manager", "callback_data": "menu_multitrade"}],
                [{"text": "⚙️ Configuration", "callback_data": "menu_config"}],
                [{"text": "📊 Trading", "callback_data": "menu_trading"}],
                [{"text": "💼 Portfolio & Analytics", "callback_data": "menu_portfolio"}],
                [{"text": "🔧 Advanced Settings", "callback_data": "menu_advanced"}],
                [{"text": "📈 Status", "callback_data": "status"}]
            ]
        }

    def get_multitrade_menu(self, chat_id: int) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get multi-trade management menu"""
        summary = self.multi_trade_manager.get_trade_summary(chat_id)
        
        keyboard = [
            [{"text": "📋 View All Trades", "callback_data": "multitrade_list"}],
            [{"text": "➕ Create New Trade", "callback_data": "multitrade_new"}],
        ]
        
        if summary['total_trades'] > 0:
            keyboard.extend([
                [{"text": "🎯 Select Trade", "callback_data": "multitrade_select"}],
                [{"text": "🚀 Start Selected Trade", "callback_data": "multitrade_start"}],
                [{"text": "⏹️ Stop Running Trades", "callback_data": "multitrade_stop_all"}],
            ])
        
        keyboard.extend([
            [{"text": "📊 Multi-Trade Status", "callback_data": "multitrade_status"}],
            [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
        ])
        
        return {"inline_keyboard": keyboard}

    def get_trade_list_menu(self, chat_id: int) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get trade list menu for selection"""
        trades = self.multi_trade_manager.get_user_trades(chat_id)
        keyboard = []
        
        for trade_id, config in trades.items():
            status_emoji = "🟢" if config.status == "active" else "🟡" if config.status == "configured" else "🔴"
            button_text = f"{status_emoji} {config.get_display_name()}"
            keyboard.append([{"text": button_text, "callback_data": f"trade_select_{trade_id}"}])
        
        keyboard.append([{"text": "🏠 Back to Multi-Trade", "callback_data": "menu_multitrade"}])
        return {"inline_keyboard": keyboard}

    def get_trade_actions_menu(self, trade_id: str) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get actions menu for a specific trade"""
        return {
            "inline_keyboard": [
                [{"text": "✏️ Edit Trade", "callback_data": f"trade_edit_{trade_id}"}],
                [{"text": "🚀 Start Trade", "callback_data": f"trade_start_{trade_id}"}],
                [{"text": "⏹️ Stop Trade", "callback_data": f"trade_stop_{trade_id}"}],
                [{"text": "📋 Copy Trade", "callback_data": f"trade_copy_{trade_id}"}],
                [{"text": "🗑️ Delete Trade", "callback_data": f"trade_delete_{trade_id}"}],
                [{"text": "🏠 Back to List", "callback_data": "multitrade_list"}]
            ]
        }

    def get_config_menu(self) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get configuration menu keyboard - simplified, moved trading basics to Trading menu"""
        return {
            "inline_keyboard": [
                [{"text": "🏷️ Set Trade Name", "callback_data": "set_trade_name"}],
                [{"text": "⚖️ Break-even Settings", "callback_data": "set_breakeven"}],
                [{"text": "📈 Trailing Stop", "callback_data": "set_trailstop"}],
                [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
            ]
        }

    def get_trading_menu(self, chat_id: Optional[int] = None) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get trading menu keyboard - now includes trading pair, leverage, amount as first steps"""
        config = None
        if chat_id is not None:
            config = self.multi_trade_manager.get_selected_trade(chat_id)
        
        # Base menu with workflow-oriented layout
        keyboard = [
            [{"text": "💱 Select Trading Pair", "callback_data": "select_pair"}],
            [{"text": "📈 Long Position", "callback_data": "set_side_long"}, 
             {"text": "📉 Short Position", "callback_data": "set_side_short"}],
            [{"text": "📊 Set Leverage", "callback_data": "set_leverage"}],
            [{"text": "💰 Set Amount", "callback_data": "set_amount"}],
            [{"text": "🎯 Set Entry Price", "callback_data": "set_entry"}],
            [{"text": "🎯 Set Take Profits", "callback_data": "set_takeprofit"}],
            [{"text": "🛑 Set Stop Loss", "callback_data": "set_stoploss"}],
        ]
        
        # Add start trade button if configuration looks complete
        if config and config.symbol and config.side and config.amount:
            keyboard.append([{"text": "🚀 Place Trade", "callback_data": "place_trade"}])
        
        keyboard.append([{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}])
        
        return {"inline_keyboard": keyboard}

    def get_advanced_menu(self) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get advanced settings menu keyboard"""
        return {
            "inline_keyboard": [
                [{"text": "🔄 Toggle Dry Run", "callback_data": "toggle_dryrun"}],
                [{"text": "🧪 Testnet Mode", "callback_data": "toggle_testnet"}],
                [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
            ]
        }

    def get_pairs_menu(self) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get trading pairs selection menu"""
        pairs = [
            "BTC/USDT", "ETH/USDT", "BNB/USDT", "ADA/USDT",
            "SOL/USDT", "XRP/USDT", "DOT/USDT", "DOGE/USDT",
            "AVAX/USDT", "LINK/USDT", "MATIC/USDT", "UNI/USDT"
        ]
        
        keyboard = []
        for i in range(0, len(pairs), 2):
            row = []
            for j in range(2):
                if i + j < len(pairs):
                    pair = pairs[i + j]
                    row.append({"text": pair, "callback_data": f"pair_{pair.replace('/', '_')}"})
            keyboard.append(row)
        
        keyboard.append([{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}])
        return {"inline_keyboard": keyboard}

    def get_leverage_menu(self) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get leverage selection menu"""
        leverages = ["1x", "2x", "5x", "10x", "20x", "50x", "100x"]
        keyboard = []
        
        for i in range(0, len(leverages), 3):
            row = []
            for j in range(3):
                if i + j < len(leverages):
                    lev = leverages[i + j]
                    row.append({"text": lev, "callback_data": f"leverage_{lev[:-1]}"})
            keyboard.append(row)
        
        keyboard.append([{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}])
        return {"inline_keyboard": keyboard}

    def get_takeprofit_menu(self) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get take profit configuration menu"""
        return {
            "inline_keyboard": [
                [{"text": "🎯 Set TP1 (%)", "callback_data": "set_tp1_percent"}],
                [{"text": "🎯 Set TP2 (%)", "callback_data": "set_tp2_percent"}],
                [{"text": "🎯 Set TP3 (%)", "callback_data": "set_tp3_percent"}],
                [{"text": "💰 Set TP Sizes (%)", "callback_data": "set_tp_sizes"}],
                [{"text": "📊 View TP Summary", "callback_data": "view_tp_summary"}],
                [{"text": "🏠 Back to Trading", "callback_data": "menu_trading"}]
            ]
        }

    def get_breakeven_menu(self) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get break-even configuration menu"""
        return {
            "inline_keyboard": [
                [{"text": "After TP1", "callback_data": "breakeven_tp1"}],
                [{"text": "After TP2", "callback_data": "breakeven_tp2"}],
                [{"text": "After TP3", "callback_data": "breakeven_tp3"}],
                [{"text": "Disable", "callback_data": "breakeven_off"}],
                [{"text": "🏠 Back to Config", "callback_data": "menu_config"}]
            ]
        }
    
    def get_trailing_stop_menu(self) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get trailing stop configuration menu"""
        return {
            "inline_keyboard": [
                [{"text": "📉 Set Trail Percentage", "callback_data": "set_trail_percent"}],
                [{"text": "🎯 Set Activation Profit %", "callback_data": "set_trail_activation"}],
                [{"text": "❌ Disable Trailing Stop", "callback_data": "disable_trailing"}],
                [{"text": "📊 View Trail Settings", "callback_data": "view_trail_settings"}],
                [{"text": "🏠 Back to Config", "callback_data": "menu_config"}]
            ]
        }
    
    def get_portfolio_menu(self) -> Dict[str, List[List[Dict[str, str]]]]:
        """Get portfolio and analytics menu"""
        return {
            "inline_keyboard": [
                [{"text": "📊 Portfolio Summary", "callback_data": "portfolio_summary"}],
                [{"text": "📈 Performance Analytics", "callback_data": "performance_analytics"}],
                [{"text": "📋 Recent Trades", "callback_data": "recent_trades"}],
                [{"text": "💹 Symbol Performance", "callback_data": "symbol_performance"}],
                [{"text": "📥 Export Trades (CSV)", "callback_data": "export_trades"}],
                [{"text": "🎲 Demo Portfolio Data", "callback_data": "demo_portfolio"}],
                [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
            ]
        }

    def handle_update(self, update: Dict[str, Any]) -> None:
        """Handle incoming Telegram update"""
        try:
            if "message" in update:
                self.handle_message(update["message"])
            elif "callback_query" in update:
                self.handle_callback_query(update["callback_query"])
        except Exception as e:
            logger.error(f"Error handling update: {e}")
            logger.error(f"Update data: {update}")

    def handle_message(self, message: Dict[str, Any]) -> None:
        """Handle incoming message"""
        try:
            chat_id = message["chat"]["id"]
            text = message.get("text", "")
            
            logger.info(f"Received message from {chat_id}: {text}")
            
            if text.startswith("/"):
                self.handle_command(chat_id, text)
            else:
                self.handle_text_input(chat_id, text)
                
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def handle_command(self, chat_id: int, command: str) -> None:
        """Handle bot commands"""
        try:
            if command.startswith("/start"):
                self.send_welcome_message(chat_id)
            elif command.startswith("/menu"):
                self.send_main_menu(chat_id)
            elif command.startswith("/status"):
                self.send_status(chat_id)
            elif command.startswith("/help"):
                self.send_help(chat_id)
            else:
                self.send_message(chat_id, "Unknown command. Use /menu to see available options.")
        except Exception as e:
            logger.error(f"Error handling command {command}: {e}")

    def handle_callback_query(self, callback_query: Dict[str, Any]) -> None:
        """Handle callback query from inline keyboard"""
        try:
            chat_id = callback_query["message"]["chat"]["id"]
            message_id = callback_query["message"]["message_id"]
            callback_data = callback_query["data"]
            
            logger.info(f"Received callback from {chat_id}: {callback_data}")
            
            # Main menu handlers
            if callback_data == "main_menu":
                self.send_main_menu(chat_id, message_id)
            elif callback_data == "menu_multitrade":
                self.send_multitrade_menu(chat_id, message_id)
            elif callback_data == "menu_config":
                self.send_config_menu(chat_id, message_id)
            elif callback_data == "menu_trading":
                self.send_trading_menu(chat_id, message_id)
            elif callback_data == "menu_portfolio":
                self.send_portfolio_menu(chat_id, message_id)
            elif callback_data == "menu_advanced":
                self.send_advanced_menu(chat_id, message_id)
            elif callback_data == "status":
                self.send_status(chat_id, message_id)
            
            # Multi-trade handlers
            elif callback_data == "multitrade_list":
                self.send_trade_list(chat_id, message_id)
            elif callback_data == "multitrade_new":
                self.create_new_trade(chat_id, message_id)
            elif callback_data == "multitrade_select":
                self.send_trade_selection(chat_id, message_id)
            elif callback_data == "multitrade_start":
                self.start_selected_trade(chat_id, message_id)
            elif callback_data == "multitrade_stop_all":
                self.stop_all_trades(chat_id, message_id)
            elif callback_data == "multitrade_status":
                self.send_multitrade_status(chat_id, message_id)
            
            # Trade-specific handlers
            elif callback_data.startswith("trade_select_"):
                trade_id = callback_data.replace("trade_select_", "")
                self.select_trade(chat_id, trade_id, message_id)
            elif callback_data.startswith("trade_start_"):
                trade_id = callback_data.replace("trade_start_", "")
                self.start_trade(chat_id, trade_id)
            elif callback_data.startswith("trade_stop_"):
                trade_id = callback_data.replace("trade_stop_", "")
                self.stop_trade(chat_id, trade_id)
            elif callback_data.startswith("trade_copy_"):
                trade_id = callback_data.replace("trade_copy_", "")
                self.copy_trade(chat_id, trade_id)
            elif callback_data.startswith("trade_delete_"):
                trade_id = callback_data.replace("trade_delete_", "")
                self.delete_trade(chat_id, trade_id)
            
            # Configuration handlers
            elif callback_data == "select_pair":
                self.send_pairs_menu(chat_id, message_id)
            elif callback_data.startswith("pair_"):
                pair = callback_data.replace("pair_", "").replace("_", "/")
                self.set_trading_pair(chat_id, pair, message_id)
            elif callback_data == "set_side_long":
                self.set_position_side(chat_id, "long", message_id)
            elif callback_data == "set_side_short":
                self.set_position_side(chat_id, "short", message_id)
            elif callback_data == "set_leverage":
                self.send_leverage_menu(chat_id, message_id)
            elif callback_data.startswith("leverage_"):
                leverage = int(callback_data.replace("leverage_", ""))
                self.set_leverage(chat_id, leverage, message_id)
            
            # Portfolio handlers
            elif callback_data == "portfolio_summary":
                self.send_portfolio_summary(chat_id, message_id)
            elif callback_data == "performance_analytics":
                self.send_performance_analytics(chat_id, message_id)
            elif callback_data == "recent_trades":
                self.send_recent_trades(chat_id, message_id)
            elif callback_data == "symbol_performance":
                self.send_symbol_performance(chat_id, message_id)
            elif callback_data == "demo_portfolio":
                self.generate_demo_portfolio(chat_id, message_id)
            
            # Answer callback query to remove loading state
            self.answer_callback_query(callback_query["id"])
            
        except Exception as e:
            logger.error(f"Error handling callback query: {e}")
            logger.error(f"Callback data: {callback_query}")

    def handle_text_input(self, chat_id: int, text: str) -> None:
        """Handle text input for configuration"""
        try:
            config = self.multi_trade_manager.get_selected_trade(chat_id)
            if not config:
                self.send_message(chat_id, "No trade selected. Please create or select a trade first.")
                return
            
            awaiting = config.awaiting_input
            if not awaiting:
                self.send_message(chat_id, "No input expected. Use the menu to configure your trade.")
                return
            
            # Handle different input types
            if awaiting == "trade_name":
                self.handle_trade_name_input(chat_id, text)
            elif awaiting == "amount":
                self.handle_amount_input(chat_id, text)
            elif awaiting == "entry_price":
                self.handle_entry_price_input(chat_id, text)
            elif awaiting == "sl_price":
                self.handle_sl_price_input(chat_id, text)
            elif awaiting == "tp1_percent":
                self.handle_tp_percent_input(chat_id, text, 1)
            elif awaiting == "tp2_percent":
                self.handle_tp_percent_input(chat_id, text, 2)
            elif awaiting == "tp3_percent":
                self.handle_tp_percent_input(chat_id, text, 3)
            elif awaiting == "trail_percent":
                self.handle_trail_percent_input(chat_id, text)
            elif awaiting == "trail_activation":
                self.handle_trail_activation_input(chat_id, text)
            else:
                self.send_message(chat_id, f"Unknown input type: {awaiting}")
            
        except Exception as e:
            logger.error(f"Error handling text input: {e}")

    def send_welcome_message(self, chat_id: int) -> None:
        """Send welcome message"""
        welcome_text = """
🤖 <b>Welcome to Toobit Multi-Trade Bot!</b>

This bot helps you manage multiple trading configurations simultaneously with advanced features:

✨ <b>Key Features:</b>
• 🔄 Multiple concurrent trades
• 📊 Advanced take profit levels
• 🛑 Smart stop loss management
• 📈 Trailing stops
• ⚖️ Break-even automation
• 💼 Portfolio tracking
• 📱 Real-time notifications

🚀 <b>Getting Started:</b>
1. Create your first trade configuration
2. Set up entry/exit points
3. Configure risk management
4. Start monitoring!

Use the menu below to begin:
        """
        self.send_message(chat_id, welcome_text, self.get_main_menu())

    def send_main_menu(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send main menu"""
        text = "🏠 <b>Main Menu</b>\n\nChoose an option:"
        
        if message_id:
            self.edit_message(chat_id, message_id, text, self.get_main_menu())
        else:
            self.send_message(chat_id, text, self.get_main_menu())

    def send_multitrade_menu(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send multi-trade menu"""
        summary = self.multi_trade_manager.get_trade_summary(chat_id)
        
        text = f"""
🔄 <b>Multi-Trade Manager</b>

📊 <b>Quick Stats:</b>
• Total Trades: {summary['total_trades']}
• Active Trades: {summary['active_trades']}
• Running Bots: {summary['running_bots']}

Manage your trading configurations:
        """
        
        if message_id:
            self.edit_message(chat_id, message_id, text, self.get_multitrade_menu(chat_id))
        else:
            self.send_message(chat_id, text, self.get_multitrade_menu(chat_id))

    def send_config_menu(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send configuration menu"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        
        if not config:
            text = "⚙️ <b>Configuration</b>\n\nNo trade selected. Please create or select a trade first."
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔄 Multi-Trade Manager", "callback_data": "menu_multitrade"}],
                    [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
                ]
            }
        else:
            text = f"""
⚙️ <b>Configuration</b>

Currently editing: {config.get_display_name()}

Advanced settings for your trade:
            """
            keyboard = self.get_config_menu()
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def send_trading_menu(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send trading menu"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        
        if not config:
            text = "📊 <b>Trading</b>\n\nNo trade selected. Please create or select a trade first."
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔄 Multi-Trade Manager", "callback_data": "menu_multitrade"}],
                    [{"text": "🏠 Back to Main Menu", "callback_data": "main_menu"}]
                ]
            }
        else:
            text = f"""
📊 <b>Trading Configuration</b>

Currently editing: {config.get_display_name()}

{config.get_workflow_progress_text()}

Configure your trade parameters:
            """
            keyboard = self.get_trading_menu(chat_id)
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def send_portfolio_menu(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send portfolio menu"""
        text = "💼 <b>Portfolio & Analytics</b>\n\nView your trading performance and history:"
        
        if message_id:
            self.edit_message(chat_id, message_id, text, self.get_portfolio_menu())
        else:
            self.send_message(chat_id, text, self.get_portfolio_menu())

    def send_advanced_menu(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send advanced settings menu"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        
        dry_run_status = "✅ Enabled" if config and config.dry_run else "❌ Disabled"
        testnet_status = "✅ Enabled" if config and config.testnet else "❌ Disabled"
        
        text = f"""
🔧 <b>Advanced Settings</b>

Current settings:
• Dry Run: {dry_run_status}
• Testnet Mode: {testnet_status}

Configure advanced options:
        """
        
        if message_id:
            self.edit_message(chat_id, message_id, text, self.get_advanced_menu())
        else:
            self.send_message(chat_id, text, self.get_advanced_menu())

    def send_status(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send bot status"""
        try:
            # Get trade summary
            summary = self.multi_trade_manager.get_trade_summary(chat_id)
            
            # Get portfolio summary
            portfolio_summary = self.portfolio_tracker.get_portfolio_summary(chat_id)
            
            # Build status message
            status_text = f"""
📈 <b>Bot Status</b>

🔄 <b>Multi-Trade Summary:</b>
• Total Trades: {summary['total_trades']}
• Active Trades: {summary['active_trades']}
• Running Bots: {summary['running_bots']}

💼 <b>Portfolio Summary:</b>
• Total P&L: {portfolio_summary['total_pnl']} USDT
• Win Rate: {portfolio_summary['win_rate']}%
• Recent 7d P&L: {portfolio_summary['recent_pnl_7d']} USDT

🤖 <b>Bot Health:</b> ✅ Online
🔌 <b>Connection:</b> ✅ Connected
⏰ <b>Last Update:</b> {datetime.now().strftime('%H:%M:%S')}
            """
            
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔄 Refresh", "callback_data": "status"}],
                    [{"text": "🏠 Main Menu", "callback_data": "main_menu"}]
                ]
            }
            
            if message_id:
                self.edit_message(chat_id, message_id, status_text, keyboard)
            else:
                self.send_message(chat_id, status_text, keyboard)
                
        except Exception as e:
            logger.error(f"Error sending status: {e}")
            error_text = "❌ Error retrieving status. Please try again."
            if message_id:
                self.edit_message(chat_id, message_id, error_text)
            else:
                self.send_message(chat_id, error_text)

    def send_help(self, chat_id: int) -> None:
        """Send help message"""
        help_text = """
📚 <b>Toobit Multi-Trade Bot Help</b>

<b>🔄 Multi-Trade Management:</b>
• Create multiple trading configurations
• Switch between different trades
• Run multiple bots simultaneously
• Copy existing configurations

<b>📊 Trading Features:</b>
• Set trading pairs and position sides
• Configure leverage and position sizes
• Set multiple take profit levels
• Advanced stop loss management
• Trailing stops with activation thresholds

<b>⚙️ Configuration Options:</b>
• Entry prices (market or limit)
• Up to 3 take profit levels with custom sizes
• Stop loss with break-even automation
• Risk management settings

<b>💼 Portfolio Tracking:</b>
• Real-time P&L monitoring
• Trade history and analytics
• Performance statistics by symbol
• Export capabilities

<b>🔧 Advanced Features:</b>
• Dry run mode for testing
• Testnet support
• Real-time notifications
• Detailed trade logging

<b>Commands:</b>
/start - Start the bot
/menu - Show main menu
/status - Show bot status
/help - Show this help

Need more help? Contact support or check the documentation.
        """
        
        self.send_message(chat_id, help_text, self.get_main_menu())

    # Multi-trade specific methods
    def send_trade_list(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send trade list"""
        text = self.multi_trade_manager.get_trade_list_text(chat_id)
        keyboard = self.get_trade_list_menu(chat_id)
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def create_new_trade(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Create new trade"""
        try:
            config = self.multi_trade_manager.create_new_trade(chat_id)
            
            text = f"""
✅ <b>New Trade Created!</b>

{config.get_display_name()}

The trade has been automatically selected for editing. Start configuring your trading parameters using the menus below.

{config.get_workflow_progress_text()}
            """
            
            keyboard = self.get_trading_menu(chat_id)
            
            if message_id:
                self.edit_message(chat_id, message_id, text, keyboard)
            else:
                self.send_message(chat_id, text, keyboard)
                
        except Exception as e:
            logger.error(f"Error creating new trade: {e}")
            error_text = "❌ Error creating new trade. Please try again."
            if message_id:
                self.edit_message(chat_id, message_id, error_text)
            else:
                self.send_message(chat_id, error_text)

    def send_trade_selection(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send trade selection menu"""
        trades = self.multi_trade_manager.get_user_trades(chat_id)
        
        if not trades:
            text = "📝 <b>No trades found</b>\n\nCreate your first trade to get started!"
            keyboard = {
                "inline_keyboard": [
                    [{"text": "➕ Create New Trade", "callback_data": "multitrade_new"}],
                    [{"text": "🏠 Back to Multi-Trade", "callback_data": "menu_multitrade"}]
                ]
            }
        else:
            text = "🎯 <b>Select Trade</b>\n\nChoose a trade to edit or manage:"
            keyboard = self.get_trade_list_menu(chat_id)
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def select_trade(self, chat_id: int, trade_id: str, message_id: Optional[int] = None) -> None:
        """Select a trade for editing"""
        success = self.multi_trade_manager.select_trade(chat_id, trade_id)
        
        if success:
            config = self.multi_trade_manager.get_trade_by_id(chat_id, trade_id)
            if config:
                text = f"""
✅ <b>Trade Selected</b>

{config.get_display_name()}

{config.get_configuration_summary()}

What would you like to do with this trade?
                """
                keyboard = self.get_trade_actions_menu(trade_id)
            else:
                text = "❌ Error loading trade details."
                keyboard = self.get_multitrade_menu(chat_id)
        else:
            text = "❌ Trade not found."
            keyboard = self.get_multitrade_menu(chat_id)
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def start_selected_trade(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Start the currently selected trade"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        
        if not config:
            text = "❌ No trade selected. Please select a trade first."
            keyboard = self.get_multitrade_menu(chat_id)
        else:
            # Attempt to start the trade
            success = self.multi_trade_manager.start_trade(chat_id, config.trade_id)
            if success:
                text = f"🚀 <b>Trade Started!</b>\n\n{config.get_display_name()} is now being monitored."
            else:
                text = f"❌ <b>Failed to start trade</b>\n\nPlease check the configuration and try again."
            keyboard = self.get_multitrade_menu(chat_id)
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def start_trade(self, chat_id: int, trade_id: str) -> None:
        """Start a specific trade"""
        success = self.multi_trade_manager.start_trade(chat_id, trade_id)
        # The multi_trade_manager sends its own confirmation messages

    def stop_trade(self, chat_id: int, trade_id: str) -> None:
        """Stop a specific trade"""
        success = self.multi_trade_manager.stop_trade(chat_id, trade_id)
        # The multi_trade_manager sends its own confirmation messages

    def copy_trade(self, chat_id: int, trade_id: str) -> None:
        """Copy a trade configuration"""
        new_config = self.multi_trade_manager.copy_trade(chat_id, trade_id)
        if new_config:
            self.send_message(chat_id, f"✅ Trade copied successfully!\n\nNew trade: {new_config.get_display_name()}")
        else:
            self.send_message(chat_id, "❌ Failed to copy trade.")

    def delete_trade(self, chat_id: int, trade_id: str) -> None:
        """Delete a trade configuration"""
        success = self.multi_trade_manager.delete_trade(chat_id, trade_id)
        if success:
            self.send_message(chat_id, "✅ Trade deleted successfully.")
        else:
            self.send_message(chat_id, "❌ Failed to delete trade.")

    def stop_all_trades(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Stop all running trades"""
        stopped_count = self.multi_trade_manager.stop_all_trades(chat_id)
        # The multi_trade_manager sends its own confirmation messages

    def send_multitrade_status(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send multi-trade status"""
        text = self.multi_trade_manager.get_multitrade_status_text(chat_id)
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔄 Refresh", "callback_data": "multitrade_status"}],
                [{"text": "🏠 Back to Multi-Trade", "callback_data": "menu_multitrade"}]
            ]
        }
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    # Configuration methods
    def send_pairs_menu(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send trading pairs menu"""
        text = "💱 <b>Select Trading Pair</b>\n\nChoose a trading pair:"
        keyboard = self.get_pairs_menu()
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def set_trading_pair(self, chat_id: int, pair: str, message_id: Optional[int] = None) -> None:
        """Set trading pair"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            self.send_message(chat_id, "❌ No trade selected.")
            return
        
        config.symbol = pair
        config.workflow_step = "side"
        
        text = f"""
✅ <b>Trading Pair Set</b>

{config.get_display_name()}
💱 Pair: {pair}

Next step: Choose position direction
        """
        
        keyboard = {
            "inline_keyboard": [
                [{"text": "📈 Long Position", "callback_data": "set_side_long"}, 
                 {"text": "📉 Short Position", "callback_data": "set_side_short"}],
                [{"text": "📊 Continue Setup", "callback_data": "menu_trading"}],
                [{"text": "🏠 Main Menu", "callback_data": "main_menu"}]
            ]
        }
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def set_position_side(self, chat_id: int, side: str, message_id: Optional[int] = None) -> None:
        """Set position side (long/short)"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            self.send_message(chat_id, "❌ No trade selected.")
            return
        
        config.side = side
        config.workflow_step = "leverage"
        
        side_emoji = "📈" if side == "long" else "📉"
        
        text = f"""
✅ <b>Position Side Set</b>

{config.get_display_name()}
{side_emoji} Position: {side.upper()}

Next step: Set leverage
        """
        
        keyboard = {
            "inline_keyboard": [
                [{"text": "📊 Set Leverage", "callback_data": "set_leverage"}],
                [{"text": "📊 Continue Setup", "callback_data": "menu_trading"}],
                [{"text": "🏠 Main Menu", "callback_data": "main_menu"}]
            ]
        }
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def send_leverage_menu(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send leverage selection menu"""
        text = "📊 <b>Select Leverage</b>\n\nChoose your leverage level:"
        keyboard = self.get_leverage_menu()
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    def set_leverage(self, chat_id: int, leverage: int, message_id: Optional[int] = None) -> None:
        """Set leverage"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            self.send_message(chat_id, "❌ No trade selected.")
            return
        
        config.leverage = leverage
        config.workflow_step = "amount"
        
        text = f"""
✅ <b>Leverage Set</b>

{config.get_display_name()}
⚡ Leverage: {leverage}x

Next step: Set position amount
        """
        
        keyboard = {
            "inline_keyboard": [
                [{"text": "💰 Set Amount", "callback_data": "set_amount"}],
                [{"text": "📊 Continue Setup", "callback_data": "menu_trading"}],
                [{"text": "🏠 Main Menu", "callback_data": "main_menu"}]
            ]
        }
        
        if message_id:
            self.edit_message(chat_id, message_id, text, keyboard)
        else:
            self.send_message(chat_id, text, keyboard)

    # Portfolio methods
    def send_portfolio_summary(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send portfolio summary"""
        try:
            summary = self.portfolio_tracker.get_portfolio_summary(chat_id)
            
            text = f"""
📊 <b>Portfolio Summary</b>

💰 <b>P&L Overview:</b>
• Total P&L: {summary['total_pnl']} USDT
• Realized P&L: {summary['realized_pnl']} USDT
• Unrealized P&L: {summary['unrealized_pnl']} USDT

📈 <b>Trading Statistics:</b>
• Total Trades: {summary['total_trades']}
• Active Trades: {summary['active_trades']}
• Winning Trades: {summary['winning_trades']}
• Losing Trades: {summary['losing_trades']}
• Win Rate: {summary['win_rate']}%

💡 <b>Performance:</b>
• Average Trade P&L: {summary['avg_trade_pnl']} USDT
• Recent 7-day P&L: {summary['recent_pnl_7d']} USDT
            """
            
            keyboard = {
                "inline_keyboard": [
                    [{"text": "📈 Performance Analytics", "callback_data": "performance_analytics"}],
                    [{"text": "📋 Recent Trades", "callback_data": "recent_trades"}],
                    [{"text": "🏠 Back to Portfolio", "callback_data": "menu_portfolio"}]
                ]
            }
            
            if message_id:
                self.edit_message(chat_id, message_id, text, keyboard)
            else:
                self.send_message(chat_id, text, keyboard)
                
        except Exception as e:
            logger.error(f"Error sending portfolio summary: {e}")
            text = "❌ Error loading portfolio data."
            if message_id:
                self.edit_message(chat_id, message_id, text)
            else:
                self.send_message(chat_id, text)

    def send_performance_analytics(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send performance analytics"""
        try:
            analytics = self.portfolio_tracker.get_performance_analytics(chat_id)
            
            text = f"""
📈 <b>Performance Analytics</b>

💰 <b>Overall Performance:</b>
• Total P&L: {analytics['total_pnl']} USDT
• Win Rate: {analytics['win_rate']}%
• Average Trade: {analytics['avg_trade_pnl']} USDT

🔥 <b>Streaks:</b>
• Current Streak: {analytics['current_streak']}
• Max Win Streak: {analytics['max_win_streak']}
• Max Loss Streak: {analytics['max_loss_streak']}
            """
            
            # Add best/worst trades if available
            if analytics.get('best_trade'):
                best = analytics['best_trade']
                text += f"\n🏆 <b>Best Trade:</b>\n• {best['trade_id']} ({best['symbol']}): +{best['pnl']} USDT"
            
            if analytics.get('worst_trade'):
                worst = analytics['worst_trade']
                text += f"\n📉 <b>Worst Trade:</b>\n• {worst['trade_id']} ({worst['symbol']}): {worst['pnl']} USDT"
            
            # Add monthly performance if available
            monthly_pnl = analytics.get('monthly_pnl', {})
            if monthly_pnl:
                text += "\n\n📅 <b>Monthly Performance:</b>"
                for month, pnl in sorted(monthly_pnl.items(), reverse=True)[:3]:
                    text += f"\n• {month}: {pnl:.2f} USDT"
            
            keyboard = {
                "inline_keyboard": [
                    [{"text": "💹 Symbol Performance", "callback_data": "symbol_performance"}],
                    [{"text": "📊 Portfolio Summary", "callback_data": "portfolio_summary"}],
                    [{"text": "🏠 Back to Portfolio", "callback_data": "menu_portfolio"}]
                ]
            }
            
            if message_id:
                self.edit_message(chat_id, message_id, text, keyboard)
            else:
                self.send_message(chat_id, text, keyboard)
                
        except Exception as e:
            logger.error(f"Error sending performance analytics: {e}")
            text = "❌ Error loading analytics data."
            if message_id:
                self.edit_message(chat_id, message_id, text)
            else:
                self.send_message(chat_id, text)

    def send_recent_trades(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send recent trades"""
        try:
            recent_trades = self.portfolio_tracker.get_recent_trades(chat_id, 5)
            
            if not recent_trades:
                text = "📋 <b>Recent Trades</b>\n\nNo completed trades found."
            else:
                text = "📋 <b>Recent Trades</b>\n"
                for trade in recent_trades:
                    symbol = trade.get('symbol', 'Unknown')
                    side = trade.get('side', '').upper()
                    pnl = trade.get('final_pnl', 0)
                    pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                    
                    text += f"\n{pnl_emoji} {trade.get('trade_id', 'Unknown')} - {symbol} {side}"
                    text += f"\n   💰 P&L: {pnl:.2f} USDT"
                    
                    if 'end_time' in trade:
                        try:
                            end_date = datetime.fromisoformat(trade['end_time'])
                            text += f" | {end_date.strftime('%m/%d %H:%M')}"
                        except (ValueError, TypeError):
                            pass
                    text += "\n"
            
            keyboard = {
                "inline_keyboard": [
                    [{"text": "📈 Performance Analytics", "callback_data": "performance_analytics"}],
                    [{"text": "💹 Symbol Performance", "callback_data": "symbol_performance"}],
                    [{"text": "🏠 Back to Portfolio", "callback_data": "menu_portfolio"}]
                ]
            }
            
            if message_id:
                self.edit_message(chat_id, message_id, text, keyboard)
            else:
                self.send_message(chat_id, text, keyboard)
                
        except Exception as e:
            logger.error(f"Error sending recent trades: {e}")
            text = "❌ Error loading recent trades."
            if message_id:
                self.edit_message(chat_id, message_id, text)
            else:
                self.send_message(chat_id, text)

    def send_symbol_performance(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Send symbol performance breakdown"""
        try:
            symbol_perf = self.portfolio_tracker.get_symbol_performance(chat_id)
            
            if not symbol_perf:
                text = "💹 <b>Symbol Performance</b>\n\nNo trading data available."
            else:
                text = "💹 <b>Symbol Performance</b>\n"
                
                # Sort by total PnL
                sorted_symbols = sorted(symbol_perf.items(), key=lambda x: x[1]['total_pnl'], reverse=True)
                
                for symbol, data in sorted_symbols:
                    pnl_emoji = "🟢" if data['total_pnl'] > 0 else "🔴" if data['total_pnl'] < 0 else "⚪"
                    
                    text += f"\n{pnl_emoji} <b>{symbol}</b>"
                    text += f"\n   📊 Trades: {data['trades']} | Win Rate: {data['win_rate']}%"
                    text += f"\n   💰 Total P&L: {data['total_pnl']} USDT"
                    text += f"\n   📈 Avg P&L: {data['avg_pnl']} USDT\n"
            
            keyboard = {
                "inline_keyboard": [
                    [{"text": "📋 Recent Trades", "callback_data": "recent_trades"}],
                    [{"text": "📊 Portfolio Summary", "callback_data": "portfolio_summary"}],
                    [{"text": "🏠 Back to Portfolio", "callback_data": "menu_portfolio"}]
                ]
            }
            
            if message_id:
                self.edit_message(chat_id, message_id, text, keyboard)
            else:
                self.send_message(chat_id, text, keyboard)
                
        except Exception as e:
            logger.error(f"Error sending symbol performance: {e}")
            text = "❌ Error loading symbol performance data."
            if message_id:
                self.edit_message(chat_id, message_id, text)
            else:
                self.send_message(chat_id, text)

    def generate_demo_portfolio(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Generate demo portfolio data"""
        try:
            self.portfolio_tracker.generate_demo_data(chat_id)
            
            text = """
🎲 <b>Demo Data Generated!</b>

✅ Added 20 sample trades
📊 Generated performance history
💰 Created P&L data across multiple symbols
📈 Set up analytics data

You can now explore all portfolio features with realistic demo data.

<i>Note: This is sample data for demonstration purposes.</i>
            """
            
            keyboard = {
                "inline_keyboard": [
                    [{"text": "📊 View Portfolio Summary", "callback_data": "portfolio_summary"}],
                    [{"text": "📈 View Analytics", "callback_data": "performance_analytics"}],
                    [{"text": "🏠 Back to Portfolio", "callback_data": "menu_portfolio"}]
                ]
            }
            
            if message_id:
                self.edit_message(chat_id, message_id, text, keyboard)
            else:
                self.send_message(chat_id, text, keyboard)
                
        except Exception as e:
            logger.error(f"Error generating demo portfolio: {e}")
            text = "❌ Error generating demo data."
            if message_id:
                self.edit_message(chat_id, message_id, text)
            else:
                self.send_message(chat_id, text)

    # Input handlers
    def handle_trade_name_input(self, chat_id: int, text: str) -> None:
        """Handle trade name input"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            return
        
        config.trade_name = text.strip()
        config.awaiting_input = None
        
        self.send_message(chat_id, f"✅ Trade name set to: {text}")
        self.send_trading_menu(chat_id)

    def handle_amount_input(self, chat_id: int, text: str) -> None:
        """Handle amount input"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            return
        
        try:
            amount = float(text.strip())
            if amount <= 0:
                raise ValueError("Amount must be positive")
            
            config.amount = amount
            config.awaiting_input = None
            config.workflow_step = "takeprofit"
            
            self.send_message(chat_id, f"✅ Position amount set to: {amount}")
            self.send_trading_menu(chat_id)
            
        except ValueError:
            self.send_message(chat_id, "❌ Invalid amount. Please enter a positive number.")

    def handle_entry_price_input(self, chat_id: int, text: str) -> None:
        """Handle entry price input"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            return
        
        try:
            price = float(text.strip())
            if price <= 0:
                raise ValueError("Price must be positive")
            
            config.entry_price = price
            config.awaiting_input = None
            
            # Update TP prices if percentages are set
            if any([config.tp1_percent, config.tp2_percent, config.tp3_percent]):
                config.update_tp_prices_from_percentages()
            
            self.send_message(chat_id, f"✅ Entry price set to: {price}")
            self.send_trading_menu(chat_id)
            
        except ValueError:
            self.send_message(chat_id, "❌ Invalid price. Please enter a positive number.")

    def handle_sl_price_input(self, chat_id: int, text: str) -> None:
        """Handle stop loss price input"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            return
        
        try:
            price = float(text.strip())
            if price <= 0:
                raise ValueError("Price must be positive")
            
            config.sl_price = price
            config.awaiting_input = None
            
            self.send_message(chat_id, f"✅ Stop loss price set to: {price}")
            self.send_trading_menu(chat_id)
            
        except ValueError:
            self.send_message(chat_id, "❌ Invalid price. Please enter a positive number.")

    def handle_tp_percent_input(self, chat_id: int, text: str, tp_level: int) -> None:
        """Handle take profit percentage input"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            return
        
        try:
            percent = float(text.strip())
            if percent <= 0:
                raise ValueError("Percentage must be positive")
            
            if tp_level == 1:
                config.tp1_percent = percent
            elif tp_level == 2:
                config.tp2_percent = percent
            elif tp_level == 3:
                config.tp3_percent = percent
            
            config.awaiting_input = None
            
            # Update TP price if entry price is set
            if config.entry_price:
                config.update_tp_prices_from_percentages()
            
            self.send_message(chat_id, f"✅ TP{tp_level} percentage set to: {percent}%")
            self.send_trading_menu(chat_id)
            
        except ValueError:
            self.send_message(chat_id, "❌ Invalid percentage. Please enter a positive number.")

    def handle_trail_percent_input(self, chat_id: int, text: str) -> None:
        """Handle trailing stop percentage input"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            return
        
        try:
            percent = float(text.strip())
            if percent < 0 or percent > 50:
                raise ValueError("Percentage must be between 0 and 50")
            
            config.trail_percent = percent
            config.awaiting_input = None
            
            self.send_message(chat_id, f"✅ Trailing stop percentage set to: {percent}%")
            self.send_config_menu(chat_id)
            
        except ValueError:
            self.send_message(chat_id, "❌ Invalid percentage. Please enter a number between 0 and 50.")

    def handle_trail_activation_input(self, chat_id: int, text: str) -> None:
        """Handle trailing stop activation input"""
        config = self.multi_trade_manager.get_selected_trade(chat_id)
        if not config:
            return
        
        try:
            percent = float(text.strip())
            if percent < 0 or percent > 100:
                raise ValueError("Percentage must be between 0 and 100")
            
            config.trail_activation_percent = percent
            config.awaiting_input = None
            
            self.send_message(chat_id, f"✅ Trailing stop activation set to: {percent}%")
            self.send_config_menu(chat_id)
            
        except ValueError:
            self.send_message(chat_id, "❌ Invalid percentage. Please enter a number between 0 and 100.")

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        """Answer callback query to remove loading state"""
        url = f"{self.base_url}/answerCallbackQuery"
        data = {
            "callback_query_id": callback_query_id,
            "text": text
        }
        
        try:
            requests.post(url, json=data, timeout=5)
        except Exception as e:
            logger.error(f"Error answering callback query: {e}")

