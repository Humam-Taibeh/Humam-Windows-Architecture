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

### Changed
- **Full dual-theme re-grade (frontend only — zero backend changes).**
  Dark mode moved from saturated navy + neon cyan to a deep
  charcoal/slate register (Linear / GitHub-dark territory): `#0f1115`
  base, elevation via lightness steps, calm azure `#58a6ff` + soft
  violet `#a78bfa` brand pair reserved for interactive states. Light
  mode is no longer blinding: a cool porcelain-gray canvas (`#eceff4`)
  with translucent soft-white cards — pure white never appears as a
  full-page surface. All four text tones re-tuned for WCAG AA on the
  new surfaces.
- **Toast notifications redesigned from scratch** (`utils/helpers.py`):
  now theme-aware (the old toast was a hardcoded dark rectangle that
  looked broken in light mode), anchored bottom-right in the VS Code
  register so they never cover the title bar or caption buttons,
  click-anywhere-to-dismiss, hover pauses the auto-hide countdown,
  duplicate messages extend the live toast instead of stacking, and the
  stack is capped at four with oldest-yields eviction. Status reads as
  a quiet ✓/✕/i chip + colored spine instead of emoji.
- **Title bar rebuilt to native-caption grade**: Segoe Fluent Icons /
  MDL2 caption glyphs (the OS's own minimize/maximize/restore/close
  characters), a solid caption-red close hover exactly like native
  Win11 windows, and the brand block now renders name · version · an
  elegant violet **BETA** channel pill (`APP_CHANNEL` in `main.py`).

### Fixed
- **Maximized click-through corners (critical)** — on a frameless
  per-pixel-alpha window, the corner pixels DWM rounds away are alpha-0
  and clicks fell straight through to whatever app sat behind Pulse.
  DWM corner rounding is now explicitly disabled while maximized
  (`DWMWCP_DONOTROUND`) and restored on unmaximize, so a maximized
  Pulse is square, opaque and click-owning edge-to-edge like every
  native Win11 app.
- **Fixed-size first launch overflowed small screens** — the hardcoded
  `1180×740 @ (140, 80)` geometry was taller than a 1366×768 laptop's
  work area (and worse at 125%+ DPI scale). The window now sizes to the
  monitor's available geometry, centers itself, and clamps its minimum
  size so it can never be forced larger than the screen it lives on.
  Fractional DPI scale factors are passed through exactly
  (`HighDpiScaleFactorRoundingPolicy.PassThrough`) for pixel-crisp
  rendering at 125/150/175%.
- **Console UTF-8 output encoding** — `core.ps1`'s interactive console mode
  never set `[Console]::OutputEncoding`, so on the default OEM code page
  (437 on US-English Windows, confirmed live) every box-drawing character
  and status glyph (✓/✗/═/║) rendered as mangled question marks and
  garbage. The GUI's spawned subprocess already had this fix
  (`helpers.PowerShellTask` sets it before invoking `core.ps1`); the
  interactive console never did. This was very likely the actual cause of
  "the UI looks chaotic" — verified before/after with the exact same
  glyphs: garbage without the fix, clean boxes with it.
- **Three winget exit codes were mislabeled** in `Resolve-WingetExitCode`,
  cross-checked against winget-cli's own `AppInstallerErrors.h` (not
  memory or a forum post): `-1978335215` was labeled "no applicable
  upgrade" and treated as a **silent success** — it's actually
  `INSTALLER_HASH_MISMATCH`, a corrupted-or-tampered-download failure that
  was being reported as "completed successfully." `-1978335189` and
  `-1978335153` were labeled "package not found" / "file in use" — both
  are actually "nothing to update" signals and are now correctly treated
  as an already-up-to-date skip instead of a failure. Added the exit code
  from this request, `-1978335226` (`SHELLEXEC_INSTALL_FAILED` — the
  wrapped installer itself failed, the common MSYS2 case), with a
  specific, actionable message instead of a generic "unhandled exit code."
- **`Get-InstalledVersion`'s column parsing was silently broken** for
  every call site: it split `winget list` output on 2+ spaces, but winget
  only pads columns for a real interactive console — the instant Pulse
  captures the output (which is always), padding can collapse to a single
  space and the split always failed, meaning the "already up to date"
  fast-path check *always* fell through to a live `winget upgrade`
  invocation it didn't need to make. Fixed to find the exact-match AppId
  token and read the next token as the version — verified against a real
  installed package (Git) confirming the instant-skip path now actually
  fires, plus a constructed edge case where the display name collides
  with winget's own Name column.
- Retry-with-`--force` no longer fires on an "already current" exit code
  it didn't recognize — the gate checked one hardcoded code, so the two
  newly-recognized "nothing to update" codes would have forced an
  unnecessary reinstall instead of honoring the skip.

### Added
- **Windows 11 Snap Layouts on the maximize button** — hovering the
  custom maximize button now summons the native Snap Layouts flyout,
  via the `WM_NCHITTEST → HTMAXBUTTON` contract from Microsoft's
  custom-titlebar guidance: Windows owns the button's mouse events
  (hover is mirrored with a `nchover` property flip, the click is
  re-injected from `WM_NCLBUTTONUP`), and hit-testing is computed
  window-relative in physical pixels so mixed-DPI multi-monitor setups
  can't skew it.
