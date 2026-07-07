#Requires -Version 5.1
<#
.SYNOPSIS
    10-Office.ps1 - Microsoft Office Deployment Tool auto-detection,
    extraction and installation (console mode only).

.DESCRIPTION
    Auto-detects the Office Deployment Tool and configuration.xml on any
    known Desktop location (handles OneDrive-redirected desktops and files
    saved with doubled extensions), extracts the ODT self-extractor when
    needed, and drives `setup.exe /configure`. Not exposed as a GUI task -
    it requires file/folder pickers, so it is reachable only from the
    interactive Software Management menu.
#>

function Get-SpecialFolderSafe {
    param([Parameter(Mandatory = $true)][int]$SpecialFolderCode)
    try {
        $Path = [Environment]::GetFolderPath($SpecialFolderCode)
        if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
        return $Path
    } catch {
        Write-Log "GetFolderPath-WARN: could not resolve special folder code $SpecialFolderCode : $($_.Exception.Message)"
        return $null
    }
}

function Find-OfficeDeploymentFolder {
    $CandidateBases = @(
        (Get-SpecialFolderSafe -SpecialFolderCode 0)
        (Get-SpecialFolderSafe -SpecialFolderCode 25)
        "$env:USERPROFILE\OneDrive\Desktop"
        "$env:PUBLIC\Desktop"
        "$env:USERPROFILE\Desktop"
    )

    $Desktops = $CandidateBases |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique |
        Where-Object {
            try { Test-Path -Path $_ -PathType Container -ErrorAction Stop } catch { $false }
        }

    foreach ($Base in $Desktops) {
        try {
            $Candidate = Join-Path -Path $Base -ChildPath "Office"
        } catch {
            continue
        }
        if (Test-OfficeFolderValid -Folder $Candidate) {
            return $Candidate
        }
    }
    return $null
}

function Find-OfficeSetupFile {
    param([string]$Folder)
    if ([string]::IsNullOrWhiteSpace($Folder)) { return $null }
    if (-not (Test-Path -Path $Folder -PathType Container -ErrorAction SilentlyContinue)) { return $null }

    $ExactNames = @("setup.exe", "setup.exe.exe", "Setup.exe", "Setup.exe.exe")
    foreach ($Name in $ExactNames) {
        try {
            $f = Join-Path -Path $Folder -ChildPath $Name
            if (Test-Path -Path $f -PathType Leaf -ErrorAction SilentlyContinue) { return $f }
        } catch {}
    }

    try {
        $Pattern = Join-Path -Path $Folder -ChildPath "officedeploymenttool*.exe"
        $Found = Get-ChildItem -Path $Pattern -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Found) { return $Found.FullName }
    } catch {}

    try {
        $AnyExe = Get-ChildItem -Path $Folder -Filter *.exe -ErrorAction SilentlyContinue
        foreach ($exe in $AnyExe) {
            if ((Test-ValidOfficeSetup -FilePath $exe.FullName) -or (Test-IsSelfExtractor -FilePath $exe.FullName)) {
                return $exe.FullName
            }
        }
    } catch {}

    return $null
}

function Find-OfficeConfigFile {
    param([string]$Folder)
    if ([string]::IsNullOrWhiteSpace($Folder)) { return $null }
    if (-not (Test-Path -Path $Folder -PathType Container -ErrorAction SilentlyContinue)) { return $null }

    $ExactNames = @("configuration.xml", "Configuration.xml", "configuration.xml.xml", "Configuration.xml.xml")
    foreach ($Name in $ExactNames) {
        try {
            $f = Join-Path -Path $Folder -ChildPath $Name
            if (Test-Path -Path $f -PathType Leaf -ErrorAction SilentlyContinue) { return $f }
        } catch {}
    }

    try {
        $AnyXml = Get-ChildItem -Path $Folder -Filter *.xml -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($AnyXml) { return $AnyXml.FullName }
    } catch {}

    return $null
}

function Test-OfficeFolderValid {
    param([string]$Folder)
    if ([string]::IsNullOrWhiteSpace($Folder)) { return $false }
    try {
        if (-not (Test-Path -Path $Folder -PathType Container -ErrorAction Stop)) { return $false }
    } catch { return $false }
    $Setup = Find-OfficeSetupFile -Folder $Folder
    $Config = Find-OfficeConfigFile -Folder $Folder
    return ($null -ne $Setup) -and ($null -ne $Config)
}

function Select-OfficeFolderDialog {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $Dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $Dialog.Description = "Select the folder containing the Office Deployment Tool and configuration.xml"
        $Dialog.ShowNewFolderButton = $false
        if ($Dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            return $Dialog.SelectedPath
        }
    } catch {
        Write-ErrorX "Could not open folder picker: $($_.Exception.Message)"
    }
    return $null
}

