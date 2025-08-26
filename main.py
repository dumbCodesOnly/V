import os
import logging
from api.app import app

# Set up logging for Replit environment
logging.basicConfig(level=logging.DEBUG)

# For Vercel deployment - expose the Flask app
application = app

# Ensure the app has the proper secret key for Replit
if not app.secret_key or app.secret_key == "dev-secret-key":
    from config import SecurityConfig
    app.secret_key = os.environ.get("SESSION_SECRET", SecurityConfig.DEFAULT_SESSION_SECRET)

if __name__ == "__main__":
    from config import Environment
    port = int(os.environ.get("PORT", Environment.DEFAULT_PORT))
    app.run(host="0.0.0.0", port=port, debug=True)
