# SMC Multi-Timeframe Institutional Analysis Implementation Plan

**Created:** December 2024  
**Status:** In Progress  
**Project:** Multi-Exchange Trading Bot - SMC Analyzer Enhancement

---

## Overview

This document outlines the comprehensive plan to upgrade the `SMCAnalyzer` class to implement institutional-style multi-timeframe crypto analysis with 15-minute execution timeframe, scaling entries, refined stop-loss management, and multi-take profit strategies.

---

## Implementation Phases

### ✅ Phase 1: Add 15m Execution Timeframe

**Objective:** Extend timeframe analysis to include 15-minute charts for precise trade execution

**Tasks:**
1. Update `SMCAnalyzer.__init__()` to include `"15m"` in `self.timeframes`
   - Current: `self.timeframes = ["1h", "4h", "1d"]`
   - Updated: `self.timeframes = ["15m", "1h", "4h", "1d"]`

2. Update `get_candlestick_data()` method
   - Add `"15m": "15m"` to `tf_map` dictionary (line ~186)
   - Ensure Binance API supports 15m interval

3. Add configuration in `config.py` → `SMCConfig` class
   - Add `TIMEFRAME_15M_LIMIT = 400` (400 candles = ~4 days of 15m data)
   - Update `RollingWindowConfig` to include 15m settings:
     - `TARGET_CANDLES_15M = 400`
     - `CLEANUP_BUFFER_15M = 100`
     - `ENABLED_15M = True`

4. Update cache TTL configuration in `config.py` → `CacheConfig.ttl_seconds()`
   - Add case for `timeframe == "15m"`: return 120 (2 minutes)

**Files Modified:**
- `api/smc_analyzer.py` (lines 108, 186)
- `config.py` (SMCConfig, RollingWindowConfig, CacheConfig)

**Testing Checklist:**
- [ ] 15m data fetches successfully from Binance
- [ ] Cache stores and retrieves 15m candles
- [ ] Rolling window properly manages 15m data

---

### ✅ Phase 2: Multi-Timeframe Analysis Workflow

**Objective:** Implement hierarchical analysis flow: Daily → H4/H1 → 15m execution

**Analysis Hierarchy:**
```
Daily (1d)  → Macro trend & liquidity targets (HTF bias)
    ↓
H4 + H1     → Intermediate structure (OBs, FVGs, BOS/CHoCH)
    ↓
15m         → Precise execution entries (must align with HTF)
```

**Tasks:**

1. Create new method: `_get_htf_bias()`
   ```python
   def _get_htf_bias(self, d1_data, h4_data) -> Dict:
       """Determine high timeframe bias from Daily and H4 structure"""
       # Analyze daily trend direction
       # Identify key liquidity levels
       # Return: {"bias": "bullish"/"bearish"/"neutral", 
       #          "confidence": 0.0-1.0,
       #          "liquidity_targets": [...]}
   ```

2. Create new method: `_get_intermediate_structure()`
   ```python
   def _get_intermediate_structure(self, h1_data, h4_data) -> Dict:
       """Analyze H4/H1 for order blocks, FVGs, structure shifts"""
       # Find order blocks on H4/H1
       # Identify unmitigated FVGs
       # Detect BOS/CHoCH
       # Return: {"order_blocks": [...], "fvgs": [...], 
       #          "structure": "...", "poi_levels": [...]}
   ```

3. Create new method: `_get_execution_signal_15m()`
   ```python
   def _get_execution_signal_15m(self, m15_data, htf_bias, intermediate_structure) -> Dict:
       """Generate precise 15m execution signal aligned with HTF"""
       # Check 15m structure aligns with HTF bias
       # Find 15m swing points for entry
       # Calculate precise entry, SL, TP levels
       # Return: {"signal": "long"/"short"/None, 
       #          "entry": float, "sl": float, 
       #          "alignment_score": 0.0-1.0}
   ```

4. Update `generate_trade_signal()` method
   - Fetch all 4 timeframes: 15m, 1h, 4h, 1d
   - Call `_get_htf_bias(d1_data, h4_data)` first
   - If HTF bias exists, call `_get_intermediate_structure(h1_data, h4_data)`
   - If intermediate structure valid, call `_get_execution_signal_15m(m15_data, htf_bias, intermediate_structure)`
   - Only proceed to entry if 15m aligns with HTF bias

