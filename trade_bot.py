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
from exchange_client import ExchangeClient

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
        
        # Exchange client
        self.exchange_client = ExchangeClient(config.testnet)
    
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
            
            # Get current price from exchange or simulate
            await self.update_current_price()
            
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
    
    async def update_current_price(self) -> None:
        """Update current price from exchange or simulate"""
        if self.config.dry_run or not self.config.symbol:
            # Simulate price movement for demo/dry run
            await self.simulate_price_update()
        else:
            # Get real price from exchange
            try:
                current_price = await self.exchange_client.get_current_price(self.config.symbol)
                if current_price:
                    self.current_price = current_price
                    self.last_price_update = datetime.now()
                    
                    # Update price tracking for trailing stops
                    if self.config.side == "long":
                        if self.highest_price is None or self.current_price > self.highest_price:
                            self.highest_price = self.current_price
                    elif self.config.side == "short":
                        if self.lowest_price is None or self.current_price < self.lowest_price:
                            self.lowest_price = self.current_price
            except Exception as e:
                logger.error(f"Error getting current price: {e}")
                # Fallback to simulation on error
                await self.simulate_price_update()
    
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
        
        # For simulation, fill entry after 15 seconds
        if self.config.dry_run and self.trade_start_time and (datetime.now() - self.trade_start_time).seconds > 15:
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
                
        elif not self.config.dry_run:
            # Real trading logic - check if entry order is filled
            try:
                # This would check actual order status from exchange
                order_filled = await self.exchange_client.check_entry_order_status(
                    self.config.symbol, self.config.entry_price
                )
                
                if order_filled:
                    self.entry_filled = True
                    await self.send_trade_update_async(chat_id,
                        f"✅ Entry filled at {self.config.entry_price:.6f}")
            except Exception as e:
                logger.error(f"Error checking entry conditions: {e}")
    
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
            
            # Execute actual trade if not dry run
            if not self.config.dry_run:
                await self.exchange_client.close_position_partially(
                    self.config.symbol, close_amount, tp_price
                )
            
            await self.send_trade_update_async(chat_id,
                f"🎯 <b>TP{tp_level} Hit!</b>\n"
                f"💰 Price: {tp_price:.6f}\n"
                f"📊 Closed: {size_percent:.1f}% ({close_amount:.4f})\n"
                f"💵 Profit: +{total_profit:.2f} USDT ({profit_percent:.2f}%)\n"
                f"📈 Total Realized P&L: {self.total_realized_pnl:.2f} USDT\n"
                f"⚖️ Remaining Position: {self.remaining_position_size:.4f}")
            
            # Check if position is fully closed
            if self.remaining_position_size <= 0.001:  # Small threshold for floating point precision
                await self.handle_position_fully_closed(chat_id)
                
        except Exception as e:
            logger.error(f"Error executing TP{tp_level}: {e}")
            await self.send_trade_update_async(chat_id, f"❌ Error executing TP{tp_level}: {str(e)}")
    
    async def check_stop_loss_conditions(self, chat_id: int) -> None:
        """Check if stop loss should be triggered"""
        if not self.entry_filled or not self.current_price or not self.current_sl_price:
            return
        
        should_trigger = False
        
        if self.config.side == "long":
            should_trigger = self.current_price <= self.current_sl_price
        elif self.config.side == "short":
            should_trigger = self.current_price >= self.current_sl_price
        
        if should_trigger:
            await self.execute_stop_loss(chat_id)
    
    async def execute_stop_loss(self, chat_id: int) -> None:
        """Execute stop loss"""
        try:
            if not self.remaining_position_size or not self.current_sl_price:
                return
            
            # Calculate loss
            if self.config.entry_price:
                if self.config.side == "long":
                    loss_per_unit = self.current_sl_price - self.config.entry_price
                else:
                    loss_per_unit = self.config.entry_price - self.current_sl_price
                
                total_loss = round(loss_per_unit * self.remaining_position_size * self.config.leverage, 2)
                loss_percent = round((loss_per_unit / self.config.entry_price) * 100, 2)
                
                self.total_realized_pnl += total_loss  # This will be negative for losses
            else:
                total_loss = 0
                loss_percent = 0
            
            # Execute actual trade if not dry run
            if not self.config.dry_run:
                await self.exchange_client.close_position_fully(
                    self.config.symbol, self.current_sl_price
                )
            
            sl_type = "Trailing Stop" if self.trailing_active else "Stop Loss"
            
            await self.send_trade_update_async(chat_id,
                f"🛑 <b>{sl_type} Triggered!</b>\n"
                f"💰 Price: {self.current_sl_price:.6f}\n"
                f"📊 Closed: 100% ({self.remaining_position_size:.4f})\n"
                f"💸 Loss: {total_loss:.2f} USDT ({loss_percent:.2f}%)\n"
                f"📉 Total Realized P&L: {self.total_realized_pnl:.2f} USDT")
            
            # Mark position as fully closed
            self.remaining_position_size = 0
            await self.handle_position_fully_closed(chat_id)
            
        except Exception as e:
            logger.error(f"Error executing stop loss: {e}")
            await self.send_trade_update_async(chat_id, f"❌ Error executing stop loss: {str(e)}")
    
    async def update_trailing_stop(self, chat_id: int) -> None:
        """Update trailing stop loss price"""
        if self.config.trail_percent == 0 or not self.current_price:
            return
        
        # Check if we should activate trailing stop
        if not self.trailing_active and self.config.should_activate_trailing_stop(self.current_price):
            self.trailing_active = True
            await self.send_trade_update_async(chat_id, 
                f"📉 <b>Trailing Stop Activated!</b>\n"
                f"Current profit has reached the activation threshold of {self.config.trail_activation_percent}%")
        
        if self.trailing_active:
            new_sl_price = self.config.calculate_trailing_stop_price(
                self.current_price, self.highest_price, self.lowest_price
            )
            
            if new_sl_price and new_sl_price != self.current_sl_price:
                # Only move SL in profitable direction
                should_update = False
                
                if self.config.side == "long" and new_sl_price > self.current_sl_price:
                    should_update = True
                elif self.config.side == "short" and new_sl_price < self.current_sl_price:
                    should_update = True
                
                if should_update:
                    old_sl = self.current_sl_price
                    self.current_sl_price = new_sl_price
                    
                    # Update stop loss order if not dry run
                    if not self.config.dry_run:
                        await self.exchange_client.update_stop_loss_order(
                            self.config.symbol, new_sl_price
                        )
                    
                    await self.send_trade_update_async(chat_id,
                        f"📉 <b>Trailing Stop Updated</b>\n"
                        f"Old SL: {old_sl:.6f}\n"
                        f"New SL: {new_sl_price:.6f}\n"
                        f"Current Price: {self.current_price:.6f}")
    
    async def check_breakeven_conditions(self, chat_id: int) -> None:
        """Check if stop loss should be moved to break-even"""
        if self.sl_moved_to_breakeven or not self.config.breakeven_after or not self.config.entry_price:
            return
        
        # Check if the specified TP level has been hit
        breakeven_triggered = False
        
        if self.config.breakeven_after == "tp1" and self.tp1_filled:
            breakeven_triggered = True
        elif self.config.breakeven_after == "tp2" and self.tp2_filled:
            breakeven_triggered = True
        elif self.config.breakeven_after == "tp3" and self.tp3_filled:
            breakeven_triggered = True
        
        if breakeven_triggered and not self.trailing_active:  # Don't interfere with trailing stop
            old_sl = self.current_sl_price
            self.current_sl_price = self.config.entry_price
            self.sl_moved_to_breakeven = True
            
            # Update stop loss order if not dry run
            if not self.config.dry_run:
                await self.exchange_client.update_stop_loss_order(
                    self.config.symbol, self.config.entry_price
                )
            
            await self.send_trade_update_async(chat_id,
                f"⚖️ <b>Stop Loss Moved to Break-even!</b>\n"
                f"Trigger: {self.config.breakeven_after.upper()} hit\n"
                f"Old SL: {old_sl:.6f}\n"
                f"New SL: {self.config.entry_price:.6f} (Break-even)")
    
    async def handle_position_fully_closed(self, chat_id: int) -> None:
        """Handle when position is fully closed"""
        self.config.status = 'completed'
        self.is_active = False
        
        # Calculate final stats
        win_loss = "WIN" if self.total_realized_pnl > 0 else "LOSS"
        win_emoji = "🎉" if self.total_realized_pnl > 0 else "😞"
        
        # Calculate total duration
        if self.trade_start_time:
            duration = datetime.now() - self.trade_start_time
            hours = int(duration.total_seconds() // 3600)
            minutes = int((duration.total_seconds() % 3600) // 60)
            duration_str = f"{hours}h {minutes}m"
        else:
            duration_str = "Unknown"
        
        await self.send_trade_update_async(chat_id,
            f"{win_emoji} <b>Trade Completed - {win_loss}</b>\n\n"
            f"📋 {self.config.get_display_name()}\n"
            f"💰 Final P&L: {self.total_realized_pnl:.2f} USDT\n"
            f"⏱️ Duration: {duration_str}\n"
            f"📊 Entry: {self.config.entry_price:.6f}\n"
            f"🏁 Exit: {self.current_price:.6f}\n\n"
            f"Thanks for using the Multi-Trade Bot! 🤖")
        
        # Record in portfolio tracker
        if hasattr(self.telegram_bot, 'portfolio_tracker'):
            self.telegram_bot.portfolio_tracker.add_trade_completion(
                chat_id, self.config.trade_id, self.total_realized_pnl, win_loss == "WIN"
            )
    
    async def send_periodic_update(self, chat_id: int) -> None:
        """Send periodic status update"""
        if not self.entry_filled or not self.current_price:
            return
        
        unrealized_pnl, pnl_percent = self.calculate_unrealized_pnl()
        total_pnl = self.total_realized_pnl + unrealized_pnl
        
        # Calculate runtime
        runtime_str = "Unknown"
        if self.trade_start_time:
            runtime = datetime.now() - self.trade_start_time
            hours = int(runtime.total_seconds() // 3600)
            minutes = int((runtime.total_seconds() % 3600) // 60)
            runtime_str = f"{hours}h {minutes}m"
        
        status_msg = f"""
📊 <b>Trade Status Update</b>

📋 {self.config.get_display_name()}
💰 Current Price: {self.current_price:.6f}
📈 Entry Price: {self.config.entry_price:.6f}
⚖️ Position Size: {self.remaining_position_size:.4f}

💵 <b>P&L Summary:</b>
• Realized: {self.total_realized_pnl:.2f} USDT
• Unrealized: {unrealized_pnl:.2f} USDT ({pnl_percent:.2f}%)
• Total: {total_pnl:.2f} USDT

⏱️ Runtime: {runtime_str}
🔴 Current SL: {self.current_sl_price:.6f}
{"📉 Trailing: Active" if self.trailing_active else ""}

TPs Hit: {"✅" if self.tp1_filled else "⏳"}TP1 {"✅" if self.tp2_filled else "⏳"}TP2 {"✅" if self.tp3_filled else "⏳"}TP3
        """
        
        await self.send_trade_update_async(chat_id, status_msg.strip())
    
    async def send_trade_update_async(self, chat_id: int, message: str) -> None:
        """Send trade update message asynchronously"""
        try:
            # Since send_message is synchronous, we run it in a thread pool
            import concurrent.futures
            loop = asyncio.get_event_loop()
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                await loop.run_in_executor(
                    executor,
                    lambda: self.telegram_bot.send_message(chat_id, message)
                )
        except Exception as e:
            logger.error(f"Error sending trade update: {e}")
    
    def stop(self) -> None:
        """Stop the trading bot"""
        self.is_active = False
        logger.info(f"Trading bot stopped for trade {self.config.trade_id}")
    
    def get_status_info(self) -> Dict[str, Any]:
        """Get current status information"""
        return {
            'trade_id': self.config.trade_id,
            'is_active': self.is_active,
            'entry_filled': self.entry_filled,
            'current_price': self.current_price,
            'remaining_position_size': self.remaining_position_size,
            'total_realized_pnl': self.total_realized_pnl,
            'position_info': self.position_info,
            'tp1_filled': self.tp1_filled,
            'tp2_filled': self.tp2_filled,
            'tp3_filled': self.tp3_filled,
            'trailing_active': self.trailing_active,
            'current_sl_price': self.current_sl_price
        }
