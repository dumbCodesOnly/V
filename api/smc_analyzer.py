"""
Smart Money Concepts (SMC) Analysis Engine
Analyzes market structure and provides trade suggestions based on institutional trading patterns
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple, Union, overload

import numpy as np
import requests

# Import circuit breaker functionality
from .circuit_breaker import CircuitBreakerError, with_circuit_breaker

# Import configuration constants
try:
    from config import SMCConfig
except ImportError:
    # Fallback if running from different directory
    import os
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import SMCConfig


class MarketStructure(Enum):
    BULLISH_BOS = "bullish_break_of_structure"
    BEARISH_BOS = "bearish_break_of_structure"
    BULLISH_CHoCH = "bullish_change_of_character"
    BEARISH_CHoCH = "bearish_change_of_character"
    CONSOLIDATION = "consolidation"


class SignalStrength(Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


@dataclass
class PriceLevel:
    price: float
    timestamp: datetime
    volume: float = 0.0
    touched_count: int = 0


@dataclass
class OrderBlock:
    price_high: float
    price_low: float
    timestamp: datetime
    direction: str  # 'bullish' or 'bearish'
    strength: float
    tested: bool = False
    retest_count: int = 0
    mitigated: bool = False
    volume_confirmed: bool = False
    impulsive_exit: bool = False


@dataclass
class FairValueGap:
    gap_high: float
    gap_low: float
    timestamp: datetime
    direction: str  # 'bullish' or 'bearish'
    filled: bool = False
    atr_size: float = 0.0
    alignment_score: float = 0.0
    age_candles: int = 0


@dataclass
class LiquidityPool:
    price: float
    type: str  # 'buy_side' or 'sell_side'
    strength: float
    swept: bool = False
    sweep_confirmed: bool = False
    sweep_timestamp: Optional[datetime] = None


@dataclass
class ScaledEntry:
    """Phase 4: Represents a single entry level in a scaled entry strategy"""
    entry_price: float
    allocation_percent: float  # 50, 25, 25
    order_type: str  # 'market' or 'limit'
    stop_loss: float
    take_profits: List[Tuple[float, float]]  # [(price, allocation), ...]
    status: str = 'pending'  # 'pending', 'filled', 'cancelled'


@dataclass
class SMCSignal:
    symbol: str
    direction: str  # 'long' or 'short'
    entry_price: float
    stop_loss: float
    take_profit_levels: List[float]
    confidence: float
    reasoning: List[str]
    signal_strength: SignalStrength
    risk_reward_ratio: float
    timestamp: datetime
    current_market_price: float  # Actual market price when signal was generated
    scaled_entries: Optional[List['ScaledEntry']] = None  # Phase 4: Scaled entry strategy


class SMCAnalyzer:
    """Smart Money Concepts analyzer for detecting institutional trading patterns"""

    def __init__(self):
        from config import SMCConfig, TradingConfig
        
        self.timeframes = ["15m", "1h", "4h", "1d"]  # Multiple timeframe analysis (15m for execution)
        self.active_signals = {}  # Cache for active signals {symbol: {signal, expiry_time}}
        self.signal_timeout = 3600  # Signal valid for 1 hour (3600 seconds)
        
        # Auto-volatility tuning parameters (set to defaults, dynamically adjusted per signal)
        self.atr_multiplier = 1.0
        self.fvg_multiplier = SMCConfig.FVG_ATR_MULTIPLIER
        self.ob_volume_multiplier = SMCConfig.OB_VOLUME_MULTIPLIER
        self.scaled_entry_depths = [
            TradingConfig.SCALED_ENTRY_DEPTH_1,
            TradingConfig.SCALED_ENTRY_DEPTH_2
        ]

    @with_circuit_breaker(
        "binance_klines_api", failure_threshold=8, recovery_timeout=120
    )
    def get_candlestick_data(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> List[Dict]:
        """Get candlestick data with cache-first approach, rolling window aware, and circuit breaker protection"""
        from config import CacheConfig, RollingWindowConfig

        from .models import KlinesCache
        
        # ROLLING WINDOW VALIDATION: Ensure requested limit doesn't exceed rolling window size
        max_available = RollingWindowConfig.get_max_candles(timeframe)
        if limit > max_available:
            logging.warning(
                f"SMC Analysis: Requested {limit} candles for {symbol}:{timeframe}, "
                f"but rolling window only keeps {max_available}. Adjusting limit to {max_available}."
            )
            limit = max_available

        # Step 1: Try to get data from cache first
        try:
            cached_data = KlinesCache.get_cached_data(symbol, timeframe, limit)
            if len(cached_data) >= limit:
                logging.debug(
                    f"CACHE HIT: Using cached data for {symbol} {timeframe} ({len(cached_data)} candles)"
                )
                return cached_data
            elif len(cached_data) > 0:
                logging.debug(
                    f"PARTIAL CACHE HIT: Found {len(cached_data)} cached candles for {symbol} {timeframe}, fetching remaining data"
                )
        except Exception as e:
            logging.warning(f"Cache retrieval failed for {symbol} {timeframe}: {e}")
            cached_data = []

        # Step 2: Determine what to fetch - OPTIMIZED for user-triggered requests
        try:
            gap_info = KlinesCache.get_data_gaps(symbol, timeframe, limit)
            if not gap_info["needs_fetch"]:
                logging.debug(
                    f"CACHE SUFFICIENT: Using existing cached data for {symbol} {timeframe}"
                )
                return KlinesCache.get_cached_data(symbol, timeframe, limit)

            # Always respect minimum fetch count from gap analysis
            min_required_fetch = gap_info["fetch_count"]
            
            # EFFICIENT OPTIMIZATION: If we have existing cache data, check if we just need to update open candle
            if len(cached_data) > 0:
                # Check if we have current open candle already
                current_open_candle = KlinesCache.get_current_open_candle(symbol, timeframe)
                if current_open_candle and min_required_fetch <= 2:
                    # SAFE: Only use efficient update when no historical gaps exist
                    fetch_limit = 1
                    logging.info(
                        f"EFFICIENT OPEN CANDLE UPDATE: Fetching only current candle for {symbol} {timeframe}"
                    )
                else:
                    # SAFE: Fetch minimum required to fill gaps
                    fetch_limit = min_required_fetch
                    if min_required_fetch > 2:
                        logging.warning(
                            f"HISTORICAL GAPS DETECTED: Fetching {fetch_limit} candles for {symbol} {timeframe} to fill gaps"
                        )
                    else:
                        logging.info(
                            f"CACHE UPDATE: Fetching latest {fetch_limit} candles for {symbol} {timeframe} to stay current"
                        )
            else:
                # No cache data - fetch full amount 
                fetch_limit = min_required_fetch
                logging.info(
                    f"CACHE MISS: Fetching {fetch_limit} candles for {symbol} {timeframe}"
                )

        except Exception as e:
            logging.warning(f"Gap analysis failed for {symbol} {timeframe}: {e}")
            # Conservative fallback: fetch more data when uncertain
            fetch_limit = min(10, limit) if len(cached_data) > 0 else limit

        # Step 3: Fetch from Binance API with circuit breaker protection
        tf_map = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
        interval = tf_map.get(timeframe, "1h")

        url = f"https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": fetch_limit}

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        klines = response.json()

        # Convert to OHLCV format
        candlesticks = []
        for kline in klines:
            # Handle potential type mismatch for timestamp
            try:
                timestamp_value = float(kline[0]) if isinstance(kline[0], str) else kline[0]
            except (ValueError, TypeError):
                logging.warning(f"Invalid timestamp format in kline data: {kline[0]}")
                continue
                
            candlestick = {
                "timestamp": datetime.fromtimestamp(timestamp_value / 1000, tz=timezone.utc),
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
            }
            candlesticks.append(candlestick)

        # Step 4: Cache the fetched data efficiently
        try:
            # If we only fetched 1 candle and have existing cache, use efficient open candle update
            if fetch_limit == 1 and len(cached_data) > 0 and len(candlesticks) == 1:
                current_candle = candlesticks[0]
                
                # Calculate appropriate TTL for open candle
                if timeframe == "15m":
                    ttl_minutes = 1  # Very short TTL for 15m open candles
                elif timeframe == "1h":
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
                    logging.debug(f"SMC: Efficiently updated open candle for {symbol} {timeframe}")
                    # Return updated cached data including the new open candle
                    return KlinesCache.get_cached_data(symbol, timeframe, limit)
                else:
                    logging.warning(f"SMC: Failed to update open candle for {symbol} {timeframe}, falling back to batch save")
                    # Fall through to batch save as fallback
            
            # Fallback: Use traditional batch save for multiple candles or when update fails
            ttl_config = {
                "15m": getattr(CacheConfig, 'KLINES_15M_CACHE_TTL', 1),  # 1 minute cache for 15m
                "1h": CacheConfig.KLINES_1H_CACHE_TTL,
                "4h": CacheConfig.KLINES_4H_CACHE_TTL,
                "1d": CacheConfig.KLINES_1D_CACHE_TTL,
            }
            cache_ttl = ttl_config.get(timeframe, CacheConfig.KLINES_1H_CACHE_TTL)

            saved_count = KlinesCache.save_klines_batch(
                symbol, timeframe, candlesticks, cache_ttl
            )
            logging.info(
                f"CACHE SAVE: Saved {saved_count} candles for {symbol} {timeframe} (TTL: {cache_ttl}m)"
            )

        except Exception as e:
            logging.error(f"Failed to cache data for {symbol} {timeframe}: {e}")

        # Step 5: Return combined data (cached + fetched) if partial hit, else just fetched
        try:
            if len(cached_data) > 0 and len(candlesticks) > 0:
                # Combine and deduplicate data
                combined_data = cached_data + candlesticks
                # Remove duplicates based on timestamp and normalize timezone
                seen_timestamps = set()
                unique_data = []
                for candle in combined_data:
                    # ISSUE #17 FIX: Create a copy to avoid mutating original data
                    candle_copy = candle.copy()
                    
                    # Normalize timestamp to timezone-aware UTC for consistent comparison
                    if isinstance(candle_copy["timestamp"], datetime):
                        # Ensure all timestamps are timezone-aware UTC
                        if candle_copy["timestamp"].tzinfo is None:
                            normalized_timestamp = candle_copy["timestamp"].replace(tzinfo=timezone.utc)
                        else:
                            normalized_timestamp = candle_copy["timestamp"].astimezone(timezone.utc)
                        candle_copy["timestamp"] = normalized_timestamp
                        timestamp_key = normalized_timestamp.isoformat()
                    else:
                        timestamp_key = str(candle_copy["timestamp"])
                        
                    if timestamp_key not in seen_timestamps:
                        seen_timestamps.add(timestamp_key)
                        unique_data.append(candle_copy)

                # Sort by timestamp (now all timezone-aware) and limit
                unique_data.sort(key=lambda x: x["timestamp"])
                return unique_data[-limit:] if len(unique_data) > limit else unique_data
            else:
                return candlesticks

        except Exception as e:
            logging.error(
                f"Error combining cached and fetched data for {symbol} {timeframe}: {e}"
            )
            return candlesticks

    def get_multi_timeframe_data(self, symbol: str) -> Dict[str, List[Dict]]:
        """Get candlestick data for multiple timeframes with circuit breaker protection"""
        from config import SMCConfig
        
        timeframe_data = {}
        timeframe_configs = [
            ("15m", SMCConfig.TIMEFRAME_15M_LIMIT),
            ("1h", SMCConfig.TIMEFRAME_1H_LIMIT), 
            ("4h", SMCConfig.TIMEFRAME_4H_LIMIT), 
            ("1d", SMCConfig.TIMEFRAME_1D_LIMIT)
        ]

        logging.info(
            f"Fetching batch candlestick data for {symbol} - 15m:{SMCConfig.TIMEFRAME_15M_LIMIT}, 1h:{SMCConfig.TIMEFRAME_1H_LIMIT}, 4h:{SMCConfig.TIMEFRAME_4H_LIMIT}, 1d:{SMCConfig.TIMEFRAME_1D_LIMIT} candles"
        )

        for timeframe, limit in timeframe_configs:
            try:
                data = self.get_candlestick_data(symbol, timeframe, limit)
                timeframe_data[timeframe] = data
                logging.debug(
                    f"Successfully fetched {len(data)} candles for {symbol} {timeframe}"
                )

                # Controlled delay between timeframes
                import time

                time.sleep(0.2)  # Reduced delay since circuit breaker handles failures

            except CircuitBreakerError as e:
                logging.warning(f"Circuit breaker OPEN for {symbol} {timeframe}: {e}")
                timeframe_data[timeframe] = []
                # Skip remaining timeframes if circuit breaker is open
                if len([tf for tf, data in timeframe_data.items() if data]) == 0:
                    logging.warning(
                        f"Circuit breaker active, skipping remaining timeframes for {symbol}"
                    )
                    break

            except Exception as e:
                logging.error(f"Failed to get {timeframe} data for {symbol}: {e}")
                timeframe_data[timeframe] = []

        return timeframe_data

    @staticmethod
    def get_bulk_multi_timeframe_data(
        symbols: List[str],
    ) -> Dict[str, Dict[str, List[Dict]]]:
        """Get candlestick data for multiple symbols using circuit breaker protection"""
        import time

        all_symbol_data = {}
        analyzer = SMCAnalyzer()

        logging.info(
            f"Starting bulk fetch for {len(symbols)} symbols with circuit breaker protection"
        )

        for i, symbol in enumerate(symbols):
            try:
                symbol_data = analyzer.get_multi_timeframe_data(symbol)
                all_symbol_data[symbol] = symbol_data

                # Check if we got any data
                total_candles = sum(len(data) for data in symbol_data.values())
                if total_candles > 0:
                    logging.info(
                        f"Completed batch data fetch for {symbol}: {total_candles} total candles"
                    )
                else:
                    logging.warning(
                        f"No data retrieved for {symbol} - circuit breaker may be active"
                    )

                # Progressive delay between symbols (shorter since circuit breaker handles failures)
                if i < len(symbols) - 1:  # Don't delay after last symbol
                    time.sleep(0.3)

            except CircuitBreakerError as e:
                logging.warning(f"Circuit breaker blocked request for {symbol}: {e}")
                all_symbol_data[symbol] = {"15m": [], "1h": [], "4h": [], "1d": []}

            except Exception as e:
                logging.error(f"Error in bulk fetch for {symbol}: {e}")
                all_symbol_data[symbol] = {"15m": [], "1h": [], "4h": [], "1d": []}

        successful_symbols = len(
            [
                s
                for s, data in all_symbol_data.items()
                if any(len(timeframe_data) > 0 for timeframe_data in data.values())
            ]
        )
        logging.info(
            f"Completed bulk data fetch: {successful_symbols}/{len(symbols)} symbols successful"
        )
        return all_symbol_data

    def detect_market_structure(self, candlesticks: List[Dict]) -> MarketStructure:
        """Detect current market structure using SMC principles"""
        if len(candlesticks) < SMCConfig.MIN_CANDLESTICKS_FOR_STRUCTURE:
            return MarketStructure.CONSOLIDATION

        # Get recent swing highs and lows
        swing_highs = self._find_swing_highs(candlesticks)
        swing_lows = self._find_swing_lows(candlesticks)

        if (
            len(swing_highs) < SMCConfig.MIN_SWING_POINTS
            or len(swing_lows) < SMCConfig.MIN_SWING_POINTS
        ):
            return MarketStructure.CONSOLIDATION

        # Analyze the pattern of highs and lows
        recent_highs = swing_highs[-3:]
        recent_lows = swing_lows[-3:]

        # Check for Break of Structure (BOS)
        if len(recent_highs) >= 2:
            if recent_highs[-1]["high"] > recent_highs[-2]["high"]:
                # Recent high broke previous high
                if (
                    len(recent_lows) >= 2
                    and recent_lows[-1]["low"] > recent_lows[-2]["low"]
                ):
                    return MarketStructure.BULLISH_BOS

        if len(recent_lows) >= 2:
            if recent_lows[-1]["low"] < recent_lows[-2]["low"]:
                # Recent low broke previous low
                if (
                    len(recent_highs) >= 2
                    and recent_highs[-1]["high"] < recent_highs[-2]["high"]
                ):
                    return MarketStructure.BEARISH_BOS

        # Check for Change of Character (CHoCH)
        if len(recent_highs) >= 3 and len(recent_lows) >= 3:
            # Look for trend reversal patterns
            high_trend = self._calculate_trend(recent_highs, "high")
            low_trend = self._calculate_trend(recent_lows, "low")

            if high_trend == "down" and low_trend == "up":
                return MarketStructure.BULLISH_CHoCH
            elif high_trend == "up" and low_trend == "down":
                return MarketStructure.BEARISH_CHoCH

        return MarketStructure.CONSOLIDATION

    def find_order_blocks(self, candlesticks: List[Dict]) -> List[OrderBlock]:
        """Enhanced order block identification with volume and impulsive move validation"""
        order_blocks = []

        if len(candlesticks) < 10:
            return order_blocks

        # Calculate average volume for filtering
        volumes = [c["volume"] for c in candlesticks[-20:] if c["volume"] > 0]
        avg_volume = sum(volumes) / len(volumes) if volumes else 1

        for i in range(3, len(candlesticks) - SMCConfig.OB_DISPLACEMENT_CANDLES):
            current = candlesticks[i]
            prev = candlesticks[i - 1]

            # Volume filter - require above average volume (dynamically tuned)
            volume_confirmed = (
                current["volume"] >= avg_volume * self.ob_volume_multiplier
            )
            if not volume_confirmed:
                continue

            # Look for strong bullish candles
            if (
                current["close"] > current["open"]
                and current["high"] - current["low"]
                > (current["open"] - prev["close"]) * 2
            ):

                # Check for impulsive exit (displacement)
                impulsive_exit = self._check_impulsive_move(candlesticks, i, "bullish")

                # Check continuation strength
                continuation_strength = 0
                for j in range(
                    i + 1, min(i + SMCConfig.CONTINUATION_LOOKAHEAD, len(candlesticks))
                ):
                    if candlesticks[j]["close"] > current["high"]:
                        continuation_strength += 1

                if continuation_strength >= 2 and impulsive_exit:
                    order_block = OrderBlock(
                        price_high=current["high"],
                        price_low=current["low"],
                        timestamp=current["timestamp"],
                        direction="bullish",
                        strength=continuation_strength / 3.0,
                        volume_confirmed=volume_confirmed,
                        impulsive_exit=impulsive_exit,
                    )
                    order_blocks.append(order_block)

            # Look for strong bearish candles
            elif (
                current["close"] < current["open"]
                and current["high"] - current["low"]
                > (prev["close"] - current["open"]) * 2
            ):

                # Check for impulsive exit
                impulsive_exit = self._check_impulsive_move(candlesticks, i, "bearish")

                continuation_strength = 0
                for j in range(
                    i + 1, min(i + SMCConfig.CONTINUATION_LOOKAHEAD, len(candlesticks))
                ):
                    if candlesticks[j]["close"] < current["low"]:
                        continuation_strength += 1

                if continuation_strength >= 2 and impulsive_exit:
                    order_block = OrderBlock(
                        price_high=current["high"],
                        price_low=current["low"],
                        timestamp=current["timestamp"],
                        direction="bearish",
                        strength=continuation_strength / 3.0,
                        volume_confirmed=volume_confirmed,
                        impulsive_exit=impulsive_exit,
                    )
                    order_blocks.append(order_block)

        # Filter order blocks by age - only keep OBs within max age and not mitigated
        if order_blocks and len(candlesticks) > 0:
            current_time = candlesticks[-1]["timestamp"]
            valid_obs = []
            for ob in order_blocks:
                # Calculate age in candles
                age = len([c for c in candlesticks if c["timestamp"] > ob.timestamp])
                if age <= SMCConfig.OB_MAX_AGE_CANDLES and not ob.mitigated:
                    valid_obs.append(ob)
            order_blocks = valid_obs
        
        # Return last 15 valid order blocks to capture institutional zones from extended 200-candle daily lookback
        # Older OBs from institutional timeframes are prioritized for confluence
        return order_blocks[-15:]

    def find_fair_value_gaps(self, candlesticks: List[Dict]) -> List[FairValueGap]:
        """Enhanced FVG detection with ATR filtering and alignment scoring"""
        fvgs = []

        if len(candlesticks) < SMCConfig.MIN_CANDLESTICKS_FOR_FVG:
            return fvgs

        # Calculate ATR for gap size filtering with safety floor (dynamically tuned)
        atr = self.calculate_atr(candlesticks)
        if atr <= 0:  # Guard against insufficient data
            current_price = candlesticks[-1]["close"]
            atr = current_price * 0.001  # 0.1% minimum ATR
        min_gap_size = atr * self.fvg_multiplier

        for i in range(1, len(candlesticks) - 1):
            prev_candle = candlesticks[i - 1]
            current = candlesticks[i]
            next_candle = candlesticks[i + 1]

            # Bullish FVG: Gap UP between previous high and next low (price gaps higher)
            if (
                prev_candle["high"] < next_candle["low"]
                and current["close"] > current["open"]
            ):

                gap_size = next_candle["low"] - prev_candle["high"]

                # Apply ATR filter
                if gap_size >= min_gap_size:
                    # ISSUE #23 FIX: Calculate alignment score based on market structure
                    alignment_score = 0.0
                    # Get market structure up to this point for context
                    if i >= 10:  # Need enough data for structure detection
                        recent_structure = self.detect_market_structure(candlesticks[:i+2])
                        if recent_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                            alignment_score = 0.8  # Strong alignment with bullish structure
                        elif recent_structure == MarketStructure.CONSOLIDATION:
                            alignment_score = 0.5  # Neutral consolidation
                        else:
                            alignment_score = 0.3  # Weak alignment or counter-trend
                    else:
                        alignment_score = 0.5  # Default for early candles
                    
                    fvg = FairValueGap(
                        gap_high=next_candle["low"],
                        gap_low=prev_candle["high"],
                        timestamp=current["timestamp"],
                        direction="bullish",
                        atr_size=gap_size / atr,
                        age_candles=0,
                        alignment_score=alignment_score,
                    )
                    fvgs.append(fvg)

            # Bearish FVG: Gap DOWN between previous low and next high (price gaps lower)
            elif (
                prev_candle["low"] > next_candle["high"]
                and current["close"] < current["open"]
            ):

                gap_size = prev_candle["low"] - next_candle["high"]

                # Apply ATR filter
                if gap_size >= min_gap_size:
                    # ISSUE #23 FIX: Calculate alignment score based on market structure
                    alignment_score = 0.0
                    # Get market structure up to this point for context
                    if i >= 10:  # Need enough data for structure detection
                        recent_structure = self.detect_market_structure(candlesticks[:i+2])
                        if recent_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                            alignment_score = 0.8  # Strong alignment with bearish structure
                        elif recent_structure == MarketStructure.CONSOLIDATION:
                            alignment_score = 0.5  # Neutral consolidation
                        else:
                            alignment_score = 0.3  # Weak alignment or counter-trend
                    else:
                        alignment_score = 0.5  # Default for early candles
                    
                    fvg = FairValueGap(
                        gap_high=prev_candle["low"],
                        gap_low=next_candle["high"],
                        timestamp=current["timestamp"],
                        direction="bearish",
                        atr_size=gap_size / atr,
                        age_candles=0,
                        alignment_score=alignment_score,
                    )
                    fvgs.append(fvg)

        # Filter out old FVGs and update age
        current_time = candlesticks[-1]["timestamp"]
        valid_fvgs = []

        for fvg in fvgs:
            age = len([c for c in candlesticks if c["timestamp"] > fvg.timestamp])
            if age <= SMCConfig.FVG_MAX_AGE_CANDLES:
                fvg.age_candles = age
                valid_fvgs.append(fvg)

        # Return last 20 valid FVGs to capture institutional zones from extended 200-candle daily lookback
        # Older FVGs from institutional timeframes are prioritized for confluence
        return valid_fvgs[-20:]

    def find_liquidity_pools(self, candlesticks: List[Dict]) -> List[LiquidityPool]:
        """Identify liquidity pools - areas where stops are likely clustered"""
        liquidity_pools = []

        # Find recent swing highs and lows as potential liquidity areas
        swing_highs = self._find_swing_highs(candlesticks)
        swing_lows = self._find_swing_lows(candlesticks)

        # Recent highs likely have sell-side liquidity above them
        for high in swing_highs[-SMCConfig.RECENT_SWING_LOOKBACK :]:
            pool = LiquidityPool(
                price=high["high"], type="sell_side", strength=high.get("strength", 1.0)
            )
            liquidity_pools.append(pool)

        # Recent lows likely have buy-side liquidity below them
        for low in swing_lows[-SMCConfig.RECENT_SWING_LOOKBACK:]:
            pool = LiquidityPool(
                price=low["low"], type="buy_side", strength=low.get("strength", 1.0)
            )
            liquidity_pools.append(pool)

        return liquidity_pools

    def calculate_rsi(self, candlesticks: List[Dict], period: int = 14) -> float:
        """Calculate RSI for momentum confirmation"""
        if len(candlesticks) < period + 1:
            return 50.0

        gains = []
        losses = []

        for i in range(1, len(candlesticks)):
            change = candlesticks[i]["close"] - candlesticks[i - 1]["close"]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        if len(gains) < period:
            return 50.0

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def calculate_atr(
        self, candlesticks: List[Dict], period: int = SMCConfig.ATR_PERIOD
    ) -> float:
        """Calculate Average True Range for volatility measurement"""
        if len(candlesticks) < period + 1:
            return 0.0

        true_ranges = []
        for i in range(1, len(candlesticks)):
            current = candlesticks[i]
            prev = candlesticks[i - 1]

            high_low = current["high"] - current["low"]
            high_prev_close = abs(current["high"] - prev["close"])
            low_prev_close = abs(current["low"] - prev["close"])

            true_range = max(high_low, high_prev_close, low_prev_close)
            true_ranges.append(true_range)

        # Calculate EMA-smoothed ATR
        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges)

        # Initial SMA for first ATR value
        atr = sum(true_ranges[:period]) / period

        # Apply EMA smoothing for subsequent values
        multiplier = SMCConfig.ATR_SMOOTHING_FACTOR / (period + 1)
        for i in range(period, len(true_ranges)):
            atr = (true_ranges[i] * multiplier) + (atr * (1 - multiplier))

        return atr

    def calculate_moving_averages(self, candlesticks: List[Dict]) -> Dict[str, float]:
        """Calculate key moving averages for trend analysis"""
        if len(candlesticks) < 50:
            return {}

        closes = [c["close"] for c in candlesticks]

        return {
            "ema_20": self._calculate_ema(closes, 20),
            "ema_50": self._calculate_ema(closes, 50),
            "sma_200": (
                sum(closes[-200:]) / 200
                if len(closes) >= 200
                else sum(closes) / len(closes)
            ),
        }

    def _analyze_bullish_signals(
        self,
        h1_structure,
        h4_structure,
        order_blocks,
        fvgs,
        current_price,
        rsi,
        mas,
        reasoning,
    ):
        """Analyze bullish signal strength."""
        bullish_signals = 0

        # H4 timeframe structure
        if h4_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
            bullish_signals += 2
            reasoning.append(f"H4 {h4_structure.value}")

        # H1 timeframe structure
        if h1_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
            bullish_signals += 1
            reasoning.append(f"H1 {h1_structure.value}")

        # Check for bullish order blocks near current price
        for ob in order_blocks:
            if (
                ob.direction == "bullish"
                and ob.price_low <= current_price <= ob.price_high * 1.02
            ):
                bullish_signals += 1
                reasoning.append("Price at bullish order block")
                break

        # Check for unfilled bullish FVGs
        for fvg in fvgs:
            if (
                fvg.direction == "bullish"
                and not fvg.filled
                and fvg.gap_low <= current_price <= fvg.gap_high
            ):
                bullish_signals += 1
                reasoning.append("Price in bullish FVG")
                break

        # RSI confirmation
        if rsi < 30:
            bullish_signals += 1
            reasoning.append("RSI oversold")
        elif 30 <= rsi <= 50:
            bullish_signals += 0.5
            reasoning.append("RSI neutral-bullish")

        # Moving average confirmation
        if mas and current_price > mas.get("ema_20", current_price):
            bullish_signals += 0.5
            reasoning.append("Above EMA 20")

        return bullish_signals

    def _analyze_bearish_signals(
        self,
        h1_structure,
        h4_structure,
        order_blocks,
        fvgs,
        current_price,
        rsi,
        mas,
        reasoning,
    ):
        """Analyze bearish signal strength."""
        bearish_signals = 0

        # H4 timeframe structure
        if h4_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
            bearish_signals += 2
            reasoning.append(f"H4 {h4_structure.value}")

        # H1 timeframe structure
        if h1_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
            bearish_signals += 1
            reasoning.append(f"H1 {h1_structure.value}")

        # Check for bearish order blocks near current price
        for ob in order_blocks:
            if (
                ob.direction == "bearish"
                and ob.price_low * 0.98 <= current_price <= ob.price_high
            ):
                bearish_signals += 1
                reasoning.append("Price at bearish order block")
                break

        # Check for unfilled bearish FVGs
        for fvg in fvgs:
            if (
                fvg.direction == "bearish"
                and not fvg.filled
                and fvg.gap_low <= current_price <= fvg.gap_high
            ):
                bearish_signals += 1
                reasoning.append("Price in bearish FVG")
                break

        # RSI confirmation
        if rsi > 70:
            bearish_signals += 1
            reasoning.append("RSI overbought")
        elif 50 <= rsi <= 70:
            bearish_signals += 0.5
            reasoning.append("RSI neutral-bearish")

        # Moving average confirmation
        if mas and current_price < mas.get("ema_20", current_price):
            bearish_signals += 0.5
            reasoning.append("Below EMA 20")

        return bearish_signals

    def _calculate_long_trade_levels(self, current_price, order_blocks, candlesticks):
        """Calculate entry, stop loss, and take profits for long trades using stable SMC analysis."""
        # ISSUE #13 FIX: Use centralized ATR calculation for consistency
        atr = self.calculate_atr(candlesticks)
        
        # Apply ATR floor to handle low volatility and insufficient data cases
        min_atr = current_price * 0.001  # 0.1% minimum ATR
        if atr <= 0:
            # Fallback to minimum ATR if centralized method fails
            atr = min_atr
            logging.warning(f"ATR calculation returned zero for long trade, using minimum ATR: {min_atr:.6f}")
        
        atr = max(atr, min_atr)  # Ensure minimum ATR
        
        # Find swing highs and lows for natural levels
        swing_highs = self._find_swing_highs(candlesticks)
        swing_lows = self._find_swing_lows(candlesticks)
        
        # FIXED: Entry price calculation using ABSOLUTE structural levels (not relative to current price)
        entry_price = None
        
        # Find the most relevant bullish order block for entry (SMC discount zone)
        # Use structural significance rather than proximity to current price
        if order_blocks:
            # Score order blocks by strength, recency, and structural validity
            scored_obs = []
            for ob in order_blocks:
                if ob.direction == "bullish":
                    # Calculate structural score (higher is better)
                    strength_score = ob.strength * 10  # Base strength
                    volume_score = 5 if ob.volume_confirmed else 0
                    impulsive_score = 5 if ob.impulsive_exit else 0
                    
                    # Recency score - more recent order blocks get higher priority
                    if hasattr(ob, 'timestamp') and candlesticks:
                        ob_index = next((i for i, c in enumerate(candlesticks) 
                                       if c["timestamp"] >= ob.timestamp), len(candlesticks)-1)
                        recency_score = max(0, 10 - (len(candlesticks) - ob_index) / 10)
                    else:
                        recency_score = 5  # Default middle score
                    
                    total_score = strength_score + volume_score + impulsive_score + recency_score
                    scored_obs.append((ob, total_score))
            
            # Sort by score and get the best valid order block
            scored_obs.sort(key=lambda x: x[1], reverse=True)
            
            # Use the highest scoring order block that's below current price (discount zone)
            best_ob = None
            for ob, score in scored_obs:
                if ob.price_high < current_price:  # Must be below current price for long entry
                    best_ob = ob
                    break
            
            if best_ob:
                # Use the top of the order block as entry point
                entry_price = best_ob.price_high
            else:
                # Check if current price is inside any high-scoring bullish order block
                for ob, score in scored_obs[:3]:  # Check top 3 scoring OBs
                    if ob.price_low <= current_price <= ob.price_high:
                        # Already inside OB - use current price for immediate entry
                        entry_price = current_price
                        break
        
        # Fallback if no suitable order block found
        if entry_price is None:
            # Use most recent swing low + small buffer as entry (structural approach)
            if swing_lows:
                recent_swing_low = swing_lows[-1]["low"]
                entry_buffer = max(atr * 0.2, recent_swing_low * 0.003)  # 0.3% minimum
                entry_price = recent_swing_low + entry_buffer
            else:
                # Final fallback: use structural discount from current price
                entry_price = current_price - max(atr * 0.5, current_price * 0.005)  # 0.5% minimum

        # Stop loss using swing lows and order block structure with safety checks
        stop_loss_candidates = []
        
        # Add recent swing lows as potential stop loss levels
        recent_swings = swing_lows[-3:] if len(swing_lows) >= 3 else swing_lows
        for swing in recent_swings:
            if swing["low"] < current_price and swing["low"] > 0:
                stop_loss_candidates.append(swing["low"])
        
        # Add bullish order block lows below current price
        for ob in order_blocks:
            if (ob.direction == "bullish" and ob.price_low < current_price 
                and ob.price_low > 0):
                stop_loss_candidates.append(ob.price_low)
        
        # Calculate stop loss with safety constraints
        if stop_loss_candidates:
            stop_loss = max(stop_loss_candidates)
        else:
            # Fallback: percentage-based stop loss
            stop_loss = current_price * 0.97  # 3% below
            
        # Ensure minimum risk distance and valid price constraints
        min_risk_distance = max(atr * 1.0, current_price * 0.005)  # 0.5% minimum
        max_risk_distance = current_price * 0.15  # Maximum 15% risk
        
        # Adjust stop loss if too close to entry
        if entry_price - stop_loss < min_risk_distance:
            stop_loss = entry_price - min_risk_distance
            
        # Ensure stop loss doesn't exceed maximum risk
        if entry_price - stop_loss > max_risk_distance:
            stop_loss = entry_price - max_risk_distance
            
        # ISSUE #11 FIX: Reordered validation logic for proper constraint sequence
        # First, ensure minimum price floor
        stop_loss = max(stop_loss, current_price * 0.01)  # At least 1% of price
        
        # Then, ensure it's below entry price for longs
        if stop_loss >= entry_price:
            stop_loss = entry_price * 0.995  # Force 0.5% below entry
        
        # Finally, cap at maximum (ensure it stays below entry)
        stop_loss = min(stop_loss, entry_price * 0.99)  # Below entry price

        # Take profits with guaranteed valid levels
        from config import TradingConfig
        tp_rr_ratios = getattr(TradingConfig, 'TP_RR_RATIOS', [1.0, 2.0, 3.0])
        
        take_profits = []
        base_reward = max(atr * 0.8, current_price * 0.008)  # Minimum 0.8% reward
        
        # TP1: Next resistance or minimum reward
        tp1_candidates = []
        
        # Check for swing highs above entry price
        for swing in swing_highs[-5:]:
            if swing["high"] > entry_price + base_reward:
                tp1_candidates.append(swing["high"])
        
        # Check for bearish order blocks above entry price
        for ob in order_blocks:
            if (ob.direction == "bearish" and ob.price_low > entry_price + base_reward):
                tp1_candidates.append(ob.price_low)
        
        # Select TP1 using config R:R ratio
        if tp1_candidates:
            tp1 = min(tp1_candidates)
        else:
            tp1 = entry_price + max(atr * tp_rr_ratios[0], current_price * 0.01)
        
        take_profits.append(tp1)
        
        # TP2: Extended target, ensuring it's above TP1 (using config R:R ratio)
        tp2_base = entry_price + max(atr * tp_rr_ratios[1], current_price * 0.02)
        extended_highs = [s["high"] for s in swing_highs[-10:] if s["high"] > tp1]
        if extended_highs:
            tp2 = min(extended_highs)
            tp2 = max(tp2, tp1 * 1.01)  # Ensure TP2 > TP1
        else:
            tp2 = max(tp2_base, tp1 * 1.01)
        
        take_profits.append(tp2)
        
        # TP3: Full extension, ensuring it's above TP2 (using config R:R ratio)
        tp3 = max(
            entry_price + max(atr * tp_rr_ratios[2], current_price * 0.03),
            tp2 * 1.01
        )
        take_profits.append(tp3)
        
        # Final validation: ensure all TPs are unique and ordered
        take_profits = sorted(list(set(take_profits)))
        if len(take_profits) < 3:
            # Fill missing TPs with calculated levels
            while len(take_profits) < 3:
                last_tp = take_profits[-1]
                next_tp = last_tp * 1.01  # 1% increment
                take_profits.append(next_tp)
        
        # ISSUE #12 FIX: Explicit validation for LONG TP ordering
        if len(take_profits) >= 3:
            if not (take_profits[0] < take_profits[1] < take_profits[2]):
                logging.error(f"Invalid LONG TP ordering detected: TP1={take_profits[0]:.2f}, TP2={take_profits[1]:.2f}, TP3={take_profits[2]:.2f}")
                # Force proper ordering
                take_profits = sorted(take_profits)
                logging.info(f"Corrected to: TP1={take_profits[0]:.2f}, TP2={take_profits[1]:.2f}, TP3={take_profits[2]:.2f}")

        return entry_price, stop_loss, take_profits[:3]  # Return exactly 3 TPs

    def _calculate_short_trade_levels(self, current_price, order_blocks, candlesticks):
        """Calculate entry, stop loss, and take profits for short trades using stable SMC analysis."""
        # ISSUE #13 FIX: Use centralized ATR calculation for consistency
        atr = self.calculate_atr(candlesticks)
        
        # Apply ATR floor to handle low volatility and insufficient data cases
        min_atr = current_price * 0.001  # 0.1% minimum ATR
        if atr <= 0:
            # Fallback to minimum ATR if centralized method fails
            atr = min_atr
            logging.warning(f"ATR calculation returned zero for short trade, using minimum ATR: {min_atr:.6f}")
        
        atr = max(atr, min_atr)  # Ensure minimum ATR
        
        # Find swing highs and lows for natural levels
        swing_highs = self._find_swing_highs(candlesticks)
        swing_lows = self._find_swing_lows(candlesticks)
        
        # FIXED: Entry price calculation using ABSOLUTE structural levels (not relative to current price)
        entry_price = None
        
        # Find the most relevant bearish order block for entry (SMC premium zone)
        # Use structural significance rather than proximity to current price
        if order_blocks:
            # Score order blocks by strength, recency, and structural validity
            scored_obs = []
            for ob in order_blocks:
                if ob.direction == "bearish":
                    # Calculate structural score (higher is better)
                    strength_score = ob.strength * 10  # Base strength
                    volume_score = 5 if ob.volume_confirmed else 0
                    impulsive_score = 5 if ob.impulsive_exit else 0
                    
                    # Recency score - more recent order blocks get higher priority
                    if hasattr(ob, 'timestamp') and candlesticks:
                        ob_index = next((i for i, c in enumerate(candlesticks) 
                                       if c["timestamp"] >= ob.timestamp), len(candlesticks)-1)
                        recency_score = max(0, 10 - (len(candlesticks) - ob_index) / 10)
                    else:
                        recency_score = 5  # Default middle score
                    
                    total_score = strength_score + volume_score + impulsive_score + recency_score
                    scored_obs.append((ob, total_score))
            
            # Sort by score and get the best valid order block
            scored_obs.sort(key=lambda x: x[1], reverse=True)
            
            # Use the highest scoring order block that's above current price (premium zone)
            best_ob = None
            for ob, score in scored_obs:
                if ob.price_low > current_price:  # Must be above current price for short entry
                    best_ob = ob
                    break
            
            if best_ob:
                # Use the bottom of the order block as entry point
                entry_price = best_ob.price_low
            else:
                # Check if current price is inside any high-scoring bearish order block
                for ob, score in scored_obs[:3]:  # Check top 3 scoring OBs
                    if ob.price_low <= current_price <= ob.price_high:
                        # Already inside OB - use current price for immediate entry
                        entry_price = current_price
                        break
        
        # Fallback if no suitable order block found
        if entry_price is None:
            # Use most recent swing high - small buffer as entry (structural approach)
            if swing_highs:
                recent_swing_high = swing_highs[-1]["high"]
                entry_buffer = max(atr * 0.2, recent_swing_high * 0.003)  # 0.3% minimum
                entry_price = recent_swing_high - entry_buffer
                
                # ISSUE #10 FIX: Ensure premium zone entry (must be >= current price for shorts)
                if entry_price < current_price:
                    entry_price = current_price + max(atr * 0.3, current_price * 0.003)
                    logging.info(f"Short entry adjusted to premium zone: ${entry_price:.2f} (was below current price)")
            else:
                # Final fallback: use structural premium from current price
                entry_price = current_price + max(atr * 0.5, current_price * 0.005)  # 0.5% minimum

        # Stop loss using swing highs and order block structure with safety checks
        stop_loss_candidates = []
        
        # Add recent swing highs as potential stop loss levels
        recent_swings = swing_highs[-3:] if len(swing_highs) >= 3 else swing_highs
        for swing in recent_swings:
            if swing["high"] > current_price:
                stop_loss_candidates.append(swing["high"])
        
        # Add bearish order block highs above current price
        for ob in order_blocks:
            if (ob.direction == "bearish" and ob.price_high > current_price):
                stop_loss_candidates.append(ob.price_high)
        
        # Calculate stop loss with safety constraints
        if stop_loss_candidates:
            stop_loss = min(stop_loss_candidates)
        else:
            # Fallback: percentage-based stop loss
            stop_loss = current_price * 1.03  # 3% above
            
        # Ensure minimum risk distance and valid price constraints
        min_risk_distance = max(atr * 1.0, current_price * 0.005)  # 0.5% minimum
        max_risk_distance = current_price * 0.15  # Maximum 15% risk
        
        # Adjust stop loss if too close to entry
        if stop_loss - entry_price < min_risk_distance:
            stop_loss = entry_price + min_risk_distance
            
        # Ensure stop loss doesn't exceed maximum risk
        if stop_loss - entry_price > max_risk_distance:
            stop_loss = entry_price + max_risk_distance
            
        # ISSUE #11 FIX: Reordered validation logic for proper constraint sequence
        # First, ensure stop loss is above entry for shorts
        if stop_loss <= entry_price:
            stop_loss = entry_price * 1.005  # Force 0.5% above entry
        
        # Finally, ensure minimum distance above entry
        stop_loss = max(stop_loss, entry_price * 1.01)  # At least 1% above entry price

        # Take profits with guaranteed valid levels
        from config import TradingConfig
        tp_rr_ratios = getattr(TradingConfig, 'TP_RR_RATIOS', [1.0, 2.0, 3.0])
        
        take_profits = []
        base_reward = max(atr * 0.8, current_price * 0.008)  # Minimum 0.8% reward
        
        # TP1: Next support or minimum reward
        tp1_candidates = []
        
        # Check for swing lows below entry price
        for swing in swing_lows[-5:]:
            if swing["low"] < entry_price - base_reward and swing["low"] > 0:
                tp1_candidates.append(swing["low"])
        
        # Check for bullish order blocks below entry price
        for ob in order_blocks:
            if (ob.direction == "bullish" and ob.price_high < entry_price - base_reward
                and ob.price_high > 0):
                tp1_candidates.append(ob.price_high)
        
        # Select TP1 using config R:R ratio
        if tp1_candidates:
            tp1 = max(tp1_candidates)
        else:
            tp1 = entry_price - max(atr * tp_rr_ratios[0], current_price * 0.01)
        
        # Ensure TP1 is positive and below entry
        tp1 = max(tp1, current_price * 0.01)  # At least 1% of current price
        tp1 = min(tp1, entry_price * 0.99)  # Below entry price
        
        take_profits.append(tp1)
        
        # TP2: Extended target, ensuring it's below TP1 (using config R:R ratio)
        tp2_base = entry_price - max(atr * tp_rr_ratios[1], current_price * 0.02)
        extended_lows = [s["low"] for s in swing_lows[-10:] if s["low"] < tp1 and s["low"] > 0]
        if extended_lows:
            tp2 = max(extended_lows)
            tp2 = min(tp2, tp1 * 0.99)  # Ensure TP2 < TP1
        else:
            tp2 = min(tp2_base, tp1 * 0.99)
        
        # Ensure TP2 is positive
        tp2 = max(tp2, current_price * 0.005)  # At least 0.5% of current price
        
        take_profits.append(tp2)
        
        # TP3: Full extension, ensuring it's below TP2 (using config R:R ratio)
        tp3 = min(
            entry_price - max(atr * tp_rr_ratios[2], current_price * 0.03),
            tp2 * 0.99
        )
        
        # Ensure TP3 is positive
        tp3 = max(tp3, current_price * 0.003)  # At least 0.3% of current price
        
        take_profits.append(tp3)
        
        # Final validation: ensure all TPs are unique, ordered, and positive
        take_profits = [tp for tp in take_profits if tp > 0]  # Remove any negative TPs
        take_profits = sorted(list(set(take_profits)), reverse=True)  # Sort descending for shorts
        
        if len(take_profits) < 3:
            # Fill missing TPs with calculated levels
            while len(take_profits) < 3:
                if len(take_profits) == 0:
                    next_tp = entry_price * 0.99
                else:
                    last_tp = take_profits[-1]
                    next_tp = last_tp * 0.99  # 1% decrement
                
                # Ensure it's positive
                next_tp = max(next_tp, current_price * 0.001)
                take_profits.append(next_tp)
        
        # ISSUE #12 FIX: Explicit validation for SHORT TP ordering
        if len(take_profits) >= 3:
            if not (take_profits[0] > take_profits[1] > take_profits[2]):
                logging.error(f"Invalid SHORT TP ordering detected: TP1={take_profits[0]:.2f}, TP2={take_profits[1]:.2f}, TP3={take_profits[2]:.2f}")
                # Force proper ordering (descending for shorts)
                take_profits = sorted(take_profits, reverse=True)
                logging.info(f"Corrected to: TP1={take_profits[0]:.2f}, TP2={take_profits[1]:.2f}, TP3={take_profits[2]:.2f}")

        return entry_price, stop_loss, take_profits[:3]  # Return exactly 3 TPs

    def _determine_trade_direction_and_levels_hybrid(
        self, h1_structure, h4_structure, bullish_signals, bearish_signals, 
        current_price, order_blocks, candlesticks, liquidity_sweeps, rsi
    ):
        """Determine trade direction using hybrid logic (trend-following + counter-trend reversal)."""
        direction = None
        confidence = 0.0
        entry_price = current_price
        stop_loss = 0.0
        take_profits = []

        # Categorize structures for easier checking
        bullish_structures = [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]
        bearish_structures = [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]
        
        h1_bullish = h1_structure in bullish_structures
        h1_bearish = h1_structure in bearish_structures
        h4_bullish = h4_structure in bullish_structures
        h4_bearish = h4_structure in bearish_structures
        
        # 1. TREND-FOLLOWING TRADES (strict confluence)
        if h1_bullish and h4_bullish:
            # Both H1 and H4 bullish - trend-following long
            direction = "long"
            confidence = min(bullish_signals / 5.0, 1.0)
            if confidence >= 0.6:  # Minimum confidence for trend trades
                entry_price, stop_loss, take_profits = self._calculate_long_trade_levels(
                    current_price, order_blocks, candlesticks
                )
            else:
                direction = None  # Filter weak trend signals
                confidence = 0.0
                
        elif h1_bearish and h4_bearish:
            # Both H1 and H4 bearish - trend-following short
            direction = "short"
            confidence = min(bearish_signals / 5.0, 1.0)
            if confidence >= 0.6:  # Minimum confidence for trend trades
                entry_price, stop_loss, take_profits = self._calculate_short_trade_levels(
                    current_price, order_blocks, candlesticks
                )
            else:
                direction = None  # Filter weak trend signals
                confidence = 0.0
                
        # 2. COUNTER-TREND REVERSAL TRADES (special case)
        elif h1_bullish and h4_bearish:
            # H1 CHoCH bullish, H4 bearish - potential reversal long
            confirmed_buy_sweeps = [s for s in liquidity_sweeps.get("buy_side", []) if s.get("confirmed", False)]
            if rsi < 30:  # RSI exhaustion for longs (SMC standard)
                direction = "long"
                confidence = min(bullish_signals / 5.0, 1.0)
                
                # Note: Sweep bonuses now applied only in Phase 3 to avoid triple counting
                
                if confidence >= 0.6:  # Lowered confidence threshold for counter-trend
                    entry_price, stop_loss, take_profits = self._calculate_long_trade_levels(
                        current_price, order_blocks, candlesticks
                    )
                else:
                    direction = None
                    confidence = 0.0
                    
        elif h1_bearish and h4_bullish:
            # H1 CHoCH bearish, H4 bullish - potential reversal short  
            confirmed_sell_sweeps = [s for s in liquidity_sweeps.get("sell_side", []) if s.get("confirmed", False)]
            if rsi > 70:  # RSI exhaustion for shorts (SMC standard)
                direction = "short"
                confidence = min(bearish_signals / 5.0, 1.0)
                
                # Note: Sweep bonuses now applied only in Phase 3 to avoid triple counting
                
                if confidence >= 0.6:  # Lowered confidence threshold for counter-trend
                    entry_price, stop_loss, take_profits = self._calculate_short_trade_levels(
                        current_price, order_blocks, candlesticks
                    )
                else:
                    direction = None
                    confidence = 0.0
        
        # If neither condition is met, do not generate signal
        return direction, confidence, entry_price, stop_loss, take_profits

    def _calculate_trade_metrics_enhanced(
        self, entry_price, stop_loss, take_profits, confidence, liquidity_sweeps, order_blocks, fvgs
    ):
        """Calculate risk-reward ratio and signal strength (confidence bonuses handled in Phase 3 only)."""
        # Calculate risk-reward ratio
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profits[0] - entry_price) if take_profits else risk
        rr_ratio = reward / risk if risk > 0 else 1.0

        # Use base confidence without duplicate bonuses (Phase 3 handles all bonuses)
        effective_confidence = confidence

        # Determine signal strength based on confidence
        if effective_confidence >= 0.9:
            signal_strength = SignalStrength.VERY_STRONG
        elif effective_confidence >= 0.8:
            signal_strength = SignalStrength.STRONG
        elif effective_confidence >= 0.7:
            signal_strength = SignalStrength.MODERATE
        else:
            signal_strength = SignalStrength.WEAK

        return rr_ratio, signal_strength

    def _get_htf_bias(self, d1_data: List[Dict], h4_data: List[Dict]) -> Dict:
        """
        Phase 2: Determine high timeframe bias from Daily and H4 structure
        
        Args:
            d1_data: Daily candlestick data
            h4_data: 4-hour candlestick data
            
        Returns:
            Dict with HTF bias information including direction, confidence, and liquidity targets
        """
        try:
            if not d1_data or not h4_data:
                return {"bias": "neutral", "confidence": 0.0, "liquidity_targets": [], "reason": "Insufficient data"}
            
            if len(d1_data) < SMCConfig.TIMEFRAME_1D_LIMIT:
                logging.warning(f"Insufficient daily data for institutional analysis: {len(d1_data)} / {SMCConfig.TIMEFRAME_1D_LIMIT} required")
                return {
                    "bias": "neutral", 
                    "confidence": 0.0, 
                    "liquidity_targets": [], 
                    "reason": f"Insufficient daily data ({len(d1_data)}/{SMCConfig.TIMEFRAME_1D_LIMIT})",
                    "d1_structure": "unknown",
                    "h4_structure": "unknown",
                    "bullish_signals": 0,
                    "bearish_signals": 0
                }
            
            d1_structure = self.detect_market_structure(d1_data)
            h4_structure = self.detect_market_structure(h4_data)
            
            d1_liquidity = self.find_liquidity_pools(d1_data)
            h4_order_blocks = self.find_order_blocks(h4_data)
            
            bullish_bias_count = 0
            bearish_bias_count = 0
            reasoning = []
            
            if d1_structure == MarketStructure.BULLISH_BOS:
                bullish_bias_count += 2
                reasoning.append("Daily bullish break of structure (strong)")
            elif d1_structure == MarketStructure.BULLISH_CHoCH:
                bullish_bias_count += 1
                reasoning.append("Daily bullish change of character")
            elif d1_structure == MarketStructure.BEARISH_BOS:
                bearish_bias_count += 2
                reasoning.append("Daily bearish break of structure (strong)")
            elif d1_structure == MarketStructure.BEARISH_CHoCH:
                bearish_bias_count += 1
                reasoning.append("Daily bearish change of character")
            
            if h4_structure == MarketStructure.BULLISH_BOS or h4_structure == MarketStructure.BULLISH_CHoCH:
                bullish_bias_count += 1
                reasoning.append("H4 bullish structure alignment")
            elif h4_structure == MarketStructure.BEARISH_BOS or h4_structure == MarketStructure.BEARISH_CHoCH:
                bearish_bias_count += 1
                reasoning.append("H4 bearish structure alignment")
            
            swing_points = [{"price": p["high"], "timestamp": p["timestamp"]} for p in self._find_swing_highs(d1_data, timeframe="1d")]
            swing_points += [{"price": p["low"], "timestamp": p["timestamp"]} for p in self._find_swing_lows(d1_data, timeframe="1d")]
            d1_trend = self._calculate_trend(sorted(swing_points, key=lambda x: x["timestamp"]), "price")
            if d1_trend == "bullish":
                bullish_bias_count += 1
                reasoning.append("Daily trend is bullish")
            elif d1_trend == "bearish":
                bearish_bias_count += 1
                reasoning.append("Daily trend is bearish")
            
            buy_side_liquidity = [lp for lp in d1_liquidity if lp.type == "buy_side" and not lp.swept]
            sell_side_liquidity = [lp for lp in d1_liquidity if lp.type == "sell_side" and not lp.swept]
            
            liquidity_targets = []
            if bullish_bias_count > bearish_bias_count:
                bias = "bullish"
                liquidity_targets = [{"price": lp.price, "type": lp.type, "strength": lp.strength} 
                                     for lp in sorted(sell_side_liquidity, key=lambda x: x.price, reverse=True)[:3]]
            elif bearish_bias_count > bullish_bias_count:
                bias = "bearish"
                liquidity_targets = [{"price": lp.price, "type": lp.type, "strength": lp.strength} 
                                     for lp in sorted(buy_side_liquidity, key=lambda x: x.price)[:3]]
            else:
                bias = "neutral"
                liquidity_targets = []
            
            max_count = max(bullish_bias_count, bearish_bias_count, 1)
            confidence = max_count / 5.0
            confidence = min(confidence, 1.0)
            
            return {
                "bias": bias,
                "confidence": confidence,
                "liquidity_targets": liquidity_targets,
                "reason": "; ".join(reasoning) if reasoning else "No clear bias",
                "d1_structure": d1_structure.value if hasattr(d1_structure, 'value') else str(d1_structure),
                "h4_structure": h4_structure.value if hasattr(h4_structure, 'value') else str(h4_structure),
                "bullish_signals": bullish_bias_count,
                "bearish_signals": bearish_bias_count
            }
            
        except Exception as e:
            logging.error(f"Error in _get_htf_bias: {e}")
            return {"bias": "neutral", "confidence": 0.0, "liquidity_targets": [], "reason": f"Error: {str(e)}"}

    def _get_intermediate_structure(self, h1_data: List[Dict], h4_data: List[Dict]) -> Dict:
        """
        Phase 2: Analyze H4/H1 for order blocks, FVGs, and structure shifts
        
        Args:
            h1_data: 1-hour candlestick data
            h4_data: 4-hour candlestick data
            
        Returns:
            Dict with intermediate structure information including order blocks, FVGs, and POI levels
        """
        try:
            if not h1_data or not h4_data:
                return {"valid": False, "reason": "Insufficient data", "order_blocks": [], "fvgs": [], "poi_levels": []}
            
            h1_order_blocks = self.find_order_blocks(h1_data)
            h4_order_blocks = self.find_order_blocks(h4_data)
            
            h1_fvgs = self.find_fair_value_gaps(h1_data)
            h4_fvgs = self.find_fair_value_gaps(h4_data)
            
            h1_structure = self.detect_market_structure(h1_data)
            h4_structure = self.detect_market_structure(h4_data)
            
            unmitigated_h1_obs = [ob for ob in h1_order_blocks if not ob.mitigated]
            unmitigated_h4_obs = [ob for ob in h4_order_blocks if not ob.mitigated]
            
            unfilled_h1_fvgs = [fvg for fvg in h1_fvgs if not fvg.filled and fvg.age_candles < SMCConfig.FVG_MAX_AGE_CANDLES]
            unfilled_h4_fvgs = [fvg for fvg in h4_fvgs if not fvg.filled and fvg.age_candles < SMCConfig.FVG_MAX_AGE_CANDLES]
            
            poi_levels = []
            for ob in unmitigated_h4_obs[:3]:
                poi_levels.append({
                    "price": (ob.price_high + ob.price_low) / 2,
                    "type": "order_block",
                    "direction": ob.direction,
                    "timeframe": "H4",
                    "strength": ob.strength
                })
            
            for fvg in unfilled_h4_fvgs[:2]:
                poi_levels.append({
                    "price": (fvg.gap_high + fvg.gap_low) / 2,
                    "type": "fvg",
                    "direction": fvg.direction,
                    "timeframe": "H4",
                    "strength": fvg.alignment_score
                })
            
            structure_shift = "none"
            if h1_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                if h4_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                    structure_shift = "bullish_aligned"
                else:
                    structure_shift = "bullish_h1_only"
            elif h1_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                if h4_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                    structure_shift = "bearish_aligned"
                else:
                    structure_shift = "bearish_h1_only"
            
            return {
                "valid": True,
                "order_blocks": unmitigated_h1_obs + unmitigated_h4_obs,
                "h1_order_blocks": unmitigated_h1_obs,
                "h4_order_blocks": unmitigated_h4_obs,
                "fvgs": unfilled_h1_fvgs + unfilled_h4_fvgs,
                "h1_fvgs": unfilled_h1_fvgs,
                "h4_fvgs": unfilled_h4_fvgs,
                "structure": structure_shift,
                "poi_levels": poi_levels,
                "h1_structure": h1_structure.value if hasattr(h1_structure, 'value') else str(h1_structure),
                "h4_structure": h4_structure.value if hasattr(h4_structure, 'value') else str(h4_structure)
            }
            
        except Exception as e:
            logging.error(f"Error in _get_intermediate_structure: {e}")
            return {"valid": False, "reason": f"Error: {str(e)}", "order_blocks": [], "fvgs": [], "poi_levels": []}

    def _get_execution_signal_15m(self, m15_data: List[Dict], htf_bias: Dict, intermediate_structure: Dict) -> Dict:
        """
        Phase 2: Generate precise 15m execution signal aligned with HTF
        
        Args:
            m15_data: 15-minute candlestick data
            htf_bias: High timeframe bias from _get_htf_bias()
            intermediate_structure: Intermediate structure from _get_intermediate_structure()
            
        Returns:
            Dict with execution signal including entry, SL, TP levels and alignment score
        """
        try:
            if not m15_data or len(m15_data) < 20:
                return {
                    "signal": None,
                    "entry": 0.0,
                    "sl": 0.0,
                    "alignment_score": 0.0,
                    "reason": "Insufficient 15m data"
                }
            
            if htf_bias["bias"] == "neutral":
                return {
                    "signal": None,
                    "entry": 0.0,
                    "sl": 0.0,
                    "alignment_score": 0.0,
                    "reason": "No clear HTF bias"
                }
            
            m15_structure = self.detect_market_structure(m15_data)
            current_price = m15_data[-1]["close"]
            
            m15_swing_highs = self._find_swing_highs(m15_data, lookback=3)
            m15_swing_lows = self._find_swing_lows(m15_data, lookback=3)
            
            alignment_score = 0.0
            signal_direction = None
            
            if htf_bias["bias"] == "bullish":
                if m15_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                    alignment_score += 0.5
                    signal_direction = "long"
                elif m15_structure == MarketStructure.CONSOLIDATION:
                    alignment_score += 0.3
                    signal_direction = "long"
                elif m15_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                    alignment_score = 0.1
                    return {
                        "signal": None,
                        "entry": current_price,
                        "sl": 0.0,
                        "alignment_score": alignment_score,
                        "reason": "15m structure conflicts with bullish HTF bias"
                    }
            
            elif htf_bias["bias"] == "bearish":
                if m15_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                    alignment_score += 0.5
                    signal_direction = "short"
                elif m15_structure == MarketStructure.CONSOLIDATION:
                    alignment_score += 0.3
                    signal_direction = "short"
                elif m15_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                    alignment_score = 0.1
                    return {
                        "signal": None,
                        "entry": current_price,
                        "sl": 0.0,
                        "alignment_score": alignment_score,
                        "reason": "15m structure conflicts with bearish HTF bias"
                    }
            
            if intermediate_structure["structure"].startswith(htf_bias["bias"]):
                alignment_score += 0.3
            
            poi_near_price = False
            for poi in intermediate_structure["poi_levels"]:
                price_diff_pct = abs(current_price - poi["price"]) / current_price * 100
                if price_diff_pct < 1.0 and poi["direction"] == htf_bias["bias"]:
                    alignment_score += 0.2
                    poi_near_price = True
                    break
            
            if signal_direction == "long":
                entry_price = current_price
                if m15_swing_lows:
                    last_swing_low = min([sw["low"] for sw in m15_swing_lows[-3:]])
                    sl = last_swing_low * 0.998
                else:
                    sl = current_price * 0.995
            else:
                entry_price = current_price
                if m15_swing_highs:
                    last_swing_high = max([sw["high"] for sw in m15_swing_highs[-3:]])
                    sl = last_swing_high * 1.002
                else:
                    sl = current_price * 1.005
            
            return {
                "signal": signal_direction,
                "entry": entry_price,
                "sl": sl,
                "alignment_score": min(alignment_score, 1.0),
                "reason": f"15m {signal_direction} signal aligned with {htf_bias['bias']} HTF bias" + 
                         (" - near HTF POI" if poi_near_price else ""),
                "m15_structure": m15_structure.value if hasattr(m15_structure, 'value') else str(m15_structure),
                "poi_confluence": poi_near_price
            }
            
        except Exception as e:
            logging.error(f"Error in _get_execution_signal_15m: {e}")
            return {
                "signal": None,
                "entry": 0.0,
                "sl": 0.0,
                "alignment_score": 0.0,
                "reason": f"Error: {str(e)}"
            }

    def _is_signal_still_valid(self, symbol: str, current_price: float) -> bool:
        """Check if existing cached signal is still valid and hasn't been invalidated."""
        if symbol not in self.active_signals:
            return False
            
        signal_data = self.active_signals[symbol]
        signal = signal_data.get('signal')
        expiry_time = signal_data.get('expiry_time', 0)
        
        # Check if signal has expired
        current_time = datetime.utcnow().timestamp()
        if current_time > expiry_time:
            logging.info(f"Signal expired for {symbol}, removing from cache")
            del self.active_signals[symbol]
            return False
        
        # Check if signal has been invalidated by price action
        if signal.direction == "long":
            # Long signal invalidated if price hits stop loss or final take profit
            if current_price <= signal.stop_loss:
                logging.info(f"Long signal invalidated for {symbol}: price hit stop loss ({current_price} <= {signal.stop_loss})")
                del self.active_signals[symbol]
                return False
            elif signal.take_profit_levels and current_price >= signal.take_profit_levels[-1]:
                logging.info(f"Long signal completed for {symbol}: price hit final TP ({current_price} >= {signal.take_profit_levels[-1]})")
                del self.active_signals[symbol]
                return False
        
        elif signal.direction == "short":
            # Short signal invalidated if price hits stop loss or final take profit
            if current_price >= signal.stop_loss:
                logging.info(f"Short signal invalidated for {symbol}: price hit stop loss ({current_price} >= {signal.stop_loss})")
                del self.active_signals[symbol]
                return False
            elif signal.take_profit_levels and current_price <= signal.take_profit_levels[-1]:
                logging.info(f"Short signal completed for {symbol}: price hit final TP ({current_price} <= {signal.take_profit_levels[-1]})")
                del self.active_signals[symbol]
                return False
        
        # Signal is still valid
        return True
    
    def _cache_signal(self, signal: SMCSignal) -> None:
        """Cache a new signal with expiry time."""
        expiry_time = datetime.utcnow().timestamp() + self.signal_timeout
        self.active_signals[signal.symbol] = {
            'signal': signal,
            'expiry_time': expiry_time
        }
        logging.info(f"Cached new {signal.direction} signal for {signal.symbol} with entry at {signal.entry_price}")

    @overload
    def generate_trade_signal(self, symbol: str, return_diagnostics: Literal[False] = False) -> Optional[SMCSignal]: ...
    
    @overload
    def generate_trade_signal(self, symbol: str, return_diagnostics: Literal[True] = ...) -> Tuple[Optional[SMCSignal], Dict]: ...
    
    def generate_trade_signal(self, symbol: str, return_diagnostics: bool = False):
        """Generate comprehensive trade signal based on SMC analysis with caching to prevent duplicate signals
        
        Args:
            symbol: Trading symbol to analyze
            return_diagnostics: If True, returns tuple of (signal, diagnostics_dict) instead of just signal
            
        Returns:
            If return_diagnostics=False: SMCSignal or None
            If return_diagnostics=True: Tuple of (SMCSignal or None, diagnostics dict)
        """
        try:
            # Initialize diagnostics tracking
            rejection_reasons = []
            analysis_details = {}
            
            # First, get current price to check existing signal validity
            quick_data = self.get_candlestick_data(symbol, "1h", 1)
            if not quick_data:
                rejection_reasons.append("No price data available")
                if return_diagnostics:
                    return None, {"rejection_reasons": rejection_reasons, "details": analysis_details}
                return None
            current_price = quick_data[-1]["close"]
            
            # Check if we have a valid cached signal for this symbol
            if self._is_signal_still_valid(symbol, current_price):
                cached_signal = self.active_signals[symbol]['signal']
                logging.debug(f"Using cached {cached_signal.direction} signal for {symbol} (entry: {cached_signal.entry_price})")
                if return_diagnostics:
                    return cached_signal, {"rejection_reasons": [], "details": {"cached": True}, "signal_generated": True}
                return cached_signal
            
            # Get multi-timeframe data in batch to reduce API calls
            timeframe_data = self.get_multi_timeframe_data(symbol)

            h1_data = timeframe_data.get("1h", [])
            h4_data = timeframe_data.get("4h", [])
            d1_data = timeframe_data.get("1d", [])
            m15_data = timeframe_data.get("15m", [])

            if not h1_data or not h4_data:
                rejection_reasons.append(f"Insufficient timeframe data (H1: {len(h1_data)} candles, H4: {len(h4_data)} candles)")
                logging.warning(
                    f"Insufficient data for {symbol}: h1={len(h1_data)}, h4={len(h4_data)}"
                )
                if return_diagnostics:
                    return None, {"rejection_reasons": rejection_reasons, "details": analysis_details}
                return None

            current_price = h1_data[-1]["close"]

            # --- Auto volatility detection BEFORE ATR filter for consistent parameters ---
            from config import TradingConfig
            symbol_upper = symbol.upper()
            profile = getattr(TradingConfig, "ASSET_PROFILES", {}).get(symbol_upper, {})

            # Compute recent ATR (14-period) from 1H candles
            atr_values = [abs(c["high"] - c["low"]) for c in h1_data[-14:]]
            current_atr = np.mean(atr_values) if atr_values else 0

            base_atr = profile.get("BASE_ATR", current_atr or 1)
            vol_ratio = current_atr / base_atr if base_atr > 0 else 1.0

            # Classify volatility regime
            if vol_ratio < 0.8:
                vol_regime = "low"
            elif vol_ratio < 1.3:
                vol_regime = "normal"
            else:
                vol_regime = "high"

            logging.info(f"{symbol_upper} volatility check → ATR={current_atr:.4f}, baseline={base_atr:.4f}, ratio={vol_ratio:.2f}, regime={vol_regime}")

            # Dynamically scale institutional parameters based on volatility regime
            # Scale config baseline values instead of using hardcoded multipliers
            base_fvg = SMCConfig.FVG_ATR_MULTIPLIER
            base_ob = SMCConfig.OB_VOLUME_MULTIPLIER
            base_depth1 = TradingConfig.SCALED_ENTRY_DEPTH_1
            base_depth2 = TradingConfig.SCALED_ENTRY_DEPTH_2
            
            if vol_regime == "low":
                atr_mult = 0.9
                fvg_mult = base_fvg * 0.85  # 85% of config value
                ob_mult = base_ob * 0.82  # 82% of config value
                depths = [base_depth1 * 0.75, base_depth2 * 0.70]  # Tighter entries
            elif vol_regime == "normal":
                atr_mult = 1.0
                fvg_mult = base_fvg  # Use config value as-is
                ob_mult = base_ob * 0.92  # 92% of config value
                depths = [base_depth1, base_depth2]  # Use config as-is
            else:  # high volatility
                atr_mult = 1.3
                fvg_mult = base_fvg * 1.29  # 129% of config value
                ob_mult = base_ob * 1.08  # 108% of config value
                depths = [base_depth1 * 1.50, base_depth2 * 1.40]  # Wider entries

            # Apply dynamic tuning
            self.atr_multiplier = atr_mult
            self.fvg_multiplier = fvg_mult
            self.ob_volume_multiplier = ob_mult
            self.scaled_entry_depths = depths

            logging.info(f"Adaptive tuning → ATR x{atr_mult}, FVG x{fvg_mult}, OB x{ob_mult}, Depths={depths}")

            # Phase 7: ATR Risk Filter - Check volatility with tuned parameters
            use_atr_filter = getattr(TradingConfig, 'USE_ATR_FILTER', True)
            
            if use_atr_filter and m15_data and len(m15_data) >= 15:
                atr_filter_result = self._check_atr_filter(m15_data, h1_data, current_price, symbol=symbol)
                analysis_details["phase7_atr_filter"] = atr_filter_result
                
                if not atr_filter_result["passes"]:
                    rejection_reasons.append(atr_filter_result["reason"])
                    logging.info(f"Phase 7: Trade rejected for {symbol} - {atr_filter_result['reason']}")
                    if return_diagnostics:
                        return None, {
                            "rejection_reasons": rejection_reasons,
                            "details": analysis_details,
                            "signal_generated": False
                        }
                    return None
                
                # Optional: Calculate dynamic position size based on ATR
                position_size_multiplier = self._calculate_dynamic_position_size(
                    base_size=1.0,
                    atr_percent=atr_filter_result["atr_15m_percent"]
                )
                analysis_details["phase7_position_size_multiplier"] = position_size_multiplier
                
                if position_size_multiplier != 1.0:
                    logging.info(
                        f"Phase 7: Dynamic position sizing for {symbol} - "
                        f"Multiplier: {position_size_multiplier:.2f}x (ATR: {atr_filter_result['atr_15m_percent']:.2f}%)"
                    )
            else:
                if not use_atr_filter:
                    logging.debug("Phase 7: ATR filter disabled in configuration")
                elif not m15_data or len(m15_data) < 15:
                    logging.warning(f"Phase 7: Insufficient 15m data for ATR filter ({len(m15_data) if m15_data else 0} candles)")

            # Phase 2: Multi-Timeframe Hierarchical Analysis
            # Step 1: Determine High Timeframe Bias (Daily + H4)
            htf_bias = self._get_htf_bias(d1_data, h4_data)
            analysis_details["htf_bias"] = htf_bias["bias"]
            analysis_details["htf_confidence"] = htf_bias["confidence"]
            analysis_details["htf_reason"] = htf_bias["reason"]
            
            logging.info(f"Phase 2 - HTF Bias for {symbol}: {htf_bias['bias']} (confidence: {htf_bias['confidence']:.2f}) - {htf_bias['reason']}")
            
            # Step 2: Analyze Intermediate Structure (H4 + H1)
            intermediate_structure = self._get_intermediate_structure(h1_data, h4_data)
            analysis_details["intermediate_structure"] = intermediate_structure["structure"]
            analysis_details["intermediate_valid"] = intermediate_structure["valid"]
            analysis_details["poi_count"] = len(intermediate_structure.get("poi_levels", []))
            
            # Step 3: Generate 15m Execution Signal (if 15m data available)
            execution_signal_15m = None
            if m15_data and len(m15_data) >= 20:
                execution_signal_15m = self._get_execution_signal_15m(m15_data, htf_bias, intermediate_structure)
                analysis_details["m15_signal"] = execution_signal_15m["signal"]
                analysis_details["m15_alignment_score"] = execution_signal_15m["alignment_score"]
                analysis_details["m15_reason"] = execution_signal_15m["reason"]
                
                logging.info(f"Phase 2 - 15m Execution Signal for {symbol}: {execution_signal_15m['signal']} (alignment: {execution_signal_15m['alignment_score']:.2f}) - {execution_signal_15m['reason']}")
                
                # Reject if 15m conflicts with HTF bias (low alignment score)
                if execution_signal_15m["alignment_score"] < 0.3 and htf_bias["bias"] != "neutral":
                    rejection_reasons.append(f"15m structure conflicts with HTF bias: {execution_signal_15m['reason']}")
                    if return_diagnostics:
                        return None, {"rejection_reasons": rejection_reasons, "details": analysis_details, "signal_generated": False}
                    return None
            else:
                logging.warning(f"15m data unavailable or insufficient for {symbol} ({len(m15_data) if m15_data else 0} candles), proceeding with standard analysis")

            # Analyze market structure across timeframes
            h1_structure = self.detect_market_structure(h1_data)
            h4_structure = self.detect_market_structure(h4_data)
            analysis_details["h1_structure"] = h1_structure.value if hasattr(h1_structure, 'value') else str(h1_structure)
            analysis_details["h4_structure"] = h4_structure.value if hasattr(h4_structure, 'value') else str(h4_structure)

            # Find key SMC elements
            order_blocks = self.find_order_blocks(h1_data)
            fvgs = self.find_fair_value_gaps(h1_data)
            liquidity_pools = self.find_liquidity_pools(h4_data)
            analysis_details["order_blocks_count"] = len(order_blocks)
            analysis_details["fvgs_count"] = len(fvgs)
            analysis_details["liquidity_pools_count"] = len(liquidity_pools)

            # Calculate technical indicators
            rsi = self.calculate_rsi(h1_data)
            mas = self.calculate_moving_averages(h1_data)
            analysis_details["rsi"] = rsi
            analysis_details["rsi_status"] = "oversold" if rsi and rsi < 30 else "overbought" if rsi and rsi > 70 else "neutral"

            # Detect liquidity sweeps for reversal trade validation
            liquidity_sweeps = self.detect_liquidity_sweeps(h1_data)
            confirmed_buy_sweeps = [s for s in liquidity_sweeps.get("buy_side", []) if s.get("confirmed", False)]
            confirmed_sell_sweeps = [s for s in liquidity_sweeps.get("sell_side", []) if s.get("confirmed", False)]
            analysis_details["liquidity_sweeps"] = {
                "buy_side_total": len(liquidity_sweeps.get("buy_side", [])),
                "buy_side_confirmed": len(confirmed_buy_sweeps),
                "sell_side_total": len(liquidity_sweeps.get("sell_side", [])),
                "sell_side_confirmed": len(confirmed_sell_sweeps)
            }
            
            # Generate signal analysis with separate reasoning lists
            bullish_reasoning = []
            bearish_reasoning = []

            # Analyze bullish and bearish signals
            bullish_signals = self._analyze_bullish_signals(
                h1_structure,
                h4_structure,
                order_blocks,
                fvgs,
                current_price,
                rsi,
                mas,
                bullish_reasoning,
            )

            bearish_signals = self._analyze_bearish_signals(
                h1_structure,
                h4_structure,
                order_blocks,
                fvgs,
                current_price,
                rsi,
                mas,
                bearish_reasoning,
            )
            
            analysis_details["bullish_signals_count"] = bullish_signals
            analysis_details["bearish_signals_count"] = bearish_signals

            # Apply hybrid signal generation logic
            direction, confidence, entry_price, stop_loss, take_profits = (
                self._determine_trade_direction_and_levels_hybrid(
                    h1_structure, h4_structure, bullish_signals, bearish_signals, 
                    current_price, order_blocks, h1_data, liquidity_sweeps, rsi
                )
            )
            
            # Track why signal was rejected
            if not direction:
                # Determine specific rejection reasons
                if h1_structure == MarketStructure.CONSOLIDATION or h4_structure == MarketStructure.CONSOLIDATION:
                    rejection_reasons.append("Market in consolidation phase (no clear trend)")
                    
                bullish_structures = [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]
                bearish_structures = [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]
                
                if (h1_structure in bullish_structures and h4_structure in bearish_structures) or \
                   (h1_structure in bearish_structures and h4_structure in bullish_structures):
                    rejection_reasons.append("H1/H4 timeframe structure conflict")
                    
                    # Note: RSI and sweep validation already done in hybrid logic
                    # No need to duplicate the checks here - they're already part of signal generation
                
                if bullish_signals < 3 and bearish_signals < 3:
                    rejection_reasons.append(f"Insufficient signal confluence (Bullish: {bullish_signals}, Bearish: {bearish_signals}, minimum: 3)")
                    
                if len(order_blocks) == 0:
                    rejection_reasons.append("No valid order blocks detected")
                    
            elif confidence <= 0:
                rejection_reasons.append(f"Confidence score too low ({confidence:.2f})")
            
            analysis_details["final_direction"] = direction
            analysis_details["final_confidence"] = confidence

            # Check if signal meets minimum requirements
            if direction and confidence > 0:
                # Phase 3: Apply Enhanced Confidence Scoring with 15m alignment
                # ISSUE #14 FIX: Increased default from 0.3 to 0.4 to allow borderline signals with strong HTF alignment
                m15_alignment_score = 0.4  # Default borderline if no 15m data (missing info shouldn't be neutral)
                if execution_signal_15m and execution_signal_15m.get("alignment_score") is not None:
                    m15_alignment_score = execution_signal_15m["alignment_score"]
                    analysis_details["phase3_m15_alignment"] = m15_alignment_score
                else:
                    analysis_details["phase3_m15_alignment_missing"] = True  # Track when 15m data is missing
                
                # Check for liquidity sweep confluence
                liquidity_swept = len(confirmed_buy_sweeps) > 0 or len(confirmed_sell_sweeps) > 0
                sweep_confirmed = (len(confirmed_buy_sweeps) > 0 and direction == "long") or (len(confirmed_sell_sweeps) > 0 and direction == "short")
                
                # Check if entry is from HTF POI (OB or FVG near entry price)
                entry_from_htf_poi = False
                for ob in order_blocks:
                    price_diff_pct = abs(entry_price - ob.price_low if direction == "long" else ob.price_high) / entry_price * 100
                    if price_diff_pct < 0.5 and not ob.mitigated:  # Within 0.5% of OB
                        entry_from_htf_poi = True
                        break
                if not entry_from_htf_poi:
                    for fvg in fvgs:
                        price_diff_pct = abs(entry_price - (fvg.gap_low if direction == "long" else fvg.gap_high)) / entry_price * 100
                        if price_diff_pct < 0.5:  # Within 0.5% of FVG
                            entry_from_htf_poi = True
                            break
                
                analysis_details["phase3_liquidity_swept"] = liquidity_swept
                analysis_details["phase3_sweep_confirmed"] = sweep_confirmed
                analysis_details["phase3_entry_from_poi"] = entry_from_htf_poi
                
                # Calculate enhanced confidence using Phase 3 method
                # Convert confidence (which is 0-1) to confluence_score (which is 0-5 scale)
                confluence_score_estimate = confidence * 5.0
                
                signal_strength, enhanced_confidence = self._calculate_signal_strength_and_confidence(
                    confluence_score=confluence_score_estimate,
                    m15_alignment_score=m15_alignment_score,
                    liquidity_swept=liquidity_swept,
                    sweep_confirmed=sweep_confirmed,
                    entry_from_htf_poi=entry_from_htf_poi
                )
                
                analysis_details["phase3_base_confidence"] = confidence
                analysis_details["phase3_enhanced_confidence"] = enhanced_confidence
                analysis_details["phase3_signal_strength"] = signal_strength.value if hasattr(signal_strength, 'value') else str(signal_strength)
                
                # Calculate risk/reward ratio
                rr_ratio, _ = self._calculate_trade_metrics_enhanced(
                    entry_price, stop_loss, take_profits, enhanced_confidence, liquidity_sweeps, order_blocks, fvgs
                )

                # Use reasoning that matches the final signal direction with defensive check
                if direction == "long":
                    final_reasoning = bullish_reasoning
                elif direction == "short":
                    final_reasoning = bearish_reasoning
                else:
                    # Fallback: use the reasoning from the stronger signal side
                    final_reasoning = bullish_reasoning if bullish_signals > bearish_signals else bearish_reasoning
                
                # Add Phase 3 bonuses to reasoning
                if m15_alignment_score >= 0.8:
                    final_reasoning.append(f"Phase 3: Perfect 15m alignment with HTF bias (+0.2 confidence)")
                if liquidity_swept and sweep_confirmed:
                    final_reasoning.append(f"Phase 3: Confirmed liquidity sweep ({direction}-side) (+0.1 confidence)")
                if entry_from_htf_poi:
                    final_reasoning.append("Phase 3: Entry from HTF POI (OB/FVG) (+0.1 confidence)")
                
                # Phase 5: Extract 15m swing levels using new Phase 5 method
                m15_swing_levels = {}
                if m15_data and len(m15_data) >= 5:
                    m15_swing_levels = self._find_15m_swing_levels(m15_data)
                    analysis_details["phase5_swing_high"] = m15_swing_levels.get("last_swing_high")
                    analysis_details["phase5_swing_low"] = m15_swing_levels.get("last_swing_low")
                
                # Phase 5: Calculate refined stop-loss with ATR buffer if enabled
                from config import TradingConfig
                if TradingConfig.USE_15M_SWING_SL and (m15_swing_levels.get("last_swing_low") is not None or m15_swing_levels.get("last_swing_high") is not None):
                    # Calculate ATR on 15m timeframe for buffer
                    atr_15m = self.calculate_atr(m15_data) if m15_data else 0.0
                    
                    # Use dynamic ATR buffer multiplier (adjusted for volatility)
                    atr_buffer_multiplier = self.atr_multiplier * 0.5  # Apply volatility scaling to buffer
                    
                    # Calculate refined stop-loss using Phase 5 method
                    refined_sl = self._calculate_refined_sl_with_atr(
                        direction=direction,
                        swing_levels=m15_swing_levels,
                        atr_value=atr_15m,
                        current_price=current_price,
                        atr_buffer_multiplier=atr_buffer_multiplier
                    )
                    
                    # Update stop-loss with refined value
                    original_sl = stop_loss
                    stop_loss = refined_sl
                    
                    analysis_details["phase5_original_sl"] = original_sl
                    analysis_details["phase5_refined_sl"] = refined_sl
                    analysis_details["phase5_atr_15m"] = atr_15m
                    
                    # Add Phase 5 reasoning
                    sl_improvement = abs(refined_sl - original_sl) / current_price * 100
                    final_reasoning.append(f"Phase 5: Refined SL using 15m swings (improved by {sl_improvement:.2f}%)")
                    
                    logging.info(f"Phase 5: Refined stop-loss from ${original_sl:.2f} to ${refined_sl:.2f} using 15m swings + ATR buffer")
                
                # Phase 6: Calculate R:R-based take profits if enabled
                if TradingConfig.USE_RR_BASED_TPS:
                    # Extract liquidity target prices from liquidity pools
                    liquidity_target_prices = []
                    if liquidity_pools:
                        for pool in liquidity_pools:
                            liquidity_target_prices.append(pool.price)
                    
                    # Calculate R:R-based take profits
                    rr_take_profits = self._calculate_rr_based_take_profits(
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        direction=direction,
                        liquidity_targets=liquidity_target_prices
                    )
                    
                    # Extract TP prices and allocations
                    original_tps = take_profits.copy() if take_profits else []
                    take_profits = [tp_price for tp_price, _ in rr_take_profits]
                    tp_allocations = [tp_alloc for _, tp_alloc in rr_take_profits]
                    
                    # Track Phase 6 metrics
                    analysis_details["phase6_tp_levels"] = take_profits
                    analysis_details["phase6_tp_allocations"] = tp_allocations
                    analysis_details["phase6_original_tps"] = original_tps
                    
                    # Calculate R:R ratio using new TP1
                    risk = abs(entry_price - stop_loss)
                    reward = abs(take_profits[0] - entry_price) if take_profits else risk
                    rr_ratio = reward / risk if risk > 0 else 1.0
                    
                    # Add Phase 6 reasoning
                    if len(take_profits) >= 3:
                        tp_rr_ratios = TradingConfig.TP_RR_RATIOS
                        final_reasoning.append(
                            f"Phase 6: R:R-based TPs - TP1: {tp_rr_ratios[0]}R ({tp_allocations[0]:.0f}%), "
                            f"TP2: {tp_rr_ratios[1]}R ({tp_allocations[1]:.0f}%), "
                            f"TP3: {tp_rr_ratios[2]}R ({tp_allocations[2]:.0f}%)"
                        )
                    
                    logging.info(
                        f"Phase 6: R:R-based take profits calculated - "
                        f"TP1: ${take_profits[0]:.2f}, TP2: ${take_profits[1]:.2f}, TP3: ${take_profits[2]:.2f}"
                    )
                
                # Phase 4: Calculate scaled entries
                scaled_entries_list = None
                if TradingConfig.USE_SCALED_ENTRIES:
                    # Calculate base take profits for scaled entries
                    base_take_profits = [(tp, 100.0/len(take_profits)) for tp in take_profits] if take_profits else []
                    
                    # Calculate scaled entries
                    scaled_entries_list = self._calculate_scaled_entries(
                        current_price=current_price,
                        direction=direction,
                        order_blocks=order_blocks,
                        fvgs=fvgs,
                        base_stop_loss=stop_loss,
                        base_take_profits=base_take_profits
                    )
                    
                    analysis_details["phase4_scaled_entries_count"] = len(scaled_entries_list)
                    analysis_details["phase4_entry_allocations"] = [entry.allocation_percent for entry in scaled_entries_list]
                    
                    # Add Phase 4 reasoning
                    if len(scaled_entries_list) > 1:
                        allocs = [f"{e.allocation_percent:.0f}%" for e in scaled_entries_list]
                        final_reasoning.append(f"Phase 4: Scaled entry strategy - {' + '.join(allocs)} allocation across {len(scaled_entries_list)} levels")
                    
                    logging.info(f"Phase 4: Generated {len(scaled_entries_list)} scaled entries for {symbol}")

                new_signal = SMCSignal(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit_levels=take_profits,
                    confidence=enhanced_confidence,  # Use Phase 3 enhanced confidence
                    reasoning=final_reasoning,
                    signal_strength=signal_strength,
                    risk_reward_ratio=rr_ratio,
                    timestamp=datetime.utcnow(),
                    current_market_price=current_price,  # Store actual market price
                    scaled_entries=scaled_entries_list  # Phase 4: Add scaled entries
                )
                
                # Cache the new signal to prevent duplicates
                self._cache_signal(new_signal)
                
                if return_diagnostics:
                    return new_signal, {"rejection_reasons": [], "details": analysis_details, "signal_generated": True}
                return new_signal

            if return_diagnostics:
                return None, {"rejection_reasons": rejection_reasons, "details": analysis_details, "signal_generated": False}
            return None

        except Exception as e:
            logging.error(f"Error generating SMC signal for {symbol}: {e}")
            if return_diagnostics:
                return None, {"rejection_reasons": [f"Analysis error: {str(e)}"], "details": {}, "signal_generated": False}
            return None

    def _find_swing_highs(
        self,
        candlesticks: List[Dict],
        lookback: int = None,
        timeframe: str = "1h"
    ) -> List[Dict]:
        """Find swing highs in price data with timeframe-aware lookback"""
        # Determine lookback based on timeframe if not explicitly provided
        if lookback is None:
            lookback_map = {
                "15m": SMCConfig.SWING_LOOKBACK_15M,
                "1h": SMCConfig.SWING_LOOKBACK_1H,
                "4h": SMCConfig.SWING_LOOKBACK_4H,
                "1d": SMCConfig.SWING_LOOKBACK_1D
            }
            lookback = lookback_map.get(timeframe, SMCConfig.DEFAULT_LOOKBACK_PERIOD)
        
        swing_highs = []

        for i in range(lookback, len(candlesticks) - lookback):
            current_high = candlesticks[i]["high"]
            is_swing_high = True

            # Check if current high is higher than surrounding candles
            for j in range(i - lookback, i + lookback + 1):
                if j != i and candlesticks[j]["high"] >= current_high:
                    is_swing_high = False
                    break

            if is_swing_high:
                swing_highs.append(
                    {
                        "high": current_high,
                        "timestamp": candlesticks[i]["timestamp"],
                        "index": i,
                        "strength": self._calculate_swing_strength(
                            candlesticks, i, "high"
                        ),
                    }
                )

        return swing_highs

    def _find_swing_lows(
        self,
        candlesticks: List[Dict],
        lookback: int = None,
        timeframe: str = "1h"
    ) -> List[Dict]:
        """Find swing lows in price data with timeframe-aware lookback"""
        # Determine lookback based on timeframe if not explicitly provided
        if lookback is None:
            lookback_map = {
                "15m": SMCConfig.SWING_LOOKBACK_15M,
                "1h": SMCConfig.SWING_LOOKBACK_1H,
                "4h": SMCConfig.SWING_LOOKBACK_4H,
                "1d": SMCConfig.SWING_LOOKBACK_1D
            }
            lookback = lookback_map.get(timeframe, SMCConfig.DEFAULT_LOOKBACK_PERIOD)
        
        swing_lows = []

        for i in range(lookback, len(candlesticks) - lookback):
            current_low = candlesticks[i]["low"]
            is_swing_low = True

            # Check if current low is lower than surrounding candles
            for j in range(i - lookback, i + lookback + 1):
                if j != i and candlesticks[j]["low"] <= current_low:
                    is_swing_low = False
                    break

            if is_swing_low:
                swing_lows.append(
                    {
                        "low": current_low,
                        "timestamp": candlesticks[i]["timestamp"],
                        "index": i,
                        "strength": self._calculate_swing_strength(
                            candlesticks, i, "low"
                        ),
                    }
                )

        return swing_lows

    def _calculate_swing_strength(
        self, candlesticks: List[Dict], index: int, swing_type: str
    ) -> float:
        """Calculate the strength of a swing point based on volume and price action"""
        if index < 1 or index >= len(candlesticks) - 1:
            return 1.0

        current = candlesticks[index]
        volume_strength = current["volume"] / max(
            [
                c["volume"]
                for c in candlesticks[
                    max(0, index - SMCConfig.VOLUME_RANGE_LOOKBACK) : index
                    + SMCConfig.VOLUME_RANGE_LOOKBACK
                ]
            ],
            default=1,
        )

        # Price range strength
        price_range = current["high"] - current["low"]
        avg_range = sum(
            [
                c["high"] - c["low"]
                for c in candlesticks[
                    max(0, index - SMCConfig.VOLUME_RANGE_LOOKBACK) : index
                    + SMCConfig.VOLUME_RANGE_LOOKBACK
                ]
            ]
        ) / min(SMCConfig.AVG_RANGE_PERIOD, len(candlesticks))
        range_strength = price_range / avg_range if avg_range > 0 else 1.0

        return min(volume_strength * range_strength, 3.0)

    def _calculate_trend(self, swing_points: List[Dict], price_key: str) -> str:
        """Calculate trend direction from swing points"""
        if len(swing_points) < 2:
            return "neutral"

        recent_prices = [point[price_key] for point in swing_points[-3:]]

        if len(recent_prices) >= SMCConfig.MIN_PRICES_FOR_TREND:
            if all(
                recent_prices[i] > recent_prices[i - 1]
                for i in range(1, len(recent_prices))
            ):
                return "up"
            elif all(
                recent_prices[i] < recent_prices[i - 1]
                for i in range(1, len(recent_prices))
            ):
                return "down"

        return "neutral"

    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average"""
        if len(prices) < period:
            return sum(prices) / len(prices)

        multiplier = 2.0 / (period + 1)
        ema = sum(prices[:period]) / period

        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))

        return ema

    def _check_impulsive_move(
        self, candlesticks: List[Dict], ob_index: int, direction: str
    ) -> bool:
        """Check if price exits order block with impulsive displacement"""
        if ob_index + SMCConfig.OB_DISPLACEMENT_CANDLES >= len(candlesticks):
            return False

        ob_candle = candlesticks[ob_index]
        displacement_candles = candlesticks[
            ob_index + 1 : ob_index + 1 + SMCConfig.OB_DISPLACEMENT_CANDLES
        ]
        
        # ISSUE #18 FIX: Prevent division by zero when OB candle has no range
        candle_range = ob_candle["high"] - ob_candle["low"]
        if candle_range == 0:
            logging.warning(f"Order block candle at index {ob_index} has zero range (high=low), skipping displacement check")
            return False

        if direction == "bullish":
            # Check for strong upward displacement
            max_high = max(c["high"] for c in displacement_candles)
            displacement_ratio = (max_high - ob_candle["high"]) / candle_range
            return displacement_ratio >= SMCConfig.OB_IMPULSIVE_MOVE_THRESHOLD
        else:
            # Check for strong downward displacement
            min_low = min(c["low"] for c in displacement_candles)
            displacement_ratio = (ob_candle["low"] - min_low) / candle_range
            return displacement_ratio >= SMCConfig.OB_IMPULSIVE_MOVE_THRESHOLD

    def detect_liquidity_sweeps(
        self, candlesticks: List[Dict]
    ) -> Dict[str, List[Dict]]:
        """Detect liquidity sweeps - wicks that take out swing highs/lows"""
        sweeps = {"buy_side": [], "sell_side": []}

        if len(candlesticks) < 20:
            return sweeps

        swing_highs = self._find_swing_highs(candlesticks)
        swing_lows = self._find_swing_lows(candlesticks)

        # Look for buy-side liquidity sweeps (wicks below swing lows)
        for i, candle in enumerate(candlesticks[-30:], start=len(candlesticks) - 30):
            body_size = abs(candle["close"] - candle["open"])
            lower_wick = min(candle["open"], candle["close"]) - candle["low"]

            # Check if wick is significant
            if lower_wick >= body_size * SMCConfig.LIQUIDITY_SWEEP_WICK_RATIO:
                # Check if wick swept below recent swing low
                for swing in swing_lows[-10:]:
                    if (
                        swing["index"] < i
                        and candle["low"] < swing["low"]
                        and candle["close"] > swing["low"]
                    ):

                        # Check for structural confirmation
                        confirmation = self._check_sweep_confirmation(
                            candlesticks, i, "buy_side"
                        )
                        
                        # If REQUIRE_CONFIRMED_SWEEPS is False, mark all sweeps as confirmed
                        if not SMCConfig.REQUIRE_CONFIRMED_SWEEPS:
                            confirmation = True

                        sweep = {
                            "price": swing["low"],
                            "sweep_candle_index": i,
                            "sweep_low": candle["low"],
                            "confirmed": confirmation,
                            "timestamp": candle["timestamp"],
                        }
                        sweeps["buy_side"].append(sweep)
                        break

        # Look for sell-side liquidity sweeps (wicks above swing highs)
        for i, candle in enumerate(candlesticks[-30:], start=len(candlesticks) - 30):
            body_size = abs(candle["close"] - candle["open"])
            upper_wick = candle["high"] - max(candle["open"], candle["close"])

            # Check if wick is significant
            if upper_wick >= body_size * SMCConfig.LIQUIDITY_SWEEP_WICK_RATIO:
                # Check if wick swept above recent swing high
                for swing in swing_highs[-10:]:
                    if (
                        swing["index"] < i
                        and candle["high"] > swing["high"]
                        and candle["close"] < swing["high"]
                    ):

                        # Check for structural confirmation
                        confirmation = self._check_sweep_confirmation(
                            candlesticks, i, "sell_side"
                        )
                        
                        # If REQUIRE_CONFIRMED_SWEEPS is False, mark all sweeps as confirmed
                        if not SMCConfig.REQUIRE_CONFIRMED_SWEEPS:
                            confirmation = True

                        sweep = {
                            "price": swing["high"],
                            "sweep_candle_index": i,
                            "sweep_high": candle["high"],
                            "confirmed": confirmation,
                            "timestamp": candle["timestamp"],
                        }
                        sweeps["sell_side"].append(sweep)
                        break

        return sweeps

    def _check_sweep_confirmation(
        self, candlesticks: List[Dict], sweep_index: int, sweep_type: str
    ) -> bool:
        """Check for structural confirmation after liquidity sweep"""
        if sweep_index + SMCConfig.LIQUIDITY_CONFIRMATION_CANDLES >= len(candlesticks):
            return False

        confirmation_candles = candlesticks[
            sweep_index + 1 : sweep_index + 1 + SMCConfig.LIQUIDITY_CONFIRMATION_CANDLES
        ]
        sweep_candle = candlesticks[sweep_index]

        if sweep_type == "buy_side":
            # Look for bullish confirmation after buy-side sweep
            bullish_count = sum(
                1 for c in confirmation_candles if c["close"] > c["open"]
            )
            price_recovery = any(
                c["close"] > sweep_candle["close"] for c in confirmation_candles
            )
            return bullish_count >= 1 and price_recovery
        else:
            # Look for bearish confirmation after sell-side sweep
            bearish_count = sum(
                1 for c in confirmation_candles if c["close"] < c["open"]
            )
            price_decline = any(
                c["close"] < sweep_candle["close"] for c in confirmation_candles
            )
            return bearish_count >= 1 and price_decline

    def _categorize_structures(
        self,
        h1_structure: MarketStructure,
        h4_structure: MarketStructure,
        d1_structure: Optional[MarketStructure] = None,
    ) -> Dict[str, bool]:
        """Categorize structure types for each timeframe."""
        bullish_structures = [
            MarketStructure.BULLISH_BOS,
            MarketStructure.BULLISH_CHoCH,
        ]
        bearish_structures = [
            MarketStructure.BEARISH_BOS,
            MarketStructure.BEARISH_CHoCH,
        ]

        result = {
            "h1_bullish": h1_structure in bullish_structures,
            "h1_bearish": h1_structure in bearish_structures,
            "h4_bullish": h4_structure in bullish_structures,
            "h4_bearish": h4_structure in bearish_structures,
        }

        if d1_structure:
            result.update(
                {
                    "d1_bullish": d1_structure in bullish_structures,
                    "d1_bearish": d1_structure in bearish_structures,
                }
            )

        return result

    def _analyze_h1_h4_alignment(
        self,
        h1_structure: MarketStructure,
        h4_structure: MarketStructure,
        categories: Dict[str, bool],
    ) -> Dict[str, Any]:
        """Analyze alignment between H1 and H4 timeframes."""
        alignment_score = 0.0
        alignment_details = []

        # Check H1/H4 alignment
        if (categories["h1_bullish"] and categories["h4_bullish"]) or (
            categories["h1_bearish"] and categories["h4_bearish"]
        ):
            alignment_score += 2.0
            alignment_details.append(
                f"H1 and H4 structures aligned ({h1_structure.value}, {h4_structure.value})"
            )
        elif (
            h1_structure == MarketStructure.CONSOLIDATION
            or h4_structure == MarketStructure.CONSOLIDATION
        ):
            alignment_score += 0.5
            alignment_details.append("Partial alignment - one timeframe consolidating")
        else:
            alignment_details.append("H1/H4 structure conflict - signal filtered")
            return {"conflict": True, "score": 0.0, "details": alignment_details}

        return {
            "conflict": False,
            "score": alignment_score,
            "details": alignment_details,
        }

    def _analyze_daily_bias_confirmation(
        self,
        d1_structure: MarketStructure,
        categories: Dict[str, bool],
        current_score: float,
    ) -> Dict[str, Any]:
        """Analyze daily bias confirmation and its impact."""
        alignment_score = current_score
        alignment_details = []

        if not d1_structure:
            return {
                "score": alignment_score,
                "details": alignment_details,
                "filtered": False,
            }

        # All timeframes aligned
        if (
            categories["h1_bullish"]
            and categories["h4_bullish"]
            and categories["d1_bullish"]
        ) or (
            categories["h1_bearish"]
            and categories["h4_bearish"]
            and categories["d1_bearish"]
        ):
            alignment_score += SMCConfig.DAILY_BIAS_WEIGHT
            alignment_details.append(
                f"Daily bias confirms direction ({d1_structure.value})"
            )

        # Daily consolidation
        elif d1_structure == MarketStructure.CONSOLIDATION:
            alignment_score += 0.5
            alignment_details.append("Daily consolidation - neutral bias")

        # Counter-trend to daily
        elif (
            categories["h1_bullish"]
            and categories["h4_bullish"]
            and categories["d1_bearish"]
        ) or (
            categories["h1_bearish"]
            and categories["h4_bearish"]
            and categories["d1_bullish"]
        ):
            # Strong reversal required against daily bias
            if alignment_score >= 2.0:  # Strong H1/H4 alignment
                alignment_score -= 0.5
                alignment_details.append(
                    "Counter-trend to daily - requires strong reversal"
                )
            else:
                alignment_details.append("Weak counter-trend signal - filtered")
                return {
                    "score": alignment_score,
                    "details": alignment_details,
                    "filtered": True,
                }

        return {
            "score": alignment_score,
            "details": alignment_details,
            "filtered": False,
        }

    def _determine_alignment_direction(self, categories: Dict[str, bool]) -> Optional[str]:
        """Determine overall direction from structure alignment."""
        if categories["h1_bullish"] and categories["h4_bullish"]:
            return "long"
        elif categories["h1_bearish"] and categories["h4_bearish"]:
            return "short"
        return None

    def _create_alignment_result(
        self, aligned: bool, score: float, details: list, direction: Optional[str]
    ) -> Dict[str, Any]:
        """Create standardized alignment result."""
        return {
            "aligned": aligned,
            "score": score,
            "details": details,
            "direction": direction,
        }

    def check_multi_timeframe_alignment(
        self,
        h1_structure: MarketStructure,
        h4_structure: MarketStructure,
        d1_structure: Optional[MarketStructure] = None,
    ) -> Dict[str, Any]:
        """Check alignment between multiple timeframes"""
        # Categorize structure types
        categories = self._categorize_structures(
            h1_structure, h4_structure, d1_structure
        )

        # Analyze H1/H4 alignment
        h1_h4_result = self._analyze_h1_h4_alignment(
            h1_structure, h4_structure, categories
        )
        if h1_h4_result["conflict"]:
            direction = self._determine_alignment_direction(categories)
            return self._create_alignment_result(
                False, h1_h4_result["score"], h1_h4_result["details"], direction
            )

        # Analyze daily bias confirmation
        daily_result = self._analyze_daily_bias_confirmation(
            d1_structure if d1_structure is not None else MarketStructure.CONSOLIDATION, 
            categories, h1_h4_result["score"]
        )
        if daily_result["filtered"]:
            direction = self._determine_alignment_direction(categories)
            return self._create_alignment_result(
                False,
                daily_result["score"],
                h1_h4_result["details"] + daily_result["details"],
                direction,
            )

        # Determine overall direction
        direction = self._determine_alignment_direction(categories)

        # Combine all details
        all_details = h1_h4_result["details"] + daily_result["details"]

        return self._create_alignment_result(
            daily_result["score"] >= SMCConfig.CONFLUENCE_MIN_SCORE,
            daily_result["score"],
            all_details,
            direction,
        )

    def _analyze_enhanced_confluence(
        self,
        h1_candlesticks: List[Dict],
        order_blocks,
        fvgs,
        liquidity_sweeps,
        alignment,
        h1_structure,
    ):
        """Analyze confluence factors for enhanced signal generation."""
        confluence_score = 0.0
        reasoning = []
        current_price = h1_candlesticks[-1]["close"]

        # Multi-timeframe structure weight
        if alignment and alignment["aligned"]:
            confluence_score += alignment["score"]
            reasoning.extend(alignment["details"])
        else:
            confluence_score, reasoning = self._analyze_single_timeframe_structure(
                h1_structure, confluence_score, reasoning
            )

        # Enhanced order block validation
        relevant_obs = self._validate_relevant_order_blocks(
            order_blocks, current_price, confluence_score, reasoning
        )
        confluence_score = relevant_obs["confluence_score"]
        reasoning = relevant_obs["reasoning"]

        # Enhanced FVG analysis
        relevant_fvgs = self._analyze_relevant_fvgs(
            fvgs, current_price, confluence_score, reasoning
        )
        confluence_score = relevant_fvgs["confluence_score"]
        reasoning = relevant_fvgs["reasoning"]

        # Liquidity sweep confirmation
        confluence_score += self._analyze_liquidity_sweeps(liquidity_sweeps, reasoning)

        # Volume confirmation
        confluence_score += self._analyze_volume_confirmation(
            h1_candlesticks, reasoning
        )

        return {
            "confluence_score": confluence_score,
            "reasoning": reasoning,
            "relevant_obs": relevant_obs["obs"],
            "relevant_fvgs": relevant_fvgs["fvgs"],
        }

    def _analyze_single_timeframe_structure(
        self, h1_structure, confluence_score, reasoning
    ):
        """Analyze single timeframe structure for confluence."""
        if h1_structure == MarketStructure.BULLISH_BOS:
            confluence_score += 0.3
            reasoning.append("H1 bullish break of structure")
        elif h1_structure == MarketStructure.BULLISH_CHoCH:
            confluence_score += 0.25
            reasoning.append("H1 bullish change of character")
        elif h1_structure == MarketStructure.BEARISH_BOS:
            confluence_score += 0.3
            reasoning.append("H1 bearish break of structure")
        elif h1_structure == MarketStructure.BEARISH_CHoCH:
            confluence_score += 0.25
            reasoning.append("H1 bearish change of character")

        return confluence_score, reasoning

    def _validate_relevant_order_blocks(
        self, order_blocks, current_price, confluence_score, reasoning
    ):
        """Validate and analyze relevant order blocks."""
        relevant_obs = []

        for ob in order_blocks:
            if (
                not ob.mitigated
                and ob.volume_confirmed
                and ob.impulsive_exit
                and (
                    (
                        ob.direction == "bullish"
                        and current_price >= ob.price_low * 0.995
                    )
                    or (
                        ob.direction == "bearish"
                        and current_price <= ob.price_high * 1.005
                    )
                )
            ):

                confluence_score += 0.25 * ob.strength
                relevant_obs.append(ob)
                reasoning.append(
                    f"Validated {ob.direction} OB at {ob.price_low:.4f}-{ob.price_high:.4f}"
                )

        return {
            "obs": relevant_obs,
            "confluence_score": confluence_score,
            "reasoning": reasoning,
        }

    def _analyze_relevant_fvgs(self, fvgs, current_price, confluence_score, reasoning):
        """Analyze relevant Fair Value Gaps."""
        relevant_fvgs = []

        for fvg in fvgs:
            if (
                not fvg.filled
                and fvg.atr_size >= self.fvg_multiplier
                and (
                    (
                        fvg.direction == "bullish"
                        and fvg.gap_low <= current_price <= fvg.gap_high
                    )
                    or (
                        fvg.direction == "bearish"
                        and fvg.gap_low <= current_price <= fvg.gap_high
                    )
                )
            ):

                age_factor = max(
                    0.5, 1.0 - (fvg.age_candles / SMCConfig.FVG_MAX_AGE_CANDLES)
                )
                fvg_weight = 0.2 * fvg.atr_size * age_factor
                confluence_score += fvg_weight
                relevant_fvgs.append(fvg)
                reasoning.append(f"Valid {fvg.direction} FVG (ATR: {fvg.atr_size:.2f})")

        return {
            "fvgs": relevant_fvgs,
            "confluence_score": confluence_score,
            "reasoning": reasoning,
        }

    def _analyze_liquidity_sweeps(self, liquidity_sweeps, reasoning):
        """Analyze liquidity sweeps for confluence."""
        sweep_confirmation = 0.0

        for sweep_type, sweeps in liquidity_sweeps.items():
            for sweep in sweeps:
                if sweep["confirmed"]:
                    sweep_confirmation += 0.3
                    reasoning.append(f"Confirmed {sweep_type.replace('_', ' ')} sweep")
                else:
                    sweep_confirmation += 0.1
                    reasoning.append(
                        f"Unconfirmed {sweep_type.replace('_', ' ')} sweep"
                    )

        return sweep_confirmation

    def _analyze_volume_confirmation(self, h1_candlesticks, reasoning):
        """Analyze volume confirmation for confluence."""
        recent_volumes = [c["volume"] for c in h1_candlesticks[-5:] if c["volume"] > 0]
        if not recent_volumes:
            return 0.0

        avg_volume = sum(recent_volumes) / len(recent_volumes)
        current_volume = h1_candlesticks[-1]["volume"]

        if current_volume >= avg_volume * SMCConfig.HIGH_VOLUME_THRESHOLD:
            reasoning.append(f"High volume confirmation")
            return 0.15

        return 0.0

    def _determine_signal_direction(
        self, alignment, h1_structure, relevant_obs, relevant_fvgs
    ):
        """Determine the signal direction based on analysis."""
        direction = None
        additional_score = 0.0

        if alignment and alignment["aligned"]:
            direction = alignment["direction"]
        else:
            bullish_signals = [
                MarketStructure.BULLISH_BOS,
                MarketStructure.BULLISH_CHoCH,
            ]
            bearish_signals = [
                MarketStructure.BEARISH_BOS,
                MarketStructure.BEARISH_CHoCH,
            ]

            if h1_structure in bullish_signals:
                bullish_obs = [ob for ob in relevant_obs if ob.direction == "bullish"]
                bullish_fvgs = [
                    fvg for fvg in relevant_fvgs if fvg.direction == "bullish"
                ]
                if bullish_obs or bullish_fvgs:
                    direction = "long"
                    additional_score = 0.1

            elif h1_structure in bearish_signals:
                bearish_obs = [ob for ob in relevant_obs if ob.direction == "bearish"]
                bearish_fvgs = [
                    fvg for fvg in relevant_fvgs if fvg.direction == "bearish"
                ]
                if bearish_obs or bearish_fvgs:
                    direction = "short"
                    additional_score = 0.1

        return direction, additional_score

    def _calculate_15m_alignment_score(self, m15_structure, htf_bias: str, intermediate_structure_direction: str, current_price: float, poi_levels: List[Dict]) -> float:
        """
        Phase 3: Calculate how well 15m structure aligns with HTF bias
        
        Args:
            m15_structure: 15-minute market structure
            htf_bias: High timeframe bias ('bullish', 'bearish', or 'neutral')
            intermediate_structure_direction: Direction of H4/H1 intermediate structure
            current_price: Current market price
            poi_levels: Points of interest from intermediate structure
            
        Returns:
            alignment_score: 0.0 (conflict) to 1.0 (perfect alignment)
        """
        alignment_score = 0.0
        
        if htf_bias == "neutral" or not m15_structure:
            return 0.5  # Neutral - no alignment either way
        
        # Check 15m structure alignment with HTF bias
        if htf_bias == "bullish":
            if m15_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                alignment_score += 0.5  # Strong bullish alignment
            elif m15_structure == MarketStructure.CONSOLIDATION:
                alignment_score += 0.3  # Neutral but acceptable
            elif m15_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                # ISSUE #22 FIX: Conflict detected - exit early without bonuses
                alignment_score = 0.1  # Conflict - bearish structure against bullish bias
                return min(alignment_score, 1.0)  # Exit immediately, no bonuses for conflicts
                
        elif htf_bias == "bearish":
            if m15_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                alignment_score += 0.5  # Strong bearish alignment
            elif m15_structure == MarketStructure.CONSOLIDATION:
                alignment_score += 0.3  # Neutral but acceptable
            elif m15_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                # ISSUE #22 FIX: Conflict detected - exit early without bonuses
                alignment_score = 0.1  # Conflict - bullish structure against bearish bias
                return min(alignment_score, 1.0)  # Exit immediately, no bonuses for conflicts
        
        # Bonus: Check if intermediate structure also aligns (only if no conflict)
        if intermediate_structure_direction and intermediate_structure_direction.startswith(htf_bias):
            alignment_score += 0.3
        
        # Bonus: Check if price is near a POI aligned with HTF bias (only if no conflict)
        for poi in poi_levels:
            price_diff_pct = abs(current_price - poi["price"]) / current_price * 100
            if price_diff_pct < 1.0 and poi["direction"] == htf_bias:
                alignment_score += 0.2
                break
        
        # Ensure score is between 0.0 and 1.0
        return min(alignment_score, 1.0)

    def _calculate_signal_strength_and_confidence(
        self, 
        confluence_score: float,
        m15_alignment_score: float = 0.5,
        liquidity_swept: bool = False,
        sweep_confirmed: bool = False,
        entry_from_htf_poi: bool = False
    ):
        """
        Phase 3: Calculate signal strength and final confidence with 15m alignment bonuses
        
        Confidence Scoring Rules:
        - Base confidence from confluence score (0.5 - 0.8)
        - +0.2 bonus if 15m perfectly aligns with HTF bias (alignment >= 0.8)
        - -0.3 penalty if 15m conflicts with HTF bias (alignment < 0.3) - signal should be rejected
        - +0.1 bonus if liquidity sweep confirmed
        - +0.1 bonus if entry from H1/H4 OB/FVG
        
        Args:
            confluence_score: Base confluence score from SMC analysis
            m15_alignment_score: 15m alignment score (0.0-1.0)
            liquidity_swept: Whether liquidity was swept
            sweep_confirmed: Whether the sweep was confirmed
            entry_from_htf_poi: Whether entry is from HTF point of interest
        
        Returns:
            (signal_strength, final_confidence)
        """
        # Start with base confidence from confluence
        base_confidence = min(confluence_score / 5.0, 1.0)
        final_confidence = base_confidence
        
        # Phase 3: Apply 15m alignment adjustments
        if m15_alignment_score >= 0.8:
            # Perfect alignment bonus
            final_confidence += 0.2
            logging.debug(f"Phase 3: +0.2 confidence bonus for perfect 15m alignment (score: {m15_alignment_score:.2f})")
        elif m15_alignment_score < 0.3:
            # Conflict penalty - signal should be rejected before reaching here
            final_confidence -= 0.3
            logging.warning(f"Phase 3: -0.3 confidence penalty for 15m conflict (score: {m15_alignment_score:.2f})")
        
        # Liquidity sweep confluence bonus
        if liquidity_swept and sweep_confirmed:
            final_confidence += 0.1
            logging.debug("Phase 3: +0.1 confidence bonus for confirmed liquidity sweep")
        
        # HTF POI entry bonus
        if entry_from_htf_poi:
            final_confidence += 0.1
            logging.debug("Phase 3: +0.1 confidence bonus for HTF POI entry")
        
        # Ensure confidence stays in valid range [0.0, 1.0]
        final_confidence = max(0.0, min(final_confidence, 1.0))
        
        # Determine signal strength based on final confidence and alignment
        if final_confidence >= 0.8 and m15_alignment_score >= 0.7:
            signal_strength = SignalStrength.VERY_STRONG
        elif final_confidence >= 0.65 and m15_alignment_score >= 0.5:
            signal_strength = SignalStrength.STRONG
        elif final_confidence >= 0.5 and m15_alignment_score >= 0.3:
            signal_strength = SignalStrength.MODERATE
        else:
            signal_strength = SignalStrength.WEAK
        
        logging.info(f"Phase 3: Final confidence: {final_confidence:.2f} (base: {base_confidence:.2f}, 15m alignment: {m15_alignment_score:.2f}) - {signal_strength.value}")

        return signal_strength, final_confidence

    def _find_nearest_bullish_fvg(
        self, 
        current_price: float, 
        fvgs: List[FairValueGap]
    ) -> Optional[Dict[str, Union[float, str]]]:
        """Find nearest bullish FVG below current price"""
        valid_fvgs = [
            fvg for fvg in fvgs 
            if fvg.direction == "bullish" 
            and not fvg.filled 
            and fvg.gap_high < current_price
        ]
        
        if not valid_fvgs:
            return None
        
        nearest_fvg = min(valid_fvgs, key=lambda x: current_price - x.gap_high)
        return {
            "high": nearest_fvg.gap_high,
            "low": nearest_fvg.gap_low,
            "type": "FVG"
        }
    
    def _find_nearest_bearish_fvg(
        self, 
        current_price: float, 
        fvgs: List[FairValueGap]
    ) -> Optional[Dict[str, Union[float, str]]]:
        """Find nearest bearish FVG above current price"""
        valid_fvgs = [
            fvg for fvg in fvgs 
            if fvg.direction == "bearish" 
            and not fvg.filled 
            and fvg.gap_low > current_price
        ]
        
        if not valid_fvgs:
            return None
        
        nearest_fvg = min(valid_fvgs, key=lambda x: x.gap_low - current_price)
        return {
            "high": nearest_fvg.gap_high,
            "low": nearest_fvg.gap_low,
            "type": "FVG"
        }
    
    def _find_nearest_bullish_ob(
        self, 
        current_price: float, 
        order_blocks: List[OrderBlock]
    ) -> Optional[Dict[str, Union[float, str]]]:
        """Find nearest bullish OB below current price"""
        valid_obs = [
            ob for ob in order_blocks 
            if ob.direction == "bullish" 
            and not ob.mitigated 
            and ob.price_high < current_price
        ]
        
        if not valid_obs:
            return None
        
        nearest_ob = min(valid_obs, key=lambda x: current_price - x.price_high)
        return {
            "high": nearest_ob.price_high,
            "low": nearest_ob.price_low,
            "type": "OB"
        }
    
    def _find_nearest_bearish_ob(
        self, 
        current_price: float, 
        order_blocks: List[OrderBlock]
    ) -> Optional[Dict[str, Union[float, str]]]:
        """Find nearest bearish OB above current price"""
        valid_obs = [
            ob for ob in order_blocks 
            if ob.direction == "bearish" 
            and not ob.mitigated 
            and ob.price_low > current_price
        ]
        
        if not valid_obs:
            return None
        
        nearest_ob = min(valid_obs, key=lambda x: x.price_low - current_price)
        return {
            "high": nearest_ob.price_high,
            "low": nearest_ob.price_low,
            "type": "OB"
        }
    
    def _zones_overlap(
        self, 
        zone1: Dict[str, Union[float, str]], 
        zone2: Dict[str, Union[float, str]]
    ) -> bool:
        """Check if two zones overlap"""
        high1 = float(zone1["high"])
        low1 = float(zone1["low"])
        high2 = float(zone2["high"])
        low2 = float(zone2["low"])
        return not (high1 < low2 or high2 < low1)
    
    def _merge_zones(
        self, 
        zone1: Dict[str, Union[float, str]], 
        zone2: Dict[str, Union[float, str]]
    ) -> Dict[str, Union[float, str]]:
        """Merge two overlapping zones"""
        return {
            "high": max(float(zone1["high"]), float(zone2["high"])),
            "low": min(float(zone1["low"]), float(zone2["low"])),
            "type": f"{zone1['type']}+{zone2['type']}"
        }
    
    def _adjust_zone_for_volatility(
        self, 
        zone: Dict[str, Union[float, str]], 
        volatility_regime: str
    ) -> Dict[str, Union[float, str]]:
        """Expand or shrink zone based on volatility regime"""
        high = float(zone["high"])
        low = float(zone["low"])
        zone_size = high - low
        
        if volatility_regime == "high":
            expansion = zone_size * 0.10
            return {
                "high": high + expansion,
                "low": low - expansion,
                "type": zone["type"]
            }
        elif volatility_regime == "low":
            contraction = zone_size * 0.10
            new_high = high - contraction
            new_low = low + contraction
            
            if new_high <= new_low:
                logging.warning(f"Zone too small for contraction (size: {zone_size:.6f}), using original zone")
                return zone
            
            return {
                "high": new_high,
                "low": new_low,
                "type": zone["type"]
            }
        else:
            return zone
    
    def _round_to_tick(
        self, 
        price: float, 
        tick_size: float = 0.01
    ) -> float:
        """Round price to symbol's tick size"""
        if tick_size <= 0:
            logging.warning(f"Invalid tick_size {tick_size}, using default 0.01")
            tick_size = 0.01
        return round(price / tick_size) * tick_size

    def _calculate_scaled_entries(
        self,
        current_price: float,
        direction: str,
        order_blocks: List[OrderBlock],
        fvgs: List[FairValueGap],
        base_stop_loss: float,
        base_take_profits: List[Tuple[float, float]],
        volatility_regime: str = "normal",
        tick_size: float = 0.01
    ) -> List[ScaledEntry]:
        """
        Phase 4: Calculate 3-level scaled entry strategy using SMC zones
        
        UPGRADED: Uses institutional-style Smart Money Concepts (SMC) rules
        for limit placement using Fair Value Gaps (FVG) and Order Blocks (OB)
        instead of fixed percentage offsets.
        
        Entry Allocation:
        - 50% at aggressive level (market or zone top/bottom)
        - 25% at balanced level (zone midpoint)
        - 25% at deep mitigation level (zone bottom/top)
        
        Args:
            current_price: Current market price
            direction: Trade direction ('long' or 'short')
            order_blocks: List of detected order blocks
            fvgs: List of fair value gaps
            base_stop_loss: Base stop-loss price
            base_take_profits: List of (price, allocation) tuples for take profits
            volatility_regime: Volatility regime ('high', 'normal', 'low')
            tick_size: Symbol's tick size for rounding (default 0.01)
        
        Returns:
            List of ScaledEntry objects
        """
        from config import TradingConfig
        
        if not TradingConfig.USE_SCALED_ENTRIES:
            return [ScaledEntry(
                entry_price=current_price,
                allocation_percent=100.0,
                order_type='market',
                stop_loss=base_stop_loss,
                take_profits=base_take_profits,
                status='pending'
            )]
        
        allocations = TradingConfig.SCALED_ENTRY_ALLOCATIONS
        
        if sum(allocations) != 100 or len(allocations) != 3:
            logging.warning(f"Invalid allocations {allocations}, using fallback [50,25,25]")
            allocations = [50, 25, 25]
        
        base_zone = None
        fvg_zone = None
        ob_zone = None
        
        if direction == "long":
            fvg_zone = self._find_nearest_bullish_fvg(current_price, fvgs)
            ob_zone = self._find_nearest_bullish_ob(current_price, order_blocks)
            
            logging.info(f"SMC Zone Detection for LONG:")
            logging.info(f"  - FVG Zone: {fvg_zone}")
            logging.info(f"  - OB Zone: {ob_zone}")
            
            if fvg_zone and ob_zone and self._zones_overlap(fvg_zone, ob_zone):
                base_zone = self._merge_zones(fvg_zone, ob_zone)
                logging.info(f"  - Using merged FVG+OB zone: {base_zone}")
            else:
                base_zone = fvg_zone or ob_zone
                if base_zone:
                    logging.info(f"  - Using {base_zone['type']} zone: {base_zone}")
            
            if base_zone:
                zone_distance = abs(current_price - float(base_zone["high"]))
                # ISSUE #16 FIX: Adaptive max distance based on volatility regime
                if volatility_regime == "high":
                    max_distance = current_price * 0.10  # 10% in high volatility
                elif volatility_regime == "low":
                    max_distance = current_price * 0.03  # 3% in low volatility  
                else:
                    max_distance = current_price * 0.05  # 5% normal
                
                if zone_distance > max_distance:
                    logging.warning(f"Zone too far from current price ({zone_distance:.2f} vs max {max_distance:.2f} for {volatility_regime} volatility), using fallback")
                    base_zone = None
        
        else:
            fvg_zone = self._find_nearest_bearish_fvg(current_price, fvgs)
            ob_zone = self._find_nearest_bearish_ob(current_price, order_blocks)
            
            logging.info(f"SMC Zone Detection for SHORT:")
            logging.info(f"  - FVG Zone: {fvg_zone}")
            logging.info(f"  - OB Zone: {ob_zone}")
            
            if fvg_zone and ob_zone and self._zones_overlap(fvg_zone, ob_zone):
                base_zone = self._merge_zones(fvg_zone, ob_zone)
                logging.info(f"  - Using merged FVG+OB zone: {base_zone}")
            else:
                base_zone = fvg_zone or ob_zone
                if base_zone:
                    logging.info(f"  - Using {base_zone['type']} zone: {base_zone}")
            
            if base_zone:
                zone_distance = abs(current_price - float(base_zone["low"]))
                # ISSUE #16 FIX: Adaptive max distance based on volatility regime
                if volatility_regime == "high":
                    max_distance = current_price * 0.10  # 10% in high volatility
                elif volatility_regime == "low":
                    max_distance = current_price * 0.03  # 3% in low volatility  
                else:
                    max_distance = current_price * 0.05  # 5% normal
                
                if zone_distance > max_distance:
                    logging.warning(f"Zone too far from current price ({zone_distance:.2f} vs max {max_distance:.2f} for {volatility_regime} volatility), using fallback")
                    base_zone = None
        
        scaled_entries = []
        
        if base_zone:
            adjusted_zone = self._adjust_zone_for_volatility(base_zone, volatility_regime)
            logging.info(f"  - Volatility Regime: {volatility_regime}")
            logging.info(f"  - Adjusted Zone: {adjusted_zone}")
            
            zone_high = float(adjusted_zone["high"])
            zone_low = float(adjusted_zone["low"])
            zone_mid = (zone_high + zone_low) / 2.0
            
            if direction == "long":
                entry1_price = current_price
                entry2_price = self._round_to_tick(zone_mid, tick_size)
                entry3_price = self._round_to_tick(zone_low, tick_size)
                
                # ISSUE #15 FIX: Validate and correct invalid LONG entry ordering
                if not (entry1_price >= entry2_price >= entry3_price):
                    logging.warning(f"Correcting invalid LONG entry ordering: {entry1_price:.4f} >= {entry2_price:.4f} >= {entry3_price:.4f}")
                    # Ensure entry2 is below entry1
                    if entry2_price > entry1_price:
                        entry2_price = entry1_price * 0.996
                    # Ensure entry3 is below entry2
                    if entry3_price > entry2_price:
                        entry3_price = entry2_price * 0.996
                    logging.info(f"Corrected to: {entry1_price:.4f} >= {entry2_price:.4f} >= {entry3_price:.4f}")
            else:
                entry1_price = current_price
                entry2_price = self._round_to_tick(zone_mid, tick_size)
                entry3_price = self._round_to_tick(zone_high, tick_size)
                
                # ISSUE #15 FIX: Validate and correct invalid SHORT entry ordering
                if not (entry1_price <= entry2_price <= entry3_price):
                    logging.warning(f"Correcting invalid SHORT entry ordering: {entry1_price:.4f} <= {entry2_price:.4f} <= {entry3_price:.4f}")
                    # Ensure entry2 is above entry1
                    if entry2_price < entry1_price:
                        entry2_price = entry1_price * 1.004
                    # Ensure entry3 is above entry2
                    if entry3_price < entry2_price:
                        entry3_price = entry2_price * 1.004
                    logging.info(f"Corrected to: {entry1_price:.4f} <= {entry2_price:.4f} <= {entry3_price:.4f}")
            
            entry1 = ScaledEntry(
                entry_price=entry1_price,
                allocation_percent=allocations[0],
                order_type='market',
                stop_loss=base_stop_loss,
                take_profits=base_take_profits,
                status='pending'
            )
            scaled_entries.append(entry1)
            
            entry2 = ScaledEntry(
                entry_price=entry2_price,
                allocation_percent=allocations[1],
                order_type='limit',
                stop_loss=base_stop_loss,
                take_profits=base_take_profits,
                status='pending'
            )
            scaled_entries.append(entry2)
            
            entry3 = ScaledEntry(
                entry_price=entry3_price,
                allocation_percent=allocations[2],
                order_type='limit',
                stop_loss=base_stop_loss,
                take_profits=base_take_profits,
                status='pending'
            )
            scaled_entries.append(entry3)
            
            logging.info(f"Phase 4: SMC Zone-Based Entries ({adjusted_zone['type']}):")
            logging.info(f"  - Entry 1 (Market): ${entry1_price:.4f} ({allocations[0]}%)")
            logging.info(f"  - Entry 2 (Balanced Limit): ${entry2_price:.4f} ({allocations[1]}%)")
            logging.info(f"  - Entry 3 (Deep Limit): ${entry3_price:.4f} ({allocations[2]}%)")
        
        else:
            logging.warning(f"No valid FVG/OB zone found for {direction} - using fixed-percentage fallback")
            
            entry1_price = current_price
            entry1 = ScaledEntry(
                entry_price=entry1_price,
                allocation_percent=allocations[0],
                order_type='market',
                stop_loss=base_stop_loss,
                take_profits=base_take_profits,
                status='pending'
            )
            scaled_entries.append(entry1)
            
            depth1 = self.scaled_entry_depths[0]
            depth2 = self.scaled_entry_depths[1]
            
            if direction == "long":
                entry2_price = current_price * (1 - depth1)
                entry3_price = current_price * (1 - depth2)
            else:
                entry2_price = current_price * (1 + depth1)
                entry3_price = current_price * (1 + depth2)
            
            entry2_price = self._align_entry_with_poi(
                entry2_price, direction, order_blocks, fvgs, max_distance_pct=0.5
            )
            entry3_price = self._align_entry_with_poi(
                entry3_price, direction, order_blocks, fvgs, max_distance_pct=1.0
            )
            
            entry2 = ScaledEntry(
                entry_price=entry2_price,
                allocation_percent=allocations[1],
                order_type='limit',
                stop_loss=base_stop_loss,
                take_profits=base_take_profits,
                status='pending'
            )
            scaled_entries.append(entry2)
            
            entry3 = ScaledEntry(
                entry_price=entry3_price,
                allocation_percent=allocations[2],
                order_type='limit',
                stop_loss=base_stop_loss,
                take_profits=base_take_profits,
                status='pending'
            )
            scaled_entries.append(entry3)
            
            logging.info(f"Phase 4: Fallback Scaled Entries - Market: ${entry1_price:.2f} ({allocations[0]}%), "
                        f"Limit1: ${entry2_price:.2f} ({allocations[1]}%), Limit2: ${entry3_price:.2f} ({allocations[2]}%)")
        
        return scaled_entries

    def _align_entry_with_poi(
        self,
        target_price: float,
        direction: str,
        order_blocks: List[OrderBlock],
        fvgs: List[FairValueGap],
        max_distance_pct: float = 0.5
    ) -> float:
        """
        Phase 4: Align entry price with nearest order block or FVG if within tolerance
        
        Args:
            target_price: Target entry price
            direction: Trade direction ('long' or 'short')
            order_blocks: List of order blocks
            fvgs: List of fair value gaps
            max_distance_pct: Maximum distance (%) to adjust price
        
        Returns:
            Adjusted entry price (or original if no suitable POI)
        """
        best_price = target_price
        min_distance = float('inf')
        
        # Check order blocks
        for ob in order_blocks:
            if direction == "long" and ob.direction == "bullish" and not ob.mitigated:
                # For long, check if OB is below target (discount)
                ob_price = (ob.price_high + ob.price_low) / 2
                if ob_price < target_price:
                    distance_pct = abs(ob_price - target_price) / target_price * 100
                    if distance_pct < max_distance_pct and distance_pct < min_distance:
                        best_price = ob_price
                        min_distance = distance_pct
            
            elif direction == "short" and ob.direction == "bearish" and not ob.mitigated:
                # For short, check if OB is above target (premium)
                ob_price = (ob.price_high + ob.price_low) / 2
                if ob_price > target_price:
                    distance_pct = abs(ob_price - target_price) / target_price * 100
                    if distance_pct < max_distance_pct and distance_pct < min_distance:
                        best_price = ob_price
                        min_distance = distance_pct
        
        # Check FVGs
        for fvg in fvgs:
            if direction == "long" and fvg.direction == "bullish" and not fvg.filled:
                # For long, aim for middle of bullish FVG
                fvg_price = (fvg.gap_high + fvg.gap_low) / 2
                if fvg_price < target_price:
                    distance_pct = abs(fvg_price - target_price) / target_price * 100
                    if distance_pct < max_distance_pct and distance_pct < min_distance:
                        best_price = fvg_price
                        min_distance = distance_pct
            
            elif direction == "short" and fvg.direction == "bearish" and not fvg.filled:
                # For short, aim for middle of bearish FVG
                fvg_price = (fvg.gap_high + fvg.gap_low) / 2
                if fvg_price > target_price:
                    distance_pct = abs(fvg_price - target_price) / target_price * 100
                    if distance_pct < max_distance_pct and distance_pct < min_distance:
                        best_price = fvg_price
                        min_distance = distance_pct
        
        if best_price != target_price:
            logging.debug(f"Phase 4: Aligned entry from ${target_price:.2f} to ${best_price:.2f} (POI distance: {min_distance:.2f}%)")
        
        return best_price

    def _calculate_entry_specific_sl(
        self,
        entry_price: float,
        direction: str,
        m15_swing_levels: Dict,
        atr_value: float = 0.0
    ) -> float:
        """
        Phase 4: Calculate stop-loss specific to each entry level using 15m swings
        
        Args:
            entry_price: Entry price for this specific level
            direction: Trade direction ('long' or 'short')
            m15_swing_levels: Dictionary with 15m swing highs and lows
            atr_value: ATR value for buffer (optional)
        
        Returns:
            Stop-loss price for this entry
        """
        # Use 15m swing levels for precise stop-loss
        if direction == "long":
            # For long, SL below last swing low
            sl_base = m15_swing_levels.get("last_swing_low", entry_price * 0.98)
            # Add small ATR buffer if provided
            if atr_value > 0:
                sl = sl_base - (atr_value * 0.5)
            else:
                sl = sl_base
        else:  # short
            # For short, SL above last swing high
            sl_base = m15_swing_levels.get("last_swing_high", entry_price * 1.02)
            # Add small ATR buffer if provided
            if atr_value > 0:
                sl = sl_base + (atr_value * 0.5)
            else:
                sl = sl_base
        
        logging.debug(f"Phase 4: Entry-specific SL for ${entry_price:.2f} ({direction}): ${sl:.2f}")
        
        return sl

    def _find_15m_swing_levels(self, m15_data: List[Dict]) -> Dict:
        """
        Phase 5: Find recent swing highs and lows on 15m timeframe
        
        Args:
            m15_data: List of 15-minute candlestick dictionaries
        
        Returns:
            Dictionary with swing levels:
            {
                "swing_highs": [list of swing high prices],
                "swing_lows": [list of swing low prices],
                "last_swing_high": most recent swing high,
                "last_swing_low": most recent swing low
            }
        """
        if len(m15_data) < 5:
            logging.warning("Phase 5: Not enough 15m data for swing detection")
            return {
                "swing_highs": [],
                "swing_lows": [],
                "last_swing_high": None,
                "last_swing_low": None
            }
        
        swing_highs = []
        swing_lows = []
        lookback = 2  # Look 2 candles before and after for swing points
        
        # Identify swing highs and lows
        for i in range(lookback, len(m15_data) - lookback):
            current_candle = m15_data[i]
            current_high = current_candle["high"]
            current_low = current_candle["low"]
            
            # Check if this is a swing high
            is_swing_high = True
            for j in range(i - lookback, i + lookback + 1):
                if j != i and m15_data[j]["high"] >= current_high:
                    is_swing_high = False
                    break
            
            if is_swing_high:
                swing_highs.append(current_high)
            
            # Check if this is a swing low
            is_swing_low = True
            for j in range(i - lookback, i + lookback + 1):
                if j != i and m15_data[j]["low"] <= current_low:
                    is_swing_low = False
                    break
            
            if is_swing_low:
                swing_lows.append(current_low)
        
        result = {
            "swing_highs": swing_highs,
            "swing_lows": swing_lows,
            "last_swing_high": swing_highs[-1] if swing_highs else None,
            "last_swing_low": swing_lows[-1] if swing_lows else None
        }
        
        logging.debug(f"Phase 5: Found {len(swing_highs)} swing highs and {len(swing_lows)} swing lows on 15m")
        if result["last_swing_high"]:
            logging.debug(f"Phase 5: Last swing high: ${result['last_swing_high']:.2f}")
        if result["last_swing_low"]:
            logging.debug(f"Phase 5: Last swing low: ${result['last_swing_low']:.2f}")
        
        return result

    def _calculate_refined_sl_with_atr(
        self,
        direction: str,
        swing_levels: Dict,
        atr_value: float,
        current_price: float,
        atr_buffer_multiplier: float = 0.5
    ) -> float:
        """
        Phase 5: Calculate stop-loss using 15m swings + ATR buffer
        
        Args:
            direction: Trade direction ('long' or 'short')
            swing_levels: Dictionary with 15m swing highs and lows from _find_15m_swing_levels()
            atr_value: ATR value for buffer calculation
            current_price: Current market price for fallback calculation
            atr_buffer_multiplier: Multiplier for ATR buffer (default 0.5)
        
        Returns:
            Refined stop-loss price
        """
        from config import TradingConfig
        
        min_sl_distance_percent = getattr(TradingConfig, 'SL_MIN_DISTANCE_PERCENT', 0.5) / 100.0
        
        if direction == "long":
            # For long, SL below last swing low - ATR buffer
            if swing_levels["last_swing_low"]:
                sl_base = swing_levels["last_swing_low"]
                sl = sl_base - (atr_value * atr_buffer_multiplier)
                logging.debug(f"Phase 5: Long SL from swing low ${sl_base:.2f} - ATR buffer = ${sl:.2f}")
            else:
                # Fallback: use 2% below current price
                sl = current_price * (1 - 0.02)
                logging.warning(f"Phase 5: No swing low found, using fallback SL: ${sl:.2f}")
            
            # Ensure minimum distance
            min_sl = current_price * (1 - min_sl_distance_percent)
            if sl > min_sl:
                logging.debug(f"Phase 5: SL too tight (${sl:.2f}), adjusting to min distance (${min_sl:.2f})")
                sl = min_sl
        
        else:  # short
            # For short, SL above last swing high + ATR buffer
            if swing_levels["last_swing_high"]:
                sl_base = swing_levels["last_swing_high"]
                sl = sl_base + (atr_value * atr_buffer_multiplier)
                logging.debug(f"Phase 5: Short SL from swing high ${sl_base:.2f} + ATR buffer = ${sl:.2f}")
            else:
                # Fallback: use 2% above current price
                sl = current_price * (1 + 0.02)
                logging.warning(f"Phase 5: No swing high found, using fallback SL: ${sl:.2f}")
            
            # Ensure minimum distance
            max_sl = current_price * (1 + min_sl_distance_percent)
            if sl < max_sl:
                logging.debug(f"Phase 5: SL too tight (${sl:.2f}), adjusting to min distance (${max_sl:.2f})")
                sl = max_sl
        
        return sl

    def _find_liquidity_target(
        self,
        entry_price: float,
        direction: str,
        liquidity_pools: List[LiquidityPool],
        min_distance: float
    ) -> Optional[float]:
        """
        Phase 6: Find nearest liquidity level beyond minimum distance for TP3
        
        Args:
            entry_price: Entry price of the trade
            direction: Trade direction ('long' or 'short')
            liquidity_pools: List of detected liquidity pools
            min_distance: Minimum distance from entry (e.g., 2R distance)
        
        Returns:
            Liquidity target price or None if no valid target found
        """
        if not liquidity_pools:
            return None
        
        valid_targets = []
        
        for pool in liquidity_pools:
            # For long trades, look for buy-side liquidity above entry + min_distance
            if direction == "long" and pool.type == "buy_side":
                if pool.price > (entry_price + min_distance):
                    valid_targets.append((pool.price, pool.strength))
            
            # For short trades, look for sell-side liquidity below entry - min_distance
            elif direction == "short" and pool.type == "sell_side":
                if pool.price < (entry_price - min_distance):
                    valid_targets.append((pool.price, pool.strength))
        
        if not valid_targets:
            logging.debug(f"Phase 6: No valid liquidity targets found beyond {min_distance:.2f} distance")
            return None
        
        # Sort by distance from entry (nearest first) and strength
        valid_targets.sort(key=lambda x: (abs(x[0] - entry_price), -x[1]))
        
        target_price = valid_targets[0][0]
        target_strength = valid_targets[0][1]
        
        logging.info(
            f"Phase 6: Found liquidity target at ${target_price:.2f} "
            f"(strength: {target_strength:.2f}) for {direction} trade"
        )
        
        return target_price

    def _calculate_rr_based_take_profits(
        self,
        entry_price: float,
        stop_loss: float,
        direction: str,
        liquidity_targets: List[float]
    ) -> List[Tuple[float, float]]:
        """
        Phase 6: Calculate take profit levels based on R:R ratios
        
        Take Profit Levels:
        - TP1: 1R (100% risk amount as profit) - 40% allocation
        - TP2: 2R (200% risk amount as profit) - 30% allocation
        - TP3: Liquidity cluster / HTF OB target or 3R - 30% allocation
        
        Args:
            entry_price: Entry price of the trade
            stop_loss: Stop-loss price
            direction: Trade direction ('long' or 'short')
            liquidity_targets: List of potential liquidity target prices
        
        Returns:
            List of (TP price, allocation %) tuples
        """
        from config import TradingConfig
        
        # Get configuration
        tp_allocations = getattr(TradingConfig, 'TP_ALLOCATIONS', [40, 30, 30])
        tp_rr_ratios = getattr(TradingConfig, 'TP_RR_RATIOS', [1.0, 2.0, 3.0])
        
        # Calculate risk amount
        risk_amount = abs(entry_price - stop_loss)
        
        take_profits = []
        
        # TP1: 1R
        if direction == "long":
            tp1_price = entry_price + (risk_amount * tp_rr_ratios[0])
        else:  # short
            tp1_price = entry_price - (risk_amount * tp_rr_ratios[0])
        
        take_profits.append((tp1_price, tp_allocations[0]))
        logging.debug(f"Phase 6: TP1 at ${tp1_price:.2f} ({tp_rr_ratios[0]}R) - {tp_allocations[0]}% allocation")
        
        # TP2: 2R
        if direction == "long":
            tp2_price = entry_price + (risk_amount * tp_rr_ratios[1])
        else:  # short
            tp2_price = entry_price - (risk_amount * tp_rr_ratios[1])
        
        take_profits.append((tp2_price, tp_allocations[1]))
        logging.debug(f"Phase 6: TP2 at ${tp2_price:.2f} ({tp_rr_ratios[1]}R) - {tp_allocations[1]}% allocation")
        
        # TP3: Nearest liquidity target beyond 2R, or 3R
        min_distance_for_tp3 = risk_amount * tp_rr_ratios[1]  # 2R minimum
        
        # Try to find liquidity target (need to pass liquidity pools, not just targets)
        # For now, check if any liquidity target is beyond 2R
        tp3_price = None
        if liquidity_targets:
            for target in liquidity_targets:
                if direction == "long" and target > (entry_price + min_distance_for_tp3):
                    tp3_price = target
                    logging.info(f"Phase 6: TP3 aligned with liquidity target at ${tp3_price:.2f}")
                    break
                elif direction == "short" and target < (entry_price - min_distance_for_tp3):
                    tp3_price = target
                    logging.info(f"Phase 6: TP3 aligned with liquidity target at ${tp3_price:.2f}")
                    break
        
        # Fallback to 3R if no liquidity target found
        if not tp3_price:
            if direction == "long":
                tp3_price = entry_price + (risk_amount * tp_rr_ratios[2])
            else:  # short
                tp3_price = entry_price - (risk_amount * tp_rr_ratios[2])
            logging.debug(f"Phase 6: TP3 using {tp_rr_ratios[2]}R fallback at ${tp3_price:.2f}")
        
        take_profits.append((tp3_price, tp_allocations[2]))
        logging.debug(f"Phase 6: TP3 at ${tp3_price:.2f} - {tp_allocations[2]}% allocation")
        
        # Validate allocations sum to 100%
        total_allocation = sum(alloc for _, alloc in take_profits)
        if abs(total_allocation - 100) > 0.1:
            logging.warning(
                f"Phase 6: TP allocations sum to {total_allocation}% (expected 100%). "
                f"Check TradingConfig.TP_ALLOCATIONS"
            )
        
        return take_profits

    def _should_trail_stop_after_tp1(self, tp_statuses: List[str]) -> bool:
        """
        Phase 6: Determine if trailing stop should activate after TP1
        
        Args:
            tp_statuses: List of TP statuses ('pending', 'hit', etc.)
        
        Returns:
            True if trailing stop should activate, False otherwise
        """
        from config import TradingConfig
        
        # Check if trailing stop is enabled in config
        enable_trailing = getattr(TradingConfig, 'ENABLE_TRAILING_AFTER_TP1', True)
        
        if not enable_trailing:
            return False
        
        # Check if TP1 was hit (first TP in the list)
        if tp_statuses and len(tp_statuses) > 0:
            tp1_hit = tp_statuses[0] == 'hit'
            if tp1_hit:
                logging.info("Phase 6: TP1 hit - Trailing stop should activate")
                return True
        
        return False

    def _check_atr_filter(
        self,
        m15_data: List[Dict],
        h1_data: List[Dict],
        current_price: float,
        symbol: Optional[str] = None
    ) -> Dict:
        """
        Phase 7: Check if ATR meets minimum volatility requirements
        
        Args:
            m15_data: 15-minute candlestick data
            h1_data: Hourly candlestick data
            current_price: Current market price
            symbol: Trading pair symbol for pair-specific thresholds
        
        Returns:
            Dictionary with filter results:
            - passes: Boolean indicating if filter passed
            - atr_15m: ATR value on 15m timeframe
            - atr_h1: ATR value on H1 timeframe
            - atr_15m_percent: ATR as percentage of price on 15m
            - atr_h1_percent: ATR as percentage of price on H1
            - min_atr_15m_threshold: The threshold used for 15m
            - min_atr_h1_threshold: The threshold used for H1
            - reason: Explanation of filter result
        """
        from config import TradingConfig
        
        # Calculate ATR on both timeframes
        atr_15m = self.calculate_atr(m15_data, period=14) if len(m15_data) >= 15 else 0.0
        atr_h1 = self.calculate_atr(h1_data, period=14) if len(h1_data) >= 15 else 0.0
        
        # Calculate ATR as percentage of current price
        atr_15m_percent = (atr_15m / current_price) * 100 if current_price > 0 else 0.0
        atr_h1_percent = (atr_h1 / current_price) * 100 if current_price > 0 else 0.0
        
        # Get pair-specific thresholds from ASSET_PROFILES
        symbol_upper = symbol.upper() if symbol else "UNKNOWN"
        profile = getattr(TradingConfig, "ASSET_PROFILES", {}).get(symbol_upper, {})
        
        # ISSUE #24 FIX: Use pair-specific thresholds if available, otherwise use updated defaults
        min_atr_15m = profile.get(
            'MIN_ATR_15M_PERCENT',
            getattr(TradingConfig, 'MIN_ATR_15M_PERCENT', 0.6)  # Updated from 0.8 to 0.6
        )
        min_atr_h1 = profile.get(
            'MIN_ATR_H1_PERCENT',
            getattr(TradingConfig, 'MIN_ATR_H1_PERCENT', 0.9)  # Updated from 1.2 to 0.9
        )
        
        # Check if both timeframes meet minimum requirements
        passes_filter = (atr_15m_percent >= min_atr_15m and atr_h1_percent >= min_atr_h1)
        
        # Generate reason message with pair-specific info
        if not passes_filter:
            reason = (
                f"Phase 7: ATR filter failed for {symbol_upper} - "
                f"15m ATR: {atr_15m_percent:.2f}% (min {min_atr_15m}%), "
                f"H1 ATR: {atr_h1_percent:.2f}% (min {min_atr_h1}%)"
            )
        else:
            reason = (
                f"Phase 7: ATR filter passed for {symbol_upper} - "
                f"15m ATR: {atr_15m_percent:.2f}%, H1 ATR: {atr_h1_percent:.2f}%"
            )
        
        logging.info(reason)
        
        return {
            "passes": passes_filter,
            "atr_15m": atr_15m,
            "atr_h1": atr_h1,
            "atr_15m_percent": atr_15m_percent,
            "atr_h1_percent": atr_h1_percent,
            "min_atr_15m_threshold": min_atr_15m,
            "min_atr_h1_threshold": min_atr_h1,
            "reason": reason
        }

    def _calculate_dynamic_position_size(
        self,
        base_size: float,
        atr_percent: float
    ) -> float:
        """
        Phase 7: Adjust position size based on ATR volatility (OPTIONAL FEATURE)
        
        Higher ATR (more volatility) = smaller position size for risk management
        Lower ATR (less volatility) = larger position size (within limits)
        
        Args:
            base_size: Base position size multiplier (usually 1.0)
            atr_percent: ATR as percentage of price
        
        Returns:
            Adjusted position size multiplier (0.5 - 1.5)
        """
        from config import TradingConfig
        
        # Check if dynamic sizing is enabled
        use_dynamic = getattr(TradingConfig, 'USE_DYNAMIC_POSITION_SIZING', False)
        
        if not use_dynamic:
            return base_size
        
        # Volatility-based adjustment
        if atr_percent > 3.0:
            # High volatility - reduce size to 70%
            adjusted_size = base_size * 0.7
            logging.info(f"Phase 7: High volatility (ATR {atr_percent:.2f}%) - Reducing position size to 70%")
        elif atr_percent < 1.5:
            # Low volatility (but above threshold) - increase size to 120%
            adjusted_size = base_size * 1.2
            logging.info(f"Phase 7: Low volatility (ATR {atr_percent:.2f}%) - Increasing position size to 120%")
        else:
            # Normal volatility - use base size
            adjusted_size = base_size
            logging.debug(f"Phase 7: Normal volatility (ATR {atr_percent:.2f}%) - Using base position size")
        
        # Ensure size stays within reasonable bounds (0.5 - 1.5)
        adjusted_size = max(0.5, min(1.5, adjusted_size))
        
        return adjusted_size
