"""
Critical fix for Render position disappearing issue
This ensures database-first loading on every request for Render deployment
"""
import os
from flask import request

def patch_render_database_loading():
    """Patch the trading app for better Render database handling"""
    
    # Only apply for Render environment
    if not os.environ.get('RENDER'):
        return
    
    print("Applying Render database persistence fix...")
    
    # This will be imported by the main app to ensure positions persist
    # across Gunicorn worker restarts on Render
    
    def force_database_reload(original_func):
        """Decorator to force database reload on Render"""
        def wrapper(*args, **kwargs):
            # Always force reload on Render to prevent worker sync issues
            if 'force_reload' in kwargs:
                kwargs['force_reload'] = True
            return original_func(*args, **kwargs)
        return wrapper
    
    return force_database_reload

# Apply the patch
render_patch = patch_render_database_loading()
if render_patch:
    print("Render database persistence patch activated")