5. Update `get_multi_timeframe_data()` method
   - Include `"15m"` in fetched timeframes
   - Adjust limits: {"15m": 400, "1h": 300, "4h": 100, "1d": 50}

**Files Modified:**
- `api/smc_analyzer.py` (new methods + updates to generate_trade_signal)

**Testing Checklist:**
- [ ] HTF bias correctly identified from D1/H4
- [ ] Intermediate structure properly detected
- [ ] 15m execution signals align with HTF
- [ ] Signals rejected when 15m conflicts with HTF

---

### ✅ Phase 3: Enhanced Confidence Scoring

**Objective:** Refine confidence calculation with 15m alignment bonus and liquidity sweep confluence

**Confidence Scoring Rules:**
- Base confidence from existing logic (0.5 - 0.8)
- **+0.2 bonus** if 15m perfectly aligns with HTF bias
- **-0.3 penalty** if 15m conflicts with HTF bias (reject trade)
- **+0.1 bonus** if liquidity sweep confirmed (optional confluence)
- **+0.1 bonus** if 15m enters from H1/H4 OB/FVG

**Tasks:**

1. Create new method: `_calculate_15m_alignment_score()`
   ```python
   def _calculate_15m_alignment_score(self, m15_structure, htf_bias) -> float:
       """Calculate how well 15m structure aligns with HTF bias"""
       # Compare 15m trend with HTF bias direction
       # Check 15m swing structure supports HTF direction
       # Return: alignment score (0.0 = conflict, 0.5 = neutral, 1.0 = perfect)
   ```

2. Update `_calculate_signal_strength_and_confidence()` method
   - Add parameter: `m15_alignment_score: float`
   - Add 15m alignment logic:
     ```python
     if m15_alignment_score >= 0.8:
         confidence += 0.2  # Perfect alignment bonus
     elif m15_alignment_score < 0.3:
         confidence -= 0.3  # Conflict penalty (likely reject)
     ```
   - Add liquidity sweep bonus logic (optional):
     ```python
     if liquidity_swept and sweep_confirmed:
         confidence += 0.1  # Liquidity confluence bonus
     ```
   - Add OB/FVG entry bonus:
     ```python
     if entry_from_htf_poi:
         confidence += 0.1  # Entering from HTF POI
     ```

3. Update signal rejection logic in `generate_trade_signal()`
   - Reject if `m15_alignment_score < 0.3` (conflict threshold)
   - Log reason: "15m structure conflicts with HTF bias"

**Files Modified:**
- `api/smc_analyzer.py`

**Testing Checklist:**
- [ ] Confidence increases with 15m alignment
- [ ] Trades rejected when 15m conflicts
- [ ] Liquidity sweep adds appropriate bonus
- [ ] OB/FVG entry bonus applied correctly

---

### ✅ Phase 4: Scaling Entry Management

**Objective:** Implement partial at-market entry with limit orders in OB/FVG zones

**Entry Allocation:**
- **50%** at market (immediate execution)
- **25%** at first FVG/OB depth level
- **25%** at second FVG/OB depth level (deeper retracement)

**Tasks:**

1. Create new dataclass: `ScaledEntry`
   ```python
   @dataclass
   class ScaledEntry:
       entry_price: float
       allocation_percent: float  # 50, 25, 25
       order_type: str  # 'market' or 'limit'
       stop_loss: float
       take_profits: List[Tuple[float, float]]  # [(price, allocation), ...]
       status: str  # 'pending', 'filled', 'cancelled'
   ```

2. Create new method: `_calculate_scaled_entries()`
   ```python
   def _calculate_scaled_entries(
       self, 
       current_price: float,
       direction: str,
       order_blocks: List[OrderBlock],
       fvgs: List[FairValueGap]
   ) -> List[ScaledEntry]:
       """Calculate 3-level scaled entry strategy"""
       # Entry 1: 50% at market (current price)
       # Entry 2: 25% at nearest OB/FVG level (0.3-0.5% better)
       # Entry 3: 25% at deeper OB/FVG level (0.8-1.2% better)
       # Return list of ScaledEntry objects
   ```

3. Create new method: `_calculate_entry_specific_sl()`
   ```python
   def _calculate_entry_specific_sl(
       self,
       entry_price: float,
       direction: str,
       m15_swing_levels: List[float]
   ) -> float:
       """Calculate stop-loss specific to each entry level"""
       # Use 15m swing high/low based on direction
       # Add ATR buffer if configured
       # Return SL price for this specific entry
   ```

