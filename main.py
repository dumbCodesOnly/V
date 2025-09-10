"""
Main entry point for the Multi-Exchange Trading Bot.

This module serves as the primary entry point for the Flask application,
handling environment-specific configurations and server startup.
"""

import os
import logging
from typing import Optional

from api.app import app

# Apply Render performance and session fixes
try:
    import scripts.render_deploy  # type: ignore
except ImportError:
    pass

# Set up logging for different environments
from config import Environment, get_log_level

logging.basicConfig(level=getattr(logging, get_log_level()))

# For Vercel deployment - expose the Flask app
application = app


def setup_app_secret() -> None:
    """Set up application secret key for all environments."""
    if not app.secret_key or app.secret_key == "dev-secret-key":
        from config import SecurityConfig

        secret_key: Optional[str] = os.environ.get(
            "SESSION_SECRET", SecurityConfig.DEFAULT_SESSION_SECRET
        )
        app.secret_key = secret_key


def main() -> None:
    """Main entry point for the application."""
    setup_app_secret()

    port: int = int(os.environ.get("PORT", Environment.DEFAULT_PORT))

    # Configure debug mode based on environment
    debug_mode: bool = Environment.IS_DEVELOPMENT or Environment.IS_REPLIT

    if Environment.IS_RENDER:
        # Render production configuration
        logging.info(f"Starting application on Render - Port: {port}")
        app.run(host="0.0.0.0", port=port, debug=False)
    elif Environment.IS_VERCEL:
        # Vercel handles the serving, this shouldn't run
        logging.info("Running in Vercel environment")
    else:
        # Replit or development environment
        logging.info(f"Starting application in development mode - Port: {port}")
        app.run(host="0.0.0.0", port=port, debug=debug_mode)


if __name__ == "__main__":
    main()
