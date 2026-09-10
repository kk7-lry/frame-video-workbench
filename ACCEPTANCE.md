# Frame 0.3.0 Acceptance

## Current Run: 2026-09-10

- Follow-up: the user supplied `https://v.douyin.com/aIT_UPmda4E/`, video ID
  `7681497975500538874`. A fresh anonymous headless Edge context received the
  official detail response with the expected title and duration 77.5 seconds.
  Added an opt-in isolated Chromium fallback. Cloud download acceptance for
  this fallback is pending; earlier tests below describe the previous build.

- 80 backend tests and nine frontend state tests passed, no skips/failures.
- Static frontend check passed with 100 unique element/icon IDs.
- Real browser on local public mode: uploaded 92,979-byte MP4, decoded and
  played 640x480 video (2.020113 seconds), saved `sample.mp4` without a browser
  download error, and preserved edited OCR text after reload.
- 1440px desktop and 390px mobile had no horizontal document overflow.
  Mobile screenshot: `artifacts/mobile-390.png` (not included in release).
- Retried all three historical Douyin links. Each reached a failed result
  because the platform did not return complete video data. No real Douyin
  download success or watermark absence is claimed.
- New public-mode contracts cover independent sessions, private media access,
  cross-site requests, disabled administration, task limits, retention, DNS
  pinning, and portable media-tool output validation.
- Deployed Docker on Render Free in Singapore. Fixed HTTPS URL:
  https://frame-video-workbench.onrender.com . Service ID:
  `srv-dah7iuf40ujc73e0i600`. The unrelated pre-existing service was unchanged.
- Render Billing showed Hobby, no card on file, $0.00 accrued/projected, and
  5 GB monthly included bandwidth. No paid resource was created.
- Public source repository was explicitly authorized by the user:
  https://github.com/kk7-lry/frame-video-workbench . Only 36 release files
  were uploaded; no task databases, platform credentials or media history.
- Live public paste of https://www.w3schools.com/html/mov_bbb.mp4 downloaded
  788,493 bytes. Linux FFmpeg decoded both sampled frames, reporting 320x176
  and 10.026667 seconds. Browser playback advanced, canvas pixels were
  nonblank, and saving `mov_bbb.mp4` completed without a download error.
- A second fresh browser context saw zero tasks; direct access to the first
  context's task and media returned HTTP 404.
- Actual Linux Tesseract OCR ran on the Chinese image. The first sparse-text
  mode fragmented the first line, so the final candidate uses block mode.
  Recognition accuracy still requires human review; speech is not configured.
- All three historical Douyin links were tested from the public server.
  Video IDs were preserved, but the platform did not return a media URL.
- Real subprocess startup exposed a Python `http` module name collision.
  Fixed and covered by a process-level test that does not need live internet.
- Windows thumbnail generation returned E_INVALIDARG on a real MP4 that
  played in the browser. That specific thumbnail-only failure now preserves
  the source with validation `unavailable`; corrupt tracks remain rejected.
- Final block-mode OCR produced both lines correctly on the public server:
  `把视频变成可用素材` and `这是一次本地文字识别测试`.
- Public edited text survived reload and exported as a TXT file. Repeated
  paste reused the downloaded video. Latest 390px screenshot had no horizontal
  overflow; browser logs contained lazy-image informational notices only.
- Final billing check still showed Hobby, no card and $0.00 accrued/projected.
  Health endpoint confirmed deployed build `e2238c7`; subsequent changes
  correct public save/download/connection labels and select only available
  automatic extraction services, covered by a frontend regression test.

## Previous Run

Status: candidate patch. Real-platform download acceptance is blocked; this is
not a completed delivery and no 95% confidence claim is made.

## Observed Environment

- Existing service: `http://127.0.0.1:4175`, reporting version 0.2.0 at the start
  of this repair. Its task history contains three distinct failed Douyin links.
- The restricted development process could not connect to a public Douyin
  short link on port 443.
- An explicit request for read-only external verification was rejected because
  the automatic approval service returned HTTP 503.
- Playwright navigation to the local application was rejected by the same
  approval service. No browser acceptance check or screenshot was completed.
- After all 48 backend tests passed, a request to run the normal project
  launcher with `-Restart -NoBrowser` was also rejected with HTTP 503. The
  launcher did not execute; the existing service was not replaced.
