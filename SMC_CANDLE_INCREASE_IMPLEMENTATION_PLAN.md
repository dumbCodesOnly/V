# SMC Candle Increase Implementation Plan

**Date Created:** October 15, 2025  
**Objective:** Increase historical candle storage from 200 4H/300 1H to at least 400 4H/600 1H candles for improved SMC analysis accuracy

## Current State

### Current Configuration
- **4H Candles:** 200 target (~33 days)
- **1H Candles:** 300 target (~12.5 days)
- **15M Candles:** 400 target (~4 days)
- **1D Candles:** 200 target (~6.5 months)

### Current Cleanup Buffers
- **4H:** Cleanup starts at 300 candles (200 target + 100 buffer)
- **1H:** Cleanup starts at 450 candles (300 target + 150 buffer)
- **15M:** Cleanup starts at 500 candles (400 target + 100 buffer)
- **1D:** Cleanup starts at 300 candles (200 target + 100 buffer)

## New Requirements

### Target Configuration
- **4H Candles:** At least 400 (~66 days / 2.2 months)
- **1H Candles:** At least 600 (~25 days)
- **15M Candles:** Keep at 400 (sufficient for execution)
- **1D Candles:** Keep at 200 (sufficient for HTF bias)

### Recommended New Cleanup Buffers
- **4H:** Cleanup starts at 500 candles (400 target + 100 buffer)
- **1H:** Cleanup starts at 750 candles (600 target + 150 buffer)
- **15M:** Keep at 500 candles (400 target + 100 buffer)
- **1D:** Keep at 300 candles (200 target + 100 buffer)

---

## Implementation Plan - Step by Step

### 1. Configuration Updates (config.py)

#### 1.1 Update SMCConfig Timeframe Limits
**File:** `config.py`  
**Section:** `SMCConfig` class (lines ~413-416)

**Changes:**
```python
# BEFORE:
TIMEFRAME_1H_LIMIT = 300  # 300 candles = ~12.5 days
TIMEFRAME_4H_LIMIT = 200  # 200 candles = ~33 days

# AFTER:
TIMEFRAME_1H_LIMIT = 600  # 600 candles = ~25 days of hourly data for enhanced structure analysis
TIMEFRAME_4H_LIMIT = 400  # 400 candles = ~66 days of 4h data for institutional intermediate structure
```

#### 1.2 Update RollingWindowConfig Targets
**File:** `config.py`  
**Section:** `RollingWindowConfig` class (lines ~433-436)

**Changes:**
```python
# BEFORE:
TARGET_CANDLES_1H = 300   # Target: 300 hourly candles (~12.5 days)
TARGET_CANDLES_4H = 200   # Target: 200 4-hour candles (~33 days)

# AFTER:
TARGET_CANDLES_1H = 600   # Target: 600 hourly candles (~25 days for enhanced SMC patterns)
TARGET_CANDLES_4H = 400   # Target: 400 4-hour candles (~66 days for institutional structure)
```

#### 1.3 Update Cleanup Buffers
**File:** `config.py`  
**Section:** `RollingWindowConfig` class (lines ~440-443)

**Changes:**
```python
# BEFORE:
CLEANUP_BUFFER_1H = 150   # Only cleanup when we have 450+ hourly candles
CLEANUP_BUFFER_4H = 100   # Only cleanup when we have 300+ 4h candles

# AFTER:
CLEANUP_BUFFER_1H = 150   # Only cleanup when we have 750+ hourly candles (600 + 150 buffer)
CLEANUP_BUFFER_4H = 100   # Only cleanup when we have 500+ 4h candles (400 + 100 buffer)
```

#### 1.4 Review get_max_candles() Method
**File:** `config.py`  
**Section:** `RollingWindowConfig.get_max_candles()` (lines ~481+)

**Action:** Verify this method correctly calculates: `TARGET + SAFETY_MARGIN`
- For 1H: 600 + 20 = 620 max candles after cleanup
- For 4H: 400 + 20 = 420 max candles after cleanup

---

### 2. Order Block & FVG Lookback Updates

#### 2.1 Update OB Max Age
**File:** `config.py`  
**Section:** `SMCConfig` class

**Current:**
```python
OB_MAX_AGE_CANDLES = 200  # Maximum age for OB validity
```

