"""
Render Performance Optimization Patches
Apply these optimizations specifically when running on Render
"""
import os
from config import Environment

def optimize_for_render():
    """Apply Render-specific performance optimizations"""
    if not Environment.IS_RENDER:
        return
    
    # Database connection optimizations
    os.environ.setdefault('SQLALCHEMY_POOL_RECYCLE', '300')
    os.environ.setdefault('SQLALCHEMY_POOL_TIMEOUT', '10')
    os.environ.setdefault('SQLALCHEMY_POOL_SIZE', '3')
    os.environ.setdefault('SQLALCHEMY_MAX_OVERFLOW', '5')
    
    # Cache optimizations
    os.environ.setdefault('CACHE_DEFAULT_TIMEOUT', '30')
    os.environ.setdefault('CACHE_THRESHOLD', '100')
    
    # Reduce logging verbosity for production
    import logging
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    print("Applied Render performance optimizations")

# Apply optimizations when imported
optimize_for_render()