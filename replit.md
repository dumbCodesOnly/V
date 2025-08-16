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
- **Comprehensive Trade Information Display:** Shows full trade details before execution and for active positions, with real-time ROE calculation and color-coded P&L indicators.
- **Complete Trade Management UI:** Full Edit/Execute/Delete functionality in the web app trading tab, with smart UI controls.
- **Collapsible UI Enhancement:** Implemented collapsible/expandable functionality for both positions and trading tabs, improving user experience.
- **Multi-Symbol Trading Support:** `get_live_market_price` function supports multiple cryptocurrency pairs via a multi-source API fallback system.
- **Position Size Display:** Correctly displays "Position Size" (margin × leverage) alongside "Margin" in the UI.
- **Closed Positions History:** Implemented comprehensive tracking and display of the last 5 closed positions in the Portfolio tab, including final P&L and detailed information.
- **Enhanced Typography System:** Professional typography using Google Fonts (Inter for text, JetBrains Mono for numerical data) with optimized weights, letter spacing, and font hierarchy for improved readability and modern trading platform appearance.

**System Design Choices:**
The trading system features a modular design with `TradeConfig` objects encapsulating trade parameters and `TradingBot` instances handling execution, state tracking, and trailing stop functionality. Position management supports partial closing at configurable take profit levels, and risk management includes breakeven stop loss movement and trailing stop activation. The `PortfolioTracker` offers comprehensive analytics, including multi-user support, detailed trade history, performance metrics, and daily summaries. Data management uses in-memory, dictionary-based structures for user data isolation, trade configuration persistence, and session management. API credentials are securely encrypted using Fernet encryption.

## External Dependencies
- **Toobit Exchange API**: Custom `ExchangeClient` for Toobit futures API communication.
- **Telegram Bot API**: For webhook-based communication, rich messaging, and inline keyboard support.
- **Python Libraries**: Flask, Requests/aiohttp, Threading/Asyncio, Logging, Cryptography.
- **Binance API**: Used for real-time market data (prices).
- **CoinGecko API**: Utilized as a fallback for live market data.
- **CryptoCompare API**: Utilized as a fallback for live market data.