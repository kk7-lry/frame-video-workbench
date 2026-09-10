# Free Public Deployment

This repository includes a Docker web service and a Render Free blueprint.
Only public video sources are attempted. Platform availability and watermark
absence must be checked against real videos; neither is guaranteed.

Current deployment: https://frame-video-workbench.onrender.com
Source: https://github.com/kk7-lry/frame-video-workbench
Render service: `srv-dah7iuf40ujc73e0i600` (Free, Singapore).

## Zero-Cost Configuration

- Use Render's Hobby workspace and the **Free** web-service compute plan.
- Do not attach a payment method. The official Free documentation states that
  exhausted bandwidth suspends free services when there is no payment method.
- Do not add a paid disk, database, worker, domain, autoscaling or paid compute.
- Use the included `onrender.com` hostname and managed HTTPS certificate.
- The existing workspace's other services share its 750 monthly free hours
  and included bandwidth. Exhausting them can suspend these services.

Verified source, 2026-09-10: https://render.com/docs/free
Render warns that Free instances are for hobby/preview workloads, can sleep
after 15 idle minutes, and may be suspended for excessive outgoing traffic.
If the platform requires a card or paid plan, stop rather than proceeding.

## Deploy

1. Push only the files included in the release package to GitHub.
2. Create a Render Web Service using this repository and Docker runtime.
3. Set compute to Free, region to Singapore, and health path to `/healthz`.
4. The Docker image defaults to public mode, port 10000 and `/tmp/frame-public`.
   Render automatically supplies `RENDER_EXTERNAL_URL` for origin validation.
5. Deploy and verify `/healthz`, `/api/health`, independent visitor sessions,
   a real public link, playback, download, upload and mobile views.

`render.yaml` describes the same service without paid resources. Container
source copying uses a whitelist; databases, cookies, media, logs and local
Python environments are excluded. The server runs as a non-root user.

## Public Data And Limits

Each browser receives an HttpOnly, SameSite=Lax cookie (Secure on HTTPS).
Tasks, media access, edits, retries and deletion require the same session.
Session tokens are hashed before being stored with task records.
Public mode never reads local platform cookies or exposes shutdown/import
endpoints. It uses its own empty data directory, separate from the desktop app.

Public files are limited to 100 MB, with at most 20 tasks per session and
200 tasks total. Storage is bounded to approximately 2 GB including queued
downloads; requests and HTTP concurrency are bounded. A single worker executes
background tasks. Public records and media expire after 24 hours. Render
sleep/restart/redeploy can delete them sooner. Clearing the browser cookie
loses access; visitors must download files and text they need to keep.

Public network requests validate and pin public DNS addresses at connection
time and disable proxy environment discovery. Only named platform extractors
are used for optional yt-dlp fallback; Generic fallback is disabled publicly.

Linux containers include FFmpeg, ffprobe and Tesseract Chinese/English OCR.
No paid APIs or speech-model downloads run automatically. Speech recognition
is unavailable in the current Linux container and is shown as unavailable.

`FRAME_BROWSER=1` enables a disposable anonymous Chromium fallback for Douyin
video pages. It only accepts the requested video ID from the official detail
response. It blocks media, images, fonts, stylesheets, WebSockets and hosts
outside the platform resource allowlist, and checks resource DNS addresses.
Unlike the media downloader, Chromium performs its own final DNS connection;
the allowlist therefore only includes platform-controlled domains. Each
attempt has a 45-second page deadline and a 65-second subprocess limit.
On Linux the timeout kills the process group, including Chromium children.
The single existing worker bounds concurrency to one browser attempt. No
visitor browser cookies, shared login or paid parsing service are used.
Set `FRAME_BROWSER=0` to disable the fallback on a constrained host.

## Local Public-Mode Preview

```powershell
$env:FRAME_PUBLIC='1'
$env:FRAME_BIND='127.0.0.1'
$env:FRAME_PUBLIC_ORIGIN='http://127.0.0.1:4180'
$env:CLIP_PORT='4180'
python server.py
```

For another HTTPS host set `FRAME_PUBLIC_ORIGIN` to that exact origin. The
application refuses a non-loopback origin using plain HTTP. Never expose the
desktop mode or its original `data` directory through a public proxy.
