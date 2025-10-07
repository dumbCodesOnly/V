# SMC Analyzer - Complete Documentation

**Last Updated:** October 7, 2025  
**Version:** 3.1 (Institutional-Grade with Phase 4 Migration)  
**Status:** ✅ **Phase 4 Migration Complete** + ⚠️ **15 Logic Issues Identified for Resolution**

---

## Table of Contents
1. [Overview](#overview)
2. [Current Status](#current-status)
3. [Phase 4 Migration Summary](#phase-4-migration-summary)
4. [Identified Issues & Recommended Fixes](#identified-issues--recommended-fixes)
5. [Key Features](#key-features)
6. [Architecture](#architecture)
7. [Configuration](#configuration)
8. [Implementation Phases](#implementation-phases)
9. [Usage Guide](#usage-guide)
10. [API Reference](#api-reference)

---

## Overview

The Smart Money Concepts (SMC) Analyzer is an institutional-grade multi-timeframe trading analysis engine that implements hierarchical top-down analysis across 4 timeframes (Daily → H4/H1 → 15m execution). The system identifies institutional trading patterns and generates high-confidence signals with precise entry, stop-loss, and take-profit levels.

### Purpose
- Identify institutional trading patterns using Smart Money Concepts
- Generate high-probability trade signals with multi-timeframe confirmation
- Provide precise entry zones, stop-losses, and take-profit levels using **institutional scaled entry strategy**
- Filter out low-quality setups using institutional-grade risk filters

---

## Current Status

### ✅ Phase 4 Migration Complete (October 7, 2025)

**Institutional Scaled Entry Format Successfully Implemented:**

| Component | Migration Status | Details |
|-----------|-----------------|---------|
| SMCSignal Dataclass | ✅ Complete | Now requires `scaled_entries`, `htf_bias`, `intermediate_structure` fields |
| Database Schema | ✅ Complete | Added new fields, made legacy fields nullable |
| Cache Conversion | ✅ Complete | Fixed `to_smc_signal()` and `from_smc_signal()` methods |
| API Endpoints | ✅ Complete | All endpoints return institutional format |
| Admin Templates | ✅ Complete | Updated to display scaled entries |
| Mini App | ✅ Complete | Chart annotations use scaled entries |

**Error Fixed:** `'SMCSignal' object has no attribute 'entry_price'` - completely resolved

### ⚠️ Outstanding Issues (15 Total)

The following logic flaws, inconsistencies, and potential bugs have been identified and require resolution:

---

## Phase 4 Migration Summary

### What Changed

#### 1. **SMCSignal Structure**
**Before (Legacy):**
```python
@dataclass
class SMCSignal:
    entry_price: float
    stop_loss: float
    take_profit_levels: List[float]
    # ... other fields
```

**After (Institutional):**
```python
@dataclass
class SMCSignal:
    symbol: str
    direction: str
    confidence: float
    reasoning: List[str]
    signal_strength: SignalStrength
    risk_reward_ratio: float
    timestamp: datetime
    current_market_price: float
    scaled_entries: List[ScaledEntry]  # ← NEW: Institutional format
    htf_bias: str                       # ← NEW: Higher timeframe context
    intermediate_structure: str         # ← NEW: Structure context
    execution_timeframe: str = "15m"    # ← NEW: Execution TF
```

#### 2. **Database Schema Updates**
```sql
-- New required fields
ALTER TABLE smc_signal_cache 
ADD COLUMN htf_bias VARCHAR(50),
ADD COLUMN intermediate_structure VARCHAR(100),
ADD COLUMN execution_timeframe VARCHAR(10) DEFAULT '15m';

-- Legacy fields made nullable
ALTER TABLE smc_signal_cache 
ALTER COLUMN entry_price DROP NOT NULL,
ALTER COLUMN stop_loss DROP NOT NULL,
ALTER COLUMN take_profit_levels DROP NOT NULL;

-- Scaled entries made required
ALTER TABLE smc_signal_cache 
ALTER COLUMN scaled_entries SET NOT NULL;
```

#### 3. **Cache Conversion Logic**
- `SMCSignalCache.to_smc_signal()` now properly constructs SMCSignal with institutional fields
- Includes fallback logic for old cache entries
- `SMCSignalCache.from_smc_signal()` saves all new fields and extracts legacy fields from first scaled entry

---

## Identified Issues & Recommended Fixes

### CRITICAL ISSUES

#### ❌ Issue #43: Circular Dependency in TradeConfiguration.to_trade_config()
**Severity:** CRITICAL  
**Location:** `api/models.py` lines 278-342  
**Problem:**
```python
def to_trade_config(self):
    from . import app  # ← CIRCULAR DEPENDENCY
    config = app.TradeConfig(self.trade_id, self.name)
```
The method imports `app` from current package, creating circular dependency if `app.py` also imports from `models.py`.

**Recommended Fix:**
```python
def to_trade_config(self):
    # Lazy import to avoid circular dependency
    from .app import TradeConfig
    config = TradeConfig(self.trade_id, self.name)
    # ... rest of method
```

**Impact:** Can cause import errors, especially during testing or module reloading

---

### HIGH PRIORITY ISSUES

#### ❌ Issue #44: Inconsistent Breakeven After Handling
**Severity:** HIGH  
**Location:** `api/models.py` lines 292-309, 362-377  
**Problem:**
- `to_trade_config()` converts string representations ('tp1', 'tp2') to floats
- `from_trade_config()` has similar logic but inconsistent handling
- Database can store either strings or floats, leading to data corruption

**Recommended Fix:**
```python
# Standardize on numeric representation in database
# Always store as float: 0.0 (disabled), 1.0 (after TP1), 2.0 (after TP2), 3.0 (after TP3)

def from_trade_config(user_id, config):
    # Convert all string representations to float before storage
    if hasattr(config, "breakeven_after"):
        breakeven_value = config.breakeven_after
        if isinstance(breakeven_value, str):
            breakeven_map = {"disabled": 0.0, "tp1": 1.0, "tp2": 2.0, "tp3": 3.0}
            db_config.breakeven_after = breakeven_map.get(breakeven_value, 0.0)
        else:
            db_config.breakeven_after = float(breakeven_value) if breakeven_value else 0.0
    else:
        db_config.breakeven_after = 0.0
```

**Impact:** Data corruption, incorrect breakeven SL trigger logic

---

#### ❌ Issue #45: Breakeven SL Price Calculated in Wrong Location
**Severity:** HIGH  
**Location:** `api/models.py` lines 311-314  
**Problem:**
```python
# WRONG: Calculated during database load
config.breakeven_sl_price = (
    self.entry_price if config.breakeven_sl_triggered else 0.0
)
```
Breakeven SL price should be calculated dynamically during trade execution/monitoring, not stored statically.

**Recommended Fix:**
```python
# Remove static calculation from to_trade_config()
# Instead, calculate in monitoring logic:

def update_breakeven_stop_loss(config):
    """Calculate breakeven SL dynamically during monitoring"""
    if config.breakeven_sl_triggered and config.entry_price > 0:
        return config.entry_price
    return 0.0
```

**Impact:** Incorrect breakeven SL prices if entry price changes

---

#### ❌ Issue #46: LONG TP Ordering Validation Weak
**Severity:** HIGH  
**Location:** `api/smc_analyzer.py` lines 1015-1020  
**Problem:**
```python
if not (take_profits[0] < take_profits[1] < take_profits[2]):
    # Auto-correct but doesn't handle duplicates or edge cases
    take_profits = sorted(take_profits)
```
Doesn't robustly handle duplicates or invalid values.

**Recommended Fix:**
```python
# Robust validation with duplicate handling
def validate_long_tp_ordering(take_profits, entry_price):
    """Ensure valid LONG TP ordering with duplicate handling"""
    # Remove duplicates
    unique_tps = sorted(list(set(take_profits)))
    
    # Ensure we have at least 3 levels
    if len(unique_tps) < 3:
        # Generate missing levels using fixed percentages
        tp_percentages = [2.0, 4.0, 6.0]  # 2%, 4%, 6%
        take_profits = [entry_price * (1 + pct/100) for pct in tp_percentages]
    else:
        take_profits = unique_tps[:3]
    
    # Verify strict ordering
    assert take_profits[0] < take_profits[1] < take_profits[2], "LONG TP ordering failed"
    return take_profits
```

**Impact:** Invalid TP levels, potential trade execution failures

---

#### ❌ Issue #47: SHORT TP Ordering Validation Weak
**Severity:** HIGH  
**Location:** `api/smc_analyzer.py` lines 1203-1208  
**Problem:** Same as Issue #46 but for SHORT positions

**Recommended Fix:**
```python
def validate_short_tp_ordering(take_profits, entry_price):
    """Ensure valid SHORT TP ordering with duplicate handling"""
    # Remove duplicates
    unique_tps = sorted(list(set(take_profits)), reverse=True)
    
    # Ensure we have at least 3 levels
    if len(unique_tps) < 3:
        # Generate missing levels using fixed percentages
        tp_percentages = [2.0, 4.0, 6.0]  # 2%, 4%, 6%
        take_profits = [entry_price * (1 - pct/100) for pct in tp_percentages]
    else:
        take_profits = unique_tps[:3]
    
    # Verify strict ordering (descending for SHORT)
    assert take_profits[0] > take_profits[1] > take_profits[2], "SHORT TP ordering failed"
    return take_profits
```

**Impact:** Invalid TP levels for short positions

---

### MEDIUM PRIORITY ISSUES

#### ❌ Issue #48: Redundant Volume Confirmation Check
**Severity:** MEDIUM  
**Location:** `api/smc_analyzer.py` lines 516-521  
**Problem:**
```python
volume_confirmed = (
    current["volume"] >= avg_volume * self.ob_volume_multiplier
)
if not volume_confirmed:
    continue  # Skip but then never use volume_confirmed flag properly
```

**Recommended Fix:**
```python
# Remove early continue, use volume_confirmed in final OB creation
volume_confirmed = (
    current["volume"] >= avg_volume * self.ob_volume_multiplier
)

# Later in code...
if continuation_strength >= 2 and impulsive_exit and volume_confirmed:
    order_block = OrderBlock(...)
```

**Impact:** May allow low-volume OBs to pass through

---

#### ❌ Issue #49: Inconsistent Order Block Age Filtering
**Severity:** MEDIUM  
**Location:** `api/smc_analyzer.py` lines 583-595  
**Problem:**
```python
# Filters by age using OB_MAX_AGE_CANDLES
# But then returns last 15 blocks regardless of age
return order_blocks[-15:]
```

**Recommended Fix:**
```python
# Return all valid OBs, not just last 15
# Let calling code decide how many to use
return valid_obs  # or return valid_obs[-15:] with clear comment
```

**Impact:** May return irrelevant old order blocks

---

#### ❌ Issue #50: Inefficient FVG Alignment Score Calculation
**Severity:** MEDIUM  
**Location:** `api/smc_analyzer.py` lines 628-638, 664-673  
**Problem:**
```python
for i in range(1, len(candlesticks) - 1):
    if i >= 10:
        recent_structure = self.detect_market_structure(candlesticks[:i+2])  # Called repeatedly!
```

**Recommended Fix:**
```python
# Calculate structure once before loop
market_structure = self.detect_market_structure(candlesticks)

for i in range(1, len(candlesticks) - 1):
    if i >= 10:
        # Use pre-calculated structure
        alignment_score = 0.8 if market_structure in bullish_structures else 0.3
```

**Impact:** Performance degradation with large datasets

---

#### ❌ Issue #51: Potential Zero Division in Trade Level Calculations
**Severity:** MEDIUM  
**Location:** `api/smc_analyzer.py` lines 934, 1120  
**Problem:**
```python
min_risk_distance = current_price * 0.005  # If current_price is 0 or very small...
```

**Recommended Fix:**
```python
# Add zero/near-zero protection
if current_price <= 0:
    logging.error(f"Invalid current_price: {current_price}")
    raise ValueError("Current price must be positive")

min_risk_distance = max(current_price * 0.005, 0.01)  # Ensure minimum value
```

**Impact:** Potential division by zero errors in edge cases

---

#### ❌ Issue #52: Lack of Scaled Entry Validation
**Severity:** MEDIUM  
**Location:** `api/smc_analyzer.py` `_calculate_scaled_entries` method  
**Problem:** Generated scaled entries lack validation for:
- Entry prices ordered correctly
- Allocations sum to 100%
- Valid price ranges

**Recommended Fix:**
```python
def validate_scaled_entries(scaled_entries, direction):
    """Validate scaled entry logic and ordering"""
    if not scaled_entries:
        raise ValueError("No scaled entries generated")
    
    # Check allocations sum to 100%
    total_allocation = sum(e.allocation_percent for e in scaled_entries)
    if not (99.0 <= total_allocation <= 101.0):  # Allow 1% tolerance
        logging.warning(f"Scaled entry allocations sum to {total_allocation}%, expected 100%")
    
    # Check entry price ordering
    entry_prices = [e.entry_price for e in scaled_entries]
    if direction == "long":
        assert entry_prices == sorted(entry_prices), "LONG scaled entries not in ascending order"
    else:
        assert entry_prices == sorted(entry_prices, reverse=True), "SHORT scaled entries not in descending order"
    
    return True
```

**Impact:** Invalid scaled entry configurations

---

### LOW PRIORITY ISSUES

#### ⚠️ Issue #53: Potential Infinite Loop in get_valid_signal
**Severity:** LOW  
**Location:** `api/models.py` `SMCSignalCache.get_valid_signal` method  
**Problem:** If `SIGNAL_MIN_CONFIDENCE` is too low, signals may consistently fail validation

**Recommended Fix:**
```python
# Add iteration limit
MAX_VALIDATION_ATTEMPTS = 3
for attempt in range(MAX_VALIDATION_ATTEMPTS):
    signal = cls.query.filter(...).first()
    if validate_signal(signal):
        return signal
return None  # No valid signal after max attempts
```

**Impact:** Performance degradation if misconfigured

---

#### ⚠️ Issue #54: Hardcoded Values Should Be Configurable
**Severity:** LOW  
**Location:** Throughout `api/smc_analyzer.py`  
**Problem:** Values like `CONTINUATION_LOOKAHEAD`, `OB_MAX_AGE_CANDLES` are hardcoded

**Recommended Fix:**
```python
# Move all to SMCConfig class
class SMCConfig:
    CONTINUATION_LOOKAHEAD = 5
    OB_MAX_AGE_CANDLES = 150
    FVG_MAX_AGE_CANDLES = 150
    # ... all other configurable values
```

**Impact:** Limited flexibility for optimization

---

#### ⚠️ Issue #55: Inconsistent Timezone Handling
**Severity:** LOW  
**Location:** Various timestamp handling locations  
**Problem:** Some parts assume naive UTC, others use timezone-aware

**Recommended Fix:**
```python
# Standardize on timezone-aware UTC everywhere
def normalize_timestamp(dt):
    """Always return timezone-aware UTC datetime"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
```

**Impact:** Potential timezone bugs in comparisons

---

#### ⚠️ Issue #56: Timestamp Type Handling in get_candlestick_data
**Severity:** LOW  
**Location:** `api/smc_analyzer.py` lines 233-238  
**Problem:** Assumes all timestamps are UTC without validation

**Recommended Fix:**
```python
try:
    timestamp_value = float(kline[0]) if isinstance(kline[0], str) else kline[0]
    # Validate timestamp is in reasonable range
    if timestamp_value < 0 or timestamp_value > 9999999999999:
        raise ValueError(f"Timestamp out of range: {timestamp_value}")
    
    timestamp = datetime.fromtimestamp(timestamp_value / 1000, tz=timezone.utc)
except (ValueError, TypeError, OSError) as e:
    logging.warning(f"Invalid timestamp in kline data: {kline[0]} - {e}")
    continue
```

**Impact:** Potential incorrect timestamp conversions

---

#### ⚠️ Issue #57: ATR Floor May Be Too Low
**Severity:** LOW  
**Location:** `api/smc_analyzer.py` lines 934, 1120, 606-608  
**Problem:** `min_atr = current_price * 0.001` (0.1%) may be too small for some assets

**Recommended Fix:**
```python
# Make ATR floor configurable per asset class
class SMCConfig:
    ATR_FLOOR_PERCENTAGE = 0.001  # 0.1% default
    ATR_FLOOR_ABSOLUTE = {
        'crypto': 0.01,  # Absolute minimum for crypto
        'forex': 0.0001,  # Absolute minimum for forex
        'stocks': 0.10   # Absolute minimum for stocks
    }

# In code:
asset_type = get_asset_type(symbol)
min_atr = max(
    current_price * SMCConfig.ATR_FLOOR_PERCENTAGE,
    SMCConfig.ATR_FLOOR_ABSOLUTE.get(asset_type, 0.01)
)
```

**Impact:** May produce too-tight stop losses for certain assets

---

## Summary of Issues

| Priority | Count | Issues |
|----------|-------|--------|
| CRITICAL | 1 | #43 (Circular Dependency) |
| HIGH | 4 | #44-47 (Data consistency, TP ordering) |
| MEDIUM | 5 | #48-52 (Performance, validation) |
| LOW | 5 | #53-57 (Edge cases, configuration) |
| **TOTAL** | **15** | **All documented above** |

---

## Key Features

### Multi-Timeframe Analysis
- **Daily (1d):** Institutional bias and major liquidity zones
- **4-Hour (4h):** Intermediate structure and order flow
- **1-Hour (1h):** Tactical structure and setup confirmation  
- **15-Minute (15m):** Precise entry timing and execution

### Institutional Pattern Recognition
- Order Blocks (OB): High-volume institutional entry zones
- Fair Value Gaps (FVG): Price imbalances for institutional fills
- Liquidity Pools: Buy/sell-side liquidity targets
- Change of Character (CHoCH): Trend reversal signals
- Break of Structure (BOS): Trend continuation confirmation

### Phase 4: Institutional Scaled Entry Strategy
- **50% @ Market:** Immediate execution at current price
- **25% @ Discount (LONG) / Premium (SHORT):** Better entry on pullback
- **25% @ Deep Discount/Premium:** Optimal institutional entry zone
- Each entry has independent SL and TP levels

---

## Architecture

### Signal Generation Workflow

```
1. Data Acquisition (15m, 1h, 4h, 1d)
   ↓
2. HTF Bias Analysis (Daily → 4H structure)
   ↓
3. Multi-Timeframe Structure Detection
   ↓
4. Pattern Identification (OB, FVG, Liquidity)
   ↓
5. Signal Strength Scoring
   ↓
6. Phase 4: Institutional Scaled Entry Calculation
   ↓
7. Phase 5: Swing-Based Stop Loss (15m precision)
   ↓
8. Phase 6: Multi-TP Management (Liquidity targets)
   ↓
9. Phase 7: ATR Risk Filter
   ↓
10. Cache & Return SMCSignal
```

### Database Schema

```sql
CREATE TABLE smc_signal_cache (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    
    -- Legacy fields (nullable for backward compatibility)
    entry_price FLOAT,
    stop_loss FLOAT,
    take_profit_levels TEXT,
    
    -- Required institutional fields
    confidence FLOAT NOT NULL,
    reasoning TEXT NOT NULL,
    signal_strength VARCHAR(20) NOT NULL,
    risk_reward_ratio FLOAT NOT NULL,
    scaled_entries TEXT NOT NULL,  -- JSON array (Phase 4)
    htf_bias VARCHAR(50),
    intermediate_structure VARCHAR(100),
    execution_timeframe VARCHAR(10) DEFAULT '15m',
    
    -- Metadata
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    market_price_at_signal FLOAT NOT NULL,
    
    INDEX idx_symbol_expires (symbol, expires_at)
);
```

---

## Configuration

### SMCConfig (config.py)

```python
class SMCConfig:
    # Timeframe candle limits
    TIMEFRAME_15M_LIMIT = 400
    TIMEFRAME_1H_LIMIT = 300
    TIMEFRAME_4H_LIMIT = 100
    TIMEFRAME_1D_LIMIT = 200  # Institutional 200-day analysis
    
    # Signal cache durations (dynamic based on strength)
    SIGNAL_CACHE_DURATION_VERY_STRONG = 20  # minutes
    SIGNAL_CACHE_DURATION_STRONG = 15
    SIGNAL_CACHE_DURATION_MODERATE = 10
    SIGNAL_CACHE_DURATION_WEAK = 5
    
    # Validation thresholds
    SIGNAL_MIN_CONFIDENCE = 0.6
    MIN_CANDLESTICKS_FOR_STRUCTURE = 20
    MIN_SWING_POINTS = 2
    
    # Order Block settings
    OB_MAX_AGE_CANDLES = 150
    OB_VOLUME_MULTIPLIER = 1.0  # Dynamic tuning
    CONTINUATION_LOOKAHEAD = 5
    
    # FVG settings
    FVG_ATR_MULTIPLIER = 0.3
    MIN_CANDLESTICKS_FOR_FVG = 3
    FVG_MAX_AGE_CANDLES = 150
    
    # Swing detection
    STRUCTURE_SWING_LOOKBACK_DEFAULT = 3
    STRUCTURE_SWING_LOOKBACK_1D = 5
```

---

## Implementation Phases

### ✅ Phase 1: 15m Execution Timeframe
- Precise entry timing using 15m candles
- Swing high/low detection for SL placement
- Fine-tuned entry zones

### ✅ Phase 2: Multi-Timeframe Workflow  
- Daily → 4H → 1H → 15m analysis
- Hierarchical structure detection
- Top-down confirmation

### ✅ Phase 3: Enhanced Confidence Scoring
- 15m alignment bonus (+0.2 confidence)
- Liquidity sweep confirmation (+0.1)
- HTF POI entry bonus (+0.1)
- Eliminated triple-counting bug

### ✅ Phase 4: Institutional Scaled Entry Management
- 50% market entry
- 25% discount/premium entry
- 25% deep entry
- Independent SL/TP per entry

### ✅ Phase 5: Refined Stop-Loss (15m Swings)
- Swing-based SL placement (15m precision)
- ATR buffer for volatility
- Minimum risk distance validation
- Breakeven SL trigger logic

### ✅ Phase 6: Multi-Take Profit Management
- Liquidity pool targeting
- Risk-reward ratio optimization
- Allocation percentages (50%, 30%, 20%)
- Swing high/low natural targets

### ✅ Phase 7: ATR Risk Filter
- Volatility regime detection
- Position size adjustment
- High volatility filtering
- Risk percentage limits

---

## Usage Guide

### Generate SMC Signal

```python
from api.smc_analyzer import SMCAnalyzer

analyzer = SMCAnalyzer()
signal = analyzer.generate_trade_signal("BTCUSDT")

if signal:
    print(f"Direction: {signal.direction}")
    print(f"Confidence: {signal.confidence:.2%}")
    print(f"HTF Bias: {signal.htf_bias}")
    print(f"Structure: {signal.intermediate_structure}")
    
    # Display scaled entries
    for i, entry in enumerate(signal.scaled_entries, 1):
        print(f"\nEntry {i}: {entry.allocation_percent}% @ ${entry.entry_price}")
        print(f"  SL: ${entry.stop_loss}")
        print(f"  TPs: {[f'${tp[0]} ({tp[1]}%)' for tp in entry.take_profits]}")
```

### API Endpoint Usage

```bash
# Get single signal
curl http://localhost:5000/api/smc-signal/BTCUSDT

# Response:
{
  "symbol": "BTCUSDT",
  "direction": "long",
  "confidence": 0.75,
  "signal_strength": "strong",
  "risk_reward_ratio": 2.5,
  "htf_bias": "bullish",
  "intermediate_structure": "H4 bullish_break_of_structure, H1 bullish_change_of_character",
  "execution_timeframe": "15m",
  "scaled_entries": [
    {
      "entry_price": 45000.0,
      "allocation_percent": 50.0,
      "order_type": "market",
      "stop_loss": 44500.0,
      "take_profits": [
        {"price": 45900.0, "allocation": 50.0},
        {"price": 46800.0, "allocation": 30.0},
        {"price": 47700.0, "allocation": 20.0}
      ]
    },
    {
      "entry_price": 44800.0,
      "allocation_percent": 25.0,
      "order_type": "limit",
      "stop_loss": 44300.0,
      "take_profits": [...]
    },
    {
      "entry_price": 44600.0,
      "allocation_percent": 25.0,
      "order_type": "limit",
      "stop_loss": 44100.0,
      "take_profits": [...]
    }
  ],
  "reasoning": [
    "H4 bullish_break_of_structure",
    "H1 bullish_change_of_character",
    "Price at bullish order block",
    "Price in bullish FVG",
    "RSI oversold",
    "Buy-side liquidity swept"
  ],
  "timestamp": "2025-10-07T12:30:00Z",
  "cache_source": false
}
```

---

## API Reference

### SMCAnalyzer Class

#### Methods

- `generate_trade_signal(symbol: str) -> Optional[SMCSignal]`
- `get_multi_timeframe_data(symbol: str) -> Dict[str, List[Dict]]`
- `detect_market_structure(candlesticks: List[Dict], timeframe: str) -> MarketStructure`
- `find_order_blocks(candlesticks: List[Dict]) -> List[OrderBlock]`
- `find_fair_value_gaps(candlesticks: List[Dict]) -> List[FairValueGap]`
- `find_liquidity_pools(candlesticks: List[Dict]) -> List[LiquidityPool]`

### SMCSignal Dataclass

```python
@dataclass
class SMCSignal:
    symbol: str
    direction: str  # 'long' or 'short'
    confidence: float
    reasoning: List[str]
    signal_strength: SignalStrength
    risk_reward_ratio: float
    timestamp: datetime
    current_market_price: float
    scaled_entries: List[ScaledEntry]
    htf_bias: str
    intermediate_structure: str
    execution_timeframe: str = "15m"
```

### ScaledEntry Dataclass

```python
@dataclass
class ScaledEntry:
    entry_price: float
    allocation_percent: float
    order_type: str  # 'market' or 'limit'
    stop_loss: float
    take_profits: List[Tuple[float, float]]  # [(price, allocation), ...]
    status: str = 'pending'
```

---

## Next Steps

### Immediate Actions Required (Priority Order)

1. **CRITICAL:** Fix circular dependency (#43) - `api/models.py`
2. **HIGH:** Standardize breakeven_after handling (#44) - `api/models.py`
3. **HIGH:** Move breakeven SL calculation to runtime (#45) - `api/models.py`
4. **HIGH:** Robust TP ordering validation (#46, #47) - `api/smc_analyzer.py`
5. **MEDIUM:** Add scaled entry validation (#52) - `api/smc_analyzer.py`

### Recommended Enhancements

- Implement comprehensive unit tests for all 15 identified issues
- Add integration tests for Phase 4 institutional format
- Create monitoring dashboard for signal quality metrics
- Document edge case handling for each issue
- Add logging for all validation failures

---

**End of Documentation**
