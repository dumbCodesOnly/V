from app import app

# For Vercel deployment - expose app as both 'app' and 'application'
application = app

def handler(event, context):
    """AWS Lambda/Vercel handler"""
    return app(event, context)

# For standard deployment
if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
