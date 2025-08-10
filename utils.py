import logging
import re
from typing import Any, Dict, List, Optional, Union, Tuple
from datetime import datetime, timedelta
import json
import hashlib
import secrets
import asyncio
from functools import wraps

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO", format_string: Optional[str] = None) -> None:
    """Setup logging configuration"""
    if format_string is None:
        format_string = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=format_string,
        handlers=[
            logging.StreamHandler(),
        ]
    )


def validate_trading_pair(pair: str) -> bool:
    """Validate trading pair format (e.g., BTC/USDT)"""
    pattern = r'^[A-Z0-9]+/[A-Z0-9]+$'
    return bool(re.match(pattern, pair))


def normalize_trading_pair(pair: str) -> str:
    """Normalize trading pair to standard format"""
    # Remove spaces and convert to uppercase
    pair = pair.replace(" ", "").upper()
    
    # Add slash if missing
    if "/" not in pair and len(pair) >= 6:
        # Common patterns: BTCUSDT -> BTC/USDT
        if pair.endswith("USDT"):
            base = pair[:-4]
            quote = "USDT"
        elif pair.endswith("BTC"):
            base = pair[:-3]
            quote = "BTC"
        elif pair.endswith("ETH"):
            base = pair[:-3]
            quote = "ETH"
        else:
            # Fallback: assume last 3-4 chars are quote
            if len(pair) > 6:
                base = pair[:-4]
                quote = pair[-4:]
            else:
                base = pair[:-3]
                quote = pair[-3:]
        
        pair = f"{base}/{quote}"
    
    return pair


def validate_amount(amount: Union[str, float, int]) -> Tuple[bool, Optional[float], str]:
    """Validate and parse trading amount"""
    try:
        if isinstance(amount, str):
            # Remove commas and spaces
            amount_clean = amount.replace(",", "").replace(" ", "")
            amount_float = float(amount_clean)
        else:
            amount_float = float(amount)
        
        if amount_float <= 0:
            return False, None, "Amount must be greater than 0"
        
        if amount_float > 1000000:  # 1M limit
            return False, None, "Amount too large (max 1,000,000)"
        
        return True, amount_float, "Valid amount"
        
    except (ValueError, TypeError):
        return False, None, "Invalid amount format"


def validate_price(price: Union[str, float, int]) -> Tuple[bool, Optional[float], str]:
    """Validate and parse price"""
    try:
        if isinstance(price, str):
            price_clean = price.replace(",", "").replace(" ", "")
            price_float = float(price_clean)
        else:
            price_float = float(price)
        
        if price_float <= 0:
            return False, None, "Price must be greater than 0"
        
        return True, price_float, "Valid price"
        
    except (ValueError, TypeError):
        return False, None, "Invalid price format"


def validate_percentage(percentage: Union[str, float, int]) -> Tuple[bool, Optional[float], str]:
    """Validate and parse percentage value"""
    try:
        if isinstance(percentage, str):
            # Remove % symbol if present
            perc_clean = percentage.replace("%", "").replace(" ", "")
            perc_float = float(perc_clean)
        else:
            perc_float = float(percentage)
        
        if perc_float < 0:
            return False, None, "Percentage cannot be negative"
        
        if perc_float > 1000:  # 1000% limit
            return False, None, "Percentage too large (max 1000%)"
        
        return True, perc_float, "Valid percentage"
        
    except (ValueError, TypeError):
        return False, None, "Invalid percentage format"


def validate_leverage(leverage: Union[str, int]) -> Tuple[bool, Optional[int], str]:
    """Validate leverage value"""
    try:
        if isinstance(leverage, str):
            leverage_clean = leverage.replace("x", "").replace("X", "").strip()
            leverage_int = int(leverage_clean)
        else:
            leverage_int = int(leverage)
        
        if leverage_int < 1:
            return False, None, "Leverage must be at least 1x"
        
        if leverage_int > 100:
            return False, None, "Leverage too high (max 100x)"
        
        valid_leverages = [1, 2, 3, 5, 10, 20, 25, 50, 75, 100]
        if leverage_int not in valid_leverages:
            # Find closest valid leverage
            closest = min(valid_leverages, key=lambda x: abs(x - leverage_int))
            return False, None, f"Invalid leverage. Did you mean {closest}x?"
        
        return True, leverage_int, "Valid leverage"
        
    except (ValueError, TypeError):
        return False, None, "Invalid leverage format"


