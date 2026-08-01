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

### Removed
- **The Software Catalog's quick-select stack chips** — *Java / University
  Stack*, *AI / Python Stack* and *Web Dev Stack*. They were a third way
  to narrow a list that already narrows two ways (tabs by category, field
  by name), they applied to one tab out of five, and they were the only
  control in the dialog that appeared and disappeared as the tab changed.
  The Development & Tools tab answers the same question by being read.
  `CATALOG_BUNDLES` / `CATALOG_BUNDLE_SECTION` and their backend mirror
  `$Script:DevHubBundles` are gone with them (nothing in the backend ever
  read the mirror), and `test_contract.py` now fails if either comes back.

### Added
- **A denser, deeper ambient field.** The background wash went from three
  orbs and 42 flat motes to **five orbs and 126 stars in three depth
  tiers** — far stars small, dim and slow; near ones larger, brighter and
  quicker — plus two smaller, dimmer orbs that lean hardest on the pointer
  parallax, so the field has a front and a back instead of reading as one
  sheet. Stars are now cached soft-glow textures blitted at native size
  rather than antialiased ellipses, which is what makes 3x the density
  cost *less* than the old field did (0.26ms per paint for 126, against
  0.17ms for 42); the first cut scaled one texture per star and cost
  1.5ms, which is the difference between a blit and a resample.
  - Density is not intensity: per-star alpha and the light-mode orb peaks
    are unchanged in character, and the light canvas still measures as the
    neutral system grey the palette specifies (mean channel spread 3.9,
    against 10.4 for the peaks that once dyed it lavender). Four new
    ambient contract tests pin the tier structure, the texture cache, that
    wash measurement and the orb layer's 10Hz rebuild budget.
- **Two new spacing steps and a test that enforces the whole scale.**
  `SPACE["xxs"]` (2px, leading inside one text block) and `SPACE["xxl"]`
  (32px, air around an empty state) close the ramp at both ends, and
  `test_layout_contract.py::test_every_layout_measurement_comes_off_the_scale`
  now walks the frontend's AST and fails on any `setSpacing` /
  `addSpacing` / `setContentsMargins` literal that is not a scale step.
  57 calls had drifted back to hand-picked numbers (1, 2, 3, 6, 7, 9, 10,
  14, 18, 20, 28, 30, 34) — every one within 2px of a step it could have
  used, which is precisely the "almost aligned" feel the scale exists to
  prevent. All 57 now come off the scale.
- **A guard against stock platform chrome.** Stacks, scroll areas and
  self-scrolling lists are the only widgets that paint Windows' own
  chrome when left unstyled, and four surfaces were: the Office wizard,
  the Update Center and the Startup Manager each drew a sunken Fusion
  frame around their pages, and the Ctrl+K palette and both card grids
  showed stock scrollbars with arrow buttons. `theme.stack_qss()` is now
  the one place a stack is styled, `command_list_qss` carries the shared
  scrollbar rules, and a new test constructs every dialog and page and
  fails on any unstyled one.
- **Activation Status** — a new read-only card under *Safety & Recovery*
  reporting Windows and Office licence state. It answers the three
  questions a technician actually has about a machine: is it licensed,
  under which channel (retail / OEM / volume-KMS / MAK / subscription),
  and does that licence expire. Each state is shown as a tone-coloured
  verdict pill plus a plain-English sentence explaining what it means —
  `LicenseStatus` is an integer the licensing service never explains, and
  it is now translated in exactly one place (`Get-ActivationStatusDetail`)
  so the GUI, the console view and the log cannot describe the same code
  differently.
  - New backend module `src/backend/modules/13-Activation.ps1`, with a
    HARD READ-ONLY contract inherited from `11-StateProbe.ps1`: it reads
    the Software Licensing WMI providers and formats what they say. It
    does not install a product key, contact a licensing server, or alter
    licence state — pinned by
    `test_contract.py::test_activation_module_is_read_only`, which fails
    on `slmgr`, `ospp`, `ActivateProduct`, `Invoke-CimMethod`,
    `Invoke-Expression`, `Invoke-WebRequest` and `Start-Process` alike.
  - Needs **no elevation**: every property read is available to a standard
    user, so the card answers in full from an unelevated Pulse. The one
    field that genuinely requires admin (the OEM firmware licence) reports
    *unknown* rather than a false "absent".
  - Both Office licensing platforms are queried and merged — modern
    Click-to-Run / Microsoft 365 under `SoftwareLicensingProduct`, and
    Office 2010-era installs under `OfficeSoftwareProtectionProduct` — so
    an absent provider is never mistaken for "Office is unlicensed". A
    separate install probe distinguishes *no Office on this machine* from
    *Office installed but holding no licence*, which are opposite findings
    that both produce an empty licence list.
  - When something needs changing, the dialog hands off to Windows' own
    activation page (`ms-settings:activation`) rather than acting on the
    licence itself.
  - Also reachable from the standalone console app: **Safety & Recovery →
    [5] Activation Status**.
