# Toobit Multi-Trade Telegram Bot

## Overview
This project is a Telegram-based trading bot for Toobit USDT-M futures, featuring multi-trade capabilities. It allows users to manage multiple simultaneous trading configurations conversationally, with advanced risk management, portfolio tracking, and real-time execution monitoring. The vision is to offer a powerful, user-friendly tool for active traders, leveraging Telegram for accessibility, with future expansion to other exchanges and a comprehensive suite of trading tools.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture
The application uses Flask for its web framework, serving as the Telegram Mini-App interface and handling webhook integration. The core `MultiTradeManager` class enables concurrent management of multiple trading configurations while ensuring user isolation and orchestrating multiple `TradingBot` instances.

**Latest Updates (August 25, 2025):**
- **Migration Completed**: Successfully migrated from Replit Agent to standard Replit environment with full functionality
- **Break-even Monitoring Fix**: Fixed issue where break-even at TP1 settings weren't being properly monitored by changing storage format from display names to internal codes

**Previous Updates (August 23, 2025):**
- **Enhanced Limit Order System**: Limit orders are now placed directly on the exchange instead of manual price monitoring, providing more realistic trading behavior
- **Improved TP/SL Management**: Take profit and stop loss orders are configured to activate automatically when limit orders are filled
- **Comprehensive Error Classification**: Implemented user-friendly error messaging system with 10 error categories, contextual suggestions, and appropriate severity levels
- Implemented enhanced caching system with smart volatility-based TTL for optimal performance
- Added comprehensive user data caching to reduce database load and improve response times
- Deployed background cache cleanup worker for automatic expired entry management
- Added real-time cache performance tracking with hit rate analytics and monitoring endpoints
- Implemented circuit breaker pattern for all external API calls (Toobit, Binance, CoinGecko, CryptoCompare)
- Circuit breakers prevent cascading failures with configurable thresholds and recovery timeouts
- Added circuit breaker monitoring dashboard with health status and statistics endpoints
- Enhanced API error handling with intelligent fallback mechanisms for improved reliability

**UI/UX Decisions:**
The UI/UX utilizes HTML formatting with a sophisticated dark blue theme, elegant gradient backgrounds, high-contrast white text, and vibrant blue accents, optimized for mobile with responsive design. Modern, professional trading platform symbols are used throughout. Typography uses Google Fonts (Inter for text, JetBrains Mono for numerical data). Application title is "Trading Expert".

**Technical Implementations & Feature Specifications:**
- Streamlined project structure with `api/` directory and clear separation of environments.
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
- Tracks and displays the last 5 closed positions in the Portfolio tab with comprehensive details including partial take profit levels, breakeven stop loss activation status, accurate ROE, and realized vs final P&L breakdown.
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
- Automatic database migration system to handle schema updates across environments, including `breakeven_sl_triggered` and `realized_pnl` columns.
- Enhanced import system with fallback mechanisms for relative (Vercel) and absolute (Replit) import paths.
- Optimized Neon PostgreSQL connection pooling.
- Fixed take profit progression where triggered TP levels are removed and subsequent TPs become new TP1.
- Corrected partial take profit allocation calculations for accurate position closure and profit calculation.
- Implemented real Toobit exchange integration for order execution, position management, and risk management.
- Added testnet/mainnet toggle functionality with real-time exchange balance display.
- Added mock trading mode for development testing without requiring API credentials.
- Enhanced trade execution to properly handle both mock and live trading modes seamlessly.

**System Design Choices:**
The system features a modular design with `TradeConfig` objects encapsulating parameters and `TradingBot` instances handling execution and state. Position management supports partial closing and risk management includes breakeven stop loss and trailing stop. `PortfolioTracker` offers comprehensive analytics, including multi-user support and detailed trade history. Data management uses in-memory, dictionary-based structures for user data isolation, trade configuration persistence, and session management. API credentials are encrypted.

## External Dependencies
- **Toobit Exchange API**: For Toobit futures API communication and market data.
- **Telegram Bot API**: For webhook-based communication, rich messaging, and inline keyboard support.
- **Python Libraries**: Flask, Requests/aiohttp, Threading/Asyncio, Logging, Cryptography.
- **Binance API**: For real-time market data (fallback).
- **CoinGecko API**: Fallback for live market data.
- **CryptoCompare API**: Fallback for live market data.