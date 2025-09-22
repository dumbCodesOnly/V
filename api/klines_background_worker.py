"""
Efficient Background Klines Worker with Circuit Breaker Integration

This module provides a comprehensive background worker system for managing klines data:
- Proactive initialization of klines data for all supported symbols/timeframes
- Regular updates of recent and open candles without user intervention  
- Automatic cleanup of old data beyond retention periods
- Full circuit breaker integration for API fault tolerance
- Cost-optimized batch processing and intelligent update scheduling
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

import requests
from config import (
    CacheConfig,
    SMCConfig,
    TimeConfig,
    TradingConfig,
)

from .circuit_breaker import circuit_manager, with_circuit_breaker
from .models import KlinesCache, db, get_utc_now


class KlinesBackgroundWorker:
    """
    Comprehensive background worker for efficient klines data management
    """

    def __init__(self, app=None):
        """Initialize the background klines worker"""
        self.app = app
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        
        # Supported timeframes with update intervals (in seconds)
        self.timeframes = {
            "1h": 300,   # Update every 5 minutes  
            "4h": 900,   # Update every 15 minutes
            "1d": 3600   # Update every hour
        }
        
        # Track last update times per symbol/timeframe
        self.last_updates: Dict[str, Dict[str, datetime]] = {}
        
        # Circuit breakers will be initialized automatically by @with_circuit_breaker decorators
        
        logging.info("Klines background worker initialized")


    @with_circuit_breaker("binance_klines_api", failure_threshold=8, recovery_timeout=120, success_threshold=3)
    def _fetch_binance_klines(self, symbol: str, interval: str, limit: int = 1000) -> List[Dict]:
        """
        Fetch klines data from Binance API with circuit breaker protection
        
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
            klines.append({
                "timestamp": datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc),
                "open": float(kline[1]),
                "high": float(kline[2]), 
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5])
            })
            
        logging.debug(f"Fetched {len(klines)} klines for {symbol} {interval} from Binance")
        return klines

    # Removed CoinGecko klines fallback - inappropriate for candlestick data
    # CoinGecko OHLC is price data, not proper klines data, and lacks volume information
    # Better to wait for Binance to recover than use poor quality data

    def _get_required_initial_candles(self, timeframe: str) -> int:
        """Calculate how many initial candles we need for each timeframe"""
        # Based on SMC analysis requirements and trading needs
        if timeframe == "1h":
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
        if symbol not in self.last_updates:
            self.last_updates[symbol] = {}
            
        if timeframe not in self.last_updates[symbol]:
            return True  # Never updated before
            
        last_update = self.last_updates[symbol][timeframe]
        time_since_update = (get_utc_now() - last_update).total_seconds()
        
        return time_since_update >= update_interval

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
                if len(total_data) >= required_candles * 0.7:  # 70% coverage is sufficient
                    needs_initial = False
                else:
                    # Check if newest data is very old (more than 1 day old)
                    newest_time = timestamps[-1] if timestamps else None
                    if newest_time:
                        age_hours = (current_time - newest_time).total_seconds() / 3600
                        needs_initial = age_hours > 24  # If data older than 24h, do initial population
                    else:
                        needs_initial = True
                
                return {
                    "count": len(total_data),
                    "oldest_timestamp": timestamps[0] if timestamps else None,
                    "newest_timestamp": timestamps[-1] if timestamps else None, 
                    "needs_initial_population": needs_initial,
                    "has_recent_data": len(recent_data) > 0
                }
            
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
            if timeframe == "1h":
                ttl_minutes = CacheConfig.KLINES_1H_CACHE_TTL
            elif timeframe == "4h":
                ttl_minutes = CacheConfig.KLINES_4H_CACHE_TTL
            elif timeframe == "1d":
                ttl_minutes = CacheConfig.KLINES_1D_CACHE_TTL
            else:
                ttl_minutes = 60  # Default 1 hour
                
            # Save to database in batches for efficiency
            if not self.app:
                logging.warning(f"No app context available for saving {symbol} {timeframe} data")
                return False
            
            with self.app.app_context():
                saved_count = KlinesCache.save_klines_batch(
                    symbol=symbol,
                    timeframe=timeframe,
                    candlesticks=klines_data,
                    cache_ttl_minutes=ttl_minutes
                )
            
            logging.info(f"Successfully populated {saved_count} candles for {symbol} {timeframe}")
            
            # Update tracking
            with self.lock:
                if symbol not in self.last_updates:
                    self.last_updates[symbol] = {}
                self.last_updates[symbol][timeframe] = get_utc_now()
                
            return True
            
        except Exception as e:
            logging.error(f"Error populating initial data for {symbol} {timeframe}: {e}")
            return False

    def _update_recent_data(self, symbol: str, timeframe: str) -> bool:
        """
        Update only recent and open candles for a symbol/timeframe - OPTIMIZED VERSION
        
        Fetches minimal data: just the last few candles to update current/recent periods
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe to update
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Fetch minimal recent data - just enough to update current period + few previous
            # 1h: last 3 candles (covers current + 2 previous hours)
            # 4h: last 2 candles (covers current + 1 previous 4h period) 
            # 1d: last 2 candles (covers current + 1 previous day)
            recent_limit = 3 if timeframe == "1h" else 2
            
            logging.debug(f"Updating recent data: {symbol} {timeframe} ({recent_limit} candles - minimal fetch)")
            
            # Fetch recent data  
            try:
                recent_klines = self._fetch_binance_klines(symbol, timeframe, recent_limit)
            except Exception as e:
                logging.warning(f"Recent update failed for {symbol} {timeframe}: {e}")
                # Brief delay before giving up
                time.sleep(TimeConfig.API_RETRY_DELAY * 0.5)
                return False
                
            if not recent_klines:
                return False
            
            # Filter to only update the most recent periods (avoid unnecessary overwrites)
            current_time = get_utc_now()
            
            # Only update candles from the last few periods to minimize database writes
            cutoff_hours = {"1h": 6, "4h": 12, "1d": 48}  # 6h, 12h, 48h cutoff
            cutoff_time = current_time - timedelta(hours=cutoff_hours.get(timeframe, 12))
            
            filtered_klines = [
                kline for kline in recent_klines 
                if kline["timestamp"] >= cutoff_time
            ]
            
            if not filtered_klines:
                logging.debug(f"No recent candles to update for {symbol} {timeframe}")
                return True  # Success but nothing to do
                
            # Calculate TTL
            if timeframe == "1h":
                ttl_minutes = CacheConfig.KLINES_1H_CACHE_TTL  
            elif timeframe == "4h":
                ttl_minutes = CacheConfig.KLINES_4H_CACHE_TTL
            elif timeframe == "1d":
                ttl_minutes = CacheConfig.KLINES_1D_CACHE_TTL
            else:
                ttl_minutes = 60
                
            # Save recent data (will update existing entries due to unique constraint)
            if not self.app:
                logging.warning(f"No app context available for saving recent {symbol} {timeframe} data")
                return False
                
            with self.app.app_context():
                saved_count = KlinesCache.save_klines_batch(
                    symbol=symbol,
                    timeframe=timeframe,
                    candlesticks=filtered_klines,
                    cache_ttl_minutes=ttl_minutes
                )
            
            logging.debug(f"Updated {saved_count}/{len(recent_klines)} recent candles for {symbol} {timeframe}")
            
            # Update tracking
            with self.lock:
                if symbol not in self.last_updates:
                    self.last_updates[symbol] = {}
                self.last_updates[symbol][timeframe] = get_utc_now()
                
            return True
            
        except Exception as e:
            logging.error(f"Error updating recent data for {symbol} {timeframe}: {e}")
            return False

    def _cleanup_old_data(self):
        """Gradual cleanup of old klines data - batch processing to avoid database locks"""
        try:
            # Skip cleanup if no app context available
            if not self.app:
                logging.debug("Skipping klines cleanup - no app context available")
                return
                
            # Need Flask app context for database operations
            with self.app.app_context():
                # Clean up expired cache entries (small batches to avoid locks)
                expired_count = KlinesCache.cleanup_expired()
                if expired_count > 0:
                    logging.info(f"Cleaned up {expired_count} expired klines cache entries")
                    
                # Gradual cleanup of very old data - only clean a bit each cycle
                # This prevents large database operations that could impact performance
                old_count = KlinesCache.cleanup_old_data(days_to_keep=CacheConfig.KLINES_DATA_RETENTION_DAYS)
                if old_count > 0:
                    logging.info(f"Cleaned up {old_count} old klines entries beyond {CacheConfig.KLINES_DATA_RETENTION_DAYS} day retention")
                
        except Exception as e:
            logging.error(f"Error during klines data cleanup: {e}")

    def _run_worker_cycle(self):
        """Execute one complete worker cycle"""
        try:
            start_time = time.time()
            
            # Get list of supported symbols
            symbols = TradingConfig.SUPPORTED_SYMBOLS
            total_tasks = len(symbols) * len(self.timeframes)
            completed_tasks = 0
            
            logging.debug(f"Starting worker cycle for {len(symbols)} symbols, {len(self.timeframes)} timeframes")
            
            # Process each symbol/timeframe combination
            for symbol in symbols:
                if self.stop_event.is_set():
                    break
                    
                for timeframe in self.timeframes.keys():
                    if self.stop_event.is_set():
                        break
                        
                    try:
                        # Check if this timeframe needs updating
                        if not self._should_update_timeframe(symbol, timeframe):
                            completed_tasks += 1
                            continue
                            
                        # Get existing data information
                        data_info = self._get_existing_data_info(symbol, timeframe)
                        
                        # OPTIMIZED: Decide between initial population or incremental update
                        if data_info["needs_initial_population"]:
                            # Initial population: Get full historical data (happens once per symbol/timeframe)
                            logging.info(f"Initial population needed for {symbol} {timeframe}")
                            success = self._populate_initial_data(symbol, timeframe)
                        else:
                            # Incremental update: Only get recent candles (much more efficient)
                            logging.debug(f"Incremental update for {symbol} {timeframe}")
                            success = self._update_recent_data(symbol, timeframe)
                            
                        if success:
                            logging.debug(f"Successfully processed {symbol} {timeframe}")
                        else:
                            logging.warning(f"Failed to process {symbol} {timeframe}")
                            
                    except Exception as e:
                        logging.error(f"Error processing {symbol} {timeframe}: {e}")
                        
                    completed_tasks += 1
                    
                    # Proper delay between requests to respect API rate limits
                    time.sleep(TimeConfig.BINANCE_KLINES_DELAY)
                    
                    # Additional longer delay during initial population to be extra conservative
                    if data_info.get("needs_initial_population", False):
                        time.sleep(3.0)  # Extra 3 seconds for initial population
                    
            # Cleanup old data at the end of each cycle
            self._cleanup_old_data()
            
            cycle_time = time.time() - start_time
            logging.info(f"Worker cycle completed: {completed_tasks}/{total_tasks} tasks in {cycle_time:.1f}s")
            
        except Exception as e:
            logging.error(f"Error in worker cycle: {e}")

    def start(self):
        """Start the background worker"""
        with self.lock:
            if self.is_running:
                logging.warning("Klines background worker already running")
                return
                
            self.is_running = True
            self.stop_event.clear()
            
            self.worker_thread = threading.Thread(
                target=self._worker_loop,
                name="KlinesBackgroundWorker",
                daemon=True
            )
            self.worker_thread.start()
            
            logging.info("Klines background worker started")

    def stop(self):
        """Stop the background worker"""
        with self.lock:
            if not self.is_running:
                return
                
            self.is_running = False
            self.stop_event.set()
            
            if self.worker_thread and self.worker_thread.is_alive():
                self.worker_thread.join(timeout=10)
                
            logging.info("Klines background worker stopped")

    def _worker_loop(self):
        """Main worker loop"""
        logging.info("Klines background worker loop started")
        
        while not self.stop_event.is_set():
            try:
                # Run one complete cycle
                self._run_worker_cycle()
                
                # Wait before next cycle (cost optimization) - Much longer intervals to reduce API pressure
                cycle_interval = TimeConfig.RENDER_SYNC_INTERVAL * 20  # ~500 seconds between cycles (8+ minutes)
                
                for _ in range(cycle_interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logging.error(f"Unexpected error in klines worker loop: {e}")
                # Wait before retrying to avoid rapid error loops
                time.sleep(30)
                
        logging.info("Klines background worker loop ended")

    def get_status(self) -> Dict:
        """Get current worker status and statistics"""
        with self.lock:
            return {
                "is_running": self.is_running,
                "tracked_symbols": len(self.last_updates),
                "total_timeframes": sum(len(tf_data) for tf_data in self.last_updates.values()),
                "supported_symbols": TradingConfig.SUPPORTED_SYMBOLS,
                "supported_timeframes": list(self.timeframes.keys()),
                "circuit_breaker_status": {
                    name: breaker.get_stats() 
                    for name, breaker in circuit_manager._breakers.items()
                    if "klines" in name
                }
            }


# Global instance (will be initialized with app when started)
klines_worker = None


def start_klines_background_worker(app=None):
    """Start the global klines background worker"""
    global klines_worker
    if klines_worker is None:
        klines_worker = KlinesBackgroundWorker(app)
    klines_worker.start()


def stop_klines_background_worker():
    """Stop the global klines background worker"""
    if klines_worker is not None:
        klines_worker.stop()


def get_klines_worker_status() -> Dict:
    """Get status of the global klines background worker"""
    if klines_worker is None:
        return {"is_running": False, "error": "Worker not initialized"}
    return klines_worker.get_status()