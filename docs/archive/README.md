# SMC Documentation Archive

This directory contains historical SMC (Smart Money Concepts) documentation that has been consolidated into the main `SMC_ANALYZER_DOCUMENTATION.md` file.

## ⚠️ IMPORTANT: Archive Status

**These documents are ARCHIVED and may contain outdated information.**

**For current, up-to-date SMC documentation, always refer to:**
👉 **`/SMC_ANALYZER_DOCUMENTATION.md`** (root directory)

---

## Archived Documents

### Implementation Planning & Status
- **SMC_MULTI_TIMEFRAME_IMPLEMENTATION_PLAN.md** - Original implementation plan for all 7 phases
- **SMC_IMPLEMENTATION_STATUS.md** - Detailed status tracking for each phase
- **PHASE_7_COMPLETION_SUMMARY.md** - Summary of Phase 7 (ATR Risk Filter) completion

### Configuration & Settings
- **SMC_Analyzer_Settings_Documentation.md** - Detailed settings and configuration guide

### Upgrades & Fixes
- **SMC_UPGRADE_PLAN.md** - Scaled entry upgrade plan (completed)
- **SMC_FIXES_PLAN.md** - Original 6 fixes (Issues #1-6) from October 5, 2025

## Current Documentation

**All SMC information has been consolidated into:**
👉 **`/SMC_ANALYZER_DOCUMENTATION.md`** (root directory)

This consolidated document contains:
- Complete feature overview
- All 7 phases implementation details
- Configuration reference
- All fixes through Issue #32 (✅ Complete)
- Current pending issues #33-38 (⚠️ Identified Oct 7, 2025)
- Usage guide and API reference

## What's Been Fixed Since Archival

**Issues #10-18** (Oct 5-6, 2025):
- Short entry validation, stop-loss ordering, TP validation, ATR consistency, zone distance, timestamp mutation, division by zero protection

**Issues #19-24** (Oct 6, 2025):
- Configuration wiring, cache gap-filling, pair-specific ATR thresholds, alignment score conflicts, FVG scoring

**Issues #25-32** (Oct 7, 2025):
- 200-candle implementation fixes, swing detection scaling, liquidity pool lookback, OB age expiration, type safety improvements

**Issues #33-38** (Oct 7, 2025):
- Pending: Python 3.12+ compatibility, config inconsistencies, defensive programming improvements

## Why Archive?

These documents were consolidated to:
1. Provide a single source of truth for SMC documentation
2. Reduce redundancy and conflicting information
3. Make it easier to maintain and update
4. Improve documentation discoverability

The archived documents are preserved for historical reference and contain detailed implementation notes that may be useful for understanding the development process.

---

**Last Updated:** October 7, 2025
