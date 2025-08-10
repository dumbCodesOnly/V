import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import json

logger = logging.getLogger(__name__)


class PortfolioTracker:
    def __init__(self) -> None:
        # User portfolio data: chat_id -> portfolio data
        self.user_portfolios: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
            'trades': [],  # List of completed trades
            'active_trades': {},  # trade_id -> trade info
            'total_pnl': 0.0,
            'realized_pnl': 0.0,
            'unrealized_pnl': 0.0,
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'symbol_performance': defaultdict(lambda: {
                'trades': 0,
                'pnl': 0.0,
                'wins': 0,
                'losses': 0
            }),
            'daily_pnl': defaultdict(float),
            'trade_history': []  # Detailed trade events
        })
    
    def add_trade_start(self, chat_id: int, trade_id: str, trade_config: dict) -> None:
        """Record the start of a new trade"""
        portfolio = self.user_portfolios[chat_id]
        
        trade_info = {
            'trade_id': trade_id,
            'symbol': trade_config.get('symbol'),
            'side': trade_config.get('side'),
            'amount': trade_config.get('amount'),
            'leverage': trade_config.get('leverage', 1),
            'entry_price': trade_config.get('entry_price'),
            'start_time': datetime.now().isoformat(),
            'status': 'active',
            'realized_pnl': 0.0,
            'tp_levels_hit': []
        }
        
        portfolio['active_trades'][trade_id] = trade_info
        
        # Add to trade history
        self.add_trade_event(chat_id, trade_id, {
            'event_type': 'trade_start',
            'message': 'Trade started',
            'timestamp': datetime.now().isoformat(),
            'trade_config': trade_config
        })
        
        logger.info(f"Started tracking trade {trade_id} for user {chat_id}")
    
    def add_trade_update(self, chat_id: int, trade_id: str, update_data: dict) -> None:
        """Record a trade update (TP hit, SL hit, etc.)"""
        portfolio = self.user_portfolios[chat_id]
        
        if trade_id not in portfolio['active_trades']:
            logger.warning(f"Trade {trade_id} not found in active trades for user {chat_id}")
            return
        
        trade_info = portfolio['active_trades'][trade_id]
        
        # Update trade info based on event type
        event_type = update_data.get('event_type', 'update')
        pnl = update_data.get('pnl', 0.0)
        
        if 'tp' in event_type and event_type not in trade_info['tp_levels_hit']:
            trade_info['tp_levels_hit'].append(event_type)
            trade_info['realized_pnl'] += pnl
        
        # Add to trade history
        self.add_trade_event(chat_id, trade_id, {
            'event_type': event_type,
            'message': update_data.get('message', 'Trade update'),
            'timestamp': datetime.now().isoformat(),
            'price': update_data.get('price'),
            'pnl': pnl,
            'status': update_data.get('status', trade_info['status'])
        })
        
        # Update portfolio totals if PnL changed
        if pnl != 0:
            portfolio['realized_pnl'] += pnl
            portfolio['total_pnl'] += pnl
            
            # Update daily PnL
            today = datetime.now().strftime('%Y-%m-%d')
            portfolio['daily_pnl'][today] += pnl
            
            # Update symbol performance
            symbol = trade_info.get('symbol', 'UNKNOWN')
            symbol_perf = portfolio['symbol_performance'][symbol]
            symbol_perf['pnl'] += pnl
    
    def add_trade_completion(self, chat_id: int, trade_id: str, final_pnl: float, 
                           completion_reason: str = 'completed') -> None:
        """Record trade completion"""
        portfolio = self.user_portfolios[chat_id]
        
        if trade_id not in portfolio['active_trades']:
            logger.warning(f"Trade {trade_id} not found in active trades for user {chat_id}")
            return
        
        trade_info = portfolio['active_trades'][trade_id]
        trade_info['status'] = 'completed'
        trade_info['end_time'] = datetime.now().isoformat()
        trade_info['final_pnl'] = final_pnl
        trade_info['completion_reason'] = completion_reason
        
        # Move to completed trades
        portfolio['trades'].append(trade_info.copy())
        del portfolio['active_trades'][trade_id]
        
        # Update statistics
        portfolio['total_trades'] += 1
        if final_pnl > 0:
            portfolio['winning_trades'] += 1
        elif final_pnl < 0:
            portfolio['losing_trades'] += 1
        
        # Update symbol performance
        symbol = trade_info.get('symbol', 'UNKNOWN')
        symbol_perf = portfolio['symbol_performance'][symbol]
        symbol_perf['trades'] += 1
        if final_pnl > 0:
            symbol_perf['wins'] += 1
        elif final_pnl < 0:
            symbol_perf['losses'] += 1
        
        # Add completion event
        self.add_trade_event(chat_id, trade_id, {
            'event_type': 'trade_completed',
            'message': f'Trade completed: {completion_reason}',
            'timestamp': datetime.now().isoformat(),
            'final_pnl': final_pnl
        })
        
        logger.info(f"Completed trade {trade_id} for user {chat_id} with PnL: {final_pnl}")
    
    def add_trade_event(self, chat_id: int, trade_id: str, event_data: dict) -> None:
        """Add an event to trade history"""
        portfolio = self.user_portfolios[chat_id]
        
        event = {
            'trade_id': trade_id,
            'timestamp': event_data.get('timestamp', datetime.now().isoformat()),
            **event_data
        }
        
        portfolio['trade_history'].append(event)
        
        # Keep only last 1000 events to prevent memory issues
        if len(portfolio['trade_history']) > 1000:
            portfolio['trade_history'] = portfolio['trade_history'][-1000:]
    
    def get_portfolio_summary(self, chat_id: int) -> Dict[str, Any]:
        """Get portfolio summary for a user"""
        portfolio = self.user_portfolios[chat_id]
        
        # Calculate win rate
        total_completed = portfolio['winning_trades'] + portfolio['losing_trades']
        win_rate = (portfolio['winning_trades'] / total_completed * 100) if total_completed > 0 else 0
        
        # Calculate average trade PnL
        completed_trades = portfolio['trades']
        avg_pnl = 0.0
        if completed_trades:
            total_completed_pnl = sum(trade.get('final_pnl', 0) for trade in completed_trades)
            avg_pnl = total_completed_pnl / len(completed_trades)
        
        # Get recent performance (last 7 days)
        recent_pnl = 0.0
        today = datetime.now()
        for i in range(7):
            date_key = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            recent_pnl += portfolio['daily_pnl'].get(date_key, 0.0)
        
        return {
            'total_trades': portfolio['total_trades'],
            'active_trades': len(portfolio['active_trades']),
            'winning_trades': portfolio['winning_trades'],
            'losing_trades': portfolio['losing_trades'],
            'win_rate': round(win_rate, 2),
            'total_pnl': round(portfolio['total_pnl'], 2),
            'realized_pnl': round(portfolio['realized_pnl'], 2),
            'unrealized_pnl': round(portfolio['unrealized_pnl'], 2),
            'avg_trade_pnl': round(avg_pnl, 2),
            'recent_pnl_7d': round(recent_pnl, 2)
        }
    
    def get_symbol_performance(self, chat_id: int) -> Dict[str, Dict[str, Any]]:
        """Get performance breakdown by trading symbol"""
        portfolio = self.user_portfolios[chat_id]
        
        result = {}
        for symbol, perf in portfolio['symbol_performance'].items():
            if perf['trades'] > 0:
                win_rate = (perf['wins'] / perf['trades'] * 100) if perf['trades'] > 0 else 0
                avg_pnl = perf['pnl'] / perf['trades'] if perf['trades'] > 0 else 0
                
                result[symbol] = {
                    'trades': perf['trades'],
                    'wins': perf['wins'],
                    'losses': perf['losses'],
                    'win_rate': round(win_rate, 2),
                    'total_pnl': round(perf['pnl'], 2),
                    'avg_pnl': round(avg_pnl, 2)
                }
        
        return result
    
    def get_recent_trades(self, chat_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent completed trades"""
        portfolio = self.user_portfolios[chat_id]
        
        # Sort by end_time (most recent first)
        recent_trades = sorted(
            portfolio['trades'],
            key=lambda x: x.get('end_time', ''),
            reverse=True
        )
        
        return recent_trades[:limit]
    
    def get_trade_history(self, chat_id: int, trade_id: Optional[str] = None, 
                         limit: int = 50) -> List[Dict[str, Any]]:
        """Get trade history events"""
        portfolio = self.user_portfolios[chat_id]
        
        history = portfolio['trade_history']
        
        # Filter by trade_id if specified
        if trade_id:
            history = [event for event in history if event.get('trade_id') == trade_id]
        
        # Sort by timestamp (most recent first)
        history = sorted(history, key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return history[:limit]
    
    def update_unrealized_pnl(self, chat_id: int, trade_id: str, unrealized_pnl: float) -> None:
        """Update unrealized PnL for an active trade"""
        portfolio = self.user_portfolios[chat_id]
        
        if trade_id in portfolio['active_trades']:
            old_unrealized = portfolio['active_trades'][trade_id].get('unrealized_pnl', 0.0)
            portfolio['active_trades'][trade_id]['unrealized_pnl'] = unrealized_pnl
            
            # Update portfolio total unrealized PnL
            portfolio['unrealized_pnl'] = portfolio['unrealized_pnl'] - old_unrealized + unrealized_pnl
            portfolio['total_pnl'] = portfolio['realized_pnl'] + portfolio['unrealized_pnl']
    
    def export_trades_csv(self, chat_id: int) -> str:
        """Export trades to CSV format"""
        portfolio = self.user_portfolios[chat_id]
        
        csv_lines = [
            "Trade ID,Symbol,Side,Amount,Leverage,Entry Price,Final PnL,Start Time,End Time,Status,Completion Reason"
        ]
        
        for trade in portfolio['trades']:
            line = f"{trade.get('trade_id', '')},{trade.get('symbol', '')},{trade.get('side', '')}," \
                   f"{trade.get('amount', 0)},{trade.get('leverage', 1)},{trade.get('entry_price', 0)}," \
                   f"{trade.get('final_pnl', 0)},{trade.get('start_time', '')},{trade.get('end_time', '')}," \
                   f"{trade.get('status', '')},{trade.get('completion_reason', '')}"
            csv_lines.append(line)
        
        return "\n".join(csv_lines)
    
    def export_to_csv(self, chat_id: int) -> str:
        """Export trade history to CSV format (alias for export_trades_csv)"""
        return self.export_trades_csv(chat_id)

    def add_demo_data(self, chat_id: int) -> None:
        """Add demo data for testing purposes"""
        demo_trades = [
            {
                'trade_id': 'demo_1',
                'symbol': 'BTCUSDT',
                'side': 'long',
                'amount': 0.1,
                'leverage': 10,
                'entry_price': 45000.0,
                'final_pnl': 150.0,
                'status': 'completed',
                'completion_reason': 'tp_hit',
                'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'end_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            {
                'trade_id': 'demo_2',
                'symbol': 'ETHUSDT',
                'side': 'short',
                'amount': 1.0,
                'leverage': 5,
                'entry_price': 3200.0,
                'final_pnl': 500.0,
                'status': 'completed',
                'completion_reason': 'tp_hit',
                'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'end_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        ]
        
        for trade in demo_trades:
            # Add the trade as completed to the portfolio
            portfolio = self.user_portfolios[chat_id]
            portfolio['trades'].append(trade)
            portfolio['total_trades'] += 1
            
            # Update totals based on PnL
            pnl = trade['final_pnl']
            if pnl >= 0:
                portfolio['winning_trades'] += 1
            else:
                portfolio['losing_trades'] += 1
            
            portfolio['realized_pnl'] += pnl
            portfolio['total_pnl'] += pnl
            
            # Update symbol performance
            symbol = trade['symbol']
            symbol_perf = portfolio['symbol_performance'][symbol]
            symbol_perf['trades'] += 1
            symbol_perf['pnl'] += pnl
            if pnl >= 0:
                symbol_perf['wins'] += 1
            else:
                symbol_perf['losses'] += 1
    
    def generate_demo_data(self, chat_id: int) -> None:
        """Generate demo portfolio data for testing"""
        import random
        from datetime import timedelta
        
        symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT']
        sides = ['long', 'short']
        
        # Generate 20 demo trades
        for i in range(20):
            trade_id = f"demo_{i:03d}"
            symbol = random.choice(symbols)
            side = random.choice(sides)
            amount = round(random.uniform(0.1, 2.0), 4)
            leverage = random.choice([1, 2, 5, 10, 20])
            entry_price = round(random.uniform(20000, 50000) if symbol == 'BTC/USDT' else random.uniform(1000, 3000), 2)
            
            # Generate random PnL (-500 to +1000)
            final_pnl = round(random.uniform(-500, 1000), 2)
            
            # Generate random dates in the last 30 days
            start_date = datetime.now() - timedelta(days=random.randint(1, 30))
            end_date = start_date + timedelta(hours=random.randint(1, 24))
            
            trade_info = {
                'trade_id': trade_id,
                'symbol': symbol,
                'side': side,
                'amount': amount,
                'leverage': leverage,
                'entry_price': entry_price,
                'start_time': start_date.isoformat(),
                'end_time': end_date.isoformat(),
                'status': 'completed',
                'final_pnl': final_pnl,
                'completion_reason': random.choice(['tp_hit', 'sl_hit', 'manual_close']),
                'tp_levels_hit': random.sample(['tp1_hit', 'tp2_hit', 'tp3_hit'], random.randint(0, 3))
            }
            
            # Add to portfolio
            portfolio = self.user_portfolios[chat_id]
            portfolio['trades'].append(trade_info)
            portfolio['total_trades'] += 1
            portfolio['total_pnl'] += final_pnl
            portfolio['realized_pnl'] += final_pnl
            
            if final_pnl > 0:
                portfolio['winning_trades'] += 1
            elif final_pnl < 0:
                portfolio['losing_trades'] += 1
            
            # Update symbol performance
            symbol_perf = portfolio['symbol_performance'][symbol]
            symbol_perf['trades'] += 1
            symbol_perf['pnl'] += final_pnl
            if final_pnl > 0:
                symbol_perf['wins'] += 1
            else:
                symbol_perf['losses'] += 1
            
            # Update daily PnL
            date_key = end_date.strftime('%Y-%m-%d')
            portfolio['daily_pnl'][date_key] += final_pnl
        
        logger.info(f"Generated demo portfolio data for user {chat_id}")

    def get_performance_analytics(self, chat_id: int) -> Dict[str, Any]:
        """Get detailed performance analytics"""
        portfolio = self.user_portfolios[chat_id]
        summary = self.get_portfolio_summary(chat_id)
        
        # Calculate monthly performance
        monthly_pnl = defaultdict(float)
        for trade in portfolio['trades']:
            if 'end_time' in trade:
                try:
                    end_date = datetime.fromisoformat(trade['end_time'])
                    month_key = end_date.strftime('%Y-%m')
                    monthly_pnl[month_key] += trade.get('final_pnl', 0)
                except (ValueError, TypeError):
                    continue
        
        # Find best and worst trades
        trades_with_pnl = [t for t in portfolio['trades'] if 'final_pnl' in t]
        best_trade = max(trades_with_pnl, key=lambda x: x['final_pnl']) if trades_with_pnl else None
        worst_trade = min(trades_with_pnl, key=lambda x: x['final_pnl']) if trades_with_pnl else None
        
        # Calculate streak statistics
        current_streak = 0
        max_win_streak = 0
        max_loss_streak = 0
        temp_win_streak = 0
        temp_loss_streak = 0
        
        for trade in reversed(portfolio['trades']):  # Most recent first
            pnl = trade.get('final_pnl', 0)
            if pnl > 0:
                temp_win_streak += 1
                temp_loss_streak = 0
                max_win_streak = max(max_win_streak, temp_win_streak)
                if current_streak == 0:
                    current_streak = temp_win_streak
            elif pnl < 0:
                temp_loss_streak += 1
                temp_win_streak = 0
                max_loss_streak = max(max_loss_streak, temp_loss_streak)
                if current_streak == 0:
                    current_streak = -temp_loss_streak
        
        return {
            **summary,
            'monthly_pnl': dict(monthly_pnl),
            'best_trade': {
                'trade_id': best_trade['trade_id'],
                'symbol': best_trade['symbol'],
                'pnl': best_trade['final_pnl']
            } if best_trade else None,
            'worst_trade': {
                'trade_id': worst_trade['trade_id'],
                'symbol': worst_trade['symbol'],
                'pnl': worst_trade['final_pnl']
            } if worst_trade else None,
            'current_streak': current_streak,
            'max_win_streak': max_win_streak,
            'max_loss_streak': max_loss_streak
        }