- **Contract tests** for three drift classes that previously had none: a
  GUI-local `@action` with no handler in `main.py` (it used to fail at
  click time with "Unknown local action"), a card `glyph` absent from
  `theme.GLYPHS` (renders a blank icon plaque, silently), and the
  activation module's read-only guarantee.

### Changed
- `HealthReportDialog`'s private row/note/heading renderers were lifted to
  module-level `report_row` / `report_note` / `report_heading` primitives
  now shared with the activation report. Both surfaces render the same
  shape of content and must colour a tone identically — two private copies
  of that mapping would eventually disagree about what amber means.

---

## [10.0.0] — 2026-07-28

The v10 UX overhaul. The app version was pinned at 6.1 while the
design system moved through v7-v10; this release reconciles them,
so `APP_VERSION`, `$Script:ScriptVersion`, the README badge and
this changelog all read 10.0.

### Added
- **Keyboard access to the operation grid.** `GlassCard` was a `QFrame`
  with mouse handlers only — no focus policy, no key handling — so the
  app's primary surface was unreachable by keyboard and Tab stopped at the
  sidebar. Cards now take `StrongFocus`, activate on Enter/Space with the
  same press + ripple feedback a click gives, traverse with the arrow keys
  across the live (filter-aware) grid, carry accessible names/descriptions,
  and paint a solid 2px accent focus ring — Qt's dotted default is
  invisible against this material.
