import sys
import os
import subprocess
import psutil
import time
import csv
import queue
import threading
import shlex

import pyqtgraph as pg

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QTextEdit, QFileDialog, QLabel,
    QSplitter, QGroupBox, QCheckBox, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import (
    QTextCursor, QIcon, QTextDocument,
    QPalette, QColor, QLinearGradient, QPainter, QBrush
)
from PySide6.QtPrintSupport import QPrinter


# ─────────────────────────────────────────────────────────────────────────────
# Pipe reader helper (daemon thread)
# ─────────────────────────────────────────────────────────────────────────────
def _pipe_reader(stream, q, tag):
    try:
        for line in iter(stream.readline, ""):
            q.put((tag, line))
    except Exception:
        pass
    finally:
        q.put((tag, None))


# ─────────────────────────────────────────────────────────────────────────────
# Background monitor thread
# ─────────────────────────────────────────────────────────────────────────────
class ProcessMonitorThread(QThread):
    stats_signal    = Signal(dict)
    finished_signal = Signal(dict)
    log_signal      = Signal(str)
    stdout_signal   = Signal(str)
    stderr_signal   = Signal(str)

    def __init__(self, script_path, extra_paths=None, extra_args=None, custom_cwd=None):
        super().__init__()
        self.script_path   = os.path.abspath(script_path)
        self.script_dir    = os.path.dirname(self.script_path)
        self.extra_paths   = extra_paths or []
        self.extra_args    = extra_args or []
        self.custom_cwd    = os.path.abspath(custom_cwd) if custom_cwd else self.script_dir
        self.is_running    = True
        self.proc          = None
        self.tracked_pids  = set()
        self._output_queue = queue.Queue()

    # ── Build environment ────────────────────────────────────────────────────
    def _build_env(self):
        env = os.environ.copy()

        # ★ FIX 1: Force UTF-8 I/O — prevents UnicodeEncodeError on Windows cp1252
        env["PYTHONUTF8"]                  = "1"
        env["PYTHONIOENCODING"]            = "utf-8"
        env["PYTHONLEGACYWINDOWSSTDIO"]    = "0"

        paths = [self.script_dir, self.custom_cwd] + self.extra_paths

        # ★ FIX 2: Auto-discover all sub-dirs containing .py files → full PYTHONPATH
        for root, dirs, files in os.walk(self.custom_cwd):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in (
                    "__pycache__", ".git", "node_modules",
                    "venv", ".venv", "env", ".env", "dist", "build",
                )
                and not d.endswith(".egg-info")
            ]
            if any(f.endswith(".py") for f in files):
                paths.append(root)

        seen, unique = set(), []
        for p in paths:
            if p not in seen:
                seen.add(p)
                unique.append(p)

        existing_pp = env.get("PYTHONPATH", "")
        joined      = os.pathsep.join(unique)
        env["PYTHONPATH"] = f"{joined}{os.pathsep}{existing_pp}" if existing_pp else joined
        return env

    # ── Main run loop ────────────────────────────────────────────────────────
    def run(self):
        env = self._build_env()
        # -u = unbuffered stdout/stderr (live output)
        cmd = [sys.executable, "-u", self.script_path] + self.extra_args

        self.log_signal.emit(f"CWD  ▸  {self.custom_cwd}")
        self.log_signal.emit(f"CMD  ▸  {' '.join(cmd)}")
        pp = env.get("PYTHONPATH", "")
        self.log_signal.emit(
            f"PYTHONPATH ▸  {pp[:300]}{'…' if len(pp) > 300 else ''}"
        )

        self.proc = subprocess.Popen(
            cmd,
            cwd=self.custom_cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",   # ★ FIX 3: read pipes as UTF-8
            errors="replace",   # replace un-decodable bytes instead of crashing
            bufsize=1,
        )

        try:
            main_p = psutil.Process(self.proc.pid)
            self.tracked_pids.add(self.proc.pid)
        except psutil.NoSuchProcess:
            self.finished_signal.emit({"status": "Failed to start"})
            return

        t_out = threading.Thread(
            target=_pipe_reader,
            args=(self.proc.stdout, self._output_queue, "out"),
            daemon=True,
        )
        t_err = threading.Thread(
            target=_pipe_reader,
            args=(self.proc.stderr, self._output_queue, "err"),
            daemon=True,
        )
        t_out.start(); t_err.start()

        start_time           = time.time()
        out_lines, err_lines = [], []
        done_out = done_err  = False

        while not (done_out and done_err):
            try:
                while True:
                    tag, line = self._output_queue.get(timeout=0.05)
                    if tag == "out":
                        if line is None: done_out = True
                        else:
                            out_lines.append(line)
                            self.stdout_signal.emit(line.rstrip("\n"))
                    else:
                        if line is None: done_err = True
                        else:
                            err_lines.append(line)
                            self.stderr_signal.emit(line.rstrip("\n"))
            except queue.Empty:
                pass

            if not self.is_running:
                break

            if self.proc.poll() is None:
                try:
                    children      = main_p.children(recursive=True)
                    self.tracked_pids.update(c.pid for c in children)
                    total_mem     = main_p.memory_info().rss
                    total_cpu     = main_p.cpu_percent(interval=None)
                    total_threads = main_p.num_threads()
                    for child in children:
                        try:
                            total_mem     += child.memory_info().rss
                            total_cpu     += child.cpu_percent(interval=None)
                            total_threads += child.num_threads()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    self.stats_signal.emit({
                        "time":        round(time.time() - start_time, 2),
                        "mem_mb":      total_mem / (1024 * 1024),
                        "cpu_percent": total_cpu,
                        "threads":     total_threads,
                        "children":    len(children),
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        self.proc.wait()
        t_out.join(timeout=2); t_err.join(timeout=2)
        self._cleanup_and_report(out_lines, err_lines)

    def _cleanup_and_report(self, out_lines, err_lines):
        time.sleep(0.4)
        zombies = []
        for pid in self.tracked_pids:
            if psutil.pid_exists(pid):
                try:
                    p = psutil.Process(pid)
                    if p.status() != psutil.STATUS_ZOMBIE:
                        zombies.append(f"PID {pid}  ({p.name()})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        self.finished_signal.emit({
            "zombies":   zombies,
            "stdout":    "".join(out_lines),
            "stderr":    "".join(err_lines),
            "exit_code": self.proc.returncode if self.proc else None,
        })

    def stop_process(self):
        self.is_running = False
        if self.proc and self.proc.poll() is None:
            self.log_signal.emit("KILL  ▸  Terminating process tree…")
            try:
                parent = psutil.Process(self.proc.pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
            except psutil.NoSuchProcess:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Accent bar widget
# ─────────────────────────────────────────────────────────────────────────────
class AccentBar(QFrame):
    def __init__(self, c1="#00d4aa", c2="#60a5fa", parent=None):
        super().__init__(parent)
        self.c1 = c1; self.c2 = c2
        self.setFixedHeight(2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _):
        p = QPainter(self)
        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0.0, QColor(self.c1))
        grad.setColorAt(0.5, QColor(self.c2))
        grad.setColorAt(1.0, QColor(self.c1))
        p.fillRect(self.rect(), QBrush(grad))


# ─────────────────────────────────────────────────────────────────────────────
# Stat badge widget
# ─────────────────────────────────────────────────────────────────────────────
class StatBadge(QFrame):
    def __init__(self, label, unit, accent="#00d4aa", parent=None):
        super().__init__(parent)
        self.setObjectName("StatBadge")
        self.setFixedHeight(60)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)

        left = QVBoxLayout()
        left.setSpacing(0)
        lbl_top = QLabel(label.upper())
        lbl_top.setStyleSheet(
            "color: #2e3555; font-size: 9px; letter-spacing: 1.5px; background: transparent;"
        )
        lbl_unit = QLabel(unit)
        lbl_unit.setStyleSheet("color: #2e3555; font-size: 9px; background: transparent;")
        left.addWidget(lbl_top)
        left.addWidget(lbl_unit)

        self.value_lbl = QLabel("—")
        self.value_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._accent = accent
        self.value_lbl.setStyleSheet(
            f"color: {accent}; font-size: 18px; font-weight: 700; "
            "font-family: 'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace;"
            " background: transparent;"
        )

        layout.addLayout(left)
        layout.addWidget(self.value_lbl, 1)

    def set_value(self, v, color=None):
        self.value_lbl.setText(str(v))
        if color:
            self.value_lbl.setStyleSheet(
                f"color: {color}; font-size: 18px; font-weight: 700; "
                "font-family: 'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace;"
                " background: transparent;"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────
STYLESHEET = """
    * { box-sizing: border-box; }
    QMainWindow, QWidget        { background: #0c0e18; color: #dde1ec; }
    QSplitter::handle           { background: #181b2a; }

    QLineEdit {
        background: #13162a;
        color: #dde1ec;
        border: 1px solid #252840;
        border-radius: 5px;
        padding: 6px 10px;
        font-size: 12px;
    }
    QLineEdit:focus     { border: 1px solid #00d4aa; }

    QPushButton {
        background: #181b2e;
        color: #8892a4;
        border: 1px solid #252840;
        border-radius: 5px;
        padding: 6px 14px;
        font-size: 12px;
        font-weight: 600;
    }
    QPushButton:hover    { background: #20243a; border-color: #00d4aa88; color: #00d4aa; }
    QPushButton:disabled { background: #0e101a; color: #2d3148; border-color: #181b2a; }

    QPushButton#run_btn {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
            stop:0 #009e7a, stop:1 #00c99e);
        color: #051a14;
        border: none;
        font-weight: 700;
        font-size: 12px;
        letter-spacing: 0.5px;
    }
    QPushButton#run_btn:hover    { background: #00e4b5; color: #020f0b; }
    QPushButton#run_btn:disabled { background: #0d1f19; color: #1a3d2e; border: none; }

    QPushButton#stop_btn {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
            stop:0 #9b1c1c, stop:1 #dc2626);
        color: #fff;
        border: none;
        font-weight: 700;
        font-size: 12px;
    }
    QPushButton#stop_btn:hover    { background: #ef4444; }
    QPushButton#stop_btn:disabled { background: #130a0a; color: #3d1515; border: none; }

    QGroupBox {
        border: 1px solid #1c1f34;
        border-radius: 7px;
        margin-top: 14px;
        padding-top: 4px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 2px;
        color: #2e3555;
        text-transform: uppercase;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        background: #0c0e18;
    }

    QTextEdit {
        background: #07090f;
        color: #b8c2d8;
        border: 1px solid #1c1f34;
        border-radius: 6px;
        font-family: 'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace;
        font-size: 11px;
        padding: 6px 8px;
        selection-background-color: #00d4aa30;
    }

    QFrame#StatBadge {
        background: #0f1120;
        border: 1px solid #1c1f34;
        border-radius: 7px;
    }

    QCheckBox { color: #3d4460; font-size: 11px; spacing: 6px; }
    QCheckBox::indicator {
        width: 13px; height: 13px;
        border: 1px solid #252840;
        border-radius: 3px;
        background: #13162a;
    }
    QCheckBox::indicator:checked { background: #00d4aa; border-color: #00d4aa; }

    QLabel#sec { color: #2e3555; font-size: 10px; letter-spacing: 1.5px; }

    QScrollBar:vertical   { background: #07090f; width: 7px; }
    QScrollBar::handle:vertical {
        background: #252840; border-radius: 3px; min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar:horizontal { background: #07090f; height: 7px; }
    QScrollBar::handle:horizontal {
        background: #252840; border-radius: 3px; min-width: 20px;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


class AnalyzerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Analyzer Pro")
        self.resize(1280, 900)
        self.setMinimumSize(920, 660)

        icon_path = "analyzer.ico"
        if hasattr(sys, "_MEIPASS"):
            icon_path = os.path.join(sys._MEIPASS, icon_path)
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.setStyleSheet(STYLESHEET)
        self.time_data, self.mem_data, self.cpu_data = [], [], []
        self.monitor_thread = None
        self._build_ui()

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _sec(self, text):
        lbl = QLabel(text.upper())
        lbl.setObjectName("sec")
        return lbl

    # ── Build UI ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        root_w = QWidget()
        self.setCentralWidget(root_w)
        root = QVBoxLayout(root_w)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(5)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(0)
        t1 = QLabel("ANALYZER")
        t1.setStyleSheet(
            "color: #00d4aa; font-size: 14px; font-weight: 800; letter-spacing: 5px;"
            " font-family: 'JetBrains Mono','Cascadia Code','Consolas',monospace;"
        )
        t2 = QLabel(" PRO")
        t2.setStyleSheet(
            "color: #60a5fa; font-size: 14px; font-weight: 800; letter-spacing: 5px;"
            " font-family: 'JetBrains Mono','Cascadia Code','Consolas',monospace;"
        )
        sub = QLabel("  ·  Python Dynamic Performance Profiler")
        sub.setStyleSheet("color: #2e3555; font-size: 10px; letter-spacing: 0.5px;")
        hdr.addWidget(t1)
        hdr.addWidget(t2)
        hdr.addWidget(sub)
        hdr.addStretch()
        root.addLayout(hdr)
        root.addWidget(AccentBar("#00d4aa", "#60a5fa"))
        root.addSpacing(1)

        # ── Config ────────────────────────────────────────────────────────────
        cfg = QGroupBox("Configuration")
        cfl = QVBoxLayout(cfg)
        cfl.setSpacing(6)
        cfl.setContentsMargins(10, 16, 10, 8)

        # Row 1: Script
        r1 = QHBoxLayout(); r1.setSpacing(6)
        r1.addWidget(self._sec("Script"))
        self.file_input = QLineEdit()
        self.file_input.setPlaceholderText("Select Python entry-point  (*.py) …")
        btn_f = QPushButton("Browse"); btn_f.setFixedWidth(78)
        btn_f.clicked.connect(self.browse_file)
        r1.addWidget(self.file_input); r1.addWidget(btn_f)
        cfl.addLayout(r1)

        # Row 2: Working dir
        r2 = QHBoxLayout(); r2.setSpacing(6)
        r2.addWidget(self._sec("Work Dir"))
        self.cwd_input = QLineEdit()
        self.cwd_input.setPlaceholderText("Defaults to script's parent folder …")
        btn_d = QPushButton("Browse"); btn_d.setFixedWidth(78)
        btn_d.clicked.connect(self.browse_dir)
        self.auto_cwd_chk = QCheckBox("Auto")
        self.auto_cwd_chk.setChecked(True)
        self.auto_cwd_chk.stateChanged.connect(self._toggle_cwd)
        self._toggle_cwd(Qt.Checked)
        r2.addWidget(self.cwd_input); r2.addWidget(btn_d); r2.addWidget(self.auto_cwd_chk)
        cfl.addLayout(r2)

        # Row 3: Extra paths + args
        r3 = QHBoxLayout(); r3.setSpacing(12)
        pc = QVBoxLayout(); pc.setSpacing(4)
        pc.addWidget(self._sec("Extra PYTHONPATH"))
        ep = QHBoxLayout(); ep.setSpacing(4)
        self.extra_path_input = QLineEdit()
        self.extra_path_input.setPlaceholderText("Additional paths (auto-discovers by default) …")
        btn_ep = QPushButton("+"); btn_ep.setFixedWidth(30)
        btn_ep.clicked.connect(self.add_extra_path)
        ep.addWidget(self.extra_path_input); ep.addWidget(btn_ep)
        pc.addLayout(ep)

        ac = QVBoxLayout(); ac.setSpacing(4)
        ac.addWidget(self._sec("Arguments"))
        self.args_input = QLineEdit()
        self.args_input.setPlaceholderText("--debug  --port 8080  --config cfg.yaml …")
        ac.addWidget(self.args_input)

        r3.addLayout(pc, 3); r3.addLayout(ac, 2)
        cfl.addLayout(r3)
        root.addWidget(cfg)

        # ── Action row ────────────────────────────────────────────────────────
        ar = QHBoxLayout(); ar.setSpacing(6)
        self.run_btn = QPushButton("▶  Run & Analyze")
        self.run_btn.setObjectName("run_btn")
        self.run_btn.setFixedHeight(30)
        self.run_btn.setFixedWidth(160)
        self.run_btn.clicked.connect(self.start_analysis)

        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("stop_btn")
        self.stop_btn.setFixedHeight(30)
        self.stop_btn.setFixedWidth(90)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_analysis)

        btn_clr = QPushButton("Clear")
        btn_clr.setFixedHeight(30)
        btn_clr.setFixedWidth(64)
        btn_clr.clicked.connect(self.clear_output)

        ar.addWidget(self.run_btn); ar.addWidget(self.stop_btn)
        ar.addWidget(btn_clr); ar.addStretch()
        root.addLayout(ar)

        # ── Stat badges ───────────────────────────────────────────────────────
        br = QHBoxLayout(); br.setSpacing(6)
        self.b_status  = StatBadge("Status",   "",     "#00d4aa")
        self.b_ram     = StatBadge("Memory",   "MB",   "#c084fc")
        self.b_cpu     = StatBadge("CPU",      "%",    "#fbbf24")
        self.b_threads = StatBadge("Threads",  "thr",  "#60a5fa")
        self.b_child   = StatBadge("Children", "proc", "#f87171")
        self.b_status.set_value("IDLE")
        for b in [self.b_status, self.b_ram, self.b_cpu, self.b_threads, self.b_child]:
            br.addWidget(b)
        root.addLayout(br)
        root.addSpacing(2)

        # ── Splitter ──────────────────────────────────────────────────────────
        vs = QSplitter(Qt.Vertical); vs.setHandleWidth(4)

        # Graphs — two plots side by side in a single GraphicsLayoutWidget
        gg = QGroupBox("Real-time Metrics")
        ggl = QVBoxLayout(gg); ggl.setContentsMargins(4, 16, 4, 4)
        pg.setConfigOptions(antialias=True, background="#07090f", foreground="#2e3555")
        self.gw = pg.GraphicsLayoutWidget()
        self.gw.setBackground("#07090f")
        self.gw.setFixedHeight(170)

        # Memory plot (left)
        self.plot_mem = self.gw.addPlot(row=0, col=0)
        self.plot_mem.showGrid(x=True, y=True, alpha=0.10)
        self.plot_mem.setLabel("left", "Memory MB", color="#c084fc", size="9pt")
        self.plot_mem.getAxis("left").setPen(pg.mkPen("#1c1f34"))
        self.plot_mem.getAxis("bottom").setPen(pg.mkPen("#1c1f34"))
        self.plot_mem.getAxis("left").setTextPen(pg.mkPen("#3a3f5c"))
        self.plot_mem.getAxis("bottom").setTextPen(pg.mkPen("#3a3f5c"))
        self.plot_mem.setContentsMargins(0, 0, 6, 0)
        self.curve_mem = self.plot_mem.plot(
            pen=pg.mkPen("#c084fc", width=1.5),
            fillLevel=0, brush=(192, 132, 252, 20),
        )

        # CPU plot (right)
        self.plot_cpu = self.gw.addPlot(row=0, col=1)
        self.plot_cpu.showGrid(x=True, y=True, alpha=0.10)
        self.plot_cpu.setLabel("left", "CPU %", color="#fbbf24", size="9pt")
        self.plot_cpu.getAxis("left").setPen(pg.mkPen("#1c1f34"))
        self.plot_cpu.getAxis("bottom").setPen(pg.mkPen("#1c1f34"))
        self.plot_cpu.getAxis("left").setTextPen(pg.mkPen("#3a3f5c"))
        self.plot_cpu.getAxis("bottom").setTextPen(pg.mkPen("#3a3f5c"))
        self.curve_cpu = self.plot_cpu.plot(
            pen=pg.mkPen("#fbbf24", width=1.5),
            fillLevel=0, brush=(251, 191, 36, 20),
        )

        ggl.addWidget(self.gw)
        vs.addWidget(gg)

        # Output panes
        hs = QSplitter(Qt.Horizontal); hs.setHandleWidth(4)

        og = QGroupBox("Program Output — stdout")
        ogl = QVBoxLayout(og); ogl.setContentsMargins(6, 16, 6, 6); ogl.setSpacing(4)
        self.stdout_area = QTextEdit(); self.stdout_area.setReadOnly(True)
        self.stdout_area.document().setDocumentMargin(8)
        ogl.addWidget(self.stdout_area)
        hs.addWidget(og)

        lg = QGroupBox("System Log — stderr / events")
        lgl = QVBoxLayout(lg); lgl.setContentsMargins(6, 16, 6, 6); lgl.setSpacing(4)
        self.report_area = QTextEdit(); self.report_area.setReadOnly(True)
        self.report_area.document().setDocumentMargin(8)
        lgl.addWidget(self.report_area)

        exr = QHBoxLayout(); exr.setSpacing(5)
        bp = QPushButton("Export PDF"); bp.setFixedHeight(24)
        bc = QPushButton("Export CSV"); bc.setFixedHeight(24)
        bp.clicked.connect(self.export_pdf)
        bc.clicked.connect(self.export_csv)
        exr.addWidget(bp); exr.addWidget(bc); exr.addStretch()
        lgl.addLayout(exr)

        hs.addWidget(lg)
        hs.setSizes([620, 620])
        vs.addWidget(hs)
        vs.setSizes([190, 520])
        root.addWidget(vs)

    # ── UI helpers ───────────────────────────────────────────────────────────
    def _toggle_cwd(self, state):
        self.cwd_input.setEnabled(state != Qt.Checked)

    def browse_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Select Entry-point", "", "Python Files (*.py)"
        )
        if p:
            self.file_input.setText(p)
            if self.auto_cwd_chk.isChecked():
                self.cwd_input.setText(os.path.dirname(p))

    def browse_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Select Working / Project Directory")
        if p:
            self.auto_cwd_chk.setChecked(False)
            self.cwd_input.setEnabled(True)
            self.cwd_input.setText(p)

    def add_extra_path(self):
        p = QFileDialog.getExistingDirectory(self, "Add Extra PYTHONPATH Directory")
        if p:
            cur = self.extra_path_input.text().strip()
            sep = os.pathsep
            self.extra_path_input.setText(f"{cur}{sep}{p}" if cur else p)

    def clear_output(self):
        self.stdout_area.clear()
        self.report_area.clear()

    def _log(self, text, color="#2e3555"):
        safe = (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))
        self.report_area.append(
            f"<span style='color:{color};font-family:monospace;'>{safe}</span>"
        )
        self.report_area.moveCursor(QTextCursor.End)

    def _out(self, text):
        safe = (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))
        self.stdout_area.append(
            f"<span style='color:#b8c2d8;font-family:monospace;'>{safe}</span>"
        )
        self.stdout_area.moveCursor(QTextCursor.End)

    # ── Analysis lifecycle ───────────────────────────────────────────────────
    def start_analysis(self):
        script_path = self.file_input.text().strip()
        if not script_path or not os.path.exists(script_path):
            self._log("ERROR  ▸  Script file not found.", "#ef4444")
            return

        if self.auto_cwd_chk.isChecked() or not self.cwd_input.text().strip():
            cwd = os.path.dirname(os.path.abspath(script_path))
        else:
            cwd = self.cwd_input.text().strip()
            if not os.path.isdir(cwd):
                self._log(f"ERROR  ▸  Working directory not found: {cwd}", "#ef4444")
                return

        raw_extra = self.extra_path_input.text().strip()
        extra_paths = []
        if raw_extra:
            for p in raw_extra.replace(";", os.pathsep).split(os.pathsep):
                p = p.strip()
                if p and os.path.isdir(p):
                    extra_paths.append(p)

        raw_args  = self.args_input.text().strip()
        extra_args = shlex.split(raw_args) if raw_args else []

        self.clear_output()
        self.time_data, self.mem_data, self.cpu_data = [], [], []
        self.curve_mem.setData([], []); self.curve_cpu.setData([], [])

        self._log(f"START  ▸  {os.path.basename(script_path)}", "#00d4aa")
        self._log(f"CWD    ▸  {cwd}", "#60a5fa")
        if extra_paths: self._log(f"PATHS  ▸  {extra_paths}", "#a78bfa")
        if extra_args:  self._log(f"ARGS   ▸  {extra_args}", "#a78bfa")
        self._log("─" * 60, "#1c1f34")

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.b_status.set_value("RUN", "#00d4aa")

        self.monitor_thread = ProcessMonitorThread(
            script_path,
            extra_paths=extra_paths,
            extra_args=extra_args,
            custom_cwd=cwd,
        )
        self.monitor_thread.stats_signal.connect(self._update_stats)
        self.monitor_thread.finished_signal.connect(self._finish_analysis)
        self.monitor_thread.log_signal.connect(lambda t: self._log(t, "#3a3f5c"))
        self.monitor_thread.stdout_signal.connect(self._out)
        self.monitor_thread.stderr_signal.connect(lambda l: self._log(l, "#f87171"))
        self.monitor_thread.start()

    def _update_stats(self, data):
        self.b_ram.set_value(f"{data['mem_mb']:.1f}")
        self.b_cpu.set_value(f"{data['cpu_percent']:.1f}")
        self.b_threads.set_value(str(data["threads"]))
        self.b_child.set_value(str(data["children"]))
        self.time_data.append(data["time"])
        self.mem_data.append(data["mem_mb"])
        self.cpu_data.append(data["cpu_percent"])
        self.curve_mem.setData(self.time_data, self.mem_data)
        self.curve_cpu.setData(self.time_data, self.cpu_data)

    def stop_analysis(self):
        if self.monitor_thread and self.monitor_thread.isRunning():
            self.monitor_thread.stop_process()
            self.stop_btn.setEnabled(False)

    def _finish_analysis(self, result):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        exit_code = result.get("exit_code")
        ok = exit_code == 0
        self.b_status.set_value("OK" if ok else "ERR",
                                "#00d4aa" if ok else "#ef4444")
        self._log("─" * 60, "#1c1f34")
        self._log("RESULTS", "#60a5fa")
        if exit_code is not None:
            self._log(f"Exit Code  ▸  {exit_code}",
                      "#00d4aa" if ok else "#ef4444")
        zombies = result.get("zombies", [])
        if zombies:
            self._log("LEAKED PROCESSES:", "#ef4444")
            for z in zombies:
                self._log(f"  · {z}", "#fbbf24")
            self._log("Tip: use daemon=True on background threads.", "#4b5563")
        else:
            self._log("No leaked / zombie processes detected.", "#00d4aa")
        self._log("─" * 60, "#1c1f34")

    # ── Export ───────────────────────────────────────────────────────────────
    def export_pdf(self):
        if not self.report_area.toPlainText().strip():
            self._log("WARNING  ▸  Nothing to export.", "#fbbf24"); return
        p, _ = QFileDialog.getSaveFileName(
            self, "Save PDF", "analysis_log.pdf", "PDF Files (*.pdf)"
        )
        if p:
            try:
                pr = QPrinter(QPrinter.HighResolution)
                pr.setOutputFormat(QPrinter.PdfFormat)
                pr.setOutputFileName(p)
                doc = QTextDocument()
                doc.setHtml(self.report_area.toHtml())
                doc.print_(pr)
                self._log(f"PDF saved  ▸  {p}", "#00d4aa")
            except Exception as e:
                self._log(f"ERROR  ▸  {e}", "#ef4444")

    def export_csv(self):
        if not self.time_data:
            self._log("WARNING  ▸  No metrics yet.", "#fbbf24"); return
        p, _ = QFileDialog.getSaveFileName(
            self, "Save CSV", "metrics.csv", "CSV Files (*.csv)"
        )
        if p:
            try:
                with open(p, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["Time (s)", "Memory (MB)", "CPU (%)"])
                    for t, m, c in zip(self.time_data, self.mem_data, self.cpu_data):
                        w.writerow([t, m, c])
                self._log(f"CSV saved  ▸  {p}", "#00d4aa")
            except Exception as e:
                self._log(f"ERROR  ▸  {e}", "#ef4444")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Force UTF-8 for this process itself on Windows
    if sys.platform == "win32":
        import io
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8", errors="replace"
            )

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor("#0c0e18"))
    pal.setColor(QPalette.WindowText,      QColor("#dde1ec"))
    pal.setColor(QPalette.Base,            QColor("#07090f"))
    pal.setColor(QPalette.AlternateBase,   QColor("#13162a"))
    pal.setColor(QPalette.Text,            QColor("#dde1ec"))
    pal.setColor(QPalette.Button,          QColor("#181b2e"))
    pal.setColor(QPalette.ButtonText,      QColor("#dde1ec"))
    pal.setColor(QPalette.Highlight,       QColor("#00d4aa"))
    pal.setColor(QPalette.HighlightedText, QColor("#040e0c"))
    app.setPalette(pal)

    w = AnalyzerApp()
    w.show()
    sys.exit(app.exec())
