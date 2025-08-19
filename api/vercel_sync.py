"""
Vercel-optimized Exchange Synchronization
Handles exchange sync for serverless environment without background processes
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from .toobit_client import ToobitClient
from .models import UserCredentials, TradeConfiguration, get_iran_time

class VercelSyncService:
    """Serverless-optimized exchange synchronization for Vercel"""
    
    def __init__(self, app, db):
        self.app = app
        self.db = db
        self.last_sync_cache = {}  # {user_id: timestamp}
        self.sync_cooldown = 30  # Minimum seconds between syncs per user
        
    def should_sync_user(self, user_id: str) -> bool:
        """Check if user should be synced based on cooldown"""
        if user_id not in self.last_sync_cache:
            return True
            
        last_sync = self.last_sync_cache[user_id]
        time_since_sync = (datetime.utcnow() - last_sync).total_seconds()
        return time_since_sync >= self.sync_cooldown
    
    def sync_user_on_request(self, user_id: str, force: bool = False) -> Dict:
        """Sync user positions on-demand (triggered by API requests)"""
        try:
            # Check cooldown unless forced
            if not force and not self.should_sync_user(user_id):
                return {
                    'success': True,
                    'cached': True,
                    'message': 'Using cached data (within sync cooldown)',
                    'last_sync': self.last_sync_cache.get(user_id)
                }
            
            # Get user credentials
            user_creds = UserCredentials.query.filter_by(
                telegram_user_id=user_id,
                is_active=True
            ).first()
            
            if not user_creds or not user_creds.has_credentials():
                return {
                    'success': False,
                    'message': 'No API credentials found',
                    'requires_setup': True
                }
            
            # Check if user has active positions to sync
            active_trades = TradeConfiguration.query.filter_by(
                telegram_user_id=user_id,
                status='active'
            ).count()
            
            if active_trades == 0:
                return {
                    'success': True,
                    'message': 'No active positions to sync',
                    'sync_skipped': True
                }
            
            # Perform sync
            sync_result = self._sync_user_positions(user_creds)
            
            # Update cache
            self.last_sync_cache[user_id] = datetime.utcnow()
            
            return sync_result
            
        except Exception as e:
            logging.error(f"Error in Vercel sync for user {user_id}: {e}")
            return {
                'success': False,
                'message': str(e),
                'error': True
            }
    
    def _sync_user_positions(self, user_creds: UserCredentials) -> Dict:
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
                return {
                    'success': False,
                    'message': f'Exchange connection failed: {message}',
                    'connection_error': True
                }
            
            # Get exchange data
            exchange_positions = client.get_positions()
            exchange_orders = client.get_orders()
            
            # Get local active trades
            local_trades = TradeConfiguration.query.filter_by(
                telegram_user_id=user_id,
                status='active'
            ).all()
            
            # Sync each trade
            updated_trades = []
            closed_trades = []
            
            for trade in local_trades:
                result = self._sync_individual_trade(trade, exchange_positions, exchange_orders)
                if result['updated']:
                    updated_trades.append(trade.trade_id)
                if result['closed']:
                    closed_trades.append(trade.trade_id)
            
            # Commit changes
            self.db.session.commit()
            
            # Update user's last used timestamp
            user_creds.last_used = datetime.utcnow()
            self.db.session.commit()
            
            return {
                'success': True,
                'message': 'Synchronization completed',
                'updated_trades': updated_trades,
                'closed_trades': closed_trades,
                'total_positions': len(exchange_positions),
                'total_orders': len(exchange_orders),
                'sync_time': get_iran_time().isoformat()
            }
            
        except Exception as e:
            logging.error(f"Error syncing user {user_id}: {e}")
            try:
                self.db.session.rollback()
            except:
                pass
            return {
                'success': False,
                'message': str(e),
                'sync_error': True
            }
    
    def _sync_individual_trade(self, trade: TradeConfiguration, 
                             exchange_positions: List[Dict], 
                             exchange_orders: List[Dict]) -> Dict:
        """Sync individual trade with exchange data"""
        try:
            symbol = trade.symbol
            updated = False
            closed = False
            
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
                    if order.get('status') == 'filled' and order.get('reduceOnly')
                ]
                
                if filled_orders:
                    # Calculate final P&L from filled orders
                    final_pnl = self._calculate_final_pnl_from_orders(filled_orders, trade)
                    
                    # Update trade status
                    trade.status = 'stopped'
                    trade.final_pnl = final_pnl
                    trade.closed_at = get_iran_time().replace(tzinfo=None)
                    
                    updated = True
                    closed = True
                    
                    logging.info(f"Trade {trade.trade_id} closed on exchange with P&L: {final_pnl}")
            
            else:
                # Position still active - update current data
                current_price = float(exchange_position.get('markPrice', trade.current_price))
                unrealized_pnl = float(exchange_position.get('unrealizedPnl', 0))
                
                # Only update if values changed significantly
                if (abs(current_price - trade.current_price) > 0.01 or 
                    abs(unrealized_pnl - trade.unrealized_pnl) > 0.01):
                    
                    trade.current_price = current_price
                    trade.unrealized_pnl = unrealized_pnl
                    updated = True
                
                # Check for partial TP/SL fills
                self._check_tp_sl_orders(trade, related_orders)
            
            return {
                'updated': updated,
                'closed': closed,
                'trade_id': trade.trade_id
            }
            
        except Exception as e:
            logging.error(f"Error syncing trade {trade.trade_id}: {e}")
            return {
                'updated': False,
                'closed': False,
                'error': str(e)
            }
    
    def _calculate_final_pnl_from_orders(self, filled_orders: List[Dict], 
                                       trade: TradeConfiguration) -> float:
        """Calculate final P&L from filled orders"""
        try:
            total_pnl = 0.0
            
            for order in filled_orders:
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

    def get_sync_status(self, user_id: Optional[str] = None) -> Dict:
        """Get synchronization status for Vercel environment"""
        status = {
            'service_type': 'vercel_on_demand',
            'sync_cooldown': self.sync_cooldown,
            'last_sync_times': {}
        }
        
        if user_id:
            status['last_sync_times'][user_id] = self.last_sync_cache.get(user_id)
            status['should_sync'] = self.should_sync_user(user_id)
        else:
            status['last_sync_times'] = {
                uid: timestamp.isoformat() if timestamp else None 
                for uid, timestamp in self.last_sync_cache.items()
            }
        
        return status

# Global Vercel sync service instance
vercel_sync_service = None

def initialize_vercel_sync_service(app, db):
    """Initialize Vercel-optimized sync service"""
    global vercel_sync_service
    vercel_sync_service = VercelSyncService(app, db)
    # Only log initialization in debug mode to reduce Vercel log noise
    if app.debug or not os.environ.get("VERCEL"):
        logging.info("Vercel exchange sync service initialized")
    return vercel_sync_service

def get_vercel_sync_service():
    """Get the global Vercel sync service instance"""
    return vercel_sync_service