4. Update `SMCSignal` dataclass
   - Add field: `scaled_entries: Optional[List[ScaledEntry]] = None`
   - Keep existing `entry_price` as weighted average for backward compatibility

5. Update `generate_trade_signal()` method
   - After determining trade direction, call `_calculate_scaled_entries()`
   - Generate individual SL for each entry level
   - Include scaled_entries in returned signal

6. Add configuration to `config.py` → `TradingConfig`
   ```python
   # Scaling Entry Configuration
   USE_SCALED_ENTRIES = True
   SCALED_ENTRY_ALLOCATIONS = [50, 25, 25]  # Market, Limit1, Limit2
   SCALED_ENTRY_DEPTH_1 = 0.004  # 0.4% better price for Limit1
   SCALED_ENTRY_DEPTH_2 = 0.010  # 1.0% better price for Limit2
   ```

**Files Modified:**
- `api/smc_analyzer.py` (new dataclass, new methods, signal updates)
- `config.py` (TradingConfig additions)

**Testing Checklist:**
- [ ] Three entry levels calculated correctly
- [ ] Allocations sum to 100%
- [ ] Limit orders placed at valid OB/FVG levels
- [ ] Each entry has appropriate individual SL

---

### ✅ Phase 5: Refined Stop-Loss with 15m Swings

**Objective:** Use 15m swing highs/lows for tighter, more precise stop-losses

**Stop-Loss Logic:**
- **Long trades:** SL = last 15m swing low - ATR buffer
- **Short trades:** SL = last 15m swing high + ATR buffer
- **ATR buffer:** Optional multiplier (default 0.5x ATR)

**Tasks:**

1. Create new method: `_find_15m_swing_levels()`
   ```python
   def _find_15m_swing_levels(self, m15_data: List[Dict]) -> Dict:
       """Find recent swing highs and lows on 15m timeframe"""
       # Identify swing highs (candle high > 2 candles before & after)
       # Identify swing lows (candle low < 2 candles before & after)
       # Return: {"swing_highs": [prices], "swing_lows": [prices],
       #          "last_swing_high": float, "last_swing_low": float}
   ```

2. Create new method: `_calculate_refined_sl_with_atr()`
   ```python
   def _calculate_refined_sl_with_atr(
       self,
       direction: str,
       swing_levels: Dict,
       atr_value: float,
       atr_buffer_multiplier: float = 0.5
   ) -> float:
       """Calculate stop-loss using 15m swings + ATR buffer"""
       if direction == "long":
           sl = swing_levels["last_swing_low"] - (atr_value * atr_buffer_multiplier)
       else:  # short
           sl = swing_levels["last_swing_high"] + (atr_value * atr_buffer_multiplier)
       return sl
   ```

3. Update `generate_trade_signal()` method
   - After fetching 15m data, call `_find_15m_swing_levels(m15_data)`
   - Calculate ATR on 15m timeframe
   - Call `_calculate_refined_sl_with_atr()` for stop-loss
   - Use this SL for all scaled entries (or adjust per entry if desired)

4. Add configuration to `config.py` → `TradingConfig`
   ```python
   # Stop-Loss Configuration
   USE_15M_SWING_SL = True
   SL_ATR_BUFFER_MULTIPLIER = 0.5  # 0.5x ATR buffer
   SL_MIN_DISTANCE_PERCENT = 0.5  # Minimum 0.5% SL distance
   ```

**Files Modified:**
- `api/smc_analyzer.py`
- `config.py` (TradingConfig)

**Testing Checklist:**
- [ ] 15m swing levels detected correctly
- [ ] SL respects ATR buffer
- [ ] SL not too tight (minimum distance enforced)
- [ ] SL properly placed for both long and short

---

### ✅ Phase 6: Multi-Take Profit Management

**Objective:** Implement scaled exit strategy with R:R-based take profits

**Take Profit Levels:**
- **TP1:** 1R (100% risk amount as profit)
- **TP2:** 2R (200% risk amount as profit)
- **TP3:** Liquidity cluster / HTF OB target
- **Allocations:** Configurable per TP (default: 40%, 30%, 30%)

**Tasks:**

