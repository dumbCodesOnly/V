# Toobit Multi-Trade Telegram Bot

## Overview

This project is a sophisticated Telegram-based trading bot designed for Toobit USDT-M futures trading, featuring multi-trade capabilities. It enables users to manage multiple simultaneous trading configurations through a conversational interface. Key capabilities include advanced risk management, portfolio tracking, and real-time trade execution monitoring. The business vision is to provide a powerful, user-friendly tool for active traders, leveraging Telegram for accessibility and real-time interaction, with ambitions to expand to other exchanges and offer a comprehensive suite of trading tools.

## User Preferences

Preferred communication style: Simple, everyday language.

## Recent Changes

### Enhanced Trade Information Display (August 11, 2025)
- ✅ Added comprehensive trade details in both positions and trading tabs
- ✅ Shows P&L, entry price, current price, ROE, margin used, and status
- ✅ Trading tab: detailed info for active trades, basic info for configurations  
- ✅ Positions tab: full trade details with entry, current price, and margin data
- ✅ Real-time ROE calculation based on margin and unrealized P&L
- ✅ Color-coded P&L and ROE indicators (green/red for profit/loss)

### Complete Trade Management UI (August 11, 2025)
- ✅ Added full Edit/Execute/Delete functionality to web app trading tab
- ✅ Implemented `/api/delete-trade` endpoint for secure trade deletion
- ✅ Execute button shows only when trade configuration is complete
- ✅ Removed delete from positions tab (only Edit/Close for active positions)
- ✅ Smart UI design with appropriate controls for each tab

### Migration Completed (August 11, 2025)
- ✅ Fixed portfolio data sharing issue between users
- ✅ Implemented proper user isolation for demo data
- ✅ Updated API endpoints to accept user-specific requests
- ✅ Fixed LSP errors and improved code security
- ✅ Project successfully migrated to Replit environment

## System Architecture

### Flask Web Application
The application utilizes Flask as its web framework, serving as the primary interface for the Telegram Mini-App and handling webhook integration for real-time message processing. It includes a `/health` endpoint for monitoring and a secure system for managing individual user API credentials.

### Multi-Trade Management System
The core innovation is the `MultiTradeManager` class, enabling concurrent management of multiple trading configurations. It ensures user isolation, allows users to switch between independent trade setups, and orchestrates multiple `TradingBot` instances while maintaining trade configuration state across sessions.

### Trading Engine Architecture
The trading system features a modular design:
- **TradeConfig**: Objects encapsulating all trade parameters, including symbol, entry/exit prices, a three-level take profit system, leverage, and position sizing.
- **TradingBot**: Individual instances responsible for trade execution, state tracking (entry fills, take profit levels, stop loss), and trailing stop functionality.
- **Position Management**: Supports partial position closing at configurable take profit levels.
- **Risk Management**: Implements breakeven stop loss movement and trailing stop activation based on profit thresholds.

### Portfolio Management
The `PortfolioTracker` offers comprehensive analytics:
- **Multi-User Support**: Maintains isolated portfolio data for each user.
- **Trade History**: Records detailed trade events, executions, and P&L calculations.
- **Performance Metrics**: Tracks win/loss ratios, realized/unrealized P&L, and symbol-specific performance.
- **Daily Analytics**: Summarizes daily P&L and performance.

### Telegram Bot Integration
The bot communicates via webhooks:
- **Message Processing**: Handles direct messages and inline keyboard callback queries.
- **UI/UX**: Utilizes HTML formatting for rich text display and interactive menus. The Telegram Mini-App features a sophisticated dark blue theme with elegant gradient backgrounds, high-contrast white text, and vibrant blue accents, optimized for mobile with a responsive design.
- **Real-time Updates**: Provides trade execution notifications and status updates.
- **Error Handling**: Includes validation and timeout management for API requests.

### Data Management
The system uses in-memory, dictionary-based data structures for:
- **User Data Isolation**: Separating data for each `chat_id`.
- **Trade Configuration Persistence**: Storing `TradeConfig` objects.
- **Session Management**: Tracking user workflow and input state.
- **Encrypted Database Storage**: API credentials (keys, secrets, passphrases) are securely encrypted using Fernet encryption derived from an app secret key, stored in `UserCredentials` and `UserTradingSession` models, ensuring secure CRUD operations.

## External Dependencies

- **Toobit Exchange API**: Custom `ExchangeClient` for Toobit futures API communication, supporting HMAC-SHA256 authentication, rate limiting, and configurable testnet/mainnet environments.
- **Telegram Bot API**: For webhook-based communication, rich messaging, and inline keyboard support.
- **Python Libraries**:
    - **Flask**: Web framework.
    - **Requests/aiohttp**: HTTP client libraries.
    - **Threading/Asyncio**: For concurrent processing.
    - **Logging**: For debugging and monitoring.
    - **Cryptography**: For secure encryption of sensitive data.