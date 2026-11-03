#!/bin/sh
# launchd の plist を生成して登録する（Mac で実行）。実物は絶対パスを含むので git に入れない。
set -eu
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.world-events.daily.plist"
mkdir -p "$REPO_DIR/data"
sed "s#@REPO_DIR@#$REPO_DIR#g" "$REPO_DIR/ops/launchd/com.world-events.daily.plist.tmpl" > "$DEST"
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
echo "登録した: $DEST"
echo "スリープ解除: sudo pmset repeat wakeorpoweron MTWRFSU 00:55:00"
