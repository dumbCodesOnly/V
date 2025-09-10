"""
Main entry point for the Multi-Exchange Trading Bot.

This module serves as the primary entry point for the Flask application,
handling environment-specific configurations and server startup.
"""

import logging
import os
from typing import Optional

from api.app import app

# Apply Render performance and session fixes
try:
    import scripts.render_deploy  # type: ignore  # noqa: F401
except ImportError:
    pass

# Set up logging for different environments
from config import Environment, get_log_level

logging.basicConfig(level=getattr(logging, get_log_level()))

# For Vercel deployment - expose the Flask app
application = app


def setup_app_secret() -> None:
    """Set up application secret key for all environments."""
    # Check for insecure default secret key
    insecure_defaults = ["dev-secret-key", "replit-default-secret-key-12345"]
    if not app.secret_key or app.secret_key in insecure_defaults:
        from config import SecurityConfig

        secret_key: Optional[str] = os.environ.get("SESSION_SECRET")
        if not secret_key:
            if Environment.IS_DEVELOPMENT or Environment.IS_REPLIT:
                # Only use default in development environments
                secret_key = SecurityConfig.DEFAULT_SESSION_SECRET
                logging.warning("Using default session secret in development mode")
            else:
                raise ValueError(
                    "SESSION_SECRET environment variable is required for production"
                )
        app.secret_key = secret_key


def main() -> None:
    """Main entry point for the application."""
    setup_app_secret()

    port: int = int(os.environ.get("PORT", Environment.DEFAULT_PORT))

    # Configure debug mode based on environment
    debug_mode: bool = Environment.IS_DEVELOPMENT or Environment.IS_REPLIT

    if Environment.IS_RENDER:
        # Render production configuration - binding to all interfaces is required for cloud deployment
        logging.info(f"Starting application on Render - Port: {port}")
        app.run(host="0.0.0.0", port=port, debug=False)  # nosec B104
    elif Environment.IS_VERCEL:
        # Vercel handles the serving, this shouldn't run
        logging.info("Running in Vercel environment")
    else:
        # Replit or development environment - binding to all interfaces is required for cloud IDE
        logging.info(f"Starting application in development mode - Port: {port}")
        app.run(host="0.0.0.0", port=port, debug=debug_mode)  # nosec B104


if __name__ == "__main__":
    main()
