<div align="center">

# 🏛️ Humam Windows Architecture

**A dual-layer Windows deployment & optimization framework — a data-driven PowerShell core wrapped in a modern, glass-morphism PySide6 desktop app.**

[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6?logo=windows&logoColor=white)](#-requirements)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](#-requirements)
[![PowerShell](https://img.shields.io/badge/powershell-5.1%2B-5391FE?logo=powershell&logoColor=white)](#-requirements)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20(Qt%206)-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython-6/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/app-v5.1-blueviolet)](CHANGELOG.md)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

*Built for power users, IT technicians, and developers who need a repeatable, safe, and portable way to configure a fresh Windows install — from a single launcher.*

[Features](#-features) · [Quick Start](#-quick-start) · [Architecture](#-architecture) · [Safety Model](#-safety-model) · [Building](#-building-a-standalone-executable) · [Contributing](#-contributing)

</div>

---

## 📖 Overview

**Humam Windows Architecture** pairs two layers that share a single strict contract:

| Layer | Technology | Role |
|---|---|---|
| 🖥️ **Frontend** | Python · PySide6 (Qt 6) | Frameless glass UI, dual themes, 60 fps animations, live console, toast notifications |
| ⚙️ **Backend** | PowerShell 5.1+ | Data-driven engine for deployment, tweaks, repair, privacy, and recovery |

The GUI never touches the system itself. Every card in the interface dispatches a named task to `core.ps1` on a background `QThread`; the backend executes it and reports back over stdout with a machine-parseable `SUCCESS|…` or `ERROR|…` verdict. The core also runs **fully standalone** as a self-elevating terminal application with a hierarchical menu — no Python required.

Every module follows the same lifecycle:

> **preview → confirm → snapshot → apply → log**

---

## ✨ Features

### 📦 Software Management
- **Curated winget catalogs** — essential apps, developer & AI toolchain, gaming launchers, hardware diagnostics — with a per-app checkbox selector before anything installs
- **Core API runtimes** deployment (Visual C++, .NET, DirectX)
- **Startup report** — audit what launches with Windows

### ⚡ System Optimization
- **Data-driven tweak engine** — every tweak (Dark Mode, Mouse Acceleration, Minimalist Taskbar, Classic Context Menu, Game Mode) is a declarative catalog entry processed by one generic function, not bespoke code
- **Network & ping optimizer** and **Ultimate Power Plan** activation
- **Edge & OneDrive removal** with automatic pre-removal backups and one-click reinstall/restore

### 🔧 Maintenance & Repair
- **SFC + DISM automation** with in-place retry logic
- **Aggressive cache clean**, drive optimization, `Windows.old` removal
- Hibernation toggle and per-drive space reporting

### 🛡️ Privacy & Security
- Bloatware removal, telemetry shutdown, Advertising ID and Activity History disablement
- **One-click "Apply ALL Privacy Settings"** composite action

### 📊 Information & Utilities
- Live system dashboard (OS build, CPU, RAM — read via registry/kernel32, zero dependencies)
- **Driver backup** and missing-driver scan
- Full session operation log, viewable in-app

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
git clone https://github.com/Humam-Taibeh/Humam-Windows-Architecture.git
cd Humam-Windows-Architecture
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

`start.bat` validates the project layout, activates the virtual environment, and opens the GUI. Pick a category in the sidebar, click a card, confirm — progress streams into the live console and finishes with a toast notification.

### Terminal mode (no Python)

The PowerShell core is a complete application on its own:

```powershell
powershell -ExecutionPolicy Bypass -File src\backend\core.ps1
```

It self-elevates if needed and presents the full hierarchical menu directly in the terminal.

---

## 🏗️ Architecture

```
Humam-Windows-Architecture/
│
├── 📄 start.bat                 # Launcher — venv activation + GUI startup
├── 📄 requirements.txt          # Python dependencies (pinned)
├── 📄 main.spec                 # PyInstaller build recipe (one-file .exe)
│
├── 📁 src/
│   ├── 📁 backend/
│   │   └── core.ps1             # The engine — tweak catalog, task dispatcher,
│   │                            #   snapshot/restore subsystem, winget deployment
│   ├── 📁 frontend/
│   │   ├── main.py              # Orchestration only — pages, navigation, task pipeline
│   │   ├── menu_structure.py    # SINGLE SOURCE OF TRUTH for the menu hierarchy
│   │   ├── theme.py             # Dual-theme design tokens, QSS factories, DWM glass
│   │   ├── animations.py        # Glow, shimmer, cascade, page fade (60 fps doctrine)
│   │   └── widgets.py           # TitleBar, NavButton, GlassCard, ConfirmDialog, …
│   └── 📁 utils/
│       └── helpers.py           # PowerShellTask (QThread worker), ToastManager
│
├── 📁 assets/                   # Icons, images (packaged into the .exe)
│
├── 📄 README.md                 # You are here
├── 📄 CHANGELOG.md              # Release history (Keep a Changelog)
├── 📄 CONTRIBUTING.md           # Development workflow & standards
├── 📄 SECURITY.md               # Vulnerability reporting policy
└── 📄 LICENSE                   # MIT
```

### Design contracts

- **`menu_structure.py` is the single source of truth.** Adding a button to the app means adding *one dict* — `main.py` renders whatever is defined there, with zero UI code changes.
- **Every GUI task maps 1:1** to a `switch ($TaskName)` case in `core.ps1`'s `Invoke-GuiTask` dispatcher, which must emit exactly one final `SUCCESS|message` or `ERROR|message` line.
- **Thread safety is non-negotiable.** Qt widgets are touched only from the GUI thread; PowerShell runs on a `QThread` and reports back exclusively through Qt signals.
- **Tweaks are data, not code.** Each tweak declares its registry paths, on/off values, and description; one generic engine function applies, snapshots, and reverses all of them.

---

## 🔐 Safety Model

Every destructive path in this tool is guarded by four independent layers:

1. **🛟 System Restore Point** — created automatically before the first system change of any session, across *all* modules.
2. **📸 Registry snapshots** — every tweak captures its original value before modification. *Reset All Tweaks* restores your real prior settings, not Microsoft's defaults.
3. **⚙️ Service snapshots** — startup type + running state are captured before any service is disabled, restorable via *Restore All Services*.
4. **📜 Session log** — every action is appended to `Desktop\HTCoreArchitecture_Log.txt`, viewable from inside the app.

Additionally, removing Edge backs up its Preferences/Bookmarks/Favicons first, and removing OneDrive offers to back up your local OneDrive folder to the Desktop.

---

## 📦 Building a Standalone Executable

The repository ships a maintained PyInstaller spec that bundles the GUI *and* the PowerShell core into a single elevated, windowed `.exe`:

```bash
.venv\Scripts\activate
pip install pyinstaller
pyinstaller main.spec
```

The output lands in `dist\HumamArchitecture.exe` — portable, no Python required on the target machine, UAC elevation built in.

---

## 🗺️ Roadmap

- [x] Data-driven tweak engine (`Invoke-Tweak`)
- [x] PySide6 frontend with dual themes and live task console
- [x] One-file PyInstaller distribution
- [ ] `Verify-Environment`: automatic PATH / env-var management for dev tools
- [ ] Non-blocking, parallel app installs
- [ ] `-WhatIf` dry-run mode across all modules
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

*If this project saved you an afternoon of Windows setup, consider giving it a ⭐*

</div>
