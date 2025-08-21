# Toobit Multi-Trade Telegram Bot

## Overview
This project is a Telegram-based trading bot for Toobit USDT-M futures, featuring multi-trade capabilities. It allows users to manage multiple simultaneous trading configurations conversationally, with advanced risk management, portfolio tracking, and real-time execution monitoring. The vision is to offer a powerful, user-friendly tool for active traders, leveraging Telegram for accessibility, with future expansion to other exchanges and a comprehensive suite of trading tools.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture
The application uses Flask for its web framework, serving as the Telegram Mini-App interface and handling webhook integration. The core `MultiTradeManager` class enables concurrent management of multiple trading configurations while ensuring user isolation and orchestrating multiple `TradingBot` instances.

**UI/UX Decisions:**
The UI/UX utilizes HTML formatting with a sophisticated dark blue theme, elegant gradient backgrounds, high-contrast white text, and vibrant blue accents, optimized for mobile with responsive design. Modern, professional trading platform symbols are used throughout. Typography uses Google Fonts (Inter for text, JetBrains Mono for numerical data).

**Technical Implementations & Feature Specifications:**
- Streamlined project structure with `api/` directory for the main Flask application and clear separation of environments.
- Comprehensive webhook security with secret token authentication.
- Focus on core trading bot capabilities; market data tab and live charting functionality removed.
- Implemented proper limit order functionality with "pending" status and monitoring.
- Corrected margin, position size, and leverage calculations for mathematically accurate futures trading.
- Enhanced TP/SL display showing actual prices and profit/loss amounts.
- Consolidated templates into `api/templates/`.
- Migrated to real-time market data across all platform deployments with multiple APIs and fallback.
- Implemented comprehensive price fetching optimization with intelligent caching, concurrent requests, and adaptive API prioritization.
- Displays full trade details before execution and for active positions, with real-time ROE and color-coded P&L.
- Complete Edit/Execute/Delete functionality in the web app trading tab with smart UI controls.
- Collapsible/expandable functionality for positions and trading tabs.
- Multi-symbol trading support for `get_live_market_price` function with concurrent processing and intelligent fallbacks.
- Correctly displays "Position Size" alongside "Margin" in the UI.
- Tracks and displays the last 5 closed positions in the Portfolio tab.
- Changed application title to "Trading Expert" for a professional, platform-agnostic brand.
- Implemented hamburger menu with API Keys functionality moved into it.
- Corrected account balance calculation to include realized P&L from closed trades.
- Implemented comprehensive micro-interactions for trade management buttons including loading states, success/error animations, and haptic feedback.
- Critical trade deletion issue resolved by implementing proper PostgreSQL database persistence with `TradeConfiguration` model.
- Neon PostgreSQL-specific configurations for optimal Vercel performance.
- Resolved data type mismatch errors in `breakeven_after` field.
- Fixed break-even stop loss display after TP1 triggers.
- Implemented GMT+3:30 (Iran Standard Time) timezone for all trade configurations and history timestamps.
- Added portfolio reset functionality, allowing users to clear all trade history and P&L data while preserving API credentials.
- Redesigned expandable boxes in portfolio tab for compactness and changed timestamp formatting to 24-hour.
- Enhanced visual refinements including modern gradient header, sophisticated hamburger menu, elegant navigation tabs, enhanced card styling, and premium typography.
- Removed "Advanced Trading Platform" subtitle and replaced lightning emoji with a pulsing live indicator in the header.
- Added "Close All Trades" button to positions tab.
- Implemented comprehensive Toobit exchange integration with dual-environment support (background polling for Replit, on-demand for Vercel).
- Implemented intelligent caching system to prevent excessive database queries from Telegram WebView.
- Enhanced realized P&L tracking system for partial take profit closures with separate display of realized vs floating P&L.
- Fixed and enhanced stop loss trigger logic with proper break-even stop loss support for both long and short positions.
- **Migration to Standard Replit Environment (2025-08-19)**: Successfully migrated from Replit Agent to standard Replit environment with proper PostgreSQL database setup, fixed schema compatibility issues, and resolved PnL calculation discrepancies between main total and active positions total by properly separating realized vs unrealized P&L in live updates.
- **Post-Migration Status**: Application now runs cleanly on Replit with PostgreSQL database, all dependencies properly installed, and Telegram WebView interface fully functional. Database tables created successfully and exchange synchronization service operational.
- **Dual Environment Support (2025-08-19)**: Enhanced the application to support both Replit and Vercel/Neon deployments seamlessly. The existing api/app.py maintains full Vercel compatibility with Neon PostgreSQL optimization, while main.py provides the proper entry point for Replit. Both environments use the same codebase with environment-specific optimizations.
- **Database Migration System (2025-08-19)**: Added automatic database migration system to handle schema updates across environments. Includes breakeven_sl_triggered column migration and ensures compatibility between Replit PostgreSQL and Vercel/Neon deployments.
- **Enhanced Import System (2025-08-19)**: Improved import handling with fallback mechanisms for both relative (Vercel) and absolute (Replit) import paths, ensuring code compatibility across deployment environments.
- **Schema Migration Fix (2025-08-19)**: Fixed database migration system to properly handle both breakeven_sl_triggered and realized_pnl columns across all environments. Added complete Vercel deployment guide and SQL schema script for Neon PostgreSQL.
- **Connection Pool Optimization (2025-08-19)**: Enhanced Neon PostgreSQL connection pooling configuration to prevent QueuePool timeout errors in Vercel serverless environment. Increased pool size, overflow limits, and connection timeouts for better concurrent request handling.
- **TP Progression Fix (2025-08-19)**: Fixed take profit progression issue where TP levels weren't properly advancing after a TP was triggered. Enhanced both Replit and Vercel sync services to properly remove triggered TP levels from the configuration and allow remaining TPs to become the new TP1, TP2, etc. Now when TP1 is filled, TP2 automatically becomes the new TP1.
- **TP Allocation Calculation Fix (2025-08-19)**: Corrected partial take profit allocation calculations. Fixed the profit amount calculation to properly account for position sizing in futures trading. Allocation percentage now correctly determines the portion of the total position that gets closed at each TP level. When 30% allocation is set, exactly 30% of the position closes at that TP level, with profit calculated as (TP_percentage × margin × allocation_percentage). This provides accurate P&L calculations where allocation determines position closure, not profit distribution.
- **Enhanced Closed Position Display (2025-08-20)**: Significantly improved the portfolio tab's closed position history with comprehensive details including: partial take profit levels with percentages and allocations displayed in monospace font, breakeven stop loss activation status with color-coded indicators (green for activated, yellow for not triggered), accurate ROE calculation for closed positions, realized vs final P&L breakdown showing partial closures, and enhanced visual styling with profit/loss color-coded left borders. This provides traders with complete visibility into how their closed trades performed across all risk management levels.
- **Complete Migration to Standard Replit Environment (2025-08-20)**: Successfully completed full migration from Replit Agent to standard Replit environment. Created PostgreSQL database, verified all dependencies, confirmed application startup, and validated web interface functionality. All features operational including trade configuration, symbol selection (including BNB/USDT), position management, and Telegram WebView integration. No code-level restrictions prevent symbol reuse after position closure.
- **Dual Environment Status (2025-08-20)**: Replit environment fully functional with all features working correctly. User-reported BNB position creation issue exists specifically on the Vercel/Neon deployment, not in the Replit environment. The codebase supports both environments with proper fallback mechanisms and environment-specific optimizations.
- **Successful Migration to Standard Replit Environment (2025-08-21)**: Completed full migration from Replit Agent to standard Replit environment. Created PostgreSQL database with all required environment variables (DATABASE_URL, PGPORT, PGUSER, PGPASSWORD, PGDATABASE, PGHOST), verified all dependencies are properly installed, and confirmed application startup with gunicorn on port 5000. Database tables created successfully, exchange synchronization service started, and web interface responding correctly with HTTP 200. Project now runs cleanly in the Replit environment with proper client/server separation and security practices.
- **Toobit API Connection Success (2025-08-20)**: Successfully resolved Toobit exchange API connectivity issues after correcting the request format. Fixed the API client to use query parameters for GET requests instead of form data, which resolved the "unknown parameter" error. API connection test now returns success with live account balance data from Toobit exchange in both testnet and mainnet modes. The application can now connect to the real Toobit exchange for actual trading execution.
- **Real Trading Implementation (2025-08-20)**: Successfully upgraded the application from simulated trading to real Toobit exchange integration. The `/api/execute-trade` endpoint now places actual orders on Toobit using the ToobitClient, including main position orders and automatic TP/SL order placement. The `/api/close-trade` and `/api/close-all-trades` endpoints now close positions on the exchange and cancel remaining TP/SL orders. Added testnet/mainnet toggle functionality with proper warnings for real money trading. All trading operations now require valid Toobit API credentials and connect to the actual exchange for order execution, position management, and risk management.
- **Vercel Deployment Updated (2025-08-20)**: The Vercel deployment has been updated with the fixed Toobit API implementation. Both Replit and Vercel environments now use the same corrected ToobitClient with proper signature generation matching Toobit's official documentation. The signature validation now works correctly for GET requests (account balance, positions) and the fix for POST requests (order placement) has been deployed to both environments.
- **Real Account Balance Display (2025-08-20)**: Added automatic real-time exchange balance display when users switch to live trading mode. The Portfolio tab now fetches and displays actual USDT balance data from the Toobit exchange API when in mainnet mode, including total balance, available balance, used margin, and margin ratio. The interface shows visual indicators distinguishing between live exchange data (🟢 Live) and simulated testnet data (🧪 Test). Balance data automatically refreshes when users toggle between testnet and mainnet modes.
- **Testnet Toggle UI Integration (2025-08-20)**: Added testnet/mainnet toggle to the web app hamburger menu under API Keys settings. Users can now safely switch between testnet (virtual funds) and mainnet (real money) modes with appropriate confirmation dialogs and warnings. Fixed API credentials saving bug in Vercel deployment where null values caused strip() errors.
- **Successful Migration to Standard Replit Environment (2025-08-21)**: Completed full migration from Replit Agent to standard Replit environment. Created PostgreSQL database with all required environment variables (DATABASE_URL, PGPORT, PGUSER, PGPASSWORD, PGDATABASE, PGHOST), verified all dependencies are properly installed, and confirmed application startup with gunicorn on port 5000. Database tables created successfully, exchange synchronization service started, and web interface responding correctly with HTTP 200. Project now runs cleanly in the Replit environment with proper client/server separation and security practices.

**System Design Choices:**
The system features a modular design with `TradeConfig` objects encapsulating parameters and `TradingBot` instances handling execution and state. Position management supports partial closing and risk management includes breakeven stop loss and trailing stop. `PortfolioTracker` offers comprehensive analytics, including multi-user support and detailed trade history. Data management uses in-memory, dictionary-based structures for user data isolation, trade configuration persistence, and session management. API credentials are encrypted.

## External Dependencies
- **Toobit Exchange API**: For Toobit futures API communication.
- **Telegram Bot API**: For webhook-based communication, rich messaging, and inline keyboard support.
- **Python Libraries**: Flask, Requests/aiohttp, Threading/Asyncio, Logging, Cryptography.
- **Binance API**: For real-time market data.
- **CoinGecko API**: Fallback for live market data.
- **CryptoCompare API**: Fallback for live market data.