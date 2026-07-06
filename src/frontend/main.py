"""
src/frontend/main.py

Humam Windows Architecture - GUI frontend (PySide6).

Key fixes vs. the previous version:
  1. THREAD SAFETY: task execution now runs on a QThread and reports back
     through Qt signals. The old code called self.big_icon.setText(...) etc.
     from inside a raw threading.Thread, which mutates Qt widgets off the
     GUI thread - undefined behaviour in Qt, and the real cause of most
     "works sometimes, glitches other times" symptoms.
  2. UI never freezes: the subprocess call happens entirely on the worker
     thread; the GUI thread only ever receives the final result.
  3. PowerShell window is fully hidden (STARTUPINFO + CREATE_NO_WINDOW),
     confirmed via src/utils/helpers.py::PowerShellTask.
  4. Silent executor: raw PowerShell stdout is never shown in the main
     area. Only the SUCCESS|... / ERROR|... status line drives the UI.
  5. Sidebar buttons are disabled while a task runs (prevents overlapping
     PowerShell processes / racing UI state), a "selected" glow marks the
     active task, and a pulsing icon + shimmering progress bar communicate
     "working" state.
"""
import os
import sys

from PySide6.QtCore import QPoint, Qt, QThread, QTimer
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

# Allow "from utils.helpers import ..." when running as src/frontend/main.py
_FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_FRONTEND_DIR)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from utils.helpers import HoverGlow, PowerShellTask, PulseAnimation, TaskResult, ToastManager  # noqa: E402

# ============================================================
#  CONFIG
# ============================================================
PS1_FILENAME = "core.ps1"
TASK_TIMEOUT_SECONDS = 900  # SFC + DISM can legitimately take 10+ minutes

TASKS = [
    ("🛡️  Telemetry", "DisableTelemetry"),
    ("🧹  Clean Cache", "CleanCache"),
    ("🔧  System Repair", "RunSFC"),
    ("📦  Remove Bloat", "RemoveBloatware"),
    ("💾  Optimize Drives", "OptimizeDrives"),
    ("🔄  Reset Tweaks", "ResetTweaks"),
]

SIDEBAR_BUTTON_QSS = """
QPushButton {
    background-color: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 12px;
    color: #ccd6f6;
    font-size: 14px;
    font-weight: 500;
    text-align: left;
    padding-left: 20px;
}
QPushButton:hover {
    background-color: rgba(0, 212, 255, 0.08);
    border: 1px solid rgba(0, 212, 255, 0.25);
    color: #ffffff;
}
QPushButton:pressed {
    background-color: rgba(0, 212, 255, 0.18);
}
QPushButton:disabled {
    color: #4a5568;
    background-color: rgba(255, 255, 255, 0.015);
    border: 1px solid rgba(255, 255, 255, 0.02);
}
QPushButton[selected="true"] {
    background-color: rgba(0, 212, 255, 0.14);
    border: 1px solid rgba(0, 212, 255, 0.55);
    color: #ffffff;
}
"""


