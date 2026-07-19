# Contributing to Pulse

Thank you for considering a contribution! This document explains the development workflow, the coding standards, and — most importantly — the **architectural contracts** that every change must preserve.

---

## 🧭 Table of Contents

- [Getting Started](#-getting-started)
- [Project Layout](#-project-layout)
- [Architectural Contracts](#-architectural-contracts)
- [Coding Standards](#-coding-standards)
- [Commit Conventions](#-commit-conventions)
- [Pull Request Process](#-pull-request-process)
- [Reporting Bugs](#-reporting-bugs)

---

## 🚀 Getting Started

1. **Fork** the repository and clone your fork:

   ```bash
   git clone https://github.com/<your-username>/Pulse.git
   cd Pulse
   ```

2. **Create the environment:**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements-dev.txt
   ```

3. **Run the app** to confirm your setup works before changing anything:

   ```bash
   start.bat
   ```

4. **Create a topic branch** off `master`:

   ```bash
   git checkout -b feat/short-description
   ```

---

## 🗂️ Project Layout

| Path | Responsibility |
|---|---|
| `src/backend/core.ps1` | The entire system-touching engine (PowerShell) |
| `src/frontend/main.py` | Orchestration **only** — pages, navigation, task pipeline |
| `src/frontend/menu_structure.py` | Single source of truth for the menu hierarchy |
| `src/frontend/theme.py` | Design tokens, QSS factories, DWM glass |
| `src/frontend/animations.py` | Motion system (glow, shimmer, cascade, fade) |
| `src/frontend/widgets.py` | Reusable components (TitleBar, GlassCard, dialogs, …) |
| `src/utils/helpers.py` | `PowerShellTask` QThread worker, `ToastManager` |

---

## 📜 Architectural Contracts

These are **non-negotiable**. Pull requests that violate them will be asked to revise.

### 1 · The GUI never touches the system
All registry, service, file-system, and package operations live in `core.ps1`. The Python layer only dispatches tasks and renders results.

### 2 · Task protocol
Every GUI task maps **1:1** to a `switch ($TaskName)` case in `core.ps1`'s `Invoke-GuiTask` dispatcher. Each task must emit **exactly one** final verdict line on stdout:

```
SUCCESS|<human-readable message>
ERROR|<human-readable message>
```

Tasks prefixed with `@` (e.g. `@open_log`) are handled locally by the GUI and never spawn PowerShell.

### 3 · Adding a feature = adding data
- **New GUI card** → add one dict to `menu_structure.py` and one matching case to `Invoke-GuiTask`. No changes to `main.py`.
- **New tweak** → add one declarative entry to `$Script:TweakCatalog` in `core.ps1` (path, name, on/off values, type). Never write a bespoke per-tweak function.
- **App catalogs** — the `apps` list in `menu_structure.py` must mirror the corresponding `$Apps_*` array in `core.ps1` exactly (same IDs, same order). The backend is the source of truth.

### 4 · Thread safety
Qt widgets are touched **only** from the GUI thread. Background work runs on a `QThread` and communicates exclusively through Qt signals. Never mutate a widget from a raw `threading.Thread`.

### 5 · Snapshot before modify
Any new reversible system change **must** capture the original state (registry value, service startup type, file backup) before applying, and hook into the existing *Reset All Tweaks* / *Restore All Services* recovery paths.

### 6 · Non-interactive safety
`core.ps1` sets `$Script:NonInteractive` when invoked with `-Task`. Any new prompt, retry loop, or fallback URL **must** respect this flag — GUI-dispatched runs have no console to block on.

---

## 🎨 Coding Standards

### Python
- Target **Python 3.10+**; keep `from __future__ import annotations` at the top of modules.
- Follow the existing module docstring style — each file opens with its path and a summary of its role.
- No new third-party dependencies without prior discussion in an issue.
- No `QGraphicsEffect` in steady state and no `setStyleSheet()` inside timers — see the performance doctrine in `animations.py`.

### PowerShell
- Target **PowerShell 5.1** (`#Requires -Version 5.1`) — no PowerShell 7-only syntax.
- `$ErrorActionPreference = "Stop"` is global; wrap expected failures in `try/catch`.
- Prefer data-driven catalogs over imperative branches.

---

## ✍️ Commit Conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <imperative summary>
```

| Type | Use for |
|---|---|
| `feat` | New user-facing capability |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf` | Performance improvement |
| `chore` | Tooling, build, or repo maintenance |

Examples:

```
feat(tweaks): add taskbar end-task toggle to the tweak catalog
fix(gui): prevent double task dispatch when a card is clicked twice
```

---

## 🔀 Pull Request Process

1. Keep PRs **focused** — one logical change per PR.
2. Update `CHANGELOG.md` under the `[Unreleased]` heading.
3. Update `README.md` if the change affects features, usage, or architecture.
4. **Test on a real or virtual Windows machine** — describe your test environment (Windows version/build, terminal vs GUI mode) in the PR description.
5. For anything that modifies system state, confirm the snapshot/restore path works: apply → *Reset All Tweaks* → verify original state returns.
6. Fill in the PR template completely.

---

## 🐛 Reporting Bugs

Open an issue using the **Bug Report** template. Always include:

- Windows version and build number (`winver`)
- Whether you ran **GUI mode** or **terminal mode**
- The relevant portion of `%LOCALAPPDATA%\Pulse\logs\Pulse_Log.txt`
- Steps to reproduce

**Security vulnerabilities must not be reported publicly** — see [SECURITY.md](SECURITY.md).

---

*Thank you for helping make Windows setup less painful for everyone.* 🏛️
