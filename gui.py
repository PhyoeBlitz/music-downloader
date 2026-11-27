#!/usr/bin/env python3
"""Windows GUI for download_music.py (Tkinter, no extra dependencies).

Builds the same command-line options from the form and runs
download_music.main() in a background thread, streaming its output into the
log box. Run with:  python gui.py
"""

import os
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import download_music as dm

APP_TITLE = "Music Downloader"
BROWSERS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi"]
QUALITIES = ["128", "192", "256", "320"]
PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]")


class QueueWriter:
    """File-like object that forwards text to the GUI thread through a queue.
    Text ending in '\\r' (yt-dlp's live progress line) is sent as a status
    update instead of being appended to the log."""

    def __init__(self, q: queue.Queue):
        self.q = q
        self.buf = ""

    def write(self, s):
        if not s:
            return 0
        self.buf += s
        while True:
            m = re.search(r"[\r\n]", self.buf)
            if not m:
                break
            piece, sep = self.buf[: m.start()], m.group()
            self.buf = self.buf[m.end():]
            if sep == "\r":
                if piece.strip():
                    self.q.put(("status", piece.strip()))
            else:
                self.q.put(("log", piece))
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def build_args(v: dict, mode: str) -> list:
    """Turn the form values into download_music command-line arguments.
    mode: 'download', 'artwork', 'rename-plain' or 'rename-numbered'."""
    args = ["--csv", v["csv"], "--out", v["out"]]
    if mode == "download":
        args += ["--workers", str(v["workers"]), "--quality", v["quality"]]
        if not v["numbering"]:
            args.append("--no-numbering")
        if not v["artwork"]:
            args.append("--no-artwork")
    elif mode == "artwork":
        args.append("--add-artwork")
    elif mode == "rename-plain":
        args += ["--rename", "plain"]
    elif mode == "rename-numbered":
        args += ["--rename", "numbered"]
    if mode in ("download", "artwork"):
        if v["cookie_mode"] == "browser":
            args += ["--cookies-from-browser", v["browser"]]
        elif v["cookie_mode"] == "file" and v["cookie_file"]:
            args += ["--cookies", v["cookie_file"]]
    return args


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("820x640")
        self.minsize(700, 520)
        self.q = queue.Queue()
        self.worker = None

        # Route all output (ours and yt-dlp's) into the log box. In the
        # windowed exe sys.stdout/stderr are None, so this is also required
        # for anything to print at all.
        sys.stdout = sys.stderr = QueueWriter(self.q)

        self.csv_var = tk.StringVar()
        self.out_var = tk.StringVar(value=str(dm.app_dir() / "downloads"))
        self.numbering_var = tk.BooleanVar(value=True)
        self.artwork_var = tk.BooleanVar(value=True)
        self.workers_var = tk.IntVar(value=2)
        self.quality_var = tk.StringVar(value="192")
        self.cookie_mode_var = tk.StringVar(value="none")
        self.browser_var = tk.StringVar(value="firefox")
        self.cookie_file_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")

        self._build()
        self._update_cookie_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    # ---------- layout ----------
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)

        ttk.Label(root, text="Song list (CSV):").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(root, textvariable=self.csv_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(root, text="Browse…", command=self._pick_csv).grid(row=0, column=2, **pad)

        ttk.Label(root, text="Save to folder:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(root, textvariable=self.out_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(root, text="Browse…", command=self._pick_out).grid(row=1, column=2, **pad)

        opts = ttk.LabelFrame(root, text="Options", padding=8)
        opts.grid(row=2, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Checkbutton(opts, text="Number file names (01_Song_0001.mp3)",
                        variable=self.numbering_var).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(opts, text="Embed album cover",
                        variable=self.artwork_var).grid(row=0, column=2, columnspan=2, sticky="w", padx=(20, 0))
        ttk.Label(opts, text="Parallel downloads:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(opts, from_=1, to=4, width=4, textvariable=self.workers_var,
                    state="readonly").grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(opts, text="MP3 quality (kbps):").grid(row=1, column=2, sticky="w", padx=(20, 0), pady=(6, 0))
        ttk.Combobox(opts, values=QUALITIES, width=6, textvariable=self.quality_var,
                     state="readonly").grid(row=1, column=3, sticky="w", pady=(6, 0))

        ck = ttk.LabelFrame(root, text="YouTube sign-in (only needed for age-restricted songs)", padding=8)
        ck.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        ck.columnconfigure(4, weight=1)
        for col, (label, value) in enumerate([("None", "none"), ("From browser:", "browser")]):
            ttk.Radiobutton(ck, text=label, value=value, variable=self.cookie_mode_var,
                            command=self._update_cookie_widgets).grid(row=0, column=col * 2, sticky="w")
        self.browser_box = ttk.Combobox(ck, values=BROWSERS, width=10, textvariable=self.browser_var,
                                        state="readonly")
        self.browser_box.grid(row=0, column=3, sticky="w", padx=(0, 16))
        ttk.Radiobutton(ck, text="cookies.txt:", value="file", variable=self.cookie_mode_var,
                        command=self._update_cookie_widgets).grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.cookie_entry = ttk.Entry(ck, textvariable=self.cookie_file_var)
        self.cookie_entry.grid(row=1, column=3, columnspan=2, sticky="ew", pady=(4, 0))
        self.cookie_btn = ttk.Button(ck, text="Browse…", command=self._pick_cookies)
        self.cookie_btn.grid(row=1, column=5, padx=(8, 0), pady=(4, 0))

        btns = ttk.Frame(root)
        btns.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)
        self.download_btn = ttk.Button(btns, text="⬇  Download", command=lambda: self._start("download"))
        self.download_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■  Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Open folder", command=self._open_out).pack(side="right")
        self.tools_btn = ttk.Menubutton(btns, text="Existing files")
        menu = tk.Menu(self.tools_btn, tearoff=False)
        menu.add_command(label="Add album covers", command=lambda: self._start("artwork"))
        menu.add_separator()
        menu.add_command(label="Rename to plain names (Song.mp3)", command=lambda: self._start("rename-plain"))
        menu.add_command(label="Rename to numbered names (01_Song_0001.mp3)",
                         command=lambda: self._start("rename-numbered"))
        self.tools_btn["menu"] = menu
        self.tools_btn.pack(side="right", padx=6)

        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(root, textvariable=self.status_var, foreground="#555").grid(
            row=6, column=0, columnspan=3, sticky="w", padx=8)

        logf = ttk.Frame(root)
        logf.grid(row=7, column=0, columnspan=3, sticky="nsew", **pad)
        root.rowconfigure(7, weight=1)
        self.log = tk.Text(logf, wrap="word", height=12, state="disabled",
                           font=("Consolas", 9), background="#fbfbfb")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log.tag_configure("err", foreground="#b00020")
        self.log.tag_configure("ok", foreground="#1b7f3b")

    # ---------- pickers ----------
    def _pick_csv(self):
        p = filedialog.askopenfilename(title="Choose song list",
                                       filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if p:
            self.csv_var.set(p)

    def _pick_out(self):
        p = filedialog.askdirectory(title="Choose where to save songs")
        if p:
            self.out_var.set(p)

    def _pick_cookies(self):
        p = filedialog.askopenfilename(title="Choose cookies.txt",
                                       filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if p:
            self.cookie_file_var.set(p)
            self.cookie_mode_var.set("file")
            self._update_cookie_widgets()

    def _update_cookie_widgets(self):
        mode = self.cookie_mode_var.get()
        self.browser_box.configure(state="readonly" if mode == "browser" else "disabled")
        st = "normal" if mode == "file" else "disabled"
        self.cookie_entry.configure(state=st)
        self.cookie_btn.configure(state=st)

    def _open_out(self):
        out = Path(self.out_var.get())
        out.mkdir(parents=True, exist_ok=True)
        os.startfile(out)

    # ---------- running ----------
    def _values(self):
        return {
            "csv": self.csv_var.get().strip(), "out": self.out_var.get().strip(),
            "numbering": self.numbering_var.get(), "artwork": self.artwork_var.get(),
            "workers": self.workers_var.get(), "quality": self.quality_var.get(),
            "cookie_mode": self.cookie_mode_var.get(), "browser": self.browser_var.get(),
            "cookie_file": self.cookie_file_var.get().strip(),
        }

    def _start(self, mode):
        if self.worker and self.worker.is_alive():
            return
        v = self._values()
        if not v["csv"] or not Path(v["csv"]).exists():
            messagebox.showwarning(APP_TITLE, "Please choose a song list (CSV) first.")
            return
        if not v["out"]:
            messagebox.showwarning(APP_TITLE, "Please choose a folder to save to.")
            return
        if v["cookie_mode"] == "file" and not Path(v["cookie_file"]).exists():
            messagebox.showwarning(APP_TITLE, "The cookies.txt file was not found.")
            return
        if mode.startswith("rename") and not messagebox.askyesno(
                APP_TITLE, "Rename the downloaded files in:\n" + v["out"] + "\n\nContinue?"):
            return

        dm.STOP.clear()
        self.progress.configure(value=0, maximum=1)
        self._append(f"\n=== {mode.replace('-', ' ').title()} ===\n")
        self._set_running(True)
        self.worker = threading.Thread(target=self._run, args=(build_args(v, mode),), daemon=True)
        self.worker.start()

    def _run(self, argv):
        try:
            dm.main(argv)
        except SystemExit as e:  # argparse / sys.exit("message") inside main()
            if e.code not in (None, 0):
                self.q.put(("log", f"ERROR: {e.code}" if isinstance(e.code, str) else "Stopped with an error."))
        except Exception as e:
            self.q.put(("log", f"ERROR: {e}"))
        finally:
            self.q.put(("done", None))

    def _stop(self):
        dm.STOP.set()
        self.status_var.set("Stopping… (waiting for running downloads to cancel)")
        self.stop_btn.configure(state="disabled")

    def _set_running(self, running):
        self.download_btn.configure(state="disabled" if running else "normal")
        self.tools_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        if running:
            self.status_var.set("Working…")

    # ---------- output ----------
    def _append(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    line = payload
                    tag = "err" if re.search(r"FAILED|ERROR|Could not", line) else (
                        "ok" if " <- " in line or "artwork added" in line else None)
                    m = PROGRESS_RE.match(line)
                    if m:
                        self.progress.configure(value=int(m.group(1)), maximum=int(m.group(2)))
                        self.status_var.set(f"{m.group(1)} of {m.group(2)} done")
                    self._append(line + "\n", tag)
                elif kind == "status":
                    self.status_var.set(payload[:150])
                elif kind == "done":
                    self._set_running(False)
                    if not self.status_var.get().endswith("done"):
                        self.status_var.set("Finished.")
                    else:
                        self.status_var.set(self.status_var.get() + " - finished.")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_TITLE, "A job is still running. Stop it and quit?"):
                return
            dm.STOP.set()
        self.destroy()
        os._exit(0)  # don't wait for in-flight downloads to wind down


def enable_dpi_awareness():
    """Render sharply on scaled (125%/150%) displays instead of being bitmap-stretched by Windows."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


if __name__ == "__main__":
    enable_dpi_awareness()
    App().mainloop()
