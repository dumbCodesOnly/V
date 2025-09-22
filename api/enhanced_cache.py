"""
Enhanced Caching System for Trading Bot
Implements smart caching with volatility-based invalidation and comprehensive user data caching
"""

import logging
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

# Import configuration constants
try:
    from config import CacheConfig
except ImportError:
    import os
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import CacheConfig


class VolatilityTracker:
    """Track price volatility for smart cache invalidation"""

    def __init__(self, window_size=None):
        self.window_size = window_size or CacheConfig.VOLATILITY_WINDOW_SIZE
        self.price_history = defaultdict(list)  # {symbol: [prices]}
        self.volatility_cache = {}  # {symbol: volatility_score}
        self.lock = threading.Lock()

    def add_price(self, symbol: str, price: float):
        """Add a new price point and calculate volatility"""
        with self.lock:
            self.price_history[symbol].append(
                {"price": price, "timestamp": datetime.utcnow()}
            )

            # Keep only recent prices within window
            cutoff_time = datetime.utcnow() - timedelta(minutes=5)
            self.price_history[symbol] = [
                p for p in self.price_history[symbol] if p["timestamp"] > cutoff_time
            ][-self.window_size :]

            # Calculate volatility if we have enough data points
            if len(self.price_history[symbol]) >= 3:
                prices = [p["price"] for p in self.price_history[symbol]]

                # Calculate standard deviation as volatility measure
                try:
                    volatility = (
                        statistics.stdev(prices)
                        / statistics.mean(prices)
                        * CacheConfig.VOLATILITY_CALCULATION_MULTIPLIER
                    )
                    self.volatility_cache[symbol] = volatility
                except (statistics.StatisticsError, ZeroDivisionError):
                    self.volatility_cache[symbol] = 0.0

    def get_volatility(self, symbol: str) -> float:
        """Get current volatility score for symbol (0-100+)"""
        with self.lock:
            return self.volatility_cache.get(symbol, 0.0)

    def is_high_volatility(
        self, symbol: str, threshold: Optional[float] = None
    ) -> bool:
        """Check if symbol is experiencing high volatility"""
        threshold = threshold or CacheConfig.HIGH_VOLATILITY_THRESHOLD
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
        self.user_credentials_cache = {}  # {user_id: cache_entry}
        self.user_preferences_cache = {}  # {user_id: cache_entry}

        # Cache statistics
        self.cache_stats = {
            "price_hits": 0,
            "price_misses": 0,
            "user_data_hits": 0,
            "user_data_misses": 0,
            "invalidations": 0,
            "total_requests": 0,
        }

        # Cache configurations
        self.config = {
            "base_price_ttl": CacheConfig.BASE_PRICE_TTL,
            "min_price_ttl": CacheConfig.MIN_PRICE_TTL,
            "max_price_ttl": CacheConfig.MAX_PRICE_TTL,
            "user_data_ttl": CacheConfig.USER_DATA_TTL,
            "credentials_ttl": CacheConfig.CREDENTIALS_TTL,
            "preferences_ttl": CacheConfig.PREFERENCES_TTL,
            "volatility_threshold": CacheConfig.HIGH_VOLATILITY_THRESHOLD,
        }

    def _create_cache_entry(
        self, data: Any, ttl_seconds: int, metadata: Optional[Dict] = None
    ) -> Dict:
        """Create a standardized cache entry"""
        return {
            "data": data,
            "timestamp": datetime.utcnow(),
            "ttl": ttl_seconds,
            "metadata": metadata or {},
            "hits": 0,
        }

    def _is_cache_valid(self, cache_entry: Dict) -> bool:
        """Check if cache entry is still valid"""
        if not cache_entry:
            return False

        age = (datetime.utcnow() - cache_entry["timestamp"]).total_seconds()
        return age < cache_entry["ttl"]

    def _calculate_dynamic_ttl(self, symbol: str, base_ttl: int) -> int:
        """Calculate dynamic TTL based on volatility"""
        volatility = self.volatility_tracker.get_volatility(symbol)

        if volatility > self.config["volatility_threshold"]:
            # High volatility = shorter cache time
            ttl_multiplier = max(
                CacheConfig.MIN_TTL_MULTIPLIER,
                1.0 - (volatility / CacheConfig.VOLATILITY_DIVISOR),
            )
            dynamic_ttl = int(base_ttl * ttl_multiplier)
            return max(self.config["min_price_ttl"], dynamic_ttl)
        else:
            # Low volatility = longer cache time
            ttl_multiplier = min(
                CacheConfig.MAX_TTL_MULTIPLIER,
                1.0
                + (
                    CacheConfig.STABILITY_MULTIPLIER
                    / max(volatility, CacheConfig.MIN_VOLATILITY_THRESHOLD)
                ),
            )
            dynamic_ttl = int(base_ttl * ttl_multiplier)
            return min(self.config["max_price_ttl"], dynamic_ttl)

    # Price Caching Methods
    def get_price(self, symbol: str) -> Optional[Tuple[float, str, Dict]]:
        """Get cached price with metadata"""
        with self.lock:
            self.cache_stats["total_requests"] += 1

            if symbol in self.price_cache:
                cache_entry = self.price_cache[symbol]

                if self._is_cache_valid(cache_entry):
                    cache_entry["hits"] += 1
                    self.cache_stats["price_hits"] += 1

                    return (
                        cache_entry["data"]["price"],
                        cache_entry["data"]["source"],
                        {
                            "cached": True,
                            "age_seconds": (
                                datetime.utcnow() - cache_entry["timestamp"]
                            ).total_seconds(),
                            "hits": cache_entry["hits"],
                            "volatility": self.volatility_tracker.get_volatility(
                                symbol
                            ),
                        },
                    )
                else:
                    # Cache expired
                    del self.price_cache[symbol]

            self.cache_stats["price_misses"] += 1
            return None

    def set_price(self, symbol: str, price: float, source: str) -> None:
        """Cache price with dynamic TTL based on volatility"""
        # Track price for volatility calculation
        self.volatility_tracker.add_price(symbol, price)

        # Calculate dynamic TTL
        dynamic_ttl = self._calculate_dynamic_ttl(symbol, self.config["base_price_ttl"])

        with self.lock:
            cache_entry = self._create_cache_entry(
                data={"price": price, "source": source},
                ttl_seconds=dynamic_ttl,
                metadata={
                    "volatility": self.volatility_tracker.get_volatility(symbol),
                    "dynamic_ttl": dynamic_ttl,
                },
            )
            self.price_cache[symbol] = cache_entry

    # Price cached - removed debug log for cleaner output

    def invalidate_price(self, symbol: Optional[str] = None) -> None:
        """Invalidate price cache for symbol or all symbols"""
        with self.lock:
            if symbol:
                if symbol in self.price_cache:
                    del self.price_cache[symbol]
                    self.cache_stats["invalidations"] += 1
            else:
                count = len(self.price_cache)
                self.price_cache.clear()
                self.cache_stats["invalidations"] += count

    # User Data Caching Methods
    def get_user_trade_configs(self, user_id: str) -> Optional[Tuple[Dict, Dict]]:
        """Get cached user trade configurations"""
        with self.lock:
            self.cache_stats["total_requests"] += 1

            if user_id in self.user_trade_configs_cache:
                cache_entry = self.user_trade_configs_cache[user_id]

                if self._is_cache_valid(cache_entry):
                    cache_entry["hits"] += 1
                    self.cache_stats["user_data_hits"] += 1

                    return cache_entry["data"], {
                        "cached": True,
                        "age_seconds": (
                            datetime.utcnow() - cache_entry["timestamp"]
                        ).total_seconds(),
                        "hits": cache_entry["hits"],
                    }
                else:
                    del self.user_trade_configs_cache[user_id]

            self.cache_stats["user_data_misses"] += 1
            return None

    def set_user_trade_configs(self, user_id: str, trade_configs: Dict) -> None:
        """Cache user trade configurations"""
        with self.lock:
            cache_entry = self._create_cache_entry(
                data=trade_configs, ttl_seconds=self.config["user_data_ttl"]
            )
            self.user_trade_configs_cache[user_id] = cache_entry

    # Trade configs cached - removed debug log for cleaner output

    def get_user_credentials(self, user_id: str) -> Optional[Tuple[Any, Dict]]:
        """Get cached user credentials"""
        with self.lock:
            if user_id in self.user_credentials_cache:
                cache_entry = self.user_credentials_cache[user_id]

                if self._is_cache_valid(cache_entry):
                    cache_entry["hits"] += 1
                    return cache_entry["data"], {
                        "cached": True,
                        "age_seconds": (
                            datetime.utcnow() - cache_entry["timestamp"]
                        ).total_seconds(),
                    }
                else:
                    del self.user_credentials_cache[user_id]

            return None

    def set_user_credentials(self, user_id: str, credentials: Any) -> None:
        """Cache user credentials"""
        with self.lock:
            cache_entry = self._create_cache_entry(
                data=credentials, ttl_seconds=self.config["credentials_ttl"]
            )
            self.user_credentials_cache[user_id] = cache_entry

    def get_user_preferences(self, user_id: str) -> Optional[Tuple[Dict, Dict]]:
        """Get cached user preferences"""
        with self.lock:
            if user_id in self.user_preferences_cache:
                cache_entry = self.user_preferences_cache[user_id]

                if self._is_cache_valid(cache_entry):
                    cache_entry["hits"] += 1
                    return cache_entry["data"], {
                        "cached": True,
                        "age_seconds": (
                            datetime.utcnow() - cache_entry["timestamp"]
                        ).total_seconds(),
                    }
                else:
                    del self.user_preferences_cache[user_id]

            return None

    def set_user_preferences(self, user_id: str, preferences: Dict) -> None:
        """Cache user preferences"""
        with self.lock:
            cache_entry = self._create_cache_entry(
                data=preferences, ttl_seconds=self.config["preferences_ttl"]
            )
            self.user_preferences_cache[user_id] = cache_entry

    def invalidate_user_data(self, user_id: Optional[str] = None) -> None:
        """Invalidate user data cache for specific user or all users"""
        with self.lock:
            if user_id:
                caches = [
                    self.user_trade_configs_cache,
                    self.user_credentials_cache,
                    self.user_preferences_cache,
                ]
                for cache in caches:
                    if user_id in cache:
                        del cache[user_id]
                        self.cache_stats["invalidations"] += 1
            else:
                total_items = (
                    len(self.user_trade_configs_cache)
                    + len(self.user_credentials_cache)
                    + len(self.user_preferences_cache)
                )
                self.user_trade_configs_cache.clear()
                self.user_credentials_cache.clear()
                self.user_preferences_cache.clear()
                self.cache_stats["invalidations"] += total_items

    def cleanup_expired(self) -> int:
        """Remove all expired cache entries and return count of removed items"""
        removed_count = 0
        current_time = datetime.utcnow()

        with self.lock:
            # Clean price cache
            expired_prices = [
                symbol
                for symbol, entry in self.price_cache.items()
                if (current_time - entry["timestamp"]).total_seconds() >= entry["ttl"]
            ]
            for symbol in expired_prices:
                del self.price_cache[symbol]
                removed_count += 1

            # Clean user data caches
            for cache_dict in [
                self.user_trade_configs_cache,
                self.user_credentials_cache,
                self.user_preferences_cache,
            ]:
                expired_keys = [
                    key
                    for key, entry in cache_dict.items()
                    if (current_time - entry["timestamp"]).total_seconds()
                    >= entry["ttl"]
                ]
                for key in expired_keys:
                    del cache_dict[key]
                    removed_count += 1

        if removed_count > 0:
            # Cleaned up expired cache entries - removed debug log for cleaner output
            pass

        return removed_count

    def get_cache_stats(self) -> Dict:
        """Get comprehensive cache statistics"""
        with self.lock:
            total_hits = (
                self.cache_stats["price_hits"] + self.cache_stats["user_data_hits"]
            )
            total_misses = (
                self.cache_stats["price_misses"] + self.cache_stats["user_data_misses"]
            )
            hit_rate = (
                (total_hits / (total_hits + total_misses))
                * CacheConfig.HIT_RATE_PERCENTAGE_MULTIPLIER
                if (total_hits + total_misses) > 0
                else 0
            )

            return {
                "hit_rate": round(hit_rate, 2),
                "total_requests": self.cache_stats["total_requests"],
                "cache_sizes": {
                    "prices": len(self.price_cache),
                    "user_trade_configs": len(self.user_trade_configs_cache),
                    "user_credentials": len(self.user_credentials_cache),
                    "user_preferences": len(self.user_preferences_cache),
                },
                "detailed_stats": self.cache_stats.copy(),
                "volatility_tracking": {
                    "symbols_tracked": len(self.volatility_tracker.volatility_cache),
                    "high_volatility_symbols": [
                        symbol
                        for symbol, volatility in self.volatility_tracker.volatility_cache.items()
                        if volatility > self.config["volatility_threshold"]
                    ],
                },
            }

    def reset_stats(self) -> None:
        """Reset cache statistics"""
        with self.lock:
            self.cache_stats = {
                "price_hits": 0,
                "price_misses": 0,
                "user_data_hits": 0,
                "user_data_misses": 0,
                "invalidations": 0,
                "total_requests": 0,
            }