- **A real shortcut layer**, up from two bindings (Escape, Ctrl+K):
  `Ctrl+1…6` jump to a module, `Ctrl+H` the dashboard, `Ctrl+L` / `Ctrl+F`
  the page filter, `Ctrl+\` the live output, `F1` / `?` a keyboard sheet.
  Bindings and the help sheet are generated from one table
  (`PulseApp.SHORTCUTS`), so a shortcut cannot exist undocumented — which
  is how Ctrl+K stayed undiscoverable for four releases.
- **Category filter rail** — an inline filter field and an operations count
  chip fill the previously empty right two-thirds of every module header.
  The filter matches titles, descriptions *and* a hub's sub-item titles, so
  searching "office" surfaces the hub that contains it instead of hiding a
  real match; the chip switches to "3 OF 12" and takes the module accent
  while a filter is active, and an explicit empty state replaces the blank
  grid when nothing matches.
- **Live output is no longer a dead end** — copy, save-to-file, clear and a
  timestamp toggle in the Activity rail. Copy and export report line counts
  through the app's toast stack, and a failed write is reported as a
  failure rather than silently implying the log was saved.
- **Applied-state badges** — cards for readable tweaks now show a green
  `APPLIED` chip when the setting is currently active on the system, so
  Pulse finally answers "did I already do this?" without re-running an
  operation and reading the log. Backed by a new read-only backend probe
  (`src/backend/modules/11-StateProbe.ps1`, task `GetTweakState`) covering
  Dark Mode, mouse acceleration, Minimalist Taskbar, Classic Context Menu,
  Game Mode, Advertising ID, Activity History, Telemetry, Hibernation and
  the Ultimate Power Plan. The probe runs on its own thread outside the
  single-task pipeline, needs no elevation, writes nothing (verified: its
  output is byte-identical with and without `-WhatIf`), and is never
  cached — settings changed outside Pulse are reflected immediately. An
  unreadable check reports *unknown* and shows no chip rather than
  guessing.
- **Recent Operations panel** in the sidebar — the last three completed
  operations with their module colour and a pass/fail dot, one click to
  re-run. Fills what was ~360px of dead rail. Re-runs resolve back to the
  live catalog entry, so they always use the current definition.
- **Preference persistence** (`src/utils/prefs.py`, QSettings) — theme,
  window geometry, Activity-drawer pin state and the recent-operations
  trail now survive a restart. Previously every session opened dark, at
  the default size, with the drawer unpinned.
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
- **Design-system foundations** — a single spacing scale (`TH.SPACE`) and
  semantic radius scale (`TH.RADIUS`) replace 13 ad-hoc spacing values and
  17 ad-hoc corner radii; card padding is symmetric.
- **Light-mode ambient wash pulled back** (multiply peaks 0.30→0.16) — the
  v10 canvas deepening made the old strength read as a lavender haze.
- **Modal presentation system** — every dialog now shares one
  construction path (`widgets._dialog_chrome`): frameless panel at its
  exact content width inside a transparent shadow gutter, a soft
  elevation drop shadow, and body-anchored positioning
  (`_present_dialog`) that always places the panel fully below the
  title bar (command palette top-anchored, everything else centered in
  the body; nested wizards climb to the app window). Opening any dialog
  raises a dense theme-aware **scrim** over the app body, so the card
  grid and console are completely masked while a modal is up — and the
  scrim deliberately stops at the title bar, which stays uncovered.
  Dialog action buttons unified at 36px height.
- **Title bar strip is now native HTCAPTION** — dragging, Aero Snap,
  double-click-to-maximize and the right-click system menu are handled
  by Windows itself (only the theme toggle keeps a client hole), which
  also means the strip keeps working while a modal dialog is open.
- **Software Management unified with the Developer Hub pattern** — every
  `apps` catalog card (Essential Apps, Gaming Launchers, Hardware
  Diagnostics, Core API Runtimes, Teams & OneDrive) now opens the same
  elite selector the Dev Hub uses: identical rows (checkbox + per-tool
  "⋯" install-options wizard offering **winget / official website /
  local installer file**), a Select All / Deselect All toolbar with a
  live "n selected" counter, and a "Deploy Selected (n)" primary
  action. Catalog entries gained GUI-only description + official-URL
  metadata (4-tuples, legacy 2-tuples still accepted); the backend
  contract is untouched — core.ps1 still receives only the ticked
  winget IDs, and a wizard's local-file pick routes through the
  existing InstallLocalFile task exactly like the Dev Hub.
- **Responsive card grid** — category pages no longer force 3 columns:
  column count follows the viewport (1 col under ~680px of content
  width, 2 at the default window size, 3 maximized on widescreen), so
  cards keep a ≥340px footprint, descriptions never clip, and the
  default view reads as a spacious 2-column layout instead of three
  cramped slivers. Verified live on the Windows platform at
  1020/1180/1860px → 1/2/3 columns.
- **Breathing-room pass** — body/sidebar/content margins, card padding
  (20×16, min height 132), grid gutters (18px), dialog padding (28×24)
  and the type scale (title 20px, body 13px, desc/tagline 12px) all
  moved one comfortable step up; long card titles now wrap instead of
  clipping.
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
- Card/nav spacing tightened for a cleaner rhythm: card description
  spacing 4→6px, category grid gutter 14→16px.
- `ConfirmDialog`'s outer panel margin (26, 24, 26, 22) now matches
  `AppSelectorDialog`'s (24, 22, 24, 20) — the two are the same "panel +
  body + actions" pattern and had drifted to slightly different insets.
- **Faster, snappier motion**: hover glow, page fade, card cascade, dialog
  entrance, theme cross-fade, and toast animations were all retuned into
  the 90–190ms band (from up to 300ms) for a lighter, more immediate feel.
  The shimmer progress sweep (an indeterminate loop, not a transition) is
  unchanged.

---

### Fixed
- **Card descriptions were being silently truncated.** Measured across the
  real catalog at the 3-column grid width, 14 cards had their description
  cut off mid-sentence (worst: PATH Doctor, losing 88px — over half its
  text) and 5 also lost part of their title; nothing warned, the text was
  simply painted outside the card's clip. Cards are rebuilt with a header
  row (icon + title) above a **full-width** description — the description
  column was ~256px and is now ~312px — plus a hard per-block line budget
  (`ClampedLabel`) that elides with the full text in a tooltip. Catalog
  copy was tightened to match. Result: **0 truncated cards** across 540
  instantiations (2 themes × 6 widths × the whole catalog).
- **Window resizing leaked ~12 GB and stuttered.** `AmbientGlow` cached its
  aurora orbs keyed on the window size, so every pixel of a drag-resize
  minted three fresh ~1800×1800 pixmaps and kept them forever: a single
  1000→1440px drag produced **1,323 cached pixmaps totalling 11.9 GB** at
  34.9 ms/frame. Orbs are now rendered once at a fixed texture size and
  scaled on blit — **3 pixmaps, 3.0 MB, 15.3 ms/frame**.
- **Grid reflow could place cards outside their container.** Column counts
  were derived from the page width while cards were laid out inside a
  scroll host whose width settles a layout pass later; when the two
  disagreed the grid overflowed (measured: a 1719px-wide grid inside a
  590px host). Reflow is now driven by the host's own resize
  (`ResponsiveGridHost`), so the width that picks the column count is the
  width the cards are laid out in.
- **Card entrance animation fought the layout.** `CascadeAnimator` drives
  card positions directly; a resize mid-cascade left cards stranded at
  stale coordinates. It now abandons the entrance when the host resizes
  and hands placement back to the layout.
- **Minimum window size is derived, not guessed** — it is computed from the
  chrome plus one minimum-width card, so the window can no longer be
  dragged to a size where the grid cannot lay out. The Welcome page's
  Quick Actions gained a scroll area; without one a short window resolved
  the impossible constraint by crushing cards to as little as 17px.
  Verified across 448 (page × size) combinations.
- **Light mode's colour system failed contrast.** The six module accents
  were single hex literals shared by both themes and tuned for a near-black
  canvas; against the light card they measured **1.86–2.64:1**, far under
  the 3:1 floor for an icon. They are per-theme tokens now
  (`theme.resolve_accent`), re-resolved on every theme switch, and every
  one clears 4.5:1 as text and 3:1 as a glyph in both modes.
- **The text ramp's bottom two steps failed AA.** `text_faint` measured
  3.00:1 and `text_muted` 3.98:1 on a card while carrying 10–13px copy.
  The ramp is rebuilt evenly in CIE L\* with its floor pinned at 4.55:1 —
  four visibly distinct steps, all legible.
- **Elevation was nearly invisible.** Card-vs-content-well separation was
  1.20:1 (dark) / 1.21:1 (light). Depth now comes from recessing the
  content well rather than brightening the card (which would have wrecked
  the text ramp above it): **1.46:1 dark, 1.27:1 light**.
- **Toasts covered the live console.** A fixed bottom margin put the toast
  stack on top of the Activity drawer exactly when a task was running. The
  stack now tracks the drawer's height live.
- **Dialog titles were never actually enlarged** — all 8 built their header
  as `label_qss(t, "card").replace("14px", "16px")`, but the card role has
  been 16px since v7, so the replace matched nothing. Added a real
  `dialog` type role (18px/700).
- **Edge force-removal now resolves `setup.exe` dynamically** — Edge's
  uninstaller lives under a per-version folder
  (`…\Edge\Application\<VERSION>\Installer\setup.exe`) that changes on
  every update; `Remove-MicrosoftEdge` (`06-Tweaks.ps1`) now hunts it
  recursively under both Program Files roots (newest version wins)
  instead of relying on a path that went stale (the cause of setup.exe
  exiting with code 93 / never running). The Appx cleanup pass now
  **excludes `Microsoft.MicrosoftEdgeDevToolsClient`**, a hard-protected
  Windows 11 OS component that always fails `Remove-AppxPackage` with
  `0x80070032` and previously aborted the whole removal into a false
  failure.
- **Window controls blocked while a modal was open (critical)** — the
  old dialogs centered over the whole window, physically covering the
  caption buttons, and Qt's application modality blocked title-bar
  clicks besides. Dialogs are now always positioned below the title
  bar, and because minimize/maximize/close run through the raw
  non-client path (`WM_NCLBUTTONUP` in `nativeEvent`), they bypass
  Qt's modal input blocking entirely — verified live with real window
  messages while a selector was open: hit-testing answered
  HTCLOSEBUTTON and a posted NC click minimized the window with the
  modal still up. Closing during a modal settles open dialogs first
  (reject) so no exec() loop is orphaned.
- **Modal bleed-through, eliminated at the source** — beyond the opaque
  panels, the new body scrim masks *everything* around an open dialog;
  nothing underneath is legible anymore in either theme.
- **Caption-button hitbox (critical)** — closing the window demanded a
  pixel-perfect hit on the 40×30 glyph. WM_NCHITTEST now maps
  generously expanded non-client zones over minimize/maximize/close
  (HTMINBUTTON/HTMAXBUTTON/HTCLOSEBUTTON): the strip from the window's
  top edge to below the buttons, from the minimize button's left edge
  to the window's right edge, split at gap midpoints — so clicking
  anywhere in the top-right corner region registers, exactly like a
  native app, and the literal screen corner closes a maximized window
  (Fitts corner-slam). Clicks are re-injected from WM_NCLBUTTONUP with
  hover mirrored per-button; a swept probe confirmed zero dead pixels
  across the entire strip. Resize borders keep priority while floating,
  matching native ordering.
- **Text bleed-through in overlays** — dialog surfaces were 98–99%
  opaque, so the card grid/console underneath ghosted through as
  overlapping text when selectors and wizards opened. Dialog
  backgrounds are now fully opaque (toasts 99%), and card titles wrap
  rather than overflow.
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
- **Maximized/fullscreen layout** — the shell's floating margins no longer
  survive maximize: `body`'s content margins now collapse to a slim
  comfort gap in lock-step with the existing corner/border flush, so a
  maximized window sits truly edge-to-edge instead of floating inside a
  dead-space frame.

### Removed
- Dead code: `total_operations()` (`menu_structure.py`), `HoverGlow` /
  `PulseAnimation` (`helpers.py`, long superseded by `animations.py`), and
  the unused `hero` / `value` / `meta` label roles — all with zero call
  sites. (`category_operations()` was removed as dead and then reinstated
  later in this same release, when the new category count chip gave it a
  real call site.)

- **Dual-theme legibility & finish pass** — light mode's two lower text
  steps deepened (`text_muted` #5d6879 → #4e5a6c ≈ 6:1, `text_faint`
  #8d97a8 → #75808f ≈ 4:1) killing the washed-out body/caption reading,
  and card hairlines strengthened (0.11 → 0.14 alpha) so white-on-porcelain
  cards keep distinct edges; dark mode's `text_faint` lifted one step
  (#5a6272 → #646e80) for 10px captions on dim laptop panels. Grouped hub
  landing screens gained commercial-grade section headers: accent-tinted
  letter-spaced titles finished with a 1px accent rule fading toward the
  panel edge (`hub_group_header_qss` / `hub_group_rule_qss`), with
  proximity-correct rhythm (headers sit tight over their own cards, a
  full step clear of the previous group). Interaction polish: selector
  rows now lift fill *and* border on hover (matching GlassCard),
  checkbox wells pre-tint with the accent on hover and acknowledge the
  cursor when checked, and dialog Cancel/Close buttons match the primary
  button's 600 weight with a firmer hover border.
- **System Tools & Utilities hub is now grouped, not a flat list** — its
  eight sub-actions are split into three scannable sub-groups the
  HubDialog renders under small section headers: **Diagnostics &
  Optimization** (Hardware Diagnostics, PATH Doctor, Startup Manager,
  Check for Updates), **Microsoft Edge** (Remove / Install-Restore), and
  **Microsoft OneDrive** (Purge / Install-Restore) — each app's teardown
  now sits directly beside its restore. Hubs may now carry `groups`
  (titled sections) in place of a flat `items` list; `menu_structure.hub_items()`
  flattens either shape so the command palette, counters and hub
  navigation are unaffected.
- **Browsers & Daily Apps trimmed to three core selections** — Browsers,
  Chat & Media / Microsoft Office Suite / Core API Runtimes. The combined
  "Teams & OneDrive" card was removed; OneDrive's install/restore now
  lives beside Purge OneDrive under System Tools & Utilities
  (`RestoreOneDrive`).
- **Microsoft Teams dropped from the catalog entirely** — purged from
  `$Apps_OfficeCompanions`, the download-URL and lock-process maps
  (`01-Catalogs.ps1`), the retired `InstallOfficeApps` GUI task
  (`30-GuiDispatcher.ps1`) and the console App Deployment Hub
  (`20-Menus.ps1`, now a OneDrive-only category).

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
