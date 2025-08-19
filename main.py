import os
import logging
from api.app import app

# Set up logging for Replit environment
logging.basicConfig(level=logging.DEBUG)

# For Vercel deployment - expose the Flask app
application = app

# Ensure the app has the proper secret key for Replit
if not app.secret_key or app.secret_key == "dev-secret-key":
    app.secret_key = os.environ.get("SESSION_SECRET", "replit-default-secret-key-12345")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
