"""
Gunicorn configuration optimized for different deployment environments
"""
import os
import multiprocessing

# Detect environment
IS_RENDER = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
IS_VERCEL = bool(os.environ.get("VERCEL"))
IS_REPLIT = not IS_RENDER and not IS_VERCEL

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# Worker processes
if IS_RENDER:
    # Render: Use multiple workers for better performance
    workers = min(multiprocessing.cpu_count() * 2 + 1, 4)
    worker_class = "sync"
    worker_connections = 1000
elif IS_VERCEL:
    # Vercel: Single worker for serverless
    workers = 1
    worker_class = "sync"
    worker_connections = 50
else:
    # Replit: Single worker for development
    workers = 1
    worker_class = "sync"
    worker_connections = 100

# Worker configuration
timeout = 120  # seconds
keepalive = 5   # seconds
max_requests = 1000
max_requests_jitter = 50

# Logging
loglevel = "info" if IS_RENDER else "debug"
accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = "trading-bot"

# SSL (if needed)
keyfile = None
certfile = None

# Server mechanics
daemon = False
pidfile = None
tmp_upload_dir = None
user = None
group = None

# Application
module = "main:app"
pythonpath = "."

# Environment-specific optimizations
if IS_RENDER:
    # Render production optimizations
    preload_app = True
    worker_tmp_dir = "/dev/shm"  # Use memory for tmp files
    forwarded_allow_ips = "*"
    secure_scheme_headers = {
        'X-FORWARDED-PROTOCOL': 'ssl',
        'X-FORWARDED-PROTO': 'https',
        'X-FORWARDED-SSL': 'on'
    }
elif IS_REPLIT:
    # Replit development settings
    reload = True
    preload_app = False