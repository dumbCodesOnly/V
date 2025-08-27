"""
Critical Render Session & Database Persistence Fix
This addresses the core issue of positions disappearing on Render
"""
import os

def apply_render_session_fix():
    """Apply session management fixes for Render deployment"""
    
    if not os.environ.get('RENDER'):
        return False
        
    # Set session configuration for multi-worker environment
    os.environ['FLASK_SESSION_TYPE'] = 'filesystem'
    os.environ['FLASK_SESSION_PERMANENT'] = 'False'
    os.environ['FLASK_SESSION_KEY_PREFIX'] = 'trading-bot:'
    
    # Force database-first operations
    os.environ['RENDER_FORCE_DB_RELOAD'] = '1'
    
    # Disable worker memory sharing
    os.environ['RENDER_NO_MEMORY_CACHE'] = '1'
    
    print("Applied Render session persistence fix")
    return True

# Apply fix when imported
if apply_render_session_fix():
    print("Render persistence optimization active")