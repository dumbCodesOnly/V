# Toobit Multi-Trade Telegram Bot

## Overview
This project is a Telegram-based trading bot for Toobit USDT-M futures, offering multi-trade capabilities. It allows users to manage multiple simultaneous trading configurations conversationally, with advanced risk management, portfolio tracking, and real-time execution monitoring. The goal is to provide a powerful, user-friendly tool for active traders, leveraging Telegram for accessibility, with future expansion to other exchanges and a comprehensive suite of trading tools.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture
The application uses Flask for its web framework, serving as the Telegram Mini-App interface and handling webhook integration. The core `MultiTradeManager` class enables concurrent management of multiple trading configurations while ensuring user isolation and orchestrating multiple `TradingBot` instances.

**UI/UX Decisions:**
The UI/UX utilizes HTML formatting with a dark blue theme, gradient backgrounds, high-contrast white text, and vibrant blue accents, optimized for mobile with responsive design. Modern, professional trading platform symbols are used. Typography uses Google Fonts (Inter for text, JetBrains Mono for numerical data). The application title is "Trading Expert".

**Technical Implementations & Feature Specifications:**
- Streamlined project structure with an `api/` directory and clear separation of environments.
- Comprehensive webhook security with secret token authentication.
- Implemented proper limit order functionality with "pending" status and monitoring.
- Corrected margin, position size, and leverage calculations for mathematically accurate futures trading.
- Enhanced TP/SL display showing actual prices and profit/loss amounts.
- Migrated to real-time market data across all platform deployments with multiple APIs and fallback.
- Implemented comprehensive price fetching optimization with intelligent caching, concurrent requests, and adaptive API prioritization, prioritizing Toobit-first pricing.
- Displays full trade details before execution and for active positions, with real-time ROE and color-coded P&L.
- Complete Edit/Execute/Delete functionality in the web app trading tab with smart UI controls.
- Collapsible/expandable functionality for positions and trading tabs.
- Multi-symbol trading support for `get_live_market_price` function with concurrent processing and intelligent fallbacks.
- Correctly displays "Position Size" alongside "Margin" in the UI.
- Tracks and displays the last 5 closed positions in the Portfolio tab with comprehensive details.
- Implemented hamburger menu with API Keys functionality.
- Corrected account balance calculation to include realized P&L from closed trades.
- Implemented comprehensive micro-interactions for trade management buttons.
- Critical trade deletion issue resolved by implementing proper PostgreSQL database persistence with `TradeConfiguration` model.
- Neon PostgreSQL-specific configurations for optimal Vercel performance.
- Implemented GMT+3:30 (Iran Standard Time) timezone for all trade configurations and history timestamps.
- Added portfolio reset functionality.
- Implemented comprehensive Toobit exchange integration with dual-environment support (background polling for Replit, on-demand for Vercel).
- Implemented intelligent caching system to prevent excessive database queries from Telegram WebView.
- Enhanced realized P&L tracking system for partial take profit closures with separate display of realized vs floating P&L.
- Fixed and enhanced stop loss trigger logic with proper break-even stop loss support for both long and short positions.
- Supports both Replit and Vercel/Neon deployments seamlessly with shared codebase and environment-specific optimizations.
- Automatic database migration system to handle schema updates across environments.
- Enhanced import system with fallback mechanisms for relative (Vercel) and absolute (Replit) import paths.
- Optimized Neon PostgreSQL connection pooling.
- Fixed take profit progression where triggered TP levels are removed and subsequent TPs become new TP1.
- Corrected partial take profit allocation calculations for accurate position closure and profit calculation.
- Implemented real Toobit exchange integration for order execution, position management, and risk management.
- Added testnet/mainnet toggle functionality with real-time exchange balance display.
- Added paper trading mode for development testing without requiring API credentials.
- Enhanced trade execution to properly handle both paper and live trading modes seamlessly.
- Enhanced error reporting with specific error messages and technical details.
- Improved paper trading reliability and debugging.
- Universal disabling of Toobit Testnet mode, forcing mainnet for all operations.
- Comprehensive debugging for position closing failures.
- UptimeRobot integration for continuous monitoring and prevention of Render free tier sleeping.
- Resolved critical bugs in TP execution and breakeven stop loss movement.
- Streamlined root directory organization and Render deployment process.
- Achieved 100% centralization of all configuration values into `config.py`.
- Standardized TP allocation format and corrected default allocation values.
- Fixed critical issues with auto-trades visibility and break-even monitoring.
- Implemented comprehensive error classification system with user-friendly messages.
- Enhanced caching system with smart volatility-based TTL and user data caching.
- Deployed background cache cleanup worker.
- Implemented circuit breaker pattern for all external API calls to prevent cascading failures.
- Resolved bug in SMC signal data retrieval (`get_live_market_price`).
- SMC signals now display correctly and are cached in the database with validation.

**System Design Choices:**
The system features a modular design with `TradeConfig` objects encapsulating parameters and `TradingBot` instances handling execution and state. Position management supports partial closing and risk management includes breakeven stop loss and trailing stop. `PortfolioTracker` offers comprehensive analytics, including multi-user support and detailed trade history. Data management uses in-memory, dictionary-based structures for user data isolation, trade configuration persistence, and session management. API credentials are encrypted.

## External Dependencies
- **Toobit Exchange API**: For Toobit futures API communication and market data.
- **Telegram Bot API**: For webhook-based communication, rich messaging, and inline keyboard support.
- **Python Libraries**: Flask, Requests/aiohttp, Threading/Asyncio, Logging, Cryptography.
- **Binance API**: For real-time market data (fallback).
- **CoinGecko API**: Fallback for live market data.
- **CryptoCompare API**: Fallback for live market data.