def parse_tp_sizes_input(input_text: str) -> Tuple[bool, Optional[Tuple[float, float, float]], str]:
    """Parse take profit size percentages input (e.g., '30,40,30' or '33.33,33.33,33.34')"""
    try:
        # Split by comma or space
        parts = re.split(r'[,\s]+', input_text.strip())
        
        if len(parts) != 3:
            return False, None, "Please provide exactly 3 percentages"
        
        percentages = []
        for part in parts:
            # Remove % symbol if present
            clean_part = part.replace("%", "").strip()
            if not clean_part:
                continue
            
            perc = float(clean_part)
            if perc < 0 or perc > 100:
                return False, None, "Each percentage must be between 0% and 100%"
            
            percentages.append(perc)
        
        if len(percentages) != 3:
            return False, None, "Please provide exactly 3 valid percentages"
        
        total = sum(percentages)
        if abs(total - 100.0) > 0.01:  # Allow small floating point differences
            return False, None, f"Percentages must sum to 100% (current: {total:.2f}%)"
        
        return True, tuple(percentages), "Valid TP sizes"
        
    except (ValueError, TypeError):
        return False, None, "Invalid format. Use: '30,40,30' or '33.33 33.33 33.34'"


def format_price(price: float, precision: int = 6) -> str:
    """Format price with appropriate precision"""
    if price >= 1000:
        return f"{price:,.{max(0, precision-4)}f}"
    elif price >= 1:
        return f"{price:.{precision}f}"
    else:
        # For small prices, show more decimal places
        return f"{price:.{precision+2}f}".rstrip('0').rstrip('.')


def format_amount(amount: float, precision: int = 4) -> str:
    """Format amount with appropriate precision"""
    if amount >= 1000:
        return f"{amount:,.{max(0, precision-2)}f}"
    else:
        return f"{amount:.{precision}f}".rstrip('0').rstrip('.')


def format_percentage(percentage: float, precision: int = 2) -> str:
    """Format percentage with sign and % symbol"""
    sign = "+" if percentage > 0 else ""
    return f"{sign}{percentage:.{precision}f}%"


def format_pnl(pnl: float, precision: int = 2) -> str:
    """Format P&L with appropriate color indicators"""
    sign = "+" if pnl > 0 else ""
    return f"{sign}{pnl:.{precision}f} USDT"


def format_duration(start_time: datetime, end_time: Optional[datetime] = None) -> str:
    """Format duration between two timestamps"""
    if end_time is None:
        end_time = datetime.now()
    
    duration = end_time - start_time
    total_seconds = int(duration.total_seconds())
    
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def calculate_position_value(amount: float, price: float, leverage: int) -> float:
    """Calculate total position value"""
    return amount * price * leverage


def calculate_margin_required(amount: float, price: float, leverage: int) -> float:
    """Calculate margin required for position"""
    return (amount * price) / leverage


def calculate_liquidation_price(entry_price: float, leverage: int, side: str, 
                              maintenance_margin_rate: float = 0.004) -> float:
    """Calculate liquidation price"""
    if side.lower() == "long":
        return entry_price * (1 - (1/leverage) + maintenance_margin_rate)
    else:  # short
        return entry_price * (1 + (1/leverage) - maintenance_margin_rate)


def calculate_risk_reward_ratio(entry_price: float, stop_loss: float, 
                               take_profit: float, side: str) -> float:
    """Calculate risk/reward ratio"""
    if side.lower() == "long":
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
    else:  # short
        risk = abs(stop_loss - entry_price)
        reward = abs(entry_price - take_profit)
    
    return reward / risk if risk > 0 else float('inf')