1. Create new method: `_calculate_rr_based_take_profits()`
   ```python
   def _calculate_rr_based_take_profits(
       self,
       entry_price: float,
       stop_loss: float,
       direction: str,
       liquidity_targets: List[float]
   ) -> List[Tuple[float, float]]:
       """Calculate take profit levels based on R:R ratios"""
       risk_amount = abs(entry_price - stop_loss)
       
       # TP1: 1R
       tp1_price = entry_price + risk_amount if direction == "long" else entry_price - risk_amount
       
       # TP2: 2R
       tp2_price = entry_price + (2 * risk_amount) if direction == "long" else entry_price - (2 * risk_amount)
       
       # TP3: Nearest liquidity target beyond 2R, or 3R
       tp3_price = self._find_liquidity_target(entry_price, direction, liquidity_targets, risk_amount * 2)
       if not tp3_price:
           tp3_price = entry_price + (3 * risk_amount) if direction == "long" else entry_price - (3 * risk_amount)
       
       # Return: [(tp1_price, 40%), (tp2_price, 30%), (tp3_price, 30%)]
       return [(tp1_price, 40), (tp2_price, 30), (tp3_price, 30)]
   ```

2. Create new method: `_find_liquidity_target()`
   ```python
   def _find_liquidity_target(
       self,
       entry_price: float,
       direction: str,
       liquidity_pools: List[LiquidityPool],
       min_distance: float
   ) -> Optional[float]:
       """Find nearest liquidity level beyond minimum distance"""
       # Filter pools by direction and distance
       # Return nearest valid liquidity price
   ```

3. Update `ScaledEntry` dataclass (from Phase 4)
   - Ensure `take_profits: List[Tuple[float, float]]` stores TP prices and allocations
   - Add field: `tp_statuses: List[str]` to track which TPs are hit

4. Update `generate_trade_signal()` method
   - After calculating SL, call `_calculate_rr_based_take_profits()`
   - Validate TP allocations sum to 100%
   - Include TPs in `SMCSignal` and `ScaledEntry` objects

5. Create new method: `_should_trail_stop_after_tp1()`
   ```python
   def _should_trail_stop_after_tp1(self, config: Dict) -> bool:
       """Determine if trailing stop should activate after TP1"""
       # Check if TP1 was hit
       # Check if trailing stop is enabled in config
       # Return True/False
   ```

6. Add configuration to `config.py` → `TradingConfig`
   ```python
   # Multi-Take Profit Configuration
   USE_RR_BASED_TPS = True
   TP_ALLOCATIONS = [40, 30, 30]  # TP1, TP2, TP3 percentages
   TP_RR_RATIOS = [1.0, 2.0, 3.0]  # R:R for TP1, TP2, TP3
   ENABLE_TRAILING_AFTER_TP1 = True
   TRAILING_STOP_PERCENT = 2.0  # 2% trailing stop
   ```

**Files Modified:**
- `api/smc_analyzer.py`
- `config.py` (TradingConfig)

**Testing Checklist:**
- [ ] TP1 at 1R calculated correctly
- [ ] TP2 at 2R calculated correctly
- [ ] TP3 targets liquidity or 3R
- [ ] Allocations sum to 100%
- [ ] Trailing stop logic functional (optional)

---

### ✅ Phase 7: Execution Risk Filter with ATR

**Objective:** Filter out low-volatility, choppy market conditions using ATR threshold

**Risk Filter Logic:**
- Calculate ATR(14) on 15m and H1 timeframes
- Skip trade if ATR below minimum threshold
- Optional: Adjust position size based on ATR (higher ATR = smaller size)

**Tasks:**

1. Update existing `calculate_atr()` method
   - Ensure it works correctly with 15m timeframe
   - Add logging for ATR values

