[⚡ Back to Main Profile](https://github.com/Humam-Taibeh)

<div align="center">

# ⚡ Pulse

**Enterprise-grade Windows orchestration — a data-driven PowerShell engine wrapped in a modern, glass-morphism PySide6 command center, with a real-time operations console and a global kill switch.**

[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6?logo=windows&logoColor=white)](#-requirements)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](#-requirements)
[![PowerShell](https://img.shields.io/badge/powershell-5.1%2B-5391FE?logo=powershell&logoColor=white)](#-requirements)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20(Qt%206)-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython-6/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/app-v6.0-blueviolet)](CHANGELOG.md)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

*Built for power users, IT technicians, and developers who need a repeatable, safe, and portable way to configure a fresh Windows install — from a single launcher.*

[Features](#-features) · [Live Console](#-the-live-operations-console) · [Quick Start](#-quick-start) · [Architecture](#-architecture) · [Safety Model](#-safety-model) · [Building](#-building-a-standalone-executable) · [Contributing](#-contributing)

</div>

---

## 📖 Overview

**Pulse** pairs two layers that share a single strict contract:

| Layer | Technology | Role |
|---|---|---|
| 🖥️ **Frontend** | Python · PySide6 (Qt 6) | Frameless glass UI, dual themes, 60 fps animations, real-time console, global kill switch, toast notifications |
| ⚙️ **Backend** | PowerShell 5.1+ | Data-driven engine for deployment, tweaks, repair, privacy, and recovery |

The GUI never touches the system itself. Every card in the interface dispatches a named task to `core.ps1` on a background `QThread`; the backend executes it, **streams its progress to the UI in real time**, and closes with a machine-parseable `SUCCESS|…` or `ERROR|…` verdict. The core also runs **fully standalone** as a self-elevating terminal application with a hierarchical menu — no Python required.

Every module follows the same lifecycle:

> **preview → confirm → snapshot → apply → log**

This project is built through **Advanced GenAI System Orchestration**: every module boundary, thread-safety contract, and rendering rule below was specified through detailed architectural prompting, then implemented, audited, and iterated on with AI coding assistants inside VS Code. The architecture discipline — module decomposition, concurrency contracts, event-loop boundaries — is mine; the code generation is delegated and rigorously reviewed. The strict orchestration contract between the PySide6 (Qt 6) UI event loop and the 13 isolated PowerShell core modules — one dispatch call in, one `SUCCESS`/`ERROR` verdict out, no shared state — is what keeps the two layers decoupled and independently testable.

---

## 🖥️ The Live Operations Console

The execution engine is built for **observability and control**, not fire-and-forget:

- **⏱️ True real-time streaming** — the worker reads the PowerShell pipe in binary chunks with an incremental UTF-8 decoder, so output appears the instant the backend writes it. Every task opens with a timestamped start banner within the first second.
- **📈 In-place progress** — bare carriage-return rewrites (the progress idiom of `sfc`, `DISM`, and `winget`) update a **single console line**, exactly like a real terminal, instead of flooding the log with thousands of percentage lines.
- **🛑 Global kill switch** — a danger-styled **■ Stop Task** button appears while a task runs. One click hard-terminates the entire process tree (`taskkill /T /F` — PowerShell *and* its winget/sfc/DISM children), reports a distinct *stopped* outcome (never a fake error), and returns the UI to ready in about a second.
- **🚦 Execution state pill** — a compact IDLE / RUNNING / SUCCESS / ERROR / STOPPED chip beside the console header mirrors the engine state at a glance.
- **✅ Verdict feedback** — the card that launched the task glows while running, then flashes green or red with the result; glass toasts carry the human-readable verdict.

---

## ✨ Features

### 📦 Software Management
- **Curated winget catalogs** — essential apps, developer & AI toolchain, gaming launchers, hardware diagnostics — with a per-app checkbox selector before anything installs
- **Core API runtimes** deployment (Visual C++, .NET, DirectX)
- **Startup report** — audit what launches with Windows

### ⚡ System Optimization
- **Data-driven tweak engine** — every tweak (Dark Mode, Mouse Acceleration, Minimalist Taskbar, Classic Context Menu, Game Mode) is a declarative catalog entry processed by one generic function, not bespoke code
- **Network & ping optimizer** and **Pulse Power Plan** activation (unlocks the hidden Ultimate Performance scheme)
- **Edge & OneDrive removal** with automatic pre-removal backups and one-click reinstall/restore

### 🔧 Maintenance & Repair
- **SFC + DISM automation** with in-place retry logic and live scan progress
- **Aggressive cache clean**, drive optimization, `Windows.old` removal
- Hibernation toggle and per-drive space reporting

### 🛡️ Privacy & Security
- Bloatware removal, telemetry shutdown, Advertising ID and Activity History disablement
- **One-click "Apply ALL Privacy Settings"** composite action

### 📊 Information & Utilities
- Live system dashboard (OS build, CPU, RAM — read via registry/kernel32, zero dependencies)
- **Driver backup** and missing-driver scan
- Full session operation log (`Desktop\Pulse_Log.txt`), viewable in-app

### 🛟 Safety & Recovery
- **Reset All Tweaks** — restores your *actual* prior values, not factory defaults
- **Restore All Services** — puts every touched service back exactly as found
- Restore-point creation and Edge/OneDrive backup recovery

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10 / 11 | 64-bit |
| PowerShell 5.1+ | Ships with Windows |
| Python 3.10+ | GUI mode only |
| Administrator rights | Requested automatically at launch |

### 1 · Clone

```bash
git clone https://github.com/Humam-Taibeh/Pulse.git
cd Pulse
```

### 2 · Set up the environment (GUI mode)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3 · Launch

```bash
start.bat
```

`start.bat` validates the project layout, activates the virtual environment, and opens the GUI. Pick a category in the sidebar, click a card, confirm — progress streams into the live console in real time, and **■ Stop Task** is one click away the whole time.

### Terminal mode (no Python)

The PowerShell core is a complete application on its own:

```powershell
powershell -ExecutionPolicy Bypass -File src\backend\core.ps1
```

It self-elevates if needed and presents the full hierarchical menu directly in the terminal. Add `-WhatIf` for a full dry-run simulation that changes nothing.

---

## 🏗️ Architecture

```
Pulse/
│
├── 📄 start.bat                 # Launcher — venv activation + GUI startup
├── 📄 requirements.txt          # Python dependencies (pinned)
├── 📄 main.spec                 # PyInstaller build recipe (one-file Pulse.exe)
│
├── 📁 src/
│   ├── 📁 backend/
│   │   ├── core.ps1             # Thin orchestrator — params, elevation, module loader
│   │   └── 📁 modules/          # 13 single-responsibility engine modules
│   │       ├── 00-Foundation.ps1     # logging, console vocabulary, dry-run primitives
│   │       ├── 01-Catalogs.ps1       # ALL data: tweaks, app catalogs, services
│   │       ├── 02-Safety.ps1         # restore points, snapshots, backups, rollback
│   │       ├── …                     # environment, software engine, startup,
│   │       │                         #   tweaks, maintenance, privacy, sysinfo
│   │       ├── 20-Menus.ps1          # the full interactive terminal experience
│   │       └── 30-GuiDispatcher.ps1  # Invoke-GuiTask — the GUI task contract
│   ├── 📁 frontend/
│   │   ├── main.py              # Orchestration only — pages, navigation, task pipeline
│   │   ├── menu_structure.py    # SINGLE SOURCE OF TRUTH for the menu hierarchy
│   │   ├── theme.py             # Dual-theme design tokens, QSS factories, DWM glass
│   │   ├── animations.py        # Glow, shimmer, cascade, page fade (60 fps doctrine)
│   │   └── widgets.py           # TitleBar, GlassCard, LiveConsole, StatePill, …
│   └── 📁 utils/
│       └── helpers.py           # PowerShellTask engine — streaming reader + kill switch
│
├── 📄 README.md                 # You are here
├── 📄 CHANGELOG.md              # Release history (Keep a Changelog)
├── 📄 CONTRIBUTING.md           # Development workflow & standards
├── 📄 SECURITY.md               # Vulnerability reporting policy
└── 📄 LICENSE                   # MIT
```

### Design contracts

- **`menu_structure.py` is the single source of truth.** Adding a button to the app means adding *one dict* — `main.py` renders whatever is defined there, with zero UI code changes.
- **Every GUI task maps 1:1** to a `switch ($TaskName)` case in the `Invoke-GuiTask` dispatcher, which must emit exactly one final `SUCCESS|message` or `ERROR|message` line.
- **Thread safety is non-negotiable.** Qt widgets are touched only from the GUI thread; PowerShell runs on a `QThread` and reports back exclusively through Qt signals.
- **One terminal signal per task.** The worker emits exactly one of `finished` / `failed` / `cancelled` — the kill switch and the timeout watchdog only terminate the process; the read loop owns the verdict, so the UI can never receive conflicting outcomes.
- **Tweaks are data, not code.** Each tweak declares its registry paths, on/off values, and description; one generic engine function applies, snapshots, and reverses all of them.

---

## 🔐 Safety Model

Every destructive path in this tool is guarded by four independent layers:

1. **🛟 System Restore Point** — `Pulse Restore Point`, created automatically before the first system change of any session, across *all* modules.
2. **📸 Registry snapshots** — every tweak captures its original value (under `HKCU:\Software\Pulse`) before modification. *Reset All Tweaks* restores your real prior settings, not Microsoft's defaults.
3. **⚙️ Service snapshots** — startup type + running state are captured before any service is disabled, restorable via *Restore All Services*.
4. **📜 Session log** — every action is appended to `Desktop\Pulse_Log.txt`, viewable from inside the app.

Additionally, removing Edge backs up its Preferences/Bookmarks/Favicons first, and removing OneDrive offers to back up your local OneDrive folder to the Desktop.

**Kill-switch semantics:** stopping a task is a *hard* process-tree termination — deliberate, immediate, and honest. Interrupted work (a half-finished scan, a partial install batch) is left incomplete but recoverable: simply re-run the task. Nothing bypasses the snapshot layers above.

**Upgrading from v5.x** (*Humam Windows Architecture*): Pulse migrates your existing safety net automatically — the legacy registry snapshots are copied to the new `HKCU:\Software\Pulse` root on first run, and restores/log viewers fall back to the old Desktop artifact names (`HTCore_*`) when the new ones don't exist yet.

---

## 📦 Building a Standalone Executable

The repository ships a maintained PyInstaller spec that bundles the GUI *and* the PowerShell core into a single elevated, windowed `.exe`:

```bash
.venv\Scripts\activate
pip install pyinstaller
pyinstaller main.spec
```

The output lands in `dist\Pulse.exe` — portable, no Python required on the target machine, UAC elevation built in.

---

## 🗺️ Roadmap

- [x] Data-driven tweak engine (`Invoke-Tweak`)
- [x] PySide6 frontend with dual themes and live task console
- [x] One-file PyInstaller distribution
- [x] `Verify-Environment`: automatic PATH / env-var management for dev tools
- [x] `-WhatIf` dry-run mode across all modules
- [x] Real-time streaming console with in-place progress and a global kill switch
- [ ] Non-blocking, parallel app installs
- [ ] JSON session report export
- [ ] Pester test coverage for the backup/restore subsystem

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, commit conventions, and the architectural contracts you must preserve. Security issues should go through [SECURITY.md](SECURITY.md) instead of the public tracker.

---

## ⚠️ Disclaimer

This tool modifies registry keys, services, and installed software. While every reversible action is snapshotted and a restore point is created automatically, **always ensure you have an independent backup before running on a production machine.** The software is provided *as is*, without warranty of any kind — see [LICENSE](LICENSE).

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for full text.

---

<div align="center">

**Crafted with precision by [Humam Taibeh](https://github.com/Humam-Taibeh)**

*If Pulse saved you an afternoon of Windows setup, consider giving it a ⭐*

</div>

---

[⚡ Back to Main Profile](https://github.com/Humam-Taibeh)
