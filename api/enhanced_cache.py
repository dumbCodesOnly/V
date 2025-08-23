"""
Enhanced Caching System for Trading Bot
Implements smart caching with volatility-based invalidation and comprehensive user data caching
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from collections import defaultdict
import statistics

class VolatilityTracker:
    """Track price volatility for smart cache invalidation"""
    
    def __init__(self, window_size=10):
        self.window_size = window_size
        self.price_history = defaultdict(list)  # {symbol: [prices]}
        self.volatility_cache = {}  # {symbol: volatility_score}
        self.lock = threading.Lock()
    
    def add_price(self, symbol: str, price: float):
        """Add a new price point and calculate volatility"""
        with self.lock:
            self.price_history[symbol].append({
                'price': price,
                'timestamp': datetime.utcnow()
            })
            
            # Keep only recent prices within window
            cutoff_time = datetime.utcnow() - timedelta(minutes=5)
            self.price_history[symbol] = [
                p for p in self.price_history[symbol] 
                if p['timestamp'] > cutoff_time
            ][-self.window_size:]
            
            # Calculate volatility if we have enough data points
            if len(self.price_history[symbol]) >= 3:
                prices = [p['price'] for p in self.price_history[symbol]]
                
                # Calculate standard deviation as volatility measure
                try:
                    volatility = statistics.stdev(prices) / statistics.mean(prices) * 100
                    self.volatility_cache[symbol] = volatility
                except (statistics.StatisticsError, ZeroDivisionError):
                    self.volatility_cache[symbol] = 0.0
    
    def get_volatility(self, symbol: str) -> float:
        """Get current volatility score for symbol (0-100+)"""
        with self.lock:
            return self.volatility_cache.get(symbol, 0.0)
    
    def is_high_volatility(self, symbol: str, threshold: float = 2.0) -> bool:
        """Check if symbol is experiencing high volatility"""
        return self.get_volatility(symbol) > threshold

class SmartCache:
    """Enhanced caching system with volatility-based invalidation"""
    
    def __init__(self):
        self.lock = threading.Lock()
        self.volatility_tracker = VolatilityTracker()
        
        # Price cache with enhanced metadata
        self.price_cache = {}  # {symbol: cache_entry}
        
        # User data caches
        self.user_trade_configs_cache = {}  # {user_id: cache_entry}
        self.user_credentials_cache = {}    # {user_id: cache_entry}
        self.user_preferences_cache = {}    # {user_id: cache_entry}
        
        # Cache statistics
        self.cache_stats = {
            'price_hits': 0,
            'price_misses': 0,
            'user_data_hits': 0,
            'user_data_misses': 0,
            'invalidations': 0,
            'total_requests': 0
        }
        
        # Cache configurations
        self.config = {
            'base_price_ttl': 10,      # Base TTL for prices (seconds)
            'min_price_ttl': 2,        # Minimum TTL for high volatility
            'max_price_ttl': 30,       # Maximum TTL for stable assets
            'user_data_ttl': 300,      # User data TTL (5 minutes)
            'credentials_ttl': 1800,   # Credentials TTL (30 minutes)
            'preferences_ttl': 3600,   # Preferences TTL (1 hour)
            'volatility_threshold': 2.0  # Volatility threshold for cache invalidation
        }
    
    def _create_cache_entry(self, data: Any, ttl_seconds: int, metadata: Optional[Dict] = None) -> Dict:
        """Create a standardized cache entry"""
        return {
            'data': data,
            'timestamp': datetime.utcnow(),
            'ttl': ttl_seconds,
            'metadata': metadata or {},
            'hits': 0
        }
    
    def _is_cache_valid(self, cache_entry: Dict) -> bool:
        """Check if cache entry is still valid"""
        if not cache_entry:
            return False
        
        age = (datetime.utcnow() - cache_entry['timestamp']).total_seconds()
        return age < cache_entry['ttl']
    
    def _calculate_dynamic_ttl(self, symbol: str, base_ttl: int) -> int:
        """Calculate dynamic TTL based on volatility"""
        volatility = self.volatility_tracker.get_volatility(symbol)
        
        if volatility > self.config['volatility_threshold']:
            # High volatility = shorter cache time
            ttl_multiplier = max(0.2, 1.0 - (volatility / 10.0))
            dynamic_ttl = int(base_ttl * ttl_multiplier)
            return max(self.config['min_price_ttl'], dynamic_ttl)
        else:
            # Low volatility = longer cache time
            ttl_multiplier = min(3.0, 1.0 + (2.0 / max(volatility, 0.1)))
            dynamic_ttl = int(base_ttl * ttl_multiplier)
            return min(self.config['max_price_ttl'], dynamic_ttl)
    
    # Price Caching Methods
    def get_price(self, symbol: str) -> Optional[Tuple[float, str, Dict]]:
        """Get cached price with metadata"""
        with self.lock:
            self.cache_stats['total_requests'] += 1
            
            if symbol in self.price_cache:
                cache_entry = self.price_cache[symbol]
                
                if self._is_cache_valid(cache_entry):
                    cache_entry['hits'] += 1
                    self.cache_stats['price_hits'] += 1
                    
                    return (
                        cache_entry['data']['price'],
                        cache_entry['data']['source'],
                        {
                            'cached': True,
                            'age_seconds': (datetime.utcnow() - cache_entry['timestamp']).total_seconds(),
                            'hits': cache_entry['hits'],
                            'volatility': self.volatility_tracker.get_volatility(symbol)
                        }
                    )
                else:
                    # Cache expired
                    del self.price_cache[symbol]
            
            self.cache_stats['price_misses'] += 1
            return None
    
    def set_price(self, symbol: str, price: float, source: str) -> None:
        """Cache price with dynamic TTL based on volatility"""
        # Track price for volatility calculation
        self.volatility_tracker.add_price(symbol, price)
        
        # Calculate dynamic TTL
        dynamic_ttl = self._calculate_dynamic_ttl(symbol, self.config['base_price_ttl'])
        
        with self.lock:
            cache_entry = self._create_cache_entry(
                data={'price': price, 'source': source},
                ttl_seconds=dynamic_ttl,
                metadata={
                    'volatility': self.volatility_tracker.get_volatility(symbol),
                    'dynamic_ttl': dynamic_ttl
                }
            )
            self.price_cache[symbol] = cache_entry
            
            logging.debug(f"Cached price for {symbol}: ${price} (TTL: {dynamic_ttl}s, volatility: {cache_entry['metadata']['volatility']:.2f}%)")
    
    def invalidate_price(self, symbol: Optional[str] = None) -> None:
        """Invalidate price cache for symbol or all symbols"""
        with self.lock:
            if symbol:
                if symbol in self.price_cache:
                    del self.price_cache[symbol]
                    self.cache_stats['invalidations'] += 1
            else:
                count = len(self.price_cache)
                self.price_cache.clear()
                self.cache_stats['invalidations'] += count
    
    # User Data Caching Methods
    def get_user_trade_configs(self, user_id: str) -> Optional[Tuple[Dict, Dict]]:
        """Get cached user trade configurations"""
        with self.lock:
            self.cache_stats['total_requests'] += 1
            
            if user_id in self.user_trade_configs_cache:
                cache_entry = self.user_trade_configs_cache[user_id]
                
                if self._is_cache_valid(cache_entry):
                    cache_entry['hits'] += 1
                    self.cache_stats['user_data_hits'] += 1
                    
                    return cache_entry['data'], {
                        'cached': True,
                        'age_seconds': (datetime.utcnow() - cache_entry['timestamp']).total_seconds(),
                        'hits': cache_entry['hits']
                    }
                else:
                    del self.user_trade_configs_cache[user_id]
            
            self.cache_stats['user_data_misses'] += 1
            return None
    
    def set_user_trade_configs(self, user_id: str, trade_configs: Dict) -> None:
        """Cache user trade configurations"""
        with self.lock:
            cache_entry = self._create_cache_entry(
                data=trade_configs,
                ttl_seconds=self.config['user_data_ttl']
            )
            self.user_trade_configs_cache[user_id] = cache_entry
            
            logging.debug(f"Cached trade configs for user {user_id} ({len(trade_configs)} trades)")
    
    def get_user_credentials(self, user_id: str) -> Optional[Tuple[Any, Dict]]:
        """Get cached user credentials"""
        with self.lock:
            if user_id in self.user_credentials_cache:
                cache_entry = self.user_credentials_cache[user_id]
                
                if self._is_cache_valid(cache_entry):
                    cache_entry['hits'] += 1
                    return cache_entry['data'], {
                        'cached': True,
                        'age_seconds': (datetime.utcnow() - cache_entry['timestamp']).total_seconds()
                    }
                else:
                    del self.user_credentials_cache[user_id]
            
            return None
    
    def set_user_credentials(self, user_id: str, credentials: Any) -> None:
        """Cache user credentials"""
        with self.lock:
            cache_entry = self._create_cache_entry(
                data=credentials,
                ttl_seconds=self.config['credentials_ttl']
            )
            self.user_credentials_cache[user_id] = cache_entry
    
    def get_user_preferences(self, user_id: str) -> Optional[Tuple[Dict, Dict]]:
        """Get cached user preferences"""
        with self.lock:
            if user_id in self.user_preferences_cache:
                cache_entry = self.user_preferences_cache[user_id]
                
                if self._is_cache_valid(cache_entry):
                    cache_entry['hits'] += 1
                    return cache_entry['data'], {
                        'cached': True,
                        'age_seconds': (datetime.utcnow() - cache_entry['timestamp']).total_seconds()
                    }
                else:
                    del self.user_preferences_cache[user_id]
            
            return None
    
    def set_user_preferences(self, user_id: str, preferences: Dict) -> None:
        """Cache user preferences"""
        with self.lock:
            cache_entry = self._create_cache_entry(
                data=preferences,
                ttl_seconds=self.config['preferences_ttl']
            )
            self.user_preferences_cache[user_id] = cache_entry
    
    def invalidate_user_data(self, user_id: Optional[str] = None) -> None:
        """Invalidate user data cache for specific user or all users"""
        with self.lock:
            if user_id:
                caches = [
                    self.user_trade_configs_cache,
                    self.user_credentials_cache,
                    self.user_preferences_cache
                ]
                for cache in caches:
                    if user_id in cache:
                        del cache[user_id]
                        self.cache_stats['invalidations'] += 1
            else:
                total_items = (
                    len(self.user_trade_configs_cache) +
                    len(self.user_credentials_cache) +
                    len(self.user_preferences_cache)
                )
                self.user_trade_configs_cache.clear()
                self.user_credentials_cache.clear()
                self.user_preferences_cache.clear()
                self.cache_stats['invalidations'] += total_items
    
    def cleanup_expired(self) -> int:
        """Remove all expired cache entries and return count of removed items"""
        removed_count = 0
        current_time = datetime.utcnow()
        
        with self.lock:
            # Clean price cache
            expired_prices = [
                symbol for symbol, entry in self.price_cache.items()
                if (current_time - entry['timestamp']).total_seconds() >= entry['ttl']
            ]
            for symbol in expired_prices:
                del self.price_cache[symbol]
                removed_count += 1
            
            # Clean user data caches
            for cache_dict in [
                self.user_trade_configs_cache,
                self.user_credentials_cache,
                self.user_preferences_cache
            ]:
                expired_keys = [
                    key for key, entry in cache_dict.items()
                    if (current_time - entry['timestamp']).total_seconds() >= entry['ttl']
                ]
                for key in expired_keys:
                    del cache_dict[key]
                    removed_count += 1
        
        if removed_count > 0:
            logging.debug(f"Cleaned up {removed_count} expired cache entries")
        
        return removed_count
    
    def get_cache_stats(self) -> Dict:
        """Get comprehensive cache statistics"""
        with self.lock:
            total_hits = self.cache_stats['price_hits'] + self.cache_stats['user_data_hits']
            total_misses = self.cache_stats['price_misses'] + self.cache_stats['user_data_misses']
            hit_rate = (total_hits / (total_hits + total_misses)) * 100 if (total_hits + total_misses) > 0 else 0
            
            return {
                'hit_rate': round(hit_rate, 2),
                'total_requests': self.cache_stats['total_requests'],
                'cache_sizes': {
                    'prices': len(self.price_cache),
                    'user_trade_configs': len(self.user_trade_configs_cache),
                    'user_credentials': len(self.user_credentials_cache),
                    'user_preferences': len(self.user_preferences_cache)
                },
                'detailed_stats': self.cache_stats.copy(),
                'volatility_tracking': {
                    'symbols_tracked': len(self.volatility_tracker.volatility_cache),
                    'high_volatility_symbols': [
                        symbol for symbol, volatility in self.volatility_tracker.volatility_cache.items()
                        if volatility > self.config['volatility_threshold']
                    ]
                }
            }
    
    def reset_stats(self) -> None:
        """Reset cache statistics"""
        with self.lock:
            self.cache_stats = {
                'price_hits': 0,
                'price_misses': 0,
                'user_data_hits': 0,
                'user_data_misses': 0,
                'invalidations': 0,
                'total_requests': 0
            }

# Global cache instance
enhanced_cache = SmartCache()

def start_cache_cleanup_worker():
    """Start background worker to clean up expired cache entries"""
    def cleanup_worker():
        while True:
            try:
                enhanced_cache.cleanup_expired()
                time.sleep(60)  # Clean up every minute
            except Exception as e:
                logging.error(f"Cache cleanup worker error: {e}")
                time.sleep(60)
    
    import threading
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()
    logging.info("Cache cleanup worker started")