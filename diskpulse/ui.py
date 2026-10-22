"""DiskPulse GUI."""

from __future__ import annotations

import ctypes
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import __version__
from . import actions
from .disk import IoProc, IoTracker, Volume, list_volumes
from .health import DiskHealth, query as query_health
from .instance import WINDOW_TITLE
from .settings import INTERVALS, load_interval, save_interval

BG = "#111111"
FG = "#ffffff"
MUTED = "#999999"
CARD = "#1a1a1a"
BAR = "#333333"
BTN = "#333333"
ACCENT = "#93c5fd"
OK = "#86efac"
WARN = "#fca5a5"
HOT = "#fde68a"


def _bar_color(pct: float) -> str:
    if pct >= 90:
        return WARN
    if pct >= 70:
        return HOT
    return OK


def _space(used: int, total: int, free: int) -> tuple[str, str, str]:
    whole = round(total / (1024 ** 3), 1)
    left = round(free / (1024 ** 3), 1)
    taken = round(whole - left, 1)
    return f"{left:.1f}", f"{taken:.1f}", f"{whole:.1f}"


def _rate(n: float) -> tuple[str, str]:
    if n < 32 * 1024:
        return "—", MUTED
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} КБ/с", HOT
    mb = n / (1024 * 1024)
    return f"{mb:.1f} МБ/с", WARN if mb >= 40 else HOT


def _mood(pct: float) -> tuple[str, str]:
    if pct >= 92:
        return "диск почти полный", WARN
    if pct >= 80:
        return "места мало", HOT
    if pct >= 50:
        return "места ещё хватает", OK
    return "места полно", OK


class DiskPulseApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry("560x580")
        self.minsize(480, 480)
        self.configure(bg=BG)
        self._sec = load_interval()
        self._vol_job: str | None = None
        self._proc_job: str | None = None
        self._busy = False
        self._nudge = False
        self._vols: list[Volume] = []
        self._picked = "C:"
        self._proc_pids: list[int] | None = None
        self._proc_rows: list[dict] = []
        self._by_pid: dict[int, dict] = {}
        self._track = IoTracker()
        self._health_cache: DiskHealth | None = None
        self._health_letter = ""
        self._health_at = 0.0

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.TCombobox",
            fieldbackground=CARD, background=BTN, foreground=FG, arrowcolor=FG,
            darkcolor=CARD, lightcolor=CARD, bordercolor=BTN,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", CARD)],
            foreground=[("readonly", FG)],
            background=[("readonly", BTN)],
        )

        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=22, pady=16)

        head = tk.Frame(pad, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="DiskPulse", bg=BG, fg=FG, font=("Segoe UI Semibold", 20)).pack(side="left")
        tk.Label(
            head, text="неделя 9 · мониторинг · день 60", bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(side="right", pady=(8, 0))

        tk.Label(pad, text="какой диск смотрим", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 4))
        self._vol_var = tk.StringVar()
        self._cb = ttk.Combobox(
            pad, textvariable=self._vol_var, values=(), state="readonly",
            width=48, style="Dark.TCombobox", font=("Segoe UI", 12),
        )
        self._cb.pack(anchor="w", fill="x")
        self._cb.bind("<<ComboboxSelected>>", lambda _e: self._on_pick())

        stat = tk.Frame(pad, bg=BG)
        stat.pack(fill="x", pady=(16, 8))
        left = tk.Frame(stat, bg=BG)
        left.pack(side="left", fill="x", expand=True)
        right = tk.Frame(stat, bg=BG)
        right.pack(side="right", anchor="ne")
        self._now = tk.Label(left, text="—", bg=BG, fg=OK, font=("Segoe UI Semibold", 26), justify="left")
        self._now.pack(anchor="w")
        self._mood = tk.Label(left, text="", bg=BG, fg=MUTED, font=("Segoe UI", 12))
        self._mood.pack(anchor="w")
        self._health = tk.Label(right, text="", bg=BG, fg=MUTED, font=("Segoe UI Semibold", 22), anchor="e")
        self._health.pack(anchor="e")
        self._health_model = tk.Label(right, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="e")
        self._health_model.pack(anchor="e")

        self._bar_track = tk.Frame(pad, bg=BAR, height=22)
        self._bar_track.pack(fill="x")
        self._bar_track.pack_propagate(False)
        self._bar_fill = tk.Frame(self._bar_track, bg=OK, height=22)
        self._bar_fill.place(relx=0, rely=0, relheight=1, relwidth=0)
        self._bar_lbl = tk.Label(pad, text="", bg=BG, fg=FG, font=("Segoe UI", 10))
        self._bar_lbl.pack(anchor="w", pady=(6, 0))

        self._busy_lbl = tk.Label(pad, text="", bg=BG, fg=MUTED, font=("Segoe UI", 11), wraplength=500, justify="left")
        self._busy_lbl.pack(anchor="w", pady=(14, 0))

        who = tk.Frame(pad, bg=BG)
        who.pack(fill="x", pady=(8, 4))
        tk.Label(who, text="кто читает / пишет", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        labels = [lab for _, lab in INTERVALS]
        self._iv_var = tk.StringVar(value=next((lab for sec, lab in INTERVALS if sec == self._sec), "1 с"))
        self._iv = ttk.Combobox(
            who, textvariable=self._iv_var, values=labels, state="readonly",
            width=6, style="Dark.TCombobox", font=("Segoe UI", 9),
        )
        self._iv.pack(side="right")
        self._iv.bind("<<ComboboxSelected>>", lambda _e: self._on_interval())
        tk.Label(who, text="обновление", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="right", padx=(0, 6))

        list_wrap = tk.Frame(pad, bg=BG)
        list_wrap.pack(fill="both", expand=True)
        self._proc_canvas = tk.Canvas(list_wrap, bg=BG, highlightthickness=0, bd=0)
        self._proc_sb = ttk.Scrollbar(list_wrap, orient="vertical", command=self._proc_canvas.yview)
        self._proc_canvas.configure(yscrollcommand=self._proc_sb.set)
        self._proc_canvas.pack(side="left", fill="both", expand=True)
        self._rows = tk.Frame(self._proc_canvas, bg=BG)
        self._rows_win = self._proc_canvas.create_window((0, 0), window=self._rows, anchor="nw")
        self._rows.bind("<Configure>", lambda _e: self._sync_scroll())
        self._proc_canvas.bind("<Configure>", self._on_canvas_cfg)
        self._proc_canvas.bind("<Enter>", lambda _e: self._proc_canvas.bind_all("<MouseWheel>", self._on_wheel))
        self._proc_canvas.bind("<Leave>", lambda _e: self._proc_canvas.unbind_all("<MouseWheel>"))

        self._status = tk.Label(pad, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=500, justify="left")
        self._status.pack(anchor="w", pady=(8, 0))

        foot = tk.Frame(pad, bg=BG)
        foot.pack(fill="x", side="bottom", pady=(8, 0))
        tk.Label(foot, text=f"v{__version__} · darkshade", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="right")

        self.bind("<Configure>", self._on_resize)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(20, self._place)
        self._vol_job = self.after(200, self._tick_vol)
        self._proc_job = self.after(400, self._tick_proc)
        self.after(300, self._refresh_health)

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _close(self) -> None:
        for job in (self._vol_job, self._proc_job):
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self.destroy()

    def _on_interval(self) -> None:
        lab = self._iv_var.get()
        for sec, name in INTERVALS:
            if name == lab:
                self._sec = sec
                save_interval(sec)
                self._request_now()
                return

    def _on_pick(self) -> None:
        lab = self._vol_var.get()
        for v in self._vols:
            if v.title == lab:
                if self._picked != v.letter:
                    self._health_at = 0.0
                    self._health.configure(text="…", fg=MUTED)
                    self._health_model.configure(text="")
                self._picked = v.letter
                self._paint_vol(v)
                self._refresh_health()
                self._request_now()
                return

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        w = max(360, event.width - 48)
        self._now.configure(wraplength=max(200, w - 180))
        self._busy_lbl.configure(wraplength=w)
        self._status.configure(wraplength=w)
        self._sync_scroll()

    def _on_canvas_cfg(self, event: tk.Event) -> None:
        self._proc_canvas.itemconfigure(self._rows_win, width=event.width)
        self._sync_scroll()

    def _sync_scroll(self) -> None:
        self._proc_canvas.update_idletasks()
        bbox = self._proc_canvas.bbox("all") or (0, 0, 0, 0)
        self._proc_canvas.configure(scrollregion=bbox)
        need = bbox[3] > self._proc_canvas.winfo_height() + 2
        if need:
            if not self._proc_sb.winfo_ismapped():
                self._proc_sb.pack(side="right", fill="y")
        elif self._proc_sb.winfo_ismapped():
            self._proc_sb.pack_forget()
            self._proc_canvas.yview_moveto(0)

    def _on_wheel(self, event: tk.Event) -> str | None:
        self._proc_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _place(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width() or 560, self.winfo_height() or 580
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        self.geometry(f"{w}x{h}+{(sw - w) // 2 + 120}+{(sh - h) // 6}")

    def _tick_vol(self) -> None:
        if not self._alive():
            return
        try:
            vols = list_volumes()
        except Exception:
            vols = []
        if not vols:
            self._status.configure(text="не удалось прочитать диски", fg=WARN)
        else:
            self._apply_vols(vols)
        self._vol_job = self.after(1000, self._tick_vol)

    def _apply_vols(self, vols: list[Volume]) -> None:
        self._vols = vols
        labels = [v.title for v in vols]
        if tuple(self._cb.cget("values") or ()) != tuple(labels):
            self._cb.configure(values=labels)
        picked = next((v for v in vols if v.letter == self._picked), vols[0])
        self._picked = picked.letter
        want = picked.title
        if self._vol_var.get() != want:
            self._vol_var.set(want)
        self._paint_vol(picked)

    def _paint_vol(self, v: Volume) -> None:
        pct = v.used_pct
        mood, mfg = _mood(pct)
        left, taken, whole = _space(v.used, v.total, v.free)
        self._now.configure(text=f"Свободно {left} ГБ", fg=_bar_color(pct))
        self._mood.configure(text=mood, fg=mfg)
        self._bar_fill.configure(bg=_bar_color(pct))
        self._bar_fill.place(relx=0, rely=0, relheight=1, relwidth=max(0.02, pct / 100.0))
        self._bar_lbl.configure(text=f"занято {taken} из {whole} ГБ")
        self._status.configure(text="", fg=MUTED)
        if self._health_letter == v.letter and self._health_cache is not None:
            self._paint_health(self._health_cache)

    def _refresh_health(self) -> None:
        letter = self._picked
        stale = letter != self._health_letter or (time.monotonic() - self._health_at) > 15
        if not stale:
            return

        def work() -> None:
            try:
                health = query_health(letter)
            except Exception:
                health = None

            def done() -> None:
                if not self._alive() or letter != self._picked:
                    return
                if health is None:
                    if self._health_cache is None or self._health_letter != letter:
                        self._health.configure(text="Неизвестно", fg=MUTED)
                        self._health_model.configure(text="")
                    return
                self._health_cache = health
                self._health_letter = letter
                self._health_at = time.monotonic()
                self._paint_health(health)

            try:
                self.after(0, done)
            except tk.TclError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _paint_health(self, h: DiskHealth) -> None:
        fg = {"good": OK, "warn": HOT, "bad": WARN}.get(h.kind, MUTED)
        self._health.configure(text=h.text, fg=fg)
        extra = " · ".join(x for x in (h.media, h.model) if x)
        self._health_model.configure(text=extra)

    def _request_now(self) -> None:
        if self._busy:
            self._nudge = True
            return
        self._schedule_proc(0)

    def _schedule_proc(self, delay_ms: int | None = None) -> None:
        if self._proc_job:
            try:
                self.after_cancel(self._proc_job)
            except tk.TclError:
                pass
            self._proc_job = None
        ms = int(self._sec * 1000) if delay_ms is None else delay_ms
        self._proc_job = self.after(max(0, ms), self._tick_proc)

    def _tick_proc(self) -> None:
        if not self._alive():
            return
        if self._busy:
            return
        self._busy = True

        def work() -> None:
            try:
                procs = self._track.top()
                total = self._track.sum_bps
            except Exception:
                procs = None
                total = None

            def done() -> None:
                self._busy = False
                if not self._alive():
                    return
                if procs is not None:
                    if total is not None:
                        rt, rc = _rate(total)
                        self._busy_lbl.configure(
                            text="сейчас тихо" if rt == "—" else f"вся система {rt}",
                            fg=MUTED if rt == "—" else rc,
                        )
                    self._render_procs(procs)
                else:
                    self._status.configure(text="не удалось прочитать процессы", fg=WARN)
                if self._nudge:
                    self._nudge = False
                    self._schedule_proc(0)
                else:
                    self._schedule_proc()

            try:
                self.after(0, done)
            except tk.TclError:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()

    def _paint_row(self, p: IoProc, row: dict) -> None:
        rt, rc = _rate(p.read_bps)
        wt, wc = _rate(p.write_bps)
        row["rd"].configure(text=f"R {rt}", fg=rc)
        row["wr"].configure(text=f"W {wt}", fg=wc)
        row["name"].configure(text=f"{p.name}  ·  {p.pid}")
        row["btn"].configure(
            command=lambda n=p.name, pid=p.pid, b=row["btn"]: self._proc_menu(n, pid, b)
        )

    def _render_procs(self, procs: list[IoProc]) -> None:
        pids = [p.pid for p in procs]
        if self._by_pid and set(pids) == set(self._by_pid):
            for row in self._by_pid.values():
                row["line"].pack_forget()
            ordered = []
            for p in procs:
                row = self._by_pid[p.pid]
                self._paint_row(p, row)
                row["line"].pack(fill="x", pady=1)
                ordered.append(row)
            self._proc_rows = ordered
            self._proc_pids = pids
            return
        for w in self._rows.winfo_children():
            w.destroy()
        self._proc_rows = []
        self._by_pid = {}
        self._proc_pids = pids
        if not procs:
            tk.Label(
                self._rows,
                text="нет данных по процессам",
                bg=BG, fg=MUTED, font=("Segoe UI", 9),
            ).pack(anchor="w")
            self._sync_scroll()
            return
        for p in procs:
            line = tk.Frame(self._rows, bg=BG)
            line.pack(fill="x", pady=1)
            rd = tk.Label(line, text="", width=12, anchor="e", bg=BG, font=("Consolas", 10))
            rd.pack(side="left")
            wr = tk.Label(line, text="", width=12, anchor="e", bg=BG, font=("Consolas", 10))
            wr.pack(side="left", padx=(4, 0))
            name = tk.Label(line, text="", bg=BG, fg=FG, font=("Segoe UI", 9), anchor="w")
            name.pack(side="left", fill="x", expand=True, padx=8)
            btn = tk.Button(
                line, text="⋯",
                bg=BAR, fg=FG, relief="flat", padx=8, pady=0, cursor="hand2",
                activebackground="#444444", activeforeground=FG, font=("Segoe UI", 10),
            )
            btn.pack(side="right")
            row = {"line": line, "rd": rd, "wr": wr, "name": name, "btn": btn}
            self._paint_row(p, row)
            self._proc_rows.append(row)
            self._by_pid[p.pid] = row
        self._sync_scroll()

    def _proc_menu(self, name: str, pid: int, anchor: tk.Widget) -> None:
        real = actions.exe_name(pid, name)
        menu = tk.Menu(self, tearoff=0, bg=CARD, fg=FG, activebackground="#333333", activeforeground=FG, bd=0)
        menu.add_command(label="Снять процесс", command=lambda: self._kill_one(real, pid))
        menu.add_command(label=f"Снять все «{real}»", command=lambda: self._kill_all(real))
        menu.add_command(label="Открыть папку", command=lambda: self._open_dir(pid))
        try:
            menu.tk_popup(anchor.winfo_rootx() + anchor.winfo_width() - 8, anchor.winfo_rooty() + 20)
        finally:
            menu.grab_release()

    def _kill_one(self, name: str, pid: int) -> None:
        if not messagebox.askyesno("Снять процесс", f"Завершить {name} (PID {pid})?"):
            return
        err = actions.kill_pid(pid, name)
        if err:
            messagebox.showerror("DiskPulse", err)
            return
        self._request_now()

    def _kill_all(self, name: str) -> None:
        n = len(actions.pids_named(name))
        if not messagebox.askyesno("Снять все", f"Завершить все процессы «{name}» ({n} шт.)?"):
            return
        killed, err = actions.kill_named(name)
        if err and killed == 0:
            messagebox.showerror("DiskPulse", err)
            return
        if err:
            messagebox.showwarning("DiskPulse", f"снято {killed}, дальше: {err}")
        self._request_now()

    def _open_dir(self, pid: int) -> None:
        err = actions.open_folder(pid)
        if err:
            messagebox.showerror("DiskPulse", err)


def run() -> None:
    DiskPulseApp().mainloop()
