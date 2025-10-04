# SMC Analyzer Settings Documentation

**Last Updated:** October 4, 2025  
**Version:** Multi-Timeframe Institutional Analysis v2.0  
**Status:** All 7 Phases Complete

---

## Overview

The Smart Money Concepts (SMC) Analyzer is an institutional-grade multi-timeframe trading analysis engine that implements hierarchical top-down analysis across 4 timeframes (Daily → H4/H1 → 15m execution). The system identifies institutional trading patterns, generates high-confidence signals with scaling entries, precise stop-losses, and R:R-based take profits.

**Key Features:**
- 📊 **Multi-Timeframe Analysis**: 15m, 1h, 4h, 1d hierarchical workflow
- 🎯 **Institutional Filtering**: ATR-based volatility filtering
- 📈 **Smart Entry Scaling**: 50%/25%/25% allocation (market + limit orders)
- 🛡️ **Precision Stop-Loss**: 15m swing levels + ATR buffers
- 💰 **R:R-Based Take Profits**: 1R, 2R, liquidity targets
- 🧠 **Dynamic Confidence Scoring**: Multi-timeframe alignment scoring

---

## Configuration Structure

All SMC settings are centralized in `config.py` → `TradingConfig` class. The configuration is organized by implementation phases for clarity and maintainability.

---

## Phase 1: 15m Execution Timeframe

### Timeframe Support
| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `TIMEFRAME_15M_LIMIT` | 400 | 15m candlesticks to fetch (~4 days of data) |
| `TARGET_CANDLES_15M` | 400 | Target rolling window size for 15m |
| `CLEANUP_BUFFER_15M` | 100 | Buffer before cleanup (500 candles total) |
| `ENABLED_15M` | True | Enable 15m timeframe analysis |
| `KLINES_15M_CACHE_TTL` | 1 | Cache TTL in minutes for 15m data |

**Purpose:** Provides precise execution-level analysis while maintaining higher timeframe context.

**Impact:**
- Enables tighter stop-losses using 15m swing levels
- Allows precise entry timing based on 15m structure
- Increases data freshness with 1-minute cache TTL

---

## Phase 2: Multi-Timeframe Workflow

### Hierarchical Analysis Flow

The analyzer implements institutional-style top-down analysis:

```
Daily (1d)  → Determine macro trend & liquidity targets (HTF bias)
    ↓
H4 + H1     → Identify intermediate structure (OBs, FVGs, BOS/CHoCH)
    ↓
15m         → Generate precise execution signals (must align with HTF)
```

### Key Methods

#### `_get_htf_bias(d1_data, h4_data) -> Dict`
**Location:** lines 1406-1493  
**Purpose:** Determines high timeframe directional bias

**Returns:**
```python
{
    "bias": "bullish" | "bearish" | "neutral",
    "confidence": 0.0-1.0,
    "daily_trend": str,
    "h4_trend": str,
    "liquidity_targets": List[float],
    "reasoning": str
}
```

**Logic:**
- Analyzes daily trend direction using recent candle closes
- Identifies H4 market structure
- Finds key liquidity levels from swing highs/lows
- Only proceeds if strong directional bias exists

#### `_get_intermediate_structure(h1_data, h4_data) -> Dict`
**Location:** lines 1494-1572  
**Purpose:** Analyzes intermediate timeframes for POI levels

**Returns:**
```python
{
    "order_blocks": List[OrderBlock],
    "fvgs": List[FairValueGap],
    "structure_direction": str,
    "poi_levels": List[Dict],
    "bos_choch_detected": bool,
    "reasoning": str
}
```

**Logic:**
- Detects unmitigated order blocks on H4/H1
- Identifies fresh fair value gaps
- Confirms BOS/CHoCH structure shifts
- Prioritizes POI levels for entries

#### `_get_execution_signal_15m(m15_data, htf_bias, intermediate_structure) -> Dict`
**Location:** lines 1573-1763  
**Purpose:** Generates 15m execution signal aligned with HTF

**Returns:**
```python
{
    "signal": "long" | "short" | None,
    "entry_price": float,
    "stop_loss": float,
    "take_profits": List[Tuple[float, float]],
    "alignment_score": 0.0-1.0,
    "reasoning": str
}
```

**Logic:**
- Ensures 15m structure aligns with HTF bias
- Finds 15m swing points for entry
- Rejects if alignment score < 0.3 (conflict threshold)
- Returns complete trade setup with entry/SL/TP

