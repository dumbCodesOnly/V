# Multi-Exchange Trading Bot (Toobit & LBank)

## Overview
This project is a comprehensive Telegram-based trading bot designed for USDT-M futures trading on both Toobit and LBank exchanges. It enables users to manage multiple simultaneous trading configurations conversationally via Telegram, offering advanced risk management, portfolio tracking, and real-time execution monitoring. The primary goal is to provide a powerful, user-friendly tool for active traders, leveraging Telegram for accessibility, with modular exchange support and a comprehensive suite of trading tools.

## Recent Changes
- **October 4, 2025**: SMC ANALYZER ENHANCEMENT - Phase 3 Complete (Enhanced Confidence Scoring)
  - Implemented `_calculate_15m_alignment_score()` method for quantifying 15m/HTF bias alignment (0.0-1.0 scale)
  - Enhanced `_calculate_signal_strength_and_confidence()` with Phase 3 bonuses and penalties:
    - **+0.2 bonus** for perfect 15m alignment with HTF bias (score ≥ 0.8)
    - **-0.3 penalty** for 15m/HTF conflict (score < 0.3) - signals rejected before this point
    - **+0.1 bonus** for confirmed liquidity sweep in signal direction
    - **+0.1 bonus** for entry from HTF point of interest (OB/FVG within 0.5%)
  - Updated signal strength thresholds to incorporate 15m alignment:
    - VERY_STRONG: confidence ≥ 0.8 AND alignment ≥ 0.7
    - STRONG: confidence ≥ 0.65 AND alignment ≥ 0.5
    - MODERATE: confidence ≥ 0.5 AND alignment ≥ 0.3
  - Integrated Phase 3 logic into `generate_trade_signal()` method
  - Added Phase 3 diagnostics tracking for alignment, sweeps, and POI entries
  - Added Phase 3 bonus explanations to signal reasoning for transparency
  - Comprehensive logging shows base vs enhanced confidence with breakdown
  - All Phase 3 enhancements fully integrated and operational
  - **Next Steps**: Implement Phase 4 (Scaling Entry Management with partial fills)
  - See SMC_MULTI_TIMEFRAME_IMPLEMENTATION_PLAN.md for detailed guidance on remaining phases

- **October 4, 2025**: SMC ANALYZER ENHANCEMENT - Phase 2 Complete (Multi-Timeframe Analysis Workflow)
  - Implemented hierarchical analysis flow: Daily → H4/H1 → 15m execution
  - Created `_get_htf_bias()` method for Daily/H4 macro trend analysis with confidence scoring
  - Created `_get_intermediate_structure()` method for H4/H1 order blocks, FVGs, and POI detection
  - Created `_get_execution_signal_15m()` method for precise 15m execution signals with HTF alignment
  - Updated `generate_trade_signal()` to use Phase 2 hierarchical workflow
  - Trades now rejected when 15m structure conflicts with HTF bias (alignment score < 0.3)
  - Added comprehensive diagnostics tracking for HTF bias, intermediate structure, and 15m signals
  - Enhanced logging for Phase 2 analysis debugging
  - Maintains full backward compatibility - proceeds with standard analysis if 15m unavailable
  - Syntax verification passed with no LSP errors

- **October 4, 2025**: SMC ANALYZER ENHANCEMENT - Phase 1 Complete
  - Added 15-minute timeframe support for institutional-style execution analysis
  - Extended SMCAnalyzer to include "15m" in timeframes (15m, 1h, 4h, 1d)
  - Added 15m configuration to RollingWindowConfig (TARGET_CANDLES_15M = 400)
  - Added 15m cache TTL settings (KLINES_15M_CACHE_TTL = 1 minute)
  - Updated config.py with TIMEFRAME_15M_LIMIT = 400 candles
  - Created comprehensive implementation plan (SMC_MULTI_TIMEFRAME_IMPLEMENTATION_PLAN.md)
  - Created implementation status tracker (SMC_IMPLEMENTATION_STATUS.md)

- **September 24, 2025**: CRITICAL BUG FIX - Resolved klines database gap issue on Render
  - Fixed incomplete candles being deleted instead of promoted to complete status
  - Modified `KlinesCache.cleanup_expired()` to properly handle candle lifecycle transitions
  - When incomplete candles' TTL expires, they are now promoted to complete status (with 21-day TTL) if their time period has ended
  - This prevents data gaps that were occurring when incomplete candles expired before next update cycle
  - Added comprehensive logging to track candle promotion events: "KLINES-FIX: Promoted expired incomplete candle to complete"
  - Ensures continuous historical data integrity across all timeframes (1h, 4h, 1d)

- **September 22, 2025**: Successfully integrated cache cleanup and klines background workers into UnifiedDataSyncService
  - Merged two separate worker threads into a single coordinated service for better efficiency and synchronization
  - Implemented unified data sync service with coordinated 2-minute intervals for optimal performance
  - Cache cleanup now runs after data updates for better coordination and resource management
  - Preserved all functionality from both original workers with line-by-line verification
  - Maintained backward compatibility for all existing references and function calls
  - Significantly improved resource efficiency with single worker thread instead of competing threads
  - Enhanced error handling and circuit breaker protection for external API calls
  - Completed full Replit environment setup: database verified, requirements cleaned, workflow configured, deployment ready

