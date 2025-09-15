"""
Smart Money Concepts (SMC) Analysis Engine
Analyzes market structure and provides trade suggestions based on institutional trading patterns
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

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
    sweep_timestamp: datetime = None


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


class SMCAnalyzer:
    """Smart Money Concepts analyzer for detecting institutional trading patterns"""

    def __init__(self):
        self.timeframes = ["1h", "4h", "1d"]  # Multiple timeframe analysis

    @with_circuit_breaker(
        "binance_klines_api", failure_threshold=2, recovery_timeout=60
    )
    def get_candlestick_data(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> List[Dict]:
        """Get candlestick data with cache-first approach and circuit breaker protection"""
        from config import CacheConfig

        from .models import KlinesCache

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

        # Step 2: Check for data gaps and determine what to fetch
        try:
            gap_info = KlinesCache.get_data_gaps(symbol, timeframe, limit)
            if not gap_info["needs_fetch"]:
                logging.debug(
                    f"CACHE SUFFICIENT: Using existing cached data for {symbol} {timeframe}"
                )
                return KlinesCache.get_cached_data(symbol, timeframe, limit)

            fetch_limit = gap_info["fetch_count"]
            logging.info(
                f"CACHE MISS/PARTIAL: Fetching {fetch_limit} candles for {symbol} {timeframe}"
            )

        except Exception as e:
            logging.warning(f"Gap analysis failed for {symbol} {timeframe}: {e}")
            fetch_limit = limit

        # Step 3: Fetch from Binance API with circuit breaker protection
        tf_map = {"1h": "1h", "4h": "4h", "1d": "1d"}
        interval = tf_map.get(timeframe, "1h")

        url = f"https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": fetch_limit}

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        klines = response.json()

        # Convert to OHLCV format
        candlesticks = []
        for kline in klines:
            candlestick = {
                "timestamp": datetime.fromtimestamp(kline[0] / 1000),
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
            }
            candlesticks.append(candlestick)

        # Step 4: Cache the fetched data with appropriate TTL
        try:
            ttl_config = {
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
                # Remove duplicates based on timestamp
                seen_timestamps = set()
                unique_data = []
                for candle in combined_data:
                    timestamp_key = (
                        candle["timestamp"].isoformat()
                        if isinstance(candle["timestamp"], datetime)
                        else str(candle["timestamp"])
                    )
                    if timestamp_key not in seen_timestamps:
                        seen_timestamps.add(timestamp_key)
                        unique_data.append(candle)

                # Sort by timestamp and limit
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
            ("1h", SMCConfig.TIMEFRAME_1H_LIMIT), 
            ("4h", SMCConfig.TIMEFRAME_4H_LIMIT), 
            ("1d", SMCConfig.TIMEFRAME_1D_LIMIT)
        ]

        logging.info(
            f"Fetching batch candlestick data for {symbol} - 1h:{SMCConfig.TIMEFRAME_1H_LIMIT}, 4h:{SMCConfig.TIMEFRAME_4H_LIMIT}, 1d:{SMCConfig.TIMEFRAME_1D_LIMIT} candles"
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
                all_symbol_data[symbol] = {"1h": [], "4h": [], "1d": []}

            except Exception as e:
                logging.error(f"Error in bulk fetch for {symbol}: {e}")
                all_symbol_data[symbol] = {"1h": [], "4h": [], "1d": []}

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

        # Calculate ATR for gap size filtering
        atr = self.calculate_atr(candlesticks)
        min_gap_size = atr * SMCConfig.FVG_ATR_MULTIPLIER

        for i in range(1, len(candlesticks) - 1):
            prev_candle = candlesticks[i - 1]
            current = candlesticks[i]
            next_candle = candlesticks[i + 1]

            # Bullish FVG: Gap between previous low and next high
            if (
                prev_candle["low"] > next_candle["high"]
                and current["close"] > current["open"]
            ):

                gap_size = prev_candle["low"] - next_candle["high"]

                # Apply ATR filter
                if gap_size >= min_gap_size:
                    fvg = FairValueGap(
                        gap_high=prev_candle["low"],
                        gap_low=next_candle["high"],
                        timestamp=current["timestamp"],
                        direction="bullish",
                        atr_size=gap_size / atr,
                        age_candles=0,
                    )
                    fvgs.append(fvg)

            # Bearish FVG: Gap between previous high and next low
            elif (
                prev_candle["high"] < next_candle["low"]
                and current["close"] < current["open"]
            ):

                gap_size = next_candle["low"] - prev_candle["high"]

                # Apply ATR filter
                if gap_size >= min_gap_size:
                    fvg = FairValueGap(
                        gap_high=next_candle["low"],
                        gap_low=prev_candle["high"],
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

    def _calculate_long_trade_levels(self, current_price, order_blocks):
        """Calculate entry, stop loss, and take profits for long trades."""
        # For LONG signals, entry should be ABOVE current price (stop buy)
        # Use bullish order block HIGH price as entry point
        entry_price = None
        
        # Find valid bullish order block above current price for entry
        for ob in order_blocks:
            if (
                ob.direction == "bullish" 
                and ob.price_high > current_price
                and ob.price_high <= current_price * 1.05  # Within 5% above
            ):
                if entry_price is None or ob.price_high < entry_price:
                    entry_price = ob.price_high
        
        # If no valid order block found, use current price + small buffer for stop buy
        if entry_price is None:
            entry_price = current_price * 1.002  # 0.2% above current price

        # Stop loss below nearest support/order block
        nearest_support = current_price * 0.97  # Default 3% below
        for ob in order_blocks:
            if ob.direction == "bullish" and ob.price_low < current_price:
                nearest_support = max(nearest_support, ob.price_low * 0.995)

        stop_loss = nearest_support

        # Take profits based on entry price
        take_profits = [
            entry_price * 1.02,  # 2% profit from entry
            entry_price * 1.035,  # 3.5% profit from entry
            entry_price * 1.05,  # 5% profit from entry
        ]

        return entry_price, stop_loss, take_profits

    def _calculate_short_trade_levels(self, current_price, order_blocks):
        """Calculate entry, stop loss, and take profits for short trades."""
        # For SHORT signals, entry should be BELOW current price (stop sell)
        # Use bearish order block LOW price as entry point
        entry_price = None
        
        # Find valid bearish order block below current price for entry
        for ob in order_blocks:
            if (
                ob.direction == "bearish" 
                and ob.price_low < current_price
                and ob.price_low >= current_price * 0.95  # Within 5% below
            ):
                if entry_price is None or ob.price_low > entry_price:
                    entry_price = ob.price_low
        
        # If no valid order block found, use current price - small buffer for stop sell
        if entry_price is None:
            entry_price = current_price * 0.998  # 0.2% below current price

        # Stop loss above nearest resistance/order block
        nearest_resistance = current_price * 1.03  # Default 3% above
        for ob in order_blocks:
            if ob.direction == "bearish" and ob.price_high > current_price:
                nearest_resistance = min(nearest_resistance, ob.price_high * 1.005)

        stop_loss = nearest_resistance

        # Take profits based on entry price
        take_profits = [
            entry_price * 0.98,  # 2% profit from entry
            entry_price * 0.965,  # 3.5% profit from entry
            entry_price * 0.95,  # 5% profit from entry
        ]

        return entry_price, stop_loss, take_profits

    def _determine_trade_direction_and_levels(
        self, bullish_signals, bearish_signals, current_price, order_blocks
    ):
        """Determine trade direction and calculate price levels."""
        direction = None
        confidence = 0.0
        entry_price = current_price
        stop_loss = 0.0
        take_profits = []

        if bullish_signals > bearish_signals and bullish_signals >= 3:
            direction = "long"
            confidence = min(bullish_signals / 5.0, 1.0)
            entry_price, stop_loss, take_profits = self._calculate_long_trade_levels(
                current_price, order_blocks
            )

        elif bearish_signals > bullish_signals and bearish_signals >= 3:
            direction = "short"
            confidence = min(bearish_signals / 5.0, 1.0)
            entry_price, stop_loss, take_profits = self._calculate_short_trade_levels(
                current_price, order_blocks
            )

        return direction, confidence, entry_price, stop_loss, take_profits

    def _calculate_trade_metrics(
        self, entry_price, stop_loss, take_profits, confidence
    ):
        """Calculate risk-reward ratio and signal strength."""
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

    def generate_trade_signal(self, symbol: str) -> Optional[SMCSignal]:
        """Generate comprehensive trade signal based on SMC analysis"""
        try:
            # Get multi-timeframe data in batch to reduce API calls
            timeframe_data = self.get_multi_timeframe_data(symbol)

            h1_data = timeframe_data.get("1h", [])
            h4_data = timeframe_data.get("4h", [])
            d1_data = timeframe_data.get("1d", [])

            if not h1_data or not h4_data:
                logging.warning(
                    f"Insufficient data for {symbol}: h1={len(h1_data)}, h4={len(h4_data)}"
                )
                return None

            current_price = h1_data[-1]["close"]

            # Analyze market structure across timeframes
            h1_structure = self.detect_market_structure(h1_data)
            h4_structure = self.detect_market_structure(h4_data)

            # Find key SMC elements
            order_blocks = self.find_order_blocks(h1_data)
            fvgs = self.find_fair_value_gaps(h1_data)
            liquidity_pools = self.find_liquidity_pools(h4_data)

            # Calculate technical indicators
            rsi = self.calculate_rsi(h1_data)
            mas = self.calculate_moving_averages(h1_data)

            # Generate signal analysis
            reasoning = []

            # Analyze bullish and bearish signals
            bullish_signals = self._analyze_bullish_signals(
                h1_structure,
                h4_structure,
                order_blocks,
                fvgs,
                current_price,
                rsi,
                mas,
                reasoning,
            )

            bearish_signals = self._analyze_bearish_signals(
                h1_structure,
                h4_structure,
                order_blocks,
                fvgs,
                current_price,
                rsi,
                mas,
                reasoning,
            )

            # Determine direction and calculate trade levels
            direction, confidence, entry_price, stop_loss, take_profits = (
                self._determine_trade_direction_and_levels(
                    bullish_signals, bearish_signals, current_price, order_blocks
                )
            )

            # Only generate signal if confidence is above threshold
            if direction and confidence >= 0.6:
                rr_ratio, signal_strength = self._calculate_trade_metrics(
                    entry_price, stop_loss, take_profits, confidence
                )

                return SMCSignal(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit_levels=take_profits,
                    confidence=confidence,
                    reasoning=reasoning,
                    signal_strength=signal_strength,
                    risk_reward_ratio=rr_ratio,
                    timestamp=datetime.now(),
                )

            return None

        except Exception as e:
            logging.error(f"Error generating SMC signal for {symbol}: {e}")
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
        for i, candle in enumerate(candlesticks[-10:], start=len(candlesticks) - 10):
            body_size = abs(candle["close"] - candle["open"])
            lower_wick = min(candle["open"], candle["close"]) - candle["low"]

            # Check if wick is significant
            if lower_wick >= body_size * SMCConfig.LIQUIDITY_SWEEP_WICK_RATIO:
                # Check if wick swept below recent swing low
                for swing in swing_lows[-5:]:
                    if (
                        swing["index"] < i
                        and candle["low"] < swing["low"]
                        and candle["close"] > swing["low"]
                    ):

                        # Check for structural confirmation
                        confirmation = self._check_sweep_confirmation(
                            candlesticks, i, "buy_side"
                        )

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
        for i, candle in enumerate(candlesticks[-10:], start=len(candlesticks) - 10):
            body_size = abs(candle["close"] - candle["open"])
            upper_wick = candle["high"] - max(candle["open"], candle["close"])

            # Check if wick is significant
            if upper_wick >= body_size * SMCConfig.LIQUIDITY_SWEEP_WICK_RATIO:
                # Check if wick swept above recent swing high
                for swing in swing_highs[-5:]:
                    if (
                        swing["index"] < i
                        and candle["high"] > swing["high"]
                        and candle["close"] < swing["high"]
                    ):

                        # Check for structural confirmation
                        confirmation = self._check_sweep_confirmation(
                            candlesticks, i, "sell_side"
                        )

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
        d1_structure: MarketStructure = None,
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

    def _determine_alignment_direction(self, categories: Dict[str, bool]) -> str:
        """Determine overall direction from structure alignment."""
        if categories["h1_bullish"] and categories["h4_bullish"]:
            return "long"
        elif categories["h1_bearish"] and categories["h4_bearish"]:
            return "short"
        return None

    def _create_alignment_result(
        self, aligned: bool, score: float, details: list, direction: str
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
        d1_structure: MarketStructure = None,
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
            return self._create_alignment_result(
                False, h1_h4_result["score"], h1_h4_result["details"], None
            )

        # Analyze daily bias confirmation
        daily_result = self._analyze_daily_bias_confirmation(
            d1_structure, categories, h1_h4_result["score"]
        )
        if daily_result["filtered"]:
            return self._create_alignment_result(
                False,
                daily_result["score"],
                h1_h4_result["details"] + daily_result["details"],
                None,
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
        """Calculate entry, stop loss, and take profit prices for long positions."""
        entry_price = current_price

        if relevant_obs:
            entry_price = min(
                ob.price_high for ob in relevant_obs if ob.direction == "bullish"
            )
        elif relevant_fvgs:
            entry_price = max(
                fvg.gap_low for fvg in relevant_fvgs if fvg.direction == "bullish"
            )

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
        """Calculate entry, stop loss, and take profit prices for short positions."""
        entry_price = current_price

        if relevant_obs:
            entry_price = max(
                ob.price_low for ob in relevant_obs if ob.direction == "bearish"
            )
        elif relevant_fvgs:
            entry_price = min(
                fvg.gap_high for fvg in relevant_fvgs if fvg.direction == "bearish"
            )

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

    def _calculate_signal_strength_and_confidence(self, confluence_score):
        """Calculate signal strength and final confidence."""
        if confluence_score >= 4.0:
            signal_strength = SignalStrength.VERY_STRONG
        elif confluence_score >= 3.0:
            signal_strength = SignalStrength.STRONG
        elif confluence_score >= 2.0:
            signal_strength = SignalStrength.MODERATE
        else:
            signal_strength = SignalStrength.WEAK

        final_confidence = min(confluence_score / 5.0, 1.0)

        return signal_strength, final_confidence

    def generate_enhanced_signal(
        self,
        symbol: str,
        h1_candlesticks: List[Dict],
        h4_candlesticks: List[Dict] = None,
        d1_candlesticks: List[Dict] = None,
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
                    timestamp=datetime.now(),
                )

            return None

        except Exception as e:
            logging.error(f"Error generating enhanced SMC signal for {symbol}: {e}")
            return None
