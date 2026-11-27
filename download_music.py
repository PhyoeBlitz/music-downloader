#!/usr/bin/env python3
"""
Download songs from YouTube/YouTube Music into per-category folders
(or a single folder if no category column is present), based on a CSV
file with columns: no, name, category, link

CSV format expected (header row required):
    name,category,link
    Song Title,Chill,https://music.youtube.com/watch?v=xxxx
    Another Song,Chill,https://music.youtube.com/watch?v=yyyy
    Workout Track,Workout,https://music.youtube.com/watch?v=zzzz

- The saved filename is prefixed with a running number, e.g.
  01_Song Title.mp3 — zero-padded to 2 digits for correct sorting.
  This number is generated automatically per category,
  based on the order rows appear in the CSV.
  Each category restarts its own count at 1, so the first
  "Workout" row is 01_... even if it's the 3rd row overall in the
  file. Sort your CSV rows into the order you want (e.g. by BPM)
  before running the script — that row order becomes the numbering.
- "name" is the song title used to build the filename. If a row has
  no name, the video's own title is used instead (with no number
  prefix).
- "link" can also contain multiple URLs in one cell, separated by
  a comma, semicolon, or newline — the script will split them. Only
  the first URL in a multi-URL cell gets the numbered/name-based
  filename; the rest fall back to the video's own title.
- Column names are matched case-insensitively, with some flexibility:
  "name" also matches "song name", "title", or "曲名"; "link" also
  matches "links", "url", or "リンク"; "category" also matches
  "カテゴリ".
- "category" is optional. If the CSV has no category column at all,
  every song is saved directly into <output_dir>/ with no subfolder.
  If the column exists but a particular row's value is blank, that
  row still falls back to an "Uncategorized" subfolder.
- Each song is saved as MP3 (192kbps) into: <output_dir>/<category>/
  (or directly into <output_dir>/ if there's no category column).
- Every downloaded song also gets a global, zero-padded search code (e.g.
  0001, 0002, ...) that is unique across the whole CSV, regardless of
  category — unlike the per-category sort number above, this one never
  resets. It's appended at the END of the filename, e.g.
  01_Song Title_0007.mp3, so it never disturbs the category sort-number
  at the front. It's assigned per downloaded file, so even the 2nd+ URL
  in a multi-URL cell (which has no CSV name) still gets a code appended
  to its filename. The same code is also written into the MP3's
  "Comment" metadata tag, so a song can be found by its code either by
  filename or from inside a media player/library.
- Already-downloaded files (by URL, recorded in out_dir/.downloaded.txt)
  are skipped on re-run. A song is only ever recorded after it fully
  succeeds (download + mp3 conversion + metadata), so a failed or
  partial attempt is always retried on the next run.
- Downloads run in parallel using a thread pool (--workers, default 2).
  Rows are interleaved round-robin across categories before download
  starts (A,B,C,D,A,B,C,D,...) so categories start together rather than
  one finishing before the next begins, but this is just ordering —
  songs within the same category (or sharing a single output folder,
  if there's no category column) also download fully in parallel with
  each other. Only the brief already-downloaded check/record around
  each song is serialized per output folder, not the download itself.
  Each download also waits a short random delay before starting, to
  avoid bursts of simultaneous requests that can trigger YouTube
  rate-limiting or a bot-detection challenge —
  keep --workers conservative rather than maximizing it.

Requirements:
    pip install yt-dlp
    ffmpeg must be installed and on PATH (for audio extraction)
      - macOS: brew install ffmpeg
      - Windows: choco install ffmpeg  (or download from ffmpeg.org)
      - Linux: sudo apt install ffmpeg

Usage:
    python download_music.py --csv songs.csv --out downloads --workers 4
"""

import argparse
import concurrent.futures
import contextlib
import csv
import glob
import os
import random
import re
import shutil
import subprocess
import tempfile
import urllib.request
import sys
import threading
import time
from itertools import zip_longest
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    sys.exit("Missing dependency. Install it with:  pip install yt-dlp")


