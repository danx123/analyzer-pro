import sys
import os
import subprocess
import psutil
import time
import tempfile
import csv
import pyqtgraph as pg

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QLineEdit, QTextEdit, 
                               QFileDialog, QLabel, QSplitter, QGroupBox)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QTextDocument, QIcon
from PySide6.QtPrintSupport import QPrinter

class ProcessMonitorThread(QThread):
    """Background thread to monitor performance and process tree."""
    stats_signal = Signal(dict)
    finished_signal = Signal(dict)
    log_signal = Signal(str)

    def __init__(self, script_path):
        super().__init__()
        self.script_path = script_path
        self.is_running = True
        self.proc = None
        self.tracked_pids = set()
        self.err_file = tempfile.NamedTemporaryFile(delete=False, mode='w+')

    def run(self):
        # Use temp file to capture stderr to prevent buffer deadlocks
        self.proc = subprocess.Popen([sys.executable, self.script_path], 
                                     stdout=subprocess.DEVNULL, 
                                     stderr=self.err_file)
        
        try:
            main_p = psutil.Process(self.proc.pid)
            self.tracked_pids.add(self.proc.pid)
        except psutil.NoSuchProcess:
            self.finished_signal.emit({"status": "Failed to start"})
            return

        start_time = time.time()
        
        while self.proc.poll() is None and self.is_running:
            try:
                children = main_p.children(recursive=True)
                current_pids = {p.pid for p in children}
                current_pids.add(main_p.pid)
                self.tracked_pids.update(current_pids) 

                # Aggregate RAM and CPU from parent + all children
                total_mem = main_p.memory_info().rss
                total_cpu = main_p.cpu_percent(interval=None)
                total_threads = main_p.num_threads()

                for child in children:
                    try:
                        total_mem += child.memory_info().rss
                        total_cpu += child.cpu_percent(interval=None)
                        total_threads += child.num_threads()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                elapsed = time.time() - start_time
                self.stats_signal.emit({
                    "time": round(elapsed, 2),
                    "mem_mb": total_mem / (1024 * 1024),
                    "cpu_percent": total_cpu,
                    "threads": total_threads,
                    "children": len(children)
                })
                time.sleep(0.5)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break 

        self.cleanup_and_report()

    def cleanup_and_report(self):
        time.sleep(1) # Allow OS some time to clean up
        zombies = []
        
        for pid in self.tracked_pids:
            if psutil.pid_exists(pid):
                try:
                    p = psutil.Process(pid)
                    if p.status() != psutil.STATUS_ZOMBIE:
                        zombies.append(f"PID {pid} ({p.name()})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        self.err_file.close()
        with open(self.err_file.name, 'r') as f:
            stderr_out = f.read()
        os.remove(self.err_file.name)

        self.finished_signal.emit({
            "zombies": zombies,
            "stderr": stderr_out,
            "exit_code": self.proc.returncode if self.proc else None
        })

    def stop_process(self):
        """Safely kill the process tree."""
        self.is_running = False
        if self.proc and self.proc.poll() is None:
            self.log_signal.emit("[SYSTEM] Sending KILL signal to process tree...")
            try:
                parent = psutil.Process(self.proc.pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
            except psutil.NoSuchProcess:
                pass


class AnalyzerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pro-Level Python Dynamic Analyzer")
        self.resize(1100, 750)
        icon_path = "analyzer.ico"
        if hasattr(sys, "_MEIPASS"):
            icon_path = os.path.join(sys._MEIPASS, icon_path)
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QLabel { color: #d4d4d4; }
            QLineEdit { background-color: #2d2d2d; color: #d4d4d4; border: 1px solid #3c3c3c; padding: 5px; }
            QPushButton { background-color: #0e639c; color: white; border: none; padding: 8px; border-radius: 4px; font-weight: bold;}
            QPushButton:hover { background-color: #1177bb; }
            QPushButton:disabled { background-color: #4d4d4d; color: #888888; }
            QTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3c3c3c; font-family: Consolas; }
            QGroupBox { color: #d4d4d4; font-weight: bold; border: 1px solid #3c3c3c; margin-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }
        """)
        
        self.time_data, self.mem_data, self.cpu_data = [], [], []
        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Top: Controls ---
        top_layout = QHBoxLayout()
        self.file_input = QLineEdit()
        self.file_input.setPlaceholderText("Select Python script (*.py)...")
        
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse)
        
        self.run_btn = QPushButton("▶ Run & Analyze")
        self.run_btn.setStyleSheet("background-color: #2ea043;")
        self.run_btn.clicked.connect(self.start_analysis)
        
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setStyleSheet("background-color: #d13b3b;")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_analysis)

        top_layout.addWidget(self.file_input)
        top_layout.addWidget(browse_btn)
        top_layout.addWidget(self.run_btn)
        top_layout.addWidget(self.stop_btn)
        main_layout.addLayout(top_layout)

        # --- Middle: Stats Dashboard ---
        self.status_label = QLabel("Status: IDLE | RAM: 0.00 MB | CPU: 0.0% | Threads: 0 | Children: 0")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #4fc1ff;")
        main_layout.addWidget(self.status_label)

        splitter = QSplitter(Qt.Vertical)
        
        # --- Graph Section (PyQtGraph) ---
        graph_group = QGroupBox("Real-time Performance Metrics")
        graph_layout = QVBoxLayout(graph_group)
        pg.setConfigOptions(antialias=True)
        self.graph_widget = pg.GraphicsLayoutWidget()
        
        self.plot_mem = self.graph_widget.addPlot(title="Memory Usage (MB)")
        self.plot_mem.showGrid(x=True, y=True, alpha=0.3)
        self.curve_mem = self.plot_mem.plot(pen=pg.mkPen('#c586c0', width=2), fillLevel=0, brush=(197, 134, 192, 50))
        
        self.graph_widget.nextRow()
        self.plot_cpu = self.graph_widget.addPlot(title="CPU Usage (%)")
        self.plot_cpu.showGrid(x=True, y=True, alpha=0.3)
        self.curve_cpu = self.plot_cpu.plot(pen=pg.mkPen('#4fc1ff', width=2), fillLevel=0, brush=(79, 193, 255, 50))
        
        graph_layout.addWidget(self.graph_widget)
        splitter.addWidget(graph_group)

        # --- Report Section ---
        report_group = QGroupBox("Analysis Log")
        report_layout = QVBoxLayout(report_group)
        self.report_area = QTextEdit()
        self.report_area.setReadOnly(True)
        report_layout.addWidget(self.report_area)

        # --- Export Controls ---
        export_layout = QHBoxLayout()
        self.export_pdf_btn = QPushButton("📄 Export Log to PDF")
        self.export_pdf_btn.clicked.connect(self.export_pdf)
        
        self.export_csv_btn = QPushButton("📊 Export Metrics to CSV")
        self.export_csv_btn.clicked.connect(self.export_csv)
        
        export_layout.addWidget(self.export_pdf_btn)
        export_layout.addWidget(self.export_csv_btn)
        report_layout.addLayout(export_layout)

        splitter.addWidget(report_group)
        main_layout.addWidget(splitter)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Script", "", "Python Files (*.py)")
        if path: self.file_input.setText(path)

    def append_log(self, text, color="#d4d4d4"):
        self.report_area.append(f"<span style='color:{color};'>{text}</span>")

    def start_analysis(self):
        path = self.file_input.text()
        if not path or not os.path.exists(path):
            self.append_log("[ERROR] File not found!", "#d13b3b")
            return

        # Reset UI & Data
        self.report_area.clear()
        self.time_data, self.mem_data, self.cpu_data = [], [], []
        self.curve_mem.setData([], [])
        self.curve_cpu.setData([], [])
        
        self.append_log(f"[START] Analyzing {os.path.basename(path)}...", "#2ea043")
        
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        # Start Thread
        self.monitor_thread = ProcessMonitorThread(path)
        self.monitor_thread.stats_signal.connect(self.update_stats)
        self.monitor_thread.finished_signal.connect(self.finish_analysis)
        self.monitor_thread.log_signal.connect(lambda txt: self.append_log(txt, "#dcdcaa"))
        self.monitor_thread.start()

    def update_stats(self, data):
        self.status_label.setText(f"Status: RUNNING | RAM: {data['mem_mb']:.2f} MB | CPU: {data['cpu_percent']}% | Threads: {data['threads']} | Children: {data['children']}")
        
        # Update graph data
        self.time_data.append(data['time'])
        self.mem_data.append(data['mem_mb'])
        self.cpu_data.append(data['cpu_percent'])
        
        self.curve_mem.setData(self.time_data, self.mem_data)
        self.curve_cpu.setData(self.time_data, self.cpu_data)

    def stop_analysis(self):
        if hasattr(self, 'monitor_thread') and self.monitor_thread.isRunning():
            self.monitor_thread.stop_process()
            self.stop_btn.setEnabled(False)

    def finish_analysis(self, result):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Status: FINISHED")
        
        self.append_log("<br><b>" + "="*20 + " ANALYSIS RESULTS " + "="*20 + "</b>", "#569cd6")
        
        # Check exit code
        exit_code = result.get('exit_code')
        if exit_code is not None:
            code_color = "#2ea043" if exit_code == 0 else "#d13b3b"
            self.append_log(f"Exit Code: {exit_code}", code_color)

        # Check Zombies
        zombies = result.get('zombies', [])
        if zombies:
            self.append_log("\n[!!!] HANGING PROCESSES (ZOMBIES/LEAKS) DETECTED:", "#d13b3b")
            for z in zombies:
                self.append_log(f"  >&nbsp; {z}", "#dcdcaa")
            self.append_log("Suggestion: Use sys.exit(), or ensure background threads are set to 'daemon=True'.", "#ce9178")
        else:
            self.append_log("\n[OK] Process cleaned up perfectly. No zombies detected.", "#2ea043")

        # Check Error from stderr
        stderr = result.get('stderr', '').strip()
        if stderr:
            self.append_log("\n[CRASH / WARNING LOG]:", "#d13b3b")
            formatted_err = stderr.replace('\n', '<br>')
            self.append_log(f"<span style='color:#ce9178;'>{formatted_err}</span>")
            
        self.append_log("<b>" + "="*58 + "</b>", "#569cd6")

    def export_pdf(self):
        if not self.report_area.toPlainText().strip():
            self.append_log("[WARNING] Nothing to export yet. Run an analysis first.", "#dcdcaa")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Save PDF Report", "analysis_log.pdf", "PDF Files (*.pdf)")
        if path:
            try:
                printer = QPrinter(QPrinter.HighResolution)
                printer.setOutputFormat(QPrinter.PdfFormat)
                printer.setOutputFileName(path)
                
                doc = QTextDocument()
                # Use a cleaner styling for the PDF output (dark text on white)
                html_content = self.report_area.toHtml().replace('color:#d4d4d4;', 'color:#000000;')
                doc.setHtml(html_content)
                doc.print_(printer)
                
                self.append_log(f"[SYSTEM] Log exported to PDF: {path}", "#2ea043")
            except Exception as e:
                self.append_log(f"[ERROR] Failed to export PDF: {str(e)}", "#d13b3b")

    def export_csv(self):
        if not self.time_data:
            self.append_log("[WARNING] No metrics data to export yet.", "#dcdcaa")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Save Raw Metrics", "metrics.csv", "CSV Files (*.csv)")
        if path:
            try:
                with open(path, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Time (s)", "Memory (MB)", "CPU (%)"])
                    for t, m, c in zip(self.time_data, self.mem_data, self.cpu_data):
                        writer.writerow([t, m, c])
                self.append_log(f"[SYSTEM] Raw metrics exported to CSV: {path}", "#2ea043")
            except Exception as e:
                self.append_log(f"[ERROR] Failed to export CSV: {str(e)}", "#d13b3b")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AnalyzerApp()
    window.show()
    sys.exit(app.exec())