2. Create new method: `_check_atr_filter()`
   ```python
   def _check_atr_filter(
       self,
       m15_data: List[Dict],
       h1_data: List[Dict],
       current_price: float
   ) -> Dict:
       """Check if ATR meets minimum volatility requirements"""
       atr_15m = self.calculate_atr(m15_data, period=14)
       atr_h1 = self.calculate_atr(h1_data, period=14)
       
       # Calculate ATR as percentage of price
       atr_15m_percent = (atr_15m / current_price) * 100
       atr_h1_percent = (atr_h1 / current_price) * 100
       
       # Check against thresholds
       min_atr_15m = TradingConfig.MIN_ATR_15M_PERCENT
       min_atr_h1 = TradingConfig.MIN_ATR_H1_PERCENT
       
       passes_filter = (atr_15m_percent >= min_atr_15m and 
                        atr_h1_percent >= min_atr_h1)
       
       return {
           "passes": passes_filter,
           "atr_15m": atr_15m,
           "atr_h1": atr_h1,
           "atr_15m_percent": atr_15m_percent,
           "atr_h1_percent": atr_h1_percent,
           "reason": f"ATR too low: 15m={atr_15m_percent:.2f}%, H1={atr_h1_percent:.2f}%"
                     if not passes_filter else "ATR filter passed"
       }
   ```

3. Create new method: `_calculate_dynamic_position_size()` (optional)
   ```python
   def _calculate_dynamic_position_size(
       self,
       base_size: float,
       atr_percent: float
   ) -> float:
       """Adjust position size based on ATR volatility"""
       # Higher ATR = smaller position size
       # Lower ATR = larger position size (but still above threshold)
       # Return: adjusted position size multiplier (0.5 - 1.5)
       if atr_percent > 3.0:  # High volatility
           return 0.7  # Reduce size to 70%
       elif atr_percent < 1.5:  # Low volatility
           return 1.2  # Increase size to 120%
       return 1.0  # Normal size
   ```

4. Update `generate_trade_signal()` method
   - After fetching 15m and H1 data, call `_check_atr_filter()`
   - If filter fails, reject signal with reason
   - If filter passes, optionally calculate dynamic position size
   - Add ATR info to signal diagnostics

5. Add configuration to `config.py` → `TradingConfig`
   ```python
   # ATR Risk Filter Configuration
   USE_ATR_FILTER = True
   MIN_ATR_15M_PERCENT = 0.8  # Minimum 0.8% ATR on 15m
   MIN_ATR_H1_PERCENT = 1.2   # Minimum 1.2% ATR on H1
   USE_DYNAMIC_POSITION_SIZING = False  # Optional feature
   ```

**Files Modified:**
- `api/smc_analyzer.py`
- `config.py` (TradingConfig)

**Testing Checklist:**
- [ ] ATR calculated correctly on 15m and H1
- [ ] Low ATR properly rejects signals
- [ ] High ATR signals pass through
- [ ] Dynamic position sizing works (if enabled)
- [ ] Rejection reasons logged clearly

---

## Implementation Summary

### Complete Workflow After All Phases

```
1. Fetch multi-timeframe data (15m, 1h, 4h, 1d)
   ↓
2. Check ATR filter (Phase 7)
   - If fails → Reject (low volatility)
   ↓
3. Determine HTF bias from D1 + H4 (Phase 2)
   - Identify macro trend
   - Find liquidity targets
   ↓
4. Analyze intermediate structure H4 + H1 (Phase 2)
   - Find order blocks, FVGs
   - Detect BOS/CHoCH
   ↓
5. Check 15m execution signal (Phase 2)
   - Verify 15m aligns with HTF bias
   - Find 15m swing levels for precise entry
   ↓
6. Calculate confidence score (Phase 3)
   - Base confidence from structure
   - +0.2 if 15m perfectly aligned
   - +0.1 if liquidity sweep confirmed
   - +0.1 if entering from HTF OB/FVG
   - Reject if 15m conflicts (< 0.3 alignment)
   ↓
7. Calculate scaled entries (Phase 4)
   - 50% at market
   - 25% at first OB/FVG depth
   - 25% at deeper OB/FVG depth
   ↓
8. Calculate refined stop-loss (Phase 5)
   - Use 15m swing levels
   - Add ATR buffer
   - Ensure minimum distance
   ↓
9. Calculate R:R-based take profits (Phase 6)
   - TP1: 1R (40% allocation)
   - TP2: 2R (30% allocation)
   - TP3: Liquidity target or 3R (30% allocation)
   ↓
10. Generate final SMCSignal with all components
    - Return signal or rejection with diagnostics
```

---

## Configuration Summary

All new configuration options to be added to `config.py`:

