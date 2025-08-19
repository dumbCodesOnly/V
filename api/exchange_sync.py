"""
Exchange Synchronization Service
Handles background polling and webhook processing for Toobit exchange
"""

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from .toobit_client import ToobitClient
from .models import UserCredentials, TradeConfiguration, get_iran_time, utc_to_iran_time

class ExchangeSyncService:
    """Background service for synchronizing with Toobit exchange"""
    
    def __init__(self, app, db):
        self.app = app
        self.db = db
        self.running = False
        self.sync_thread = None
        self.sync_interval = 60  # Sync every 60 seconds
        self.last_sync = {}  # {user_id: timestamp}
        
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
        """Main synchronization loop"""
        while self.running:
            try:
                self._sync_all_users()
                time.sleep(self.sync_interval)
            except Exception as e:
                logging.error(f"Error in sync loop: {e}")
                time.sleep(30)  # Wait longer on error
    
    def _sync_all_users(self):
        """Synchronize all users with active positions"""
        with self.app.app_context():
            try:
                # Get all users with credentials
                users_with_creds = UserCredentials.query.filter_by(is_active=True).all()
                
                for user_creds in users_with_creds:
                    user_id = user_creds.telegram_user_id
                    
                    # Check if user has active positions
                    active_trades = TradeConfiguration.query.filter_by(
                        telegram_user_id=user_id,
                        status='active'
                    ).all()
                    
                    if active_trades:
                        self._sync_user_positions(user_creds)
                        
            except Exception as e:
                logging.error(f"Error syncing all users: {e}")
    
    def _sync_user_positions(self, user_creds: UserCredentials):
        """Synchronize positions for a specific user"""
        user_id = user_creds.telegram_user_id
        
        try:
            # Create Toobit client
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
            total_realized_pnl = 0.0
            for tp_fill in tp_fills:
                fill_price = tp_fill['price']
                fill_quantity = tp_fill['quantity']
                
                # Calculate P&L for this partial close
                if trade.side == 'long':
                    pnl = (fill_price - trade.entry_price) * fill_quantity
                else:
                    pnl = (trade.entry_price - fill_price) * fill_quantity
                
                total_realized_pnl += pnl
            
            # Update realized P&L
            current_realized_pnl = getattr(trade, 'realized_pnl', 0.0) or 0.0
            trade.realized_pnl = current_realized_pnl + total_realized_pnl
            
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
                        logging.info(f"Breakeven stop loss triggered for trade {trade.trade_id}")
            
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