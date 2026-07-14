#!/bin/zsh
# One-time setup: python venv + the three launchd email jobs.
# Safe to re-run; it just rebuilds the plists and reloads them.
# AGENT_DIR override is for testing plist generation without touching launchd.
set -e
cd "$(dirname "$0")"
DIR=$PWD
USER_LABEL="com.$(whoami).health-coach"
AGENT_DIR=${AGENT_DIR:-$HOME/Library/LaunchAgents}

[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[[ -f .env ]] || cp .env.example .env
mkdir -p logs "$AGENT_DIR"

# job name | schedule keys | program arguments
make_plist() {  # $1=job $2=schedule-xml $3...=program args
  local job=$1 schedule=$2; shift 2
  local args=""
  for a in "$@"; do args+="        <string>$a</string>\n"; done
  cat > "$AGENT_DIR/$USER_LABEL.$job.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$USER_LABEL.$job</string>
    <key>ProgramArguments</key>
    <array>
$(print -- "$args")    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>StartCalendarInterval</key>
    <dict>
$schedule
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$DIR/logs/$job.log</string>
    <key>StandardErrorPath</key>
    <string>$DIR/logs/$job.log</string>
</dict>
</plist>
EOF
  if [[ -z ${SKIP_LAUNCHCTL:-} ]]; then
    launchctl unload "$AGENT_DIR/$USER_LABEL.$job.plist" 2>/dev/null || true
    launchctl load "$AGENT_DIR/$USER_LABEL.$job.plist"
  fi
}

daily_sched="        <key>Hour</key>
        <integer>21</integer>"
sunday_9am="        <key>Weekday</key>
        <integer>0</integer>
        <key>Hour</key>
        <integer>9</integer>"
sunday_8pm="        <key>Weekday</key>
        <integer>0</integer>
        <key>Hour</key>
        <integer>20</integer>"

make_plist daily  "$daily_sched" "$DIR/.venv/bin/python3" "$DIR/coach_email.py" daily
make_plist weigh  "$sunday_9am"  "$DIR/.venv/bin/python3" "$DIR/coach_email.py" weight-prompt
make_plist weekly "$sunday_8pm"  "$DIR/weekly_email.sh"

echo "Installed. Next: fill in .env, then run .venv/bin/python3 authorize.py"