- All eight existing task records were backed up with the SQLite backup API to
  `data/backups/before-0.2.2.sqlite3`. This backup is excluded from the package.
- On continuation across 2026-09-08/09, external access, browser navigation and
  the normal launcher were again rejected with approval-service HTTP 503.
  Read-only configuration inspection identified the matching endpoint as the
  configured CodeRelay Hub provider at `https://xiaodaitongxue.com/responses`.
  No provider credentials, permissions, or approval settings were changed.
- The user chose to complete local repairs while this provider remains
  unavailable. This iteration did not retry external verification, browser
  navigation or the real launcher. Preflight tests mock external networking.
- Final local HTTP inspection on 2026-09-09 still reports version 0.2.0,
  eight tasks and network state `connected`. A successful health probe does
  not establish that any particular video can be downloaded.

## Changes To Verify

- A failed canonical video page still falls back to its public mobile share
  page, preserving the requested video ID and any available caption.
- Media download tries up to four distinct candidate URLs in quality order.
  HTTP failures, invalid media responses and truncated downloads trigger the
  next candidate; system network denial stops attempts immediately.
- Pasting an existing failed link retries its task. Edited captions survive.
- Redownloaded files receive a fresh 24-hour retention period.
- A redirect to another video must not download that video's generic media.
- MP4/MOV/M4V files are checked with the Windows media decoder when available.
  It reads the first and last video frames and reports file dimensions/duration.
  A rejected video triggers the next candidate. An unavailable decoder is
  explicitly reported as unavailable and does not imply a successful check.
- Downloads use a sanitized UTF-8 title with the media file extension.
- Paste and button retries share scheduling, reset stale download fields and
  preserve all text. Active recognition/downloads and available files are
  reused. Executor failure releases the task for a later retry.
- An old frontend poll cannot discard automatic extraction after a retry.
  Automatic work waits for active recognition and respects edited empty text
  and unsaved drafts. Reused tasks honor the automatic extraction option.
- Restart recovery preserves interrupted recognition text, edit flags and
  frame timestamps.
- Before stopping an existing service, the launcher runs candidate checks for
  Python, writable data/media/processing directories, database integrity,
  loopback binding and network reachability. A local check failure or an
  unreachable candidate network leaves the existing service running.
  This is a precheck, not an atomic replacement or rollback guarantee.

## Local Evidence

Latest results on 2026-09-09: 65 backend tests and eight frontend state tests
passed, with no failures or skips. JavaScript syntax, all 94 HTML control/icon
IDs, and all six PowerShell scripts passed syntax checks.

Run from this directory:

```powershell
python -m unittest discover -s tests -v
node --check app.js
node tests/check_frontend.cjs
node --test tests/test_frontend.cjs
```

The tests include actual local HTTP connections, real Windows OCR and isolated
temporary databases. Successful download fixtures now use a real MP4 generated
locally from `chinese.png`: 640x480, approximately 2.02 seconds, with an AAC track.
Windows actually decodes its first and last frames. Tests also reject corrupt
MP4 payloads even when the signature and Content-Length appear valid, and recover
through a backup media URL. `tests/build_media_fixture.ps1` regenerates this asset.

Frontend state tests run the real application functions in Node with mocked
DOM and API responses. They cover an older poll arriving during both retry
paths, repeated paste, existing recognition, and manual text preservation.
They do not test browser layout, clipboard permissions or file saving dialogs.
Startup tests use temporary directories, a read-only integrity check on the
test database and mocked external connectivity; queued records remain intact.

Platform HTML is still represented by offline fixtures. These local checks do
not prove platform support, full playback, audible audio, or watermark absence.
WEBM retains signature/transfer checks only; unsupported/unavailable Windows
decoding is not presented as a successful playback check.

## Required Before Delivery

1. Load 0.2.3 using the normal launcher in a process allowed to access the
   network, and confirm the version reported by `/api/health`.
2. Retry the three existing failed public links without importing credentials.
   Record final status, target content ID, actual file size and media format.
3. Open the saved videos, check complete playback and audio, and inspect frames
   for the actual presence or absence of watermarks.
4. Use Playwright on desktop and mobile widths to test paste, submit, preview,
   download, repeated paste, retry, upload, edited text persistence and deletion.
5. Inspect screenshots for clipping or overlap and browser errors for failures.

Failures at these gates must be repaired and rerun before calling the website
ready. A passing unit test count alone cannot satisfy these gates.