- **September 18, 2025**: Enhanced admin panel with comprehensive database management functionality
  - Added database statistics dashboard showing table counts and statuses
  - Implemented table viewer with secure data browsing (sensitive data masked)
  - Added cache cleanup worker monitoring and status tracking
  - Built database health checker with memory usage, connection tests, and integrity checks
  - Created cache clearing functionality for enhanced, SMC, and klines caches
  - Fixed workflow configuration to use Python module system for gunicorn
  - Cleaned up requirements.txt removing duplicates and added psutil dependency
  - All services running smoothly: database, cache cleanup worker, circuit breakers, exchange sync

- **September 16, 2025**: Successfully set up GitHub project import in Replit environment
  - Verified PostgreSQL database connection (DATABASE_URL already configured)
  - Set up Flask application workflow on port 5000 with webview output type
  - Configured deployment settings for Replit autoscale deployment
  - Application running successfully - gunicorn server started, database tables created, and all services initialized
  - All exchange synchronization services and circuit breakers are operational

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture
The application is built on Flask, serving as the Telegram Mini-App interface and handling webhook integration. A core `MultiTradeManager` class manages concurrent trading configurations, ensuring user isolation and orchestrating multiple `TradingBot` instances.

**UI/UX Decisions:**
The UI/UX features a dark blue theme with gradient backgrounds, high-contrast white text, and vibrant blue accents, optimized for mobile with responsive design. It utilizes modern, professional trading platform symbols and typography from Google Fonts (Inter for text, JetBrains Mono for numerical data), with "Trading Expert" as the application title.

**Technical Implementations & Feature Specifications:**
- Streamlined project structure with clear separation of environments and an `api/` directory.
- Comprehensive webhook security with secret token authentication.
- Robust limit order functionality with pending status and monitoring.
- Mathematically accurate margin, position size, and leverage calculations for futures trading.
- Enhanced TP/SL display showing actual prices and profit/loss.
- Real-time market data across all platform deployments with multiple APIs and intelligent caching, prioritizing Toobit-first pricing.
- Detailed trade display before execution and for active positions, with real-time ROE and color-coded P&L.
- Full Edit/Execute/Delete functionality in the web app with smart UI controls.
- Collapsible/expandable functionality for positions and trading tabs.
- Multi-symbol trading support with concurrent processing and intelligent fallbacks.
- Correct display of "Position Size" alongside "Margin" and tracking of the last 5 closed positions.
- API Keys management via a hamburger menu.
- Accurate account balance calculation including realized P&L.
- Comprehensive micro-interactions for trade management buttons.
- PostgreSQL database persistence for `TradeConfiguration` model, with Neon-specific optimizations for Vercel.
- All timestamps (trade configurations, history) are in GMT+3:30 (Iran Standard Time).
- Portfolio reset functionality.
- Comprehensive Toobit and LBank exchange integration with dual-environment support (background polling for Replit, on-demand for Vercel).
- Intelligent caching system to prevent excessive database queries from Telegram WebView.
- Enhanced realized P&L tracking for partial take profit closures.
- Fixed and enhanced stop loss trigger logic, including break-even stop loss.
- Seamless support for Replit and Vercel/Neon deployments with shared codebase and environment-specific optimizations.
- Automatic database migration system for schema updates.
- Enhanced import system with fallback mechanisms for relative and absolute import paths.
- Optimized Neon PostgreSQL connection pooling.
- Corrected take profit progression logic, where triggered TP levels are removed and subsequent TPs become new TP1.
- Accurate partial take profit allocation calculations.
- Real Toobit exchange integration for order execution, position management, and risk management.
- Testnet/mainnet toggle with real-time exchange balance display.
- Paper trading mode for development testing, seamlessly integrated with live trading.
- Enhanced error reporting with specific messages.
- Universal disabling of Toobit Testnet mode, forcing mainnet.
- UptimeRobot integration for continuous monitoring.
- Centralized configuration values in `config.py`.
- Standardized TP allocation format and corrected default values.
- Comprehensive error classification system with user-friendly messages.
- Enhanced caching system with smart volatility-based TTL and user data caching, including a background cleanup worker.
- Circuit breaker pattern for all external API calls.
- SMC signals are correctly displayed and cached in the database with validation.
- Modular exchange client architecture with factory pattern for seamless multi-exchange operation and a unified API interface.

**System Design Choices:**
The system features a modular design with `TradeConfig` objects encapsulating parameters and `TradingBot` instances handling execution and state. Position management supports partial closing, and risk management includes breakeven stop loss and trailing stop. `PortfolioTracker` offers comprehensive analytics, including multi-user support and detailed trade history. Data management uses in-memory, dictionary-based structures for user data isolation, trade configuration persistence, and session management. API credentials are encrypted.

## External Dependencies
- **Toobit Exchange API**: For Toobit futures API communication and market data.
- **LBank Exchange API**: For LBank futures API communication and market data.
- **Telegram Bot API**: For webhook-based communication, rich messaging, and inline keyboard support.
- **Python Libraries**: Flask, Requests/aiohttp, Threading/Asyncio, Logging, Cryptography.
- **Binance API**: For real-time market data (fallback).
- **CoinGecko API**: Fallback for live market data.
- **CryptoCompare API**: Fallback for live market data.