# Analyzer Pro

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/PySide6-GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white"/>
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge"/>  
</p>

<p align="center">
  A professional-grade, real-time Python script performance analyzer with a modern dark-themed GUI —<br/>
  built for developers who need deep visibility into runtime behavior, without touching target code.
</p>

---

## Overview

**Analyzer Pro** is a standalone desktop application that dynamically profiles any Python script or multi-module project without requiring instrumentation or code modification. It monitors memory usage, CPU consumption, thread count, and child process activity in real time, visualized through side-by-side live performance graphs.

Designed for developers, QA engineers, and DevOps professionals who need to understand, debug, and validate the runtime characteristics of Python programs — including complex projects with nested packages, subprocess trees, and third-party dependencies.

---

## Key Features

| Feature | Description |
|---|---|
| **Full Project Support** | Automatically discovers and injects all sub-packages into `PYTHONPATH` — no manual configuration required for multi-module projects |
| **Unicode / Emoji Safety** | Forces UTF-8 I/O encoding on the target process, preventing `UnicodeEncodeError` crashes on Windows (`cp1252`) |
| **Live stdout Streaming** | Real-time output displayed as the process runs, not buffered until completion |
| **Side-by-side Performance Graphs** | Memory (MB) and CPU (%) plotted simultaneously in a compact horizontal layout via PyQtGraph |
| **5-Metric Status Dashboard** | Flat stat badges for Status, Memory, CPU, Threads, and Children — always visible at a glance |
| **Process Tree Monitoring** | Recursively tracks parent and all spawned child processes via `psutil` |
| **Zombie / Leak Detection** | Identifies hanging or leaked processes after script termination with actionable suggestions |
| **Configurable Working Directory** | Set a custom CWD independent of the script path — essential for projects where the entry-point is in a subdirectory |
| **Script Arguments Support** | Pass CLI arguments to the target script directly from the UI |
| **Extra PYTHONPATH Injection** | Manually append additional directories to `PYTHONPATH` alongside auto-discovery |
| **Crash Log Capture** | Captures and streams `stderr` output live, color-coded in the System Log panel |
| **PDF & CSV Export** | Export the analysis log as a PDF report or raw metrics as a time-series CSV |
| **Non-intrusive Profiling** | Zero modifications required to the target script or project |

---

## Screenshots
<img width="1365" height="767" alt="Screenshot 2026-02-28 025214" src="https://github.com/user-attachments/assets/53be0903-394c-4d98-bd5a-15cfb8e3d770" />



---

## Requirements

- Python **3.10** or higher
- Dependencies:

```
PySide6
psutil
pyqtgraph
```

---

## Installation

**1. Clone the repository**

```bash
git clone https://github.com/danx123/analyzer-pro.git
cd analyzer-pro
```

**2. Create a virtual environment (recommended)**

```bash
python -m venv venv
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows
```

**3. Install dependencies**

```bash
pip install PySide6 psutil pyqtgraph
```

**4. Run the application**

```bash
python analyzer_pro.py
```

---

## Usage

### Basic Script

1. **Launch** the application via `python analyzer_pro.py`.
2. Click **Browse** next to the *Script* field and select your `.py` entry-point.
3. Press **▶ Run & Analyze** to start profiling.
4. Monitor the **Memory** and **CPU** graphs in real time as the script executes.
5. Review the **Program Output** panel for live `stdout` and the **System Log** panel for `stderr` / events.
6. Use **■ Stop** at any time to safely terminate the entire process tree.
7. Export results via **Export PDF** or **Export CSV**.

### Multi-module Project

For projects with local packages, submodules, or complex import trees:

1. Set the *Script* field to your entry-point (e.g. `src/main.py`).
2. Uncheck **Auto** next to *Working Dir* and set it to your **project root** (e.g. `D:/MyProject`).
3. Analyzer Pro will automatically walk the project directory and inject all discovered Python source directories into `PYTHONPATH`.
4. Optionally, add any extra directories via the **+** button next to *Extra PYTHONPATH* if your project has dependencies outside the project root.
5. Pass any required CLI arguments in the *Arguments* field (e.g. `--config config.yaml --port 8080`).

---

## Architecture