**Recommended Change:**
```python
OB_MAX_AGE_CANDLES = 300  # Increased from 200 to utilize extended 4H data (400 candles available)
```

**Rationale:** With 400 4H candles available, we can look back further for institutional order blocks while maintaining a conservative 75% utilization ratio (300/400).

#### 2.2 Update FVG Max Age
**File:** `config.py`  
**Section:** `SMCConfig` class

**Current:**
```python
FVG_MAX_AGE_CANDLES = 200  # Maximum age for FVG validity
```

**Recommended Change:**
```python
FVG_MAX_AGE_CANDLES = 300  # Increased from 200 to utilize extended 4H data (400 candles available)
```

**Rationale:** Extended lookback allows detection of older institutional fair value gaps that may still be relevant for price reactions.

---

### 3. BOS & CHOCH Detection Updates

#### 3.1 Current BOS/CHOCH Configuration
**File:** `config.py`  
**Section:** `SMCConfig` class

**Current Swing Detection Lookback:**
```python
SWING_LOOKBACK_15M = 3   # 15m: tight swings for precise execution
SWING_LOOKBACK_1H = 5    # 1h: standard swing detection
SWING_LOOKBACK_4H = 10   # 4h: broader swings (designed for 200-candle context)
SWING_LOOKBACK_1D = 15   # 1d: institutional swings (200-candle context)
```

**Current Structure Analysis Lookback:**
```python
STRUCTURE_SWING_LOOKBACK_1D = 7      # Daily: analyze 7 swing points
STRUCTURE_SWING_LOOKBACK_DEFAULT = 3  # Other timeframes: 3 swing points
```

#### 3.2 How BOS/CHOCH Detection Works

**Break of Structure (BOS):**
- **Bullish BOS:** Recent high breaks previous high AND recent low breaks previous low (upward)
- **Bearish BOS:** Recent low breaks previous low AND recent high breaks previous high (downward)
- **Purpose:** Confirms trend continuation with institutional backing

**Change of Character (CHoCH):**
- **Bullish CHoCH:** High trend is "down" but low trend is "up" (reversal signal)
- **Bearish CHoCH:** High trend is "up" but low trend is "down" (reversal signal)
- **Purpose:** Identifies potential trend reversals early

**Current Limitation:**
- 4H analysis with only 200 candles = limited swing point history
- 1H analysis with only 300 candles = may miss broader structure shifts
- Increased candles will capture more institutional swing points for accurate BOS/CHoCH detection

#### 3.3 Recommended Updates for Increased Klines

##### 3.3.1 Update 1H Swing Lookback
**File:** `config.py`  
**Section:** `SMCConfig`

**Current:**
```python
SWING_LOOKBACK_1H = 5    # 1h: standard swing detection
```

**Recommended Change:**
```python
SWING_LOOKBACK_1H = 8    # 1h: enhanced swing detection with 600-candle context (was 5 for 300 candles)
```

**Rationale:** With 600 1H candles available (2x increase), we can look back further for more significant swing points without noise. Increasing from 5 to 8 provides ~60% more context while remaining conservative.

##### 3.3.2 Update 4H Swing Lookback
**File:** `config.py`  
**Section:** `SMCConfig`

**Current:**
```python
SWING_LOOKBACK_4H = 10   # 4h: broader swings for institutional intermediate structure (200-candle context)
```

**Recommended Change:**
```python
SWING_LOOKBACK_4H = 15   # 4h: institutional swings with 400-candle context (was 10 for 200 candles)
```

**Rationale:** With 400 4H candles (2x increase), increasing lookback from 10 to 15 allows detection of broader institutional swing points across the extended 66-day period. This captures weekly and bi-weekly patterns more reliably.

##### 3.3.3 Update Structure Analysis Lookback
**File:** `config.py`  
**Section:** `SMCConfig`

**Current:**
```python
STRUCTURE_SWING_LOOKBACK_DEFAULT = 3  # Other timeframes: analyze 3 recent swing points
```

**Recommended Change:**
```python
STRUCTURE_SWING_LOOKBACK_1H = 5      # 1h: analyze 5 swing points with 600-candle context
STRUCTURE_SWING_LOOKBACK_4H = 5      # 4h: analyze 5 swing points with 400-candle context
STRUCTURE_SWING_LOOKBACK_DEFAULT = 3  # 15m and others: keep at 3 swing points
```

