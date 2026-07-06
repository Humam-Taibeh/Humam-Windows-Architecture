# Humam Windows Architecture

**A modular, data-driven Windows deployment and optimization framework built in PowerShell.**

Designed for power users, IT technicians, and developers who need a repeatable, safe, and portable way to configure a fresh Windows install from a single USB drive.

---

## Overview

Humam Windows Architecture is a single-launch PowerShell engine with a luxury terminal UI, driven by:

- A `.bat` launcher with self-elevation and pre-flight validation
- A modular `.ps1` core covering deployment, optimization, repair, and privacy
- A **snapshot-before-modify** safety model — every reversible change is backed up before it's applied

Every module follows the same contract: **preview → confirm → snapshot → apply → log**.

---

## Features

| Module | Description |
|---|---|
| 📦 **Software Management** | Winget-driven app deployment with GitHub API bootstrap and manual-download fallback |
| ⚡ **System Optimization** | Data-driven registry tweaks (Dark Mode, Game Mode, Taskbar, Mouse, Context Menu) |
| 🔧 **Maintenance & Repair** | SFC/DISM automation with retry logic |
| 🛡️ **Privacy & Security** | Telemetry, Advertising ID, Activity History, bloatware removal |
| 📊 **Information & Utilities** | System dashboard, driver backup, missing-driver scan, session log |
| 🛟 **Safety & Recovery** | One-click rollback, full tweak reset, service restoration, restore points |

---

## Safety Model

- **System Restore Point** created automatically before the first system change of any session
- **Registry snapshots**: every tweak captures its original value before modification, restorable via *Reset All Tweaks*
- **Service snapshots**: startup type + running state captured before any service is disabled, restorable via *Restore All Services*
- **Session log**: every action is written to `Desktop\Humam Windows Architecture_Log.txt`

---

## Requirements

- Windows 10 or Windows 11
- PowerShell 5.1+
- Administrator privileges (handled automatically by the launcher)

---

## Usage

1. Clone or download this repository.
2. Keep `start.bat` and `core.ps1` in the same folder.
3. Run `start.bat` — it will self-elevate if needed.
4. Navigate the menu to deploy software or optimize the system.

```bash
git clone https://github.com/Humam-Taibeh/Humam-Windows-Architecture.git
cd Humam Windows Architecture
start.bat
```

---

## Architecture

```
Humam Windows Architecture/
├── start.bat                  # Elevation + launch wrapper
├── core.ps1    # Core engine
└── README.md
```

Tweaks, dev-tool dependencies, and app catalogs are designed to move toward a **data-driven model**: each item is defined as a single object (path, registry name, on/off values, description) processed by one generic engine function — instead of one function per tweak.

---

## Roadmap

- [x] Data-driven tweak engine (`Invoke-Tweak`)
- [ ] `Verify-Environment`: automatic PATH / env-var management for dev tools (Python, Java, NetBeans, Cursor)
- [ ] Non-blocking, parallel app installs
- [ ] `-WhatIf` dry-run mode
- [ ] JSON session report export
- [ ] Pester test coverage for the backup/restore subsystem

---

## Disclaimer

This tool modifies system registry keys, services, and installed software. While every reversible action is snapshotted, always ensure you have an independent backup or restore point before running on a production machine.

---

## Author

**Humam Taibeh**
