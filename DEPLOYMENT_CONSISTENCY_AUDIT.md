# Deployment Consistency Audit - August 2025

## Current Inconsistencies Between Replit and Vercel

### 1. Entry Points
**Replit:**
- Main workflow: `gunicorn main:app` (uses main.py)
- Failed workflow: `python app.py` (incorrect, file doesn't exist)

**Vercel:**
- Entry: `api/app.py` directly via vercel.json
- Backup: `api/index.py` (imports from api.app but unused)

### 2. Dependencies
**Replit:**
- Uses `pyproject.toml` (clean, versioned)
- Has messy root `requirements.txt` with duplicates
- Packages managed via pyproject.toml

**Vercel:**
- Uses `api/requirements.txt` (clean, versioned)
- Ignores root requirements.txt

### 3. Environment Detection
Both deployments use the same `api/app.py` which detects:
```python
if os.environ.get("VERCEL"):
    # Vercel-specific initialization
else:
    # Replit-specific initialization
```

## Issues Found

### Critical Issues:
1. **Telegram Trading Bot workflow fails** - tries to run non-existent `app.py`
2. **Different dependency sources** - could lead to version mismatches

### Minor Issues:
1. **Unused api/index.py** - confusing entry point for Vercel
2. **Messy root requirements.txt** - contains duplicates

## Current Status: MOSTLY CONSISTENT

Despite the inconsistencies, both environments work because:
1. They both ultimately use the same `api/app.py` Flask application
2. The environment detection ensures proper initialization
3. Recent fixes (limit order logic) are applied to shared codebase

## Recommendations

1. **Fix failing workflow** - Update to use correct Python module
2. **Standardize dependencies** - Use single source of truth
3. **Clean up unused files** - Remove redundant entry points
4. **Document deployment differences** - Make intentional choices clear

## Impact Assessment: LOW RISK

The core trading functionality is consistent because both environments use the same Flask app with environment-specific initialization.