**Rationale:** With extended candle data, analyzing 5 recent swing points (vs 3) provides better BOS/CHoCH detection by examining more structural patterns. This reduces false signals from short-term noise.

#### 3.4 Impact on BOS/CHOCH Detection

**Before (Current Configuration):**
- 4H BOS/CHOCH: Based on 10-candle swing lookback over 200 candles (~33 days)
- 1H BOS/CHOCH: Based on 5-candle swing lookback over 300 candles (~12.5 days)
- Structure analysis: 3 swing points (all timeframes except daily)

**After (New Configuration):**
- 4H BOS/CHOCH: Based on 15-candle swing lookback over 400 candles (~66 days)
- 1H BOS/CHOCH: Based on 8-candle swing lookback over 600 candles (~25 days)
- Structure analysis: 5 swing points for 1H/4H, 7 for daily, 3 for 15m

**Expected Improvements:**
1. **Fewer False Signals:** More data = better swing point validation
2. **Earlier Detection:** Extended lookback catches structure shifts sooner
3. **Better Confluence:** More swing points allow multi-level structure confirmation
4. **Institutional Alignment:** 66-day 4H data captures institutional weekly/monthly patterns

#### 3.5 Update detect_market_structure() Method
**File:** `api/smc_analyzer.py`  
**Method:** `detect_market_structure()`

**Current Logic (line 535):**
```python
# Determine swing lookback based on timeframe
lookback = SMCConfig.STRUCTURE_SWING_LOOKBACK_1D if timeframe == "1d" else SMCConfig.STRUCTURE_SWING_LOOKBACK_DEFAULT
```

**Recommended Update:**
```python
# Determine swing lookback based on timeframe - use extended lookback for 1H and 4H
if timeframe == "1d":
    lookback = SMCConfig.STRUCTURE_SWING_LOOKBACK_1D
elif timeframe == "4h":
    lookback = SMCConfig.STRUCTURE_SWING_LOOKBACK_4H
elif timeframe == "1h":
    lookback = SMCConfig.STRUCTURE_SWING_LOOKBACK_1H
else:
    lookback = SMCConfig.STRUCTURE_SWING_LOOKBACK_DEFAULT
```

**Action Required:** Modify the method to use timeframe-specific lookback values for improved BOS/CHoCH accuracy.

---

### 4. Kline Batch Fetch Updates

#### 3.1 Initial Population Logic
**File:** `api/klines_background_worker_backup.py`  
**Method:** `_get_required_initial_candles()` and `_populate_initial_data()`

**Changes Required:**
- The `_get_required_initial_candles()` method uses `SMCConfig.TIMEFRAME_*_LIMIT` values
- Once config.py is updated, this will automatically fetch:
  - 600 candles for 1H timeframe
  - 400 candles for 4H timeframe

**Action Items:**
1. Verify Binance API can return 600 candles in a single request (check API limit is 1000)
2. Test `_fetch_binance_klines()` with new limits
3. Ensure batch processing can handle larger datasets

#### 3.2 Incremental Updates
**File:** `api/klines_background_worker_backup.py`  
**Method:** `update_symbol_data()`

**Current Behavior:**
- Fetches incremental candles based on last cached timestamp
- No changes needed - logic is already dynamic

**Verification Needed:**
- Ensure the update logic correctly fills gaps when migrating from old to new limits

---

### 4. Circuit Breaker Adjustments

#### 4.1 Review API Call Volume Impact
**File:** `api/circuit_breaker.py`

**Current Configuration:**
```python
DEFAULT_FAILURE_THRESHOLD = 8  # Opens circuit after 8 failures
DEFAULT_RECOVERY_TIMEOUT = 120  # 120 seconds recovery timeout
```

**Analysis:**
- Fetching 600 vs 300 1H candles = same number of API calls (single request)
- Fetching 400 vs 200 4H candles = same number of API calls (single request)
- **No changes needed** - circuit breaker thresholds remain appropriate

**Action:** Monitor circuit breaker metrics after deployment for any anomalies

---

### 5. Database Table Considerations

#### 5.1 Schema Review
**File:** `api/models.py`  
**Table:** `klines_cache`