**Configuration:**
No additional configuration required - uses existing timeframe limits.

---

## Phase 3: Enhanced Confidence Scoring

### Confidence Scoring Rules

Base confidence from existing logic (0.5 - 0.8) is enhanced with multi-timeframe alignment:

| Condition | Adjustment | Reasoning |
|-----------|-----------|-----------|
| 15m perfectly aligns with HTF | +0.2 | Strong multi-timeframe confluence |
| 15m conflicts with HTF | -0.3 (reject) | Avoid counter-trend trades |
| Liquidity sweep confirmed | +0.1 | Additional institutional confluence |
| Entry from HTF OB/FVG | +0.1 | Entering from POI level |

### Key Method

#### `_calculate_15m_alignment_score(m15_structure, htf_bias, ...) -> float`
**Location:** lines 2967-3085  
**Purpose:** Calculates multi-timeframe alignment score

**Returns:** Alignment score (0.0-1.0)
- **1.0**: Perfect alignment (all timeframes agree)
- **0.5**: Neutral (no clear alignment)
- **0.0**: Conflict (timeframes disagree)

**Logic:**
- Compares 15m trend direction with HTF bias
- Checks 15m swing structure supports HTF
- Evaluates proximity to intermediate POI levels
- Early rejection saves computational resources

**Configuration:**
Built into confidence calculation - no separate parameters.

---

## Phase 4: Scaling Entry Management

### Entry Allocation Strategy

| Entry Level | Allocation | Order Type | Price Depth |
|-------------|-----------|------------|-------------|
| Entry 1 | 50% | Market | Current price (immediate) |
| Entry 2 | 25% | Limit | 0.4% better price |
| Entry 3 | 25% | Limit | 1.0% better price |

### Configuration Parameters

| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `USE_SCALED_ENTRIES` | True | Enable scaling entry strategy |
| `SCALED_ENTRY_ALLOCATIONS` | [50, 25, 25] | Allocation percentages (must sum to 100) |
| `SCALED_ENTRY_DEPTH_1` | 0.004 | First limit order depth (0.4%) |
| `SCALED_ENTRY_DEPTH_2` | 0.010 | Second limit order depth (1.0%) |

### Key Method

#### `_calculate_scaled_entries(current_price, direction, order_blocks, fvgs) -> List[Dict]`
**Location:** lines 3086-3302  
**Purpose:** Calculates 3-level entry strategy

**Returns:**
```python
[
    {
        "entry_price": float,
        "allocation_percent": 50,
        "order_type": "market",
        "reasoning": str
    },
    {
        "entry_price": float,
        "allocation_percent": 25,
        "order_type": "limit",
        "reasoning": str
    },
    {
        "entry_price": float,
        "allocation_percent": 25,
        "order_type": "limit",
        "reasoning": str
    }
]
```

**Logic:**
- Entry 1: Immediate market execution (50%)
- Entry 2: Limit at nearest OB/FVG zone (25%)
- Entry 3: Limit at deeper OB/FVG zone (25%)
- Each entry tracks individual status and diagnostics

**Benefits:**
- Reduces FOMO by securing partial position immediately
- Improves average entry price through limit orders
- Allows scaling into high-conviction setups

---

## Phase 5: Refined Stop-Loss with 15m Swings

### Stop-Loss Logic

**Long Trades:**
```
SL = Last 15m Swing Low - (ATR × Buffer Multiplier)
```

**Short Trades:**
```
SL = Last 15m Swing High + (ATR × Buffer Multiplier)
```

### Configuration Parameters

| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `USE_15M_SWING_SL` | True | Use 15m swing levels for stop-loss |
| `SL_ATR_BUFFER_MULTIPLIER` | 0.5 | ATR buffer multiplier (0.5x) |
| `SL_MIN_DISTANCE_PERCENT` | 0.005 | Minimum SL distance (0.5%) |

### Key Methods

#### `_find_15m_swing_levels(m15_data) -> Dict`
**Location:** lines 3303-3385  
**Purpose:** Identifies recent swing highs and lows on 15m

**Returns:**
```python
{
    "swing_highs": List[float],
    "swing_lows": List[float],
    "last_swing_high": float,
    "last_swing_low": float,
    "count_highs": int,
    "count_lows": int
}
```

