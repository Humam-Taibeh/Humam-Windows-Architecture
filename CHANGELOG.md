# Changelog

All notable changes to **Pulse** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning note: the GUI application and the PowerShell core are versioned
independently (GUI `APP_VERSION` in `src/frontend/main.py`, core
`$Script:ScriptVersion` in `src/backend/core.ps1`). Releases below track the
GUI version, with core changes called out explicitly.

---

## [Unreleased]

### Added
- **Three-path Office wizard** — `OfficeWizardDialog`'s single "locate your
  files" flow is now three up-front paths:
  - **Path A: Automated Cloud Download** (task `InstallOfficeODTAuto`,
    backend `Invoke-GuiOfficeAutoDownload`) — fetches
    `officecdn.microsoft.com/pr/wsus/setup.exe` directly (the same stable
    CDN endpoint winget's own `Microsoft.Office` manifest uses) rather than
    scraping Microsoft's download-center page for the versioned
    `officedeploymenttool_*.exe` link. That file already IS the extracted
    Click-to-Run client, so there's no self-extractor dialog to click
    through at all. Writes a built-in default `configuration.xml`
    (`Get-OfficeDefaultConfigXml`) only if the target folder doesn't
    already have one. The wizard states plainly that this default targets
    Volume License/KMS activation, not a plug-and-play key.
  - **Path B**: unchanged auto-detect / browse / individual-file-pick flow.
  - **Path C: Beginner Guide** — numbered, plain-language walkthrough of
    downloading the ODT and building a configuration.xml via Microsoft's
    own tools (the former single "download" step), feeding into Path B's
    locate flow once the files exist.
  - **Multi-config detection** — `Find-OfficeConfigFile` / the wizard's
    `_find_office_files` now recognize OCT's own export naming
    (`configuration-Office365-x64.xml` etc.) in preference order; if a
    folder has more than one `.xml`, the wizard shows a picker (top match
    marked "recommended") instead of silently grabbing the first one
    alphabetically.
- **Office Deployment Tool wizard** (`widgets.OfficeWizardDialog`, task
  `InstallOfficeODT`) — replaces the winget-based Office install (which
  could only apply Microsoft's stock default configuration) with a
  4-step guided flow that preserves full `configuration.xml` control:
  choose to download the official ODT + Customization Tool (direct
  browser links) or locate files already on disk (auto-detects
  `Desktop\Office`, including the OneDrive-redirected and Public Desktop
  variants, with a folder browser and an individual-file-picker fallback),
  then a confirm step with a prominent amber warning ("don't close the
  setup window") before handing off to the normal task pipeline — same
  live console, Stop button and toast machinery as every other task.
  Backend: `10-Office.ps1`'s new `Invoke-GuiOfficeODTInstall` reuses the
  existing self-extractor/validation helpers but never prompts (the
  wizard already collected consent client-side); gated by
  `AdminRequiredTasks` since `setup.exe /configure` needs elevation.
  `core.ps1` gained `-OfficeSetupPath`/`-OfficeConfigPath` params, threaded
  through `PowerShellTask` from the resolved wizard paths. The
  `$Apps_Office` winget catalog entry for the Office bundle itself is
  removed (Word/Excel/etc. have no per-app winget package, only a
  default-config ODT run); Teams and OneDrive remain on the ordinary
  winget path as `$Apps_OfficeCompanions`, exposed as their own card.
- **Unified glass material system** (`theme.glass_fill`) — cards, Welcome
  insight tiles and dialog panels now share one frosted-glass gradient
  definition instead of three that had quietly drifted apart (card/insight
  sheen stops were 0.12 vs 0.15). Dialogs (`ConfirmDialog`,
  `AppSelectorDialog`, `CommandPalette`) also gained the same painted
  bevel edge (`DepthCard`) cards already had — previously every dialog was
  a flat rectangle while every card had visible glass depth.
- **Brand duotone gradient** (`theme.brand_gradient`) — the violet `accent2`
  color existed only in the shimmer bar; it's now part of a deliberate
  accent→accent2 sweep reused on the primary dialog button, the selected
  sidebar item, and the running-state pill, so the two-tone brand reads as
  one system. Danger confirmations (Purge OneDrive, Remove Edge, etc.)
  deliberately keep a flat solid red — no gradient on a "hard to undo"
  action.
- **Active nav indicator bar** — the selected sidebar item now shows a
  short rounded accent→accent2 bar on its left edge, the same affordance
  Windows 11 Settings uses for its selected nav entry.
- **Horizontal scrollbar styling** — `scroll_area_qss`/`console_qss` only
  styled the vertical scrollbar; any control needing horizontal scroll
  (the card grid or app-selector list at a narrow width) fell back to the
  raw unstyled OS scrollbar. Both now match.
- **Microsoft Office Suite catalog entry** (`$Apps_Office` in
  `01-Catalogs.ps1`, mirrored in `menu_structure.py` as `InstallOfficeApps`)
  — Word, Excel, PowerPoint, Outlook, OneNote, Access and Publisher via the
  `Microsoft.Office` winget package (Microsoft 365 Apps for enterprise,
  silent Click-to-Run default install — no `configuration.xml` needed),
  plus Microsoft Teams and OneDrive as real standalone winget packages.
  Reachable from the GUI's Software Management category and the console's
  App Deployment Hub `[E]`, through the same checkbox multi-selector and
  `Smart-Deploy` pipeline as every other app category. The advanced,
  config.xml-driven ODT flow (`Show-OfficeDeployment`, console-only) is
  unchanged and still covers custom deployments winget's default config
  can't express.
- **Command palette (Ctrl+K)** — fuzzy-search quick launcher over every
  task in `menu_structure.py`. Runs picks through the normal
  `request_task()` pipeline (confirmations, the app selector, and the
  single-task-at-a-time guard all apply, exactly as a card click would).
- **Glass bevel on every surface** (`animations.paint_bevel_frame`) — a
  permanent diagonal-gradient stroke (bright top-left highlight → soft
  bottom-right shadow) on operation cards, sidebar nav buttons, and the
  new `DepthCard` (Welcome insight tiles + status dock). One painted
  stroke, no offscreen shadow buffer, and no Qt corner artifacts (the
  failure mode per-side `border-*-color` QSS rules hit on rounded rects).
- **Click ripple** (`animations.RippleController` /
  `paint_ripple_frame`) — an expanding, fading accent-tinted ripple from
  the click point on cards and nav buttons, clipped to the rounded rect.
- **Icon "pop" on hover** — GlassCard icons grow subtly (28→31px), driven
  by the existing hover-glow intensity via a managed `QFont` (never a
  per-frame stylesheet rebuild).
- **Breathing status dot** — the bottom-bar `●` now pulses softly only
  while a task is actually running (`widgets.StatusDot`, the same
  pure-paint technique as `BreathingIcon`), and goes still the instant
  it's done.
- **Custom console empty state** — `LiveConsole` paints a small on-brand
  "pulse" waveform motif + message in place of the generic placeholder
  text, replaced live the instant real output streams in.

### Changed
- Card/nav spacing tightened for a cleaner rhythm: card description
  spacing 4→6px, category grid gutter 14→16px.
- `ConfirmDialog`'s outer panel margin (26, 24, 26, 22) now matches
  `AppSelectorDialog`'s (24, 22, 24, 20) — the two are the same "panel +
  body + actions" pattern and had drifted to slightly different insets.

### Fixed
- **Maximized/fullscreen layout** — the shell's floating margins no longer
  survive maximize: `body`'s content margins now collapse to a slim
  comfort gap in lock-step with the existing corner/border flush, so a
  maximized window sits truly edge-to-edge instead of floating inside a
  dead-space frame.

### Changed
- **Faster, snappier motion**: hover glow, page fade, card cascade, dialog
  entrance, theme cross-fade, and toast animations were all retuned into
  the 90–190ms band (from up to 300ms) for a lighter, more immediate feel.
  The shimmer progress sweep (an indeterminate loop, not a transition) is
  unchanged.

---

## [6.1.0] — 2026-07-19

### Added
- **Official application icon** (`assets/pulse.ico`, seven sizes 16–256px):
  the Pulse four-pointed star on a deep-navy plate, shown in the title bar
  and taskbar (with an explicit `AppUserModelID` so source runs don't group
  under python.exe) and embedded into `Pulse.exe` via `main.spec`.
- **Native Windows 11 window behavior**: dragging uses the OS system-move
  loop (real Aero Snap zones, drag-to-top maximize, native
  restore-from-maximized), the outer 8px is a native `WM_NCHITTEST` resize
  border with real cursors, Win+Up/Down work via min/max window hints, and
  a maximized window drops the floating radius/border so corners sit flush.
- **Micro-interactions**: 220ms cross-fade on theme switch, weighted press
  tint on operation cards, `:pressed` states on every button, dialog
  entrance fade, and a settle-upward fade on returning Home.

### Changed
- **Enterprise color grading** for both themes: neutral deep charcoal-navy
  dark mode and cool-gray light mode, calmer Fluent-adjacent accents
  (`#4cc2ff` dark / `#0067c0` light), GitHub-grade status colors replacing
  the neon green/gold/coral, matching category accents and toast colors,
  and the sheen-gradient glass treatment extended to the Welcome insight
  cards for one consistent material.
- **Hardened verdict contract**: the backend's final line is now sentinel-
  prefixed (`##PULSE##SUCCESS|…` / `##PULSE##ERROR|…`) and the GUI scans
  backwards for it, so stray trailing output from a module or external tool
  can never shadow the verdict. Bare `SUCCESS|`/`ERROR|` lines from pre-6.1
  backends still parse via a strict fallback; the console displays the
  verdict without the machine sentinel.
- **Log relocated** from the Desktop to `%LOCALAPPDATA%\Pulse\logs\
  Pulse_Log.txt` with size rotation (5 MB threshold, five archives kept).
  A OneDrive-synced Desktop no longer pays sync traffic per log line. An
  existing v6.0 Desktop log is migrated automatically; the in-app log opener
  falls back to the legacy Desktop locations.
- **UPX disabled** in `main.spec` — packed executables are a classic
  antivirus false-positive heuristic; an elevated system tool cannot afford
  that reputation risk.

### Added
- `ROADMAP.md` — the three-phase plan (v6.1 Trust & Hardening, v6.5
  Resilience & Native Feel, v7.0 Orchestration).

---

## [6.0.0] — 2026-07-19

### Changed — Rebrand to Pulse
- The project, application, window branding, terminal banners, and executable
  are now **Pulse** (`dist\Pulse.exe`).
- Runtime artifacts renamed: session log `Desktop\Pulse_Log.txt`, snapshot
  registry root `HKCU:\Software\Pulse`, Desktop backups
  `Pulse_{Edge,OneDrive,Startup,Driver}Backup`, power scheme
  **Pulse Power Plan**, restore point **Pulse Restore Point**.
- **Migration shims** keep v5.x machines whole: the legacy
  `HKCU:\Software\HTCoreArchitecture` snapshot root is copied once to the
  Pulse root; Edge/startup restores and the in-app log/backup openers fall
  back to the old `HTCore_*` Desktop artifacts; an old-named power plan is
  renamed in place instead of duplicated.

### Added
- **Global kill switch** — a danger-styled *■ Stop Task* button in the console
  header hard-terminates the running task's entire process tree
  (`taskkill /T /F`: PowerShell plus its winget/sfc/DISM children). The engine
  reports a distinct `cancelled` outcome — never a fake error — and the UI
  resets immediately. One terminal signal per task, guaranteed:
  cancel > timeout > contract verdict.
- **True real-time console** — the worker reads the pipe in binary chunks with
  an incremental UTF-8 decoder and understands bare carriage-return rewrites,
  so `sfc` / `DISM` / `winget` progress updates a single console line live;
  per-chunk coalescing keeps the GUI event queue bounded on chatty tools.
- **Execution state pill** (IDLE / RUNNING / SUCCESS / ERROR / STOPPED) beside
  the console header, plus a transient green/red **verdict flash** on the card
  that launched the task.
- Refined frosted-glass theme tokens (stronger borders, card sheen gradient)
  in both dark and light modes.

### Changed
- Complete repository meta-file overhaul: rewritten `README.md`, comprehensive
  `.gitignore`, added `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`,
  `.editorconfig`, `.gitattributes`, and GitHub issue/PR templates.
- `requirements.txt` cleaned up: removed the unused `customtkinter` dependency
  (the GUI is pure PySide6); build tooling moved to `requirements-dev.txt`.
- `main.spec` (PyInstaller build recipe) is now version-controlled.

### Core (PowerShell v4.0 → v6.0)
- Version aligned with the GUI. Every GUI task now opens with a timestamped
  start banner in the live console, and SFC output streams line-by-line while
  it scans (previously buffered until the scan finished).

---

## [5.1] — 2026-07-07

### Added
- **PySide6 (Qt 6) frontend** replacing the earlier CustomTkinter prototype:
  frameless glass-morphism window, dual light/dark themes with live switching,
  60 fps motion system (glow, shimmer, cascade, page fade), live task console,
  and toast notifications.
- **Modular frontend blueprint** — menu data (`menu_structure.py`), design
  tokens (`theme.py`), motion (`animations.py`), components (`widgets.py`),
  and threading utilities (`utils/helpers.py`) fully separated from the
  orchestrator (`main.py`).
- **Per-app checkbox selector** for winget catalogs — install only the apps
  you tick.
- **System insights dashboard** — OS build, CPU, and RAM read via registry
  and kernel32 with zero third-party dependencies.
- **One-file PyInstaller distribution** (`main.spec`) bundling the GUI and
  the PowerShell core into a single elevated, windowed executable.

### Fixed
- Qt thread-safety: PowerShell now runs on a `QThread` and reports back
  exclusively through Qt signals — widgets are never mutated from a
  background thread.
- Non-interactive guard: when dispatched from the GUI, the core never blocks
  on console input or pops browser/Store windows mid-run.

### Core (PowerShell v3.3 → v3.4)
- New **Safety & Recovery** hub: one-click rollback to the session restore
  point, *Reset ALL Tweaks*, *Restore All Services*, and an in-app log viewer.
- Every reversible tweak snapshots its **original** value before changing
  anything, so resets restore your real prior settings.
- Automatic System Restore point before the first system change of any
  session, across all modules.
- Edge removal backs up Preferences/Bookmarks/Favicons; OneDrive removal
  offers a Desktop backup first.
- Failed operations (SFC/DISM, Edge removal/reinstall, restore points) can be
  retried in place.

---

## [1.0] — 2026-07-06

### Added
- Initial release: data-driven PowerShell deployment & optimization framework
  (`core.ps1`) with a self-elevating launcher, hierarchical terminal menu,
  winget-based software deployment, registry tweak engine, SFC/DISM repair
  automation, privacy hardening, and session logging.

[Unreleased]: https://github.com/Humam-Taibeh/Pulse/compare/master...HEAD