**Current Schema:**
- `id` (Primary Key)
- `symbol`, `timeframe`, `timestamp` (Composite Index)
- OHLCV data (open, high, low, close, volume)
- `created_at`, `expires_at`, `is_complete`

**Changes Required:** ✅ **NONE**
- Schema supports unlimited candles per symbol/timeframe
- Indexes are optimized for range queries
- Unique constraint prevents duplicates

#### 5.2 Storage Impact Estimation
**Current Storage (per symbol):**
- 4H: 200 candles × ~100 bytes = ~20 KB
- 1H: 300 candles × ~100 bytes = ~30 KB
- Total: ~50 KB per symbol

**New Storage (per symbol):**
- 4H: 400 candles × ~100 bytes = ~40 KB
- 1H: 600 candles × ~100 bytes = ~60 KB
- Total: ~100 KB per symbol

**Impact:** 2x storage increase per symbol (acceptable for PostgreSQL)

#### 5.3 Index Performance
**Action:** Monitor query performance on:
- `idx_klines_symbol_timeframe_timestamp`
- `idx_klines_expires`
- `idx_klines_symbol_timeframe_expires`

**Expected:** No degradation (indexes scale logarithmically)

---

### 6. Cleanup Worker Updates

#### 6.1 Rolling Window Cleanup
**File:** `api/models.py` (KlinesCache class)  
**Method:** `cleanup_all_rolling_windows()`

**Current Logic:**
```python
# Uses RollingWindowConfig values:
# - TARGET_CANDLES_*
# - CLEANUP_BUFFER_*
# - SAFETY_MARGIN
```

**Changes Required:** ✅ **NONE**
- Logic is configuration-driven
- Automatically uses updated config values
- Cleanup triggers when: count > (TARGET + BUFFER)
- Keeps: (TARGET + SAFETY_MARGIN) candles

**Verification:**
1. After config update, 1H cleanup triggers at 750+ candles (600 + 150)
2. After cleanup, keeps 620 candles (600 + 20)
3. After config update, 4H cleanup triggers at 500+ candles (400 + 100)
4. After cleanup, keeps 420 candles (400 + 20)

#### 6.2 Expiration-Based Cleanup
**File:** `api/models.py` (KlinesCache class)  
**Method:** `cleanup_old_data(days_to_keep)`

**Current:**
```python
KLINES_DATA_RETENTION_DAYS = 21  # Keep klines for 21 days
```

**Recommended Change:**
```python
KLINES_DATA_RETENTION_DAYS = 90  # Increased to 90 days to support 66-day 4H requirement
```

**Rationale:** 
- 400 4H candles = 66 days of data
- Need buffer beyond 66 days to prevent premature deletion
- 90 days provides safe margin (66 + 24 days buffer)

**File:** `config.py`  
**Section:** `CacheConfig` (line ~578)

---

### 7. Cache TTL and Expiration Updates

#### 7.1 Complete Candles TTL
**File:** `config.py`  
**Method:** `CacheConfig.ttl_seconds()`

**Current:**
```python
elif kind == "klines_complete":
    return 21 * 24 * 3600  # 21 days for complete candles
```

**Recommended Change:**
```python
elif kind == "klines_complete":
    return 90 * 24 * 3600  # 90 days for complete candles (supports 66-day 4H requirement)
```

#### 7.2 Open Candles TTL
**No changes needed** - open candles are short-lived by design:
- 15m: 60 seconds
- 1h: 180 seconds
- 4h: 480 seconds
- 1d: 1200 seconds

---

### 8. SMC Analyzer Validation Updates

#### 8.1 HTF Bias Validation
**File:** `api/smc_analyzer.py`  
**Method:** `_get_htf_bias()`

**Current Checks (lines ~1534, 1548):**
```python
if len(d1_data) < SMCConfig.TIMEFRAME_1D_LIMIT:
    # Insufficient daily data
if len(h4_data) < SMCConfig.TIMEFRAME_4H_LIMIT:
    # Insufficient 4H data
```

**Changes Required:** ✅ **NONE**
- Automatically uses updated `SMCConfig.TIMEFRAME_4H_LIMIT = 400`
- Will now require 400 4H candles before proceeding

**Action:** Test that analysis gracefully handles transition period when building up from 200 to 400 candles

#### 8.2 Intermediate Structure Validation
**File:** `api/smc_analyzer.py`  
**Method:** `_get_intermediate_structure()`