# Global cache instance
enhanced_cache = SmartCache()


# Global variables to track cache cleanup worker status
_cleanup_worker_thread = None
_cleanup_worker_running = False
_last_cleanup_time = None

def start_cache_cleanup_worker(app=None):
    """Start background worker to clean up expired cache entries including klines data"""
    global _cleanup_worker_thread, _cleanup_worker_running, _last_cleanup_time
    
    def cleanup_worker():
        global _cleanup_worker_running, _last_cleanup_time
        _cleanup_worker_running = True
        
        while True:
            try:
                # Clean up enhanced cache (price data, user data, etc.)
                enhanced_cache.cleanup_expired()
                _last_cleanup_time = datetime.utcnow()

                # Clean up klines cache data with proper app context
                if app:
                    try:
                        with app.app_context():
                            from .models import KlinesCache, SMCSignalCache
                            from config import RollingWindowConfig

                            # Clean up expired klines cache entries
                            klines_cleaned = KlinesCache.cleanup_expired()
                            if klines_cleaned > 0:
                                logging.info(
                                    f"Enhanced cache: cleaned up {klines_cleaned} expired klines cache entries"
                                )

                            # ROLLING WINDOW CLEANUP - Priority cleanup to maintain window sizes
                            rolling_window_results = KlinesCache.cleanup_all_rolling_windows(
                                batch_size=RollingWindowConfig.CLEANUP_BATCH_SIZE
                            )
                            if rolling_window_results["total_deleted"] > 0:
                                logging.info(
                                    f"Enhanced cache rolling window: deleted {rolling_window_results['total_deleted']} old candles "
                                    f"across {rolling_window_results['symbols_processed']} symbol/timeframe combinations"
                                )
                                
                                # Log details for transparency
                                for key, count in rolling_window_results["details"].items():
                                    logging.debug(f"Enhanced cache rolling window: {key} deleted {count} candles")

                            # Clean up old klines data beyond retention period (fallback)
                            old_klines_cleaned = KlinesCache.cleanup_old_data(
                                CacheConfig.KLINES_DATA_RETENTION_DAYS
                            )
                            if old_klines_cleaned > 0:
                                logging.info(
                                    f"Enhanced cache: cleaned up {old_klines_cleaned} old klines data entries (fallback cleanup)"
                                )

                            # Clean up expired SMC signals
                            smc_cleaned = SMCSignalCache.cleanup_expired()
                            if smc_cleaned > 0:
                                logging.info(
                                    f"Enhanced cache: cleaned up {smc_cleaned} expired SMC signal cache entries"
                                )
                            else:
                                logging.debug("No expired SMC signals to clean up")

                    except Exception as db_cleanup_error:
                        logging.error(
                            f"Database cache cleanup error: {db_cleanup_error}"
                        )
                else:
                    logging.debug(
                        "Database cache cleanup skipped (no app instance provided)"
                    )

                time.sleep(
                    CacheConfig.CLEANUP_INTERVAL
                )  # Clean up every 2 minutes by default

            except Exception as e:
                logging.error(f"Cache cleanup worker error: {e}")
                time.sleep(CacheConfig.CLEANUP_INTERVAL)

    import threading

    # Stop existing worker if running
    if _cleanup_worker_thread and _cleanup_worker_thread.is_alive():
        _cleanup_worker_running = False
        # Note: Daemon threads will stop when main program exits

    _cleanup_worker_thread = threading.Thread(target=cleanup_worker, daemon=True)
    _cleanup_worker_thread.start()
    logging.info("Cache cleanup worker started")

def restart_cache_cleanup_worker(app=None):
    """Restart the cache cleanup worker"""
    global _cleanup_worker_running
    logging.info("Restarting cache cleanup worker...")
    _cleanup_worker_running = False
    time.sleep(1)  # Allow current worker to stop
    start_cache_cleanup_worker(app)
    return True

def get_cache_cleanup_worker_status():
    """Get current cache cleanup worker status"""
    global _cleanup_worker_thread, _cleanup_worker_running, _last_cleanup_time
    
    is_alive = _cleanup_worker_thread and _cleanup_worker_thread.is_alive()
    
    return {
        'enabled': is_alive and _cleanup_worker_running,
        'thread_alive': is_alive,
        'last_cleanup': _last_cleanup_time.isoformat() if _last_cleanup_time else 'never',
        'worker_running_flag': _cleanup_worker_running
    }
