# Toobit Multi-Trade Telegram Bot

## Overview

A sophisticated Telegram-based trading bot for Toobit USDT-M futures trading with multi-trade capabilities. The system allows users to manage multiple simultaneous trading configurations through a conversational interface, featuring advanced risk management, portfolio tracking, and real-time trade execution monitoring.

## User Preferences

Preferred communication style: Simple, everyday language.

## Recent Changes

**August 11, 2025**: Successfully completed migration from Replit Agent to Replit environment - project fully operational
- **TELEGRAM MINI-APP INTEGRATION**: Created comprehensive Telegram Mini-App interface specifically optimized for Telegram's mini-app feature. Built lightweight, mobile-first web application maintaining all trading bot capabilities including position management, trade configuration, portfolio tracking, and real-time execution. Features native Telegram Web App SDK integration, responsive design, and user-specific data isolation. Accessible via main route (/) and /miniapp with complete API backend supporting user trades, trade configuration, execution, and portfolio analytics
- **DASHBOARD REMOVAL**: Removed legacy dashboard.html template and replaced main route with mini-app interface per user request. The mini-app now serves as the primary web interface providing all necessary functionality in a mobile-optimized format
- **DARK BLUE THEME IMPLEMENTATION**: Redesigned mini-app with sophisticated dark blue theme for optimal text readability. Implemented elegant gradient backgrounds (#1a2332 to #263040), high-contrast white text (#e8eaed), and vibrant blue accents (#2196f3). Enhanced visual elements with subtle shadows, proper button states, and improved form controls. Theme ensures excellent readability across all interface elements while maintaining professional appearance
- **ENHANCED DARK MODE COMPATIBILITY**: Improved dark mode experience with deeper background colors (#0f1419), enhanced contrast ratios, better button states with gradients and shadows, improved form controls with better focus states, and comprehensive styling for all UI elements including modals, cards, and navigation tabs
- **REPLIT IMPORT MIGRATION**: Completed full migration from Replit Agent to native Replit environment with comprehensive verification
- **TRADING MENU UI OPTIMIZATION**: Reorganized trading tab buttons into 2-column layout to reduce vertical height while maintaining all functionality. Improved user experience by making the interface more compact
- **PORTFOLIO & MARGIN CONSOLIDATION**: Merged portfolio summary and margin dashboard into unified "Portfolio & Margin Overview" for cleaner interface. Combined all functionality including account summary, risk assessment, active positions, configured positions, and portfolio statistics. Eliminated duplicate code and redundant menus while preserving all features - users now get comprehensive view in single screen
- **PORTFOLIO & ANALYTICS OVERHAUL**: Fixed all key issues in Portfolio & Analytics tab. Enhanced Portfolio Summary to show real account data, holdings, and position statistics. Completely rewrote Performance Analytics with comprehensive P&L analysis, trading activity metrics, position breakdowns, and risk assessments. Improved Recent Trades to combine executed trades with current position status for unified view
- **COMPREHENSIVE MARGIN DASHBOARD**: Added sophisticated margin tracking system with real-time portfolio analytics. Created margin dashboard showing account balance, position margin, free margin, floating P&L, margin utilization levels, and individual position details with risk metrics and color-coded indicators
- **CONFIGURATION TAB REMOVAL**: Completely removed the useless configuration tab and all associated callback handlers per user request. Deleted the configuration menu from main navigation, removed get_config_menu() function, removed menu_config callback handler, and cleaned up all set_trade_name functionality. Simplified breakeven handling to work directly on trade configs instead of global user configs
- **UI CLEANUP**: Removed redundant "Configure Trade" button since "Edit Trade" provides identical functionality. Cleaned up associated callback handlers and helper functions to streamline the interface
- **HEADER ENHANCEMENT**: Added break-even and trailing stop information to trade headers. Headers now display complete trade configuration including break-even settings and trailing stop status with parameters
- **TERMINOLOGY UPDATE**: Changed "Multi-Trade Manager" to "Positions Manager" throughout the entire interface. Updated all callback handlers, menu functions, and user-facing text to consistently use "positions" terminology for better clarity
**August 10, 2025**: Completed migration from Replit Agent to Replit environment with comprehensive debugging fixes
- Successfully migrated Telegram trading bot with full functionality
- Created missing dashboard.html template with beautiful Bootstrap design
- Fixed inline keyboard functionality and callback query handling
- Added comprehensive trading features: market/limit orders, entry prices, take profits, stop loss
- Enhanced text input handling for numeric values (amounts, prices, percentages)
- Improved error handling in dashboard JavaScript with proper API response validation
- All bot interactions now work correctly including edit trade, set amounts, entry prices, and take profits
- Flask app running successfully on port 5000 with auto-reload functionality
- Added seamless wizard flow: leverage → amount → entry price → take profits → stop loss
- Fixed break-even settings, trailing stop configuration, and default settings management
- Enhanced multi-take profit system with proper TP1/TP2/TP3 configuration
- Debugged all callback handlers and text input processing for complete functionality
- **FINAL UPDATE**: Removed quick config options after trading pair selection per user request - users now go directly to trading menu
- **TAKE PROFIT SYSTEM REWRITE**: Completely rewrote buggy take profit system with new two-phase approach: set percentages first (TP1, TP2, TP3), then set position allocation for each TP level with validation to ensure total allocation doesn't exceed 100%
- **LIMIT ORDER BUG FIX**: Fixed critical bug where limit orders were always executing as market orders. Added proper state tracking with `waiting_for_limit_price` flag and `entry_type` field. System now correctly executes LIMIT orders at user-specified prices vs MARKET orders at current market price
- **ALLOCATION RESET BUG FIX**: Fixed missing callback handlers for "Reset All Allocations" and "Reset Last Allocation" buttons. Added proper handlers for `tp_reset_all_alloc` and `tp_reset_last_alloc` with smart logic to reset all or just the most recent allocation
- **UI CLEANUP**: Removed useless "Default Settings" button from configuration menu per user request. Cleaned up associated callback handlers and helper functions to streamline the interface
- **TRAILING STOP REWRITE**: Completely rewrote buggy trailing stop system with clean implementation. New system has exactly three options: Set Trail Percentage button, Set Activation Price button, and Disable Trailing Stop button. Removed all legacy trailing stop code and replaced with focused, bug-free implementation with proper state tracking
- **MENU REORGANIZATION**: Moved Break-even Settings and Trailing Stop from Configuration tab to Trading tab per user request. Removed "Reset All Settings" button from Configuration menu. Updated all navigation flows to return users to Trading menu after configuring break-even or trailing stop settings
- **PROGRESS INDICATORS**: Added comprehensive trade information display and progress tracking for every trade configuration step. Each configuration screen now shows trade header with real-time progress bar (█████░), completion status (✅⏳), current step indicator, and visual progress tracking across 6 core steps: Symbol, Side, Amount, Entry, Take Profits, Stop Loss. Fixed main trading tab to properly display progress indicators and ensured all navigation paths back to trading menu maintain progress display. Enhanced trade header to include complete settings summary showing pair, side, amount, leverage, entry type, take profits count, and stop loss percentage

## System Architecture

### Flask Web Application
The application uses Flask as the primary web framework serving multiple purposes:
- **Webhook Integration**: Processes Telegram bot updates via `/webhook` endpoint for real-time message handling with comprehensive validation
- **Health Monitoring**: Provides `/health` endpoint for external uptime monitoring services  
- **Web Dashboard**: Serves an HTML interface showing real-time bot status, statistics, and active trades with Bootstrap styling and gradient backgrounds

The Flask app maintains a single TelegramBot instance that orchestrates all user interactions through dependency injection.

### Multi-Trade Management System
The core architectural innovation centers around the MultiTradeManager class enabling concurrent trade management:
- **User Isolation**: Each Telegram user maintains multiple independent trading configurations using chat_id-based separation
- **Trade Selection**: Users can switch between different trade setups using a selection system with unique trade IDs
- **Bot Orchestration**: Manages multiple TradingBot instances running concurrently for different trades
- **State Management**: Tracks user selections and maintains trade configuration state across sessions

### Trading Engine Architecture
The trading system follows a modular design pattern with clear separation of concerns:
- **TradeConfig**: Configuration objects containing all trade parameters including symbol, entry/exit prices, three-level take profit system, leverage, and position sizing percentages
- **TradingBot**: Individual bot instances that execute trades with state tracking for entry fills, take profit levels, stop loss management, and trailing stop functionality
- **Position Management**: Supports partial position closing at different take profit levels with configurable size percentages
- **Risk Management**: Implements breakeven stop loss movement and trailing stop activation based on profit thresholds

### Portfolio Management
The PortfolioTracker provides comprehensive analytics and trade history:
- **Multi-User Support**: Maintains separate portfolio data for each Telegram user with defaultdict initialization
- **Trade History**: Records detailed trade events, executions, and P&L calculations with timestamps
- **Performance Metrics**: Tracks win/loss ratios, realized/unrealized P&L, and symbol-specific performance statistics
- **Daily Analytics**: Maintains daily P&L tracking and performance summaries

### Telegram Bot Integration
The bot uses webhook-based communication with the Telegram Bot API:
- **Message Processing**: Handles both direct messages and callback queries from inline keyboards
- **HTML Formatting**: Rich text display with HTML parsing for enhanced user interface
- **Interactive Menus**: Provides keyboard-based navigation for trade configuration and management
- **Real-time Updates**: Sends trade execution notifications and status updates to users
- **Error Handling**: Comprehensive validation and timeout management for API requests

### Data Management
The system uses in-memory storage with dictionary-based data structures:
- **User Data Isolation**: Separate data spaces for each Telegram user identified by chat_id
- **Trade Configuration Persistence**: TradeConfig objects store all trade parameters and state
- **Session Management**: Workflow tracking and user input state management
- **Performance Data**: Aggregated statistics and historical trade records

## External Dependencies

### Toobit Exchange Integration
- **API Client**: Custom ExchangeClient class handling Toobit futures API communication
- **Authentication**: HMAC-SHA256 signature-based API authentication with rate limiting
- **Testnet Support**: Configurable testnet/mainnet environment switching
- **Simulation Mode**: Fallback simulation when API credentials are unavailable

### Telegram Bot API
- **Webhook Communication**: Real-time message processing via Telegram webhook endpoints
- **Rich Messaging**: HTML-formatted messages with inline keyboard support
- **File Uploads**: Support for sending charts and trading screenshots

### Python Libraries
- **Flask**: Web application framework for webhook handling and dashboard
- **Requests/aiohttp**: HTTP client libraries for API communication
- **Threading/Asyncio**: Concurrent processing for multiple trading bots
- **Logging**: Comprehensive logging system for debugging and monitoring

### Environment Configuration
- **API Credentials**: Toobit API key and secret via environment variables
- **Bot Token**: Telegram Bot API token for authentication
- **Flask Secret**: Session management and security token generation
- **Testnet Toggle**: Environment-based trading mode configuration