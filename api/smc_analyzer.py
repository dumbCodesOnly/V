"""
Smart Money Concepts (SMC) Analysis Engine
Analyzes market structure and provides trade suggestions based on institutional trading patterns
"""

import logging
import json
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import requests
from dataclasses import dataclass
from enum import Enum

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

@dataclass
class FairValueGap:
    gap_high: float
    gap_low: float
    timestamp: datetime
    direction: str  # 'bullish' or 'bearish'
    filled: bool = False

@dataclass
class LiquidityPool:
    price: float
    type: str  # 'buy_side' or 'sell_side'
    strength: float
    swept: bool = False

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
        self.timeframes = ['1h', '4h', '1d']  # Multiple timeframe analysis
        
    def get_candlestick_data(self, symbol: str, timeframe: str = '1h', limit: int = 100) -> List[Dict]:
        """Get candlestick data from Binance (free API for analysis)"""
        try:
            # Convert timeframe to Binance format
            tf_map = {'1h': '1h', '4h': '4h', '1d': '1d'}
            interval = tf_map.get(timeframe, '1h')
            
            url = f"https://api.binance.com/api/v3/klines"
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            klines = response.json()
            
            # Convert to OHLCV format
            candlesticks = []
            for kline in klines:
                candlestick = {
                    'timestamp': datetime.fromtimestamp(kline[0] / 1000),
                    'open': float(kline[1]),
                    'high': float(kline[2]),
                    'low': float(kline[3]),
                    'close': float(kline[4]),
                    'volume': float(kline[5])
                }
                candlesticks.append(candlestick)
            
            return candlesticks
            
        except Exception as e:
            logging.error(f"Failed to get candlestick data for {symbol}: {e}")
            return []
    
    def detect_market_structure(self, candlesticks: List[Dict]) -> MarketStructure:
        """Detect current market structure using SMC principles"""
        if len(candlesticks) < 20:
            return MarketStructure.CONSOLIDATION
        
        # Get recent swing highs and lows
        swing_highs = self._find_swing_highs(candlesticks)
        swing_lows = self._find_swing_lows(candlesticks)
        
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return MarketStructure.CONSOLIDATION
        
        # Analyze the pattern of highs and lows
        recent_highs = swing_highs[-3:]
        recent_lows = swing_lows[-3:]
        
        # Check for Break of Structure (BOS)
        if len(recent_highs) >= 2:
            if recent_highs[-1]['high'] > recent_highs[-2]['high']:
                # Recent high broke previous high
                if len(recent_lows) >= 2 and recent_lows[-1]['low'] > recent_lows[-2]['low']:
                    return MarketStructure.BULLISH_BOS
        
        if len(recent_lows) >= 2:
            if recent_lows[-1]['low'] < recent_lows[-2]['low']:
                # Recent low broke previous low
                if len(recent_highs) >= 2 and recent_highs[-1]['high'] < recent_highs[-2]['high']:
                    return MarketStructure.BEARISH_BOS
        
        # Check for Change of Character (CHoCH)
        if len(recent_highs) >= 3 and len(recent_lows) >= 3:
            # Look for trend reversal patterns
            high_trend = self._calculate_trend(recent_highs, 'high')
            low_trend = self._calculate_trend(recent_lows, 'low')
            
            if high_trend == 'down' and low_trend == 'up':
                return MarketStructure.BULLISH_CHoCH
            elif high_trend == 'up' and low_trend == 'down':
                return MarketStructure.BEARISH_CHoCH
        
        return MarketStructure.CONSOLIDATION
    
    def find_order_blocks(self, candlesticks: List[Dict]) -> List[OrderBlock]:
        """Identify order blocks - areas where institutional orders are likely placed"""
        order_blocks = []
        
        if len(candlesticks) < 10:
            return order_blocks
        
        for i in range(3, len(candlesticks) - 3):
            current = candlesticks[i]
            prev = candlesticks[i-1]
            next_candle = candlesticks[i+1]
            
            # Look for strong bullish candles followed by continuation
            if (current['close'] > current['open'] and 
                current['high'] - current['low'] > (current['open'] - prev['close']) * 2):
                
                # Check if next few candles continue the move
                continuation_strength = 0
                for j in range(i+1, min(i+4, len(candlesticks))):
                    if candlesticks[j]['close'] > current['high']:
                        continuation_strength += 1
                
                if continuation_strength >= 2:
                    order_block = OrderBlock(
                        price_high=current['high'],
                        price_low=current['low'],
                        timestamp=current['timestamp'],
                        direction='bullish',
                        strength=continuation_strength / 3.0
                    )
                    order_blocks.append(order_block)
            
            # Look for strong bearish candles
            elif (current['close'] < current['open'] and 
                  current['high'] - current['low'] > (prev['close'] - current['open']) * 2):
                
                continuation_strength = 0
                for j in range(i+1, min(i+4, len(candlesticks))):
                    if candlesticks[j]['close'] < current['low']:
                        continuation_strength += 1
                
                if continuation_strength >= 2:
                    order_block = OrderBlock(
                        price_high=current['high'],
                        price_low=current['low'],
                        timestamp=current['timestamp'],
                        direction='bearish',
                        strength=continuation_strength / 3.0
                    )
                    order_blocks.append(order_block)
        
        return order_blocks[-5:]  # Return last 5 order blocks
    
    def find_fair_value_gaps(self, candlesticks: List[Dict]) -> List[FairValueGap]:
        """Identify Fair Value Gaps (FVGs) - inefficient price movements"""
        fvgs = []
        
        if len(candlesticks) < 3:
            return fvgs
        
        for i in range(1, len(candlesticks) - 1):
            prev_candle = candlesticks[i-1]
            current = candlesticks[i]
            next_candle = candlesticks[i+1]
            
            # Bullish FVG: Gap between previous low and next high
            if (prev_candle['low'] > next_candle['high'] and 
                current['close'] > current['open']):
                
                fvg = FairValueGap(
                    gap_high=prev_candle['low'],
                    gap_low=next_candle['high'],
                    timestamp=current['timestamp'],
                    direction='bullish'
                )
                fvgs.append(fvg)
            
            # Bearish FVG: Gap between previous high and next low
            elif (prev_candle['high'] < next_candle['low'] and 
                  current['close'] < current['open']):
                
                fvg = FairValueGap(
                    gap_high=next_candle['low'],
                    gap_low=prev_candle['high'],
                    timestamp=current['timestamp'],
                    direction='bearish'
                )
                fvgs.append(fvg)
        
        return fvgs[-10:]  # Return last 10 FVGs
    
    def find_liquidity_pools(self, candlesticks: List[Dict]) -> List[LiquidityPool]:
        """Identify liquidity pools - areas where stops are likely clustered"""
        liquidity_pools = []
        
        # Find recent swing highs and lows as potential liquidity areas
        swing_highs = self._find_swing_highs(candlesticks)
        swing_lows = self._find_swing_lows(candlesticks)
        
        # Recent highs likely have sell-side liquidity above them
        for high in swing_highs[-5:]:
            pool = LiquidityPool(
                price=high['high'],
                type='sell_side',
                strength=high.get('strength', 1.0)
            )
            liquidity_pools.append(pool)
        
        # Recent lows likely have buy-side liquidity below them
        for low in swing_lows[-5:]:
            pool = LiquidityPool(
                price=low['low'],
                type='buy_side', 
                strength=low.get('strength', 1.0)
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
            change = candlesticks[i]['close'] - candlesticks[i-1]['close']
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
    
    def calculate_moving_averages(self, candlesticks: List[Dict]) -> Dict[str, float]:
        """Calculate key moving averages for trend analysis"""
        if len(candlesticks) < 50:
            return {}
        
        closes = [c['close'] for c in candlesticks]
        
        return {
            'ema_20': self._calculate_ema(closes, 20),
            'ema_50': self._calculate_ema(closes, 50),
            'sma_200': sum(closes[-200:]) / 200 if len(closes) >= 200 else sum(closes) / len(closes)
        }
    
    def generate_trade_signal(self, symbol: str) -> Optional[SMCSignal]:
        """Generate comprehensive trade signal based on SMC analysis"""
        try:
            # Get multi-timeframe data
            h1_data = self.get_candlestick_data(symbol, '1h', 100)
            h4_data = self.get_candlestick_data(symbol, '4h', 50)
            d1_data = self.get_candlestick_data(symbol, '1d', 30)
            
            if not h1_data or not h4_data:
                return None
            
            current_price = h1_data[-1]['close']
            
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
            
            # Generate signal logic
            reasoning = []
            confidence = 0.0
            direction = None
            entry_price = current_price
            stop_loss = 0.0
            take_profits = []
            
            # Bullish signal logic
            bullish_signals = 0
            if h4_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                bullish_signals += 2
                reasoning.append(f"H4 {h4_structure.value}")
            
            if h1_structure in [MarketStructure.BULLISH_BOS, MarketStructure.BULLISH_CHoCH]:
                bullish_signals += 1
                reasoning.append(f"H1 {h1_structure.value}")
            
            # Check for bullish order blocks near current price
            for ob in order_blocks:
                if (ob.direction == 'bullish' and 
                    ob.price_low <= current_price <= ob.price_high * 1.02):
                    bullish_signals += 1
                    reasoning.append("Price at bullish order block")
                    break
            
            # Check for unfilled bullish FVGs
            for fvg in fvgs:
                if (fvg.direction == 'bullish' and not fvg.filled and
                    fvg.gap_low <= current_price <= fvg.gap_high):
                    bullish_signals += 1
                    reasoning.append("Price in bullish FVG")
                    break
            
            # Technical indicator confirmation
            if rsi < 30:
                bullish_signals += 1
                reasoning.append("RSI oversold")
            elif 30 <= rsi <= 50:
                bullish_signals += 0.5
                reasoning.append("RSI neutral-bullish")
            
            if mas and current_price > mas.get('ema_20', current_price):
                bullish_signals += 0.5
                reasoning.append("Above EMA 20")
            
            # Bearish signal logic
            bearish_signals = 0
            if h4_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                bearish_signals += 2
                reasoning.append(f"H4 {h4_structure.value}")
            
            if h1_structure in [MarketStructure.BEARISH_BOS, MarketStructure.BEARISH_CHoCH]:
                bearish_signals += 1
                reasoning.append(f"H1 {h1_structure.value}")
            
            for ob in order_blocks:
                if (ob.direction == 'bearish' and 
                    ob.price_low * 0.98 <= current_price <= ob.price_high):
                    bearish_signals += 1
                    reasoning.append("Price at bearish order block")
                    break
            
            for fvg in fvgs:
                if (fvg.direction == 'bearish' and not fvg.filled and
                    fvg.gap_low <= current_price <= fvg.gap_high):
                    bearish_signals += 1
                    reasoning.append("Price in bearish FVG")
                    break
            
            if rsi > 70:
                bearish_signals += 1
                reasoning.append("RSI overbought")
            elif 50 <= rsi <= 70:
                bearish_signals += 0.5
                reasoning.append("RSI neutral-bearish")
            
            if mas and current_price < mas.get('ema_20', current_price):
                bearish_signals += 0.5
                reasoning.append("Below EMA 20")
            
            # Determine signal direction and strength
            if bullish_signals > bearish_signals and bullish_signals >= 3:
                direction = 'long'
                confidence = min(bullish_signals / 5.0, 1.0)
                
                # Calculate entry, SL, and TP for long
                entry_price = current_price
                
                # Stop loss below nearest support/order block
                nearest_support = current_price * 0.97  # Default 3% below
                for ob in order_blocks:
                    if ob.direction == 'bullish' and ob.price_low < current_price:
                        nearest_support = max(nearest_support, ob.price_low * 0.995)
                
                stop_loss = nearest_support
                
                # Take profits based on resistance levels
                take_profits = [
                    current_price * 1.02,  # 2% profit
                    current_price * 1.035, # 3.5% profit
                    current_price * 1.05   # 5% profit
                ]
                
            elif bearish_signals > bullish_signals and bearish_signals >= 3:
                direction = 'short'
                confidence = min(bearish_signals / 5.0, 1.0)
                
                # Calculate entry, SL, and TP for short
                entry_price = current_price
                
                # Stop loss above nearest resistance/order block
                nearest_resistance = current_price * 1.03  # Default 3% above
                for ob in order_blocks:
                    if ob.direction == 'bearish' and ob.price_high > current_price:
                        nearest_resistance = min(nearest_resistance, ob.price_high * 1.005)
                
                stop_loss = nearest_resistance
                
                # Take profits based on support levels
                take_profits = [
                    current_price * 0.98,  # 2% profit
                    current_price * 0.965, # 3.5% profit
                    current_price * 0.95   # 5% profit
                ]
            
            # Only generate signal if confidence is above threshold
            if direction and confidence >= 0.6:
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
                    timestamp=datetime.now()
                )
            
            return None
            
        except Exception as e:
            logging.error(f"Error generating SMC signal for {symbol}: {e}")
            return None
    
    def _find_swing_highs(self, candlesticks: List[Dict], lookback: int = 5) -> List[Dict]:
        """Find swing highs in price data"""
        swing_highs = []
        
        for i in range(lookback, len(candlesticks) - lookback):
            current_high = candlesticks[i]['high']
            is_swing_high = True
            
            # Check if current high is higher than surrounding candles
            for j in range(i - lookback, i + lookback + 1):
                if j != i and candlesticks[j]['high'] >= current_high:
                    is_swing_high = False
                    break
            
            if is_swing_high:
                swing_highs.append({
                    'high': current_high,
                    'timestamp': candlesticks[i]['timestamp'],
                    'index': i,
                    'strength': self._calculate_swing_strength(candlesticks, i, 'high')
                })
        
        return swing_highs
    
    def _find_swing_lows(self, candlesticks: List[Dict], lookback: int = 5) -> List[Dict]:
        """Find swing lows in price data"""
        swing_lows = []
        
        for i in range(lookback, len(candlesticks) - lookback):
            current_low = candlesticks[i]['low']
            is_swing_low = True
            
            # Check if current low is lower than surrounding candles
            for j in range(i - lookback, i + lookback + 1):
                if j != i and candlesticks[j]['low'] <= current_low:
                    is_swing_low = False
                    break
            
            if is_swing_low:
                swing_lows.append({
                    'low': current_low,
                    'timestamp': candlesticks[i]['timestamp'],
                    'index': i,
                    'strength': self._calculate_swing_strength(candlesticks, i, 'low')
                })
        
        return swing_lows
    
    def _calculate_swing_strength(self, candlesticks: List[Dict], index: int, swing_type: str) -> float:
        """Calculate the strength of a swing point based on volume and price action"""
        if index < 1 or index >= len(candlesticks) - 1:
            return 1.0
        
        current = candlesticks[index]
        volume_strength = current['volume'] / max([c['volume'] for c in candlesticks[max(0, index-10):index+10]], default=1)
        
        # Price range strength
        price_range = current['high'] - current['low']
        avg_range = sum([c['high'] - c['low'] for c in candlesticks[max(0, index-10):index+10]]) / min(20, len(candlesticks))
        range_strength = price_range / avg_range if avg_range > 0 else 1.0
        
        return min(volume_strength * range_strength, 3.0)
    
    def _calculate_trend(self, swing_points: List[Dict], price_key: str) -> str:
        """Calculate trend direction from swing points"""
        if len(swing_points) < 2:
            return 'neutral'
        
        recent_prices = [point[price_key] for point in swing_points[-3:]]
        
        if len(recent_prices) >= 2:
            if all(recent_prices[i] > recent_prices[i-1] for i in range(1, len(recent_prices))):
                return 'up'
            elif all(recent_prices[i] < recent_prices[i-1] for i in range(1, len(recent_prices))):
                return 'down'
        
        return 'neutral'
    
    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average"""
        if len(prices) < period:
            return sum(prices) / len(prices)
        
        multiplier = 2.0 / (period + 1)
        ema = sum(prices[:period]) / period
        
        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        
        return ema