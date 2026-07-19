# Pulse — Roadmap for Excellence

The path from system utility to industry-standard orchestration tool, in three
deliberate phases. Guiding principle: **quality over quantity** — every item
here must add real value to the daily workflow of IT technicians and power
users, or it doesn't ship.

Phases build on each other: v6.1 buys trust, v6.5 buys resilience and polish,
v7.0 changes what Pulse *is*.

---

## Phase 1 · v6.1 — Trust & Hardening

*Goal: a build the world can trust — no false-positive triggers, no fragile
contracts, no support tickets from slow OneDrive-synced logs.*

### Code (shipped in 6.1.0)
- [x] **UPX disabled** in the PyInstaller recipe — packed executables are a
  classic antivirus false-positive heuristic an elevated system tool cannot
  afford.
- [x] **Sentinel verdict contract** — the backend's final verdict line is now
  `##PULSE##SUCCESS|…` / `##PULSE##ERROR|…`, scanned backwards by the GUI, so
  stray trailing output from any module or external tool can never shadow or
  spoof the result. Legacy bare `SUCCESS|`/`ERROR|` lines still parse
  (fallback for pre-6.1 backends).
- [x] **Log relocation + rotation** — the session log moved from the Desktop
  (frequently OneDrive-synced → per-line sync traffic, unbounded growth) to
  `%LOCALAPPDATA%\Pulse\logs\Pulse_Log.txt`, rotated at 5 MB with the five
  newest archives kept. Existing Desktop logs migrate automatically; the
  in-app log viewer falls back to legacy locations.

### Release process (operational follow-ups)
- [ ] **Code signing** via Azure Trusted Signing (~$10/month, individual
  enrollment, near-immediate SmartScreen reputation). Sign `Pulse.exe` *and*
  the `.ps1` modules (`Set-AuthenticodeSignature`) so `AllSigned` execution
  policies can run the engine.
- [ ] **One-dir + signed installer** (Inno Setup or MSIX) as the primary
  distribution channel; portable ZIP of the one-dir build as secondary.
  One-file self-extraction to `%TEMP%` is slower and another AV heuristic.
- [ ] **CI release builds** (GitHub Actions) with published `SHA256SUMS`,
  pre-release VirusTotal scan, and proactive submission to Microsoft's
  false-positive portal.

---

## Phase 2 · v6.5 — Resilience & Native Feel

*Goal: an engine that cannot leak processes, and a UI that reads as native
Windows rather than a themed app.*

### Engine resilience
- [ ] **Job Object kill guarantee** — assign the PowerShell child to a Windows
  Job Object with `KILL_ON_JOB_CLOSE` at spawn (ctypes). Closes the
  reparenting gap where a detached grandchild (msiexec hand-off, winget's
  elevation broker) survives `taskkill /T` and holds the stdout pipe open.
  `taskkill` stays as fallback.
- [ ] **Test suite promoted to `tests/`** — the v6 engine harness (CR-splitter
  units, live-stream assertions, kill-switch and timeout tests against real
  PowerShell) plus the offscreen UI smoke test, wired into a GitHub Actions
  gate with PSScriptAnalyzer for the modules.
- [ ] **Pester coverage for the backup/restore subsystem** — the code users
  depend on when something went wrong must never regress.

### Native-feel UX
- [ ] **Taskbar progress + completion notifications** — `ITaskbarList3`
  progress on the taskbar button (green running / red error) and a Windows
  notification-center toast when a task finishes while Pulse is unfocused.
- [ ] **Elapsed time & memory** — state pill shows `RUNNING · 02:41`; cards
  show "Last run: 3 days ago ✓" and typical duration, derived from the log.
- [ ] **Console polish** — colorized SUCCESS / ERROR / `[WHATIF]` lines,
  auto-scroll that pauses while the user scrolls up.
- [ ] **Modern backdrop** — `DWMWA_SYSTEMBACKDROP_TYPE` (Mica/Acrylic) on
  Windows 11 builds that support it, falling back to the current blur-behind.
- [ ] **Guard rails** — confirm-on-close while a task is running; `Ctrl+K`
  quick-launch palette over `menu_structure.py`.

---

## Phase 3 · v7.0 — Orchestration

*Goal: from a tool you remember to use, to a system that keeps machines
healthy — repeatable, reportable, schedulable.*

- [ ] **Playbooks** — declarative machine baselines: an ordered JSON list of
  task IDs + app selections, run as a queue through the existing dispatcher
  contract with per-step verdicts and an end-of-run summary. `-WhatIf` gives
  full playbook preview for free. The flagship feature for technicians:
  "new machine → apply `workstation-standard.pulse` → walk away."
- [ ] **Health & Drift Report** — loop `Test-TweakAlreadyOn` across the tweak
  catalog to detect tweaks silently reverted by Windows Update; combine with
  drive space, startup creep, restore-point status, and missing drivers into
  a Health card and an exportable **HTML session report** (a client
  deliverable for IT work).
- [ ] **Scheduled unattended maintenance** — recurring engine runs
  (`core.ps1 -Task CleanCache` etc.) via Windows Task Scheduler, surfaced in
  the GUI on next launch: "Since you last opened Pulse: 2 runs, 1 warning."
- [ ] **Structured verdict payloads** — extend the sentinel line with a JSON
  body (counts, durations, per-item results) to power reporting and playbook
  summaries.
- [ ] **Persistent runspace** — one long-lived PowerShell host fed queued
  tasks, eliminating the per-task module-load cost that a 20-step playbook
  would otherwise pay 20 times.

---

*Maintained by [Humam Taibeh](https://github.com/Humam-Taibeh). Items move
between phases only with a written rationale in the PR.*
