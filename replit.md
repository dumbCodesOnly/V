# Toobit Multi-Trade Telegram Bot

## Overview
This project is a sophisticated Telegram-based trading bot designed for Toobit USDT-M futures trading, featuring multi-trade capabilities. It enables users to manage multiple simultaneous trading configurations through a conversational interface. Key capabilities include advanced risk management, portfolio tracking, and real-time trade execution monitoring. The business vision is to provide a powerful, user-friendly tool for active traders, leveraging Telegram for accessibility and real-time interaction, with ambitions to expand to other exchanges and offer a comprehensive suite of trading tools.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture
The application utilizes Flask as its web framework, serving as the primary interface for the Telegram Mini-App and handling webhook integration for real-time message processing. The core innovation is the `MultiTradeManager` class, enabling concurrent management of multiple trading configurations while ensuring user isolation and orchestrating multiple `TradingBot` instances.

The trading system features a modular design with `TradeConfig` objects encapsulating trade parameters and `TradingBot` instances handling execution, state tracking, and trailing stop functionality. Position management supports partial closing at configurable take profit levels, and risk management includes breakeven stop loss movement and trailing stop activation.

The `PortfolioTracker` offers comprehensive analytics, including multi-user support, detailed trade history, performance metrics (win/loss ratios, P&L), and daily summaries.

The Telegram Bot integrates via webhooks, processing messages and callback queries. The UI/UX utilizes HTML formatting for rich text and interactive menus, with a sophisticated dark blue theme, elegant gradient backgrounds, high-contrast white text, and vibrant blue accents, optimized for mobile with a responsive design. It provides real-time trade execution notifications and status updates with robust error handling.

Data management uses in-memory, dictionary-based structures for user data isolation, trade configuration persistence, and session management. API credentials are securely encrypted using Fernet encryption and stored in `UserCredentials` and `UserTradingSession` models.

The system features:
- **Comprehensive Trade Information Display**: Shows full trade details (entry, take profits, stop loss, amounts) before execution and for active positions, with real-time ROE calculation and color-coded P&L indicators.
- **Complete Trade Management UI**: Full Edit/Execute/Delete functionality in the web app trading tab, with smart UI controls tailored for each tab.
- **Live Market Data Implementation**: Market tab as primary interface with live price data, Chart.js integration for professional price chart visualization, and `/api/market-data` and `/api/kline-data` endpoints using Binance API for real-time data. Supports multiple timeframes, symbol selection (BTC, ETH, BNB, ADA, DOT, SOL), and auto-refresh for updates. Trade execution uses live market prices.
- **Collapsible UI Enhancement**: Implemented collapsible/expandable functionality for both positions and trading tabs, improving user experience with dynamic display of trade details.
- **Multi-Symbol Trading Support**: Enhanced `get_live_market_price` function with a multi-source API fallback system, supporting 12+ cryptocurrency pairs and ensuring reliable live price data for trade execution.

## External Dependencies
- **Toobit Exchange API**: Custom `ExchangeClient` for Toobit futures API communication.
- **Telegram Bot API**: For webhook-based communication, rich messaging, and inline keyboard support.
- **Python Libraries**: Flask, Requests/aiohttp, Threading/Asyncio, Logging, Cryptography.
- **Chart.js**: For interactive data visualization and charting.
- **Binance API**: Used for real-time market data (prices, kline data).
- **CoinGecko API**: Utilized as a fallback for live market data.
- **CryptoCompare API**: Utilized as a fallback for live market data.