def app_dir() -> Path:
    """Folder of the .exe when packaged with PyInstaller, else of this script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def tool_path(name: str):
    """Find an external tool (ffmpeg, ffprobe, node, ...): next to the app first, then on PATH."""
    local = app_dir() / f"{name}.exe"
    if local.exists():
        return str(local)
    return shutil.which(name)


def resolve_js_runtime(choice: str):
    """Return yt-dlp's js_runtimes option for the chosen runtime ('auto' picks the first one found)."""
    if choice == "none":
        return None
    for name in (["deno", "node", "bun"] if choice == "auto" else [choice]):
        path = tool_path(name)
        if path:
            return {name: {"path": path}}
    if choice != "auto":
        print(f"Warning: JavaScript runtime '{choice}' not found; continuing without one.")
    else:
        print("Note: no JavaScript runtime (Node.js / Deno) found. Downloads usually still work, "
              "but installing Node.js (nodejs.org) makes YouTube extraction more reliable.\n")
    return None


# Set when the user presses Ctrl+C. Worker threads check it so queued songs
# don't start and in-progress downloads abort at their next progress update.
STOP = threading.Event()


class Cancelled(Exception):
    pass


def _abort_if_stopped(_status):
    if STOP.is_set():
        raise Cancelled("stopped by user")


def sanitize_folder_name(name: str) -> str:
    """Make a string safe to use as a folder name across OSes."""
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name or "Uncategorized"


def split_links(cell: str):
    """Split a CSV cell that may contain multiple URLs."""
    if not cell:
        return []
    parts = re.split(r"[,\;\n]+", cell)
    return [p.strip() for p in parts if p.strip()]


def sanitize_filename(name: str) -> str:
    """Make a string safe to use as a filename across OSes."""
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name


