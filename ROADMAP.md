# Pulse — Roadmap

**Current release: v10.0** · engine contract `##PULSE##` · 80-test regression
suite · CI on `windows-latest`.

Guiding principle, unchanged: **quality over quantity** — every item here must
add real value to the daily workflow of IT technicians and power users, or it
doesn't ship.

> **History.** The original roadmap was written against v6.1 and planned three
> phases (v6.1 Trust, v6.5 Resilience, v7.0 Orchestration). Most of it shipped;
> the parts that didn't are carried forward below with their status made
> explicit, and one item was deliberately **reversed** — see
> [Settled decisions](#settled-decisions). Items move between phases only with
> a written rationale in the PR.

---

## Settled decisions

Things that are **closed**. They are recorded here so they are not
re-proposed, re-litigated, or accidentally undone by a future change.

### The window is opaque. There is no backdrop effect. *(settled v10.0)*

The v6.5 plan called for `DWMWA_SYSTEMBACKDROP_TYPE` (Mica/Acrylic) "falling
back to the current blur-behind." **That item is struck and will not be
implemented.**

`WA_TranslucentBackground` makes the top-level window `WS_EX_LAYERED` — per-pixel
alpha, software-composited. In practice that shipped as a dark semi-transparent
blurred box on launch, invisible UI sections, and tearing while dragging and
resizing. None of it raised an exception, so nothing caught it. The whole class
of defect was eliminated by making the shell **unconditionally opaque**.

The current contract, enforced by `tests/test_rendering.py` and
`tests/test_dialogs.py`:

- No `WA_TranslucentBackground`, ever. The window must never become `WS_EX_LAYERED`.
- The shell gradient tokens (`bg_grad_top` / `bg_grad_bottom`) are **solid hex**;
  an `rgba()` value there punches translucency straight back through the window
  and is rejected by `test_opaque_canvas_tokens_are_solid_hex`.
- Rounded corners come from the OS via `DWMWCP_ROUND`, not from a painted mask —
  so they are real, correctly anti-aliased, and shadowed by DWM.
- The window keeps `WS_THICKFRAME` for native resize, and `WM_NCCALCSIZE` must
  respect `IsZoomed` or a maximized window overhangs the work area.

Every one of these fails **silently and visually** rather than loudly, which is
why they are pinned by pixel-level and Win32-level tests rather than by review.

### Frame cost is a tested budget, not a memory *(settled v10.0)*

Whole-window render was driven ~14ms → 8.22ms → **7.41ms**. That is now held by
`tests/test_shell_budget.py` (median under a 12ms ceiling, plus a
cache-identity guard), because a doubling of frame cost is invisible in review
and obvious to the hand on a 180Hz display.

---

## Shipped

Condensed record of what the earlier phases delivered.

**Trust & hardening (v6.1)** — UPX disabled in the PyInstaller recipe (packed
executables are a classic AV false-positive heuristic); the `##PULSE##SUCCESS|` /
`##PULSE##ERROR|` sentinel verdict contract, scanned backwards so stray tool
output can never spoof a result; log relocation to `%LOCALAPPDATA%\Pulse\logs\`
with 5MB rotation.

**Resilience & feel (v6.5 → v10.0)** — the regression suite promoted to `tests/`
with preference isolation; `Ctrl+K` command palette over `menu_structure.py`;
the full v10 keyboard layer (grid navigation, module jumps, filter, shortcut
sheet) with `SHORTCUTS` as the single source of truth for both bindings and the
help sheet; read-only applied-state probe (`11-StateProbe.ps1`) surfacing an
`APPLIED` chip; semantic per-module accent tokens that resolve differently per
theme so light mode clears contrast floors; opaque shell + native DWM corners.

**Automation (v10.0)** — GitHub Actions CI on `windows-latest`: PowerShell parse
check, PSScriptAnalyzer at zero findings against a documented rule set, and the
full pytest suite with a floor assertion so a headless runner can't silently
reduce coverage to a quarter of the suite and still report green.

---

## Phase 1 · Engine resilience — **shipped v10.2**

*Goal: an engine that cannot leak processes, and backend code that is tested
rather than merely linted.*

- [x] **Job Object kill guarantee.** The PowerShell child is assigned to a
  Windows Job Object (`KILL_ON_JOB_CLOSE`) at spawn via ctypes, with
  `taskkill /T /F` kept as the fallback for machines where the job cannot be
  created. `tests/test_process_job.py` demonstrates the old defect directly:
  an orphaned grandchild survives `taskkill /T /F` and is killed by the job.
  **The success path disarms kill-on-close first** — several tasks end by
  launching something for the user (`cleanmgr.exe`, a restarted
  `explorer.exe`), and closing an armed job would destroy them. *(v10.2)*
- [x] **Pester coverage for the backup/restore subsystem.** 19 tests over
  `Backup`/`Restore-OriginalRegValue` and `Backup-ServiceState`, pinning
  first-write-wins, the `__NOTSET__` sentinel, `-WhatIf` inertness and the
  failure-reporting contract. Redirected to a throwaway hive so the user's
  real rollback data is never touched. Found and fixed a latent defect in
  `Restore-OriginalRegValue` (see below). *(v10.2)*
- [x] **Confirm-on-close while a task is running.** `CloseConfirmDialog` names
  the running task; the safe choice is the default so a reflexive Enter or
  Escape cannot end a long install. *(v10.2)*

### Fixed along the way

**`Restore-OriginalRegValue` could never report failure.** `$DefaultIfMissing`
is declared `[string]`, and PowerShell coerces an unsupplied `[string]`
parameter's `$null` default to the **empty string** — so the guard
`if ($null -eq $Value) { return $false }` was unreachable. A caller that
passed no default for a value with no snapshot fell through to `Set-RegValue`,
wrote `""` (stored as `0` in a DWord target), and returned `$true`.
`Reset-AllTweaksToDefaults` gates its green "reverted" line on that return
value, so the user would have been told a setting was restored while it was
being zeroed. Latent today because every current call site passes a default.

## Phase 2 · Release engineering

*Goal: a build a stranger can download without a SmartScreen warning.*

- [ ] **Code signing** via Azure Trusted Signing. Sign `Pulse.exe` *and* the
  `.ps1` modules (`Set-AuthenticodeSignature`) so `AllSigned` execution policies
  can run the engine.
- [ ] **One-dir + signed installer** (Inno Setup or MSIX) as the primary channel,
  portable ZIP as secondary. One-file self-extraction to `%TEMP%` is slower and
  another AV heuristic.
- [ ] **CI release builds** with published `SHA256SUMS`, pre-release VirusTotal
  scan, and proactive submission to Microsoft's false-positive portal. The CI
  workflow added in v10.0 is the foundation this builds on.

## Phase 3 · Completing the state story

*Goal: every card that CAN honestly report its own state does.*

- [x] **Probe coverage extended** to the remaining readable tasks —
  `NetworkOptimization`, `RemoveEdge`, `RemoveOneDrive`, `RemoveWindowsOld`,
  `RemoveBloatware`, `ApplyAllPrivacy` — under the same read-only, never-guess
  contract. *(v10.1)*
- [x] **Probe/GUI key contract test** so a probe key can never drift from the
  task name it claims to describe. *(v10.1)*
- [x] **Last run & typical duration on cards**, derived from a persisted
  per-task history. *(v10.1)*
- [ ] **Elapsed time in the state pill** — `RUNNING · 02:41`. The per-task
  duration history added in v10.1 makes a *remaining*-time estimate possible
  too, for the long installs where it actually helps.
- [ ] **Console polish** — colorized SUCCESS / ERROR / `[DRY-RUN]` lines, and
  auto-scroll that pauses while the user is scrolled up.

## Phase 4 · Orchestration — **shipped v10.3**

*Goal: from a tool you remember to use, to a system that keeps machines
healthy — repeatable, reportable, schedulable.*

- [x] **Structured verdict payloads.** Every task now emits one
  `##PULSE##META|{…}` line carrying task name, duration, dry-run/elevation
  flags and succeeded/failed/skipped counts, parsed into `TaskResult.meta`.
  Emitted from `Invoke-GuiTask`'s `finally`, so every exit path is measured —
  not just the cases routing through `Complete-GuiTask`. **A third channel
  rather than a reuse of `DATA`:** the frontend takes the *last* `DATA` line,
  so a metrics envelope sharing that channel would have shadowed the Update
  Center's version audit. `META` carries no outcome field — the
  `SUCCESS|`/`ERROR|` verdict stays the single source of truth. *(v10.3)*
- [x] **Playbooks** — declarative machine baselines as JSON, validated against
  the live catalog at load time and run one step at a time through the ordinary
  dispatcher. Ships with Gamer Rig Setup, Privacy Hardening and Post-Install
  Clean. Preview runs every step under `-WhatIf`. A failed *required* step
  halts the run; steps marked `optional` record the failure and continue.
  *(v10.3)*
- [x] **Health & drift report** — read-only snapshot (`12-HealthReport.ps1`) of
  applied-tweak drift, drives, restore-point status, startup load and system
  facts, exportable as a self-contained HTML deliverable or diffable JSON.
  Drift reuses the probe verbatim, so the report and the cards' `APPLIED`
  chips can never disagree. *(v10.3)*
- [ ] **Scheduled unattended maintenance** — recurring engine runs via Windows
  Task Scheduler, surfaced on next launch: "Since you last opened Pulse: 2 runs,
  1 warning." The metrics envelope above is what makes such a summary possible.
- [ ] **Persistent runspace** — one long-lived PowerShell host fed queued tasks,
  eliminating the per-task module-load cost a playbook pays once per step.
  Measured at ~400ms per step on the current design.

---

*Maintained by [Humam Taibeh](https://github.com/Humam-Taibeh).*
