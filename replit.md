# Toobit Multi-Trade Telegram Bot

## Overview
This project is a sophisticated Telegram-based trading bot designed for Toobit USDT-M futures trading, featuring multi-trade capabilities. It enables users to manage multiple simultaneous trading configurations through a conversational interface. Key capabilities include advanced risk management, portfolio tracking, and real-time trade execution monitoring. The business vision is to provide a powerful, user-friendly tool for active traders, leveraging Telegram for accessibility and real-time interaction, with ambitions to expand to other exchanges and offer a comprehensive suite of trading tools.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture
The application utilizes Flask as its web framework, serving as the primary interface for the Telegram Mini-App and handling webhook integration for real-time message processing. The core innovation is the `MultiTradeManager` class, enabling concurrent management of multiple trading configurations while ensuring user isolation and orchestrating multiple `TradingBot` instances.

**UI/UX Decisions:**
The UI/UX utilizes HTML formatting for rich text and interactive menus, with a sophisticated dark blue theme, elegant gradient backgrounds, high-contrast white text, and vibrant blue accents, optimized for mobile with a responsive design. Modern, professional trading platform symbols are used throughout the application (e.g., ⚡ for title, ▲/▼ for positions, ⊞ for trading, ◈ for portfolio, ◉ for limit orders, ⦿ for market orders).

