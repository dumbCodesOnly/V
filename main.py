import os
import logging
from api.app import app

# Apply Render performance and session fixes
try:
    import scripts.render_deploy
except ImportError:
    pass

# Set up logging for different environments
from config import Environment, get_log_level
logging.basicConfig(level=getattr(logging, get_log_level()))

# For Vercel deployment - expose the Flask app
application = app

# Ensure the app has the proper secret key for all environments
if not app.secret_key or app.secret_key == "dev-secret-key":
    from config import SecurityConfig
    app.secret_key = os.environ.get("SESSION_SECRET", SecurityConfig.DEFAULT_SESSION_SECRET)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", Environment.DEFAULT_PORT))
    
    # Configure debug mode based on environment
    debug_mode = Environment.IS_DEVELOPMENT or Environment.IS_REPLIT
    
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
