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

    def __init__(self):
        """Initialize the background klines worker"""
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


    @with_circuit_breaker("binance_klines_api", failure_threshold=2, recovery_timeout=60, success_threshold=2)
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

    @with_circuit_breaker("coingecko_klines_api", failure_threshold=3, recovery_timeout=90, success_threshold=2)
    def _fetch_coingecko_klines(self, symbol: str, timeframe: str, days: int = 30) -> List[Dict]:
        """
        Fetch klines data from CoinGecko API with circuit breaker protection
        
        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            timeframe: Timeframe ('1h', '4h', '1d')
            days: Number of days of historical data
            
        Returns:
            List of klines data in OHLCV format
        """
        # Convert symbol format for CoinGecko (BTCUSDT -> bitcoin)
        symbol_map = {
            "BTCUSDT": "bitcoin",
            "ETHUSDT": "ethereum", 
            "BNBUSDT": "binancecoin",
            "ADAUSDT": "cardano",
            "XRPUSDT": "ripple",
            "SOLUSDT": "solana",
            "DOTUSDT": "polkadot",
            "DOGEUSDT": "dogecoin",
            "AVAXUSDT": "avalanche-2",
            "LINKUSDT": "chainlink"
        }
        
        coin_id = symbol_map.get(symbol)
        if not coin_id:
            raise ValueError(f"Symbol {symbol} not supported by CoinGecko fallback")
            
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
        params = {"vs_currency": "usd", "days": days}
        
        response = requests.get(url, params=params, timeout=TimeConfig.PRICE_API_TIMEOUT)
        response.raise_for_status()
        
        ohlc_data = response.json()
        
        # Convert CoinGecko format to standardized format
        klines = []
        for ohlc in ohlc_data:
            klines.append({
                "timestamp": datetime.fromtimestamp(ohlc[0] / 1000, tz=timezone.utc),
                "open": float(ohlc[1]),
                "high": float(ohlc[2]),
                "low": float(ohlc[3]), 
                "close": float(ohlc[4]),
                "volume": 0.0  # CoinGecko OHLC doesn't include volume
            })
            
        logging.debug(f"Fetched {len(klines)} klines for {symbol} from CoinGecko fallback")
        return klines

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
            Dict with keys: count, oldest_timestamp, newest_timestamp, gaps
        """
        try:
            # Get existing cached data
            cached_data = KlinesCache.get_cached_data(
                symbol=symbol,
                timeframe=timeframe, 
                limit=self._get_required_initial_candles(timeframe),
                include_incomplete=True
            )
            
            if not cached_data:
                return {
                    "count": 0,
                    "oldest_timestamp": None,
                    "newest_timestamp": None,
                    "needs_initial_population": True
                }
                
            timestamps = [candle["timestamp"] for candle in cached_data]
            timestamps.sort()
            
            return {
                "count": len(cached_data),
                "oldest_timestamp": timestamps[0] if timestamps else None,
                "newest_timestamp": timestamps[-1] if timestamps else None, 
                "needs_initial_population": len(cached_data) < self._get_required_initial_candles(timeframe) * 0.8
            }
            
        except Exception as e:
            logging.warning(f"Error checking existing data for {symbol} {timeframe}: {e}")
            return {
                "count": 0,
                "oldest_timestamp": None,
                "newest_timestamp": None,
                "needs_initial_population": True
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
                logging.warning(f"Binance API failed for {symbol} {timeframe}: {e}, trying fallback")
                # Fallback to CoinGecko for critical symbols
                try:
                    days_needed = required_candles // (24 if timeframe == "1h" else 6 if timeframe == "4h" else 1)
                    klines_data = self._fetch_coingecko_klines(symbol, timeframe, days_needed)
                except Exception as fallback_error:
                    logging.error(f"All data sources failed for {symbol} {timeframe}: {fallback_error}")
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
            saved_count = KlinesCache.save_klines_batch(
                symbol=symbol,
                timeframe=timeframe,
                klines_data=klines_data,
                ttl_minutes=ttl_minutes
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
        Update only recent and open candles for a symbol/timeframe
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe to update
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Fetch only recent candles (last 10-20 periods)
            recent_limit = 20
            
            logging.debug(f"Updating recent data: {symbol} {timeframe} ({recent_limit} candles)")
            
            # Fetch recent data
            try:
                recent_klines = self._fetch_binance_klines(symbol, timeframe, recent_limit)
            except Exception as e:
                logging.warning(f"Recent update failed for {symbol} {timeframe}: {e}")
                return False
                
            if not recent_klines:
                return False
                
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
            saved_count = KlinesCache.save_klines_batch(
                symbol=symbol,
                timeframe=timeframe,
                klines_data=recent_klines,
                ttl_minutes=ttl_minutes
            )
            
            logging.debug(f"Updated {saved_count} recent candles for {symbol} {timeframe}")
            
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
        """Clean up old klines data beyond retention period"""
        try:
            # Need Flask app context for database operations
            from flask import has_app_context, current_app
            
            if not has_app_context():
                # Skip cleanup if no app context available
                logging.debug("Skipping klines cleanup - no app context available")
                return
                
            # Clean up expired cache entries
            expired_count = KlinesCache.cleanup_expired()
            if expired_count > 0:
                logging.info(f"Cleaned up {expired_count} expired klines cache entries")
                
            # Clean up very old data beyond retention period  
            old_count = KlinesCache.cleanup_old_data(days_to_keep=CacheConfig.KLINES_DATA_RETENTION_DAYS)
            if old_count > 0:
                logging.info(f"Cleaned up {old_count} old klines entries beyond retention period")
                
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
                        
                        # Decide between initial population or recent update
                        if data_info["needs_initial_population"]:
                            success = self._populate_initial_data(symbol, timeframe)
                        else:
                            success = self._update_recent_data(symbol, timeframe)
                            
                        if success:
                            logging.debug(f"Successfully processed {symbol} {timeframe}")
                        else:
                            logging.warning(f"Failed to process {symbol} {timeframe}")
                            
                    except Exception as e:
                        logging.error(f"Error processing {symbol} {timeframe}: {e}")
                        
                    completed_tasks += 1
                    
                    # Small delay between requests to be API-friendly
                    time.sleep(0.1)
                    
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
                
                # Wait before next cycle (cost optimization)
                cycle_interval = TimeConfig.RENDER_SYNC_INTERVAL * 10  # ~120 seconds between cycles
                
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


# Global instance
klines_worker = KlinesBackgroundWorker()


def start_klines_background_worker():
    """Start the global klines background worker"""
    klines_worker.start()


def stop_klines_background_worker():
    """Stop the global klines background worker"""
    klines_worker.stop()


def get_klines_worker_status() -> Dict:
    """Get status of the global klines background worker"""
    return klines_worker.get_status()