**Detection Logic:**
- Swing High: High > 2 candles before AND 2 candles after
- Swing Low: Low < 2 candles before AND 2 candles after
- Tracks last 20 swing points for reliability

#### `_calculate_refined_sl_with_atr(direction, swing_levels, atr_value, ...) -> float`
**Location:** lines 3386-3485  
**Purpose:** Calculates precise stop-loss with ATR buffer

**Logic:**
1. Selects appropriate swing level (high for short, low for long)
2. Adds/subtracts ATR buffer for safety margin
3. Enforces minimum distance requirements
4. Returns precise SL price

**Benefits:**
- Tighter stop-losses than fixed percentage
- Respects market structure (swing levels)
- Adaptive to volatility (ATR buffer)
- Reduces risk per trade

---

## Phase 6: Multi-Take Profit Management

### R:R-Based Take Profit Levels

| TP Level | R:R Ratio | Allocation | Target |
|----------|-----------|-----------|--------|
| TP1 | 1.0 | 40% | 1R (100% of risk) |
| TP2 | 2.0 | 30% | 2R (200% of risk) |
| TP3 | 3.0 or Liquidity | 30% | Liquidity pool or 3R |

### Configuration Parameters

| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `USE_RR_BASED_TPS` | True | Use R:R-based take profit levels |
| `TP_ALLOCATIONS` | [40, 30, 30] | TP allocation percentages (must sum to 100) |
| `TP_RR_RATIOS` | [1.0, 2.0, 3.0] | R:R ratios for TP1, TP2, TP3 |
| `ENABLE_TRAILING_AFTER_TP1` | True | Activate trailing stop after TP1 hit |
| `TRAILING_STOP_PERCENT` | 0.8 | Trailing stop distance (0.8% - optimized for leverage) |

### Key Method

#### `_calculate_rr_based_take_profits(entry_price, stop_loss, direction, liquidity_targets) -> List[Tuple[float, float]]`
**Location:** lines 3486-3603  
**Purpose:** Calculates R:R-based take profit levels

**Returns:**
```python
[
    (tp1_price, 40),  # TP1 at 1R, 40% allocation
    (tp2_price, 30),  # TP2 at 2R, 30% allocation
    (tp3_price, 30)   # TP3 at liquidity or 3R, 30% allocation
]
```

**Logic:**
1. Calculates risk amount: `|entry_price - stop_loss|`
2. TP1 = Entry + 1R (fixed 1:1 risk/reward)
3. TP2 = Entry + 2R (fixed 2:1 risk/reward)
4. TP3 = Nearest liquidity pool beyond 2R, or Entry + 3R
5. Validates allocations sum to 100%

**Liquidity Targeting:**
- TP3 intelligently targets detected liquidity pools
- Only uses liquidity targets beyond 2R minimum distance
- Falls back to 3R if no valid liquidity found
- Improves exit quality by targeting institutional levels

**Trailing Stop:**
- Optional feature activated after TP1 is hit
- Trails at configured percentage (default 0.8%)
- Protects profits while allowing runner to extend

**Benefits:**
- Consistent risk/reward across all trades
- Removes guesswork from TP placement
- TP3 targets institutional liquidity for optimal exits
- Flexible allocation allows customization

---

## Phase 7: ATR Risk Filter

### Volatility-Based Trade Filtering

The ATR filter rejects trades in low-volatility, choppy market conditions to improve signal quality.

### Configuration Parameters

| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| `USE_ATR_FILTER` | True | Enable ATR-based volatility filtering |
| `MIN_ATR_15M_PERCENT` | 0.8 | Minimum 0.8% ATR on 15m timeframe |
| `MIN_ATR_H1_PERCENT` | 1.2 | Minimum 1.2% ATR on H1 timeframe |
| `USE_DYNAMIC_POSITION_SIZING` | False | Adjust position size based on ATR (optional) |

### Key Methods

#### `_check_atr_filter(m15_data, h1_data, current_price) -> Dict`
**Location:** lines 3604-3665  
**Purpose:** Checks if ATR meets minimum volatility requirements

**Returns:**
```python
{
    "passes": bool,
    "atr_15m": float,
    "atr_h1": float,
    "atr_15m_percent": float,
    "atr_h1_percent": float,
    "reason": str
}
```