- **Smart Skip, made visible**: `Smart-Deploy` now returns a distinct
  `AlreadyCurrent` flag alongside `Status='Success'` (never renamed
  `Status` itself — several existing call sites checked `-eq 'Success'`
  directly and would have silently broken from a rename). Already-current
  results now print with `Write-AlreadyOK` (green ✓, same color as
  `Write-Success` by design — see the color-scheme note below) and a
  distinct "already up to date - skipped" message, and both
  `Invoke-GuiBulkDeploy` (GUI) and `Process-AppCategory` (console) tally
  them in their own bucket: "3 installed, 2 already up to date" instead of
  the old blended "5 installed or already current."
- **Strict 3-color status scheme, enforced**: `Write-AlreadyOK` used a
  mismatched `DarkCyan` instead of `Write-Success`'s green — a real
  inconsistency in exactly the way the request described. Green (✓) now
  means success or already-current everywhere, uniformly; red (✗) means
  failure; yellow (!) means warning/notice. Session summary
  (`Show-MainMenu`) gained a `$Script:SessionSkipCount` tracked separately
  from successes, shown as "N succeeded / N already up to date / N failed"
  when non-zero.
- **MSYS2 added to `LockProcessMap`** (`mintty`, `bash`, `pacman`) — a
  leftover MSYS2/MinGW terminal holding files open is the most common
  real-world cause of the `SHELLEXEC_INSTALL_FAILED` conflict above;
  closing them before install/upgrade avoids it instead of just reporting
  a cryptic failure afterward.
- **Path Doctor, re-engineered for plain-language clarity**: opens with a
  beginner-friendly explanation of what PATH actually is and why the check
  is harmless (user-scope, no elevation). Each of the 7 tracked tools
  (`$Script:DevToolCatalog`) gained a `Why` field — a one-line "why you'd
  want this" reason surfaced both after "already working" confirmations
  and next to "not installed yet" notices — and the closing summary
  distinguishes "nothing to fix" from "N fixed, N still missing" instead
  of a flat stats line. Mirrored in the GUI's toast message and the
  Software Management card description.
- **Developer & University Hub moved back inside Software Management** —
  it was split out into its own top-level sidebar category last session;
  per this request it's folded back in as two cards (Developer Toolkit,
  PATH Doctor) right after Essential Apps, keeping the sidebar to the
  original six categories. The underlying `DevHubSelectorDialog` /
  `ToolInstallWizardDialog` / catalog data are unchanged — only where the
  entry point lives moved.
- **Developer & University Hub** — a new top-level category, precisely
  separated from every other app list (zero hardware drivers, zero
  general-purpose apps): 16 tools across five sections (Core Runtimes &
  Compilers, IDEs & Editors, AI & Local LLM Stack, Databases & API Tools,
  Containerization), all new to the catalog except the six migrated from
  the retired "Programming & AI Core" card. New entries: IntelliJ IDEA
  Community, Docker Desktop, DBeaver, Postman, Bruno, Open WebUI, Node.js
  and Java JDK promoted from PATH-doctor-only to directly installable,
  Python promoted the same way. Every winget ID was verified live via
  `winget show` before being added — nothing here is guessed.
  - **`DevHubSelectorDialog`** — manual-first (nothing pre-checked), with
    a master Select All/Deselect All, three quick-select bundles (Java/
    University Stack, AI/Python Stack, Web Dev Stack) that tick their
    tools without forcing anything, live dependency hints (checking
    NetBeans/IntelliJ/PyCharm softly highlights its still-unchecked
    runtime — correctly handles two IDEs sharing one runtime, verified
    with an explicit test), and a per-tool "⋯" button.
  - **`ToolInstallWizardDialog`** — the "⋯" button's generic 3-path
    dialog: Path A narrows the normal bulk winget deploy to just that one
    tool (no new backend code — it's the existing selection/deploy
    pipeline with one AppId), Path B opens the vendor's official page,
    Path C hands a picked local installer to the new generic
    `InstallLocalFile` task (`Invoke-GuiLocalInstall` in
    `04-SoftwareEngine.ps1`: msiexec for `.msi`, direct run otherwise, no
    forced elevation — installers that need it self-elevate via their own
    UAC manifest, same as a manual double-click).
  - **PATH Doctor**, promoted to its own prominent card in the new hub
    (moved out of Software Management). Runs at user scope
    (`[Environment]::SetEnvironmentVariable(..., "User")`) — genuinely no
    elevation required for a per-user PATH/JAVA_HOME repair; over-elevating
    a user-scope operation would be a step backward, not a feature.
  - Fixed a real bug caught during verification: `,@("id","name")`, not
    `@(@("id","name"))`, for a single-tool catalog array — PowerShell
    silently flattens the latter (`@( @(x,y) )` unwraps to a flat 2-element
    array when it's the ONLY item), which broke Docker Desktop's entry
    until an explicit array-shape test caught it.
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
