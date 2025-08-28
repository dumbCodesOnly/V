"""
Exchange Synchronization Service
Handles background polling and webhook processing for Toobit exchange
"""

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from api.toobit_client import ToobitClient
from api.models import UserCredentials, TradeConfiguration, get_iran_time, utc_to_iran_time
from config import TimeConfig

class ExchangeSyncService:
    """Background service for synchronizing with Toobit exchange"""
    
    def __init__(self, app, db):
        self.app = app
        self.db = db
        self.running = False
        self.sync_thread = None
        
        # Import here to avoid circular import
        from config import Environment
        
        # Optimize sync interval based on environment
        if Environment.IS_RENDER:
            self.sync_interval = TimeConfig.RENDER_SYNC_INTERVAL
        else:
            self.sync_interval = TimeConfig.EXCHANGE_SYNC_INTERVAL
            
        self.last_sync = {}  # {user_id: timestamp}
        
        # Health ping boost mechanism
        self.last_health_ping = None
        self.is_render = Environment.IS_RENDER
        self.is_vercel = Environment.IS_VERCEL
        
    def start(self):
        """Start the background synchronization service"""
        if not self.running:
            self.running = True
            self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
            self.sync_thread.start()
            logging.info("Exchange synchronization service started")
    
    def stop(self):
        """Stop the background synchronization service"""
        self.running = False
        if self.sync_thread:
            self.sync_thread.join(timeout=10)
        logging.info("Exchange synchronization service stopped")
    
    def _sync_loop(self):
        """Main synchronization loop with health ping boost"""
        while self.running:
            try:
                # Check if we're in boost period before syncing
                current_interval = self._get_current_sync_interval()
                is_boost_active = ((self.is_render or not self.is_vercel) and self.last_health_ping and 
                                 (datetime.utcnow() - self.last_health_ping).total_seconds() <= TimeConfig.HEALTH_PING_BOOST_DURATION)
                
                if is_boost_active:
                    logging.info("Health ping boost: executing enhanced monitoring sync")
                
                self._sync_all_users()
                
                # Sleep for the calculated interval
                time.sleep(current_interval)
                
            except Exception as e:
                logging.error(f"Error in sync loop: {e}")
                time.sleep(TimeConfig.VERCEL_SYNC_COOLDOWN)  # Wait longer on error
    
    def _get_current_sync_interval(self):
        """Get current sync interval based on health ping boost status"""
        # Apply boost for all environments when health ping is received
        if not self.last_health_ping:
            return self.sync_interval
        
        # Check if we're within the boost period
        time_since_health_ping = (datetime.utcnow() - self.last_health_ping).total_seconds()
        
        if time_since_health_ping <= TimeConfig.HEALTH_PING_BOOST_DURATION:
            # Use faster interval during boost period
            boost_interval = TimeConfig.HEALTH_PING_BOOST_INTERVAL
            if time_since_health_ping < 30:  # First 30 seconds after ping
                logging.info(f"Health ping boost active: using {boost_interval}s interval (time since ping: {time_since_health_ping:.1f}s)")
            elif int(time_since_health_ping) % 30 == 0:  # Log every 30 seconds during boost
                logging.info(f"Health ping boost continues: {time_since_health_ping:.0f}s elapsed, using {boost_interval}s interval")
            return boost_interval
        else:
            # Return to normal interval
            return self.sync_interval
    
    def trigger_health_ping_boost(self):
        """Trigger extended monitoring after health ping"""
        # Allow boost for ALL environments (Render, Vercel, Replit)
        self.last_health_ping = datetime.utcnow()
        if self.is_render:
            env_name = "Render"
        elif self.is_vercel:
            env_name = "Vercel"
        else:
            env_name = "Development"
        logging.info(f"Health ping boost activated for {TimeConfig.HEALTH_PING_BOOST_DURATION} seconds ({env_name} environment)")
    
    def _sync_all_users(self):
        """Synchronize all users with active positions"""
        with self.app.app_context():
            try:
                # Get all users with credentials for real trading
                users_with_creds = UserCredentials.query.filter_by(is_active=True).all()
                logging.info(f"SYNC: Found {len(users_with_creds)} users with active credentials")
                
                users_synced = 0
                for user_creds in users_with_creds:
                    user_id = user_creds.telegram_user_id
                    
                    # Check if user has active positions
                    active_trades = TradeConfiguration.query.filter_by(
                        telegram_user_id=user_id,
                        status='active'
                    ).all()
                    
                    if active_trades:
                        logging.info(f"SYNC: Syncing {len(active_trades)} active trades for user {user_id}")
                        self._sync_user_positions(user_creds)
                        users_synced += 1
                    else:
                        logging.debug(f"SYNC: User {user_id} has no active trades to sync")
                
                logging.info(f"SYNC: Completed real trading sync for {users_synced} users")
                
                # CRITICAL FIX: Also monitor paper trading positions
                self._sync_paper_trading_positions()
                        
            except Exception as e:
                logging.error(f"Error syncing all users: {e}")
    
    def _sync_paper_trading_positions(self):
        """Monitor and process paper trading positions for TP/SL triggers"""
        try:
            # Import the necessary functions from app.py
            from api.app import user_trade_configs, process_paper_trading_position, get_live_market_price
            
            # Get all paper trading positions
            paper_positions_processed = 0
            
            for user_id, trades in user_trade_configs.items():
                for trade_id, config in trades.items():
                    # Debug logging for ALL active positions to diagnose the issue
                    if config.status == "active":
                        paper_mode_flag = getattr(config, 'paper_trading_mode', None)
                        has_tp = hasattr(config, 'take_profits') and config.take_profits
                        has_paper_tp = hasattr(config, 'paper_tp_levels')
                        has_paper_sl = hasattr(config, 'paper_sl_data')
                        order_id = getattr(config, 'exchange_order_id', '')
                        is_paper_order = str(order_id).startswith('paper_')
                        
                        logging.info(f"DEBUGGING Position {trade_id}: paper_mode={paper_mode_flag}, "
                                   f"has_tp={has_tp}, has_paper_tp={has_paper_tp}, has_paper_sl={has_paper_sl}, "
                                   f"order_id='{order_id}', is_paper_order={is_paper_order}, "
                                   f"symbol={config.symbol}, status={config.status}")
                    
                    # Process paper trading positions - detect and fix missing paper mode
                    # Check if this position should be in paper mode (user is in paper mode)
                    from api.app import user_paper_trading_preferences, user_trade_configs
                    
                    user_is_paper_mode = user_paper_trading_preferences.get(int(user_id), True)  # Default paper mode
                    
                    should_monitor = (
                        config.status == "active" and 
                        hasattr(config, 'take_profits') and 
                        config.take_profits and
                        (
                            getattr(config, 'paper_trading_mode', False) or  # Explicit paper flag
                            hasattr(config, 'paper_tp_levels') or            # Has paper TP data  
                            hasattr(config, 'paper_sl_data') or              # Has paper SL data
                            str(getattr(config, 'exchange_order_id', '')).startswith('paper_') or  # Paper order ID
                            user_is_paper_mode  # CRITICAL FIX: User is in paper mode, so position should be paper trading
                        )
                    )
                        
                    if should_monitor:
                        try:
                            # Ensure paper_trading_mode flag is set for monitoring
                            if not getattr(config, 'paper_trading_mode', False):
                                config.paper_trading_mode = True
                                logging.info(f"FIXED: Set paper_trading_mode=True for position {trade_id}")
                            
                            # CRITICAL FIX: Initialize missing paper trading monitoring structures
                            if not hasattr(config, 'paper_tp_levels') and config.take_profits:
                                from api.app import calculate_tp_sl_prices_and_amounts
                                import uuid
                                
                                tp_sl_data = calculate_tp_sl_prices_and_amounts(config)
                                if tp_sl_data.get('take_profits'):
                                    config.paper_tp_levels = []
                                    for i, tp_data in enumerate(tp_sl_data['take_profits']):
                                        mock_order_id = f"paper_tp_{i+1}_{uuid.uuid4().hex[:6]}"
                                        config.paper_tp_levels.append({
                                            'order_id': mock_order_id,
                                            'level': i + 1,
                                            'price': tp_data['price'],
                                            'percentage': tp_data['percentage'],
                                            'allocation': tp_data['allocation'],
                                            'triggered': False
                                        })
                                    logging.info(f"FIXED: Initialized {len(config.paper_tp_levels)} paper TP levels for position {trade_id}")
                                
                                if tp_sl_data.get('stop_loss') and config.stop_loss_percent > 0:
                                    sl_order_id = f"paper_sl_{uuid.uuid4().hex[:6]}"
                                    config.paper_sl_data = {
                                        'order_id': sl_order_id,
                                        'price': tp_sl_data['stop_loss']['price'],
                                        'percentage': config.stop_loss_percent,
                                        'triggered': False
                                    }
                                    logging.info(f"FIXED: Initialized paper SL data for position {trade_id}")
                            
                            # Get current market price for the symbol
                            current_price = get_live_market_price(config.symbol)
                            if current_price and current_price > 0:
                                config.current_price = current_price
                                
                                # Process paper trading position for TP/SL triggers
                                process_paper_trading_position(user_id, trade_id, config)
                                paper_positions_processed += 1
                        except Exception as position_error:
                            logging.error(f"Error processing paper position {trade_id}: {position_error}")
            
            if paper_positions_processed > 0:
                logging.info(f"PAPER TRADING MONITORING: Processed {paper_positions_processed} paper trading positions")
                
        except Exception as e:
            logging.error(f"Error syncing paper trading positions: {e}")
    
    def _sync_user_positions(self, user_creds: UserCredentials):
        """Synchronize positions for a specific user"""
        user_id = user_creds.telegram_user_id
        
        try:
            chat_id = int(user_id)
            
            # Import here to avoid circular imports
            from api.app import user_paper_trading_preferences
            
            # Check if user is in paper trading mode - skip live API calls if so
            manual_paper_mode = user_paper_trading_preferences.get(chat_id, True)  # Default to paper trading
            is_paper_mode = (manual_paper_mode or 
                           not user_creds or 
                           user_creds.testnet_mode or 
                           not user_creds.has_credentials())
            
            # Skip live API calls for users in paper trading mode
            if is_paper_mode:
                logging.debug(f"Skipping live sync for user {user_id} - in paper trading mode")
                return
            
            # Create Toobit client for live trading only
            client = ToobitClient(
                api_key=user_creds.get_api_key(),
                api_secret=user_creds.get_api_secret(),
                passphrase=user_creds.get_passphrase(),
                testnet=user_creds.testnet_mode
            )
            
            # Test connection first
            is_connected, message = client.test_connection()
            if not is_connected:
                logging.warning(f"User {user_id} connection failed: {message}")
                return
            
            # Get exchange positions
            exchange_positions = client.get_positions()
            exchange_orders = client.get_orders()
            
            # Get local active trades
            local_trades = TradeConfiguration.query.filter_by(
                telegram_user_id=user_id,
                status='active'
            ).all()
            
            # Sync each local trade with exchange
            for trade in local_trades:
                self._sync_individual_trade(trade, exchange_positions, exchange_orders, client)
            
            # Update last sync time
            self.last_sync[user_id] = datetime.utcnow()
            user_creds.last_used = datetime.utcnow()
            self.db.session.commit()
            
            logging.info(f"Synced positions for user {user_id}")
            
        except Exception as e:
            logging.error(f"Error syncing user {user_id}: {e}")
            try:
                self.db.session.rollback()
            except:
                pass
    
    def _sync_individual_trade(self, trade: TradeConfiguration, exchange_positions: List[Dict], 
                             exchange_orders: List[Dict], client: ToobitClient):
        """Sync individual trade with exchange data"""
        try:
            symbol = trade.symbol
            
            # Find matching exchange position
            exchange_position = next(
                (pos for pos in exchange_positions if pos.get('symbol') == symbol), 
                None
            )
            
            # Find related orders
            related_orders = [
                order for order in exchange_orders 
                if order.get('symbol') == symbol
            ]
            
            # Check if position was closed on exchange
            if not exchange_position or float(exchange_position.get('size', 0)) == 0:
                # Position closed - check for fill information
                filled_orders = [
                    order for order in related_orders 
                    if order.get('status') == 'filled'
                ]
                
                if filled_orders:
                    # Calculate final P&L from filled orders
                    final_pnl = self._calculate_final_pnl_from_orders(filled_orders, trade)
                    
                    # Update trade status
                    trade.status = 'stopped'
                    trade.final_pnl = final_pnl
                    trade.closed_at = get_iran_time().replace(tzinfo=None)
                    
                    logging.info(f"Trade {trade.trade_id} closed on exchange with P&L: {final_pnl}")
            
            else:
                # Position still active - update current data
                current_price = float(exchange_position.get('markPrice', trade.current_price))
                unrealized_pnl = float(exchange_position.get('unrealizedPnl', 0))
                
                # Update trade data
                trade.current_price = current_price
                trade.unrealized_pnl = unrealized_pnl
                
                # Check if any TP/SL orders were filled
                self._check_tp_sl_orders(trade, related_orders)
            
            self.db.session.commit()
            
        except Exception as e:
            logging.error(f"Error syncing trade {trade.trade_id}: {e}")
            try:
                self.db.session.rollback()
            except:
                pass
    
    def _calculate_final_pnl_from_orders(self, filled_orders: List[Dict], trade: TradeConfiguration) -> float:
        """Calculate final P&L from filled orders"""
        try:
            total_pnl = 0.0
            
            for order in filled_orders:
                if order.get('reduceOnly'):
                    # This is a closing order (TP/SL)
                    fill_price = float(order.get('avgPrice', 0))
                    fill_quantity = float(order.get('executedQty', 0))
                    
                    # Calculate P&L for this partial close
                    if trade.side == 'long':
                        pnl = (fill_price - trade.entry_price) * fill_quantity
                    else:
                        pnl = (trade.entry_price - fill_price) * fill_quantity
                    
                    total_pnl += pnl
            
            return total_pnl
            
        except Exception as e:
            logging.error(f"Error calculating final P&L: {e}")
            return 0.0
    
    def _check_tp_sl_orders(self, trade: TradeConfiguration, orders: List[Dict]):
        """Check if any TP/SL orders were partially filled and update TP levels"""
        try:
            tp_filled = False
            sl_filled = False
            partial_tp_fills = []
            
            for order in orders:
                if order.get('status') == 'filled' and order.get('reduceOnly'):
                    order_type = order.get('type', '').lower()
                    fill_price = float(order.get('avgPrice', 0))
                    fill_quantity = float(order.get('executedQty', 0))
                    
                    if 'limit' in order_type:
                        tp_filled = True
                        partial_tp_fills.append({
                            'price': fill_price,
                            'quantity': fill_quantity,
                            'order': order
                        })
                    elif 'stop' in order_type:
                        sl_filled = True
            
            # Process partial TP fills
            if tp_filled and partial_tp_fills:
                self._process_partial_tp_fills(trade, partial_tp_fills)
            
            # Log significant events
            if tp_filled:
                logging.info(f"Take profit order filled for trade {trade.trade_id}")
            if sl_filled:
                logging.info(f"Stop loss order filled for trade {trade.trade_id}")
                
        except Exception as e:
            logging.error(f"Error checking TP/SL orders: {e}")
    
    def _process_partial_tp_fills(self, trade: TradeConfiguration, tp_fills: List[Dict]):
        """Process partial take profit fills and update remaining TP levels"""
        try:
            if not trade.take_profits:
                return
            
            import json
            
            # Parse current take profits
            current_tps = json.loads(trade.take_profits) if trade.take_profits else []
            if not current_tps:
                return
            
            # Calculate realized P&L from this TP fill
            # TP allocation percentage determines portion of position closed at each TP level
            # The exchange provides the actual P&L amount for the position size that was closed
            total_realized_pnl = 0.0
            for tp_fill in tp_fills:
                fill_price = tp_fill['price']
                fill_quantity = tp_fill['quantity']  # Actual position size closed by exchange
                
                # Calculate P&L for this partial close
                # P&L = price_difference * actual_position_size_closed
                if trade.side == 'long':
                    pnl = (fill_price - trade.entry_price) * fill_quantity
                else:
                    pnl = (trade.entry_price - fill_price) * fill_quantity
                
                total_realized_pnl += pnl
            
            # CRITICAL FIX: Store original amounts before any TP triggers to preserve correct allocation calculations
            if not hasattr(trade, 'original_amount') or trade.original_amount is None:
                trade.original_amount = trade.amount
            if not hasattr(trade, 'original_margin') or trade.original_margin is None:
                # Calculate original margin based on original amount and leverage
                trade.original_margin = trade.original_amount / trade.leverage if trade.leverage > 0 else trade.original_amount
            
            # Update realized P&L
            current_realized_pnl = getattr(trade, 'realized_pnl', 0.0) or 0.0
            trade.realized_pnl = current_realized_pnl + total_realized_pnl
            
            # Commit realized P&L update to database immediately
            self.db.session.commit()
            logging.info(f"Updated realized P&L for trade {trade.trade_id}: ${trade.realized_pnl:.2f} (added ${total_realized_pnl:.2f})")
            logging.info(f"Preserved original allocation amounts - Original: ${trade.original_amount:.2f}, Current: ${trade.amount:.2f}")
            
            # Remove the first TP level (the one that was just triggered)
            if current_tps:
                triggered_tp = current_tps.pop(0)  # Remove the first TP
                logging.info(f"Removed triggered TP level: {triggered_tp}")
                
                # Update the trade configuration with remaining TPs
                trade.take_profits = json.dumps(current_tps)
                
                # Trigger breakeven stop loss if this was the first TP
                if hasattr(trade, 'breakeven_after') and trade.breakeven_after > 0:
                    if not getattr(trade, 'breakeven_sl_triggered', False):
                        trade.breakeven_sl_triggered = True
                        logging.info(f"Breakeven stop loss triggered for trade {trade.trade_id} - SL moved to entry price {trade.entry_price}")
            
            logging.info(f"Updated TP levels for trade {trade.trade_id}. Remaining TPs: {len(current_tps)}")
            
        except Exception as e:
            logging.error(f"Error processing partial TP fills: {e}")
    
    def force_sync_user(self, user_id: str):
        """Force immediate synchronization for a specific user"""
        with self.app.app_context():
            try:
                user_creds = UserCredentials.query.filter_by(
                    telegram_user_id=user_id,
                    is_active=True
                ).first()
                
                if user_creds:
                    self._sync_user_positions(user_creds)
                    return True
                else:
                    logging.warning(f"No active credentials found for user {user_id}")
                    return False
                    
            except Exception as e:
                logging.error(f"Error force syncing user {user_id}: {e}")
                return False
    
    def get_sync_status(self, user_id: Optional[str] = None) -> Dict:
        """Get synchronization status"""
        status = {
            'service_running': self.running,
            'sync_interval': self.sync_interval,
            'last_sync_times': {}
        }
        
        if user_id:
            status['last_sync_times'][user_id] = self.last_sync.get(user_id)
        else:
            status['last_sync_times'] = dict(self.last_sync)
        
        return status

# Global sync service instance
sync_service = None

def initialize_sync_service(app, db):
    """Initialize and start the exchange synchronization service"""
    global sync_service
    sync_service = ExchangeSyncService(app, db)
    sync_service.start()
    return sync_service

def get_sync_service():
    """Get the global sync service instance"""
    return sync_service