**Logic:**
1. Calculates ATR(14) on both 15m and H1 timeframes
2. Converts ATR to percentage of current price
3. Checks against minimum thresholds from configuration
4. Both timeframes must pass for filter to pass
5. Returns comprehensive filter results

**Filter Rules:**
- **15m ATR** must be ≥ 0.8% of price
- **H1 ATR** must be ≥ 1.2% of price
- **Both** conditions must be met to pass filter
- Rejects trade immediately if filter fails

#### `_calculate_dynamic_position_size(base_size, atr_percent) -> float`
**Location:** lines 3667-3710  
**Purpose:** Adjusts position size based on ATR volatility (OPTIONAL)

**Returns:** Position size multiplier (0.5x to 1.5x)

**Logic:**
| ATR Percent | Condition | Multiplier | Rationale |
|-------------|-----------|-----------|-----------|
| > 3.0% | High volatility | 0.7x (70%) | Reduce size for risk management |
| 1.5% - 3.0% | Normal volatility | 1.0x (100%) | Use base position size |
| < 1.5% | Low volatility | 1.2x (120%) | Increase size (within limits) |

**Bounds:** Multiplier capped between 0.5x (50%) and 1.5x (150%)

**Benefits:**
- Rejects low-quality trades in choppy conditions
- Improves win rate by avoiding ranging markets
- Saves computational resources (early rejection)
- Optional dynamic sizing adapts to market volatility
- Percentage-based thresholds fair across all symbols

---

## Technical Indicator Integration

### Average True Range (ATR)

**Purpose:** Measures market volatility for stop-loss placement and risk filtering

**Parameters:**
- Period: 14 candles
- Timeframes: 15m, 1h (for Phase 7 filter)

**Usage:**
- Phase 5: ATR buffer for stop-loss calculation
- Phase 7: Volatility filtering (minimum ATR thresholds)
- Optional: Dynamic position sizing based on ATR

---

## Signal Generation Workflow

### Complete Multi-Timeframe Analysis Flow

```
1. Fetch Data (15m, 1h, 4h, 1d)
   ↓
2. Phase 7: Check ATR Filter
   - If fails → Reject (low volatility)
   ↓
3. Phase 2: Determine HTF Bias (D1 + H4)
   - Identify macro trend
   - Find liquidity targets
   - If no clear bias → Reject
   ↓
4. Phase 2: Analyze Intermediate Structure (H4 + H1)
   - Find unmitigated OBs and FVGs
   - Detect BOS/CHoCH
   - Identify POI levels
   - If no valid structure → Reject
   ↓
5. Phase 2: Generate 15m Execution Signal
   - Check 15m aligns with HTF bias
   - Find 15m entry point
   - If alignment < 0.3 → Reject (conflict)
   ↓
6. Phase 3: Calculate Confidence Score
   - Base confidence + 15m alignment bonus
   - Add liquidity sweep bonus
   - Add POI entry bonus
   - If confidence < threshold → Reject
   ↓
7. Phase 5: Find 15m Swing Levels
   - Identify recent swing highs/lows
   - Select appropriate level for SL
   ↓
8. Phase 5: Calculate Refined Stop-Loss
   - Use 15m swing + ATR buffer
   - Enforce minimum distance
   ↓
9. Phase 6: Calculate R:R-Based Take Profits
   - TP1 at 1R (40% allocation)
   - TP2 at 2R (30% allocation)
   - TP3 at liquidity or 3R (30% allocation)
   ↓
10. Phase 4: Calculate Scaled Entries
    - Entry 1: 50% at market
    - Entry 2: 25% at first limit level
    - Entry 3: 25% at second limit level
    ↓
11. Return Complete SMC Signal
    - Signal direction (long/short)
    - Scaled entry levels with allocations
    - Precise stop-loss
    - R:R-based take profits
    - Confidence score
    - Comprehensive diagnostics
```

---

## Diagnostics and Logging

The analyzer provides comprehensive diagnostics for each signal:

### Available Diagnostic Fields

