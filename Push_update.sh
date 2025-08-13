#!/bin/bash
git add .
git commit -m "Update from Replit" || git commit --allow-empty -m "Empty update commit"
git push https://dumbCodesOnly:$GITHUB_PAT@github.com/dumbCodesOnly/V0.01.git main
