# YouTube Downloader Studio

Desktop app (Python + PySide6 + yt-dlp + FFmpeg) for importing a YouTube channel's public videos,
selecting the ones you want, and downloading them in bulk. Target: **macOS 11.7 (Big Sur), Intel x86_64**.

> Only download content you own or have the right to download. The app does not bypass DRM or access controls.

## Status: v0.3 — universal downloader, player, dashboard + DMG packaging pipeline

| Phase | Scope | State |
|---|---|---|
| 1 | SQLite storage, settings, channel import (yt-dlp), paginated vertical library with persistent selection, thumbnail cache, preview window | **Done – unit-tested headless on Linux** |
| 2 | Parallel download manager (≤10), retries, pause/resume/cancel, FFmpeg merge, ZIP export, Downloads/Completed/Failed/Settings pages | **Done – tested with real yt-dlp + FFmpeg against a local server; not yet tried on real YouTube** |
| 3 | Add Link (single video / playlist / profile, many platforms), video+audio formats and qualities, built-in player, dashboard, light/dark + accent, toasts, drag & drop, clipboard detection, filters/sort, speed options | **Done – 130+ tests; real yt-dlp+FFmpeg verified against a local server; Facebook/Instagram/TikTok not tried live** |
| 4 | PyInstaller spec, DMG scripts, GitHub Actions | **Written; first CI run not yet done** |

### What is and is not verified
- Verified: core logic and Qt model/delegate/worker code (84 tests, offscreen Qt, PySide6 6.6.3.1 on Linux / Python 3.12).
- Verified: PySide6 6.5.3 x86_64 Qt binaries declare minimum macOS **11.0** (read from the Mach-O headers of QtCore and libqcocoa).
- **Not verified:** real YouTube import (no network access to YouTube in the build sandbox), anything on macOS,
  PySide6 6.5.3 itself (needs Python <= 3.11, the sandbox had 3.12), DMG build.
- Channel listings from yt-dlp's flat extraction often lack upload dates, so the date column may show "Date unknown"
  until a later phase enriches them.

## Run (Mac Intel, Python 3.11)
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Test
```bash
pytest -q
```

## Data location
`~/Library/Application Support/YouTube Downloader Studio/` – `Database/library.db` (with rolling backups),
`settings.json`, `Cache/Thumbnails/`, `Logs/app.log`, `Downloads/`. Set `YTDS_DATA_DIR` to override.

## Phase 1 behaviour
- Accepts `@handle`, `/channel/UC…`, `/c/…`, `/user/…` links (any tab is normalised to `/videos`); other hosts are rejected.
- No artificial limit on imported videos; private/deleted/upcoming/members-only items are stored with their own availability and cannot be selected.
- 50 videos per page; selection is stored in SQLite, survives paging and restarts.
- Shortcuts: Ctrl/⌘+I focus import, Ctrl/⌘+F search, Ctrl/⌘+D deselect all.

## Build the DMG on GitHub (no Mac needed)
1. Push this folder to a GitHub repository (`git init`, `git add .`, `git commit`, `git remote add origin …`, `git push -u origin main`).
2. Repo → **Actions → build-macos → Run workflow**. When it finishes, download the artifact
   **YouTube-Downloader-Studio-dmg** (a zip containing `YouTube-Downloader-Studio.dmg`).
3. Or create a **Release** (Releases → Draft a new release → Publish): the DMG is attached to it automatically.

The workflow runs on `macos-15-intel` (native x86_64), installs python.org Python 3.11.9, runs the tests,
downloads static Intel FFmpeg, builds the `.app` with PyInstaller, smoke-tests the **built** app (`--selftest`),
builds the DMG, mounts it and re-checks it. `scripts/check_macho.py` fails the build if any bundled Mach-O file
lacks an x86_64 slice or needs macOS newer than 11.0.

Local build on a Mac: `bash scripts/setup_mac.sh`, `bash scripts/fetch_ffmpeg.sh`, then (inside the venv)
`bash scripts/build_mac.sh && bash scripts/create_dmg.sh`.

### Installing the DMG
The app is ad-hoc signed, **not notarized** (that needs a paid Apple Developer account). On first launch:
right-click the app → Open → Open, or `xattr -dr com.apple.quarantine "/Applications/YouTube Downloader Studio.app"`.

### Notes
- FFmpeg comes from evermeet.cx static builds (GPL). Distributing the DMG means complying with that licence.
- GitHub ends Intel macOS runner support in Fall 2027.
- The CI cannot run macOS 11; the final test must be done on a real Big Sur Intel Mac.

## الخلاصة بالعربية
- ارفع المجلد إلى GitHub ← تبويب Actions ← build-macos ← Run workflow ← نزّل الملف من Artifacts.
- أو أنشئ Release وسيُرفَق ملف DMG تلقائياً.
- أول تشغيل: انقر بزر الماوس الأيمن على التطبيق ← Open (التطبيق غير موثّق من Apple).

## Downloading (Phase 2)
Library → tick videos → **Download Selected** (or right-click a row → *Download This Video*).
- Quality: best available up to your limit (default 1080p, H.264 preferred so MP4 plays in QuickTime); audio+video merged by FFmpeg.
- Up to 10 parallel downloads (Settings), automatic retries with back-off (not for private/removed videos), one failure never stops the others.
- Pause All keeps partial files and resumes where it stopped; Cancel All keeps partial files too.
- Files go to `<download folder>/<channel name>/<title> [<id>].mp4`; a video already on disk is not downloaded twice.
- **Completed** page: Open File, Show in Finder, Copy Path, Remove from List (never deletes the file), Create ZIP Archive.
- Only the following hosts are accepted for downloads: youtube.com (https). Filename templates cannot contain path separators.
- Not enough free disk space (< 500 MB) blocks queueing with a clear message; a full disk while writing or zipping is reported and partial archives are removed.

## What's new in v0.3
- **Add Link** (⌘L): paste links or drop them on the window. Single videos show the qualities the video really offers and an estimated size; playlists/profiles list every item with checkboxes, filter and Select All.
- **Video**: MP4, MKV, WebM — Best, 2160p (4K), 1440p, 1080p, 720p, 480p, 360p. **Audio only**: MP3, M4A (AAC), Opus, FLAC, WAV, OGG with selectable bitrate.
- **Platforms**: YouTube, Facebook, Instagram, TikTok, X/Twitter, Vimeo, Dailymotion, Twitch, Reddit, SoundCloud, Bilibili, Pinterest, Snapchat, LinkedIn, Bandcamp — and any other link yt-dlp understands (switchable in Settings). Support depends on yt-dlp; sites change often.
- **Speed**: parallel downloads (1–10), parallel fragments (HLS/DASH), optional per-download speed limit, Move to Top of Queue.
- **Organization**: folders by channel, platform › channel, playlist, or flat; numbered playlist files (001 - …); custom “Save to” folder per batch; Completed list with search, platform/type filters and sorting.
- **Player** (built in, Qt Multimedia): queue, seek, volume, speed 0.5–2×, fullscreen (F), shortcuts: Space, ←/→, ↑/↓, M, N/P. If a file’s codec is unsupported on your macOS the app says so and offers the default player.
- **Dashboard**, toasts, clipboard link detection, dark/light theme with 5 accent colors.
- Optional `cookies.txt` for content your own account can access (stays on your Mac). DRM-protected content cannot be downloaded.

Upgrading from 0.2: the database is migrated automatically; your history is kept.