# ============================================================
#  MAIN WINDOW (LUXURY GLASS)
# ============================================================
class HumamApp(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Humam Architecture")
        self.setGeometry(150, 100, 1100, 700)
        self.setMinimumSize(900, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("""
            QMainWindow {
                background-color: rgba(12, 15, 25, 0.92);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 30px;
            }
        """)

        # Keep strong references to running thread/worker + per-button glow
        # filters + toast manager, or Qt/Python will garbage-collect them
        # mid-flight and either crash or silently stop animating.
        self._thread: QThread | None = None
        self._worker: PowerShellTask | None = None
        self._glow_filters: list[HoverGlow] = []
        self._task_buttons: dict[str, QPushButton] = {}
        self._selected_task: str | None = None

        central = QWidget()
        central.setObjectName("central")
        central.setStyleSheet("#central { background: transparent; }")
        self.setCentralWidget(central)

        self.toasts = ToastManager(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        main_layout.addWidget(self._build_sidebar())
        main_layout.addWidget(self._build_content())

        # ============================================================
        #  ENGINE INIT
        # ============================================================
        self.ps1_path = self._locate_ps1()
        if not self.ps1_path:
            QTimer.singleShot(200, lambda: self.toasts.show(
                "error", f"{PS1_FILENAME} not found next to the app.", 8000))
            self._set_all_tasks_enabled(False)
        else:
            QTimer.singleShot(200, lambda: self.toasts.show(
                "success", "Engine loaded successfully.", 3000))

    # ============================================================
    #  UI BUILDERS
    # ============================================================
    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border-radius: 25px;
                border: 1px solid rgba(255, 255, 255, 0.04);
            }
        """)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(15, 30, 15, 30)
        layout.setSpacing(10)

        logo = QLabel("✦")
        logo.setStyleSheet("color: #00d4ff; font-size: 40px; font-weight: 300;")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)

        title = QLabel("HUMAM")
        title.setStyleSheet("color: white; font-size: 20px; font-weight: 600; letter-spacing: 3px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("ARCHITECTURE")
        subtitle.setStyleSheet("color: #8892b0; font-size: 11px; letter-spacing: 5px;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(25)
        layout.addWidget(subtitle)
        layout.addSpacing(30)

        for text, task in TASKS:
            btn = QPushButton(text)
            btn.setFixedHeight(45)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(SIDEBAR_BUTTON_QSS)
            btn.setProperty("selected", False)
            btn.clicked.connect(lambda checked=False, t=task: self.run_task(t))
            glow = HoverGlow(btn, color="#00d4ff", max_blur=20.0)
            btn.installEventFilter(glow)
            self._glow_filters.append(glow)
            self._task_buttons[task] = btn
            layout.addWidget(btn)

        layout.addStretch()

        exit_btn = QPushButton("✕  Exit")
        exit_btn.setFixedHeight(45)
        exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 70, 70, 0.08);
                border: 1px solid rgba(255, 70, 70, 0.15);
                border-radius: 12px;
                color: #ff6b6b;
                font-size: 14px;
                font-weight: 500;
            }
            QPushButton:hover { background-color: rgba(255, 70, 70, 0.2); }
        """)
        exit_btn.clicked.connect(self.close)
        layout.addWidget(exit_btn)

        return sidebar

    def _build_content(self) -> QFrame:
        content = QFrame()
        content.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.02);
                border-radius: 25px;
                border: 1px solid rgba(255, 255, 255, 0.04);
            }
        """)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        header = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #00ff88; font-size: 18px;")
        header.addWidget(self.status_dot)
        self.status_text = QLabel("System Ready")
        self.status_text.setStyleSheet("color: #ccd6f6; font-size: 18px; font-weight: 600;")
        header.addWidget(self.status_text)
        header.addStretch()
        layout.addLayout(header)

        self.display_frame = QFrame()
        self.display_frame.setStyleSheet("""
            QFrame {
                background: rgba(0, 0, 0, 0.2);
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 0.02);
            }
        """)
        display_layout = QVBoxLayout(self.display_frame)

        self.big_icon = QLabel("✦")
        self.big_icon.setStyleSheet("color: #00d4ff; font-size: 55px;")
        self.big_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        display_layout.addWidget(self.big_icon)
        self.pulse = PulseAnimation(self.big_icon)

        self.big_text = QLabel("Select a task from the left panel")
        self.big_text.setStyleSheet("color: #8892b0; font-size: 22px; font-weight: 300;")
        self.big_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        display_layout.addWidget(self.big_text)

        self.big_sub = QLabel("Optimize, repair, and secure your Windows system instantly")
        self.big_sub.setStyleSheet("color: #495670; font-size: 14px;")
        self.big_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        display_layout.addWidget(self.big_sub)

        layout.addWidget(self.display_frame)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setRange(0, 0)  # indeterminate; shimmer handled manually below
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self._shimmer_phase = 0.0
        self._shimmer_timer = QTimer(self)
        self._shimmer_timer.setInterval(40)
        self._shimmer_timer.timeout.connect(self._tick_shimmer)

        return content

    # ============================================================
    #  ENGINE PATH RESOLUTION
    # ============================================================
    def _locate_ps1(self) -> str | None:
        """
        Looks for core.ps1 in the layout documented for this project
        (src/backend/core.ps1 relative to src/frontend/main.py), then falls
        back to a couple of sensible alternatives so the app still works
        if the project is ever flattened or repackaged by PyInstaller.
        """
        candidates = [
            os.path.join(_SRC_DIR, "backend", PS1_FILENAME),
            os.path.join(_FRONTEND_DIR, PS1_FILENAME),
            os.path.join(os.path.dirname(_SRC_DIR), PS1_FILENAME),
        ]
        # PyInstaller onefile bundles extract to sys._MEIPASS at runtime.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.insert(0, os.path.join(meipass, "src", "backend", PS1_FILENAME))

        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    # ============================================================
    #  TASK EXECUTION (thread-safe)
    # ============================================================
    def run_task(self, task_name: str):
        if self._thread is not None and self._thread.isRunning():
            self.toasts.show("info", "A task is already running - please wait.", 3000)
            return
        if not self.ps1_path:
            self.toasts.show("error", f"{PS1_FILENAME} not found.", 4000)
            return

        self._set_selected(task_name)
        self._set_all_tasks_enabled(False)

        self.status_dot.setStyleSheet("color: #ffd700; font-size: 18px;")
        self.status_text.setText(f"Executing: {task_name} ...")
        self.big_icon.setText("⚡")
        self.big_text.setText(f"Running {task_name}...")
        self.big_sub.setText("Please wait, this may take a moment.")
        self.pulse.start()

        self.progress.setVisible(True)
        self._shimmer_timer.start()

        self.toasts.show("info", f"Starting {task_name}...", 3000)

        thread = QThread(self)
        worker = PowerShellTask(self.ps1_path, task_name, timeout=TASK_TIMEOUT_SECONDS)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_task_finished)
        worker.failed.connect(self._on_task_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_task_finished(self, result: TaskResult):
        if result.success:
            self.toasts.show("success", result.message, 5000)
            self.big_icon.setText("✅")
            self.big_text.setText("Completed Successfully!")
            self.big_sub.setText(result.message)
            self.status_dot.setStyleSheet("color: #00ff88; font-size: 18px;")
        else:
            self.toasts.show("error", result.message, 6000)
            self.big_icon.setText("❌")
            self.big_text.setText("Task Failed!")
            self.big_sub.setText(result.message)
            self.status_dot.setStyleSheet("color: #ff6b6b; font-size: 18px;")
        self._finish_common()

    def _on_task_failed(self, message: str):
        self.toasts.show("error", message, 6000)
        self.big_icon.setText("⏰" if "timed out" in message.lower() else "💥")
        self.big_text.setText("Task Failed!")
        self.big_sub.setText(message)
        self.status_dot.setStyleSheet("color: #ff6b6b; font-size: 18px;")
        self._finish_common()

    def _finish_common(self):
        self.pulse.stop()
        self.progress.setVisible(False)
        self._shimmer_timer.stop()
        self.status_text.setText("System Ready")
        self._set_all_tasks_enabled(True)

    def _on_thread_finished(self):
        # Threads/workers are cleaned up here rather than deleteLater'd
        # immediately in the signal handlers above, so Qt doesn't destroy
        # them while a queued signal to this same slot is still pending.
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    # ============================================================
    #  SIDEBAR STATE HELPERS
    # ============================================================
    def _set_selected(self, task_name: str):
        if self._selected_task and self._selected_task in self._task_buttons:
            prev = self._task_buttons[self._selected_task]
            prev.setProperty("selected", False)
            prev.style().unpolish(prev)
            prev.style().polish(prev)

        self._selected_task = task_name
        btn = self._task_buttons.get(task_name)
        if btn:
            btn.setProperty("selected", True)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _set_all_tasks_enabled(self, enabled: bool):
        for btn in self._task_buttons.values():
            btn.setEnabled(enabled)

    # ============================================================
    #  PROGRESS BAR SHIMMER
    # ============================================================
    def _tick_shimmer(self):
        self._shimmer_phase = (self._shimmer_phase + 0.015) % 1.0
        stop1 = self._shimmer_phase
        stop2 = min(1.0, stop1 + 0.45)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255, 255, 255, 0.05);
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:{stop1:.3f}, y1:0, x2:{stop2:.3f}, y2:0,
                    stop:0 #00d4ff, stop:1 #7b61ff);
                border-radius: 4px;
            }}
        """)

    # ============================================================
    #  KEEP TOASTS DOCKED TO THE TOP-RIGHT ON RESIZE
    # ============================================================
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toasts.reposition()


# ============================================================
#  ENTRY POINT
# ============================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = HumamApp()
    window.show()
    sys.exit(app.exec())