#!/bin/bash
# 用獨立的 profile 目錄開 Chrome，不影響現有的 Chrome 視窗
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/Desktop/bank_scraper/chrome_profile" \
  --no-first-run \
  --no-default-browser-check \
  &
