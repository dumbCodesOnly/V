# Multi-Exchange Trading Bot

## Recent Changes

### October 5, 2025 - SMC Analysis Logic Fixes
- **Fixed Type Safety**: Added `@overload` decorators to `generate_trade_signal()` method, reducing LSP errors from 117 to 49
- **Fixed Counter-Trend Logic**: Removed duplicate RSI/sweep validation to eliminate conflicting rejection reasons
- **Fixed Confidence Scoring**: Eliminated triple-counting of bonuses - now Phase 3 is single source of truth
- **Updated RSI Thresholds**: Changed from 35/65 to 30/70 to align with SMC standards
- **Optimized ATR Filter**: Moved volatility check BEFORE parameter tuning to save computation
- **Improved 15m Data Handling**: Changed missing data default from 0.5 (neutral) to 0.3 (borderline) for better risk assessment
- See `SMC_FIXES_PLAN.md` for detailed documentation

## Overview
This project is a Telegram-based trading bot designed for USDT-M futures trading across Toobit and LBank exchanges. It allows users to manage multiple trading configurations conversationally through Telegram, offering advanced risk management, portfolio tracking, and real-time execution monitoring. The goal is to provide a powerful, user-friendly tool for active traders, leveraging Telegram for accessibility, with modular exchange support and a comprehensive suite of trading tools. The bot aims to be a robust solution for managing and automating trading strategies across multiple platforms.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture
The application uses Flask for the Telegram Mini-App interface and webhook integration. A `MultiTradeManager` orchestrates concurrent `TradingBot` instances, ensuring user isolation for multiple trading configurations.

**UI/UX Decisions:**
The UI/UX features a dark blue theme with gradient backgrounds, high-contrast white text, and vibrant blue accents, optimized for mobile with responsive design. It uses professional trading platform symbols and typography from Google Fonts (Inter for text, JetBrains Mono for numerical data), with "Trading Expert" as the application title.

**Technical Implementations & Feature Specifications:**
- Streamlined project structure with clear environment separation and an `api/` directory.
- Secure webhook integration with secret token authentication.
- Robust limit order functionality with pending status and monitoring.
- Accurate margin, position size, and leverage calculations for futures trading.
- Enhanced TP/SL display showing actual prices and profit/loss.
- Real-time market data with multiple APIs and intelligent caching, prioritizing Toobit.
- Detailed trade display before execution and for active positions, with real-time ROE and color-coded P&L.
- Full Edit/Execute/Delete functionality in the web app with smart UI controls.
- Collapsible/expandable functionality for positions and trading tabs.
- Multi-symbol trading support with concurrent processing.
- Correct display of "Position Size" alongside "Margin" and tracking of the last 5 closed positions.
- API Keys management via a hamburger menu.
- Accurate account balance calculation including realized P&L.
- Comprehensive micro-interactions for trade management buttons.
- PostgreSQL database persistence for `TradeConfiguration`, with Neon optimizations.
- All timestamps (trade configurations, history) are in GMT+3:30 (Iran Standard Time).
- Portfolio reset functionality.
- Comprehensive Toobit and LBank exchange integration with dual-environment support.
- Intelligent caching system to prevent excessive database queries.
- Enhanced realized P&L tracking for partial take profit closures.
- Fixed and enhanced stop loss trigger logic, including break-even stop loss.
- Seamless support for Replit and Vercel/Neon deployments with shared codebase.
- Automatic database migration system for schema updates.
- Enhanced import system with fallback mechanisms.
- Optimized Neon PostgreSQL connection pooling.
- Corrected take profit progression logic, where triggered TP levels are removed.
- Accurate partial take profit allocation calculations.
- Real Toobit exchange integration for order execution, position management, and risk management.
- Testnet/mainnet toggle with real-time exchange balance display.
- Paper trading mode for development testing, seamlessly integrated.
- Enhanced error reporting with specific messages.
- Universal disabling of Toobit Testnet mode, forcing mainnet.
- UptimeRobot integration for continuous monitoring.
- Centralized configuration values in `config.py`.
- Standardized TP allocation format and corrected default values.
- Comprehensive error classification system with user-friendly messages.
- Enhanced caching system with smart volatility-based TTL and user data caching, including a background cleanup worker.
- Circuit breaker pattern for all external API calls.
- Modular exchange client architecture with factory pattern for seamless multi-exchange operation and a unified API interface.
- **Multi-Timeframe SMC Analysis** (All 7 Phases Complete - October 2025):
  - Institutional-grade top-down analysis across 4 timeframes (1d → 4h/1h → 15m execution)
  - HTF bias determination from Daily and H4 structure
  - Intermediate structure analysis on H4/H1 (Order Blocks, FVGs, BOS/CHoCH)
  - 15m execution signals with HTF alignment validation
  - Enhanced confidence scoring with multi-timeframe alignment bonuses
  - Scaling entry strategy: 50% market + 25% + 25% limit orders at OB/FVG zones
  - Refined stop-loss using 15m swing levels + ATR buffers
  - R:R-based take profits (1R, 2R, liquidity targets) with 40/30/30 allocation
  - ATR risk filter rejecting low-volatility choppy conditions (0.8% min on 15m, 1.2% min on H1)
  - Optional dynamic position sizing based on ATR volatility
  - SMC signals correctly displayed and cached in the database with validation

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