**Current:** No explicit limit checks
**Action:** Consider adding validation:
```python
if len(h1_data) < SMCConfig.TIMEFRAME_1H_LIMIT:
    logging.warning(f"Insufficient 1H data: {len(h1_data)}/{SMCConfig.TIMEFRAME_1H_LIMIT}")
    # Continue with degraded confidence or return neutral
```

---

### 9. Migration Strategy

#### 9.1 Gradual Data Backfill
**Approach:** Background worker will gradually fetch historical data

**Timeline:**
- Initial deployment: Existing symbols have 200 4H / 300 1H candles
- Background worker fetches: 200 additional 4H / 300 additional 1H candles
- Full data available: Within 1-2 background worker cycles (~5-10 minutes)

**File:** `api/klines_background_worker_backup.py`  
**Method:** `update_symbol_data()`

**Process:**
1. Worker detects current cache has < new target
2. Calculates gap: `required_candles = TIMEFRAME_LIMIT - len(current_cache)`
3. Fetches historical data to fill gap
4. Merges with existing data

#### 9.2 Handle Analysis During Transition
**Challenge:** SMC analysis may fail if it expects 400 4H but only 250 are available

**Solution Options:**

**Option A: Graceful Degradation (Recommended)**
```python
# In _get_htf_bias()
if len(h4_data) < SMCConfig.TIMEFRAME_4H_LIMIT:
    # Use available data with reduced confidence
    confidence_penalty = len(h4_data) / SMCConfig.TIMEFRAME_4H_LIMIT
    # Reduce confidence score proportionally
```

**Option B: Strict Validation**
```python
# Reject analysis until full data is available
if len(h4_data) < SMCConfig.TIMEFRAME_4H_LIMIT:
    return neutral_bias
```

**Recommendation:** Implement Option A for smoother user experience

---

### 10. Testing & Validation Plan

#### 10.1 Unit Tests
**Create/Update Tests:**
1. `test_config_values()` - Verify new config values
2. `test_rolling_window_cleanup()` - Test cleanup with 600 1H / 400 4H candles
3. `test_kline_fetch_limits()` - Verify batch fetch respects new limits
4. `test_smc_analysis_with_extended_data()` - Test analysis with full dataset

#### 10.2 Integration Tests
**Scenarios:**
1. Fresh symbol initialization (fetch 600 1H / 400 4H from scratch)
2. Migration from old to new (backfill additional candles)
3. Cleanup triggers correctly at new thresholds (750 1H / 500 4H)
4. Database performance with 2x data volume

#### 10.3 Performance Monitoring
**Metrics to Track:**
1. Background worker execution time (should remain < 60s)
2. Database query latency (should remain < 100ms)
3. Memory usage during analysis (monitor for spikes)
4. Circuit breaker trip rate (should not increase)

---

### 11. Rollback Plan

#### 11.1 Quick Rollback (if issues occur)
**Steps:**
1. Revert `config.py` changes:
   - `TIMEFRAME_1H_LIMIT = 300`
   - `TIMEFRAME_4H_LIMIT = 200`
   - `TARGET_CANDLES_1H = 300`
   - `TARGET_CANDLES_4H = 200`
   - `CLEANUP_BUFFER_1H = 150`
   - `CLEANUP_BUFFER_4H = 100`

2. Trigger cleanup worker to remove excess candles:
   ```python
   KlinesCache.cleanup_all_rolling_windows(batch_size=50)
   ```

3. Restart application

**Data Impact:** Excess candles removed, but no data corruption

#### 11.2 Database Cleanup (if needed)
```sql
-- Remove excess 1H candles (keep only 300 most recent per symbol)
DELETE FROM klines_cache
WHERE id IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY symbol, timeframe 
            ORDER BY timestamp DESC
        ) as rn
        FROM klines_cache
        WHERE timeframe = '1h'
    ) t
    WHERE rn > 300
);

-- Remove excess 4H candles (keep only 200 most recent per symbol)
DELETE FROM klines_cache
WHERE id IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY symbol, timeframe 
            ORDER BY timestamp DESC
        ) as rn
        FROM klines_cache
        WHERE timeframe = '4h'
    ) t
    WHERE rn > 200
);
```

---

## Implementation Checklist

