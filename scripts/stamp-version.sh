#!/bin/sh
# Stamp a fresh version into the site so browsers fetch new code after a deploy.
# GitHub Pages caches files for 10 minutes and its headers can't be changed, so
# every asset URL carries ?v=<version>, and open tabs poll docs/version.json
# and reload themselves when it changes. Run this before committing site changes.
set -e
cd "$(dirname "$0")/../docs"
v=$(date +%Y%m%d%H%M%S)
perl -pi -e "s/\\?v=\\d+/?v=$v/g" index.html app.js
perl -pi -e "s/^const APP_VERSION = \"\\d+\";/const APP_VERSION = \"$v\";/" app.js
printf '{ "version": "%s" }\n' "$v" > version.json
echo "stamped version $v"
