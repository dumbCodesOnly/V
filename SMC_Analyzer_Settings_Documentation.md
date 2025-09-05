# SMC Analyzer Settings Documentation

## Overview
The Smart Money Concepts (SMC) Analyzer is a sophisticated trading analysis engine that identifies institutional trading patterns using market structure, order blocks, fair value gaps, and liquidity pool analysis. This document provides comprehensive details on all configurable settings and parameters.

## Configuration Structure
All SMC settings are centralized in the `SMCConfig` class within the configuration system, ensuring consistency across the entire application.

---

## Core Analysis Settings

### Market Structure Analysis
Controls the detection of market structure patterns like Break of Structure (BOS) and Change of Character (CHoCH).

| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `MIN_CANDLESTICKS_FOR_STRUCTURE` | 20 | Minimum number of candlesticks required to perform market structure analysis |
| `MIN_SWING_POINTS` | 2 | Minimum swing highs and lows needed to identify consolidation patterns |

**Impact on Analysis:**
- Higher `MIN_CANDLESTICKS_FOR_STRUCTURE` values provide more reliable structure detection but require more historical data
- Lower `MIN_SWING_POINTS` values increase sensitivity to structure changes but may generate false signals

### Swing Point Detection
Defines how swing highs and lows are identified in price action.

| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `DEFAULT_LOOKBACK_PERIOD` | 5 | Number of candles to look back/forward when identifying swing points |
| `CONTINUATION_LOOKAHEAD` | 4 | Candles to analyze ahead for continuation strength validation |

**Usage in Code:**
```python
swing_highs = self._find_swing_highs(candlesticks, lookback=SMCConfig.DEFAULT_LOOKBACK_PERIOD)
```

**Optimization Notes:**
- Smaller lookback periods (3-4) capture more swing points but may include noise
- Larger lookback periods (7-10) provide stronger swing points but may miss subtle structure changes

---

## Order Block Detection Settings

Order blocks represent areas where institutional traders place significant orders. The analyzer identifies these through specific candlestick patterns and validation criteria.

### Current Implementation Parameters
- **Strong Candle Criteria**: Bullish/bearish candles with range > 2x the previous candle's body
- **Continuation Validation**: Requires at least 2 candles continuing the move
- **Maximum Tracked**: Last 5 order blocks retained for analysis

### Strength Calculation
Order block strength is calculated using:
```python
strength = continuation_strength / 3.0
```
Where `continuation_strength` counts candles that continue the initial move.

---

## Fair Value Gap (FVG) Detection

### Configuration Parameters
| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `MIN_CANDLESTICKS_FOR_FVG` | 3 | Minimum candlesticks required for FVG detection |

### Detection Logic
- **Bullish FVG**: Gap between previous candle's low and next candle's high
- **Bearish FVG**: Gap between previous candle's high and next candle's low
- **Maximum Tracked**: Last 10 FVGs retained for analysis

### FVG Validation Criteria
1. Must be formed by 3 consecutive candles
2. Middle candle must be bullish (for bullish FVG) or bearish (for bearish FVG)
3. Gap must exist between outer candles

---

## Liquidity Pool Analysis

### Configuration Parameters
| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `RECENT_SWING_LOOKBACK` | 5 | Number of recent swing points analyzed for liquidity identification |

### Liquidity Types
1. **Buy-side Liquidity**: Located below recent swing lows (stop losses from long positions)
2. **Sell-side Liquidity**: Located above recent swing highs (stop losses from short positions)

---

## Volume and Range Analysis

### Configuration Parameters
| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `VOLUME_RANGE_LOOKBACK` | 10 | Candles to analyze for volume and range calculations |
| `AVG_RANGE_PERIOD` | 20 | Period for calculating average price range |

### Swing Strength Calculation
Combines volume and price range analysis:
```python
volume_strength = current_volume / max_volume_in_range
range_strength = current_range / average_range
swing_strength = min(volume_strength * range_strength, 3.0)
```

**Maximum Strength Cap**: 3.0 to prevent outlier distortion

---

## Trend Analysis Settings

### Configuration Parameters
| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `MIN_PRICES_FOR_TREND` | 2 | Minimum price points required for trend direction analysis |

### Trend Detection Logic
- **Uptrend**: All recent prices higher than previous ones
- **Downtrend**: All recent prices lower than previous ones  
- **Neutral**: Mixed or insufficient price movement

---

## Multi-Timeframe Analysis