def generate_trade_id() -> str:
    """Generate unique trade ID"""
    return secrets.token_hex(4).upper()


def hash_config(config_dict: Dict[str, Any]) -> str:
    """Generate hash of configuration for change detection"""
    config_str = json.dumps(config_dict, sort_keys=True)
    return hashlib.md5(config_str.encode()).hexdigest()[:8]


def chunk_list(input_list: List[Any], chunk_size: int) -> List[List[Any]]:
    """Split list into chunks of specified size"""
    return [input_list[i:i + chunk_size] for i in range(0, len(input_list), chunk_size)]


def safe_float_convert(value: Any, default: float = 0.0) -> float:
    """Safely convert value to float with default"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int_convert(value: Any, default: int = 0) -> int:
    """Safely convert value to int with default"""
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate text to specified length"""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def escape_markdown(text: str) -> str:
    """Escape markdown special characters"""
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text


def retry_on_exception(max_retries: int = 3, delay: float = 1.0, 
                      exceptions: Tuple = (Exception,)):
    """Decorator for retrying function calls on exceptions"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(f"Attempt {attempt + 1} failed for {func.__name__}: {e}")
                        await asyncio.sleep(delay * (2 ** attempt))  # Exponential backoff
                    else:
                        logger.error(f"All {max_retries + 1} attempts failed for {func.__name__}")
            
            raise last_exception
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(f"Attempt {attempt + 1} failed for {func.__name__}: {e}")
                        import time
                        time.sleep(delay * (2 ** attempt))  # Exponential backoff
                    else:
                        logger.error(f"All {max_retries + 1} attempts failed for {func.__name__}")
            
            raise last_exception
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


class RateLimiter:
    """Simple rate limiter implementation"""
    
    def __init__(self, max_calls: int, time_window: float):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = []
    
    async def acquire(self):
        """Acquire rate limit permission"""
        now = datetime.now()
        
        # Remove old calls outside the time window
        cutoff = now - timedelta(seconds=self.time_window)
        self.calls = [call_time for call_time in self.calls if call_time > cutoff]
        
        # Check if we can make a call
        if len(self.calls) >= self.max_calls:
            # Calculate how long to wait
            oldest_call = min(self.calls)
            wait_time = (oldest_call + timedelta(seconds=self.time_window) - now).total_seconds()
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                return await self.acquire()  # Recursive call after waiting
        
        # Record this call
        self.calls.append(now)


def get_emoji_for_side(side: str) -> str:
    """Get emoji for trading side"""
    return "📈" if side.lower() == "long" else "📉"


def get_emoji_for_status(status: str) -> str:
    """Get emoji for trade status"""
    status_emojis = {
        "configured": "🟡",
        "active": "🟢",
        "paused": "🟠",
        "completed": "✅",
        "cancelled": "❌",
        "error": "🔴"
    }
    return status_emojis.get(status.lower(), "⚪")


def get_emoji_for_pnl(pnl: float) -> str:
    """Get emoji for P&L"""
    if pnl > 0:
        return "💚"
    elif pnl < 0:
        return "❤️"
    else:
        return "🤍"


def create_progress_bar(progress: float, width: int = 10) -> str:
    """Create text-based progress bar"""
    filled = int(progress * width / 100)
    empty = width - filled
    return "█" * filled + "░" * empty


def validate_telegram_chat_id(chat_id: Any) -> bool:
    """Validate Telegram chat ID format"""
    try:
        chat_id_int = int(chat_id)
        # Telegram chat IDs are typically large negative numbers for groups
        # or positive numbers for private chats
        return abs(chat_id_int) > 0
    except (ValueError, TypeError):
        return False


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file operations"""
    # Remove or replace dangerous characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing dots and spaces
    sanitized = sanitized.strip('. ')
    # Ensure it's not empty
    if not sanitized:
        sanitized = "untitled"
    return sanitized


