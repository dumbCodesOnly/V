import logging
import random
from datetime import datetime
from app import app, db
from models import Trade

logger = logging.getLogger(__name__)

class TradingService:
    """Mock trading service for demonstration purposes"""
    
    def __init__(self):
        # Mock price data for common symbols
        self.mock_prices = {
            'BTCUSDT': 45000.00,
            'ETHUSDT': 3000.00,
            'ADAUSDT': 0.45,
            'DOGEUSDT': 0.08,
            'BNBUSDT': 350.00,
            'XRPUSDT': 0.60,
            'SOLUSDT': 100.00,
            'MATICUSDT': 0.85,
            'LTCUSDT': 150.00,
            'AVAXUSDT': 25.00
        }
        
        # Mock portfolio storage (in production, this would be in database)
        self.portfolios = {}
    
    async def get_price(self, symbol):
        """Get current price for a symbol"""
        try:
            # In production, this would call a real trading API
            # For now, return mock data with slight random variation
            base_price = self.mock_prices.get(symbol)
            if not base_price:
                return None
            
            # Add small random variation (+/- 2%)
            variation = random.uniform(-0.02, 0.02)
            current_price = base_price * (1 + variation)
            
            return {
                'symbol': symbol,
                'price': current_price,
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return None
    
    async def place_order(self, user_id, symbol, action, quantity):
        """Place a buy or sell order"""
        try:
            # Get current price
            price_data = await self.get_price(symbol)
            if not price_data:
                return {
                    'success': False,
                    'error': f'Unknown symbol: {symbol}'
                }
            
            current_price = price_data['price']
            
            # Validate order
            if quantity <= 0:
                return {
                    'success': False,
                    'error': 'Quantity must be greater than 0'
                }
            
            # In production, validate account balance, market hours, etc.
            
            # Create trade record
            with app.app_context():
                trade = Trade(
                    user_id=user_id,
                    symbol=symbol,
                    action=action,
                    quantity=quantity,
                    price=current_price,
                    status='executed'  # In production, this might start as 'pending'
                )
                db.session.add(trade)
                
                # Update portfolio (simplified logic)
                self._update_portfolio(user_id, symbol, action, quantity, current_price)
                
                # Update bot status
                from models import BotStatus
                status = BotStatus.query.first()
                if status:
                    status.total_trades += 1
                
                db.session.commit()
            
            return {
                'success': True,
                'price': current_price,
                'trade_id': trade.id
            }
            
        except Exception as e:
            logger.error(f"Error placing {action} order: {e}")
            
            # Log failed trade
            try:
                with app.app_context():
                    trade = Trade(
                        user_id=user_id,
                        symbol=symbol,
                        action=action,
                        quantity=quantity,
                        status='failed',
                        error_message=str(e)
                    )
                    db.session.add(trade)
                    db.session.commit()
            except:
                pass
            
            return {
                'success': False,
                'error': str(e)
            }
    
    def _update_portfolio(self, user_id, symbol, action, quantity, price):
        """Update user portfolio (simplified mock implementation)"""
        if user_id not in self.portfolios:
            self.portfolios[user_id] = {}
        
        portfolio = self.portfolios[user_id]
        
        if action == 'buy':
            if symbol in portfolio:
                portfolio[symbol] += quantity
            else:
                portfolio[symbol] = quantity
        elif action == 'sell':
            if symbol in portfolio:
                portfolio[symbol] = max(0, portfolio[symbol] - quantity)
                if portfolio[symbol] == 0:
                    del portfolio[symbol]
    
    def get_portfolio(self, user_id):
        """Get user portfolio"""
        try:
            portfolio = self.portfolios.get(user_id, {})
            result = []
            
            for symbol, quantity in portfolio.items():
                # Get current price
                import asyncio
                try:
                    price_data = asyncio.run(self.get_price(symbol))
                    current_price = price_data['price'] if price_data else 0
                except:
                    current_price = 0
                
                result.append({
                    'symbol': symbol,
                    'quantity': quantity,
                    'current_price': current_price,
                    'value': quantity * current_price
                })
            
            return result
        except Exception as e:
            logger.error(f"Error fetching portfolio: {e}")
            return []
    
    def get_recent_trades(self, user_id):
        """Get recent trades for user"""
        try:
            with app.app_context():
                trades = Trade.query.filter_by(user_id=user_id)\
                    .order_by(Trade.timestamp.desc())\
                    .limit(10).all()
                return trades
        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
            return []