def read_csv_rows(csv_path: Path, numbering: bool = True):
    """Yield (category, url, song_name_or_None, global_code) tuples, tolerant of column naming."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            sys.exit("CSV appears to be empty or has no header row.")

        # Match columns case-insensitively. Also accepts Japanese header names
        # (e.g. exported from a BGM selection tracker) as aliases.
        field_map = {fn.strip().lower(): fn for fn in reader.fieldnames}
        cat_field = field_map.get("category") or field_map.get("カテゴリ")
        link_field = (
            field_map.get("links") or field_map.get("link") or field_map.get("url")
            or field_map.get("リンク")
        )
        name_field = (
            field_map.get("song name") or field_map.get("name") or field_map.get("title")
            or field_map.get("曲名")
        )

        if not link_field:
            sys.exit(
                f"Could not find a 'Link(s)' column. "
                f"Found columns: {reader.fieldnames}"
            )

        category_counters = {}
        global_counter = 0
        for row in reader:
            # Category is optional. If the column doesn't exist in the CSV at
            # all, every row gets category "" and main() puts everything
            # directly in --out with no per-category subfolder. If the column
            # exists but this row's value is blank, it still falls back to an
            # "Uncategorized" subfolder, same as before.
            category = sanitize_folder_name(row.get(cat_field, "")) if cat_field else ""
            counter =  category_counters.get(category, 0) + 1
            category_counters[category] = counter
            number = str(counter).zfill(2)  # zero-pad to 2 digits, for sort order within the category
            song_name_without_number = row.get(name_field, "").strip() if name_field else ""
            if numbering and song_name_without_number:
                song_name = f"{number}_{song_name_without_number}"
            else:
                # No CSV name: stays empty so the video's own title is used
                # (otherwise numbering alone would give a bare "01_0003.mp3").
                song_name = song_name_without_number
            urls = split_links(row.get(link_field, ""))
            for url in urls:
                # global_counter is unique per downloaded file (not per row), so
                # every song gets its own searchable code regardless of whether
                # it has a CSV name.
                global_counter += 1
                global_code = str(global_counter).zfill(4)  # e.g. 0001, 0002, ...

                # If there's one URL for this row, the CSV name applies directly.
                # If a cell had multiple URLs, only the first gets the exact name
                # (to avoid duplicate filenames); the rest fall back to the video title.
                if song_name and urls.index(url) == 0:
                    parts = (song_name, global_code) if numbering else (song_name,)
                    final_name = sanitize_filename("_".join(parts))
                else:
                    final_name = None
                yield category, url, final_name, global_code


def download_song(url: str, out_dir: Path, audio_format: str = "mp3", quality: str = "192",
                   song_name: str = None, js_runtimes: dict = None, lock: threading.Lock = None,
                   global_code: str = None, numbering: bool = True, cookie_opts: dict = None,
                   artwork: bool = True):
    out_dir.mkdir(parents=True, exist_ok=True)

    # Use the CSV-provided name when given, otherwise fall back to the video's own
    # title. Either way, the global search code is appended so every downloaded
    # file (custom-named or not) can be found by its code.
    if song_name:
        outtmpl = str(out_dir / f"{song_name}.%(ext)s")
    elif numbering:
        outtmpl = str(out_dir / f"%(title)s [%(id)s]_{global_code}.%(ext)s")
    else:
        outtmpl = str(out_dir / "%(title)s [%(id)s].%(ext)s")

    ydl_opts = {
        # Falls through to a combined video+audio format if the audio-only
        # stream is unavailable or blocked (e.g. HTTP 403 on some YouTube
        # Music "Video"-type entries). FFmpegExtractAudio below always
        # converts whatever gets downloaded to mp3 and discards the video,
        # so this doesn't change the output — just adds a fallback so the
        # download doesn't fail outright when the audio-only path is blocked.
        "format": "bestaudio/best[acodec!=none]",
        "outtmpl": outtmpl,
        "ffmpeg_location": tool_path("ffmpeg"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                "preferredquality": quality,
            },
            {"key": "FFmpegMetadata"},  # embeds title/artist tags where available
        ],
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        # False so a failed URL raises an exception instead of being silently
        # swallowed. main() already keeps the batch going past any one song's
        # failure; this just makes sure that failure is actually visible, and
        # that a failed song never gets marked as downloaded below.
        "ignoreerrors": False,
        "sleep_interval_requests": 1,  # avoid hammering YouTube
        "sleep_interval": 1,
        "max_sleep_interval": 5,
    }

    if js_runtimes:
        # Only set when a runtime was found: yt-dlp rejects js_runtimes=None.
        ydl_opts["js_runtimes"] = js_runtimes

    if cookie_opts:
        # Cookies from a signed-in YouTube session, needed for age-restricted
        # songs and to get past "Sign in to confirm you're not a bot".
        ydl_opts.update(cookie_opts)

    if global_code:
        # Embed the same search code into the MP3's "Comment" tag, so the song
        # is findable by code from inside a media player/library too, not just
        # by filename.
        ydl_opts["postprocessor_args"] = {"ffmpeg": ["-metadata", f"comment={global_code}"]}

    # Stagger request start times across worker threads so YouTube doesn't see
    # a burst of simultaneous connections (which can trigger rate-limiting or
    # a bot-detection challenge).
    time.sleep(random.uniform(1.5, 4.0))
    if STOP.is_set():
        raise Cancelled("stopped by user")
    # Lets Ctrl+C abort a download that is already running.
    ydl_opts["progress_hooks"] = [_abort_if_stopped]

    # Track already-downloaded songs ourselves (by URL) instead of using
    # yt-dlp's own "download_archive" option. yt-dlp's version wraps the
    # whole download+conversion in whatever lock protects the file, which
    # would serialize every song sharing an output folder. Here, the lock
    # only guards the brief moments we actually touch this tiny text file --
    # the slow part (download + ffmpeg conversion) runs fully unlocked, so
    # multiple songs genuinely download in parallel even when they share one
    # output folder. We only record success AFTER ydl.download() returns
    # without raising (i.e. download AND postprocessing both succeeded), so a
    # failed or partial song is never wrongly marked done.
    archive_path = out_dir / ".downloaded.txt"
    with (lock or contextlib.nullcontext()):
        already_downloaded = archive_path.exists() and url in archive_path.read_text(encoding="utf-8").splitlines()
    if already_downloaded:
        return

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if artwork and info:
        downloads = info.get("requested_downloads") or []
        final_path = downloads[0].get("filepath") if downloads else None
        if final_path and Path(final_path).exists():
            try:
                embed_artwork(Path(final_path), info)
            except Exception as e:
                # Missing artwork shouldn't fail an otherwise good download.
                print(f"  (artwork not added for {Path(final_path).name}: {e})")

    with (lock or contextlib.nullcontext()):
        with archive_path.open("a", encoding="utf-8") as f:
            f.write(url + "\n")


def _best_thumbnail_url(info: dict):
    """Pick the largest thumbnail, preferring JPEG (ffmpeg reads both)."""
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    if not thumbs:
        return info.get("thumbnail")
    thumbs.sort(key=lambda t: ((t.get("width") or 0) * (t.get("height") or 0),
                               t["url"].split("?")[0].endswith(".jpg")))
    return thumbs[-1]["url"]


# Hide the console window ffmpeg/ffprobe would otherwise flash up when
# running from the windowed (GUI) exe.
_NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def has_artwork(audio_path: Path) -> bool:
    r = subprocess.run(
        **_NO_WINDOW,
        args=
        [tool_path("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(audio_path)],
        capture_output=True, text=True,
    )
    return bool(r.stdout.strip())


def embed_artwork(audio_path: Path, info: dict):
    """Download the video's thumbnail, crop it to a centered square (YouTube Music
    puts the square album cover in the middle of a 16:9 image) and embed it as
    the file's front cover, so it shows in Apple Music / iTunes and other players."""
    url = _best_thumbnail_url(info)
    if not url:
        raise RuntimeError("no thumbnail available")
    audio_path = Path(audio_path)
    with tempfile.TemporaryDirectory() as tmp:
        thumb = Path(tmp) / "thumb"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            thumb.write_bytes(resp.read())
        out = Path(tmp) / ("out" + audio_path.suffix)
        cmd = [
            tool_path("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-i", str(audio_path), "-i", str(thumb),
            "-map", "0:a", "-map", "1:0", "-c:a", "copy",
            "-c:v", "mjpeg", "-q:v", "2", "-vf", r"crop=min(iw\,ih):min(iw\,ih)",
            "-disposition:v", "attached_pic",
            "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)",
        ]
        if audio_path.suffix.lower() == ".mp3":
            cmd += ["-id3v2_version", "3"]  # ID3v2.3: the version Apple Music reads most reliably
        r = subprocess.run(cmd + [str(out)], capture_output=True, text=True, **_NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[-300:] or "ffmpeg failed")
        shutil.move(str(out), str(audio_path))


def find_downloaded_file(folder: Path, numbered_name, plain_name, url, code, ext):
    """Locate a song's file whether it currently has the numbered or plain name."""
    for name in (numbered_name, plain_name):
        if name and (folder / f"{name}.{ext}").exists():
            return folder / f"{name}.{ext}"
    vid = video_id(url)
    if vid and folder.exists():
        for suffix in (f" [{vid}]_{code}.{ext}", f" [{vid}].{ext}"):
            matches = list(folder.glob("*" + glob.escape(suffix)))
            if matches:
                return matches[0]
    return None


def add_artwork_to_existing(csv_path: Path, out: Path, ext: str, cookie_opts: dict):
    """Embed cover art into already-downloaded files, without re-downloading audio."""
    numbered_rows = list(read_csv_rows(csv_path, numbering=True))
    plain_rows = list(read_csv_rows(csv_path, numbering=False))
    total = len(numbered_rows)
    added = skipped = missing = failed = 0
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, **(cookie_opts or {})}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for i, ((category, url, numbered_name, code), (_, _, plain_name, _)) in enumerate(
                zip(numbered_rows, plain_rows), start=1):
            if STOP.is_set():
                print("\nStopped.")
                break
            folder = (out / category) if category else out
            path = find_downloaded_file(folder, numbered_name, plain_name, url, code, ext)
            if not path:
                missing += 1
                print(f"[{i}/{total}] not found: {category}/{plain_name or url}")
                continue
            if has_artwork(path):
                skipped += 1
                continue
            try:
                time.sleep(random.uniform(1.0, 2.5))  # stay under YouTube's rate limits
                info = ydl.extract_info(url, download=False, process=False)
                embed_artwork(path, info)
                added += 1
                print(f"[{i}/{total}] artwork added: {category}/{path.name}")
            except Exception as e:
                failed += 1
                print(f"[{i}/{total}] FAILED: {category}/{path.name}: {e}")
    print(f"\nArtwork added: {added}. Already had artwork: {skipped}. "
          f"Not found: {missing}. Failed: {failed}.")