function Pick-FileDialog {
    param([string]$Title, [string]$Filter)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $Dialog = New-Object System.Windows.Forms.OpenFileDialog
        $Dialog.Title = $Title
        $Dialog.Filter = $Filter
        $Dialog.CheckFileExists = $true
        if ($Dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            return $Dialog.FileName
        }
    } catch {
        Write-ErrorX "Could not open file picker: $($_.Exception.Message)"
    }
    return $null
}

function Test-IsSelfExtractor {
    param([string]$FilePath)
    try {
        $result = & $FilePath /? 2>&1 | Out-String
        if ($result -match "/extract") { return $true }
    } catch {}
    return $false
}

function Test-ValidOfficeSetup {
    param([string]$FilePath)
    try {
        $result = & $FilePath /? 2>&1 | Out-String
        if ($result -match "/configure") { return $true }
    } catch {}
    return $false
}

function Invoke-ExtractSelfExtractor {
    param([string]$ExtractorPath, [string]$DestinationFolder)
    if (Test-DryRun "Extract ODT self-extractor '$ExtractorPath' to '$DestinationFolder'") { return $false }
    Write-Info "Extracting Office Deployment Tool files to '$DestinationFolder'..."
    try {
        $Argument = '/extract:' + [char]34 + $DestinationFolder + [char]34
        $Proc = Start-Process -FilePath $ExtractorPath -ArgumentList $Argument -Wait -NoNewWindow -PassThru
        if ($Proc.ExitCode -eq 0) {
            Write-Success "Extraction complete. The real setup.exe should now be in '$DestinationFolder'."
            return $true
        } else {
            Write-ErrorX "Extraction failed (exit code $($Proc.ExitCode))."
            return $false
        }
    } catch {
        Write-ErrorX "Extraction failed: $($_.Exception.Message)"
        return $false
    }
}

