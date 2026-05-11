#!/bin/bash
# Run as fiet via: ssh isp 'sudo -iu fiet bash' < this_file
set +e
cd ~
echo "===pwd==="; pwd
echo "===home==="; ls -la | head -30
echo "===STATE==="; cat live/self/state.md 2>/dev/null
echo "===ACTIVE==="; cat live/active_session.json 2>/dev/null
echo "===COMM==="; cat live/self/comm_state.json 2>/dev/null
echo "===TODO==="; wc -l live/self/todo*.jsonl 2>/dev/null; head -5 live/self/todo.jsonl 2>/dev/null
echo "===PROC==="; ps -eo pid,user,etime,cmd --sort=-etime | grep -E 'daemon|fiam|dashboard|claude' | grep -v grep | head -20
echo "===SYSTEMD==="; systemctl --user list-units --type=service 2>/dev/null | head; sudo systemctl list-units --type=service 2>/dev/null | grep -Ei 'fiam' | head
echo "===LOGS==="; ls -la live/logs/ 2>/dev/null; ls -la fiam-code/logs/ 2>/dev/null | head
echo "===INBOX_RECENT==="; ls -t live/inbox/ 2>/dev/null | head -5
echo "===API==="; curl -s http://127.0.0.1:8766/api/status 2>/dev/null | head -c 400; echo
echo "===HEALTH==="; curl -s http://127.0.0.1:8766/api/health 2>/dev/null | head -c 800; echo
