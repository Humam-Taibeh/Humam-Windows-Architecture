"""
src/frontend/health_report.py

Rendering for the Health & Drift Report (v10.3).

Deliberately PURE: this module takes the backend's report dict and returns
strings. No Qt, no file dialogs, no I/O beyond the caller writing what it
gets back. That keeps the interesting part — the drift classification and
the HTML escaping — testable without a window.

THE DELIVERABLE. The HTML export is meant to be handed to a client or
attached to a ticket, so it is a single self-contained file: styles inline,
no external fonts, no scripts. It must open correctly on a machine that has
never heard of Pulse, offline, years from now.

DRIFT, DEFINED. A tweak Pulse can apply has three states, taken straight
from the read-only probe: applied, not applied, or unknown. "Unknown" is a
first-class answer — a policy-locked hive or an unelevated read genuinely
cannot be judged — and it is rendered as such rather than being folded into
"not applied", which would invent drift that may not exist.
"""
from __future__ import annotations

import html
import json
from datetime import datetime

#: Human labels for the probe's task-name keys. A key with no entry here
#: falls back to its raw task name, so a newly probed tweak still appears
#: in the report instead of vanishing until someone updates this table.
TWEAK_LABELS = {
    "DarkMode": "Global dark mode",
    "DisableMouseAccel": "Mouse acceleration disabled",
    "MinimalistTaskbar": "Minimalist taskbar",
    "ClassicContextMenu": "Classic context menu",
    "GameMode": "Game Mode & Game Bar",
    "DisableAdvertisingID": "Advertising ID disabled",
    "DisableActivityHistory": "Activity history disabled",
    "DisableTelemetry": "Telemetry disabled",
    "DisableHibernation": "Hibernation disabled",
    "EnableHibernation": "Hibernation enabled",
    "UltimatePowerPlan": "Ultimate power plan active",
    "RemoveEdge": "Microsoft Edge removed",
    "RemoveOneDrive": "OneDrive removed",
    "RemoveWindowsOld": "Windows.old removed",
    "RemoveBloatware": "Bloatware removed",
    "ApplyAllPrivacy": "Full privacy pass applied",
}

#: Free-space percentage under which a drive is called out.
LOW_DISK_PERCENT = 10
#: A restore point older than this is stale enough to mention.
STALE_RESTORE_DAYS = 30


def tweak_rows(report: dict) -> list[tuple[str, str, str]]:
    """[(label, state, task)] sorted so what needs attention reads first.

    Order is applied-last on purpose: a report is scanned, not read, and
    the rows worth acting on belong at the top.
    """
    tweaks = report.get("tweaks") or {}
    if not isinstance(tweaks, dict):
        return []
    rank = {"not-applied": 0, "unknown": 1, "applied": 2}
    rows = []
    for task, value in tweaks.items():
        if value is None:
            state = "unknown"
        elif value:
            state = "applied"
        else:
            state = "not-applied"
        rows.append((TWEAK_LABELS.get(task, task), state, task))
    return sorted(rows, key=lambda row: (rank[row[1]], row[0]))


def findings(report: dict) -> list[str]:
    """Plain-language things a technician should act on.

    Only genuine signals: an empty list means the machine looked fine on
    every axis measured, and saying so is more useful than padding the
    section with restatements of the data above it.
    """
    out: list[str] = []

    for drive in report.get("drives") or []:
        try:
            if float(drive.get("percentFree", 100)) < LOW_DISK_PERCENT:
                out.append(
                    f"Drive {drive.get('name')}: only "
                    f"{drive.get('percentFree')}% free "
                    f"({drive.get('freeGB')} GB of {drive.get('totalGB')} GB).")
        except (TypeError, ValueError):
            continue

    restore = report.get("restorePoint") or {}
    if restore.get("available") is False:
        out.append("System Restore is unavailable or could not be read — "
                   "Pulse cannot create a rollback checkpoint.")
    elif restore.get("count") == 0:
        out.append("No System Restore points exist on this machine.")
    else:
        age = restore.get("newestAgeDays")
        if isinstance(age, (int, float)) and age > STALE_RESTORE_DAYS:
            out.append(f"The newest restore point is {age:.0f} days old.")

    startup = report.get("startup") or {}
    recommended = startup.get("recommendedDisable")
    if isinstance(recommended, int) and recommended > 0:
        out.append(f"{recommended} startup item(s) are recommended for "
                   "disabling.")

    summary = report.get("tweakSummary") or {}
    unknown = summary.get("unknown")
    if isinstance(unknown, int) and unknown > 0:
        out.append(f"{unknown} setting(s) could not be read in this session "
                   "— run Pulse as Administrator for a complete picture.")

    return out


