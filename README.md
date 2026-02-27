# Analyzer Pro

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/PySide6-GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white"/>
  <img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=for-the-badge"/>  
</p>

<p align="center">
  A professional-grade, real-time Python script performance analyzer with a modern dark-themed GUI — built for developers who need deep visibility into runtime behavior.
</p>

---

## Overview

**Analyzer Pro** is a desktop application that dynamically profiles any Python script without requiring instrumentation or code modification. It monitors memory usage, CPU consumption, thread count, and child process activity in real time — all visualized through live interactive graphs.

Designed for developers, QA engineers, and DevOps professionals who need to understand, debug, and validate the runtime characteristics of Python programs.

---

## Key Features

| Feature | Description |
|---|---|
| **Real-time Performance Graphs** | Live memory (MB) and CPU (%) charts powered by PyQtGraph |
| **Process Tree Monitoring** | Tracks parent process and all spawned children recursively |
| **Zombie / Leak Detection** | Identifies hanging or leaked processes after script termination |
| **Crash Log Capture** | Captures and displays `stderr` output without buffer deadlocks |
| **PDF Export** | Export the full analysis log as a formatted PDF report |
| **CSV Export** | Export raw time-series metrics for external analysis |
| **Non-intrusive Profiling** | Zero modifications required to the target script |
| **Dark Theme UI** | Professional VS Code-inspired dark interface |

---

## Screenshots
<img width="1365" height="767" alt="image" src="https://github.com/user-attachments/assets/6b1cecfd-122d-4d35-8c54-a7aa98d8dae6" />



---

## Requirements

- Python **3.10** or higher
- The following packages (see [Installation](#installation)):

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

1. **Launch** the application via `python analyzer_pro.py`.
2. Click **Browse** to select any `.py` script you want to analyze.
3. Press **▶ Run & Analyze** to start profiling.
4. Monitor real-time **Memory** and **CPU** graphs as the script executes.
5. Review the **Analysis Log** panel for exit code, zombie process warnings, and crash/stderr output.
6. Use **⏹ Stop** at any time to safely terminate the entire process tree.
7. Export results:
   - **📄 Export Log to PDF** — saves a formatted analysis report
   - **📊 Export Metrics to CSV** — saves raw time-series data for further processing

---

## Architecture

```
analyzer_pro.py
├── ProcessMonitorThread (QThread)
│   ├── Spawns target script via subprocess.Popen
│   ├── Polls psutil for memory, CPU, threads, and child processes
│   ├── Emits stats_signal (live updates) and finished_signal (final report)
│   └── Handles safe process tree termination on stop
│
└── AnalyzerApp (QMainWindow)
    ├── File selector + run/stop controls
    ├── Real-time status bar
    ├── PyQtGraph live plots (Memory & CPU)
    ├── HTML-formatted analysis log (QTextEdit)
    └── PDF & CSV export handlers
```

**Design Decisions:**
- `stderr` is redirected to a `tempfile` to prevent pipe buffer deadlocks on long-running or verbose scripts.
- Process monitoring uses recursive `psutil.children()` to accurately aggregate metrics across multi-process workloads.
- The monitoring thread is decoupled from the UI thread via Qt Signals to ensure the GUI remains responsive at all times.

---

## Export Formats

### PDF Report (`analysis_log.pdf`)
Contains the full analysis log including start event, real-time log entries, exit code, zombie warnings, and stderr crash output — formatted for readability with light text on white background.

### CSV Metrics (`metrics.csv`)
Time-series data with the following columns:

| Column | Description |
|---|---|
| `Time (s)` | Elapsed time in seconds since script start |
| `Memory (MB)` | Aggregate RSS memory across parent + children |
| `CPU (%)` | Aggregate CPU usage across parent + children |

---

## Building a Standalone Executable

To distribute Analyzer Pro as a single executable (no Python installation required), use **PyInstaller**:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=analyzer.ico analyzer_pro.py
```

The output will be located in the `dist/` folder.

> **Note:** Place `analyzer.ico` in the same directory as `analyzer_pro.py` for the window icon to load correctly. When bundled with PyInstaller, the icon is automatically resolved via `sys._MEIPASS`.

---

## Known Limitations

- CPU percentage readings may show values above 100% on multi-core systems, as `psutil` reports per-core utilization.
- Scripts that suppress their own `stdout`/`stderr` or redirect file descriptors may affect log capture behavior.
- The zombie detection heuristic checks for process existence post-termination; very short-lived child processes may not be captured in all cases.

---

## Contributing

Contributions are welcome. Please follow these steps:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Commit your changes: `git commit -m 'feat: add your feature'`
4. Push to the branch: `git push origin feature/your-feature-name`
5. Open a Pull Request

Please ensure your code passes linting (`flake8`) and includes relevant documentation updates.

---

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.

---

## Acknowledgements

- [PySide6](https://doc.qt.io/qtforpython/) — Qt bindings for Python
- [psutil](https://github.com/giampaolo/psutil) — Cross-platform process and system utilities
- [PyQtGraph](https://www.pyqtgraph.org/) — High-performance scientific graphics library

---

<p align="center">Made with ❤️ by <a href="https://github.com/danx123">danx123</a></p>