def parse_time_string(time_str: str) -> Optional[timedelta]:
    """Parse time string like '1h 30m' into timedelta"""
    try:
        total_seconds = 0
        
        # Find hours
        hours_match = re.search(r'(\d+)h', time_str, re.IGNORECASE)
        if hours_match:
            total_seconds += int(hours_match.group(1)) * 3600
        
        # Find minutes
        minutes_match = re.search(r'(\d+)m', time_str, re.IGNORECASE)
        if minutes_match:
            total_seconds += int(minutes_match.group(1)) * 60
        
        # Find seconds
        seconds_match = re.search(r'(\d+)s', time_str, re.IGNORECASE)
        if seconds_match:
            total_seconds += int(seconds_match.group(1))
        
        return timedelta(seconds=total_seconds) if total_seconds > 0 else None
        
    except Exception:
        return None


def mask_api_key(api_key: str) -> str:
    """Mask API key for logging purposes"""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return api_key[:4] + "*" * (len(api_key) - 8) + api_key[-4:]


def is_market_hours() -> bool:
    """Check if it's market hours (crypto markets are 24/7)"""
    return True  # Crypto markets never close


def get_next_market_open() -> Optional[datetime]:
    """Get next market open time (not applicable for crypto)"""
    return None  # Crypto markets are always open


class ConfigValidator:
    """Validator class for trading configurations"""
    
    @staticmethod
    def validate_trading_config(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate complete trading configuration"""
        errors = []
        
        # Required fields
        required_fields = ['symbol', 'side', 'amount', 'entry_price']
        for field in required_fields:
            if not config.get(field):
                errors.append(f"Missing required field: {field}")
        
        # Validate symbol
        if config.get('symbol') and not validate_trading_pair(config['symbol']):
            errors.append("Invalid trading pair format")
        
        # Validate side
        if config.get('side') and config['side'].lower() not in ['long', 'short']:
            errors.append("Side must be 'long' or 'short'")
        
        # Validate amounts and prices
        numeric_fields = ['amount', 'entry_price', 'sl_price', 'tp1_price', 'tp2_price', 'tp3_price']
        for field in numeric_fields:
            value = config.get(field)
            if value is not None:
                try:
                    float_val = float(value)
                    if float_val <= 0:
                        errors.append(f"{field} must be greater than 0")
                except (ValueError, TypeError):
                    errors.append(f"{field} must be a valid number")
        
        # Validate leverage
        leverage = config.get('leverage', 1)
        valid, _, error_msg = validate_leverage(leverage)
        if not valid:
            errors.append(f"Leverage error: {error_msg}")
        
        # Validate percentages
        percentage_fields = ['tp1_percent', 'tp2_percent', 'tp3_percent', 'trail_percent']
        for field in percentage_fields:
            value = config.get(field)
            if value is not None:
                valid, _, error_msg = validate_percentage(value)
                if not valid:
                    errors.append(f"{field} error: {error_msg}")
        
        return len(errors) == 0, errors


# Export commonly used functions
__all__ = [
    'setup_logging',
    'validate_trading_pair',
    'normalize_trading_pair',
    'validate_amount',
    'validate_price',
    'validate_percentage',
    'validate_leverage',
    'parse_tp_sizes_input',
    'format_price',
    'format_amount',
    'format_percentage',
    'format_pnl',
    'format_duration',
    'calculate_position_value',
    'calculate_margin_required',
    'calculate_liquidation_price',
    'calculate_risk_reward_ratio',
    'generate_trade_id',
    'hash_config',
    'chunk_list',
    'safe_float_convert',
    'safe_int_convert',
    'truncate_text',
    'escape_markdown',
    'retry_on_exception',
    'RateLimiter',
    'get_emoji_for_side',
    'get_emoji_for_status',
    'get_emoji_for_pnl',
    'create_progress_bar',
    'validate_telegram_chat_id',
    'sanitize_filename',
    'parse_time_string',
    'mask_api_key',
    'is_market_hours',
    'get_next_market_open',
    'ConfigValidator'
]
