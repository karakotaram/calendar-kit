#!/bin/bash
# Refresh the local-only sources and publish them.
#
# CI cannot reach the sources registered `runs_in_ci: false`: venues that block
# GitHub's IP ranges, or that only load in a visible browser window. Their
# events only change when this runs on a Mac with someone logged in.
#
# It pulls, runs scrape_local.py, and commits and pushes data/events.json only
# if it changed. Pushing to main deploys. Safe to run by hand.
#
#   scripts/weekly_local_scrape.sh             # the real thing
#   scripts/weekly_local_scrape.sh --dry-run   # scrape, leave the result uncommitted
#   scripts/weekly_local_scrape.sh --install   # run it Mondays at 10:00 via launchd
#   scripts/weekly_local_scrape.sh --uninstall # stop that
#
# The launchd label is org.<short_name>.scrape-local, from calendar.config.yaml
# `site.short_name`; the log is logs/weekly_local_scrape.log. If the Mac is
# asleep at 10:00, launchd runs it at the next wake.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:$PATH"
PY="$REPO/.venv/bin/python"

[ -x "$PY" ] || { echo "No virtualenv at $REPO/.venv - create it first (see the README)"; exit 1; }
cfg() { "$PY" -c "from src import config; print(config.$1)"; }
SITE_NAME="$(cfg SITE_NAME)"
SHORT_NAME="$(cfg SHORT_NAME | tr '[:upper:]' '[:lower:]')"
LABEL="org.${SHORT_NAME}.scrape-local"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "${1:-}" in
    --install)
        mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"
        cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$REPO/scripts/weekly_local_scrape.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>10</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$REPO/logs/weekly_local_scrape.log</string>
    <key>StandardErrorPath</key>
    <string>$REPO/logs/weekly_local_scrape.log</string>
</dict>
</plist>
PLIST
        launchctl unload "$PLIST" 2>/dev/null
        launchctl load "$PLIST" && echo "Installed $LABEL: Mondays at 10:00, log at logs/weekly_local_scrape.log"
        exit $?
        ;;
    --uninstall)
        launchctl unload "$PLIST" 2>/dev/null
        rm -f "$PLIST" && echo "Removed $LABEL"
        exit 0
        ;;
esac

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
notify() { osascript -e "display notification \"$1\" with title \"$SITE_NAME\"" >/dev/null 2>&1 || true; }
fail() { say "FAILED: $1"; notify "Weekly local scrape failed: $1"; exit 1; }

say "weekly local scrape starting in $REPO"
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || fail "checkout is not on main"
# Someone's work in progress is not ours to commit or rebase over
git diff --quiet HEAD -- data/ src/ registry/ scrape_local.py || fail "uncommitted changes in data/, src/ or registry/"
git pull -q --rebase --autostash || fail "git pull failed"

mkdir -p logs
output=$("$PY" scrape_local.py 2>&1) || { echo "$output"; fail "scrape_local.py refused to write (see log)"; }
echo "$output" | grep -E "Scraped [0-9]+ events from|failed|Keeping stored|Total events"
failed=$(echo "$output" | grep -E "^.* - ERROR - Scraper .* failed" | sed -E 's/.* - ERROR - Scraper (.*) failed: .*/\1/' | paste -sd, - | sed 's/,/, /g')

# What a run may change: the events, and the geocoder's cache if it looked anything up
changed=()
for f in data/events.json data/geocode-cache.json; do
    [ -f "$f" ] || continue
    if ! git ls-files --error-unmatch "$f" >/dev/null 2>&1 || ! git diff --quiet -- "$f"; then
        changed+=("$f")
    fi
done

if [ "${#changed[@]}" -eq 0 ]; then
    say "no change to data/events.json"
    [ -n "$failed" ] && fail "nothing new; these sources failed: $failed"
    exit 0
fi
if [ "$DRY_RUN" = 1 ]; then
    say "dry run: ${changed[*]} left modified and uncommitted"
    exit 0
fi

git add "${changed[@]}"
git commit -q -m "Refresh local-only sources - $(date -u '+%Y-%m-%d %H:%M UTC')" || fail "git commit failed"
if ! git pull -q --rebase --autostash; then
    # CI pushed new data while this ran. Drop this run's commit rather than
    # leave a conflict behind; next week's run starts from a clean main.
    git rebase --abort 2>/dev/null
    git reset -q --keep HEAD~1
    git pull -q --rebase --autostash
    fail "main moved during the scrape; nothing pushed, rerun the script"
fi
git push -q || fail "git push failed; the commit is local, push it by hand"

say "pushed"
if [ -n "$failed" ]; then
    notify "Weekly local scrape pushed, but these sources failed: $failed"
else
    notify "Weekly local scrape pushed"
fi