### Phase 1: Configuration Updates
- [ ] Update `SMCConfig.TIMEFRAME_1H_LIMIT` from 300 to 600
- [ ] Update `SMCConfig.TIMEFRAME_4H_LIMIT` from 200 to 400
- [ ] Update `RollingWindowConfig.TARGET_CANDLES_1H` from 300 to 600
- [ ] Update `RollingWindowConfig.TARGET_CANDLES_4H` from 200 to 400
- [ ] Update cleanup buffer calculations (1H: 750, 4H: 500)
- [ ] Update `CacheConfig.KLINES_DATA_RETENTION_DAYS` from 21 to 90
- [ ] Update complete candles TTL from 21 days to 90 days

### Phase 2: Lookback Threshold Updates
- [ ] Update `SMCConfig.OB_MAX_AGE_CANDLES` from 200 to 300
- [ ] Update `SMCConfig.FVG_MAX_AGE_CANDLES` from 200 to 300
- [ ] Update `SMCConfig.SWING_LOOKBACK_1H` from 5 to 8
- [ ] Update `SMCConfig.SWING_LOOKBACK_4H` from 10 to 15
- [ ] Add `SMCConfig.STRUCTURE_SWING_LOOKBACK_1H = 5`
- [ ] Add `SMCConfig.STRUCTURE_SWING_LOOKBACK_4H = 5`
- [ ] Update `detect_market_structure()` method to use timeframe-specific structure lookback

### Phase 3: SMC Analyzer Enhancements
- [ ] Add graceful degradation for partial data scenarios
- [ ] Update confidence scoring to account for data completeness
- [ ] Add 1H data validation in `_get_intermediate_structure()`
- [ ] Test analysis quality with extended lookback

### Phase 4: Testing & Validation
- [ ] Run unit tests for config changes
- [ ] Test background worker with new fetch limits
- [ ] Test cleanup worker with new thresholds
- [ ] Validate database performance with increased data
- [ ] Monitor circuit breaker behavior

### Phase 5: Deployment & Monitoring
- [ ] Deploy configuration changes
- [ ] Monitor background worker for successful backfill
- [ ] Monitor database storage growth
- [ ] Monitor SMC analysis quality improvements
- [ ] Track circuit breaker metrics

### Phase 6: Documentation
- [ ] Update `SMC_ANALYZER_DOCUMENTATION.md` with new limits
- [ ] Document performance benchmarks
- [ ] Update API documentation if applicable
- [ ] Create runbook for operators

---

## Expected Benefits

### 1. Improved Pattern Detection
- **Order Blocks:** 50% longer lookback (300 vs 200 candles) = better institutional zone identification
- **Fair Value Gaps:** 50% longer lookback = capture older unfilled gaps
- **Market Structure:** 2x 4H data (66 vs 33 days) = more reliable trend identification
- **BOS Detection:** 50% wider swing lookback (15 vs 10 for 4H, 8 vs 5 for 1H) = fewer false breaks
- **CHOCH Detection:** 67% more swing points analyzed (5 vs 3) = earlier reversal signals with higher confidence

### 2. Enhanced Signal Quality
- **Reduced False Positives:** More historical context for validation
- **Better Confluence:** Extended data allows multi-timeframe confirmation
- **Institutional Alignment:** 66 days of 4H data covers typical institutional cycles

### 3. Risk Management
- **Better SL Placement:** More swing points for optimal stop loss
- **Improved TP Targets:** Extended liquidity pool detection
- **Reduced Whipsaw:** Better understanding of larger market structure

---

## Potential Risks & Mitigations

### Risk 1: Increased Memory Usage
**Impact:** 2x candle data = ~2x memory for analysis  
**Mitigation:** 
- Monitor memory usage in production
- Consider pagination for very large datasets
- Optimize data structures if needed

### Risk 2: Slower Analysis Performance
**Impact:** More data = longer processing time  
**Mitigation:**
- Profile analysis methods before/after
- Optimize bottleneck functions (use numpy/vectorization)
- Consider caching intermediate results

### Risk 3: Database Storage Growth
**Impact:** 2x candles per symbol = 2x database storage  
**Mitigation:**
- Monitor database size metrics
- Ensure cleanup workers run reliably
- Review retention policy after 30 days