### Supported Timeframes
The analyzer operates on three key timeframes:
- **1 Hour (1h)**: Primary analysis timeframe for entry signals
- **4 Hour (4h)**: Medium-term structure and confirmation
- **Daily (1d)**: Long-term bias and major structure levels

### Data Requirements
| Timeframe | Default Limit | Purpose |
|-----------|---------------|---------|
| 1h | 100 candles | Detailed entry analysis and order block detection |
| 4h | 50 candles | Structure confirmation and liquidity pool analysis |
| 1d | 30 candles | Directional bias and major structure levels |

---

## Signal Generation Settings

### Confidence Thresholds
- **Minimum Signal Confidence**: 0.6 (60%)
- **Strong Signal Threshold**: 0.8 (80%)
- **Very Strong Signal Threshold**: 0.9 (90%)

### Signal Strength Categories
1. **WEAK**: Confidence 0.6-0.69
2. **MODERATE**: Confidence 0.7-0.79
3. **STRONG**: Confidence 0.8-0.89
4. **VERY_STRONG**: Confidence 0.9+

### Risk-Reward Calculations
- **Default Stop Loss**: 3% from entry price
- **Take Profit Levels**: 2%, 3.5%, and 5% from entry
- **Risk-Reward Ratio**: Calculated as `reward / risk`

---

## Technical Indicator Integration

### RSI (Relative Strength Index)
- **Period**: 14 candles
- **Oversold Threshold**: 30
- **Overbought Threshold**: 70

### Moving Averages
- **EMA 20**: 20-period Exponential Moving Average
- **EMA 50**: 50-period Exponential Moving Average  
- **SMA 200**: 200-period Simple Moving Average

---

## Performance Optimization Settings

### API Call Management
- **Data Source**: Binance API for reliable candlestick data
- **Request Timeout**: 10 seconds
- **Error Handling**: Graceful fallback with empty data return

### Memory Management
- **Order Blocks**: Maximum 5 recent blocks retained
- **FVGs**: Maximum 10 recent gaps retained
- **Swing Points**: Dynamic based on lookback period

---

## Customization Guidelines

### Adjusting Sensitivity
**For Higher Sensitivity (More Signals):**
- Reduce `MIN_CANDLESTICKS_FOR_STRUCTURE` to 15-18
- Decrease `DEFAULT_LOOKBACK_PERIOD` to 3-4
- Lower confidence threshold to 0.5

**For Lower Sensitivity (Fewer, Stronger Signals):**
- Increase `MIN_CANDLESTICKS_FOR_STRUCTURE` to 25-30
- Increase `DEFAULT_LOOKBACK_PERIOD` to 6-8
- Raise confidence threshold to 0.7-0.8

### Market-Specific Adjustments
**For Volatile Markets:**
- Increase `VOLUME_RANGE_LOOKBACK` to 15
- Reduce FVG retention to 5-7 gaps
- Higher continuation requirements for order blocks

**For Stable Markets:**
- Decrease `AVG_RANGE_PERIOD` to 15
- Increase swing point sensitivity
- Lower volume requirements for validation

---

## Monitoring and Diagnostics

### Key Metrics to Track
1. **Signal Generation Rate**: Signals per time period
2. **Confidence Distribution**: Breakdown by strength categories
3. **Structure Detection Accuracy**: BOS vs CHoCH identification
4. **False Signal Rate**: Signals that don't reach first TP

### Performance Indicators
- **Average Confidence Score**: Should be > 0.7 for quality signals
- **Risk-Reward Ratio**: Target average > 1.5:1
- **Multi-timeframe Alignment**: Higher confidence when timeframes agree

---

## Implementation Notes

### Code Structure
The SMC analyzer follows object-oriented design principles with clear separation of concerns:
- **Data Acquisition**: `get_candlestick_data()`
- **Structure Analysis**: `detect_market_structure()`
- **Pattern Recognition**: `find_order_blocks()`, `find_fair_value_gaps()`
- **Signal Generation**: `generate_trade_signal()`

### Error Handling
All methods include comprehensive error handling with logging for debugging and monitoring purposes.

### Extensibility
The configuration-driven approach allows easy adjustment of parameters without code changes, supporting:
- A/B testing of different parameter sets
- Market-specific optimization
- Performance tuning based on historical results

---

*This documentation reflects the current implementation as of the latest version. Parameters should be adjusted based on backtesting results and market conditions.*