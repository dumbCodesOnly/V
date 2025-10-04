"""
Smart Money Concepts (SMC) Analysis Engine
Analyzes market structure and provides trade suggestions based on institutional trading patterns
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

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
        self.timeframes = ["15m", "1h", "4h", "1d"]  # Multiple timeframe analysis (15m for execution)
        self.active_signals = {}  # Cache for active signals {symbol: {signal, expiry_time}}
        self.signal_timeout = 3600  # Signal valid for 1 hour (3600 seconds)

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

            # EFFICIENT OPTIMIZATION: If we have existing cache data, check if we just need to update open candle
            if len(cached_data) > 0:
                # Check if we have current open candle already
                current_open_candle = KlinesCache.get_current_open_candle(symbol, timeframe)
                if current_open_candle:
                    # Just update the existing open candle (most efficient approach)
                    fetch_limit = 1
                    logging.info(
                        f"EFFICIENT OPEN CANDLE UPDATE: Fetching only current candle for {symbol} {timeframe}"
                    )
                else:
                    # Fetch only latest 2 candles (current + previous for gap filling)
                    fetch_limit = min(2, gap_info["fetch_count"])
                    logging.info(
                        f"CACHE UPDATE: Fetching latest {fetch_limit} candles for {symbol} {timeframe} to stay current"
                    )
            else:
                # No cache data - fetch full amount 
                fetch_limit = gap_info["fetch_count"]
                logging.info(
                    f"CACHE MISS: Fetching {fetch_limit} candles for {symbol} {timeframe}"
                )

        except Exception as e:
            logging.warning(f"Gap analysis failed for {symbol} {timeframe}: {e}")
            # Conservative fallback: only fetch latest 1 candle if we have cached data (efficient open candle update)
            fetch_limit = 1 if len(cached_data) > 0 else limit

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
                    # Normalize timestamp to timezone-aware UTC for consistent comparison
                    if isinstance(candle["timestamp"], datetime):
                        # Ensure all timestamps are timezone-aware UTC
                        if candle["timestamp"].tzinfo is None:
                            normalized_timestamp = candle["timestamp"].replace(tzinfo=timezone.utc)
                        else:
                            normalized_timestamp = candle["timestamp"].astimezone(timezone.utc)
                        candle["timestamp"] = normalized_timestamp
                        timestamp_key = normalized_timestamp.isoformat()
                    else:
                        timestamp_key = str(candle["timestamp"])
                        
                    if timestamp_key not in seen_timestamps:
                        seen_timestamps.add(timestamp_key)
                        unique_data.append(candle)

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

            # Volume filter - require above average volume
            volume_confirmed = (
                current["volume"] >= avg_volume * SMCConfig.OB_VOLUME_MULTIPLIER
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

        return order_blocks[-5:]  # Return last 5 order blocks

    def find_fair_value_gaps(self, candlesticks: List[Dict]) -> List[FairValueGap]:
        """Enhanced FVG detection with ATR filtering and alignment scoring"""
        fvgs = []

        if len(candlesticks) < SMCConfig.MIN_CANDLESTICKS_FOR_FVG:
            return fvgs

        # Calculate ATR for gap size filtering with safety floor
        atr = self.calculate_atr(candlesticks)
        if atr <= 0:  # Guard against insufficient data
            current_price = candlesticks[-1]["close"]
            atr = current_price * 0.001  # Use same 0.1% floor as trade calculations
        min_gap_size = atr * SMCConfig.FVG_ATR_MULTIPLIER

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
                    fvg = FairValueGap(
                        gap_high=next_candle["low"],
                        gap_low=prev_candle["high"],
                        timestamp=current["timestamp"],
                        direction="bullish",
                        atr_size=gap_size / atr,
                        age_candles=0,
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
                    fvg = FairValueGap(
                        gap_high=prev_candle["low"],
                        gap_low=next_candle["high"],
                        timestamp=current["timestamp"],
                        direction="bearish",
                        atr_size=gap_size / atr,
                        age_candles=0,
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

        return valid_fvgs[-10:]  # Return last 10 valid FVGs

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
        for low in swing_lows[-5:]:
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
        # Calculate ATR with robust fallback for insufficient data
        atr = self.calculate_atr(candlesticks)
        
        # Apply ATR floor to handle low volatility and insufficient data cases
        min_atr = current_price * 0.001  # 0.1% of price minimum (removed problematic absolute floor)
        if atr <= 0:
            # Calculate median true range from available data as fallback
            if len(candlesticks) >= 2:
                true_ranges = []
                for i in range(1, min(len(candlesticks), 20)):
                    current = candlesticks[i]
                    prev = candlesticks[i - 1]
                    tr = max(
                        current["high"] - current["low"],
                        abs(current["high"] - prev["close"]),
                        abs(current["low"] - prev["close"])
                    )
                    true_ranges.append(tr)
                atr = sum(true_ranges) / len(true_ranges) if true_ranges else min_atr
            else:
                atr = min_atr
        
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
                        entry_price = ob.price_high  # Use top of OB as entry
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
            
        # Final safety check: ensure stop loss is positive and below entry
        stop_loss = max(stop_loss, current_price * 0.01)  # At least 1% of price
        stop_loss = min(stop_loss, entry_price * 0.99)  # Below entry price

        # Take profits with guaranteed valid levels
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
        
        # Select TP1
        if tp1_candidates:
            tp1 = min(tp1_candidates)
        else:
            tp1 = entry_price + max(atr * 1.0, current_price * 0.01)
        
        take_profits.append(tp1)
        
        # TP2: Extended target, ensuring it's above TP1
        tp2_base = entry_price + max(atr * 2.0, current_price * 0.02)
        extended_highs = [s["high"] for s in swing_highs[-10:] if s["high"] > tp1]
        if extended_highs:
            tp2 = min(extended_highs)
            tp2 = max(tp2, tp1 * 1.01)  # Ensure TP2 > TP1
        else:
            tp2 = max(tp2_base, tp1 * 1.01)
        
        take_profits.append(tp2)
        
        # TP3: Full extension, ensuring it's above TP2
        tp3 = max(
            entry_price + max(atr * 3.0, current_price * 0.03),
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

        return entry_price, stop_loss, take_profits[:3]  # Return exactly 3 TPs

    def _calculate_short_trade_levels(self, current_price, order_blocks, candlesticks):
        """Calculate entry, stop loss, and take profits for short trades using stable SMC analysis."""
        # Calculate ATR with robust fallback for insufficient data
        atr = self.calculate_atr(candlesticks)
        
        # Apply ATR floor to handle low volatility and insufficient data cases
        min_atr = current_price * 0.001  # 0.1% of price minimum (removed problematic absolute floor)
        if atr <= 0:
            # Calculate median true range from available data as fallback
            if len(candlesticks) >= 2:
                true_ranges = []
                for i in range(1, min(len(candlesticks), 20)):
                    current = candlesticks[i]
                    prev = candlesticks[i - 1]
                    tr = max(
                        current["high"] - current["low"],
                        abs(current["high"] - prev["close"]),
                        abs(current["low"] - prev["close"])
                    )
                    true_ranges.append(tr)
                atr = sum(true_ranges) / len(true_ranges) if true_ranges else min_atr
            else:
                atr = min_atr
        
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
                        entry_price = ob.price_low  # Use bottom of OB as entry
                        break
        
        # Fallback if no suitable order block found
        if entry_price is None:
            # Use most recent swing high - small buffer as entry (structural approach)
            if swing_highs:
                recent_swing_high = swing_highs[-1]["high"]
                entry_buffer = max(atr * 0.2, recent_swing_high * 0.003)  # 0.3% minimum
                entry_price = recent_swing_high - entry_buffer
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
            
        # Final safety check: ensure stop loss is above entry
        stop_loss = max(stop_loss, entry_price * 1.01)  # Above entry price

        # Take profits with guaranteed valid levels
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
        
        # Select TP1
        if tp1_candidates:
            tp1 = max(tp1_candidates)
        else:
            tp1 = entry_price - max(atr * 1.0, current_price * 0.01)
        
        # Ensure TP1 is positive and below entry
        tp1 = max(tp1, current_price * 0.01)  # At least 1% of current price
        tp1 = min(tp1, entry_price * 0.99)  # Below entry price
        
        take_profits.append(tp1)
        
        # TP2: Extended target, ensuring it's below TP1
        tp2_base = entry_price - max(atr * 2.0, current_price * 0.02)
        extended_lows = [s["low"] for s in swing_lows[-10:] if s["low"] < tp1 and s["low"] > 0]
        if extended_lows:
            tp2 = max(extended_lows)
            tp2 = min(tp2, tp1 * 0.99)  # Ensure TP2 < TP1
        else:
            tp2 = min(tp2_base, tp1 * 0.99)
        
        # Ensure TP2 is positive
        tp2 = max(tp2, current_price * 0.005)  # At least 0.5% of current price
        
        take_profits.append(tp2)
        
        # TP3: Full extension, ensuring it's below TP2
        tp3 = min(
            entry_price - max(atr * 3.0, current_price * 0.03),
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
            if rsi < 35:  # RSI exhaustion for longs
                direction = "long"
                confidence = min(bullish_signals / 5.0, 1.0)
                
                # Add sweep bonus if confirmed sweeps are present
                sweep_bonus = 0.2 if confirmed_buy_sweeps else 0.0
                confidence += sweep_bonus
                confidence = min(confidence, 1.0)  # Cap at 1.0
                
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
            if rsi > 65:  # RSI exhaustion for shorts
                direction = "short"
                confidence = min(bearish_signals / 5.0, 1.0)
                
                # Add sweep bonus if confirmed sweeps are present
                sweep_bonus = 0.2 if confirmed_sell_sweeps else 0.0
                confidence += sweep_bonus
                confidence = min(confidence, 1.0)  # Cap at 1.0
                
                if confidence >= 0.6:  # Lowered confidence threshold for counter-trend
                    entry_price, stop_loss, take_profits = self._calculate_short_trade_levels(
                        current_price, order_blocks, candlesticks
                    )
                else:
                    direction = None
                    confidence = 0.0
        
        # If neither condition is met, do not generate signal
        return direction, confidence, entry_price, stop_loss, take_profits

    def _determine_trade_direction_and_levels(
        self, bullish_signals, bearish_signals, current_price, order_blocks, candlesticks
    ):
        """Legacy method - kept for backward compatibility."""
        direction = None
        confidence = 0.0
        entry_price = current_price
        stop_loss = 0.0
        take_profits = []

        if bullish_signals > bearish_signals and bullish_signals >= 3:
            direction = "long"
            confidence = min(bullish_signals / 5.0, 1.0)
            entry_price, stop_loss, take_profits = self._calculate_long_trade_levels(
                current_price, order_blocks, candlesticks
            )

        elif bearish_signals > bullish_signals and bearish_signals >= 3:
            direction = "short"
            confidence = min(bearish_signals / 5.0, 1.0)
            entry_price, stop_loss, take_profits = self._calculate_short_trade_levels(
                current_price, order_blocks, candlesticks
            )

        return direction, confidence, entry_price, stop_loss, take_profits

    def _calculate_trade_metrics_enhanced(
        self, entry_price, stop_loss, take_profits, confidence, liquidity_sweeps, order_blocks, fvgs
    ):
        """Calculate risk-reward ratio and signal strength with liquidity sweep and confluence bonuses."""
        # Calculate risk-reward ratio
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profits[0] - entry_price) if take_profits else risk
        rr_ratio = reward / risk if risk > 0 else 1.0

        # Start with base confidence
        effective_confidence = confidence
        
        # Liquidity sweep bonus: +0.2 if confirmed sweep present
        confirmed_sweeps = []
        for sweep_type in ["buy_side", "sell_side"]:
            confirmed_sweeps.extend([s for s in liquidity_sweeps.get(sweep_type, []) if s.get("confirmed", False)])
        
        if confirmed_sweeps:
            effective_confidence += 0.2
            
        # Structural confluence bonus: +0.1 for order block alignment, +0.1 for FVG alignment
        if order_blocks:
            effective_confidence += 0.1
        if fvgs:
            effective_confidence += 0.1
            
        # Cap at 1.0
        effective_confidence = min(effective_confidence, 1.0)

        # Determine signal strength based on enhanced confidence
        if effective_confidence >= 0.9:
            signal_strength = SignalStrength.VERY_STRONG
        elif effective_confidence >= 0.8:
            signal_strength = SignalStrength.STRONG
        elif effective_confidence >= 0.7:
            signal_strength = SignalStrength.MODERATE
        else:
            signal_strength = SignalStrength.WEAK

        return rr_ratio, signal_strength

    def _calculate_trade_metrics(
        self, entry_price, stop_loss, take_profits, confidence
    ):
        """Legacy method - calculate risk-reward ratio and signal strength."""
        # Calculate risk-reward ratio
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profits[0] - entry_price) if take_profits else risk
        rr_ratio = reward / risk if risk > 0 else 1.0

        # Determine signal strength
        if confidence >= 0.9:
            signal_strength = SignalStrength.VERY_STRONG
        elif confidence >= 0.8:
            signal_strength = SignalStrength.STRONG
        elif confidence >= 0.7:
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
            
            d1_trend = self._calculate_trend(self._find_swing_highs(d1_data) + self._find_swing_lows(d1_data), "price")
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
                    last_swing_low = min([sw["price"] for sw in m15_swing_lows[-3:]])
                    sl = last_swing_low * 0.998
                else:
                    sl = current_price * 0.995
            else:
                entry_price = current_price
                if m15_swing_highs:
                    last_swing_high = max([sw["price"] for sw in m15_swing_highs[-3:]])
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

    def generate_trade_signal(self, symbol: str, return_diagnostics: bool = False) -> Union[Optional[SMCSignal], Tuple[Optional[SMCSignal], Dict]]:
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
                    
                    # Check RSI for counter-trend requirements
                    if h1_structure in bullish_structures and h4_structure in bearish_structures:
                        if not (rsi and rsi < 35):
                            rejection_reasons.append(f"RSI not in extreme oversold zone for reversal long (RSI: {f'{rsi:.1f}' if rsi is not None else 'N/A'})")
                        if not confirmed_buy_sweeps:
                            rejection_reasons.append("No confirmed buy-side liquidity sweeps for reversal confirmation")
                    elif h1_structure in bearish_structures and h4_structure in bullish_structures:
                        if not (rsi and rsi > 65):
                            rejection_reasons.append(f"RSI not in extreme overbought zone for reversal short (RSI: {f'{rsi:.1f}' if rsi is not None else 'N/A'})")
                        if not confirmed_sell_sweeps:
                            rejection_reasons.append("No confirmed sell-side liquidity sweeps for reversal confirmation")
                
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
                m15_alignment_score = 0.5  # Default neutral if no 15m data
                if execution_signal_15m and execution_signal_15m.get("alignment_score") is not None:
                    m15_alignment_score = execution_signal_15m["alignment_score"]
                    analysis_details["phase3_m15_alignment"] = m15_alignment_score
                
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
                
                # Phase 4: Calculate scaled entries
                scaled_entries_list = None
                from config import TradingConfig
                if TradingConfig.USE_SCALED_ENTRIES:
                    # Extract 15m swing levels for stop-loss calculations
                    m15_swing_levels = {}
                    if m15_data and len(m15_data) >= 10:
                        swing_highs_15m = self._find_swing_highs(m15_data, lookback=2)
                        swing_lows_15m = self._find_swing_lows(m15_data, lookback=2)
                        if swing_highs_15m:
                            m15_swing_levels["last_swing_high"] = swing_highs_15m[-1]["high"]
                        if swing_lows_15m:
                            m15_swing_levels["last_swing_low"] = swing_lows_15m[-1]["low"]
                    
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
        lookback: int = SMCConfig.DEFAULT_LOOKBACK_PERIOD,
    ) -> List[Dict]:
        """Find swing highs in price data"""
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
        lookback: int = SMCConfig.DEFAULT_LOOKBACK_PERIOD,
    ) -> List[Dict]:
        """Find swing lows in price data"""
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

        if direction == "bullish":
            # Check for strong upward displacement
            max_high = max(c["high"] for c in displacement_candles)
            displacement_ratio = (max_high - ob_candle["high"]) / (
                ob_candle["high"] - ob_candle["low"]
            )
            return displacement_ratio >= SMCConfig.OB_IMPULSIVE_MOVE_THRESHOLD
        else:
            # Check for strong downward displacement
            min_low = min(c["low"] for c in displacement_candles)
            displacement_ratio = (ob_candle["low"] - min_low) / (
                ob_candle["high"] - ob_candle["low"]
            )
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
                and fvg.atr_size >= SMCConfig.FVG_ATR_MULTIPLIER
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

    def _calculate_long_prices(
        self, relevant_obs, relevant_fvgs, current_price, atr, order_blocks
    ):
        """Calculate entry, stop loss, and take profit prices for long positions using SMC discount zone logic."""
        entry_price = current_price

        # SMC Long Logic: Look for bullish OBs BELOW current price (discount zone)
        if relevant_obs:
            # Filter for bullish OBs below current price only (discount zone)
            discount_obs = [
                ob for ob in relevant_obs 
                if ob.direction == "bullish" and ob.price_high < current_price
            ]
            if discount_obs:
                # Use the highest bullish OB below current price (closest to current price)
                entry_price = max(ob.price_high for ob in discount_obs)
            else:
                # Fallback: slight discount from current price
                entry_price = current_price * 0.998  # 0.2% below
        elif relevant_fvgs:
            # Filter for bullish FVGs below current price (discount zone)
            discount_fvgs = [
                fvg for fvg in relevant_fvgs 
                if fvg.direction == "bullish" and fvg.gap_high < current_price
            ]
            if discount_fvgs:
                # Use the highest FVG below current price
                entry_price = max(fvg.gap_high for fvg in discount_fvgs)
            else:
                # Fallback: slight discount from current price
                entry_price = current_price * 0.998  # 0.2% below

        # ATR-based stop loss
        atr_stop = current_price - (atr * 1.5)
        ob_stop = current_price * 0.98

        for ob in order_blocks:
            if ob.direction == "bullish" and ob.price_low < current_price:
                ob_stop = max(ob_stop, ob.price_low * 0.995)

        stop_loss = max(atr_stop, ob_stop)
        take_profits = [
            current_price + (atr * 1.0),
            current_price + (atr * 2.0),
            current_price + (atr * 3.0),
        ]

        return entry_price, stop_loss, take_profits

    def _calculate_short_prices(
        self, relevant_obs, relevant_fvgs, current_price, atr, order_blocks
    ):
        """Calculate entry, stop loss, and take profit prices for short positions using SMC premium zone logic."""
        entry_price = current_price

        # SMC Short Logic: Look for bearish OBs ABOVE current price (premium zone)
        if relevant_obs:
            # Filter for bearish OBs above current price only (premium zone)
            premium_obs = [
                ob for ob in relevant_obs 
                if ob.direction == "bearish" and ob.price_low > current_price
            ]
            if premium_obs:
                # Use the lowest bearish OB above current price (closest to current price)
                entry_price = min(ob.price_low for ob in premium_obs)
            else:
                # Fallback: slight premium from current price
                entry_price = current_price * 1.002  # 0.2% above
        elif relevant_fvgs:
            # Filter for bearish FVGs above current price (premium zone)
            premium_fvgs = [
                fvg for fvg in relevant_fvgs 
                if fvg.direction == "bearish" and fvg.gap_low > current_price
            ]
            if premium_fvgs:
                # Use the lowest FVG above current price
                entry_price = min(fvg.gap_low for fvg in premium_fvgs)
            else:
                # Fallback: slight premium from current price
                entry_price = current_price * 1.002  # 0.2% above

        # ATR-based stop loss
        atr_stop = current_price + (atr * 1.5)
        ob_stop = current_price * 1.02

        for ob in order_blocks:
            if ob.direction == "bearish" and ob.price_high > current_price:
                ob_stop = min(ob_stop, ob.price_high * 1.005)

        stop_loss = min(atr_stop, ob_stop)
        take_profits = [
            current_price - (atr * 1.0),
            current_price - (atr * 2.0),
            current_price - (atr * 3.0),
        ]

        return entry_price, stop_loss, take_profits

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
                alignment_score = 0.1  # Conflict - bearish structure against bullish bias
                
        elif htf_bias == "bearish":
            if m15_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                alignment_score += 0.5  # Strong bearish alignment
            elif m15_structure == MarketStructure.CONSOLIDATION:
                alignment_score += 0.3  # Neutral but acceptable
            elif m15_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                alignment_score = 0.1  # Conflict - bullish structure against bearish bias
        
        # Bonus: Check if intermediate structure also aligns
        if intermediate_structure_direction and intermediate_structure_direction.startswith(htf_bias):
            alignment_score += 0.3
        
        # Bonus: Check if price is near a POI aligned with HTF bias
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

    def _calculate_scaled_entries(
        self,
        current_price: float,
        direction: str,
        order_blocks: List[OrderBlock],
        fvgs: List[FairValueGap],
        base_stop_loss: float,
        base_take_profits: List[Tuple[float, float]]
    ) -> List[ScaledEntry]:
        """
        Phase 4: Calculate 3-level scaled entry strategy
        
        Entry Allocation:
        - 50% at market (immediate execution)
        - 25% at first FVG/OB depth level (0.4% better price)
        - 25% at second FVG/OB depth level (1.0% better price)
        
        Args:
            current_price: Current market price
            direction: Trade direction ('long' or 'short')
            order_blocks: List of detected order blocks
            fvgs: List of fair value gaps
            base_stop_loss: Base stop-loss price
            base_take_profits: List of (price, allocation) tuples for take profits
        
        Returns:
            List of ScaledEntry objects
        """
        from config import TradingConfig
        
        if not TradingConfig.USE_SCALED_ENTRIES:
            # If scaled entries disabled, return single market entry
            return [ScaledEntry(
                entry_price=current_price,
                allocation_percent=100.0,
                order_type='market',
                stop_loss=base_stop_loss,
                take_profits=base_take_profits,
                status='pending'
            )]
        
        scaled_entries = []
        allocations = TradingConfig.SCALED_ENTRY_ALLOCATIONS
        
        # Entry 1: 50% at market (immediate execution)
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
        
        # Entry 2: 25% at first depth level (0.4% better price)
        depth1 = TradingConfig.SCALED_ENTRY_DEPTH_1
        if direction == "long":
            # For long, better price means lower
            entry2_price = current_price * (1 - depth1)
        else:  # short
            # For short, better price means higher
            entry2_price = current_price * (1 + depth1)
        
        # Try to align with nearest OB/FVG
        entry2_price = self._align_entry_with_poi(
            entry2_price, direction, order_blocks, fvgs, max_distance_pct=0.5
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
        
        # Entry 3: 25% at second depth level (1.0% better price)
        depth2 = TradingConfig.SCALED_ENTRY_DEPTH_2
        if direction == "long":
            entry3_price = current_price * (1 - depth2)
        else:  # short
            entry3_price = current_price * (1 + depth2)
        
        # Try to align with deeper OB/FVG
        entry3_price = self._align_entry_with_poi(
            entry3_price, direction, order_blocks, fvgs, max_distance_pct=1.0
        )
        
        entry3 = ScaledEntry(
            entry_price=entry3_price,
            allocation_percent=allocations[2],
            order_type='limit',
            stop_loss=base_stop_loss,
            take_profits=base_take_profits,
            status='pending'
        )
        scaled_entries.append(entry3)
        
        logging.info(f"Phase 4: Calculated scaled entries - Market: ${entry1_price:.2f} ({allocations[0]}%), "
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

    def generate_enhanced_signal(
        self,
        symbol: str,
        h1_candlesticks: List[Dict],
        h4_candlesticks: Optional[List[Dict]] = None,
        d1_candlesticks: Optional[List[Dict]] = None,
    ) -> Optional[SMCSignal]:
        """Enhanced SMC signal generation with multi-timeframe analysis and improved filtering"""
        try:
            if len(h1_candlesticks) < SMCConfig.MIN_CANDLESTICKS_FOR_STRUCTURE:
                return None

            # Multi-timeframe structure analysis
            h1_structure = self.detect_market_structure(h1_candlesticks)
            h4_structure = (
                self.detect_market_structure(h4_candlesticks)
                if h4_candlesticks
                else h1_structure
            )
            d1_structure = (
                self.detect_market_structure(d1_candlesticks)
                if d1_candlesticks
                else None
            )

            if not h1_structure:
                return None

            # Check multi-timeframe alignment if required
            alignment = None
            if SMCConfig.TIMEFRAME_ALIGNMENT_REQUIRED:
                alignment = self.check_multi_timeframe_alignment(
                    h1_structure, h4_structure, d1_structure
                )
                if not alignment["aligned"]:
                    return None

            # Enhanced SMC elements analysis
            order_blocks = self.find_order_blocks(h1_candlesticks)
            fvgs = self.find_fair_value_gaps(h1_candlesticks)
            liquidity_pools = self.find_liquidity_pools(h1_candlesticks)
            liquidity_sweeps = self.detect_liquidity_sweeps(h1_candlesticks)

            # Calculate ATR for dynamic thresholds
            atr = self.calculate_atr(h1_candlesticks)
            current_price = h1_candlesticks[-1]["close"]

            # Enhanced confluence analysis
            confluence_analysis = self._analyze_enhanced_confluence(
                h1_candlesticks,
                order_blocks,
                fvgs,
                liquidity_sweeps,
                alignment,
                h1_structure,
            )

            confluence_score = confluence_analysis["confluence_score"]
            reasoning = confluence_analysis["reasoning"]
            relevant_obs = confluence_analysis["relevant_obs"]
            relevant_fvgs = confluence_analysis["relevant_fvgs"]

            # Determine signal direction
            direction, additional_score = self._determine_signal_direction(
                alignment, h1_structure, relevant_obs, relevant_fvgs
            )
            confluence_score += additional_score

            # Enhanced confidence threshold
            min_confidence = 0.7 if SMCConfig.TIMEFRAME_ALIGNMENT_REQUIRED else 0.6

            if direction and confluence_score >= min_confidence:
                # Calculate entry, stop loss, and take profits
                if direction == "long":
                    entry_price, stop_loss, take_profits = self._calculate_long_prices(
                        relevant_obs, relevant_fvgs, current_price, atr, order_blocks
                    )
                else:  # direction == 'short'
                    entry_price, stop_loss, take_profits = self._calculate_short_prices(
                        relevant_obs, relevant_fvgs, current_price, atr, order_blocks
                    )

                # Calculate risk/reward and signal strength
                risk = abs(entry_price - stop_loss)
                reward = abs(take_profits[0] - entry_price) if take_profits else risk
                rr_ratio = reward / risk if risk > 0 else 1.0

                signal_strength, final_confidence = (
                    self._calculate_signal_strength_and_confidence(confluence_score)
                )

                return SMCSignal(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit_levels=take_profits,
                    confidence=final_confidence,
                    reasoning=reasoning,
                    signal_strength=signal_strength,
                    risk_reward_ratio=rr_ratio,
                    timestamp=datetime.now(timezone.utc),
                    current_market_price=current_price,
                )

            return None

        except Exception as e:
            logging.error(f"Error generating enhanced SMC signal for {symbol}: {e}")
            return None