```python
class SMCConfig:
    # Phase 1: 15m timeframe
    TIMEFRAME_15M_LIMIT = 400  # 400 candles = ~4 days

class RollingWindowConfig:
    # Phase 1: 15m rolling window
    TARGET_CANDLES_15M = 400
    CLEANUP_BUFFER_15M = 100
    ENABLED_15M = True

class CacheConfig:
    # Phase 1: 15m cache TTL
    # Add to ttl_seconds() method: elif timeframe == "15m": return 120

class TradingConfig:
    # Phase 4: Scaling entries
    USE_SCALED_ENTRIES = True
    SCALED_ENTRY_ALLOCATIONS = [50, 25, 25]
    SCALED_ENTRY_DEPTH_1 = 0.004  # 0.4%
    SCALED_ENTRY_DEPTH_2 = 0.010  # 1.0%
    
    # Phase 5: Refined stop-loss
    USE_15M_SWING_SL = True
    SL_ATR_BUFFER_MULTIPLIER = 0.5
    SL_MIN_DISTANCE_PERCENT = 0.5
    
    # Phase 6: Multi-take profit
    USE_RR_BASED_TPS = True
    TP_ALLOCATIONS = [40, 30, 30]
    TP_RR_RATIOS = [1.0, 2.0, 3.0]
    ENABLE_TRAILING_AFTER_TP1 = True
    TRAILING_STOP_PERCENT = 2.0
    
    # Phase 7: ATR risk filter
    USE_ATR_FILTER = True
    MIN_ATR_15M_PERCENT = 0.8
    MIN_ATR_H1_PERCENT = 1.2
    USE_DYNAMIC_POSITION_SIZING = False
```

---

## Testing Strategy

### Unit Tests
- Test each new method independently
- Validate calculations (R:R, ATR, swing levels)
- Test edge cases (no liquidity targets, missing data)

### Integration Tests
- Test complete workflow end-to-end
- Verify signal generation with all phases
- Test rejection scenarios

### Live Testing
- Paper trading with new signals
- Monitor for false signals or missed opportunities
- Validate confidence scores match expectations

---

## Rollback Plan

If issues occur during implementation:
1. Each phase is independent - can rollback individual phases
2. Configuration flags allow disabling features:
   - `USE_SCALED_ENTRIES = False`
   - `USE_15M_SWING_SL = False`
   - `USE_RR_BASED_TPS = False`
   - `USE_ATR_FILTER = False`
3. Git commits per phase for easy revert

---

## Progress Tracking

### Phase Status
- [x] Phase 1: Add 15m Timeframe ✅ COMPLETE
- [ ] Phase 2: Multi-Timeframe Workflow
- [ ] Phase 3: Enhanced Confidence Scoring
- [ ] Phase 4: Scaling Entry Management
- [ ] Phase 5: Refined Stop-Loss
- [ ] Phase 6: Multi-Take Profit Management
- [ ] Phase 7: ATR Risk Filter

### Current Phase: **Phase 2 - Multi-Timeframe Analysis Workflow**
### Last Completed Step: **Phase 1 complete - 15m timeframe added to config and analyzer**
### Next Step: **Create _get_htf_bias() method for Daily/H4 bias analysis**

---

## Phase 1 Completion Notes

**Completed Changes:**
- ✅ Updated `SMCAnalyzer.__init__()` to include "15m" in timeframes list
- ✅ Added "15m": "15m" to `tf_map` in `get_candlestick_data()`
- ✅ Added `TIMEFRAME_15M_LIMIT = 400` to `SMCConfig`
- ✅ Added 15m configuration to `RollingWindowConfig`:
  - TARGET_CANDLES_15M = 400
  - CLEANUP_BUFFER_15M = 100
  - ENABLED_15M = True
- ✅ Updated all RollingWindowConfig methods to handle 15m
- ✅ Added `KLINES_15M_CACHE_TTL = 1` to `CacheConfig`
- ✅ Updated `CacheConfig.ttl_seconds()` to handle 15m with 60-second TTL
- ✅ Updated cache TTL logic in `get_candlestick_data()` for 15m open candles

---

## Notes

- All changes maintain backward compatibility
- Existing signals continue to work during implementation
- Features can be toggled via configuration flags
- Comprehensive logging added for debugging
- Performance impact monitored (additional API calls for 15m data)

---

## References

- Original SMC Analyzer: `api/smc_analyzer.py`
- Configuration file: `config.py`
- Related documentation: `SMC_Analyzer_Settings_Documentation.md`
