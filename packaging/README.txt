MusicDownloader
===============

Downloads songs from YouTube / YouTube Music as MP3 (with album cover),
into one folder per category (for example, per artist), from a CSV list.

1. SETUP (once)
---------------
MusicDownloader needs FFmpeg. Do ONE of these:
  a) Open PowerShell and run:   winget install Gyan.FFmpeg
     then close and reopen PowerShell.
  b) Or download FFmpeg from https://www.gyan.dev/ffmpeg/builds/ and copy
     ffmpeg.exe and ffprobe.exe (from its "bin" folder) into this folder,
     next to MusicDownloader.exe.

Optional but recommended: install Node.js (https://nodejs.org). It makes
YouTube downloads more reliable.

2. MAKE A CSV
-------------
Create a CSV (Excel: "Save As" > "CSV UTF-8") with this header row:

  name,category,link
  Duality,Slipknot,https://music.youtube.com/watch?v=B2lmOei7qfk

  name      song title used for the file name
  category  folder name (for example, the artist)
  link      YouTube or YouTube Music link

See sample.csv in this folder.

3. RUN - THE EASY WAY (window)
------------------------------
Double-click MusicDownloaderGUI.exe.
  * "Song list (CSV)": click Browse and pick your CSV.
  * "Save to folder": where the songs go.
  * Click Download. Click Stop to stop (finished songs are kept).
  * "Existing files" menu: add album covers to songs you already downloaded,
    or switch file names between numbered and plain - no re-download.

4. RUN - COMMAND LINE (same features)
-------------------------------------
Open PowerShell in this folder (Shift + right-click > "Open PowerShell window here") and run:

  .\MusicDownloader.exe --csv sample.csv

Songs are saved to the "downloads" folder:  downloads\<category>\01_Song_0001.mp3

Useful options:
  --out D:\Music             save somewhere else
  --no-numbering             file names without numbers:  Song.mp3
  --workers 2                songs downloaded at the same time (keep it low)
  --cookies-from-browser firefox   for age-restricted songs (sign in to YouTube in Firefox first)
  --rename plain / numbered  rename already-downloaded files, no re-download
  --add-artwork              add album covers to already-downloaded files

Run  .\MusicDownloader.exe -h  to see all options.
Press Ctrl+C to stop. Run the same command again to continue; finished songs are skipped.

TROUBLESHOOTING
---------------
* "Windows protected your PC": click "More info" > "Run anyway". The program
  isn't code-signed, so Windows warns about it. To avoid the warning, unblock
  the zip BEFORE extracting it: right-click the zip > Properties > tick
  "Unblock" > OK (or in PowerShell: Unblock-File .\MusicDownloader-windows.zip).
* "Could not find ffmpeg": do step 1.
* "Sign in to confirm you're not a bot": wait a while, use --workers 1, or add
  --cookies-from-browser firefox.