**Technical Implementations & Feature Specifications:**
- **Codebase Consolidation:** Streamlined project structure with `api/` directory containing the main Flask application and a clean separation between Replit development and Vercel production environments.
- **Webhook Security:** Implemented comprehensive webhook security with secret token authentication and request structure validation.
- **Core Trading Focus:** Removed market data tab and live charting functionality to focus on core trading bot capabilities.
- **Limit Order Functionality:** Implemented proper limit order functionality, including "pending" status, automated monitoring, and support for all four types of limit orders (long limit, long stop, short limit, short stop).
- **Trading Logic Correction:** Corrected fundamental flaws in margin, position size, and leverage calculations, ensuring mathematically correct futures trading formulas where profit/loss amounts are calculated from the user's margin input.
- **Enhanced TP/SL Display:** Comprehensive take profit and stop loss display showing actual prices alongside profit/loss amounts based on margin trading principles.
- **Template Structure Consolidation:** All templates are consolidated into the `api/templates/` directory for consistent resolution.
- **Live Market Data Integration:** Migration to real-time market data across all platform deployments, utilizing multiple APIs with fallback mechanisms.
- **API Exchange Optimization (2025-08-17):** Implemented comprehensive price fetching optimization with intelligent caching (10-second TTL), concurrent API requests, performance metrics tracking, adaptive API prioritization based on success rates and response times, batch price fetching for multiple symbols, emergency stale cache fallback, and detailed performance monitoring endpoints.
- **Comprehensive Trade Information Display:** Shows full trade details before execution and for active positions, with real-time ROE calculation and color-coded P&L indicators.
- **Complete Trade Management UI:** Full Edit/Execute/Delete functionality in the web app trading tab, with smart UI controls.
- **Collapsible UI Enhancement:** Implemented collapsible/expandable functionality for both positions and trading tabs, improving user experience.
- **Multi-Symbol Trading Support:** Enhanced `get_live_market_price` function with extended cryptocurrency pair support, concurrent request processing, and intelligent fallback mechanisms across Binance, CoinGecko, and CryptoCompare APIs.
- **Position Size Display:** Correctly displays "Position Size" (margin × leverage) alongside "Margin" in the UI.
- **Closed Positions History:** Implemented comprehensive tracking and display of the last 5 closed positions in the Portfolio tab, including final P&L and detailed information.
- **Enhanced Typography System:** Professional typography using Google Fonts (Inter for text, JetBrains Mono for numerical data) with optimized weights, letter spacing, and font hierarchy for improved readability and modern trading platform appearance.
- **Header Branding Update:** Changed application title from "Toobit Trading Bot" to "Trading Expert" to create a more professional, platform-agnostic brand identity.
- **Migration to Replit (2025-08-17):** Successfully migrated project from Replit Agent to standard Replit environment, including PostgreSQL database setup, gunicorn configuration, and optimized price fetching to only update symbols for active positions and configured trades rather than all available cryptocurrency pairs. Fixed UI collapsing issue by removing redundant DOM rebuilds during live price updates.
- **Clean Production Environment (2025-08-18):** Completed final migration cleanup, removing all demo trade data for a clean production-ready environment. Application now starts with no pre-populated trades, ensuring authentic user experience from the start.
- **Live Price Update Fix (2025-08-18):** Fixed positions tab totals not updating without page refresh by enhancing the live price update mechanism to include real-time position count and total P&L updates directly in the positions summary section. Extended real-time updates to portfolio tab for complete live data refresh across all financial metrics including account balance, margin usage, and P&L calculations.
- **Account Balance Fix (2025-08-18):** Fixed critical issue where realized P&L from closed trades was not being added to the account balance. Modified `get_margin_summary` function to properly calculate account balance as initial balance plus realized P&L, ensuring accurate financial tracking.
- **Micro-Interactions Enhancement (2025-08-18):** Implemented comprehensive micro-interactions for trade management buttons including loading states, success/error animations, haptic feedback integration, and smooth visual transitions. Added CSS animations with cubic-bezier easing, button hover effects, ripple animations, and loading spinners to enhance user experience and provide immediate feedback for all trading actions.
- **Database Persistence Fix (2025-08-18):** Resolved critical trade deletion issue by implementing proper PostgreSQL database persistence. Added `TradeConfiguration` model, database helper functions, and modified all trade operations to save/load from database instead of in-memory storage. Enhanced serverless reliability with explicit app context management, forced database commits, and Vercel-specific database loading to handle cold starts. This ensures trades persist permanently across app sessions and serverless deployments on both Replit and Vercel.
- **Neon Database Optimization (2025-08-18):** Implemented Neon PostgreSQL-specific configurations for optimal Vercel performance including SSL requirements, serverless connection pooling (pool_size=1), retry logic with exponential backoff, connection timeouts, and application identification in Neon logs. Added dedicated Neon setup guide and troubleshooting documentation.
- **Database Schema Fix (2025-08-18):** Resolved data type mismatch error where `breakeven_after` field expected numeric values but received "disabled" strings. Implemented proper data type conversion in `TradeConfiguration.from_trade_config()` method to handle string-to-numeric conversion and ensure Neon database compatibility.
- **Migration to Standard Replit Environment (2025-08-18):** Successfully migrated project from Replit Agent to standard Replit environment with gunicorn server configuration. Confirmed Flask application running properly on port 5000 with PostgreSQL database integration, all packages installed correctly, and Telegram WebApp functionality verified through browser testing.

**System Design Choices:**
The trading system features a modular design with `TradeConfig` objects encapsulating trade parameters and `TradingBot` instances handling execution, state tracking, and trailing stop functionality. Position management supports partial closing at configurable take profit levels, and risk management includes breakeven stop loss movement and trailing stop activation. The `PortfolioTracker` offers comprehensive analytics, including multi-user support, detailed trade history, performance metrics, and daily summaries. Data management uses in-memory, dictionary-based structures for user data isolation, trade configuration persistence, and session management. API credentials are securely encrypted using Fernet encryption.

## External Dependencies
- **Toobit Exchange API**: Custom `ExchangeClient` for Toobit futures API communication.
- **Telegram Bot API**: For webhook-based communication, rich messaging, and inline keyboard support.
- **Python Libraries**: Flask, Requests/aiohttp, Threading/Asyncio, Logging, Cryptography.
- **Binance API**: Used for real-time market data (prices).
- **CoinGecko API**: Utilized as a fallback for live market data.
- **CryptoCompare API**: Utilized as a fallback for live market data.