def video_id(url: str):
    """Extract the 11-character YouTube video id from a watch / youtu.be URL."""
    m = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/)([\w-]{11})", url)
    return m.group(1) if m else None


def rename_files(csv_path: Path, out: Path, to_numbered: bool, ext: str, dry_run: bool = False):
    """Switch already-downloaded files between numbered and plain names, without
    re-downloading. Uses the CSV (same file, same row order as the download) to
    work out each file's numbered name (01_Song_0001.mp3) and plain name
    (Song.mp3), then renames whichever one exists to the other."""
    numbered_rows = list(read_csv_rows(csv_path, numbering=True))
    plain_rows = list(read_csv_rows(csv_path, numbering=False))

    renamed = already = missing = conflicts = 0
    for (category, url, numbered_name, code), (_, _, plain_name, _) in zip(numbered_rows, plain_rows):
        folder = (out / category) if category else out
        if numbered_name:
            src_name, dst_name = (plain_name, numbered_name) if to_numbered else (numbered_name, plain_name)
            src = folder / f"{src_name}.{ext}"
            dst = folder / f"{dst_name}.{ext}"
        else:
            # Row had no CSV name, so the file is named after the video title:
            # "<title> [<id>]_0001.mp3" (numbered) or "<title> [<id>].mp3" (plain).
            vid = video_id(url)
            if not vid:
                missing += 1
                continue
            numbered_suffix, plain_suffix = f" [{vid}]_{code}.{ext}", f" [{vid}].{ext}"
            src_suffix, dst_suffix = (plain_suffix, numbered_suffix) if to_numbered else (numbered_suffix, plain_suffix)
            matches = list(folder.glob("*" + glob.escape(src_suffix))) if folder.exists() else []
            if not matches:
                dst_matches = list(folder.glob("*" + glob.escape(dst_suffix))) if folder.exists() else []
                if dst_matches:
                    already += 1
                else:
                    missing += 1
                    print(f"  not found: {folder / ('*' + src_suffix)}")
                continue
            src = matches[0]
            dst = folder / (src.name[: -len(src_suffix)] + dst_suffix)

        if dst.exists():
            if src.exists():
                conflicts += 1
                print(f"  SKIP (target already exists): {src.name} -> {dst.name}")
            else:
                already += 1
            continue
        if not src.exists():
            missing += 1
            print(f"  not found: {src}")
            continue
        print(f"  {'[dry run] ' if dry_run else ''}{folder.name}/{src.name} -> {dst.name}")
        if not dry_run:
            src.rename(dst)
        renamed += 1

    action = "Would rename" if dry_run else "Renamed"
    print(f"\n{action} {renamed} file(s). Already named correctly: {already}. "
          f"Not found: {missing}. Skipped (name clash): {conflicts}.")


