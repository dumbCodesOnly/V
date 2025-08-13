# Codebase Consolidation Plan

## Current Issues
- 20+ markdown documentation files with overlapping content
- Duplicate Python files (app.py in root and api/, models.py duplicated)
- 6+ webhook setup scripts with similar functionality
- Multiple backup files cluttering the repository
- Redundant Vercel configuration files

## Consolidation Strategy

### 1. Documentation Consolidation
**Keep:** 
- `replit.md` (main project documentation)
- `VERCEL_DEPLOYMENT_FIXED.md` (latest working guide)

**Merge into a single comprehensive guide:**
- All deployment guides
- All webhook setup guides
- All security guides

**Remove:**
- Outdated deployment files
- Redundant backup documentation

### 2. Python File Consolidation
**Keep:**
- `api/app.py` (main Vercel-compatible app)
- `api/models.py` (database models)
- `api/index.py` (Vercel entry point)
- `main.py` (Replit entry point)

**Remove:**
- Root `app.py` (duplicate of api/app.py)
- Root `models.py` (duplicate of api/models.py)
- All backup files (`*_backup.py`)
- `api/app_streamlined.py` (now unused)

### 3. Configuration Cleanup
**Keep:**
- `vercel.json` (main Vercel config)
- `requirements.txt` (main requirements)
- `api/requirements.txt` (Vercel-specific)

**Remove:**
- Redundant Vercel configs
- Backup requirement files

### 4. Script Consolidation
**Keep:**
- One unified webhook setup script

**Remove:**
- Multiple webhook setup variations
- Deployment script duplicates

## Implementation Steps
1. Create unified documentation
2. Remove duplicate Python files
3. Clean up configuration files
4. Update imports and references
5. Test Vercel deployment
6. Update replit.md with new structure