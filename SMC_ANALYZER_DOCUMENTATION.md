# SMC Analyzer - Complete Documentation

**Last Updated:** October 5, 2025  
**Version:** 2.0 (All Phases Complete)  
**Status:** ✅ Production Ready

---

## Table of Contents
1. [Overview](#overview)
2. [Current Status](#current-status)
3. [Key Features](#key-features)
4. [Architecture](#architecture)
5. [Configuration](#configuration)
6. [Implementation Phases](#implementation-phases)
7. [Recent Fixes](#recent-fixes)
8. [Usage Guide](#usage-guide)
9. [API Reference](#api-reference)

---

## Overview

The Smart Money Concepts (SMC) Analyzer is an institutional-grade multi-timeframe trading analysis engine that implements hierarchical top-down analysis across 4 timeframes (Daily → H4/H1 → 15m execution). The system identifies institutional trading patterns and generates high-confidence signals with precise entry, stop-loss, and take-profit levels.

### Purpose
- Identify institutional trading patterns using Smart Money Concepts
- Generate high-probability trade signals with multi-timeframe confirmation
- Provide precise entry zones, stop-losses, and take-profit levels
- Filter out low-quality setups using institutional-grade risk filters

---

## Current Status

### ✅ All 7 Phases Complete (October 2025)

| Phase | Feature | Status | Completed |
|-------|---------|--------|-----------|
| Phase 1 | 15m Execution Timeframe | ✅ Complete | Oct 2025 |
| Phase 2 | Multi-Timeframe Workflow | ✅ Complete | Oct 2025 |
| Phase 3 | Enhanced Confidence Scoring | ✅ Complete | Oct 4, 2025 |
| Phase 4 | Scaling Entry Management | ✅ Complete | Oct 4, 2025 |
| Phase 5 | Refined Stop-Loss (15m Swings) | ✅ Complete | Oct 4, 2025 |
| Phase 6 | Multi-Take Profit Management | ✅ Complete | Oct 4, 2025 |
| Phase 7 | ATR Risk Filter | ✅ Complete | Oct 4, 2025 |

### Recent Improvements (October 5, 2025)
- ✅ Fixed type safety with `@overload` decorators (LSP errors: 117 → 49)
- ✅ Eliminated confidence score triple-counting
- ✅ Updated RSI thresholds to SMC standards (30/70)
- ✅ Optimized ATR filter placement for performance
- ✅ Improved 15m missing data handling

---

## Key Features

### 📊 Multi-Timeframe Analysis
- **Hierarchical Workflow:** Daily → H4/H1 → 15m execution
- **Timeframe Support:** 15m, 1h, 4h, 1d with optimized data limits
- **HTF Bias Detection:** Identifies macro trend from Daily and H4 structure
- **15m Execution:** Precise entry timing with HTF alignment validation

### 🎯 Institutional Filtering
- **ATR Risk Filter:** Rejects low-volatility choppy conditions
  - Minimum 0.8% ATR on 15m timeframe
  - Minimum 1.2% ATR on H1 timeframe
- **Auto-Volatility Detection:** Dynamic parameter tuning based on market conditions
- **Volatility Regimes:** Low, Normal, High with adaptive thresholds

### 📈 Smart Entry Scaling
- **50%/25%/25% Allocation Strategy:**
  - Entry 1: 50% market order (aggressive)
  - Entry 2: 25% limit order at OB/FVG zone midpoint
  - Entry 3: 25% limit order at OB/FVG zone deep level
- **Zone-Based Placement:** Uses Order Blocks and Fair Value Gaps
- **Fallback Logic:** Fixed percentage offsets when zones unavailable

### 🛡️ Precision Stop-Loss
- **15m Swing Levels:** Uses recent swing highs/lows for tight stops
- **ATR Buffers:** Adaptive buffer based on volatility (0.5x - 1.0x ATR)
- **Minimum Distance:** Ensures stop-loss at least 0.5% from entry
- **Dynamic Refinement:** Improves stop-loss precision vs. basic methods

### 💰 R:R-Based Take Profits
- **Risk/Reward Targets:** 1R, 2R, and liquidity-based levels
- **Default Allocations:** 40% @ TP1, 30% @ TP2, 30% @ TP3
- **Liquidity Targeting:** Identifies and targets institutional liquidity pools
- **Trailing Stop Option:** Move stop to breakeven after TP1 (configurable)

### 🧠 Dynamic Confidence Scoring
- **Multi-Timeframe Alignment:** Bonus for HTF/15m confluence
- **Liquidity Sweep Confirmation:** +0.1 confidence for confirmed sweeps
- **POI Entry Bonus:** +0.1 confidence for entries from HTF Order Blocks/FVGs
- **15m Alignment Bonus:** +0.2 confidence for perfect alignment (score ≥ 0.8)

### 🔧 Optional Features
- **Dynamic Position Sizing:** Adjust size based on ATR volatility
- **Trailing Stop-Loss:** Move to breakeven after TP1 hit
- **Signal Caching:** Prevents duplicate signals (1-hour timeout)

---

## Architecture

### Core Components

#### 1. SMCAnalyzer Class (`api/smc_analyzer.py`)
Main analysis engine with the following key methods:

**Signal Generation:**
- `generate_trade_signal(symbol: str, return_diagnostics: bool = False)` - Main entry point
- Returns `SMCSignal` object or tuple with diagnostics

**Multi-Timeframe Analysis:**
- `_get_htf_bias(d1_data, h4_data)` - High timeframe bias (Phase 2)
- `_get_intermediate_structure(h1_data, h4_data)` - H4/H1 structure (Phase 2)
- `_get_execution_signal_15m(m15_data, htf_bias, intermediate_structure)` - 15m execution (Phase 2)

**Entry Management:**
- `_calculate_scaled_entries()` - Zone-based scaling (Phase 4)
- `_determine_trade_direction_and_levels_hybrid()` - Hybrid logic for direction

**Risk Management:**
- `_find_15m_swing_levels(m15_data)` - Swing level detection (Phase 5)
- `_calculate_refined_sl_with_atr()` - Stop-loss refinement (Phase 5)
- `_calculate_rr_based_take_profits()` - Take profit calculation (Phase 6)

**Filtering & Validation:**
- `_check_atr_filter(m15_data, h1_data, current_price)` - Volatility filter (Phase 7)
- `_calculate_signal_strength_and_confidence()` - Confidence scoring (Phase 3)

**Pattern Detection:**
- `detect_market_structure()` - BOS/CHoCH/Consolidation detection
- `find_order_blocks()` - Order Block identification
- `find_fair_value_gaps()` - FVG detection
- `find_liquidity_pools()` - Liquidity level analysis
- `detect_liquidity_sweeps()` - Sweep confirmation

#### 2. Data Models

**SMCSignal (dataclass):**
```python
@dataclass
class SMCSignal:
    symbol: str
    direction: str  # "long" | "short" | "hold"
    entry_price: float
    stop_loss: float
    take_profit_levels: List[float]
    confidence: float  # 0.0-1.0
    reasoning: List[str]
    signal_strength: SignalStrength
    risk_reward_ratio: float
    timestamp: datetime
    current_market_price: float
    scaled_entries: Optional[List[ScaledEntry]] = None
```

**ScaledEntry (dataclass):**
```python
@dataclass
class ScaledEntry:
    entry_price: float
    allocation_percent: float
    order_type: str  # "market" | "limit"
    stop_loss: float
    take_profits: List[Tuple[float, float]]
    status: str  # "pending" | "filled" | "cancelled"
```

#### 3. Configuration (`config.py`)

**TradingConfig Class:**
All SMC settings are centralized in the `TradingConfig` class within `config.py`.

---

## Configuration

### Phase 1: 15m Execution Timeframe

| Parameter | Default | Description |
|-----------|---------|-------------|
| `TIMEFRAME_15M_LIMIT` | 400 | 15m candles to fetch (~4 days) |
| `TARGET_CANDLES_15M` | 400 | Rolling window target for 15m |
| `CLEANUP_BUFFER_15M` | 100 | Buffer before cleanup (500 total) |
| `ENABLED_15M` | True | Enable 15m timeframe |
| `KLINES_15M_CACHE_TTL` | 1 | Cache TTL in minutes for 15m |

### Phase 2: Multi-Timeframe Workflow

**Hierarchical Analysis Flow:**
```
Daily (1d)  → HTF bias & liquidity targets
    ↓
H4 + H1     → Intermediate structure (OBs, FVGs, BOS/CHoCH)
    ↓
15m         → Execution signal (must align with HTF)
```

**Key Settings:**
- Daily bias weight: 2.0x multiplier
- Minimum confluence score: 3.0
- Alignment required: True (enforce H1/H4 alignment)

### Phase 3: Enhanced Confidence Scoring

| Bonus Type | Value | Condition |
|------------|-------|-----------|
| 15m Perfect Alignment | +0.2 | Alignment score ≥ 0.8 |
| Confirmed Liquidity Sweep | +0.1 | Sweep matches signal direction |
| HTF POI Entry | +0.1 | Entry within 0.5% of OB/FVG |

**Signal Strength Thresholds:**
- Very Strong: ≥ 0.9 confidence
- Strong: ≥ 0.8 confidence
- Moderate: ≥ 0.7 confidence
- Weak: < 0.7 confidence

### Phase 4: Scaling Entry Management

| Parameter | Default | Description |
|-----------|---------|-------------|
| `USE_SCALED_ENTRIES` | True | Enable scaled entry strategy |
| `SCALED_ENTRY_ALLOCATIONS` | [50, 25, 25] | % allocation per entry |
| `SCALED_ENTRY_MIN_ZONE_SIZE` | 0.003 | Min zone size (0.3%) |
| `SCALED_ENTRY_MAX_ZONE_SIZE` | 0.015 | Max zone size (1.5%) |

**Entry Logic:**
- Long: Entry 1 @ zone top, Entry 2 @ midpoint, Entry 3 @ bottom
- Short: Entry 1 @ zone bottom, Entry 2 @ midpoint, Entry 3 @ top
- Fallback: Fixed depths [0.004, 0.010] if no zones found

### Phase 5: Refined Stop-Loss

| Parameter | Default | Description |
|-----------|---------|-------------|
| `USE_15M_SWING_SL` | True | Use 15m swing levels for SL |
| `SL_ATR_BUFFER_MULTIPLIER` | 0.5 | ATR buffer for swing SL |
| `SL_MIN_DISTANCE_PERCENT` | 0.005 | Min SL distance (0.5%) |

**Calculation Logic:**
1. Find last swing high/low on 15m
2. Add ATR buffer (0.5x ATR default)
3. Ensure minimum distance from entry
4. Compare with base SL, use tighter option

### Phase 6: Multi-Take Profit Management

| Parameter | Default | Description |
|-----------|---------|-------------|
| `USE_RR_BASED_TPS` | True | Use R:R-based take profits |
| `TP_RR_RATIOS` | [1.0, 2.0, 3.0] | Risk/Reward ratios |
| `TP_ALLOCATIONS` | [40, 30, 30] | % allocation per TP |
| `USE_TRAILING_STOP` | False | Trail stop after TP1 |

**TP Calculation:**
- TP1 = Entry + (1R × risk)
- TP2 = Entry + (2R × risk)
- TP3 = Entry + (3R × risk) OR nearest liquidity pool

### Phase 7: ATR Risk Filter

| Parameter | Default | Description |
|-----------|---------|-------------|
| `USE_ATR_FILTER` | True | Enable ATR risk filter |
| `MIN_ATR_15M_PERCENT` | 0.8 | Min ATR % on 15m (0.8%) |
| `MIN_ATR_H1_PERCENT` | 1.2 | Min ATR % on H1 (1.2%) |
| `USE_DYNAMIC_POSITION_SIZING` | False | Adjust size by ATR |

**Filter Logic:**
1. Calculate ATR% = (ATR / current_price) × 100
2. Check 15m ATR% ≥ 0.8% AND H1 ATR% ≥ 1.2%
3. Reject if either threshold not met
4. Optional: Scale position size by volatility

---

## Implementation Phases

### Phase 1: 15m Execution Timeframe ✅

**Objective:** Add 15-minute timeframe for precise execution

**Files Modified:**
- `api/smc_analyzer.py` - Added "15m" to timeframes
- `config.py` - Added 15m limits and cache settings
- `api/unified_data_sync_service.py` - Added 15m sync
- `api/models.py` - Added 15m timestamp flooring

**Key Changes:**
- Timeframes: `["15m", "1h", "4h", "1d"]`
- 15m limit: 400 candles (~4 days)
- Cache TTL: 1 minute for 15m open candles

### Phase 2: Multi-Timeframe Analysis Workflow ✅

**Objective:** Implement Daily → H4/H1 → 15m hierarchical analysis

**Key Methods Added:**
1. `_get_htf_bias()` - HTF bias from Daily/H4
2. `_get_intermediate_structure()` - H4/H1 structure analysis
3. `_get_execution_signal_15m()` - 15m execution with alignment

**Analysis Flow:**
```
1. Determine HTF bias (Daily + H4)
2. Analyze intermediate structure (H4 + H1)
3. Generate 15m execution signal
4. Validate 15m alignment with HTF
5. Reject if alignment score < 0.3
```

### Phase 3: Enhanced Confidence Scoring ✅

**Objective:** Dynamic confidence based on alignment and confluence

**Key Method:**
- `_calculate_signal_strength_and_confidence()` - Enhanced scoring

**Confidence Bonuses:**
- Base confidence from signal confluence
- +0.2 for perfect 15m alignment (≥ 0.8)
- +0.1 for confirmed liquidity sweeps
- +0.1 for entry from HTF POI (OB/FVG)

### Phase 4: Scaling Entry Management ✅

**Objective:** Zone-based scaled entries using OBs/FVGs

**Key Method:**
- `_calculate_scaled_entries()` - Zone-based entry calculation

**Entry Strategy:**
- 50% aggressive (market/limit at zone edge)
- 25% balanced (limit at zone midpoint)
- 25% deep (limit at zone opposite edge)

**Zone Detection:**
- Find nearest OB/FVG in signal direction
- Merge overlapping zones
- Adjust for volatility regime
- Fallback to fixed % if no zones

### Phase 5: Refined Stop-Loss with 15m Swings ✅

**Objective:** Precision stop-loss using 15m swing levels

**Key Methods:**
- `_find_15m_swing_levels()` - Detect swing highs/lows
- `_calculate_refined_sl_with_atr()` - Calculate refined SL

**SL Calculation:**
1. Find last swing high/low on 15m (5-candle lookback)
2. Add ATR buffer (0.5x multiplier)
3. Ensure min 0.5% distance from entry
4. Use tighter of refined vs. base SL

### Phase 6: Multi-Take Profit Management ✅

**Objective:** R:R-based TPs targeting liquidity

**Key Methods:**
- `_calculate_rr_based_take_profits()` - Calculate TP levels
- `_find_liquidity_target()` - Find institutional targets
- `_should_trail_stop_after_tp1()` - Trailing stop logic

**TP Logic:**
- Calculate 1R, 2R, 3R based on risk
- Check for liquidity pools near 3R
- Use liquidity target if within 0.5%
- Allocate 40%/30%/30% by default

### Phase 7: ATR Risk Filter ✅

**Objective:** Filter low-volatility choppy markets

**Key Methods:**
- `_check_atr_filter()` - Volatility threshold check
- `_calculate_dynamic_position_size()` - Optional sizing

**Filter Logic:**
1. Check filter BEFORE analysis (optimization)
2. Calculate 15m ATR% and H1 ATR%
3. Require 15m ≥ 0.8% AND H1 ≥ 1.2%
4. Reject if either fails
5. Optional: Adjust position size by volatility

---

## Recent Fixes (October 5, 2025)

### Issue 1: Type Safety ✅
**Problem:** Union return type causing 117 LSP errors  
**Solution:** Added `@overload` decorators with `Literal[True]` and `Literal[False]`  
**Result:** LSP errors reduced to 49 (58% improvement)

### Issue 2: Counter-Trend Logic ✅
**Problem:** Duplicate RSI/sweep validation creating conflicting rejection reasons  
**Solution:** Removed duplicate checks from rejection logic  
**Result:** Clean rejection reasons, validation happens once

### Issue 3: Confidence Triple Counting ✅
**Problem:** Same evidence counted 3 times (hybrid → metrics → Phase 3)  
**Solution:** Removed duplicate bonuses, Phase 3 is single source of truth  
**Result:** Accurate confidence scores, no artificial inflation

### Issue 4: RSI Thresholds ✅
**Problem:** Using 35/65 instead of SMC standard 30/70  
**Solution:** Updated thresholds to 30 (oversold) and 70 (overbought)  
**Result:** More accurate reversal signal detection

### Issue 5: ATR Filter Placement ✅
**Problem:** Volatility tuning ran before ATR filter check  
**Solution:** Moved ATR filter check BEFORE parameter tuning  
**Result:** Saves computation on rejected trades

### Issue 6: 15m Missing Data ✅
**Problem:** Missing data scored 0.5 (neutral), better than 0.2 (conflict)  
**Solution:** Changed missing data default to 0.3 (borderline)  
**Result:** Proper risk assessment when 15m unavailable

---

## Usage Guide

### Basic Usage

```python
from api.smc_analyzer import SMCAnalyzer

# Initialize analyzer
analyzer = SMCAnalyzer()

# Generate signal
signal = analyzer.generate_trade_signal("BTCUSDT")

if signal:
    print(f"Direction: {signal.direction}")
    print(f"Entry: ${signal.entry_price}")
    print(f"Stop Loss: ${signal.stop_loss}")
    print(f"Take Profits: {signal.take_profit_levels}")
    print(f"Confidence: {signal.confidence:.2%}")
    print(f"Risk/Reward: {signal.risk_reward_ratio:.2f}R")
    
    # Access scaled entries
    if signal.scaled_entries:
        for i, entry in enumerate(signal.scaled_entries, 1):
            print(f"Entry {i}: ${entry.entry_price} ({entry.allocation_percent}%)")
```

### With Diagnostics

```python
# Get signal with diagnostics
signal, diagnostics = analyzer.generate_trade_signal("ETHUSDT", return_diagnostics=True)

if signal:
    print("Signal generated successfully")
    print(f"HTF Bias: {diagnostics['details']['htf_bias']}")
    print(f"15m Alignment: {diagnostics['details'].get('m15_alignment_score', 'N/A')}")
else:
    print("Signal rejected:")
    for reason in diagnostics['rejection_reasons']:
        print(f"  - {reason}")
```

### Configuration

```python
from config import TradingConfig

# Enable/disable features
TradingConfig.USE_ATR_FILTER = True
TradingConfig.USE_SCALED_ENTRIES = True
TradingConfig.USE_15M_SWING_SL = True
TradingConfig.USE_RR_BASED_TPS = True

# Adjust thresholds
TradingConfig.MIN_ATR_15M_PERCENT = 1.0  # More strict
TradingConfig.TP_RR_RATIOS = [1.5, 3.0, 4.5]  # Higher targets
```

---

## API Reference

### Main Methods

#### `generate_trade_signal(symbol: str, return_diagnostics: bool = False)`
**Purpose:** Main entry point for signal generation

**Parameters:**
- `symbol`: Trading pair (e.g., "BTCUSDT")
- `return_diagnostics`: Return diagnostics dict if True

**Returns:**
- If `return_diagnostics=False`: `Optional[SMCSignal]`
- If `return_diagnostics=True`: `Tuple[Optional[SMCSignal], Dict]`

**Example:**
```python
signal = analyzer.generate_trade_signal("BTCUSDT")
signal, diagnostics = analyzer.generate_trade_signal("BTCUSDT", return_diagnostics=True)
```

### Signal Properties

**SMCSignal Object:**
- `symbol`: str - Trading pair
- `direction`: str - "long", "short", or "hold"
- `entry_price`: float - Primary entry price
- `stop_loss`: float - Stop-loss price
- `take_profit_levels`: List[float] - TP prices
- `confidence`: float - 0.0 to 1.0
- `reasoning`: List[str] - Analysis reasoning
- `signal_strength`: SignalStrength - Enum value
- `risk_reward_ratio`: float - R:R ratio
- `timestamp`: datetime - Signal generation time
- `current_market_price`: float - Market price at generation
- `scaled_entries`: Optional[List[ScaledEntry]] - Entry details

### Helper Methods

**Pattern Detection:**
- `detect_market_structure(data)` → MarketStructure
- `find_order_blocks(data)` → List[OrderBlock]
- `find_fair_value_gaps(data)` → List[FairValueGap]
- `find_liquidity_pools(data)` → List[LiquidityPool]
- `detect_liquidity_sweeps(data)` → Dict

**Technical Indicators:**
- `calculate_rsi(data, period=14)` → float
- `calculate_atr(data, period=14)` → float
- `calculate_moving_averages(data)` → Dict

---

## Troubleshooting

### Common Issues

**1. No Signal Generated**
- Check diagnostics for rejection reasons
- Verify sufficient data (min 100 candles per timeframe)
- Ensure ATR filter thresholds aren't too strict

**2. Low Confidence Scores**
- Check multi-timeframe alignment
- Verify liquidity sweeps are confirmed
- Ensure entries are near HTF POIs

**3. Missing 15m Data**
- System will use default alignment score of 0.3
- Signals still generated but with lower confidence
- Check data sync service status

**4. ATR Filter Always Rejecting**
- Lower MIN_ATR thresholds in config
- Check if market is in consolidation
- Verify current volatility regime

---

## Performance Notes

- ATR filter runs BEFORE analysis (optimization)
- Signal caching prevents duplicate analysis (1-hour timeout)
- Rolling window limits database queries
- Multi-timeframe data fetched in parallel

---

## Version History

- **v2.0** (Oct 5, 2025) - Logic fixes, type safety improvements
- **v1.7** (Oct 4, 2025) - Phase 7 complete (ATR filter)
- **v1.6** (Oct 4, 2025) - Phase 6 complete (Multi-TP)
- **v1.5** (Oct 4, 2025) - Phase 5 complete (Refined SL)
- **v1.4** (Oct 4, 2025) - Phase 4 complete (Scaled entries)
- **v1.3** (Oct 4, 2025) - Phase 3 complete (Confidence scoring)
- **v1.2** (Oct 2025) - Phase 2 complete (MTF workflow)
- **v1.1** (Oct 2025) - Phase 1 complete (15m timeframe)
- **v1.0** (Initial) - Basic SMC analysis

---

## License & Credits

Part of the Multi-Exchange Trading Bot project.  
Implements institutional Smart Money Concepts methodology.

For issues or questions, refer to the project documentation or open an issue.