def main(argv=None):
    """Command-line entry point. argv lets the GUI pass options directly."""
    parser = argparse.ArgumentParser(
        description="Download songs from YouTube / YouTube Music as MP3, into one folder per category.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
CSV format (header row required):
  name,category,link
  Duality,Slipknot,https://music.youtube.com/watch?v=B2lmOei7qfk

  name      song title used for the filename (blank = use the video title)
  category  folder name, e.g. the artist (optional column)
  link      YouTube / YouTube Music URL (several allowed, separated by , ; or newline)

Output: <out>/<category>/01_Song Title_0001.mp3
        (or <out>/<category>/Song Title.mp3 with --no-numbering)

Examples:
  %(prog)s --csv metal_playlist.csv
  %(prog)s --csv metal_playlist.csv --out D:\\Music --no-numbering
  %(prog)s --csv metal_playlist.csv --cookies-from-browser firefox
  %(prog)s --csv metal_playlist.csv --cookies cookies.txt --workers 1

Rename already-downloaded files (no re-download):
  %(prog)s --csv metal_playlist.csv --rename plain --dry-run
  %(prog)s --csv metal_playlist.csv --rename plain
  %(prog)s --csv metal_playlist.csv --rename numbered

Add album covers to already-downloaded files (no re-download):
  %(prog)s --csv burmese_pop.csv --out burmese --add-artwork

Already-downloaded songs are skipped on re-run. Press Ctrl+C to stop
(press it twice to quit immediately); unfinished songs are retried next run.
""",
    )
    parser.add_argument("--csv", required=True, type=Path,
                        help="Path to the CSV file (columns: name, category, link)")
    parser.add_argument("--out", default=Path("downloads"), type=Path,
                        help="Base output directory (default: downloads)")
    parser.add_argument("--format", default="mp3", help="Audio format (default: mp3)")
    parser.add_argument("--quality", default="192", help="Audio bitrate in kbps (default: 192)")
    parser.add_argument(
        "--js-runtime",
        default="auto",
        choices=["auto", "node", "deno", "bun", "quickjs", "quickjs-ng", "none"],
        help="JavaScript runtime yt-dlp uses to solve YouTube's player challenges "
             "(default: auto = Deno, Node.js or Bun, whichever is installed). "
             "Use 'none' to disable (may reduce available formats).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Number of songs to download in parallel (default: 2). Keep this low to "
             "avoid YouTube rate-limiting or bot-detection challenges.",
    )
    parser.add_argument(
        "--no-numbering",
        action="store_true",
        help="Don't add the per-category number before the title (01_) or the search "
             "code after it (_0001). Files are saved as just 'Song Title.mp3'. "
             "The search code is still written to the MP3 Comment tag.",
    )
    cookie_group = parser.add_mutually_exclusive_group()
    cookie_group.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER[:PROFILE]",
        help="Use YouTube cookies from your browser, e.g. firefox, chrome, edge, brave, "
             "or 'chrome:Profile 1'. Needed for age-restricted songs.",
    )
    cookie_group.add_argument(
        "--cookies",
        type=Path,
        metavar="FILE",
        help="Use a Netscape-format cookies.txt file exported from your browser.",
    )
    parser.add_argument(
        "--rename",
        choices=["plain", "numbered"],
        help="Don't download. Rename files already in --out instead: 'plain' turns "
             "01_Song_0001.mp3 into Song.mp3, 'numbered' does the reverse. Use the "
             "same CSV (same row order) that the files were downloaded with.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --rename: only show what would be renamed, don't change anything.",
    )
    parser.add_argument(
        "--no-artwork",
        action="store_true",
        help="Don't embed the album cover (YouTube thumbnail, cropped square) into downloaded files.",
    )
    parser.add_argument(
        "--add-artwork",
        action="store_true",
        help="Don't download. Embed album covers into files already in --out "
             "(files that already have artwork are skipped).",
    )
    args = parser.parse_args(argv)

    if args.dry_run and not args.rename:
        parser.error("--dry-run only works together with --rename")
    if args.rename and args.add_artwork:
        parser.error("use --rename and --add-artwork in separate runs")

    cookie_opts = {}
    if args.cookies_from_browser:
        browser, _, profile = args.cookies_from_browser.partition(":")
        cookie_opts["cookiesfrombrowser"] = (browser.lower(), profile or None, None, None)
    elif args.cookies:
        if not args.cookies.exists():
            sys.exit(f"Cookies file not found: {args.cookies}")
        cookie_opts["cookiefile"] = str(args.cookies)

    if not args.rename:
        # Downloading and embedding artwork both need ffmpeg/ffprobe.
        missing_tools = [t for t in ("ffmpeg", "ffprobe") if not tool_path(t)]
        if missing_tools:
            sys.exit(
                f"Could not find {' and '.join(missing_tools)}.\n"
                f"Put ffmpeg.exe and ffprobe.exe in the same folder as this program ({app_dir()}),\n"
                f"or install FFmpeg (e.g. 'winget install Gyan.FFmpeg') and open a new terminal."
            )

    if args.rename or args.add_artwork:
        if not args.csv.exists():
            sys.exit(f"CSV file not found: {args.csv}")
        if args.rename:
            rename_files(args.csv, args.out, args.rename == "numbered", args.format, args.dry_run)
        else:
            add_artwork_to_existing(args.csv, args.out, args.format, cookie_opts)
        return

    if not args.csv.exists():
        sys.exit(f"CSV file not found: {args.csv}")

    js_runtimes = resolve_js_runtime(args.js_runtime)
    rows = list(read_csv_rows(args.csv, numbering=not args.no_numbering))
    if not rows:
        sys.exit("No (Category, Link) pairs found in the CSV.")

    # Interleave rows round-robin across categories (A,B,C,D,A,B,C,D,...) instead
    # of leaving them grouped as they appear in the CSV. Tasks are submitted to
    # the thread pool in this order, so interleaving means the first batch of
    # submitted tasks already spans every category — with --workers set to the
    # number of categories, all of them start downloading in parallel right away
    # instead of working through one category before reaching the next.
    rows_by_category = {}
    for row in rows:
        rows_by_category.setdefault(row[0], []).append(row)
    rows = [row for group in zip_longest(*rows_by_category.values()) for row in group if row is not None]

    print(f"Found {len(rows)} link(s) across {len(set(c for c, _, _, _ in rows))} categor(y/ies), "
          f"using {args.workers} parallel worker(s).\n")

    # One lock per category: songs in the same category share an archive file
    # (out_dir/.downloaded.txt). The lock only guards the brief read/append to
    # that file (see download_song()), not the download itself, so songs in
    # the same category still download fully in parallel with each other --
    # this just stops their archive-file writes from colliding.
    category_locks = {category: threading.Lock() for category in {c for c, _, _, _ in rows}}

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    try:
        future_to_row = {
            executor.submit(
                download_song,
                url,
                (args.out / category) if category else args.out,
                args.format,
                args.quality,
                song_name,
                js_runtimes,
                category_locks[category],
                global_code,
                not args.no_numbering,
                cookie_opts,
                not args.no_artwork,
            ):(category, url, song_name, global_code)
            for category, url, song_name, global_code in rows
        }

        # Wait with a short timeout in a loop instead of blocking indefinitely:
        # on Windows a blocking wait swallows Ctrl+C until every song finishes.
        pending = set(future_to_row)
        i = 0
        while pending:
            if STOP.is_set():
                # Stop requested from outside (the GUI's Stop button): cancel
                # queued songs; running ones abort via the progress hook.
                executor.shutdown(wait=False, cancel_futures=True)
                print("\nStopped. Finished songs are kept; unfinished ones will be retried next run.")
                return
            done, pending = concurrent.futures.wait(
                pending, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                i += 1
                category, url, song_name, global_code = future_to_row[future]
                label = song_name or "(video title)"
                try:
                    future.result()
                    print(f"[{i}/{len(rows)}] [{category}] [{global_code}] {label} <- {url}")
                except Exception as e:
                    print(f"[{i}/{len(rows)}] [{category}] [{global_code}] {label} -> FAILED: {e}")
    except KeyboardInterrupt:
        STOP.set()
        executor.shutdown(wait=False, cancel_futures=True)
        print("\nCtrl+C pressed - stopping. Finished songs are kept; unfinished ones "
              "will be retried on the next run.")
        try:
            # Give running downloads a moment to abort cleanly via the progress hook.
            deadline = time.time() + 5
            while time.time() < deadline and any(t.is_alive() for t in executor._threads):
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass  # second Ctrl+C: exit right away
        os._exit(130)

    executor.shutdown(wait=True)
    print("\nDone.")


if __name__ == "__main__":
    main()