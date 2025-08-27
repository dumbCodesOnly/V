-- Migration script to fix Toobit testnet issue on Neon PostgreSQL
-- Run this in your Neon database console

-- Fix existing Toobit credentials that incorrectly have testnet_mode = true
UPDATE user_credentials 
SET testnet_mode = false 
WHERE exchange_name = 'toobit' AND testnet_mode = true;

-- Verify the fix
SELECT id, telegram_user_id, exchange_name, testnet_mode 
FROM user_credentials 
WHERE exchange_name = 'toobit';

-- Optional: Set default to false for future Toobit credentials
-- This should already be handled by the application code, but can be added as a constraint
-- ALTER TABLE user_credentials ADD CONSTRAINT check_toobit_no_testnet 
-- CHECK (NOT (exchange_name = 'toobit' AND testnet_mode = true));