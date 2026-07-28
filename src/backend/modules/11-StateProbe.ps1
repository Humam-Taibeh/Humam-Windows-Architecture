#Requires -Version 5.1
<#
.SYNOPSIS
    11-StateProbe.ps1 - read-only "is this tweak currently applied?" probe.

.DESCRIPTION
    Pulse could always APPLY a tweak but never report whether it was
    already in effect. Every card was fire-and-forget: the only way to
    answer "did I already do this?" was to run the operation again and
    read the log. This module answers it directly, so the GUI can show an
    "Applied" state on the cards whose effect is readable.

    HARD CONTRACT - this file is READ-ONLY:
      * It MUST NOT write to the registry, touch services, create restore
        points, or mutate anything. It is invoked on a timer-ish basis by
        the GUI (on launch and after every task), so a mutating probe
        would silently re-apply tweaks behind the user's back.
      * Every check is wrapped so that a missing key, a policy-locked
        hive or an access-denied read yields $null ("unknown"), never an
        exception and never a false "applied". Unknown is a legitimate,
        honest third state - the GUI renders nothing for it rather than
        guessing.
      * It requires NO elevation. Checks that would need admin to read are
        reported as $null rather than escalating; an unelevated Pulse
        still gets a useful answer for every HKCU-based tweak.

    States returned per key: $true (applied) / $false (not applied) /
    $null (unknown - unreadable on this machine or in this session).
#>

function Test-RegValueEquals {
    <# $true when EVERY (Path, Name, Value) triple matches, $false when any
       readable one differs, $null when nothing could be read at all (the
       key doesn't exist / access denied) - so "unknown" never masquerades
       as "not applied", which would show a misleading un-Applied card. #>
    param([Parameter(Mandatory)][array]$Checks)

    $readAny = $false
    foreach ($check in $Checks) {
        $value = $null
        try {
            if (Test-Path $check.Path -ErrorAction Stop) {
                $item = Get-ItemProperty -Path $check.Path -Name $check.Name -ErrorAction Stop
                $value = $item.($check.Name)
            }
        } catch {
            continue     # unreadable entry - fall through to the next one
        }
        if ($null -eq $value) { continue }
        $readAny = $true
        if ([string]$value -ne [string]$check.Value) { return $false }
    }
    if (-not $readAny) { return $null }
    return $true
}

function Get-PulseTweakState {
    <# The full applied-state map the GUI consumes. Keys are GUI TASK NAMES
       (menu_structure.py's `task` values), so the frontend can look a card
       up directly with no translation table to drift out of sync. #>

    $state = [ordered]@{}

    # -- Global Dark Mode ------------------------------------------------
    $state["DarkMode"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"; Name = "AppsUseLightTheme";   Value = 0 },
        @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"; Name = "SystemUsesLightTheme"; Value = 0 }
    )

    # -- Mouse acceleration ----------------------------------------------
    $state["DisableMouseAccel"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKCU:\Control Panel\Mouse"; Name = "MouseSpeed";      Value = 0 },
        @{ Path = "HKCU:\Control Panel\Mouse"; Name = "MouseThreshold1"; Value = 0 },
        @{ Path = "HKCU:\Control Panel\Mouse"; Name = "MouseThreshold2"; Value = 0 }
    )

    # -- Minimalist taskbar (Windows 11 only) ----------------------------
    if ($Script:OSBuild -ge 22000) {
        $state["MinimalistTaskbar"] = Test-RegValueEquals -Checks @(
            @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name = "TaskbarAl"; Value = 0 },
            @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name = "TaskbarDa"; Value = 0 }
        )
        # Classic context menu: the tweak is the PRESENCE of the CLSID
        # InprocServer32 key with an empty default value, so absence is a
        # definite "not applied" rather than an unknown.
        $classic = "HKCU:\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
        try {
            if (Test-Path $classic -ErrorAction Stop) {
                $default = (Get-ItemProperty -Path $classic -ErrorAction Stop).'(default)'
                $state["ClassicContextMenu"] = ($default -eq "")
            } else {
                $state["ClassicContextMenu"] = $false
            }
        } catch {
            $state["ClassicContextMenu"] = $null
        }
    } else {
        $state["MinimalistTaskbar"] = $null
        $state["ClassicContextMenu"] = $null
    }

    # -- Game Mode & Game DVR --------------------------------------------
    $state["GameMode"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKCU:\Software\Microsoft\GameBar";     Name = "AutoGameModeEnabled"; Value = 1 },
        @{ Path = "HKCU:\System\GameConfigStore";         Name = "GameDVR_Enabled";     Value = 0 }
    )

    # -- Advertising ID ---------------------------------------------------
    $state["DisableAdvertisingID"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo"; Name = "Enabled"; Value = 0 }
    )

    # -- Activity history (HKLM policy; readable unelevated) -------------
    $state["DisableActivityHistory"] = Test-RegValueEquals -Checks @(
        @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System"; Name = "EnableActivityFeed"; Value = 0 }
    )

    # -- Telemetry: policy value AND the DiagTrack service ---------------
    $telemetryPolicy = Test-RegValueEquals -Checks @(
        @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection"; Name = "AllowTelemetry"; Value = 0 }
    )
    $diagTrackOff = $null
    try {
        $svc = Get-Service -Name "DiagTrack" -ErrorAction Stop
        $diagTrackOff = ($svc.StartType -eq "Disabled")
    } catch {
        # service genuinely absent => nothing left to disable
        $diagTrackOff = $true
    }
    if ($null -eq $telemetryPolicy) {
        $state["DisableTelemetry"] = $null
    } else {
        $state["DisableTelemetry"] = ($telemetryPolicy -and $diagTrackOff)
    }

    # -- Hibernation (mutually exclusive pair) ---------------------------
    # hiberfil.sys is not enumerable without admin on some systems, so a
    # failed probe is reported as unknown rather than "off".
    try {
        $hiberOn = Test-Path "$env:SystemDrive\hiberfil.sys" -ErrorAction Stop
        $state["DisableHibernation"] = (-not $hiberOn)
        $state["EnableHibernation"] = $hiberOn
    } catch {
        $state["DisableHibernation"] = $null
        $state["EnableHibernation"] = $null
    }

    # -- Ultimate power plan: active scheme carries the Pulse name -------
    try {
        $active = (powercfg /getactivescheme 2>$null | Out-String)
        if ([string]::IsNullOrWhiteSpace($active)) {
            $state["UltimatePowerPlan"] = $null
        } else {
            $state["UltimatePowerPlan"] = ($active -match "Pulse|Ultimate Performance|Humam Ultimate")
        }
    } catch {
        $state["UltimatePowerPlan"] = $null
    }

    return $state
}
