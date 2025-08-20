-- Vercel/Neon Database Schema Creation Script
-- This ensures all required columns exist for the trading bot
-- Updated for real trading integration with exchange order tracking

-- Create user_credentials table
CREATE TABLE IF NOT EXISTS user_credentials (
    id SERIAL PRIMARY KEY,
    telegram_user_id VARCHAR(50) UNIQUE NOT NULL,
    telegram_username VARCHAR(100),
    exchange_name VARCHAR(50) DEFAULT 'toobit',
    api_key_encrypted TEXT,
    api_secret_encrypted TEXT,
    passphrase_encrypted TEXT,
    testnet_mode BOOLEAN DEFAULT TRUE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used TIMESTAMP
);

-- Create user_trading_sessions table
CREATE TABLE IF NOT EXISTS user_trading_sessions (
    id SERIAL PRIMARY KEY,
    telegram_user_id VARCHAR(50) NOT NULL,
    session_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_end TIMESTAMP,
    total_trades INTEGER DEFAULT 0,
    successful_trades INTEGER DEFAULT 0,
    failed_trades INTEGER DEFAULT 0,
    total_volume FLOAT DEFAULT 0.0,
    api_calls_made INTEGER DEFAULT 0,
    api_errors INTEGER DEFAULT 0,
    last_api_error TEXT,
    is_active BOOLEAN DEFAULT TRUE
);

-- Create trade_configurations table with all required columns
CREATE TABLE IF NOT EXISTS trade_configurations (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(50) NOT NULL,
    telegram_user_id VARCHAR(50) NOT NULL,
    name VARCHAR(200) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    amount FLOAT NOT NULL,
    leverage INTEGER DEFAULT 1,
    entry_type VARCHAR(20) DEFAULT 'market',
    entry_price FLOAT DEFAULT 0.0,
    take_profits TEXT,
    stop_loss_percent FLOAT DEFAULT 0.0,
    breakeven_after FLOAT DEFAULT 0.0,
    breakeven_sl_triggered BOOLEAN DEFAULT FALSE,
    trailing_stop_enabled BOOLEAN DEFAULT FALSE,
    trail_percentage FLOAT DEFAULT 0.0,
    trail_activation_price FLOAT DEFAULT 0.0,
    status VARCHAR(20) DEFAULT 'configured',
    position_margin FLOAT DEFAULT 0.0,
    unrealized_pnl FLOAT DEFAULT 0.0,
    current_price FLOAT DEFAULT 0.0,
    position_size FLOAT DEFAULT 0.0,
    position_value FLOAT DEFAULT 0.0,
    realized_pnl FLOAT DEFAULT 0.0,
    final_pnl FLOAT DEFAULT 0.0,
    closed_at TIMESTAMP,
    exchange_order_id VARCHAR(100),
    exchange_client_order_id VARCHAR(100),
    exchange_tp_sl_orders TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Add missing columns if they don't exist (for existing databases)
ALTER TABLE trade_configurations ADD COLUMN IF NOT EXISTS breakeven_sl_triggered BOOLEAN DEFAULT FALSE;
ALTER TABLE trade_configurations ADD COLUMN IF NOT EXISTS realized_pnl FLOAT DEFAULT 0.0;
ALTER TABLE trade_configurations ADD COLUMN IF NOT EXISTS exchange_order_id VARCHAR(100);
ALTER TABLE trade_configurations ADD COLUMN IF NOT EXISTS exchange_client_order_id VARCHAR(100);
ALTER TABLE trade_configurations ADD COLUMN IF NOT EXISTS exchange_tp_sl_orders TEXT;

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_user_credentials_telegram_user_id ON user_credentials(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_user_trading_sessions_telegram_user_id ON user_trading_sessions(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_trade_configurations_telegram_user_id ON trade_configurations(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_trade_configurations_trade_id ON trade_configurations(trade_id);
CREATE INDEX IF NOT EXISTS idx_trade_configurations_status ON trade_configurations(status);