def _fmt_generated(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw).strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return raw or "unknown"


def to_json(report: dict) -> str:
    """The machine-readable export. Pretty-printed rather than compact:
    its whole purpose is to be diffed between two runs to see what moved."""
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)


def to_html(report: dict) -> str:
    """A single self-contained HTML file — no external assets of any kind.

    Every interpolated value goes through html.escape. The inputs are
    machine-derived (hostname, CPU model, restore-point descriptions) and
    therefore not attacker-controlled in any normal sense, but they are
    also not under our control, and an ampersand in a GPU name should not
    be able to produce a broken deliverable.
    """
    esc = html.escape
    system = report.get("system") or {}
    summary = report.get("tweakSummary") or {}
    restore = report.get("restorePoint") or {}
    startup = report.get("startup") or {}

    def row(label: str, value: object) -> str:
        shown = "—" if value in (None, "") else str(value)
        return f"<tr><th>{esc(label)}</th><td>{esc(shown)}</td></tr>"

    system_rows = "".join([
        row("Operating system", system.get("os")),
        row("Build", system.get("build")),
        row("Edition", system.get("edition")),
        row("Processor", system.get("cpu")),
        row("Memory",
            f"{system.get('freeRAMGB')} GB free of {system.get('totalRAMGB')} GB"
            if system.get("totalRAMGB") else None),
        row("Active power plan", system.get("powerPlan")),
        row("Uptime",
            f"{system.get('uptimeHours')} hours"
            if system.get("uptimeHours") is not None else None),
        row("PowerShell", system.get("psVersion")),
    ])

    drive_rows = "".join(
        f"<tr><th>Drive {esc(str(d.get('name')))}</th>"
        f"<td>{esc(str(d.get('freeGB')))} GB free of "
        f"{esc(str(d.get('totalGB')))} GB "
        f"<span class='muted'>({esc(str(d.get('percentFree')))}% free)</span></td></tr>"
        for d in (report.get("drives") or [])
    ) or "<tr><td colspan='2' class='muted'>No drive data available.</td></tr>"

    if restore.get("available") is False:
        restore_text = "Unavailable or unreadable in this session"
    elif restore.get("count") == 0:
        restore_text = "No restore points"
    else:
        age = restore.get("newestAgeDays")
        age_text = f"{age:.0f} days old" if isinstance(age, (int, float)) else "age unknown"
        restore_text = (f"{restore.get('count')} point(s); newest "
                        f"{age_text} ({restore.get('newestDescription') or 'unnamed'})")

    startup_text = (
        f"{startup.get('enabled')} enabled of {startup.get('total')} total"
        if startup.get("total") is not None else "Unavailable")

    state_labels = {"applied": "Applied", "not-applied": "Not applied",
                    "unknown": "Unknown"}
    tweak_html = "".join(
        f"<tr><th>{esc(label)}</th>"
        f"<td><span class='pill {state}'>{state_labels[state]}</span></td></tr>"
        for label, state, _task in tweak_rows(report)
    ) or "<tr><td colspan='2' class='muted'>No tweak state available.</td></tr>"

    found = findings(report)
    findings_html = ("<ul>" + "".join(f"<li>{esc(f)}</li>" for f in found) + "</ul>"
                     if found else
                     "<p class='ok-note'>Nothing needing attention was found.</p>")

    hostname = esc(str(report.get("hostname") or "unknown"))
    generated = esc(_fmt_generated(str(report.get("generatedAt") or "")))
    elevated = ("elevated" if report.get("elevated")
                else "not elevated — some values may read as unknown")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pulse Health Report — {hostname}</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 20px;
    font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f6f7fb; color: #1b1f2a;
  }}
  .sheet {{ max-width: 820px; margin: 0 auto; background: #fff;
    border: 1px solid #e2e6ef; border-radius: 14px; padding: 32px 34px; }}
  h1 {{ margin: 0 0 4px; font-size: 22px; letter-spacing: -.01em; }}
  .sub {{ color: #5b6478; font-size: 13px; margin-bottom: 26px; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .08em;
    color: #5b6478; margin: 28px 0 10px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 7px 0; vertical-align: top;
    border-bottom: 1px solid #eef1f6; font-weight: 400; }}
  th {{ width: 42%; color: #5b6478; font-weight: 500; }}
  .muted {{ color: #8b93a5; }}
  .pill {{ display: inline-block; padding: 1px 9px; border-radius: 999px;
    font-size: 12px; font-weight: 600; }}
  .applied {{ background: #e4f5ea; color: #1a7f37; }}
  .not-applied {{ background: #fdeaea; color: #b42318; }}
  .unknown {{ background: #eef1f6; color: #667085; }}
  ul {{ margin: 6px 0 0; padding-left: 20px; }}
  li {{ margin: 4px 0; }}
  .ok-note {{ color: #1a7f37; margin: 6px 0 0; }}
  .totals {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 4px; }}
  .totals div {{ background: #f2f4f9; border-radius: 10px; padding: 10px 14px;
    min-width: 96px; }}
  .totals b {{ display: block; font-size: 19px; }}
  footer {{ margin-top: 30px; color: #8b93a5; font-size: 12px;
    border-top: 1px solid #eef1f6; padding-top: 14px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #14171f; color: #e6e9f0; }}
    .sheet {{ background: #1a1e28; border-color: #2a3040; }}
    th, td {{ border-bottom-color: #242a37; }}
    th, .sub, h2, .muted, footer {{ color: #98a1b5; }}
    .totals div {{ background: #212734; }}
    .applied {{ background: #16301f; color: #56d07f; }}
    .not-applied {{ background: #3a1c1c; color: #f28b82; }}
    .unknown {{ background: #262c39; color: #98a1b5; }}
    .ok-note {{ color: #56d07f; }}
  }}
  @media print {{ body {{ background: #fff; padding: 0; }}
    .sheet {{ border: none; }} }}
</style>
</head>
<body>
<div class="sheet">
  <h1>System Health &amp; Drift Report</h1>
  <div class="sub">{hostname} · generated {generated} · {esc(elevated)}</div>

  <h2>Findings</h2>
  {findings_html}

  <h2>Configuration drift</h2>
  <div class="totals">
    <div><b>{esc(str(summary.get('applied', 0)))}</b>applied</div>
    <div><b>{esc(str(summary.get('notApplied', 0)))}</b>not applied</div>
    <div><b>{esc(str(summary.get('unknown', 0)))}</b>unknown</div>
  </div>
  <table>{tweak_html}</table>

  <h2>System</h2>
  <table>{system_rows}</table>

  <h2>Storage</h2>
  <table>{drive_rows}</table>

  <h2>Resilience</h2>
  <table>
    {row("System Restore", restore_text)}
    {row("Startup programs", startup_text)}
  </table>

  <footer>Generated by Pulse — Windows System Orchestrator.
  Values marked unknown could not be read in the session that produced this
  report; running elevated resolves most of them.</footer>
</div>
</body>
</html>
"""
