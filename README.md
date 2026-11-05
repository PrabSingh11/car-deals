# car-deals — TikTok transcriber

Pulls recent videos from a list of TikTok creators and writes a plain-text
transcript of each one.

## Use
1. Put the creator handles in `creators.txt` (one per line, e.g. `@somecardealer`).
2. Run:
   ```
   python3 transcribe.py                 # all creators in creators.txt, 10 newest each
   python3 transcribe.py @handle --per-creator 5
   python3 transcribe.py https://www.tiktok.com/@x/video/123   # one specific video
   ```

## Output
- `transcripts/<handle>/<video_id>.txt` — one file per video (with the link + caption).
- `transcripts/ALL.md` — everything combined, newest first.

## Notes
- Transcribes audio locally with faster-whisper (free, no API keys). Set
  `WHISPER_MODEL=small` for better accuracy, `tiny` for speed.
- TikTok blocks the normal video API from this server, so it fetches media via
  TikTok's embed endpoint and throttles (`THROTTLE` seconds, default 8) to avoid
  rate-limit 429s. `cookies.txt` holds a logged-in TikTok cookie for reliability.
- Already-transcribed videos are skipped, so re-running only picks up new ones.
