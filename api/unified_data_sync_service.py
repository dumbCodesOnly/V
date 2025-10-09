"""
Unified Data Synchronization Service for Trading Bot

This module combines cache cleanup and klines background workers into a single
coordinated service for better integration and synchronization:
- Smart caching with volatility-based invalidation
- Klines data management with real-time updates
- Coordinated cleanup and maintenance cycles
- Unified monitoring and status reporting
"""

import logging
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# Import configuration constants
try:
    from config import CacheConfig, CircuitBreakerConfig, RollingWindowConfig, SMCConfig, TimeConfig, TradingConfig
except ImportError:
    import os
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import CacheConfig, CircuitBreakerConfig, RollingWindowConfig, SMCConfig, TimeConfig, TradingConfig

from .circuit_breaker import circuit_manager, with_circuit_breaker


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
        volatility = self.volatility_tracker.get_volatility(symbol)

        with self.lock:
            cache_entry = self._create_cache_entry(
                data={"price": price, "source": source},
                ttl_seconds=dynamic_ttl,
                metadata={
                    "volatility": volatility,
                    "dynamic_ttl": dynamic_ttl,
                },
            )
            self.price_cache[symbol] = cache_entry
            
            # Debug log for cache operations with volatility info
            logging.debug(f"Cached price {symbol}: ${price:.4f} (source: {source}, TTL: {dynamic_ttl}s, volatility: {volatility:.2f}%)")

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
        cleanup_details = {"prices": 0, "trade_configs": 0, "credentials": 0, "preferences": 0}

        with self.lock:
            # Clean price cache
            expired_prices = []
            for symbol, entry in self.price_cache.items():
                try:
                    # Ensure timestamp is a datetime object
                    timestamp = entry["timestamp"]
                    if isinstance(timestamp, str):
                        timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    elif timestamp is None:
                        continue  # Skip entries with no timestamp
                    
                    if (current_time - timestamp).total_seconds() >= entry["ttl"]:
                        expired_prices.append(symbol)
                except (TypeError, ValueError, KeyError) as e:
                    logging.warning(f"Error processing timestamp for symbol {symbol}: {e}, removing entry")
                    expired_prices.append(symbol)  # Remove problematic entries
            for symbol in expired_prices:
                del self.price_cache[symbol]
                removed_count += 1
                cleanup_details["prices"] += 1

            # Clean user data caches
            cache_names = ["trade_configs", "credentials", "preferences"]
            cache_dicts = [
                self.user_trade_configs_cache,
                self.user_credentials_cache,
                self.user_preferences_cache,
            ]
            
            for cache_name, cache_dict in zip(cache_names, cache_dicts):
                expired_keys = []
                for key, entry in cache_dict.items():
                    try:
                        # Ensure timestamp is a datetime object
                        timestamp = entry["timestamp"]
                        if isinstance(timestamp, str):
                            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        elif timestamp is None:
                            continue  # Skip entries with no timestamp
                        
                        if (current_time - timestamp).total_seconds() >= entry["ttl"]:
                            expired_keys.append(key)
                    except (TypeError, ValueError, KeyError) as e:
                        logging.warning(f"Error processing timestamp for {cache_name} cache key {key}: {e}, removing entry")
                        expired_keys.append(key)  # Remove problematic entries
                for key in expired_keys:
                    del cache_dict[key]
                    removed_count += 1
                    cleanup_details[cache_name] += 1

        if removed_count > 0:
            logging.debug(f"Cache cleanup: removed {removed_count} expired entries - prices: {cleanup_details['prices']}, configs: {cleanup_details['trade_configs']}, creds: {cleanup_details['credentials']}, prefs: {cleanup_details['preferences']}")

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

            # Safely get cache sizes with error handling
            def safe_cache_len(cache_obj, cache_name="unknown"):
                try:
                    if cache_obj is None:
                        return 0
                    return len(cache_obj) if hasattr(cache_obj, '__len__') else 0
                except (TypeError, AttributeError):
                    logging.warning(f"Error getting length of {cache_name} cache, returning 0")
                    return 0

            return {
                "hit_rate": round(hit_rate, 2),
                "total_requests": self.cache_stats["total_requests"],
                "cache_sizes": {
                    "prices": safe_cache_len(self.price_cache, "prices"),
                    "user_trade_configs": safe_cache_len(self.user_trade_configs_cache, "user_trade_configs"),
                    "user_credentials": safe_cache_len(self.user_credentials_cache, "user_credentials"),
                    "user_preferences": safe_cache_len(self.user_preferences_cache, "user_preferences"),
                },
                "detailed_stats": self.cache_stats.copy(),
                "volatility_tracking": {
                    "symbols_tracked": safe_cache_len(getattr(self.volatility_tracker, 'volatility_cache', None), "volatility_tracker"),
                    "high_volatility_symbols": [
                        symbol
                        for symbol, volatility in getattr(self.volatility_tracker, 'volatility_cache', {}).items()
                        if volatility > self.config.get("volatility_threshold", 2.0)
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


class UnifiedDataSyncService:
    """
    Unified service combining cache cleanup and klines background workers
    """

    def __init__(self, app=None):
        """Initialize the unified data sync service"""
        self.app = app
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        
        # Initialize smart cache
        self.cache = SmartCache()
        
        # Klines management
        self.timeframes = {
            "15m": 60,   # Update every 1 minute for fast execution
            "1h": 120,   # Update every 2 minutes for live tracking
            "4h": 300,   # Update every 5 minutes  
            "1d": 900    # Update every 15 minutes for daily candles
        }
        
        # Track last update times per symbol/timeframe
        self.last_klines_updates: Dict[str, Dict[str, datetime]] = {}
        self.last_cache_cleanup: Optional[datetime] = None
        
        # Track gap fill failures for exponential backoff
        self._gap_fill_failures = {}
        
        logging.info("Unified data sync service initialized")

    @with_circuit_breaker("binance_klines_bulk_api", failure_threshold=CircuitBreakerConfig.BINANCE_FAILURE_THRESHOLD, recovery_timeout=CircuitBreakerConfig.BINANCE_RECOVERY_TIMEOUT, success_threshold=2)
    def _fetch_binance_klines(self, symbol: str, interval: str, limit: int = 1000) -> List[Dict]:
        """
        Fetch klines data from Binance API with circuit breaker protection
        Updated for extended 4H candle fetches (200 candles) - uses config values: 15 failures, 240s timeout
        
        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            interval: Timeframe ('1h', '4h', '1d')
            limit: Number of candles to fetch (max 1000)
            
        Returns:
            List of klines data in OHLCV format
        """
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000)  # Binance API limit
        }
        
        response = requests.get(url, params=params, timeout=TimeConfig.PRICE_API_TIMEOUT)
        response.raise_for_status()
        
        klines_raw = response.json()
        
        # Convert to standardized format
        klines = []
        for kline in klines_raw:
            # Handle potential type mismatch for timestamp
            try:
                timestamp_value = float(kline[0]) if isinstance(kline[0], str) else kline[0]
            except (ValueError, TypeError):
                logging.warning(f"Invalid timestamp format in kline data: {kline[0]}")
                continue
                
            klines.append({
                "timestamp": datetime.fromtimestamp(timestamp_value / 1000, tz=timezone.utc),
                "open": float(kline[1]),
                "high": float(kline[2]), 
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5])
            })
            
        logging.debug(f"Fetched {len(klines)} klines for {symbol} {interval} from Binance")
        
        # Update volatility tracker with latest price for cache optimization
        if klines:
            latest_price = klines[-1]["close"]
            self.cache.volatility_tracker.add_price(symbol, latest_price)
            current_volatility = self.cache.volatility_tracker.get_volatility(symbol)
            logging.debug(f"Updated volatility for {symbol}: {current_volatility:.2f}% (price: ${latest_price:.2f})")
            
        return klines

    @with_circuit_breaker("binance_klines_gap_fill_api", failure_threshold=CircuitBreakerConfig.BINANCE_FAILURE_THRESHOLD, recovery_timeout=CircuitBreakerConfig.BINANCE_RECOVERY_TIMEOUT, success_threshold=3)
    def _fetch_binance_klines_gap_fill(self, symbol: str, interval: str, limit: int = 10) -> List[Dict]:
        """
        Fetch klines data from Binance API specifically for gap filling with more conservative circuit breaker
        Updated for extended 4H candle fetches (200 candles) - uses config values: 15 failures, 240s timeout
        
        This method uses separate circuit breaker settings optimized for incremental updates:
        - Higher failure threshold (15) to handle extended 4H data fetches
        - Longer recovery timeout (240s / 4 min) to avoid rapid retry cycles  
        - Smaller limit (max 10) for targeted gap fills
        
        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            interval: Timeframe ('1h', '4h', '1d')
            limit: Number of candles to fetch (max 10 for gap fills)
            
        Returns:
            List of klines data in OHLCV format
        """
        # Add delay before gap fill requests to respect rate limits
        time.sleep(TimeConfig.BINANCE_KLINES_DELAY)
        
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 10)  # Conservative limit for gap fills
        }
        
        response = requests.get(url, params=params, timeout=TimeConfig.PRICE_API_TIMEOUT)
        response.raise_for_status()
        
        klines_raw = response.json()
        
        # Convert to standardized format
        klines = []
        for kline in klines_raw:
            # Handle potential type mismatch for timestamp
            try:
                timestamp_value = float(kline[0]) if isinstance(kline[0], str) else kline[0]
            except (ValueError, TypeError):
                logging.warning(f"Invalid timestamp format in kline data: {kline[0]}")
                continue
                
            klines.append({
                "timestamp": datetime.fromtimestamp(timestamp_value / 1000, tz=timezone.utc),
                "open": float(kline[1]),
                "high": float(kline[2]), 
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5])
            })
            
        logging.debug(f"Gap fill: Fetched {len(klines)} klines for {symbol} {interval} from Binance")
        
        # Update volatility tracker with latest price for cache optimization
        if klines:
            latest_price = klines[-1]["close"]
            self.cache.volatility_tracker.add_price(symbol, latest_price)
        
        return klines

    def _get_required_initial_candles(self, timeframe: str) -> int:
        """Calculate how many initial candles we need for each timeframe"""
        # Based on SMC analysis requirements and trading needs
        if timeframe == "15m":
            return SMCConfig.TIMEFRAME_15M_LIMIT  # 400 candles (~4 days)
        elif timeframe == "1h":
            return SMCConfig.TIMEFRAME_1H_LIMIT  # 300 candles (~12.5 days)
        elif timeframe == "4h": 
            return SMCConfig.TIMEFRAME_4H_LIMIT  # 100 candles (~16 days)
        elif timeframe == "1d":
            return SMCConfig.TIMEFRAME_1D_LIMIT  # 50 candles (~7 weeks)
        else:
            return 100  # Default fallback

    def _should_update_timeframe(self, symbol: str, timeframe: str) -> bool:
        """
        Determine if a specific symbol/timeframe combination needs updating
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe to check
            
        Returns:
            True if update is needed, False otherwise
        """
        update_interval = self.timeframes.get(timeframe, 300)
        
        # Check if we have last update time tracked
        if symbol not in self.last_klines_updates:
            self.last_klines_updates[symbol] = {}
            
        if timeframe not in self.last_klines_updates[symbol]:
            logging.debug(f"Update needed for {symbol} {timeframe}: never updated before")
            return True  # Never updated before
            
        last_update = self.last_klines_updates[symbol][timeframe]
        try:
            from .models import get_utc_now
            time_since_update = (get_utc_now() - last_update).total_seconds()
        except ImportError:
            time_since_update = (datetime.utcnow() - last_update).total_seconds()
        
        needs_update = time_since_update >= update_interval
        
        if needs_update:
            logging.debug(f"Update needed for {symbol} {timeframe}: {time_since_update:.1f}s since last update (interval: {update_interval}s)")
        else:
            logging.debug(f"Update not needed for {symbol} {timeframe}: {time_since_update:.1f}s < {update_interval}s interval")
            
        return needs_update

    def _get_existing_data_info(self, symbol: str, timeframe: str) -> Dict:
        """
        Get information about existing cached data for a symbol/timeframe
        
        Returns:
            Dict with keys: count, oldest_timestamp, newest_timestamp, needs_initial_population, has_recent_data
        """
        try:
            # Skip database operations if no app context available
            if not self.app:
                logging.debug(f"No app context available for {symbol} {timeframe} data check")
                return {
                    "count": 0,
                    "oldest_timestamp": None,
                    "newest_timestamp": None,
                    "needs_initial_population": True,
                    "has_recent_data": False
                }
                
            # Get existing cached data with proper app context
            with self.app.app_context():
                from .models import KlinesCache, get_utc_now
                
                # Check for recent data (last 48 hours worth)
                recent_limit = 48 if timeframe == "1h" else 12 if timeframe == "4h" else 2  # Last 48h worth
                recent_data = KlinesCache.get_cached_data(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=recent_limit,
                    include_incomplete=True
                )
                
                # Check total data count with a reasonable sample
                total_data = KlinesCache.get_cached_data(
                    symbol=symbol,
                    timeframe=timeframe, 
                    limit=200,  # Sample to check if we have substantial data
                    include_incomplete=True
                )
                
                if not total_data:
                    return {
                        "count": 0,
                        "oldest_timestamp": None,
                        "newest_timestamp": None,
                        "needs_initial_population": True,
                        "has_recent_data": False
                    }
                    
                timestamps = [candle["timestamp"] for candle in total_data]
                timestamps.sort()
                
                # Determine if we need initial population based on data age and completeness
                required_candles = self._get_required_initial_candles(timeframe)
                current_time = get_utc_now()
                
                # Check if we have reasonable historical coverage
                coverage_ratio = len(total_data) / required_candles if required_candles > 0 else 0
                
                if len(total_data) >= required_candles * 0.7:  # 70% coverage is sufficient
                    needs_initial = False
                    logging.debug(f"{symbol} {timeframe} has sufficient data: {len(total_data)}/{required_candles} candles ({coverage_ratio:.1%} coverage)")
                else:
                    # Check if newest data is very old (more than 1 day old)
                    newest_time = timestamps[-1] if timestamps else None
                    if newest_time:
                        # Ensure newest_time is timezone-aware for comparison
                        from api.models import normalize_to_utc
                        newest_time_utc = normalize_to_utc(newest_time)
                        age_hours = (current_time - newest_time_utc).total_seconds() / 3600
                        needs_initial = age_hours > 24  # If data older than 24h, do initial population
                        if needs_initial:
                            logging.debug(f"{symbol} {timeframe} needs initial population: newest data is {age_hours:.1f}h old (insufficient coverage: {coverage_ratio:.1%})")
                        else:
                            logging.debug(f"{symbol} {timeframe} recent data is {age_hours:.1f}h old, coverage {coverage_ratio:.1%} - no initial population needed")
                    else:
                        needs_initial = True
                        logging.debug(f"{symbol} {timeframe} needs initial population: no existing data found")
                
                result = {
                    "count": len(total_data),
                    "oldest_timestamp": timestamps[0] if timestamps else None,
                    "newest_timestamp": timestamps[-1] if timestamps else None, 
                    "needs_initial_population": needs_initial,
                    "has_recent_data": len(recent_data) > 0
                }
                
                logging.debug(f"{symbol} {timeframe} data analysis: {len(total_data)} total candles, {len(recent_data)} recent, needs_initial={needs_initial}")
                return result
            
        except Exception as e:
            logging.warning(f"Error checking existing data for {symbol} {timeframe}: {e}")
            return {
                "count": 0,
                "oldest_timestamp": None,
                "newest_timestamp": None,
                "needs_initial_population": True,
                "has_recent_data": False
            }

    def _populate_initial_data(self, symbol: str, timeframe: str) -> bool:
        """
        Populate initial historical data for a symbol/timeframe combination
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe to populate
            
        Returns:
            True if successful, False otherwise
        """
        try:
            required_candles = self._get_required_initial_candles(timeframe)
            
            logging.info(f"Populating initial data: {symbol} {timeframe} ({required_candles} candles)")
            
            # Fetch initial data with primary source (Binance)
            try:
                klines_data = self._fetch_binance_klines(symbol, timeframe, required_candles)
            except Exception as e:
                logging.warning(f"Binance API failed for {symbol} {timeframe}: {e}")
                # Wait before marking as failed to avoid rapid retries
                time.sleep(TimeConfig.API_RETRY_DELAY)
                logging.info(f"Skipping {symbol} {timeframe} - will retry in next cycle when Binance recovers")
                return False
            
            if not klines_data:
                logging.warning(f"No data received for {symbol} {timeframe}")
                return False
                
            # Calculate appropriate TTL based on timeframe
            # Use shorter TTL for initial population to ensure open candles update frequently
            if timeframe == "1h":
                ttl_minutes = 3  # Short TTL for frequent updates of open candles
            elif timeframe == "4h":
                ttl_minutes = 8  # Medium TTL for 4h open candles
            elif timeframe == "1d":
                ttl_minutes = 20  # Longer TTL for daily open candles
            else:
                ttl_minutes = 5  # Default short TTL
                
            # Save to database in batches for efficiency
            if not self.app:
                logging.warning(f"No app context available for saving {symbol} {timeframe} data")
                return False
            
            with self.app.app_context():
                from .models import KlinesCache, get_utc_now
                saved_count = KlinesCache.save_klines_batch(
                    symbol=symbol,
                    timeframe=timeframe,
                    candlesticks=klines_data,
                    cache_ttl_minutes=ttl_minutes
                )
            
            print(f"[RENDER-KLINES] Successfully populated {saved_count} candles for {symbol} {timeframe}")
            logging.info(f"[RENDER-KLINES] Successfully populated {saved_count} candles for {symbol} {timeframe}")
            
            # Update tracking
            with self.lock:
                if symbol not in self.last_klines_updates:
                    self.last_klines_updates[symbol] = {}
                try:
                    from .models import get_utc_now
                    self.last_klines_updates[symbol][timeframe] = get_utc_now()
                except ImportError:
                    self.last_klines_updates[symbol][timeframe] = datetime.utcnow()
                
            return True
            
        except Exception as e:
            logging.error(f"Error populating initial data for {symbol} {timeframe}: {e}")
            required_candles = "unknown"  # Initialize for debug logging
            logging.debug(f"Initial population error context: required_candles={required_candles}, app_context={self.app is not None}")
            return False

    def _update_recent_data(self, symbol: str, timeframe: str) -> bool:
        """
        EFFICIENT: Update only the current open candle in place instead of fetching multiple candles
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe to update
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.app:
                logging.warning(f"No app context available for updating {symbol} {timeframe}")
                return False
                
            with self.app.app_context():
                from .models import KlinesCache, get_utc_now
                
                # Check if open candle already exists for current period
                existing_open_candle = KlinesCache.get_current_open_candle(symbol, timeframe)
                
                # Fetch ONLY the current open candle (1 API call instead of multiple)
                try:
                    # Use dedicated circuit breaker for gap fills (more conservative)
                    current_klines = self._fetch_binance_klines_gap_fill(symbol, timeframe, 1)  # Only fetch 1 candle
                except Exception as e:
                    logging.warning(f"Open candle update failed for {symbol} {timeframe}: {e}")
                    # Use proper klines delay for gap fills (more conservative than generic retry delay)
                    time.sleep(TimeConfig.BINANCE_KLINES_DELAY)
                    return False
                    
                if not current_klines:
                    return False
                
                # Get the most recent candle (current open candle)
                current_candle = current_klines[0]
                
                # Calculate appropriate TTL for open candle
                if timeframe == "1h":
                    ttl_minutes = 2  # Very short TTL for hourly open candles
                elif timeframe == "4h":
                    ttl_minutes = 5  # Short TTL for 4h open candles
                elif timeframe == "1d":
                    ttl_minutes = 15  # Medium TTL for daily open candles
                else:
                    ttl_minutes = 3  # Default short TTL
                    
                # Use efficient in-place update method
                success = KlinesCache.update_open_candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_price=current_candle["open"],
                    high=current_candle["high"],
                    low=current_candle["low"],
                    close=current_candle["close"],
                    volume=current_candle["volume"],
                    timestamp=current_candle["timestamp"],
                    cache_ttl_minutes=ttl_minutes
                )
                
                if success:
                    if existing_open_candle:
                        logging.debug(f"Updated existing open candle for {symbol} {timeframe}: C:{current_candle['close']}")
                    else:
                        logging.debug(f"Created new open candle for {symbol} {timeframe}: C:{current_candle['close']}")
                    
                    # Reset failure tracking on success
                    self._record_gap_fill_success(symbol, timeframe)
                else:
                    logging.warning(f"Failed to update open candle for {symbol} {timeframe}")
                    self._record_gap_fill_failure(symbol, timeframe)
                    return False
            
            # Update tracking
            with self.lock:
                if symbol not in self.last_klines_updates:
                    self.last_klines_updates[symbol] = {}
                try:
                    from .models import get_utc_now
                    self.last_klines_updates[symbol][timeframe] = get_utc_now()
                except ImportError:
                    self.last_klines_updates[symbol][timeframe] = datetime.utcnow()
                
            return True
            
        except Exception as e:
            logging.error(f"Error updating open candle for {symbol} {timeframe}: {e}")
            self._record_gap_fill_failure(symbol, timeframe)
            
            # Apply exponential backoff delay for this specific symbol/timeframe
            delay = self._get_gap_fill_delay(symbol, timeframe)
            logging.warning(f"Gap fill failure #{self._gap_fill_failures.get(f'{symbol}_{timeframe}', 0)} for {symbol} {timeframe}, waiting {delay:.1f}s before retry")
            time.sleep(delay)
            
            existing_open_candle = None  # Initialize for debug logging
            logging.debug(f"Open candle update error context: app_context={self.app is not None}, existing_candle={existing_open_candle is not None}")
            return False
    
    def _get_gap_fill_delay(self, symbol: str, timeframe: str) -> float:
        """Calculate exponential backoff delay for gap fill failures"""
        key = f"{symbol}_{timeframe}"
        failure_count = self._gap_fill_failures.get(key, 0)
        
        if failure_count == 0:
            return TimeConfig.BINANCE_KLINES_DELAY
        
        # Exponential backoff: 2s, 5s, 12s, 30s, 60s (max)
        delay = min(TimeConfig.BINANCE_KLINES_DELAY * (TimeConfig.API_BACKOFF_MULTIPLIER ** failure_count), 60)
        return delay
    
    def _record_gap_fill_success(self, symbol: str, timeframe: str):
        """Reset failure count on successful gap fill"""
        key = f"{symbol}_{timeframe}"
        if key in self._gap_fill_failures:
            logging.debug(f"Gap fill recovered for {symbol} {timeframe} after {self._gap_fill_failures[key]} failures")
            self._gap_fill_failures.pop(key, None)
    
    def _record_gap_fill_failure(self, symbol: str, timeframe: str):
        """Track gap fill failure for exponential backoff"""
        key = f"{symbol}_{timeframe}"
        self._gap_fill_failures[key] = self._gap_fill_failures.get(key, 0) + 1
        max_failures = 8  # Reset after 8 failures to prevent permanent blocking
        if self._gap_fill_failures[key] > max_failures:
            logging.warning(f"Resetting gap fill failure count for {symbol} {timeframe} (was {self._gap_fill_failures[key]})")
            self._gap_fill_failures[key] = max_failures // 2

    def _cleanup_klines_data(self):
        """Enhanced cleanup of old klines data with rolling window management"""
        cleanup_start_time = time.time()
        logging.info("CLEANUP_DEBUG: Starting klines data cleanup process")
        
        try:
            # Skip cleanup if no app context available
            if not self.app:
                logging.warning("CLEANUP_DEBUG: Skipping klines cleanup - no app context available")
                return
                
            logging.debug("CLEANUP_DEBUG: App context available, proceeding with cleanup")
            
            # Need Flask app context for database operations
            with self.app.app_context():
                from .models import KlinesCache, SMCSignalCache
                logging.debug("CLEANUP_DEBUG: Successfully imported models and entered app context")
                
                # First, check current database state
                try:
                    total_candles_before = 0
                    candles_by_timeframe = {}
                    
                    # Get total count before cleanup
                    from sqlalchemy import text
                    from api.models import db
                    
                    result = db.session.execute(text("SELECT COUNT(*) FROM klines_cache"))
                    total_candles_before = result.scalar()
                    
                    # Get breakdown by timeframe
                    result = db.session.execute(text("SELECT timeframe, COUNT(*) FROM klines_cache GROUP BY timeframe"))
                    candles_by_timeframe = dict(result.fetchall())
                    
                    logging.info(f"CLEANUP_DEBUG: Database state before cleanup - Total candles: {total_candles_before}")
                    for timeframe, count in candles_by_timeframe.items():
                        logging.info(f"CLEANUP_DEBUG: {timeframe}: {count} candles")
                        
                except Exception as e:
                    logging.error(f"CLEANUP_DEBUG: Error checking database state: {e}")
                    total_candles_before = -1
                
                # Clean up expired cache entries (small batches to avoid locks)
                logging.debug("CLEANUP_DEBUG: Starting expired cache cleanup")
                try:
                    expired_count = KlinesCache.cleanup_expired()
                    if expired_count > 0:
                        logging.info(f"CLEANUP_DEBUG: Cleaned up {expired_count} expired klines cache entries")
                    else:
                        logging.debug("CLEANUP_DEBUG: No expired cache entries found")
                except Exception as e:
                    logging.error(f"CLEANUP_DEBUG: Error in expired cache cleanup: {e}")
                    
                # ROLLING WINDOW CLEANUP - NEW FEATURE
                # This maintains the configured window size for each timeframe
                logging.debug("CLEANUP_DEBUG: Starting rolling window cleanup")
                try:
                    # Check if the cleanup method exists
                    if not hasattr(KlinesCache, 'cleanup_all_rolling_windows'):
                        logging.error("CLEANUP_DEBUG: KlinesCache.cleanup_all_rolling_windows method not found!")
                        rolling_window_results = {"total_deleted": 0, "symbols_processed": 0, "details": {}}
                    else:
                        logging.debug(f"CLEANUP_DEBUG: Calling rolling window cleanup with batch_size={RollingWindowConfig.CLEANUP_BATCH_SIZE}")
                        rolling_window_results = KlinesCache.cleanup_all_rolling_windows(
                            batch_size=RollingWindowConfig.CLEANUP_BATCH_SIZE
                        )
                        logging.debug(f"CLEANUP_DEBUG: Rolling window cleanup completed, results: {rolling_window_results}")
                        
                    if rolling_window_results["total_deleted"] > 0:
                        logging.info(f"CLEANUP_DEBUG: Rolling window cleanup: deleted {rolling_window_results['total_deleted']} old candles across {rolling_window_results['symbols_processed']} symbol/timeframe combinations")
                        
                        # Log details for each symbol/timeframe that had cleanup
                        for key, count in rolling_window_results["details"].items():
                            logging.info(f"CLEANUP_DEBUG: Rolling window: {key} deleted {count} candles")
                    else:
                        logging.debug("CLEANUP_DEBUG: Rolling window cleanup: no candles deleted")
                        
                except Exception as e:
                    logging.error(f"CLEANUP_DEBUG: Error in rolling window cleanup: {e}")
                    import traceback
                    logging.error(f"CLEANUP_DEBUG: Rolling window cleanup traceback: {traceback.format_exc()}")
                    
                # Traditional cleanup of very old data as fallback
                logging.debug("CLEANUP_DEBUG: Starting fallback old data cleanup")
                try:
                    old_count = KlinesCache.cleanup_old_data(days_to_keep=CacheConfig.KLINES_DATA_RETENTION_DAYS)
                    if old_count > 0:
                        logging.info(f"CLEANUP_DEBUG: Fallback cleanup: {old_count} old klines entries beyond {CacheConfig.KLINES_DATA_RETENTION_DAYS} day retention")
                    else:
                        logging.debug(f"CLEANUP_DEBUG: Fallback cleanup: no entries older than {CacheConfig.KLINES_DATA_RETENTION_DAYS} days found")
                except Exception as e:
                    logging.error(f"CLEANUP_DEBUG: Error in fallback cleanup: {e}")

                # Clean up expired SMC signals
                logging.debug("CLEANUP_DEBUG: Starting SMC signal cleanup")
                try:
                    smc_cleaned = SMCSignalCache.cleanup_expired()
                    if smc_cleaned > 0:
                        logging.info(f"CLEANUP_DEBUG: Cleaned up {smc_cleaned} expired SMC signal cache entries")
                    else:
                        logging.debug("CLEANUP_DEBUG: No expired SMC signals found")
                except Exception as e:
                    logging.error(f"CLEANUP_DEBUG: Error in SMC cleanup: {e}")
                
                # Check database state after cleanup
                try:
                    result = db.session.execute(text("SELECT COUNT(*) FROM klines_cache"))
                    total_candles_after = result.scalar()
                    
                    result = db.session.execute(text("SELECT timeframe, COUNT(*) FROM klines_cache GROUP BY timeframe"))
                    candles_after = dict(result.fetchall())
                    
                    cleanup_duration = time.time() - cleanup_start_time
                    candles_removed = total_candles_before - total_candles_after if total_candles_before >= 0 else -1
                    
                    logging.info(f"CLEANUP_DEBUG: Database state after cleanup - Total candles: {total_candles_after} (removed: {candles_removed})")
                    for timeframe, count in candles_after.items():
                        before_count = candles_by_timeframe.get(timeframe, 0)
                        removed = before_count - count
                        logging.info(f"CLEANUP_DEBUG: {timeframe}: {count} candles (removed: {removed})")
                    
                    logging.info(f"CLEANUP_DEBUG: Cleanup completed in {cleanup_duration:.2f} seconds")
                    
                except Exception as e:
                    logging.error(f"CLEANUP_DEBUG: Error checking database state after cleanup: {e}")
                
        except Exception as e:
            cleanup_duration = time.time() - cleanup_start_time
            logging.error(f"CLEANUP_DEBUG: Error during klines data cleanup after {cleanup_duration:.2f}s: {e}")
            import traceback
            logging.error(f"CLEANUP_DEBUG: Cleanup error traceback: {traceback.format_exc()}")

    def _cleanup_cache_data(self) -> int:
        """Clean up expired cache entries and return count"""
        try:
            # Clean up enhanced cache (price data, user data, etc.)
            removed_count = self.cache.cleanup_expired()
            self.last_cache_cleanup = datetime.utcnow()
            return removed_count
        except Exception as e:
            logging.error(f"Error during cache cleanup: {e}")
            return 0

    def _check_for_gaps_before_cleanup(self, symbols: "List[str]") -> None:
        """Check for gaps before cleanup to establish baseline"""
        try:
            if not self.app:
                return
                
            with self.app.app_context():
                from .models import KlinesCache
                
                # Quick gap check for 1h timeframe (most sensitive to issues)
                for symbol in symbols[:2]:  # Check first 2 symbols to avoid too much overhead
                    try:
                        gap_analysis = KlinesCache.detect_gaps(symbol, "1h", days_back=2)
                        
                        if gap_analysis.get("total_gaps", 0) > 0:
                            logging.warning(f"GAP_MONITOR: {symbol}:1h has {gap_analysis['total_gaps']} gaps BEFORE cleanup - largest: {gap_analysis.get('largest_gap_hours', 0):.1f}h")
                        else:
                            logging.debug(f"GAP_MONITOR: {symbol}:1h no gaps detected before cleanup")
                            
                    except Exception as e:
                        logging.debug(f"Gap check error for {symbol}: {e}")
                        
        except Exception as e:
            logging.error(f"Error in gap check before cleanup: {e}")

    def _check_for_gaps_after_cleanup(self, symbols: "List[str]") -> None:
        """Check for gaps after cleanup to detect if cleanup caused issues"""
        try:
            if not self.app:
                return
                
            with self.app.app_context():
                from .models import KlinesCache
                
                # Quick gap check for 1h timeframe (most sensitive to issues)
                gaps_detected = []
                
                for symbol in symbols[:2]:  # Check first 2 symbols to avoid too much overhead
                    try:
                        gap_analysis = KlinesCache.detect_gaps(symbol, "1h", days_back=2)
                        
                        if gap_analysis.get("total_gaps", 0) > 0:
                            gaps_detected.append(f"{symbol}:{gap_analysis['total_gaps']} gaps")
                            logging.warning(f"GAP_MONITOR: {symbol}:1h has {gap_analysis['total_gaps']} gaps AFTER cleanup - largest: {gap_analysis.get('largest_gap_hours', 0):.1f}h")
                            
                            # Log recent gaps for investigation
                            for gap in gap_analysis.get("gaps", [])[-2:]:  # Show last 2 gaps
                                logging.warning(f"GAP_DETAIL: Recent gap in {symbol}:1h from {gap.get('start_time', 'unknown')} to {gap.get('end_time', 'unknown')} ({gap.get('duration_hours', 0):.1f}h, {gap.get('missing_candles', 0)} candles)")
                        else:
                            logging.debug(f"GAP_MONITOR: {symbol}:1h no gaps detected after cleanup")
                            
                    except Exception as e:
                        logging.debug(f"Gap check error for {symbol}: {e}")
                
                if gaps_detected:
                    logging.error(f"GAP_ALERT: Data gaps detected after cleanup! {', '.join(gaps_detected)} - This may indicate cleanup is deleting valid data")
                        
        except Exception as e:
            logging.error(f"Error in gap check after cleanup: {e}")

    def _run_coordinated_sync_cycle(self):
        """Execute one complete coordinated sync cycle"""
        try:
            start_time = time.time()
            
            # Initialize variables at function level to avoid unbound variable issues
            data_info = {"needs_initial_population": False}
            existing_open_candle = None
            
            # Get list of supported symbols
            symbols = TradingConfig.SUPPORTED_SYMBOLS
            total_klines_tasks = len(symbols) * len(self.timeframes)
            completed_klines_tasks = 0
            successful_fetches = 0  # Track successful data fetches
            
            logging.debug(f"Starting unified sync cycle for {len(symbols)} symbols, {len(self.timeframes)} timeframes")
            
            # Phase 1: Update klines data (coordinated with volatility tracking)
            for symbol in symbols:
                if self.stop_event.is_set():
                    break
                    
                for timeframe in self.timeframes.keys():
                    if self.stop_event.is_set():
                        break
                        
                    try:
                        # Initialize variables for safe error handling
                        data_info = {"needs_initial_population": False}
                        success = False  # Initialize success flag
                        
                        # Check if this timeframe needs updating
                        if not self._should_update_timeframe(symbol, timeframe):
                            completed_klines_tasks += 1
                            continue
                            
                        # Get existing data information
                        data_info = self._get_existing_data_info(symbol, timeframe)
                        
                        # Check if we just need to update current open candle (most efficient)
                        existing_open_candle = None  # Initialize variable
                        if not data_info["needs_initial_population"]:
                            try:
                                if self.app:  # Check app exists before using context
                                    with self.app.app_context():
                                        from .models import KlinesCache
                                        existing_open_candle = KlinesCache.get_current_open_candle(symbol, timeframe)
                            except Exception:
                                pass
                        
                        # SMART DECISION: Choose most efficient update method
                        if data_info["needs_initial_population"]:
                            # Initial population: Get full historical data (happens once per symbol/timeframe)
                            logging.info(f"STRATEGY: Initial population chosen for {symbol} {timeframe} (missing historical data)")
                            success = self._populate_initial_data(symbol, timeframe)
                        elif existing_open_candle:
                            # MOST EFFICIENT: Just update the existing open candle in place
                            logging.debug(f"STRATEGY: Efficient open candle update for {symbol} {timeframe} (existing open candle found)")
                            success = self._update_recent_data(symbol, timeframe)
                        else:
                            # No open candle exists, might need to create it or fetch recent data
                            logging.debug(f"STRATEGY: Incremental update for {symbol} {timeframe} (no open candle, creating new)")
                            success = self._update_recent_data(symbol, timeframe)
                            
                        if success:
                            successful_fetches += 1
                            logging.debug(f"Successfully processed {symbol} {timeframe}")
                        else:
                            logging.warning(f"Failed to process {symbol} {timeframe}")
                            
                    except Exception as e:
                        logging.error(f"Error processing {symbol} {timeframe}: {e}")
                        success = False
                        
                    completed_klines_tasks += 1
                    
                    # Proper delay between requests to respect API rate limits
                    time.sleep(TimeConfig.BINANCE_KLINES_DELAY)
                    
                    # Variable delay based on operation type  
                    try:
                        if data_info.get("needs_initial_population", False):
                            time.sleep(5.0)  # Extra 5 seconds for initial population/gap fill (heavy bulk operation - prevent rate limits)
                        elif existing_open_candle:
                            time.sleep(1.5)  # Conservative delay for open candle updates to prevent rate limiting
                    except (NameError, UnboundLocalError):
                        pass  # Variables may not be defined in error scenarios

            # Phase 2: COORDINATED cleanup after data updates
            # FIXED: Only run cleanup if we had successful data fetches to prevent data loss
            if successful_fetches > 0:
                logging.info(f"SYNC_DEBUG: Starting cleanup phase after {successful_fetches} successful data updates out of {completed_klines_tasks} attempts")
                
                # NEW: Check for gaps before cleanup to detect issues
                self._check_for_gaps_before_cleanup(symbols)
                
                logging.debug("SYNC_DEBUG: Calling klines data cleanup")
                self._cleanup_klines_data()  # Database cleanup
                
                logging.debug("SYNC_DEBUG: Calling cache data cleanup")
                cache_removed = self._cleanup_cache_data()  # Memory cache cleanup
                
                # NEW: Check for gaps after cleanup to detect if cleanup caused issues
                self._check_for_gaps_after_cleanup(symbols)
                
                logging.debug(f"SYNC_DEBUG: Cleanup phase completed - cache entries removed: {cache_removed}")
            else:
                logging.warning(f"SYNC_DEBUG: Skipping cleanup phase - no successful data updates (all {completed_klines_tasks} attempts failed). This prevents data loss when API is unavailable.")
                cache_removed = 0
            
            cycle_time = time.time() - start_time
            # Log cycle performance and efficiency metrics
            task_efficiency = (completed_klines_tasks / total_klines_tasks * 100) if total_klines_tasks > 0 else 0
            fetch_efficiency = (successful_fetches / completed_klines_tasks * 100) if completed_klines_tasks > 0 else 0
            tasks_per_second = completed_klines_tasks / cycle_time if cycle_time > 0 else 0
            
            logging.info(f"Unified sync cycle completed: {successful_fetches} successful fetches out of {completed_klines_tasks}/{total_klines_tasks} klines tasks (task: {task_efficiency:.1f}%, fetch: {fetch_efficiency:.1f}%), {cache_removed} cache entries cleaned in {cycle_time:.1f}s ({tasks_per_second:.1f} tasks/s)")
            
            # Log circuit breaker status if any are tripped (using safe attribute access)
            tripped_breakers = []
            try:
                for name, breaker in circuit_manager._breakers.items():
                    # Use getattr for safe access to state attribute
                    state = getattr(breaker, 'state', 'UNKNOWN')
                    if state != 'CLOSED':
                        tripped_breakers.append(name)
            except Exception:
                pass  # Safely handle any circuit breaker access issues
            
            if tripped_breakers:
                logging.warning(f"Circuit breakers not in CLOSED state: {', '.join(tripped_breakers)}")
            
        except Exception as e:
            logging.error(f"Error in unified sync cycle: {e}")
            # Safe initialization for debug logging
            completed_klines_tasks = locals().get('completed_klines_tasks', 0)
            total_klines_tasks = locals().get('total_klines_tasks', 0)
            start_time = locals().get('start_time', time.time())
            symbols = locals().get('symbols', [])
            logging.debug(f"Sync cycle error context: completed_tasks={completed_klines_tasks}/{total_klines_tasks}, cycle_time={time.time() - start_time:.1f}s, symbols={len(symbols)}")

    def start(self):
        """Start the unified background service"""
        logging.info("INIT_DEBUG: UnifiedDataSyncService.start() called")
        try:
            with self.lock:
                if self.is_running:
                    logging.warning("INIT_DEBUG: Unified data sync service already running")
                    return
                    
                logging.info("INIT_DEBUG: Setting service as running and starting worker thread")
                self.is_running = True
                self.stop_event.clear()
                
                self.worker_thread = threading.Thread(
                    target=self._service_loop,
                    name="UnifiedDataSyncService",
                    daemon=True
                )
                self.worker_thread.start()
                
                logging.info(f"INIT_DEBUG: Unified data sync service started - monitoring {len(TradingConfig.SUPPORTED_SYMBOLS)} symbols across {len(self.timeframes)} timeframes")
                logging.debug(f"INIT_DEBUG: Service configuration: symbols={TradingConfig.SUPPORTED_SYMBOLS}, timeframes={list(self.timeframes.keys())}, intervals={self.timeframes}")
                
        except Exception as e:
            logging.error(f"INIT_DEBUG: Error in UnifiedDataSyncService.start(): {e}")
            import traceback
            logging.error(f"INIT_DEBUG: Start error traceback: {traceback.format_exc()}")

    def stop(self):
        """Stop the unified background service"""
        with self.lock:
            if not self.is_running:
                return
                
            self.is_running = False
            self.stop_event.set()
            
            if self.worker_thread and self.worker_thread.is_alive():
                self.worker_thread.join(timeout=10)
                
            logging.info("Unified data sync service stopped")

    def restart(self):
        """Restart the unified background service"""
        logging.info("Restarting unified data sync service...")
        self.stop()
        time.sleep(1)  # Allow service to stop
        self.start()
        return True

    def _service_loop(self):
        """Main service loop"""
        logging.info("Unified data sync service loop started")
        cycle_count = 0
        
        while not self.stop_event.is_set():
            try:
                cycle_count += 1
                logging.debug(f"Starting sync cycle #{cycle_count}")
                
                # Run one complete coordinated cycle
                self._run_coordinated_sync_cycle()
                
                # Wait before next cycle - Balanced for open candle updates and API limits
                cycle_interval = 120  # 2 minutes between cycles for coordinated updates
                
                logging.debug(f"Sync cycle #{cycle_count} completed, waiting {cycle_interval}s before next cycle")
                
                for _ in range(cycle_interval):
                    if self.stop_event.is_set():
                        logging.debug(f"Stop event received during wait period after cycle #{cycle_count}")
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logging.error(f"Unexpected error in unified service loop (cycle #{cycle_count}): {e}")
                logging.debug(f"Error context: cycle_count={cycle_count}, is_running={self.is_running}, stop_event={self.stop_event.is_set()}")
                # Wait before retrying to avoid rapid error loops
                time.sleep(30)
                
        logging.info(f"Unified data sync service loop ended after {cycle_count} cycles")

    def get_status(self) -> Dict:
        """Get comprehensive service status and statistics"""
        with self.lock:
            cache_stats = self.cache.get_cache_stats()
            
            # Safe circuit breaker status retrieval to prevent timestamp type errors
            circuit_breaker_status = {}
            try:
                circuit_breaker_status = {
                    name: breaker.get_stats() 
                    for name, breaker in circuit_manager._breakers.items()
                    if "klines" in name or "binance" in name
                }
            except Exception as e:
                logging.warning(f"Error getting circuit breaker status: {e}")
                circuit_breaker_status = {"error": "Failed to retrieve circuit breaker status"}
            
            return {
                "service_running": self.is_running,
                "last_cache_cleanup": self.last_cache_cleanup.isoformat() if self.last_cache_cleanup else 'never',
                "last_cache_cleanup_raw": self.last_cache_cleanup,  # Keep raw datetime for internal use
                "klines_tracking": {
                    "tracked_symbols": len(self.last_klines_updates),
                    "total_timeframes": sum(len(tf_data) for tf_data in self.last_klines_updates.values()),
                    "supported_symbols": TradingConfig.SUPPORTED_SYMBOLS,
                    "supported_timeframes": list(self.timeframes.keys()),
                },
                "cache_statistics": cache_stats,
                "circuit_breaker_status": circuit_breaker_status
            }


# Global instances
enhanced_cache = SmartCache()  # Maintain compatibility
unified_service = None

# Compatibility functions for existing cache interface
def start_cache_cleanup_worker(app=None):
    """Legacy interface: Start cache cleanup (now part of unified service)"""
    global unified_service
    if unified_service is None:
        unified_service = UnifiedDataSyncService(app)
    unified_service.start()
    logging.info("Cache cleanup worker started (unified service)")

def restart_cache_cleanup_worker(app=None):
    """Legacy interface: Restart cache cleanup worker"""
    global unified_service
    if unified_service is None:
        unified_service = UnifiedDataSyncService(app)
    return unified_service.restart()

def get_cache_cleanup_worker_status():
    """Legacy interface: Get cache cleanup worker status"""
    global unified_service
    if unified_service is None:
        return {
            'enabled': False,
            'thread_alive': False,
            'last_cleanup': 'never',
            'worker_running_flag': False
        }
    
    status = unified_service.get_status()
    return {
        'enabled': status['service_running'],
        'thread_alive': status['service_running'],
        'last_cleanup': status['last_cache_cleanup'],
        'worker_running_flag': status['service_running']
    }

# Compatibility functions for existing klines interface
def start_klines_background_worker(app=None):
    """Legacy interface: Start klines background worker (now part of unified service)"""
    global unified_service
    if unified_service is None:
        unified_service = UnifiedDataSyncService(app)
    unified_service.start()

def stop_klines_background_worker():
    """Legacy interface: Stop klines background worker"""
    global unified_service
    if unified_service is not None:
        unified_service.stop()

def get_klines_worker_status() -> Dict:
    """Legacy interface: Get klines worker status"""
    global unified_service
    if unified_service is None:
        return {"is_running": False, "error": "Worker not initialized"}
    return unified_service.get_status()

# New unified interface
def start_unified_data_sync_service(app=None):
    """Start the unified data sync service"""
    logging.info("INIT_DEBUG: start_unified_data_sync_service called")
    global unified_service
    try:
        if unified_service is None:
            logging.info("INIT_DEBUG: Creating new UnifiedDataSyncService instance")
            unified_service = UnifiedDataSyncService(app)
            logging.info("INIT_DEBUG: UnifiedDataSyncService instance created successfully")
        else:
            logging.info("INIT_DEBUG: UnifiedDataSyncService instance already exists")
            
        logging.info("INIT_DEBUG: Calling unified_service.start()")
        unified_service.start()
        logging.info("INIT_DEBUG: unified_service.start() completed")
        
    except Exception as e:
        logging.error(f"INIT_DEBUG: Error starting unified data sync service: {e}")
        import traceback
        logging.error(f"INIT_DEBUG: Traceback: {traceback.format_exc()}")

def stop_unified_data_sync_service():
    """Stop the unified data sync service"""
    global unified_service
    if unified_service is not None:
        unified_service.stop()

def restart_unified_data_sync_service(app=None):
    """Restart the unified data sync service"""
    print(f"[RENDER-KLINES] Restarting unified data sync service")
    logging.info("[RENDER-KLINES] Restarting unified data sync service")
    
    global unified_service
    if unified_service is None:
        print(f"[RENDER-KLINES] Creating new unified service instance")
        logging.info("[RENDER-KLINES] Creating new unified service instance")
        unified_service = UnifiedDataSyncService(app)
    
    restart_result = unified_service.restart()
    
    if restart_result:
        print(f"[RENDER-KLINES] Unified service restart successful")
        logging.info("[RENDER-KLINES] Unified service restart successful")
    else:
        print(f"[RENDER-KLINES] Unified service restart failed")
        logging.warning("[RENDER-KLINES] Unified service restart failed")
    
    return restart_result

def get_unified_service_status() -> Dict:
    """Get unified service status"""
    global unified_service
    if unified_service is None:
        return {"service_running": False, "error": "Service not initialized"}
    return unified_service.get_status()