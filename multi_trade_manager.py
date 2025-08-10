import logging
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from datetime import datetime
import threading
import asyncio

if TYPE_CHECKING:
    from bot import TelegramBot
    from trade_bot import TradingBot

from trade_config import TradeConfig

logger = logging.getLogger(__name__)


class MultiTradeManager:
    def __init__(self, telegram_bot: 'TelegramBot') -> None:
        self.telegram_bot = telegram_bot
        self.user_trades: Dict[int, Dict[str, TradeConfig]] = {}  # chat_id -> {trade_id: config}
        self.active_bots: Dict[str, 'TradingBot'] = {}  # trade_id -> bot
        self.user_selections: Dict[int, str] = {}  # chat_id -> selected_trade_id
        
    def get_user_trades(self, chat_id: int) -> Dict[str, TradeConfig]:
        """Get all trades for a user"""
        if chat_id not in self.user_trades:
            self.user_trades[chat_id] = {}
        return self.user_trades[chat_id]
    
    def get_selected_trade(self, chat_id: int) -> Optional[TradeConfig]:
        """Get currently selected trade for a user"""
        trades = self.get_user_trades(chat_id)
        selected_id = self.user_selections.get(chat_id)
        
        if selected_id and selected_id in trades:
            return trades[selected_id]
        
        # Auto-select if only one trade exists
        if len(trades) == 1:
            trade_id = list(trades.keys())[0]
            self.user_selections[chat_id] = trade_id
            return trades[trade_id]
        
        return None
    
    def get_trade_by_id(self, chat_id: int, trade_id: str) -> Optional[TradeConfig]:
        """Get a specific trade by ID for a user"""
        trades = self.get_user_trades(chat_id)
        return trades.get(trade_id)
    
    def select_trade(self, chat_id: int, trade_id: str) -> bool:
        """Select a trade for editing"""
        trades = self.get_user_trades(chat_id)
        if trade_id in trades:
            self.user_selections[chat_id] = trade_id
            logger.info(f"User {chat_id} selected trade {trade_id}")
            return True
        logger.warning(f"User {chat_id} tried to select non-existent trade {trade_id}")
        return False
    
    def create_new_trade(self, chat_id: int, trade_name: Optional[str] = None) -> TradeConfig:
        """Create a new trade configuration"""
        config = TradeConfig()
        config.trade_name = trade_name
        config.workflow_step = "pair"  # Start workflow from trading pair selection
        
        trades = self.get_user_trades(chat_id)
        trades[config.trade_id] = config
        self.user_selections[chat_id] = config.trade_id
        
        logger.info(f"Created new trade {config.trade_id} for user {chat_id}")
        return config
    
    def copy_trade(self, chat_id: int, source_trade_id: str, new_name: Optional[str] = None) -> Optional[TradeConfig]:
        """Copy an existing trade configuration"""
        trades = self.get_user_trades(chat_id)
        if source_trade_id not in trades:
            logger.warning(f"User {chat_id} tried to copy non-existent trade {source_trade_id}")
            return None
        
        source_config = trades[source_trade_id]
        new_config = source_config.copy()
        
        if new_name:
            new_config.trade_name = new_name
        elif source_config.trade_name:
            new_config.trade_name = f"{source_config.trade_name} (Copy)"
        
        trades[new_config.trade_id] = new_config
        self.user_selections[chat_id] = new_config.trade_id
        
        logger.info(f"Copied trade {source_trade_id} to {new_config.trade_id} for user {chat_id}")
        return new_config
    
    def delete_trade(self, chat_id: int, trade_id: str) -> bool:
        """Delete a trade configuration"""
        trades = self.get_user_trades(chat_id)
        if trade_id not in trades:
            logger.warning(f"User {chat_id} tried to delete non-existent trade {trade_id}")
            return False
        
        # Stop active bot if running
        if trade_id in self.active_bots:
            self.stop_trade(chat_id, trade_id)
        
        # Remove from trades
        del trades[trade_id]
        
        # Clear selection if this was selected
        if self.user_selections.get(chat_id) == trade_id:
            if trades:
                # Select first available trade
                self.user_selections[chat_id] = list(trades.keys())[0]
            else:
                # No trades left, remove selection
                if chat_id in self.user_selections:
                    del self.user_selections[chat_id]
        
        logger.info(f"Deleted trade {trade_id} for user {chat_id}")
        return True
    
    def start_trade(self, chat_id: int, trade_id: str) -> bool:
        """Start monitoring a trade"""
        from trade_bot import TradingBot  # Import here to avoid circular imports
        
        trades = self.get_user_trades(chat_id)
        if trade_id not in trades:
            logger.warning(f"User {chat_id} tried to start non-existent trade {trade_id}")
            return False
        
        config = trades[trade_id]
        
        # Validate configuration thoroughly
        errors = config.validate()
        if errors:
            error_msg = "❌ <b>Cannot start trade</b>\n\nPlease fix these issues:\n\n" + "\n".join(f"• {error}" for error in errors)
            self.telegram_bot.send_message(chat_id, error_msg)
            return False
        
        # Check if already running
        if trade_id in self.active_bots:
            self.telegram_bot.send_message(chat_id, f"⚠️ Trade {config.get_display_name()} is already running!")
            return False
        
        try:
            # Ensure TP prices are calculated from percentages if needed
            if config.entry_price and any([config.tp1_percent, config.tp2_percent, config.tp3_percent]):
                config.update_tp_prices_from_percentages()
            
            # Create and start trading bot
            bot = TradingBot(config, self.telegram_bot)
            self.active_bots[trade_id] = bot
            config.status = 'active'
            
            # Start monitoring in background using threading for non-async context
            def start_monitoring_thread() -> None:
                loop = None
                try:
                    # Create new event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(bot.start_monitoring(chat_id))
                except asyncio.CancelledError:
                    logger.info(f"Monitoring cancelled for trade {trade_id}")
                except Exception as e:
                    logger.error(f"Error in monitoring thread for trade {trade_id}: {e}")
                    bot.is_active = False
                    config.status = 'error'
                    # Remove from active bots on error
                    if trade_id in self.active_bots:
                        del self.active_bots[trade_id]
                    # Notify user of error
                    self.telegram_bot.send_message(chat_id, 
                        f"❌ Monitoring error for {config.get_display_name()}: {str(e)}")
                finally:
                    if loop and not loop.is_closed():
                        try:
                            # Cancel any remaining tasks
                            pending = asyncio.all_tasks(loop)
                            for task in pending:
                                task.cancel()
                            # Wait for tasks to complete cancellation
                            if pending:
                                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        except Exception as e:
                            logger.error(f"Error cancelling tasks: {e}")
                        finally:
                            loop.close()
            
            monitor_thread = threading.Thread(target=start_monitoring_thread, daemon=True)
            monitor_thread.start()
            
            # Record trade start in portfolio tracker
            if hasattr(self.telegram_bot, 'portfolio_tracker'):
                self.telegram_bot.portfolio_tracker.add_trade_start(chat_id, trade_id, config.to_dict())
            
            # Send detailed confirmation
            confirmation_msg = f"""
🚀 <b>Trade Started Successfully!</b>

{config.get_configuration_summary()}

✅ <b>Validation passed</b>
🤖 <b>Trading bot activated</b>
📊 <b>Real-time monitoring enabled</b>

The bot will now monitor your trade and execute take profits, stop losses, and trailing stops automatically.
            """
            self.telegram_bot.send_message(chat_id, confirmation_msg)
            
            logger.info(f"Started trade {trade_id} for user {chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start trade {trade_id}: {e}")
            self.telegram_bot.send_message(chat_id, f"❌ Failed to start trade: {str(e)}")
            # Clean up on failure
            if trade_id in self.active_bots:
                del self.active_bots[trade_id]
            config.status = 'error'
            return False
    
    def stop_trade(self, chat_id: int, trade_id: str) -> bool:
        """Stop monitoring a trade"""
        if trade_id not in self.active_bots:
            trades = self.get_user_trades(chat_id)
            if trade_id in trades:
                self.telegram_bot.send_message(chat_id, 
                    f"⚠️ Trade {trades[trade_id].get_display_name()} is not currently running")
            return False
        
        try:
            bot = self.active_bots[trade_id]
            bot.stop()
            del self.active_bots[trade_id]
            
            trades = self.get_user_trades(chat_id)
            if trade_id in trades:
                trades[trade_id].status = 'paused'
                self.telegram_bot.send_message(chat_id, 
                    f"⏹️ Stopped monitoring {trades[trade_id].get_display_name()}")
            
            logger.info(f"Stopped trade {trade_id} for user {chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop trade {trade_id}: {e}")
            self.telegram_bot.send_message(chat_id, f"❌ Error stopping trade: {str(e)}")
            return False
    
    def stop_all_trades(self, chat_id: int) -> int:
        """Stop all running trades for a user"""
        stopped_count = 0
        trades = self.get_user_trades(chat_id)
        
        # Find all active trades for this user
        user_active_trades = [trade_id for trade_id in trades.keys() if trade_id in self.active_bots]
        
        for trade_id in user_active_trades:
            if self.stop_trade(chat_id, trade_id):
                stopped_count += 1
        
        if stopped_count > 0:
            self.telegram_bot.send_message(chat_id, f"⏹️ Stopped {stopped_count} running trade(s)")
        else:
            self.telegram_bot.send_message(chat_id, "ℹ️ No active trades to stop")
        
        return stopped_count
    
    def pause_trade(self, chat_id: int, trade_id: str) -> bool:
        """Pause a running trade without stopping the bot"""
        if trade_id not in self.active_bots:
            return False
        
        trades = self.get_user_trades(chat_id)
        if trade_id in trades:
            trades[trade_id].status = 'paused'
            self.telegram_bot.send_message(chat_id, f"⏸️ Trade {trades[trade_id].get_display_name()} paused")
            return True
        return False
    
    def resume_trade(self, chat_id: int, trade_id: str) -> bool:
        """Resume a paused trade"""
        trades = self.get_user_trades(chat_id)
        if trade_id not in trades:
            return False
        
        config = trades[trade_id]
        if config.status == 'paused' and trade_id in self.active_bots:
            config.status = 'active'
            self.telegram_bot.send_message(chat_id, f"▶️ Trade {config.get_display_name()} resumed")
            return True
        return False
    
    def get_active_trades(self, chat_id: int) -> List[TradeConfig]:
        """Get all active trades for a user"""
        trades = self.get_user_trades(chat_id)
        return [config for config in trades.values() if config.status == 'active']
    
    def get_trade_summary(self, chat_id: int) -> Dict[str, Any]:
        """Get summary of all trades for a user"""
        trades = self.get_user_trades(chat_id)
        active_trades = self.get_active_trades(chat_id)
        
        # Count trades by status
        status_counts = {
            'configured': 0,
            'active': 0,
            'paused': 0,
            'completed': 0,
            'cancelled': 0
        }
        
        for config in trades.values():
            status_counts[config.status] = status_counts.get(config.status, 0) + 1
        
        # Get selected trade info
        selected_trade = self.get_selected_trade(chat_id)
        selected_trade_info = "None selected"
        if selected_trade:
            selected_trade_info = f"{selected_trade.get_display_name()} ({selected_trade.status})"
        
        # Count running bots for this user
        user_running_bots = sum(1 for trade_id in trades.keys() if trade_id in self.active_bots)
        
        return {
            'total_trades': len(trades),
            'active_trades': status_counts['active'],
            'configured_trades': status_counts['configured'],
            'paused_trades': status_counts['paused'],
            'completed_trades': status_counts['completed'],
            'cancelled_trades': status_counts['cancelled'],
            'running_bots': user_running_bots,
            'selected_trade_info': selected_trade_info,
            'trades': trades
        }
    
    def get_all_active_bots(self) -> Dict[str, 'TradingBot']:
        """Get all active trading bots"""
        return self.active_bots.copy()
    
    def cleanup_finished_trades(self, chat_id: int) -> int:
        """Clean up completed or cancelled trades"""
        trades = self.get_user_trades(chat_id)
        cleanup_count = 0
        
        # Find trades that are completed or cancelled and not running
        to_cleanup = []
        for trade_id, config in trades.items():
            if config.status in ['completed', 'cancelled'] and trade_id not in self.active_bots:
                to_cleanup.append(trade_id)
        
        # Remove them
        for trade_id in to_cleanup:
            del trades[trade_id]
            cleanup_count += 1
            
            # Clear selection if this was selected
            if self.user_selections.get(chat_id) == trade_id:
                if trades:
                    self.user_selections[chat_id] = list(trades.keys())[0]
                else:
                    if chat_id in self.user_selections:
                        del self.user_selections[chat_id]
        
        logger.info(f"Cleaned up {cleanup_count} finished trades for user {chat_id}")
        return cleanup_count
    
    def get_system_stats(self) -> Dict[str, Any]:
        """Get system-wide statistics"""
        total_users = len(self.user_trades)
        total_trades = sum(len(trades) for trades in self.user_trades.values())
        total_active_bots = len(self.active_bots)
        
        # Count trades by status
        status_counts = {
            'configured': 0,
            'active': 0,
            'paused': 0,
            'completed': 0,
            'cancelled': 0,
            'error': 0
        }
        
        for trades in self.user_trades.values():
            for config in trades.values():
                status_counts[config.status] = status_counts.get(config.status, 0) + 1
        
        return {
            'total_users': total_users,
            'total_trades': total_trades,
            'active_bots': total_active_bots,
            'status_counts': status_counts,
            'timestamp': datetime.now().isoformat()
        }
