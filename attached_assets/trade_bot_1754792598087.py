import os
import logging
import time
import threading
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING
import random

if TYPE_CHECKING:
    from bot import TelegramBot

from trade_config import TradeConfig

logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(self, config: TradeConfig, telegram_bot: 'TelegramBot') -> None:
        self.config = config
        self.telegram_bot = telegram_bot
        self.is_active = True
        self.chat_id: Optional[int] = None
        
        # Trading state
        self.entry_filled = False
        self.tp1_filled = False
        self.tp2_filled = False
        self.tp3_filled = False
        self.sl_moved_to_breakeven = False
        self.trailing_active = False
        self.trailing_stop_triggered = False
        
        # Price tracking for trailing stop
        self.highest_price: Optional[float] = None  # For long positions
        self.lowest_price: Optional[float] = None   # For short positions
        self.current_sl_price = config.sl_price
        self.current_price: Optional[float] = None
        self.last_price_update: Optional[datetime] = None
        
        # Position tracking
        self.position_info = "Monitoring configuration"
        self.trade_start_time: Optional[datetime] = None
        self.remaining_position_size: Optional[float] = config.amount
        self.total_realized_pnl = 0.0
        
        # Exchange (simplified for now to avoid async issues)
        self.exchange = None
        self.setup_exchange()
    
    def setup_exchange(self) -> None:
        """Setup exchange connection (simplified)"""
        try:
            # For now, just log the setup to avoid async issues
            if self.config.testnet:
                logger.info(f"Using Toobit testnet for trade {self.config.trade_id}")
                self.position_info = "Connected to testnet"
            else:
                logger.info(f"Using Toobit mainnet for trade {self.config.trade_id}")
                self.position_info = "Connected to mainnet"
        except Exception as e:
            logger.error(f"Error setting up exchange for trade {self.config.trade_id}: {e}")
            self.position_info = f"Setup error: {str(e)}"
    
    async def start_monitoring(self, chat_id: int) -> None:
        """Start monitoring trade execution"""
        self.is_active = True
        self.chat_id = chat_id
        self.trade_start_time = datetime.now()
        
        try:
            # Send initial status
            await self.send_trade_update_async(chat_id, 
                f"🤖 Trade monitoring started for {self.config.get_display_name()}")
            
            # Initialize price tracking
            if self.config.entry_price:
                self.current_price = self.config.entry_price
                if self.config.side == "long":
                    self.highest_price = self.config.entry_price
                elif self.config.side == "short":
                    self.lowest_price = self.config.entry_price
            
            # Initialize position size
            if self.config.amount:
                self.remaining_position_size = self.config.amount
            
            # Main monitoring loop
            monitor_cycle = 0
            while self.is_active and self.config.status == 'active':
                try:
                    await self.monitor_trade_execution(chat_id)
                    monitor_cycle += 1
                    
                    # Send periodic updates every 12 cycles (1 minute if 5s interval)
                    if monitor_cycle % 12 == 0:
                        await self.send_periodic_update(chat_id)
                    
                    await asyncio.sleep(5)  # Check every 5 seconds
                    
                except asyncio.CancelledError:
                    logger.info(f"Monitoring cancelled for trade {self.config.trade_id}")
                    break
                except Exception as e:
                    logger.error(f"Error in monitoring iteration for trade {self.config.trade_id}: {e}")
                    await asyncio.sleep(10)  # Wait longer on error
                
        except Exception as e:
            logger.error(f"Error in monitoring loop for trade {self.config.trade_id}: {e}")
            await self.send_trade_update_async(chat_id, f"❌ Monitoring error: {str(e)}")
            self.is_active = False
        finally:
            if self.config.status == 'active':
                self.config.status = 'paused'
            await self.send_trade_update_async(chat_id, f"⏹️ Monitoring stopped for {self.config.get_display_name()}")
    
    async def monitor_trade_execution(self, chat_id: int) -> None:
        """Main trade monitoring logic"""
        try:
            # Update runtime info
            runtime_str = "Unknown"
            if self.trade_start_time:
                runtime = datetime.now() - self.trade_start_time
                hours = int(runtime.total_seconds() // 3600)
                minutes = int((runtime.total_seconds() % 3600) // 60)
                runtime_str = f"{hours}h {minutes}m"
            
            # Simulate price updates (in real implementation, get from exchange)
            await self.simulate_price_update()
            
            # Check entry conditions
            if not self.entry_filled:
                await self.check_entry_conditions(chat_id)
                return  # Don't check exit conditions until entry is filled
            
            # Update position info with current status
            profit_info = ""
            if self.current_price and self.config.entry_price:
                unrealized_pnl, pnl_percent = self.calculate_unrealized_pnl()
                total_pnl = self.total_realized_pnl + unrealized_pnl
                profit_info = f" | P&L: {total_pnl:.2f} USDT ({pnl_percent:.2f}%)"
            
            self.position_info = f"Active - Runtime: {runtime_str}{profit_info} | Price: {self.current_price}"
            
            # Check take profit levels
            await self.check_take_profit_levels(chat_id)
            
            # Check stop loss conditions (including trailing stop)
            await self.check_stop_loss_conditions(chat_id)
            
            # Update trailing stop
            await self.update_trailing_stop(chat_id)
            
            # Check breakeven conditions
            await self.check_breakeven_conditions(chat_id)
            
        except Exception as e:
            logger.error(f"Error in monitor trade execution for {self.config.trade_id}: {e}")
    
    def calculate_unrealized_pnl(self) -> Tuple[float, float]:
        """Calculate current unrealized P&L and percentage"""
        if not self.current_price or not self.config.entry_price or not self.remaining_position_size:
            return 0.0, 0.0
        
        if self.config.side == "long":
            pnl_per_unit = self.current_price - self.config.entry_price
        else:
            pnl_per_unit = self.config.entry_price - self.current_price
        
        unrealized_pnl = pnl_per_unit * self.remaining_position_size * self.config.leverage
        pnl_percent = (pnl_per_unit / self.config.entry_price) * 100
        
        return unrealized_pnl, pnl_percent
    
    async def simulate_price_update(self) -> None:
        """Simulate price movement for demo purposes"""
        if not self.config.entry_price:
            return
        
        # Simple price simulation - in real implementation, get from exchange API
        if self.current_price is None:
            self.current_price = self.config.entry_price
        
        # Simulate small price movements (±0.1% to ±0.5%)
        change_percent = random.uniform(-0.5, 0.5)
        price_change = self.current_price * (change_percent / 100)
        self.current_price = round(self.current_price + price_change, 8)
        self.last_price_update = datetime.now()
        
        # Update price tracking for trailing stops
        if self.config.side == "long":
            if self.highest_price is None or self.current_price > self.highest_price:
                self.highest_price = self.current_price
        elif self.config.side == "short":
            if self.lowest_price is None or self.current_price < self.lowest_price:
                self.lowest_price = self.current_price
    
    async def check_entry_conditions(self, chat_id: int) -> None:
        """Check if entry conditions are met"""
        if self.entry_filled:
            return
        
        # Simulate entry after 15 seconds for demo
        if self.trade_start_time and (datetime.now() - self.trade_start_time).seconds > 15:
            self.entry_filled = True
            entry_price = self.config.entry_price or self.current_price
            
            # Update config with actual entry price if market order
            if not self.config.entry_price:
                self.config.entry_price = self.current_price
                self.config.update_tp_prices_from_percentages()  # Recalculate TP prices
            
            await self.send_trade_update_async(chat_id, 
                f"✅ Entry filled at {entry_price:.6f}\n"
                f"💰 Position size: {self.remaining_position_size:.4f}\n"
                f"⚡ Leverage: {self.config.leverage}x")
            
            # Initialize price tracking properly
            if self.config.side == "long":
                self.highest_price = entry_price
            else:
                self.lowest_price = entry_price
    
    async def check_take_profit_levels(self, chat_id: int) -> None:
        """Check if any take profit levels should be triggered"""
        if not self.entry_filled or not self.current_price:
            return
        
        # Check TP1
        if not self.tp1_filled and self.config.tp1_price:
            if self.should_trigger_tp(self.config.tp1_price):
                await self.execute_take_profit(chat_id, 1, self.config.tp1_price, self.config.tp1_size_percent)
        
        # Check TP2
        if not self.tp2_filled and self.config.tp2_price:
            if self.should_trigger_tp(self.config.tp2_price):
                await self.execute_take_profit(chat_id, 2, self.config.tp2_price, self.config.tp2_size_percent)
        
        # Check TP3
        if not self.tp3_filled and self.config.tp3_price:
            if self.should_trigger_tp(self.config.tp3_price):
                await self.execute_take_profit(chat_id, 3, self.config.tp3_price, self.config.tp3_size_percent)
    
    def should_trigger_tp(self, tp_price: float) -> bool:
        """Check if current price should trigger a take profit level"""
        if not self.current_price:
            return False
        
        if self.config.side == "long":
            return self.current_price >= tp_price
        elif self.config.side == "short":
            return self.current_price <= tp_price
        
        return False
    
    async def execute_take_profit(self, chat_id: int, tp_level: int, tp_price: float, size_percent: float) -> None:
        """Execute take profit level"""
        try:
            # Calculate position size to close
            if self.remaining_position_size is None:
                return
            close_amount = round(self.remaining_position_size * (size_percent / 100), 8)
            self.remaining_position_size = round(self.remaining_position_size - close_amount, 8)
            
            # Calculate profit
            if self.config.entry_price is None:
                return
                
            if self.config.side == "long":
                profit_per_unit = tp_price - self.config.entry_price
            else:
                profit_per_unit = self.config.entry_price - tp_price
            
            total_profit = round(profit_per_unit * close_amount * self.config.leverage, 2)
            profit_percent = round((profit_per_unit / self.config.entry_price) * 100, 2)
            
            # Add to realized P&L
            self.total_realized_pnl += total_profit
            
            # Mark TP as filled
            if tp_level == 1:
                self.tp1_filled = True
            elif tp_level == 2:
                self.tp2_filled = True
            elif tp_level == 3:
                self.tp3_filled = True
            
            await self.send_trade_update_async(chat_id,
                f"🎯 <b>TP{tp_level} Hit!</b>\n"
                f"💰 Price: {tp_price:.6f}\n"
                f"📊 Closed: {size_percent:.1f}% ({close_amount:.4f})\n"
                f"💵 Profit: +{total_profit:.2f} USDT ({profit_percent:.2f}%)\n"
                f"🔄 Remaining: {self.remaining_position_size:.4f}")
            
            # Record this TP execution in portfolio tracker
            if hasattr(self.telegram_bot, 'portfolio_tracker'):
                self.telegram_bot.portfolio_tracker.add_trade_update(chat_id, self.config.trade_id, {
                    'event_type': f'tp{tp_level}_hit',
                    'message': f'TP{tp_level} executed',
                    'price': tp_price,
                    'pnl': total_profit,
                    'status': 'active'
                })
            
            # Check if position is fully closed
            if self.remaining_position_size <= 0.0001:  # Account for floating point precision
                await self.complete_trade(chat_id, 'all_tp_hit')
                
        except Exception as e:
            logger.error(f"Error executing TP{tp_level} for trade {self.config.trade_id}: {e}")
    
    async def check_stop_loss_conditions(self, chat_id: int) -> None:
        """Check if stop loss should be triggered"""
        if not self.entry_filled or not self.current_price or self.trailing_stop_triggered:
            return
        
        # Check regular stop loss
        if self.current_sl_price and self.should_trigger_sl(self.current_sl_price):
            await self.execute_stop_loss(chat_id, self.current_sl_price, 'stop_loss')
    
    def should_trigger_sl(self, sl_price: float) -> bool:
        """Check if current price should trigger stop loss"""
        if not self.current_price:
            return False
        
        if self.config.side == "long":
            return self.current_price <= sl_price
        elif self.config.side == "short":
            return self.current_price >= sl_price
        
        return False
    
    async def execute_stop_loss(self, chat_id: int, sl_price: float, reason: str) -> None:
        """Execute stop loss"""
        try:
            if self.remaining_position_size is None or self.config.entry_price is None:
                return
                
            # Calculate loss
            if self.config.side == "long":
                loss_per_unit = sl_price - self.config.entry_price
            else:
                loss_per_unit = self.config.entry_price - sl_price
            
            total_loss = round(loss_per_unit * self.remaining_position_size * self.config.leverage, 2)
            loss_percent = round((loss_per_unit / self.config.entry_price) * 100, 2)
            
            # Add to realized P&L
            self.total_realized_pnl += total_loss
            
            reason_text = {
                'stop_loss': 'Stop Loss Hit',
                'trailing_stop': 'Trailing Stop Hit',
                'breakeven_stop': 'Breakeven Stop Hit'
            }.get(reason, 'Stop Loss Hit')
            
            await self.send_trade_update_async(chat_id,
                f"🛑 <b>{reason_text}!</b>\n"
                f"💰 Price: {sl_price:.6f}\n"
                f"📊 Closed: 100% ({self.remaining_position_size:.4f})\n"
                f"💸 Loss: {total_loss:.2f} USDT ({loss_percent:.2f}%)")
            
            # Record in portfolio tracker
            if hasattr(self.telegram_bot, 'portfolio_tracker'):
                self.telegram_bot.portfolio_tracker.add_trade_update(chat_id, self.config.trade_id, {
                    'event_type': 'sl_hit',
                    'message': reason_text,
                    'price': sl_price,
                    'pnl': total_loss,
                    'status': 'completed'
                })
            
            await self.complete_trade(chat_id, reason)
            
        except Exception as e:
            logger.error(f"Error executing stop loss for trade {self.config.trade_id}: {e}")
    
    async def update_trailing_stop(self, chat_id: int) -> None:
        """Update trailing stop based on current conditions"""
        if (not self.entry_filled or not self.current_price or 
            self.config.trail_percent == 0 or self.trailing_stop_triggered):
            return
        
        # Check if trailing stop should be activated  
        if not self.trailing_active and self.current_price is not None and self.config.should_activate_trailing_stop(self.current_price):
            self.trailing_active = True
            await self.send_trade_update_async(chat_id, 
                f"📉 Trailing stop activated at {self.config.trail_activation_percent}% profit")
        
        if self.trailing_active:
            # Calculate new trailing stop price
            if self.current_price is not None:
                new_trail_price = self.config.calculate_trailing_stop_price(
                    self.current_price, self.highest_price, self.lowest_price)
            else:
                new_trail_price = None
            
            if new_trail_price and (self.current_sl_price is None or 
                                   self.is_better_sl_price(new_trail_price, self.current_sl_price)):
                old_sl = self.current_sl_price
                self.current_sl_price = new_trail_price
                
                await self.send_trade_update_async(chat_id,
                    f"📈 Trailing stop updated: {old_sl:.6f} → {new_trail_price:.6f}")
            
            # Check if trailing stop should trigger
            if self.current_sl_price and self.should_trigger_sl(self.current_sl_price):
                self.trailing_stop_triggered = True
                await self.execute_stop_loss(chat_id, self.current_sl_price, 'trailing_stop')
    
    def is_better_sl_price(self, new_price: float, current_price: float) -> bool:
        """Check if new stop loss price is better than current one"""
        if self.config.side == "long":
            return new_price > current_price  # Higher is better for long
        else:
            return new_price < current_price  # Lower is better for short
    
    async def check_breakeven_conditions(self, chat_id: int) -> None:
        """Check if stop loss should be moved to breakeven"""
        if (not self.entry_filled or self.sl_moved_to_breakeven or 
            not self.config.breakeven_after or not self.config.entry_price):
            return
        
        should_move_to_breakeven = False
        
        if self.config.breakeven_after == "tp1" and self.tp1_filled:
            should_move_to_breakeven = True
        elif self.config.breakeven_after == "tp2" and self.tp2_filled:
            should_move_to_breakeven = True
        elif self.config.breakeven_after == "tp3" and self.tp3_filled:
            should_move_to_breakeven = True
        
        if should_move_to_breakeven:
            old_sl = self.current_sl_price
            self.current_sl_price = self.config.entry_price
            self.sl_moved_to_breakeven = True
            
            await self.send_trade_update_async(chat_id,
                f"⚖️ Stop loss moved to breakeven: {old_sl:.6f} → {self.current_sl_price:.6f}")
    
    async def complete_trade(self, chat_id: int, reason: str) -> None:
        """Complete the trade and stop monitoring"""
        try:
            self.config.status = 'completed'
            self.is_active = False
            
            # Record completion in portfolio tracker
            if hasattr(self.telegram_bot, 'portfolio_tracker'):
                self.telegram_bot.portfolio_tracker.add_trade_completion(
                    chat_id, self.config.trade_id, self.total_realized_pnl, reason)
            
            final_message = f"""
🏁 <b>Trade Completed</b>

{self.config.get_display_name()}
💰 Final P&L: {self.total_realized_pnl:.2f} USDT
📊 Reason: {reason.replace('_', ' ').title()}
⏱️ Duration: {self.get_trade_duration()}

🤖 Monitoring stopped automatically.
            """
            
            await self.send_trade_update_async(chat_id, final_message)
            
        except Exception as e:
            logger.error(f"Error completing trade {self.config.trade_id}: {e}")
    
    def get_trade_duration(self) -> str:
        """Get formatted trade duration"""
        if not self.trade_start_time:
            return "Unknown"
        
        duration = datetime.now() - self.trade_start_time
        hours = int(duration.total_seconds() // 3600)
        minutes = int((duration.total_seconds() % 3600) // 60)
        seconds = int(duration.total_seconds() % 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    
    async def send_periodic_update(self, chat_id: int) -> None:
        """Send periodic status update"""
        if not self.entry_filled:
            return
        
        try:
            unrealized_pnl, pnl_percent = self.calculate_unrealized_pnl()
            total_pnl = self.total_realized_pnl + unrealized_pnl
            
            update_message = f"""
📊 <b>Trade Update</b> - {self.config.get_display_name()}

💰 Current Price: {self.current_price:.6f}
📈 Unrealized P&L: {unrealized_pnl:.2f} USDT ({pnl_percent:.2f}%)
💵 Realized P&L: {self.total_realized_pnl:.2f} USDT
🔢 Total P&L: {total_pnl:.2f} USDT

🔄 Remaining Position: {self.remaining_position_size:.4f}
⏱️ Runtime: {self.get_trade_duration()}
            """
            
            # Add trailing stop info if active
            if self.trailing_active and self.current_sl_price:
                update_message += f"\n📉 Trailing Stop: {self.current_sl_price:.6f}"
            
            await self.send_trade_update_async(chat_id, update_message)
            
        except Exception as e:
            logger.error(f"Error sending periodic update for trade {self.config.trade_id}: {e}")
    
    async def send_trade_update_async(self, chat_id: int, message: str) -> None:
        """Send trade update message asynchronously"""
        try:
            # Use asyncio to run the sync send_message in the event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.telegram_bot.send_message, chat_id, message)
        except Exception as e:
            logger.error(f"Error sending trade update: {e}")
    
    def stop(self) -> None:
        """Stop the trading bot"""
        self.is_active = False
        logger.info(f"Trading bot stopped for trade {self.config.trade_id}")
