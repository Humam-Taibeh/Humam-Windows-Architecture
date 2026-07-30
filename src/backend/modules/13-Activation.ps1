#Requires -Version 5.1
<#
.SYNOPSIS
    13-Activation.ps1 - read-only Windows & Office licence/activation status.

.DESCRIPTION
    Answers one question honestly: is Windows on this machine licensed, is
    Office licensed, and under which channel? It is a REPORT, not an
    activator.

    HARD CONTRACT, inherited from 11-StateProbe.ps1 and 12-HealthReport.ps1
    and for the same reason - this file is READ-ONLY. It reads the Software
    Licensing service's own view of the machine through WMI and formats it.
    Nothing here installs a product key, contacts a KMS host, edits the
    licensing store or shells out to slmgr/ospp. A "status" module that
    could change status would be lying about what it is.

    NO ELEVATION REQUIRED. Every property below is readable by a standard
    user, so an unelevated Pulse gets the same answer an elevated one does.
    The one field that genuinely needs admin (the OEM firmware key) is
    reported as $null - unknown - rather than as a false "absent", the same
    three-state honesty rule 11-StateProbe.ps1 follows.

    PERFORMANCE, and the reason naive activation scripts appear to hang:
    SoftwareLicensingProduct carries ~100 licence stubs for every edition
    and add-on this machine COULD run. Enumerating it unfiltered takes tens
    of seconds. Both queries here filter on `PartialProductKey IS NOT NULL`
    inside WMI, which narrows it to the products actually licensed here and
    returns in well under a second.

    EVERY SECTION IS INDEPENDENTLY FALLIBLE (12-HealthReport.ps1's rule): a
    locked-down WMI, Server Core, or a machine that has never had Office
    must still produce a document. Each block yields $null for its own
    section instead of failing the whole probe.
#>

# The two Software Licensing application IDs. Stable Microsoft GUIDs, not
# machine-specific: one marks a licence as "this is Windows", the other as
# "this is Office". They are what makes the WMI filter below selective.
$Script:ActivationAppIdWindows = '55c92734-d682-4d71-983e-d6ec3f16059f'
$Script:ActivationAppIdOffice  = '0ff1ce15-a989-479d-af46-f275c6370663'

function Get-ActivationStatusDetail {
    <# LicenseStatus is an integer the licensing service documents but never
       explains. It is translated ONCE, here - label, tone and a sentence
       saying what it means for the person reading it - so the GUI card, the
       console view and the log can never describe the same code
       differently. `tone` is the frontend's ok/warn/err key, not a colour;
       the hex comes from the theme tokens at paint time. #>
    param([int]$Code, [double]$RemainingMinutes = 0)

    $Remaining = ""
    if ($RemainingMinutes -gt 0) {
        $Days = [math]::Floor($RemainingMinutes / 1440)
        $Remaining = if ($Days -ge 1) { " About $Days day(s) left." } else { " Less than a day left." }
    }

    switch ($Code) {
        0 { return [PSCustomObject]@{ code = 0; label = "Unlicensed"; tone = "err"
                explanation = "No licence is applied. Windows runs with personalisation disabled and shows activation reminders." } }
        1 { return [PSCustomObject]@{ code = 1; label = "Licensed"; tone = "ok"
                explanation = "Fully activated - nothing needs doing." } }
        2 { return [PSCustomObject]@{ code = 2; label = "Initial grace period"; tone = "warn"
                explanation = "Not activated yet, but still inside the grace period Windows allows after installation.$Remaining" } }
        3 { return [PSCustomObject]@{ code = 3; label = "Out-of-tolerance grace"; tone = "warn"
                explanation = "The hardware changed enough that this licence has to be re-activated.$Remaining" } }
        4 { return [PSCustomObject]@{ code = 4; label = "Non-genuine grace"; tone = "err"
                explanation = "The licensing service reports this installation as non-genuine.$Remaining" } }
        5 { return [PSCustomObject]@{ code = 5; label = "Not activated"; tone = "err"
                explanation = "The grace period has expired and no valid licence is applied. Activation reminders and feature limits are active." } }
        6 { return [PSCustomObject]@{ code = 6; label = "Extended grace"; tone = "warn"
                explanation = "Running on an extended grace period; a valid licence is still needed.$Remaining" } }
        default { return [PSCustomObject]@{ code = $Code; label = "Unknown ($Code)"; tone = ""
                explanation = "The licensing service returned a status code this build does not recognise." } }
    }
}

function Get-ActivationChannel {
    <# SoftwareLicensingProduct.Description is a machine string such as
       "Windows(R) Operating System, VOLUME_KMSCLIENT channel". The channel
       token buried in it is the part a technician actually needs - it is
       the difference between a licence that is permanent and one that
       expires - so it is lifted out and said in plain English.

       Order matters: VOLUME_*, SUBSCRIPTION and the MAK forms are matched
       before RETAIL because a description can legitimately contain more
       than one token, and the more specific channel is the true one.
       Office volume products in particular describe themselves as
       "RETAIL(MAK)" - seen on a real machine during development - which a
       plain /RETAIL/ match would mislabel as a consumer retail licence. #>
    param([string]$Description)

    if ([string]::IsNullOrWhiteSpace($Description)) { return "Unknown" }
    switch -Regex ($Description) {
        'OEM_DM|OEM_SLP|OEM_COA'   { return "OEM - pre-installed by the manufacturer" }
        'VOLUME_KMSCLIENT'         { return "Volume licence, KMS client" }
        'VOLUME_MAK|RETAIL\(MAK\)' { return "Volume licence, MAK key" }
        'SUBSCRIPTION'             { return "Subscription (Microsoft 365)" }
        'TIMEBASED'                { return "Time-limited evaluation licence" }
        'RETAIL'                   { return "Retail licence" }
        default                    { return "Unrecognised channel" }
    }
}

function ConvertTo-ActivationProduct {
    <# One licensed product -> the flat shape the GUI renders. Both WMI
       providers (SoftwareLicensingProduct and the legacy
       OfficeSoftwareProtectionProduct) expose the same property names for
       everything read here, which is why one converter serves both. #>
    param($Product)

    $Minutes = 0
    if ($null -ne $Product.GracePeriodRemaining) { $Minutes = [double]$Product.GracePeriodRemaining }
    $Detail = Get-ActivationStatusDetail -Code ([int]$Product.LicenseStatus) -RemainingMinutes $Minutes
    $Description = [string]$Product.Description

    # A KMS, subscription or evaluation licence is leased and renews on a
    # timer; a retail, OEM or digital licence does not expire. "Is this
    # permanent?" is the most-asked question about any activation state, so
    # it is answered here rather than left for the reader to infer from a
    # channel string.
    $IsLeased = $Description -match 'VOLUME_KMSCLIENT|TIMEBASED|SUBSCRIPTION'

    $Days = $null
    if ($Minutes -gt 0) { $Days = [int][math]::Floor($Minutes / 1440) }

    return [PSCustomObject]@{
        name        = [string]$Product.Name
        description = $Description
        channel     = Get-ActivationChannel -Description $Description
        statusCode  = $Detail.code
        status      = $Detail.label
        tone        = $Detail.tone
        explanation = $Detail.explanation
        # Last five characters of the key only - exactly what Windows itself
        # shows in Settings. The full key is never read or displayed.
        partialKey  = [string]$Product.PartialProductKey
        permanent   = ($Detail.code -eq 1 -and -not $IsLeased)
        # DUAL MEANING, deliberately one field: on a LICENSED leased product
        # this is the time until re-activation is due; in any grace state it
        # is the time left before Windows starts restricting features. Both
        # answer "how long until you must act", which is the only thing a
        # reader wants from it. $null when the provider reports no timer.
        remainingDays = $Days
    }
}

function Get-WindowsActivationStatus {
    <# The Windows licence, or $null when WMI cannot be read at all. #>
    try {
        # Escaped even though the operand is a module-level constant GUID:
        # the WQL-escaping contract (test_contract.py) is enforced with no
        # "this one is safe" exemptions, because the exemption is what
        # survives into the next filter that isn't.
        $Filter = "ApplicationID = '{0}' AND PartialProductKey IS NOT NULL" -f `
            (ConvertTo-WqlLiteral $Script:ActivationAppIdWindows)
        $Products = @(Get-CimInstance -ClassName SoftwareLicensingProduct `
            -Filter $Filter -ErrorAction Stop)
        if ($Products.Count -eq 0) { return $null }
        # A machine can hold several licensed stubs at once (an upgraded
        # edition alongside the one it replaced). A licensed one is the
        # machine's real state, so it wins; otherwise take the first.
        $Chosen = $Products |
            Sort-Object @{ Expression = { if ([int]$_.LicenseStatus -eq 1) { 0 } else { 1 } } } |
            Select-Object -First 1
        return ConvertTo-ActivationProduct -Product $Chosen
    } catch {
        return $null
    }
}