```python
{
    # Phase 2: HTF Analysis
    "phase2_htf_bias": Dict,
    "phase2_intermediate_structure": Dict,
    "phase2_execution_signal_15m": Dict,
    
    # Phase 3: Confidence Scoring
    "phase3_alignment_score": float,
    "phase3_confidence_breakdown": Dict,
    
    # Phase 4: Scaling Entries
    "phase4_scaled_entries": List[Dict],
    "phase4_entry_allocations": List[int],
    
    # Phase 5: Stop-Loss
    "phase5_swing_levels": Dict,
    "phase5_sl_calculation": Dict,
    
    # Phase 6: Take Profits
    "phase6_tp_levels": List[float],
    "phase6_tp_allocations": List[int],
    "phase6_rr_ratios": List[float],
    
    # Phase 7: ATR Filter
    "phase7_atr_filter": Dict,
    "phase7_position_size_multiplier": float
}
```

### Logging Levels

- **INFO**: Key decisions (HTF bias, signal direction, rejections)
- **DEBUG**: Detailed analysis (swing levels, ATR values, calculations)
- **WARNING**: Filter failures, alignment conflicts

---

## Performance Considerations

### Data Fetching
- **15m data**: 400 candles (~4 days) - very short cache TTL (1 min)
- **1h data**: 300 candles (~12.5 days) - 2-minute cache TTL
- **4h data**: 100 candles (~16.6 days) - 5-minute cache TTL
- **1d data**: 50 candles (~50 days) - 15-minute cache TTL

### Optimization Strategies
1. **Early Rejection**: ATR filter runs before heavy analysis
2. **Hierarchical Flow**: HTF bias checked before intermediate structure
3. **Smart Caching**: Volatility-based cache TTLs reduce API calls
4. **Diagnostic Tracking**: Comprehensive debugging without performance impact

---

## Configuration Best Practices

### For Conservative Trading
```python
USE_ATR_FILTER = True
MIN_ATR_15M_PERCENT = 1.0  # Higher threshold
MIN_ATR_H1_PERCENT = 1.5   # Higher threshold
SCALED_ENTRY_ALLOCATIONS = [60, 20, 20]  # More at market
SL_ATR_BUFFER_MULTIPLIER = 0.7  # Wider stops
TP_ALLOCATIONS = [50, 30, 20]  # Take more profit early
```

### For Aggressive Trading
```python
USE_ATR_FILTER = True
MIN_ATR_15M_PERCENT = 0.6  # Lower threshold
MIN_ATR_H1_PERCENT = 1.0   # Lower threshold
SCALED_ENTRY_ALLOCATIONS = [40, 30, 30]  # More at limit orders
SL_ATR_BUFFER_MULTIPLIER = 0.3  # Tighter stops
TP_ALLOCATIONS = [30, 30, 40]  # Let more run to TP3
```

### For Testing/Development
```python
USE_ATR_FILTER = False  # Disable to see all signals
USE_DYNAMIC_POSITION_SIZING = True  # Test optional feature
# All other features enabled with default values
```

---

## Troubleshooting

### Common Issues

**Issue: No signals generated**
- Check ATR filter thresholds (may be too strict)
- Verify HTF bias requirements (may need clearer trend)
- Review confidence thresholds

**Issue: Too many signals**
- Increase ATR filter thresholds
- Tighten 15m alignment requirements
- Raise confidence threshold

**Issue: Entries not filling**
- Adjust `SCALED_ENTRY_DEPTH_1` and `SCALED_ENTRY_DEPTH_2`
- Consider increasing market allocation percentage
- Review limit order placement logic

**Issue: Stop-loss too tight**
- Increase `SL_ATR_BUFFER_MULTIPLIER`
- Increase `SL_MIN_DISTANCE_PERCENT`
- Review 15m swing detection logic

---

## Related Files

- **Implementation Plan**: `SMC_MULTI_TIMEFRAME_IMPLEMENTATION_PLAN.md`
- **Implementation Status**: `SMC_IMPLEMENTATION_STATUS.md`
- **Phase 7 Summary**: `PHASE_7_COMPLETION_SUMMARY.md`
- **Main Code**: `api/smc_analyzer.py` (~3710 lines)
- **Configuration**: `config.py` → `TradingConfig` class

---

## Version History

**v2.0 (October 4, 2025)**: Complete multi-timeframe institutional analysis
- All 7 phases implemented
- Hierarchical top-down workflow
- ATR risk filtering
- Scaling entries and R:R-based TPs

**v1.0 (Prior to October 2025)**: Single-timeframe analysis
- Basic SMC pattern detection
- Simple entry/exit logic
- No multi-timeframe validation

---

*This documentation reflects the current implementation as of October 4, 2025. All 7 phases are complete and operational.*
