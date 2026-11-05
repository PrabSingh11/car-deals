#!/usr/bin/env bash
# Daily: pull any new videos from the tracked creators, rebuild the page,
# and push to GitHub so the live site updates. Exits non-zero on real failure
# so cronwrap.sh raises a Telegram alert.
set -eo pipefail
cd /root/car-deals

# refresh the TikTok cookie from the live session (it rotates)
python3 - <<'PY' || true
import json, time
try:
    d = json.load(open('/root/tweet-agent/tiktok_session.json'))
except Exception:
    raise SystemExit(0)
lines = ["# Netscape HTTP Cookie File"]
for c in d.get('cookies', []):
    if 'tiktok' not in c.get('domain',''): continue
    host=c['domain']; flag="TRUE" if host.startswith('.') else "FALSE"
    secure="TRUE" if c.get('secure') else "FALSE"
    exp=int(c.get('expires') or time.time()+31536000);  exp = exp if exp>0 else int(time.time()+31536000)
    lines.append("\t".join([host,flag,c.get('path','/'),secure,str(exp),c['name'],c['value']]))
open('cookies.txt','w').write("\n".join(lines)+"\n")
PY

# 1. capture new videos (already-transcribed ones are skipped)
python3 transcribe.py --per-creator 10

# 2. rebuild deals.json + index.html
python3 build_site.py

# 3. publish if anything changed
if [ -n "$(git status --porcelain transcripts index.html deals.json)" ]; then
  git add -A
  git commit -m "Daily car-deals update $(date -u +%Y-%m-%d)" -q
  git push -q origin main
  echo "pushed update"
else
  echo "no changes today"
fi