function Get-OfficeActivationStatus {
    <# Two providers, because Microsoft changed licensing platform mid-life:
       Office 2013 and later (Click-to-Run and Microsoft 365 included)
       register under the SAME SoftwareLicensingProduct class Windows uses,
       keyed by the Office application ID, while Office 2010 and some 2013
       MSI builds use the separate OfficeSoftwareProtectionProduct class -
       which does not exist at all on a machine that never had them.

       Both are queried and the results merged, so a provider that is simply
       absent is never mistaken for "Office is not licensed". #>
    $Found = @()
    try {
        $Filter = "ApplicationID = '{0}' AND PartialProductKey IS NOT NULL" -f `
            (ConvertTo-WqlLiteral $Script:ActivationAppIdOffice)
        $Found += @(Get-CimInstance -ClassName SoftwareLicensingProduct `
            -Filter $Filter -ErrorAction Stop)
    } catch {
        # WMI unreadable, or no modern Office licence registered here.
    }
    try {
        $Found += @(Get-CimInstance -ClassName OfficeSoftwareProtectionProduct `
            -Filter "PartialProductKey IS NOT NULL" -ErrorAction Stop)
    } catch {
        # Class genuinely absent = no Office 2010-era install on this machine.
    }
    return @($Found | ForEach-Object { ConvertTo-ActivationProduct -Product $_ })
}

function Get-OfficeInstallInfo {
    <# Is Office INSTALLED, independent of whether it is licensed? Without
       this the report cannot tell "no Office on this machine" (nothing to
       report, and nothing wrong) apart from "Office is installed but holds
       no licence" (the actionable case) - both of which produce an empty
       licence list. #>
    $C2R = "HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration"
    try {
        if (Test-Path $C2R -ErrorAction Stop) {
            $Props = Get-ItemProperty -Path $C2R -ErrorAction Stop
            return [PSCustomObject]@{
                installed = $true
                products  = [string]$Props.ProductReleaseIds
                version   = [string]$Props.VersionToReport
                kind      = "Click-to-Run"
            }
        }
    } catch {
        return $null   # registry unreadable - unknown, not "absent"
    }
    # Legacy MSI installs leave a versioned product hive instead.
    try {
        $Legacy = @(Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Office" -ErrorAction Stop |
            Where-Object { $_.PSChildName -match '^\d+\.\d+$' -and (Test-Path "$($_.PSPath)\Common\InstallRoot") })
        if ($Legacy.Count -gt 0) {
            return [PSCustomObject]@{
                installed = $true
                products  = ""
                version   = [string](@($Legacy)[-1].PSChildName)
                kind      = "MSI (legacy)"
            }
        }
    } catch {
        return $null
    }
    return [PSCustomObject]@{ installed = $false; products = ""; version = ""; kind = "" }
}

function Get-ActivationServiceInfo {
    <# Machine-wide licensing facts that belong to no single product: the
       KMS host this machine is pointed at (empty on a normal consumer PC),
       and whether the motherboard firmware carries an OEM licence - the
       most useful thing to know when a freshly reinstalled machine reports
       itself unlicensed, because that licence re-applies itself and needs
       no key typed in at all.

       OA3xOriginalProductKey requires elevation. Unelevated it comes back
       empty, which is indistinguishable from "no firmware key" - so this
       reports $null (unknown) rather than a confident $false. #>
    try {
        $Service = Get-CimInstance -ClassName SoftwareLicensingService -ErrorAction Stop | Select-Object -First 1
        if (-not $Service) { return $null }

        $Elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        $HasFirmwareKey = $null
        if ($Elevated) { $HasFirmwareKey = -not [string]::IsNullOrWhiteSpace([string]$Service.OA3xOriginalProductKey) }

        return [PSCustomObject]@{
            kmsHost            = [string]$Service.KeyManagementServiceMachine
            firmwareKeyPresent = $HasFirmwareKey
            firmwareKeyEdition = [string]$Service.OA3xOriginalProductKeyDescription
        }
    } catch {
        return $null
    }
}

function Get-PulseActivationStatus {
    <# The whole document, as one JSON-serializable object. Presentation
       neutral: no console formatting and no verdict text, exactly like
       12-HealthReport.ps1, so the GUI dialog and the console view render
       the same facts. #>
    $OS = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue

    return [PSCustomObject]@{
        generatedAt   = (Get-Date).ToString("o")
        hostname      = $env:COMPUTERNAME
        edition       = if ($OS) { [string]$OS.Caption } else { "Unknown" }
        build         = $Script:OSBuild
        windows       = Get-WindowsActivationStatus
        office        = @(Get-OfficeActivationStatus)
        officeInstall = Get-OfficeInstallInfo
        service       = Get-ActivationServiceInfo
    }
}

function Get-ActivationSummaryLine {
    <# The one-line verdict the GUI toast and the log both carry. Built here
       rather than in the dispatcher so the console view and the GUI can
       never summarise the same report differently. #>
    param($Report)

    $Parts = @()
    if ($Report.windows) {
        $Parts += "Windows: $($Report.windows.status) ($($Report.windows.channel))"
    } else {
        $Parts += "Windows: status unreadable"
    }

    $Office = @($Report.office)
    if ($Office.Count -gt 0) {
        $Licensed = @($Office | Where-Object { $_.statusCode -eq 1 }).Count
        $Parts += "Office: $Licensed of $($Office.Count) product(s) licensed"
    } elseif ($Report.officeInstall -and $Report.officeInstall.installed) {
        $Parts += "Office: installed, no licence found"
    } else {
        $Parts += "Office: not installed"
    }
    return ($Parts -join "   -   ")
}

function Show-ActivationStatusReport {
    <# Console counterpart of the GUI card, so the standalone terminal app
       reaches the same information. Read-only, like everything above. #>
    Write-Banner "ACTIVATION STATUS"
    Write-Host "   Reading the licensing service..." -ForegroundColor DarkGray
    $Report = Get-PulseActivationStatus

    Write-Host ""
    Write-SectionHeader "WINDOWS"
    if ($Report.windows) {
        $Colour = switch ($Report.windows.tone) { "ok" { "Green" } "warn" { "Yellow" } "err" { "Red" } default { "White" } }
        Write-Host "   Edition    : $($Report.edition)" -ForegroundColor White
        Write-Host "   Status     : $($Report.windows.status)" -ForegroundColor $Colour
        Write-Host "   Channel    : $($Report.windows.channel)" -ForegroundColor White
        if ($Report.windows.partialKey) {
            Write-Host "   Key ends   : ...$($Report.windows.partialKey)" -ForegroundColor DarkGray
        }
        if ($null -ne $Report.windows.remainingDays) {
            $Label = if ($Report.windows.statusCode -eq 1) { "Renews in " } else { "Grace left" }
            Write-Host "   ${Label}: $($Report.windows.remainingDays) day(s)" -ForegroundColor DarkGray
        }
        Write-Host "   $($Report.windows.explanation)" -ForegroundColor DarkGray
    } else {
        Write-Host "   Status could not be read from the licensing service." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-SectionHeader "MICROSOFT OFFICE"
    $Office = @($Report.office)
    if ($Office.Count -gt 0) {
        foreach ($Product in $Office) {
            $Colour = switch ($Product.tone) { "ok" { "Green" } "warn" { "Yellow" } "err" { "Red" } default { "White" } }
            Write-Host "   $($Product.name)" -ForegroundColor White
            Write-Host "      Status : $($Product.status)  -  $($Product.channel)" -ForegroundColor $Colour
        }
    } elseif ($Report.officeInstall -and $Report.officeInstall.installed) {
        Write-Host "   Office is installed ($($Report.officeInstall.kind)) but reports no licence." -ForegroundColor Yellow
    } else {
        Write-Host "   No Microsoft Office installation was found on this machine." -ForegroundColor DarkGray
    }

    if ($Report.service -and $Report.service.kmsHost) {
        Write-Host ""
        Write-Host "   KMS host configured: $($Report.service.kmsHost)" -ForegroundColor DarkGray
    }
    if ($Report.service -and $Report.service.firmwareKeyPresent -eq $true) {
        Write-Host "   This motherboard carries an OEM firmware licence ($($Report.service.firmwareKeyEdition))." -ForegroundColor DarkGray
    }

    Write-Log "ACTIVATION $(Get-ActivationSummaryLine -Report $Report)"
    Write-Host ""
    Write-Divider
    Read-Host "   Press Enter to continue" | Out-Null
}