### Risk 4: API Rate Limiting
**Impact:** Larger initial fetches may trigger rate limits  
**Mitigation:**
- Binance allows 1000 candles per request (600 is well within limit)
- Circuit breaker handles rate limit errors gracefully
- Background worker has built-in retry logic

---

## Success Metrics

### Technical Metrics
- ✅ 600 1H candles successfully stored per active symbol
- ✅ 400 4H candles successfully stored per active symbol
- ✅ Background worker completes in < 120 seconds
- ✅ Database query latency remains < 150ms
- ✅ Cleanup worker maintains target candle counts

### Business Metrics
- 📈 Signal confidence scores increase by 10-15%
- 📈 Win rate improves by 5-10% (measure over 30 days)
- 📈 Drawdown reduces by 10-20% (better risk management)
- 📉 False signal rate decreases by 15-20%

---

## Next Steps

1. **Review this plan** with the team
2. **Create feature branch:** `feature/increase-candle-limits`
3. **Implement Phase 1:** Configuration updates
4. **Test thoroughly** in staging environment
5. **Deploy to production** with monitoring
6. **Measure results** over 30-day period
7. **Iterate** based on performance data

---

## Notes & Considerations

### Why 400 4H and 600 1H?
- **4H (400 candles):** 66 days captures ~2 months of institutional structure, including weekly and monthly patterns
- **1H (600 candles):** 25 days provides sufficient intraday structure for order block and FVG detection
- **Balance:** More than 400/600 risks noise and stale data; less than 400/600 misses institutional context

### Database Retention Strategy
- **Complete Candles:** 90 days (supports 66-day 4H requirement + 24-day buffer)
- **Open Candles:** Short TTL (1-20 minutes) - refreshed frequently
- **Cleanup:** Rolling window maintains target + safety margin
- **Fallback:** Time-based cleanup removes data older than 90 days

### Circuit Breaker Configuration
- **No changes needed:** Single API call handles 600 candles (within 1000 limit)
- **Existing protection:** 8 failure threshold, 120s recovery timeout
- **Monitoring:** Track metrics for any anomalies post-deployment

---

## BOS & CHOCH Improvement Summary

### What's Changing:
1. **Swing Detection Lookback:**
   - 1H: 5 → 8 candles (+60% wider swing detection)
   - 4H: 10 → 15 candles (+50% wider swing detection)

2. **Structure Analysis Lookback:**
   - 1H: 3 → 5 swing points (+67% more pattern analysis)
   - 4H: 3 → 5 swing points (+67% more pattern analysis)

3. **Historical Data Available:**
   - 1H: 300 → 600 candles (2x data for 25-day structure)
   - 4H: 200 → 400 candles (2x data for 66-day structure)

### Why This Matters:

**Break of Structure (BOS):**
- **More Reliable Confirmation:** With 15-candle lookback on 4H (vs 10), BOS signals will only trigger when price breaks through well-established swing points across 66 days of institutional data
- **Reduced Whipsaws:** Wider lookback filters out short-term noise that creates false BOS signals
- **Better Trend Continuation Signals:** Extended data captures true institutional trend structures

**Change of Character (CHOCH):**
- **Earlier Reversal Detection:** Analyzing 5 swing points (vs 3) allows the system to spot trend exhaustion patterns sooner
- **Higher Confidence Reversals:** More swing points = better trend direction calculation = fewer false reversal signals
- **Institutional Reversal Zones:** 66-day 4H data captures major institutional position changes that create genuine CHOCH patterns

### Real-World Impact:

**Before (Current):**
```
4H CHOCH Detection:
- Analyzes 3 recent swing points over 33 days
- May miss earlier reversal signals
- Higher risk of false reversals from short-term moves
```

**After (New Configuration):**
```
4H CHOCH Detection:
- Analyzes 5 recent swing points over 66 days
- Captures reversal patterns 1-2 weeks earlier
- Filters out noise with institutional-grade data depth
```

**Practical Example:**
If an asset has been in a bullish trend for 40 days, the current system (33-day 4H data) might miss the early CHOCH signals. The new system (66-day 4H data) will capture the full trend context and detect the reversal as soon as institutional structure shifts.

---

**Document Version:** 1.1  
**Last Updated:** October 15, 2025  
**Status:** Ready for Implementation - Includes BOS/CHOCH Enhancement Details
