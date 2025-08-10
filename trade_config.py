from typing import Optional, List, Tuple, Union, Dict, Any
import uuid
from datetime import datetime


class TradeConfig:
    def __init__(self, trade_id: Optional[str] = None) -> None:
        # Trade identification
        self.trade_id: str = trade_id or str(uuid.uuid4())[:8]
        self.created_at: datetime = datetime.now()
        self.trade_name: Optional[str] = None  # User-friendly name
        
        # Trading pair and basic settings
        self.symbol: Optional[str] = None
        self.side: Optional[str] = None  # 'long' or 'short'
        self.amount: Optional[float] = None
        self.leverage: int = 1
        
        # Entry and exit prices
        self.entry_price: Optional[float] = None
        self.sl_price: Optional[float] = None
        
        # Take profit levels with percentage support
        self.tp1_price: Optional[float] = None
        self.tp1_percent: float = 0  # Percentage gain/loss from entry
        self.tp2_price: Optional[float] = None
        self.tp2_percent: float = 0
        self.tp3_price: Optional[float] = None
        self.tp3_percent: float = 0
        
        # Position size percentages for each TP level (must sum to 100)
        self.tp1_size_percent: float = 33.33  # Default equal distribution
        self.tp2_size_percent: float = 33.33
        self.tp3_size_percent: float = 33.34
        
        # Advanced settings
        self.breakeven_after: Optional[str] = None  # 'tp1', 'tp2', 'tp3'
        self.trail_percent: float = 0  # Trailing stop percentage
        self.trail_activation_percent: float = 0  # When to activate trailing stop
        
        # Bot settings
        self.dry_run: bool = True
        self.testnet: bool = True
        
        # Trade status and workflow
        self.status: str = 'configured'  # configured, active, paused, completed, cancelled
        self.awaiting_input: Optional[str] = None
        self.workflow_step: Optional[str] = None  # Track current step in setup workflow
        self.workflow_progress: int = 0  # Progress percentage (0-100)
        
    def get_display_name(self) -> str:
        """Get user-friendly display name for the trade"""
        if self.trade_name:
            return f"{self.trade_name} ({self.trade_id})"
        elif self.symbol:
            side_text = self.side.upper() if self.side else ''
            return f"{self.symbol} {side_text} ({self.trade_id})"
        else:
            return f"Trade {self.trade_id}"
    
    def get_configuration_summary(self) -> str:
        """Get detailed configuration summary"""
        lines = [f"<b>📋 {self.get_display_name()}</b>"]
        
        if self.symbol:
            lines.append(f"💱 Pair: {self.symbol}")
        if self.side:
            lines.append(f"📊 Side: {self.side.upper()}")
        if self.amount:
            lines.append(f"💰 Amount: {self.amount}")
        if self.leverage and self.leverage > 1:
            lines.append(f"⚡ Leverage: {self.leverage}x")
        if self.entry_price:
            lines.append(f"🎯 Entry: {self.entry_price}")
        
        # Take profits
        tps = []
        if self.tp1_percent > 0:
            tps.append(f"TP1: {self.tp1_percent}% ({self.tp1_size_percent}%)")
        if self.tp2_percent > 0:
            tps.append(f"TP2: {self.tp2_percent}% ({self.tp2_size_percent}%)")
        if self.tp3_percent > 0:
            tps.append(f"TP3: {self.tp3_percent}% ({self.tp3_size_percent}%)")
        
        if tps:
            lines.append(f"🎯 Take Profits: {', '.join(tps)}")
        
        if self.sl_price:
            lines.append(f"🛑 Stop Loss: {self.sl_price}")
        
        if self.breakeven_after:
            lines.append(f"⚖️ Break-even: After {self.breakeven_after.upper()}")
        
        if self.trail_percent > 0:
            trail_info = f"{self.trail_percent}%"
            if self.trail_activation_percent > 0:
                trail_info += f" (activates at +{self.trail_activation_percent}%)"
            lines.append(f"📉 Trailing Stop: {trail_info}")
        
        mode_text = 'Testnet' if self.testnet else 'Mainnet'
        run_text = 'Dry Run' if self.dry_run else 'Live Trading'
        lines.append(f"🤖 Mode: {mode_text} | {run_text}")
        lines.append(f"📊 Status: {self.status.title()}")
        
        return "\n".join(lines)
    
    def get_workflow_progress_text(self) -> str:
        """Get workflow progress indicator"""
        steps = [
            "Trading Pair",
            "Position Side", 
            "Leverage",
            "Amount",
            "Take Profits",
            "Stop Loss",
            "Break-even"
        ]
        
        progress_indicators = []
        current_step = self.workflow_step or "pair"
        
        step_mapping = {
            "pair": 0, "side": 1, "leverage": 2, "amount": 3,
            "takeprofit": 4, "stoploss": 5, "breakeven": 6
        }
        
        current_index = step_mapping.get(current_step, 0)
        
        for i, step in enumerate(steps):
            if i < current_index:
                progress_indicators.append(f"✅ {step}")
            elif i == current_index:
                progress_indicators.append(f"🔄 {step}")
            else:
                progress_indicators.append(f"⏳ {step}")
        
        progress_percent = int((current_index / len(steps)) * 100)
        
        return f"<b>Setup Progress ({progress_percent}%)</b>\n" + "\n".join(progress_indicators)

    def calculate_tp_price_from_percent(self, tp_percent: float) -> Optional[float]:
        """Calculate TP price from percentage gain/loss"""
        if not self.entry_price or tp_percent == 0:
            return None
            
        if self.side == "long":
            # For long positions: TP price = entry_price * (1 + percentage/100)
            return round(self.entry_price * (1 + tp_percent / 100), 8)
        elif self.side == "short":
            # For short positions: TP price = entry_price * (1 - percentage/100)
            return round(self.entry_price * (1 - tp_percent / 100), 8)
        
        return None
    
    def calculate_percent_from_tp_price(self, tp_price: float) -> float:
        """Calculate percentage gain/loss from TP price"""
        if not self.entry_price or not tp_price:
            return 0.0
            
        if self.side == "long":
            # For long positions: percentage = (tp_price - entry_price) / entry_price * 100
            return round((tp_price - self.entry_price) / self.entry_price * 100, 2)
        elif self.side == "short":
            # For short positions: percentage = (entry_price - tp_price) / entry_price * 100
            return round((self.entry_price - tp_price) / self.entry_price * 100, 2)
        
        return 0.0
    
    def update_tp_prices_from_percentages(self) -> None:
        """Update TP prices based on percentage values"""
        if self.tp1_percent > 0:
            self.tp1_price = self.calculate_tp_price_from_percent(self.tp1_percent)
        if self.tp2_percent > 0:
            self.tp2_price = self.calculate_tp_price_from_percent(self.tp2_percent)
        if self.tp3_percent > 0:
            self.tp3_price = self.calculate_tp_price_from_percent(self.tp3_percent)
    
    def update_tp_percentages_from_prices(self) -> None:
        """Update percentage values based on TP prices"""
        if self.tp1_price:
            self.tp1_percent = self.calculate_percent_from_tp_price(self.tp1_price)
        if self.tp2_price:
            self.tp2_percent = self.calculate_percent_from_tp_price(self.tp2_price)
        if self.tp3_price:
            self.tp3_percent = self.calculate_percent_from_tp_price(self.tp3_price)
    
    def calculate_sl_price_from_percent(self, sl_percent: float) -> Optional[float]:
        """Calculate stop loss price from percentage"""
        if not self.entry_price or sl_percent == 0:
            return None
            
        if self.side == "long":
            # For long positions: SL price = entry_price * (1 - percentage/100)
            return round(self.entry_price * (1 - sl_percent / 100), 8)
        elif self.side == "short":
            # For short positions: SL price = entry_price * (1 + percentage/100)
            return round(self.entry_price * (1 + sl_percent / 100), 8)
        
        return None
    
    def calculate_trailing_stop_price(self, current_price: float, 
                                    highest_price: Optional[float] = None, 
                                    lowest_price: Optional[float] = None) -> Optional[float]:
        """Calculate trailing stop price based on current market conditions"""
        if self.trail_percent == 0 or not current_price:
            return None
            
        if self.side == "long":
            # For long positions, trail from the highest price achieved
            reference_price = highest_price if highest_price is not None else current_price
            trail_price = round(reference_price * (1 - self.trail_percent / 100), 8)
            return trail_price
        elif self.side == "short":
            # For short positions, trail from the lowest price achieved
            reference_price = lowest_price if lowest_price is not None else current_price
            trail_price = round(reference_price * (1 + self.trail_percent / 100), 8)
            return trail_price
        
        return None
    
    def should_activate_trailing_stop(self, current_price: float) -> bool:
        """Check if trailing stop should be activated based on profit threshold"""
        if not self.entry_price or not current_price or self.trail_activation_percent == 0:
            return True  # Activate immediately if no threshold set
        
        if self.side == "long":
            profit_percent = (current_price - self.entry_price) / self.entry_price * 100
        elif self.side == "short":
            profit_percent = (self.entry_price - current_price) / self.entry_price * 100
        else:
            return False
        
        return profit_percent >= self.trail_activation_percent
    
    def validate_tp_size_percentages(self) -> List[str]:
        """Validate that TP size percentages sum to 100%"""
        errors = []
        total_percent = self.tp1_size_percent + self.tp2_size_percent + self.tp3_size_percent
        
        if abs(total_percent - 100.0) > 0.01:  # Allow small floating point differences
            errors.append(f"TP size percentages must sum to 100%, currently: {total_percent:.2f}%")
        
        if self.tp1_size_percent < 0 or self.tp2_size_percent < 0 or self.tp3_size_percent < 0:
            errors.append("TP size percentages cannot be negative")
        
        if self.tp1_size_percent > 100 or self.tp2_size_percent > 100 or self.tp3_size_percent > 100:
            errors.append("Individual TP size percentages cannot exceed 100%")
            
        return errors

    def copy(self) -> 'TradeConfig':
        """Create a copy of this config with a new trade ID"""
        new_config = TradeConfig()
        
        # Copy all attributes except trade_id and created_at
        new_config.trade_name = self.trade_name
        new_config.symbol = self.symbol
        new_config.side = self.side
        new_config.amount = self.amount
        new_config.leverage = self.leverage
        new_config.entry_price = self.entry_price
        new_config.sl_price = self.sl_price
        new_config.tp1_price = self.tp1_price
        new_config.tp1_percent = self.tp1_percent
        new_config.tp2_price = self.tp2_price
        new_config.tp2_percent = self.tp2_percent
        new_config.tp3_price = self.tp3_price
        new_config.tp3_percent = self.tp3_percent
        new_config.tp1_size_percent = self.tp1_size_percent
        new_config.tp2_size_percent = self.tp2_size_percent
        new_config.tp3_size_percent = self.tp3_size_percent
        new_config.breakeven_after = self.breakeven_after
        new_config.trail_percent = self.trail_percent
        new_config.trail_activation_percent = self.trail_activation_percent
        new_config.dry_run = self.dry_run
        new_config.testnet = self.testnet
        new_config.status = 'configured'
        new_config.awaiting_input = None
        new_config.workflow_step = None
        new_config.workflow_progress = 0
        
        return new_config
        
    def validate(self) -> List[str]:
        """Validate configuration and return list of errors"""
        errors = []
        
        if not self.symbol:
            errors.append("Trading pair not set")
        
        if not self.side:
            errors.append("Position side not set (long/short)")
        
        if not self.amount or self.amount <= 0:
            errors.append("Position amount not set or invalid")
        
        if not self.entry_price or self.entry_price <= 0:
            errors.append("Entry price not set or invalid")
        
        # Validate take profit configuration
        has_tp = any([self.tp1_percent > 0, self.tp2_percent > 0, self.tp3_percent > 0])
        if not has_tp:
            errors.append("At least one take profit level must be set")
        
        # Validate TP size percentages if any TP is set
        if has_tp:
            tp_errors = self.validate_tp_size_percentages()
            errors.extend(tp_errors)
        
        # Validate stop loss
        if not self.sl_price or self.sl_price <= 0:
            errors.append("Stop loss price not set or invalid")
        
        # Validate leverage
        if self.leverage <= 0 or self.leverage > 100:
            errors.append("Leverage must be between 1 and 100")
        
        # Validate price relationships
        if self.entry_price and self.sl_price:
            if self.side == "long":
                if self.sl_price >= self.entry_price:
                    errors.append("Stop loss must be below entry price for long positions")
            elif self.side == "short":
                if self.sl_price <= self.entry_price:
                    errors.append("Stop loss must be above entry price for short positions")
        
        # Validate TP price relationships
        if self.entry_price:
            for i, (tp_price, tp_percent) in enumerate([(self.tp1_price, self.tp1_percent), 
                                                       (self.tp2_price, self.tp2_percent),
                                                       (self.tp3_price, self.tp3_percent)], 1):
                if tp_percent > 0 and tp_price:
                    if self.side == "long":
                        if tp_price <= self.entry_price:
                            errors.append(f"TP{i} must be above entry price for long positions")
                    elif self.side == "short":
                        if tp_price >= self.entry_price:
                            errors.append(f"TP{i} must be below entry price for short positions")
        
        # Validate trailing stop
        if self.trail_percent < 0 or self.trail_percent > 50:
            errors.append("Trailing stop percentage must be between 0% and 50%")
        
        if self.trail_activation_percent < 0:
            errors.append("Trailing stop activation percentage cannot be negative")
        
        return errors
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        return {
            'trade_id': self.trade_id,
            'created_at': self.created_at.isoformat(),
            'trade_name': self.trade_name,
            'symbol': self.symbol,
            'side': self.side,
            'amount': self.amount,
            'leverage': self.leverage,
            'entry_price': self.entry_price,
            'sl_price': self.sl_price,
            'tp1_price': self.tp1_price,
            'tp1_percent': self.tp1_percent,
            'tp2_price': self.tp2_price,
            'tp2_percent': self.tp2_percent,
            'tp3_price': self.tp3_price,
            'tp3_percent': self.tp3_percent,
            'tp1_size_percent': self.tp1_size_percent,
            'tp2_size_percent': self.tp2_size_percent,
            'tp3_size_percent': self.tp3_size_percent,
            'breakeven_after': self.breakeven_after,
            'trail_percent': self.trail_percent,
            'trail_activation_percent': self.trail_activation_percent,
            'dry_run': self.dry_run,
            'testnet': self.testnet,
            'status': self.status,
            'workflow_step': self.workflow_step,
            'workflow_progress': self.workflow_progress
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TradeConfig':
        """Create configuration from dictionary"""
        config = cls(trade_id=data.get('trade_id'))
        
        # Parse created_at if it's a string
        if isinstance(data.get('created_at'), str):
            config.created_at = datetime.fromisoformat(data['created_at'])
        elif data.get('created_at'):
            config.created_at = data['created_at']
        
        # Set all other attributes
        for key, value in data.items():
            if key not in ['trade_id', 'created_at'] and hasattr(config, key):
                setattr(config, key, value)
        
        return config
