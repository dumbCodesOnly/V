"""
Database migration to add scaled_entries column to smc_signal_cache table.

This migration adds support for persisting scaled entry configurations in the signal cache,
fixing the issue where scaled entries were lost after page refresh.

Run this script to update your database schema:
    python scripts/migrate_add_scaled_entries.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.models import db
from api.app import app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_migration():
    """Add scaled_entries column to smc_signal_cache table if it doesn't exist"""
    
    with app.app_context():
        try:
            # Check if column already exists
            from sqlalchemy import inspect, text
            
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('smc_signal_cache')]
            
            if 'scaled_entries' in columns:
                logger.info("Column 'scaled_entries' already exists in smc_signal_cache table. No migration needed.")
                return True
            
            # Add the column
            logger.info("Adding 'scaled_entries' column to smc_signal_cache table...")
            
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE smc_signal_cache ADD COLUMN scaled_entries TEXT"
                ))
                conn.commit()
            
            logger.info("Successfully added 'scaled_entries' column to smc_signal_cache table.")
            logger.info("Migration completed successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False


if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
