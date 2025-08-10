import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import json

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Individual trade record for portfolio tracking"""
    trade_id: str
    user_id: int
    symbol: str
    side: str
    amount: float
    leverage: int
    entry_price: float
    exit_price: Optional[float]
    start_time: datetime
    end_time: Optional[datetime]
    realized_pnl: float
    unrealized_pnl: float
    status: str  # active, completed, cancelled
    tp_hits: List[int]  # Which TP levels were hit
    max_profit: float
    max_loss: float
    trade_config: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        # Convert datetime objects to ISO strings
        data['start_time'] = self.start_time.isoformat() if self.start_time else None
        data['end_time'] = self.end_time.isoformat() if self.end_time else None
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TradeRecord':
        """Create from dictionary"""
        # Convert ISO strings back to datetime objects
        if data.get('start_time'):
            data['start_time'] = datetime.fromisoformat(data['start_time'])
        if data.get('end_time'):
            data['end_time'] = datetime.fromisoformat(data['end_time'])
        return cls(**data)


class PortfolioTracker:
    """Track portfolio performance, P&L, and trade history"""
    
    def __init__(self) -> None:
        self.trade_records: Dict[int, List[TradeRecord]] = {}  # user_id -> trades
        self.active_trades: Dict[str, TradeRecord] = {}  # trade_id -> record
        self.daily_pnl: Dict[int, Dict[str, float]] = {}  # user_id -> {date: pnl}
        
    def add_trade_start(self, user_id: int, trade_id: str, config: Dict[str, Any]) -> None:
        """Record the start of a new trade"""
        try:
            if user_id not in self.trade_records:
                self.trade_records[user_id] = []
            
            trade_record = TradeRecord(
                trade_id=trade_id,
                user_id=user_id,
                symbol=config.get('symbol', ''),
                side=config.get('side', ''),
                amount=config.get('amount', 0),
                leverage=config.get('leverage', 1),
                entry_price=config.get('entry_price', 0),
                exit_price=None,
                start_time=datetime.now(),
                end_time=None,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                status='active',
                tp_hits=[],
                max_profit=0.0,
                max_loss=0.0,
                trade_config=config
            )
            
            self.trade_records[user_id].append(trade_record)
            self.active_trades[trade_id] = trade_record
            
            logger.info(f"Added trade start record for {trade_id} (user {user_id})")
            
        except Exception as e:
            logger.error(f"Error adding trade start record: {e}")
    
    def update_trade_pnl(self, trade_id: str, realized_pnl: float, unrealized_pnl: float) -> None:
        """Update P&L for an active trade"""
        try:
            if trade_id in self.active_trades:
                record = self.active_trades[trade_id]
                record.realized_pnl = realized_pnl
                record.unrealized_pnl = unrealized_pnl
                
                # Track max profit/loss
                total_pnl = realized_pnl + unrealized_pnl
                if total_pnl > record.max_profit:
                    record.max_profit = total_pnl
                elif total_pnl < record.max_loss:
                    record.max_loss = total_pnl
                    
        except Exception as e:
            logger.error(f"Error updating trade P&L: {e}")
    
    def add_tp_hit(self, trade_id: str, tp_level: int, profit: float) -> None:
        """Record a take profit hit"""
        try:
            if trade_id in self.active_trades:
                record = self.active_trades[trade_id]
                if tp_level not in record.tp_hits:
                    record.tp_hits.append(tp_level)
                    record.realized_pnl += profit
                    
                    # Update daily P&L
                    self._update_daily_pnl(record.user_id, profit)
                    
        except Exception as e:
            logger.error(f"Error recording TP hit: {e}")
    
    def add_trade_completion(self, user_id: int, trade_id: str, final_pnl: float, 
                           is_win: bool, exit_price: Optional[float] = None) -> None:
        """Record trade completion"""
        try:
            if trade_id in self.active_trades:
                record = self.active_trades[trade_id]
                record.end_time = datetime.now()
                record.realized_pnl = final_pnl
                record.unrealized_pnl = 0.0
                record.status = 'completed'
                record.exit_price = exit_price
                
                # Remove from active trades
                del self.active_trades[trade_id]
                
                # Update daily P&L
                self._update_daily_pnl(user_id, final_pnl)
                
                logger.info(f"Completed trade record for {trade_id} (user {user_id}): {final_pnl:.2f} USDT")
                
        except Exception as e:
            logger.error(f"Error completing trade record: {e}")
    
    def _update_daily_pnl(self, user_id: int, pnl_change: float) -> None:
        """Update daily P&L tracking"""
        try:
            if user_id not in self.daily_pnl:
                self.daily_pnl[user_id] = {}
            
            today = datetime.now().strftime('%Y-%m-%d')
            if today not in self.daily_pnl[user_id]:
                self.daily_pnl[user_id][today] = 0.0
            
            self.daily_pnl[user_id][today] += pnl_change
            
        except Exception as e:
            logger.error(f"Error updating daily P&L: {e}")
    
    def get_portfolio_summary(self, user_id: int) -> Dict[str, Any]:
        """Get comprehensive portfolio summary for a user"""
        try:
            if user_id not in self.trade_records:
                return self._empty_portfolio_summary()
            
            trades = self.trade_records[user_id]
            completed_trades = [t for t in trades if t.status == 'completed']
            active_trades = [t for t in trades if t.status == 'active']
            
            # Calculate totals
            total_realized_pnl = sum(t.realized_pnl for t in trades)
            total_unrealized_pnl = sum(t.unrealized_pnl for t in active_trades)
            total_pnl = total_realized_pnl + total_unrealized_pnl
            
            # Calculate win rate
            winning_trades = len([t for t in completed_trades if t.realized_pnl > 0])
            total_completed = len(completed_trades)
            win_rate = (winning_trades / total_completed * 100) if total_completed > 0 else 0
            
            # Calculate average profit/loss
            avg_win = 0.0
            avg_loss = 0.0
            if winning_trades > 0:
                avg_win = sum(t.realized_pnl for t in completed_trades if t.realized_pnl > 0) / winning_trades
            
            losing_trades = total_completed - winning_trades
            if losing_trades > 0:
                avg_loss = sum(t.realized_pnl for t in completed_trades if t.realized_pnl < 0) / losing_trades
            
            # Get active positions text
            active_positions_text = "No active positions"
            if active_trades:
                active_list = []
                for trade in active_trades[:5]:  # Show up to 5 active trades
                    pnl_text = f"{trade.realized_pnl + trade.unrealized_pnl:.2f} USDT"
                    active_list.append(f"• {trade.symbol} {trade.side.upper()}: {pnl_text}")
                active_positions_text = "\n".join(active_list)
                if len(active_trades) > 5:
                    active_positions_text += f"\n... and {len(active_trades) - 5} more"
            
            return {
                'total_balance': 10000.0 + total_pnl,  # Assume starting balance of 10k USDT
                'realized_pnl': total_realized_pnl,
                'unrealized_pnl': total_unrealized_pnl,
                'total_pnl': total_pnl,
                'total_trades': len(trades),
                'active_trades': len(active_trades),
                'completed_trades': total_completed,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'win_rate': win_rate,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'active_positions_text': active_positions_text,
                'best_trade': max((t.realized_pnl for t in completed_trades), default=0),
                'worst_trade': min((t.realized_pnl for t in completed_trades), default=0),
                'total_volume': sum(t.amount * t.leverage for t in trades)
            }
            
        except Exception as e:
            logger.error(f"Error getting portfolio summary: {e}")
            return self._empty_portfolio_summary()
    
    def _empty_portfolio_summary(self) -> Dict[str, Any]:
        """Return empty portfolio summary"""
        return {
            'total_balance': 10000.0,
            'realized_pnl': 0.0,
            'unrealized_pnl': 0.0,
            'total_pnl': 0.0,
            'total_trades': 0,
            'active_trades': 0,
            'completed_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'active_positions_text': 'No active positions',
            'best_trade': 0.0,
            'worst_trade': 0.0,
            'total_volume': 0.0
        }
    
    def get_quick_summary(self, user_id: int) -> str:
        """Get quick portfolio summary text"""
        try:
            summary = self.get_portfolio_summary(user_id)
            
            if summary['total_trades'] == 0:
                return "No trading history yet"
            
            pnl_emoji = "📈" if summary['total_pnl'] >= 0 else "📉"
            
            return f"""
{pnl_emoji} Balance: {summary['total_balance']:.2f} USDT
💰 Total P&L: {summary['total_pnl']:.2f} USDT
📊 Win Rate: {summary['win_rate']:.1f}% ({summary['winning_trades']}/{summary['total_trades']})
⚡ Active: {summary['active_trades']} trades
            """.strip()
            
        except Exception as e:
            logger.error(f"Error getting quick summary: {e}")
            return "Error loading portfolio data"
    
    def get_performance_analytics(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        """Get detailed performance analytics"""
        try:
            if user_id not in self.trade_records:
                return {'error': 'No trading data available'}
            
            trades = self.trade_records[user_id]
            cutoff_date = datetime.now() - timedelta(days=days)
            recent_trades = [t for t in trades if t.start_time >= cutoff_date and t.status == 'completed']
            
            if not recent_trades:
                return {'error': f'No completed trades in the last {days} days'}
            
            # Performance metrics
            total_pnl = sum(t.realized_pnl for t in recent_trades)
            winning_trades = [t for t in recent_trades if t.realized_pnl > 0]
            losing_trades = [t for t in recent_trades if t.realized_pnl <= 0]
            
            win_rate = len(winning_trades) / len(recent_trades) * 100
            avg_win = sum(t.realized_pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
            avg_loss = sum(t.realized_pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0
            
            # Risk metrics
            profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
            max_consecutive_wins = self._calculate_max_consecutive_wins(recent_trades)
            max_consecutive_losses = self._calculate_max_consecutive_losses(recent_trades)
            
            # Trading frequency
            avg_trade_duration = self._calculate_avg_trade_duration(recent_trades)
            
            # Symbol analysis
            symbol_performance = self._analyze_symbol_performance(recent_trades)
            
            return {
                'period_days': days,
                'total_trades': len(recent_trades),
                'total_pnl': total_pnl,
                'win_rate': win_rate,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'profit_factor': profit_factor,
                'max_consecutive_wins': max_consecutive_wins,
                'max_consecutive_losses': max_consecutive_losses,
                'avg_trade_duration': avg_trade_duration,
                'best_trade': max(t.realized_pnl for t in recent_trades),
                'worst_trade': min(t.realized_pnl for t in recent_trades),
                'symbol_performance': symbol_performance,
                'daily_pnl': self._get_daily_pnl_series(user_id, days)
            }
            
        except Exception as e:
            logger.error(f"Error getting performance analytics: {e}")
            return {'error': f'Error calculating analytics: {str(e)}'}
    
    def _calculate_max_consecutive_wins(self, trades: List[TradeRecord]) -> int:
        """Calculate maximum consecutive winning trades"""
        max_wins = 0
        current_wins = 0
        
        for trade in sorted(trades, key=lambda x: x.start_time):
            if trade.realized_pnl > 0:
                current_wins += 1
                max_wins = max(max_wins, current_wins)
            else:
                current_wins = 0
        
        return max_wins
    
    def _calculate_max_consecutive_losses(self, trades: List[TradeRecord]) -> int:
        """Calculate maximum consecutive losing trades"""
        max_losses = 0
        current_losses = 0
        
        for trade in sorted(trades, key=lambda x: x.start_time):
            if trade.realized_pnl <= 0:
                current_losses += 1
                max_losses = max(max_losses, current_losses)
            else:
                current_losses = 0
        
        return max_losses
    
    def _calculate_avg_trade_duration(self, trades: List[TradeRecord]) -> str:
        """Calculate average trade duration"""
        if not trades:
            return "N/A"
        
        durations = []
        for trade in trades:
            if trade.end_time and trade.start_time:
                duration = trade.end_time - trade.start_time
                durations.append(duration.total_seconds())
        
        if not durations:
            return "N/A"
        
        avg_seconds = sum(durations) / len(durations)
        hours = int(avg_seconds // 3600)
        minutes = int((avg_seconds % 3600) // 60)
        
        return f"{hours}h {minutes}m"
    
    def _analyze_symbol_performance(self, trades: List[TradeRecord]) -> Dict[str, Dict[str, Any]]:
        """Analyze performance by trading symbol"""
        symbol_stats = {}
        
        for trade in trades:
            symbol = trade.symbol
            if symbol not in symbol_stats:
                symbol_stats[symbol] = {
                    'trades': 0,
                    'wins': 0,
                    'total_pnl': 0.0,
                    'win_rate': 0.0
                }
            
            symbol_stats[symbol]['trades'] += 1
            symbol_stats[symbol]['total_pnl'] += trade.realized_pnl
            
            if trade.realized_pnl > 0:
                symbol_stats[symbol]['wins'] += 1
        
        # Calculate win rates
        for symbol, stats in symbol_stats.items():
            stats['win_rate'] = (stats['wins'] / stats['trades']) * 100 if stats['trades'] > 0 else 0
        
        return symbol_stats
    
    def _get_daily_pnl_series(self, user_id: int, days: int) -> List[Dict[str, Any]]:
        """Get daily P&L series for charting"""
        if user_id not in self.daily_pnl:
            return []
        
        series = []
        user_daily_pnl = self.daily_pnl[user_id]
        
        # Generate series for the last N days
        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            pnl = user_daily_pnl.get(date, 0.0)
            series.append({
                'date': date,
                'pnl': pnl
            })
        
        return list(reversed(series))  # Chronological order
    
    def get_trade_history(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Get trade history with pagination"""
        try:
            if user_id not in self.trade_records:
                return []
            
            trades = self.trade_records[user_id]
            # Sort by start time, most recent first
            sorted_trades = sorted(trades, key=lambda x: x.start_time, reverse=True)
            
            # Convert to dictionaries and limit results
            history = []
            for trade in sorted_trades[:limit]:
                trade_dict = trade.to_dict()
                
                # Add formatted fields for display
                if trade.start_time:
                    trade_dict['start_time_formatted'] = trade.start_time.strftime('%Y-%m-%d %H:%M')
                if trade.end_time:
                    trade_dict['end_time_formatted'] = trade.end_time.strftime('%Y-%m-%d %H:%M')
                    duration = trade.end_time - trade.start_time
                    hours = int(duration.total_seconds() // 3600)
                    minutes = int((duration.total_seconds() % 3600) // 60)
                    trade_dict['duration_formatted'] = f"{hours}h {minutes}m"
                else:
                    trade_dict['duration_formatted'] = "Ongoing"
                
                # Add profit/loss indicators
                total_pnl = trade.realized_pnl + trade.unrealized_pnl
                trade_dict['pnl_status'] = 'profit' if total_pnl > 0 else 'loss' if total_pnl < 0 else 'neutral'
                trade_dict['pnl_emoji'] = '🟢' if total_pnl > 0 else '🔴' if total_pnl < 0 else '🟡'
                
                history.append(trade_dict)
            
            return history
            
        except Exception as e:
            logger.error(f"Error getting trade history: {e}")
            return []
    
    def export_trade_data(self, user_id: int, format: str = 'json') -> Optional[str]:
        """Export trade data in specified format"""
        try:
            if user_id not in self.trade_records:
                return None
            
            trades = self.trade_records[user_id]
            trade_data = [trade.to_dict() for trade in trades]
            
            if format.lower() == 'json':
                return json.dumps(trade_data, indent=2, default=str)
            elif format.lower() == 'csv':
                # Basic CSV export
                if not trade_data:
                    return ""
                
                headers = list(trade_data[0].keys())
                csv_lines = [','.join(headers)]
                
                for trade in trade_data:
                    row = [str(trade.get(header, '')) for header in headers]
                    csv_lines.append(','.join(row))
                
                return '\n'.join(csv_lines)
            
            return None
            
        except Exception as e:
            logger.error(f"Error exporting trade data: {e}")
            return None
    
    def get_risk_metrics(self, user_id: int) -> Dict[str, Any]:
        """Calculate risk management metrics"""
        try:
            if user_id not in self.trade_records:
                return {'error': 'No trading data available'}
            
            trades = self.trade_records[user_id]
            completed_trades = [t for t in trades if t.status == 'completed']
            
            if not completed_trades:
                return {'error': 'No completed trades available'}
            
            # Calculate drawdown
            running_balance = 10000.0  # Starting balance
            max_balance = running_balance
            max_drawdown = 0.0
            current_drawdown = 0.0
            
            for trade in sorted(completed_trades, key=lambda x: x.start_time):
                running_balance += trade.realized_pnl
                
                if running_balance > max_balance:
                    max_balance = running_balance
                    current_drawdown = 0.0
                else:
                    current_drawdown = (max_balance - running_balance) / max_balance * 100
                    max_drawdown = max(max_drawdown, current_drawdown)
            
            # Risk/Reward ratio
            winning_trades = [t for t in completed_trades if t.realized_pnl > 0]
            losing_trades = [t for t in completed_trades if t.realized_pnl <= 0]
            
            avg_win = sum(t.realized_pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
            avg_loss = abs(sum(t.realized_pnl for t in losing_trades) / len(losing_trades)) if losing_trades else 0
            
            risk_reward_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
            
            # Sharpe ratio (simplified)
            returns = [t.realized_pnl for t in completed_trades]
            if len(returns) > 1:
                import statistics
                avg_return = statistics.mean(returns)
                std_return = statistics.stdev(returns)
                sharpe_ratio = avg_return / std_return if std_return > 0 else 0
            else:
                sharpe_ratio = 0
            
            return {
                'max_drawdown': max_drawdown,
                'current_drawdown': current_drawdown,
                'risk_reward_ratio': risk_reward_ratio,
                'sharpe_ratio': sharpe_ratio,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'largest_win': max((t.realized_pnl for t in completed_trades), default=0),
                'largest_loss': min((t.realized_pnl for t in completed_trades), default=0),
                'win_streak': self._calculate_max_consecutive_wins(completed_trades),
                'loss_streak': self._calculate_max_consecutive_losses(completed_trades)
            }
            
        except Exception as e:
            logger.error(f"Error calculating risk metrics: {e}")
            return {'error': f'Error calculating risk metrics: {str(e)}'}