```
analyzer_pro.py
├── ProcessMonitorThread (QThread)
│   ├── Builds UTF-8 environment (PYTHONUTF8, PYTHONIOENCODING)
│   ├── Auto-discovers PYTHONPATH via os.walk() on project root
│   ├── Spawns target script via subprocess.Popen (unbuffered, UTF-8 pipes)
│   ├── Streams stdout / stderr live via daemon reader threads + queue
│   ├── Polls psutil for memory, CPU, threads, and child processes (0.5s interval)
│   ├── Emits: stats_signal, stdout_signal, stderr_signal, finished_signal, log_signal
│   └── Handles safe recursive process tree termination on stop
│
├── AccentBar (QFrame)
│   └── Gradient header bar rendered via QPainter
│
├── StatBadge (QFrame)
│   └── Compact horizontal label+value badge with per-metric accent colors
│
└── AnalyzerApp (QMainWindow)
    ├── Configuration panel (script, working dir, extra paths, arguments)
    ├── Action controls (Run, Stop, Clear)
    ├── 5-metric stat dashboard (Status, Memory, CPU, Threads, Children)
    ├── Side-by-side PyQtGraph live plots (Memory MB | CPU %)
    ├── Split output panes (stdout | stderr/system log)
    └── PDF & CSV export handlers
```

**Key Design Decisions:**

- **Pipe-based streaming over tempfiles** — stdout and stderr are read via daemon threads feeding a `queue.Queue`, enabling live output with no buffer deadlocks, even on long-running or verbose processes.
- **`-u` flag (unbuffered)** — the target script is launched with `python -u` to force line-by-line output flushing without requiring the target code to call `flush()`.
- **UTF-8 enforcement** — `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, and `errors="replace"` on pipe decoding ensure emoji and non-ASCII characters in target script output never crash the analyzer on Windows.
- **Recursive PYTHONPATH discovery** — `os.walk()` on the project CWD injects every subdirectory containing `.py` files, resolving relative imports in multi-package projects automatically.
- **Qt Signal/Slot decoupling** — all cross-thread communication uses typed Qt Signals, keeping the UI thread fully responsive regardless of target script behavior.

---

## Export Formats

### PDF Report (`analysis_log.pdf`)

Contains the full System Log including session start metadata, CWD, PYTHONPATH, live event entries, exit code, zombie process warnings, and crash output.

### CSV Metrics (`metrics.csv`)

Time-series data sampled at 0.5-second intervals:

| Column | Description |
|---|---|
| `Time (s)` | Elapsed time in seconds since script start |
| `Memory (MB)` | Aggregate RSS memory across parent + all children |
| `CPU (%)` | Aggregate CPU usage across parent + all children |

---

## Building a Standalone Executable

To distribute Analyzer Pro as a single executable with no Python installation required:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=analyzer.ico analyzer_pro.py
```

Output will be located in the `dist/` folder.

> **Note:** Place `analyzer.ico` in the same directory as `analyzer_pro.py` for the window icon to load correctly. When bundled, the icon path is resolved automatically via `sys._MEIPASS`.

---

## Known Limitations

- CPU percentage may exceed 100% on multi-core systems, as `psutil` reports aggregate per-core utilization summed across all cores.
- Scripts that explicitly redirect or suppress their own file descriptors may affect live output capture.
- Zombie detection checks for process existence after a short grace period; very short-lived child processes spawned near termination may not always be captured.
- GUI responsiveness may degrade slightly if the target script produces extremely high-frequency stdout output (thousands of lines per second).

---

## Contributing

Contributions, bug reports, and feature requests are welcome.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Commit using [Conventional Commits](https://www.conventionalcommits.org/): `git commit -m 'feat: add your feature'`
4. Push to the branch: `git push origin feature/your-feature-name`
5. Open a Pull Request with a clear description of the change and its motivation

Please ensure code passes linting (`flake8`) and that any UI changes are tested on Windows and Linux.

---

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.

---

## Acknowledgements

- [PySide6](https://doc.qt.io/qtforpython/) — Qt6 bindings for Python
- [psutil](https://github.com/giampaolo/psutil) — Cross-platform process and system utilities
- [PyQtGraph](https://www.pyqtgraph.org/) — High-performance real-time scientific graphics

---

<p align="center">Made with ❤️ by <a href="https://github.com/danx123">danx123</a></p>
