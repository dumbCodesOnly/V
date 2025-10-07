-- Database migration to add scaled_entries column to smc_signal_cache table
-- This fixes the issue where scaled entries were lost after page refresh

-- Add scaled_entries column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'smc_signal_cache'
        AND column_name = 'scaled_entries'
    ) THEN
        ALTER TABLE smc_signal_cache ADD COLUMN scaled_entries TEXT;
        RAISE NOTICE 'Column scaled_entries added to smc_signal_cache table';
    ELSE
        RAISE NOTICE 'Column scaled_entries already exists in smc_signal_cache table';
    END IF;
END $$;