function Invoke-OfficeDeploymentInstall {
    param([string]$SetupExe, [string]$ConfigXml)

    if (-not (Test-ValidOfficeSetup -FilePath $SetupExe)) {
        if (Test-IsSelfExtractor -FilePath $SetupExe) {
            Write-Warn "This is the Office Deployment Tool self-extractor, not the final setup.exe."
            $ExtractDir = Split-Path $SetupExe -Parent
            if (Ask-User "Auto-Extract ODT" "Extracts the real setup.exe into the same folder ('$ExtractDir'). After extraction, installation will continue automatically.") {
                if (Invoke-ExtractSelfExtractor -ExtractorPath $SetupExe -DestinationFolder $ExtractDir) {
                    $RealSetup = Find-OfficeSetupFile -Folder $ExtractDir
                    if (-not $RealSetup) {
                        Write-ErrorX "Extraction succeeded, but could not locate the resulting setup.exe. Please check the folder and try again."
                        return "FAIL"
                    }
                    Write-Info "Using the extracted setup.exe: $RealSetup"
                    $SetupExe = $RealSetup
                } else { return "FAIL" }
            } else {
                Write-Info "You can extract it manually by running: $SetupExe /extract:`"$ExtractDir`""
                return "FAIL"
            }
        } else {
            Write-ErrorX "The selected setup.exe does not support /configure and is not a recognized self-extractor."
            Write-Warn "Please download the correct Office Deployment Tool using option [D] from the menu."
            return "FAIL"
        }
    }

    Write-Success "Office deployment files confirmed:"
    Write-Info "   Setup : $SetupExe"
    Write-Info "   Config: $ConfigXml"

    if (Ask-User "Auto-Install Office" "Runs setup.exe /configure configuration.xml. DO NOT close the setup window.") {
        if (Test-DryRun "Run '$SetupExe /configure $ConfigXml' (Office installation)") { return "OK" }
        try {
            Write-Info "Starting Office installation..."
            $Argument = '/configure ' + [char]34 + $ConfigXml + [char]34
            $Proc = Start-Process -FilePath $SetupExe -ArgumentList $Argument -Wait -NoNewWindow -PassThru
            if ($Proc.ExitCode -eq 0) {
                Write-Success "Office installation command completed. Verify Office is installed."
            } else {
                Write-ErrorX "Office installation exited with code $($Proc.ExitCode). Check your configuration."
            }
        } catch {
            Write-ErrorX "Office installation failed: $($_.Exception.Message)"
        }
    } else {
        Write-Info "You can manually run:"
        Write-Info "   $SetupExe /configure $ConfigXml"
    }
    return "OK"
}

function Show-OfficeDeployment {
    Write-Banner "MICROSOFT OFFICE DEPLOYMENT"
    Write-ModulePreview -Items @(
        "Auto-detects the Office Deployment Tool and configuration file (even with default names).",
        "If the self-extractor is present, it will be extracted automatically.",
        "Missing files can be downloaded or selected manually."
    )

    do {
        $OfficeFolder = Find-OfficeDeploymentFolder
        $SetupExe = $null
        $ConfigXml = $null
        if ($OfficeFolder) {
            $SetupExe  = Find-OfficeSetupFile -Folder $OfficeFolder
            $ConfigXml = Find-OfficeConfigFile -Folder $OfficeFolder
            if ($SetupExe -and $ConfigXml) {
                $result = Invoke-OfficeDeploymentInstall -SetupExe $SetupExe -ConfigXml $ConfigXml
                if ($result -eq "OK") {
                    break
                }
            } else {
                Write-Warn "The 'Office' folder was found, but one or both required files are missing."
            }
        } else {
            Write-Warn "Office deployment folder not found automatically."
        }

        $SetupMissing  = -not $SetupExe
        $ConfigMissing = -not $ConfigXml

        Write-Host ""
        if ($SetupMissing)   { Write-ErrorX "[X] Office Deployment Tool (setup.exe / officedeploymenttool*.exe) not found." }
        else                 { Write-Success "Office Deployment Tool found." }
        if ($ConfigMissing)  { Write-ErrorX "[X] configuration.xml not found." }
        else                 { Write-Success "configuration.xml found." }

        Write-Host ""
        Write-Host "   [D]  Download Office Deployment Tool (Self-Extractor)" -ForegroundColor White
        Write-Host "   [C]  Download Office Customization Tool (configuration.xml)" -ForegroundColor White
        Write-Host "   [B]  Browse for Office folder (smart detection)" -ForegroundColor White
        Write-Host "   [R]  Retry auto-detection" -ForegroundColor Yellow
        Write-Host "   [M]  Manual pick: choose setup.exe & configuration.xml separately" -ForegroundColor White
        Write-Host "   [X]  Return to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select option (B/D/C/R/M/X)" -Valid @('b','d','c','r','m','x')

        switch ($Choice) {
            'b' {
                $Picked = Select-OfficeFolderDialog
                if (-not $Picked) {
                    Write-Info "Folder selection cancelled."
                } else {
                    $SetupExe  = Find-OfficeSetupFile -Folder $Picked
                    $ConfigXml = Find-OfficeConfigFile -Folder $Picked
                    if ($SetupExe -and $ConfigXml) {
                        $result = Invoke-OfficeDeploymentInstall -SetupExe $SetupExe -ConfigXml $ConfigXml
                        if ($result -eq "OK") {
                            Read-Host "   Press Enter to continue"
                            return
                        }
                    } else {
                        Write-ErrorX "The selected folder does not contain both required files."
                        if (-not $SetupExe)  { Write-ErrorX "   Missing: Office Deployment Tool (setup.exe / officedeploymenttool*.exe)" }
                        else { Write-Success "   Office Deployment Tool found" }
                        if (-not $ConfigXml) { Write-ErrorX "   Missing: configuration.xml" }
                        else { Write-Success "   configuration.xml found" }
                        Write-Warn "You can download the missing items using [D] or [C], or pick them manually using [M]."
                    }
                }
                Read-Host "   Press Enter to continue"
            }
            'd' {
                Write-Info "Opening Office Deployment Tool download page..."
                Start-Process "https://www.microsoft.com/en-us/download/details.aspx?id=49117"
                Read-Host "   Press Enter to continue"
            }
            'c' {
                Write-Info "Opening Office Customization Tool (configuration.xml)..."
                Start-Process "https://config.office.com/deploymentsettings"
                Read-Host "   Press Enter to continue"
            }
            'r' {
                Write-Info "Retrying auto-detection..."
            }
            'm' {
                Write-Info "Pick the Office Deployment Tool (setup.exe or officedeploymenttool*.exe)..."
                $SetupExe = Pick-FileDialog -Title "Select Office Deployment Tool" -Filter "Executable files (*.exe)|*.exe"
                if (-not $SetupExe) {
                    Write-Info "Operation cancelled."
                    Read-Host "   Press Enter to continue"
                    continue
                }
                Write-Info "Now pick the configuration.xml file..."
                $ConfigXml = Pick-FileDialog -Title "Select configuration.xml" -Filter "XML files (*.xml)|*.xml"
                if (-not $ConfigXml) {
                    Write-Info "Operation cancelled."
                    Read-Host "   Press Enter to continue"
                    continue
                }
                $result = Invoke-OfficeDeploymentInstall -SetupExe $SetupExe -ConfigXml $ConfigXml
                if ($result -eq "OK") {
                    Read-Host "   Press Enter to continue"
                    return
                }
                Read-Host "   Press Enter to continue"
            }
            'x' { return }
        }
    } while ($true)

    Read-Host "   Press Enter to continue"
}
