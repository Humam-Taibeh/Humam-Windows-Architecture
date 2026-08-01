#Requires -Version 5.1
<#
.SYNOPSIS
    01-Catalogs.ps1 - the single source of truth for ALL backend data.

.DESCRIPTION
    Data-driven design contract: adding an app, tweak, service, bloat package
    or developer tool = adding ONE entry here. No bespoke functions.

    Frontend mirror contract: the $Apps_* and $Runtimes arrays below MUST be
    mirrored exactly (same IDs, same order) by the `apps` lists in
    src/frontend/menu_structure.py - this file is the source of truth for
    what winget ID each entry installs; the GUI list is only its mirror.

    Contains zero functions and zero side effects - pure data.
#>

# ============================================================
#  TWEAK CATALOG (Data-Driven Tweak Engine input)
# ============================================================
$Script:TweakCatalog = @(
    @{
        Key         = "DarkMode"
        Category    = "Personalization"
        Description = "Switches Windows to dark theme (apps + system)."
        # Theme registry writes don't repaint the running shell on their own,
        # so the taskbar/other surfaces glitch until the next sign-in. This
        # flag makes Invoke-Tweak fire Invoke-ShellThemeRefresh after applying,
        # so Dark/Light takes effect cleanly and immediately.
        RefreshShell = $true
        Entries = @(
            @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"; Name = "AppsUseLightTheme";   OnValue = 0; OffValue = 1; Type = "DWord" }
            @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"; Name = "SystemUsesLightTheme"; OnValue = 0; OffValue = 1; Type = "DWord" }
        )
    },
    @{
        Key         = "GameMode"
        Category    = "Performance"
        Description = "Optimizes Windows for gaming, kills background recording."
        Entries = @(
            @{ Path = "HKCU:\Software\Microsoft\GameBar";           Name = "AllowAutoGameMode";               OnValue = 1; OffValue = 0; Type = "DWord" }
            @{ Path = "HKCU:\Software\Microsoft\GameBar";           Name = "AutoGameModeEnabled";              OnValue = 1; OffValue = 0; Type = "DWord" }
            @{ Path = "HKCU:\System\GameConfigStore";               Name = "GameDVR_Enabled";                  OnValue = 0; OffValue = 1; Type = "DWord" }
            @{ Path = "HKCU:\System\GameConfigStore";               Name = "GameDVR_FSEBehaviorMode";          OnValue = 2; OffValue = 0; Type = "DWord" }
            @{ Path = "HKCU:\System\GameConfigStore";               Name = "GameDVR_HonorUserFSEBehaviorMode"; OnValue = 1; OffValue = 0; Type = "DWord" }
            @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR"; Name = "AppCaptureEnabled";   OnValue = 0; OffValue = 1; Type = "DWord" }
        )
    }
)

# ============================================================
#  APP CATALOG (mirrored by menu_structure.py)
# ============================================================
$Apps_Basic = @(
    @("Google.Chrome", "Google Chrome"), @("Brave.Brave", "Brave Browser"),
    @("Mozilla.Firefox", "Mozilla Firefox"), @("Microsoft.Edge", "Microsoft Edge"),
    @("Telegram.TelegramDesktop", "Telegram Desktop"),
    @("Spotify.Spotify", "Spotify (Win32)"),
    @("Discord.Discord", "Discord"), @("9NKSQCEZVDDB", "WhatsApp (Store)"),
    @("9PKTQ5699M62", "iCloud (Store)"), @("Apple.iTunes", "iTunes"),
    @("7zip.7zip", "7-Zip"), @("VideoLAN.VLC", "VLC Media Player"),
    @("TheDocumentFoundation.LibreOffice", "LibreOffice"), @("Notion.Notion", "Notion")
)
$Apps_Gaming = @(
    @("Valve.Steam", "Steam"), @("EpicGames.EpicGamesLauncher", "Epic Games"),
    @("RockstarGames.Launcher", "Rockstar Games"), @("BlueStacks.BlueStacks", "BlueStacks 5")
)
# Notion lives in $Apps_Basic (Browsers & Daily Apps) - it's a daily
# productivity app, not a hardware tool; this catalog stays purely
# diagnostic/monitoring utilities.
$Apps_Tools = @(
    @("CPUID.CPU-Z", "CPU-Z"), @("TechPowerUp.GPU-Z", "GPU-Z"),
    @("CPUID.HWMonitor", "HWMonitor"), @("CrystalDewWorld.CrystalDiskInfo", "CrystalDiskInfo"),
    @("Guru3D.Afterburner", "MSI Afterburner")
)
# Word/Excel/PowerPoint/Outlook/OneNote/Access/Publisher ship as ONE
# Click-to-Run bundle with no per-app winget package - the only winget
# option ("Microsoft.Office") just runs the ODT with Microsoft's stock
# default config, giving up the configuration.xml control the ODT wizard
# (InstallOfficeODT task, 10-Office.ps1's Invoke-GuiOfficeODTInstall,
# widgets.OfficeWizardDialog) exists specifically to preserve - so Office
# itself is NOT in this catalog. Microsoft Teams was dropped from the
# catalog entirely. OneDrive DOES ship as a real standalone winget
# package; in the GUI its install/restore lives beside Purge OneDrive
# under System Tools & Utilities (RestoreOneDrive task), and the console
# App Deployment Hub still exposes it here as a Smart-Deploy category.
# Leading comma forces the single-element array to stay nested (same
# PowerShell flattening pitfall documented for $Apps_DevContainers).
$Apps_OfficeCompanions = ,@("Microsoft.OneDrive", "Microsoft OneDrive")
$Runtimes = @(
    @("Microsoft.DirectX", "DirectX End-User Runtime"),
    @("Microsoft.VCRedist.2015+.x64", "Visual C++ Redistributables"),
    @("Microsoft.DotNet.DesktopRuntime.8", ".NET Desktop Runtime"),
    @("Oracle.JavaRuntimeEnvironment", "Java Runtime Environment")
)

# ============================================================
#  DEVELOPER & UNIVERSITY HUB CATALOG
#  Precisely separated from every other app list above - zero hardware
#  drivers, zero general-purpose apps. Grouped into five sections purely
#  for section headers; $Apps_DevHubAll (the flat concatenation, order
#  preserved) is what Smart-Deploy/bulk-install actually iterates, and it
#  is also the "Development & Tools" slice of $Apps_CatalogAll. Mirrored
#  group-for-group by SOFTWARE_CATALOG's "development" section in
#  menu_structure.py.
# ============================================================
$Apps_DevRuntimes = @(
    @("Python.Python.3.12", "Python 3.12"),
    @("EclipseAdoptium.Temurin.21.JDK", "Java JDK (Temurin 21)"),
    @("OpenJS.NodeJS.LTS", "Node.js (LTS)"),
    @("Git.Git", "Git / Git Bash"),
    @("MSYS2.MSYS2", "GCC / MinGW-w64 Compiler")
)
$Apps_DevIDEs = @(
    @("Microsoft.VisualStudioCode", "VS Code"),
    @("Anysphere.Cursor", "Cursor IDE"),
    @("JetBrains.PyCharm.Community", "PyCharm Community"),
    @("JetBrains.IntelliJIDEA.Community", "IntelliJ IDEA Community"),
    @("Apache.NetBeans", "NetBeans IDE")
)
$Apps_DevAI = @(
    @("Ollama.Ollama", "Ollama (Local LLM Runner)"),
    @("OpenWebUI.OpenWebUI", "Open WebUI (Local Chat Interface)")
)
$Apps_DevData = @(
    @("DBeaver.DBeaver.Community", "DBeaver (Database Client)"),
    @("Postman.Postman", "Postman (API Client)"),
    @("Bruno.Bruno", "Bruno (Open-Source API Client)")
)
# Leading comma is deliberate, not a typo: `@( @("id","name") )` - a
# single inner array as the ONLY content of the outer @() - is a classic
# PowerShell flattening pitfall; PowerShell unwraps it to a flat 2-element
# array instead of a 1-element array containing a 2-tuple. `,@(...)` (the
# unary comma/array-construction operator) forces it to stay nested.
$Apps_DevContainers = ,@("Docker.DockerDesktop", "Docker Desktop")
$Apps_DevHubAll = @() + $Apps_DevRuntimes + $Apps_DevIDEs + $Apps_DevAI + $Apps_DevData + $Apps_DevContainers

# ============================================================
#  UNIFIED SOFTWARE CATALOG (v1.0 RC)
#  THE flat list every GUI software install now deploys from - the single
#  hub behind menu_structure.py's SOFTWARE_CATALOG and the one
#  InstallCatalogApps dispatcher case.
#
#  Software Management used to spread installable apps across FOUR
#  separate cards (Essential Apps / Dev Hub / Gaming / Diagnostics) with
#  four dispatcher cases and four selector dialogs, so "where do I get
#  Docker" and "where do I get VLC" had different answers and nothing let
#  you pick one of each in a single pass. The GUI now shows one catalog
#  with a sub-category tab bar; the tabs are a VIEW over this list, not
#  four lists wearing a hat.
#
#  Order is load-bearing: it mirrors SOFTWARE_CATALOG's section order
#  (Browsers & Media -> Development & Tools -> Gaming Launchers ->
#  System Runtimes & Utilities) so the deploy log reads in the same order
#  the user saw on screen. The per-category arrays above are NOT dead -
#  console mode's App Deployment Hub (20-Menus.ps1) still walks them
#  category by category, which is the right shape for a numbered text
#  menu; only the GUI collapsed to one catalog.
# ============================================================
$Apps_CatalogAll = @() + $Apps_Basic + $Apps_DevHubAll + $Apps_Gaming + $Runtimes + $Apps_Tools

# The two hardware-matched EXTRAS Smart-Deploy can append (a GPU vendor
# suite, a motherboard suite) used to ride along unconditionally with the
# Gaming and Diagnostics cards respectively. In a unified catalog they
# must stay tied to INTENT, not fire on every deploy: picking only VLC
# should not silently pull in an NVIDIA suite. InstallCatalogApps appends
# each extra only when the user's selection actually intersects the list
# that promised it.
$Script:CatalogGpuExtraTriggerIds  = @($Apps_Gaming | ForEach-Object { $_[0] })
$Script:CatalogMoboExtraTriggerIds = @($Apps_Tools  | ForEach-Object { $_[0] })

# Pre-configured quick-select bundles for the Software Catalog's
# Development & Tools tab - each just ticks the listed AppIds; nothing is
# forced, the user can still deselect any of them before deploying.
# Mirrored by CATALOG_BUNDLES in menu_structure.py.
$Script:DevHubBundles = @(
    @{ Key = "java-university"; Icon = "🎓"; Title = "Java / University Stack"
       AppIds = @("EclipseAdoptium.Temurin.21.JDK", "Apache.NetBeans", "JetBrains.IntelliJIDEA.Community", "Git.Git", "Microsoft.VisualStudioCode") }
    @{ Key = "ai-python"; Icon = "🧠"; Title = "AI / Python Stack"
       AppIds = @("Python.Python.3.12", "Ollama.Ollama", "OpenWebUI.OpenWebUI", "Microsoft.VisualStudioCode") }
    @{ Key = "web-dev"; Icon = "🌐"; Title = "Web Dev Stack"
       AppIds = @("OpenJS.NodeJS.LTS", "Git.Git", "Microsoft.VisualStudioCode", "Postman.Postman") }
)

# Smart dependency hints for the Dev Hub selector UI (surfaced as a caption
# under the IDE's row - "subtly suggests", never auto-forces a checkbox).
# Distinct from $Script:DevDependencyMap below, which is the POST-INSTALL
# offer console/GUI tasks make after a successful deploy - this one drives
# the selector's UI before anything is installed.
$Script:DevHubDependencyHints = @{
    "JetBrains.PyCharm.Community"     = @{ RequiresId = "Python.Python.3.12";            RequiresName = "Python 3.12" }
    "JetBrains.IntelliJIDEA.Community" = @{ RequiresId = "EclipseAdoptium.Temurin.21.JDK"; RequiresName = "Java JDK" }
    "Apache.NetBeans"                 = @{ RequiresId = "EclipseAdoptium.Temurin.21.JDK"; RequiresName = "Java JDK" }
}

# ============================================================
#  APP DOWNLOAD FALLBACK URLS
# ============================================================
$Script:DownloadUrls = @{
    "Google.Chrome"                 = "https://www.google.com/chrome/"
    "Brave.Brave"                   = "https://brave.com/download/"
    "Mozilla.Firefox"               = "https://www.mozilla.org/firefox/new/"
    "Microsoft.Edge"                = "https://www.microsoft.com/en-us/edge/download"
    "Telegram.TelegramDesktop"      = "https://telegram.org/apps"
    "Spotify.Spotify"               = "https://www.spotify.com/download"
    "Discord.Discord"               = "https://discord.com/download"
    "Apple.iTunes"                  = "https://www.apple.com/itunes/download/"
    "Valve.Steam"                   = "https://store.steampowered.com/about/"
    "EpicGames.EpicGamesLauncher"   = "https://store.epicgames.com/en-US/download"
    "RockstarGames.Launcher"        = "https://socialclub.rockstargames.com/rockstar-games-launcher"
    "BlueStacks.BlueStacks"         = "https://www.bluestacks.com/download.html"
    "CPUID.CPU-Z"                   = "https://www.cpuid.com/softwares/cpu-z.html"
    "TechPowerUp.GPU-Z"             = "https://www.techpowerup.com/gpuz/"
    "CPUID.HWMonitor"               = "https://www.cpuid.com/softwares/hwmonitor.html"
    "CrystalDewWorld.CrystalDiskInfo" = "https://crystalmark.info/en/software/crystaldiskinfo/"
    "Guru3D.Afterburner"            = "https://www.msi.com/Landing/afterburner/graphics-cards"
    "Notion.Notion"                 = "https://www.notion.so/desktop"
    "Anysphere.Cursor"              = "https://cursor.sh/"
    "Microsoft.VisualStudioCode"    = "https://code.visualstudio.com/download"
    "JetBrains.PyCharm.Community"   = "https://www.jetbrains.com/pycharm/download/"
    "Apache.NetBeans"               = "https://netbeans.apache.org/download/index.html"
    "MSYS2.MSYS2"                   = "https://www.msys2.org/"
    "Ollama.Ollama"                 = "https://ollama.com/download"
    "7zip.7zip"                     = "https://www.7-zip.org/download.html"
    "VideoLAN.VLC"                  = "https://www.videolan.org/vlc/"
    "Microsoft.OneDrive"            = "https://www.microsoft.com/microsoft-365/onedrive/download"
    "TheDocumentFoundation.LibreOffice" = "https://www.libreoffice.org/download/download/"
    "Python.Python.3.12"            = "https://www.python.org/downloads/"
    "EclipseAdoptium.Temurin.21.JDK" = "https://adoptium.net/temurin/releases/"
    "OpenJS.NodeJS.LTS"              = "https://nodejs.org/en/download"
    "Git.Git"                        = "https://git-scm.com/downloads"
    "JetBrains.IntelliJIDEA.Community" = "https://www.jetbrains.com/idea/download/"
    "OpenWebUI.OpenWebUI"            = "https://openwebui.com/"
    "DBeaver.DBeaver.Community"      = "https://dbeaver.io/download/"
    "Postman.Postman"                = "https://www.postman.com/downloads/"
    "Bruno.Bruno"                    = "https://www.usebruno.com/downloads"
    "Docker.DockerDesktop"           = "https://www.docker.com/products/docker-desktop/"
}

# ============================================================
#  PROCESSES THAT LOCK THEIR OWN INSTALLERS
# ============================================================
$Script:LockProcessMap = @{
    "Discord.Discord"            = @("Discord", "DiscordCanary", "DiscordPTB")
    "Anysphere.Cursor"           = @("Cursor")
    "Microsoft.VisualStudioCode" = @("Code")
    "Spotify.Spotify"            = @("Spotify")
    "Valve.Steam"                = @("steam", "steamwebhelper")
    "Microsoft.OneDrive"         = @("OneDrive")
    "Docker.DockerDesktop"       = @("Docker Desktop", "com.docker.backend", "com.docker.build")
    # MSYS2's installer is a shell-executed process (winget exit code
    # -1978335226 / SHELLEXEC_INSTALL_FAILED when it fails) - a leftover
    # MSYS2/MinGW terminal or pacman process holding files open is the most
    # common real-world cause. Pre-emptively closing them avoids the
    # conflict instead of just reporting a cryptic failure afterward.
    "MSYS2.MSYS2"                = @("mintty", "bash", "pacman")
    # msedge.exe/msedgewebview2.exe hold their own binaries open - a
    # running browser (or a background WebView2 host another app spawned)
    # blocks both a fresh install and an upgrade over itself.
    "Microsoft.Edge"             = @("msedge", "msedgewebview2")
}

# ============================================================
#  APP IDS THAT MUST ALWAYS RE-RUN THE INSTALLER, EVEN WHEN winget
#  REPORTS THE SAME VERSION AS "LATEST"
#  Windows keeps Microsoft Edge registered under the SAME winget Id
#  (Microsoft.Edge) whether it's the user-managed standalone install OR
#  the protected inbox/OS-component stub some builds fall back to after
#  Remove-MicrosoftEdge (06-Tweaks.ps1) removes the standalone copy - so
#  `winget list --id Microsoft.Edge` can still report a "current" version
#  right after a clean removal. Smart-Deploy's normal
#  CurrentVersion-equals-LatestVersion fast path would then silently skip
#  the reinstall entirely (the exact "installs do nothing" bug this list
#  exists to close) - AppIds here bypass that fast path and always run a
#  forced `winget install` instead.
# ============================================================
$Script:AlwaysForceReinstallAppIds = @(
    "Microsoft.Edge"
)

# ============================================================
#  ELEVATION-PROHIBITED APP IDS
#  Packages whose installer manifest sets "elevationProhibited" - winget
#  itself reports these with exit code -1978335146 / 0x8A150056
#  (APPINSTALLER_CLI_ERROR_INSTALLER_PROHIBITS_ELEVATION) the instant it's
#  run under an Administrator token, no matter what flags are passed
#  (confirmed against winget-cli's own AppInstallerErrors.h and
#  microsoft/winget-pkgs#210448 - "--scope user" does NOT bypass this; the
#  installer refuses before scope is even evaluated). Pulse's console mode
#  always self-elevates (core.ps1) and the GUI has no de-elevate button
#  (only elevate), so this is a real, reachable failure - not a corner
#  case. Listing known offenders here lets Smart-Deploy skip the doomed
#  winget call up front instead of burning a failed attempt + a force
#  retry, both guaranteed to hit the same wall. Resolve-WingetExitCode in
#  04-SoftwareEngine.ps1 still handles the code correctly for any AppId
#  NOT listed here (e.g. a future catalog addition) - this list is a
#  latency/log-noise optimization, not the actual safety net.
# ============================================================
$Script:KnownElevationProhibitedAppIds = @(
    "Spotify.Spotify"
)

# ============================================================
#  DEVELOPER AUTO-PATHING (post-install PATH registration)
# ============================================================
$Script:DevAppPaths = @{
    "JetBrains.PyCharm.Community" = @{ Name = "PyCharm";  ExeName = "pycharm64.exe" }
    "Anysphere.Cursor"            = @{ Name = "Cursor";   ExeName = "Cursor.exe" }
    "Apache.NetBeans"             = @{ Name = "NetBeans";  ExeName = "netbeans64.exe" }
    "MSYS2.MSYS2"                 = @{ Name = "MSYS2";    ExeName = "bash.exe" }
}

# ============================================================
#  DEV DEPENDENCY SUGGESTIONS (post-install helper data)
# ============================================================
$Script:DevDependencyMap = @{
    "JetBrains.PyCharm.Community" = @{
        CommandName  = "python"
        FriendlyName = "Python"
        WingetId     = "Python.Python.3.12"
        Url          = "https://www.python.org/downloads/"
    }
    "Apache.NetBeans" = @{
        CommandName  = "javac"
        FriendlyName = "JDK (Eclipse Temurin 21)"
        WingetId     = "EclipseAdoptium.Temurin.21.JDK"
        Url          = "https://adoptium.net/temurin/releases/"
    }
}

# ============================================================
#  DEV TOOL CATALOG (Verify-Environment input)
#  Command : what must resolve on PATH (checked as .exe/.cmd/.bat)
#  Probes  : well-known install directories (wildcards allowed) searched
#            when the command is NOT on PATH; a hit is auto-added to the
#            user PATH. Order matters: first hit wins, newest-first within
#            a wildcard.
#  EnvVarName : optional companion variable (e.g. JAVA_HOME) set to the
#            tool's home directory (parent of its bin dir) when absent.
# ============================================================
# `Why` is the plain-language reason PATH matters for this tool - shown by
# Verify-Environment (03-Environment.ps1) so "PATH doctor" reads like a
# helpful assistant explaining itself, not a cryptic systems tool.
$Script:DevToolCatalog = @(
    @{ Command = "git";    Name = "Git";        WingetId = "Git.Git"
       Why     = "so any terminal or IDE can run git for you - version control that just works, everywhere."
       Probes  = @("$env:ProgramFiles\Git\cmd", "${env:ProgramFiles(x86)}\Git\cmd", "$env:LOCALAPPDATA\Programs\Git\cmd") }
    @{ Command = "python"; Name = "Python";     WingetId = "Python.Python.3.12"
       Why     = "so typing 'python' in any terminal runs it, instead of only from its install folder."
       Probes  = @("$env:LOCALAPPDATA\Programs\Python\Python3*", "$env:ProgramFiles\Python3*") }
    @{ Command = "javac";  Name = "Java JDK";   WingetId = "EclipseAdoptium.Temurin.21.JDK"; EnvVarName = "JAVA_HOME"
       Why     = "so 'javac'/'java' work everywhere, and JAVA_HOME lets IDEs like NetBeans/IntelliJ find your JDK automatically."
       Probes  = @("$env:ProgramFiles\Eclipse Adoptium\jdk*\bin", "$env:ProgramFiles\Java\jdk*\bin", "$env:ProgramFiles\Microsoft\jdk*\bin") }
    @{ Command = "code";   Name = "VS Code";    WingetId = "Microsoft.VisualStudioCode"
       Why     = "so typing 'code' in a terminal opens VS Code right there, instead of hunting through the Start menu."
       Probes  = @("$env:LOCALAPPDATA\Programs\Microsoft VS Code\bin", "$env:ProgramFiles\Microsoft VS Code\bin") }
    @{ Command = "gcc";    Name = "GCC (MSYS2)"; WingetId = "MSYS2.MSYS2"
       Why     = "so 'gcc' works from any terminal to compile C/C++ code."
       Probes  = @("C:\msys64\mingw64\bin", "C:\msys64\ucrt64\bin") }
    @{ Command = "node";   Name = "Node.js";    WingetId = "OpenJS.NodeJS.LTS"
       Why     = "so 'node' and 'npm' work from any terminal to run JavaScript projects and install packages."
       Probes  = @("$env:ProgramFiles\nodejs") }
    @{ Command = "ollama"; Name = "Ollama";     WingetId = "Ollama.Ollama"
       Why     = "so 'ollama' works from any terminal to run local AI models."
       Probes  = @("$env:LOCALAPPDATA\Programs\Ollama") }
)

# ============================================================
#  SERVICES OPTIMIZER CATALOG
# ============================================================
$Script:OptionalServices = @(
    @{ Name = "Fax";                                       Label = "Fax";                              Note = "Legacy fax service. Safe to disable on virtually all modern PCs." }
    @{ Name = "RemoteRegistry";                             Label = "Remote Registry";                  Note = "Allows remote registry edits. Disabled by default on most consumer PCs; safe to keep disabled." }
    @{ Name = "MapsBroker";                                 Label = "Downloaded Maps Manager";          Note = "Manages offline Windows Maps data. Safe to disable if you don't use the Maps app." }
    @{ Name = "WMPNetworkSvc";                               Label = "Windows Media Player Network Sharing"; Note = "Shares media libraries over the network. Safe to disable if unused." }
    @{ Name = "RetailDemo";                                  Label = "Retail Demo Service";               Note = "Only used for in-store demo units. Safe to disable." }
    @{ Name = "diagnosticshub.standardcollector.service";    Label = "Microsoft Diagnostics Hub";         Note = "Performance diagnostics collector used mainly by developers/Visual Studio profiling." }
    @{ Name = "SysMain";                                     Label = "SysMain (Superfetch)";              Note = "Pre-loads apps into RAM. Helpful on HDDs, often unnecessary (or counter-productive) on SSDs." }
    @{ Name = "PhoneSvc";                                    Label = "Phone Service";                     Note = "Supports cellular/'Your Phone' features. Safe to disable if you don't link an Android phone." }
)

# ============================================================
#  DEBLOAT CATALOG
# ============================================================
$Script:BloatApps = @(
    "Microsoft.3DBuilder", "Microsoft.BingFinance", "Microsoft.BingNews", "Microsoft.BingSports",
    "Microsoft.BingWeather", "Microsoft.GetHelp", "Microsoft.Getstarted", "Microsoft.MicrosoftOfficeHub",
    "Microsoft.MicrosoftSolitaireCollection", "Microsoft.MixedReality.Portal", "Microsoft.People",
    "Microsoft.SkypeApp", "Microsoft.WindowsFeedbackHub", "Microsoft.WindowsMaps", "Microsoft.Xbox.TCUI",
    "Microsoft.XboxApp", "Microsoft.XboxGameOverlay", "Microsoft.XboxGamingOverlay", "Microsoft.XboxIdentityProvider",
    "Microsoft.XboxSpeechToTextOverlay", "Microsoft.YourPhone", "Microsoft.ZuneMusic", "Microsoft.ZuneVideo",
    # --- v9.4 expansion: modern Win11 preinstalls users asked to purge ---
    "7EE7776C.LinkedInforWindows",              # LinkedIn
    "Clipchamp.Clipchamp",                      # Clipchamp video editor
    "Microsoft.OutlookForWindows",              # "new" Outlook
    "Microsoft.MicrosoftStickyNotes",           # Sticky Notes
    "MSTeams",                                  # new Microsoft Teams (personal)
    "MicrosoftWindows.Client.WebExperience"     # Widgets / Web Experience pack
)

# ============================================================
#  TELEMETRY SCHEDULED TASKS
# ============================================================
$Script:TelemetryTasks = @(
    @{ Path = "\Microsoft\Windows\Application Experience\"; Name = "Microsoft Compatibility Appraiser" },
    @{ Path = "\Microsoft\Windows\Application Experience\"; Name = "ProgramDataUpdater" },
    @{ Path = "\Microsoft\Windows\Autochk\"; Name = "Proxy" },
    @{ Path = "\Microsoft\Windows\Customer Experience Improvement Program\"; Name = "Consolidator" },
    @{ Path = "\Microsoft\Windows\Customer Experience Improvement Program\"; Name = "UsbCeip" },
    @{ Path = "\Microsoft\Windows\DiskDiagnostic\"; Name = "Microsoft-Windows-DiskDiagnosticDataCollector" }
)

# ============================================================
#  STARTUP MANAGER LOCATIONS
# ============================================================
$Script:StartupDisabledRegPath = "HKCU:\Software\Pulse\DisabledStartup"
# Where a disabled item CAME FROM (v1.0). A SUB-KEY, deliberately, not extra
# values under DisabledStartup: Get-DisabledStartupItems enumerates that key's
# values to build the disabled list, so an origin record stored alongside them
# would surface in the GUI as a phantom startup entry. Sub-keys are invisible
# to Get-ItemProperty, so this rides along without touching that contract.
#
# Value name  = "<Type>|||<Name>"  (Registry|||Acme Updater, Folder|||Foo.lnk)
# Value       = the hive path or folder the item was removed from
#
# Without it, Enable-StartupItem had nowhere to look and unconditionally
# restored to the CURRENT USER — so disabling an all-users entry (HKLM Run, or
# a shortcut in ProgramData) and re-enabling it silently narrowed its scope to
# one profile. Entries disabled by an older Pulse have no record here and fall
# back to the old per-user behaviour, which is why lookups must tolerate a miss.
$Script:StartupOriginRegPath   = "$Script:StartupDisabledRegPath\_Origins"
$Script:StartupBackupFolder    = "$env:USERPROFILE\Desktop\Pulse_StartupBackup"
# The per-user and all-users Run keys / Startup folders an item can be restored
# to. Kept as data so Enable-StartupItem can validate a recorded origin against
# a known-good set instead of writing to whatever string it read back.
$Script:StartupRunKeyPaths = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
)
$Script:StartupFolderPaths = @(
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Startup"
)
# Pre-rebrand fallback: shortcuts disabled under v5.x were moved into the old
# HTCore folder - keep them restorable after the rename to Pulse.
if (-not (Test-Path $Script:StartupBackupFolder) -and (Test-Path "$env:USERPROFILE\Desktop\HTCore_StartupBackup")) {
    $Script:StartupBackupFolder = "$env:USERPROFILE\Desktop\HTCore_StartupBackup"
}

# ============================================================
#  GUI TASKS THAT REQUIRE ADMINISTRATOR RIGHTS
#  (write HKLM / services / machine state - checked up-front by the
#  dispatcher so the user gets one clear message instead of a pile of
#  access-denied noise)
#
#  Software-install/update tasks (InstallCatalogApps, UpdateSelectedApps)
#  are deliberately NOT in this list: winget and every
#  individual installer already handle their own elevation needs (a
#  machine-scope MSI still triggers its own UAC consent prompt when it
#  genuinely needs one), and blanket-requiring Pulse itself to be
#  elevated for the whole category actively breaks user-scope/
#  elevation-prohibited packages - Spotify's installer manifest sets
#  elevationProhibited and hard-refuses under an Administrator token
#  (winget exit code -1978335146 / 0x8A150056, see
#  $Script:WingetElevationConflictCodes in 04-SoftwareEngine.ps1) - so
#  requiring admin here made it permanently un-installable rather than
#  safer. Office's ODT flow stays admin-required: it writes to
#  install roots the ODT itself expects elevated.
# ============================================================
$Script:AdminRequiredTasks = @(
    "RunSFC","CleanCache","RemoveBloatware","OptimizeDrives","RemoveWindowsOld",
    "DisableHibernation","EnableHibernation","DisableTelemetry","DisableActivityHistory",
    "NetworkOptimization","UltimatePowerPlan","RemoveOneDrive","RemoveEdge",
    "CreateRestorePoint","DriverBackup","RestoreServices","RestoreEdge","RestoreOneDrive","ApplyAllPrivacy",
    "ResetTweaks","InstallOfficeODT","InstallOfficeODTAuto",
    "StartupDisableItem","StartupEnableItem",
    # v1.0 two-way toggles. Only the two that write HKLM POLICY keys are
    # listed - a revert needs exactly the rights its apply-counterpart
    # needed, and the other six restore HKCU values an unelevated session
    # owns. Listing those would raise a needless UAC prompt to undo a
    # per-user setting; omitting these two would let the restore reach
    # HKLM and fail with access-denied instead of being blocked cleanly.
    # tests/test_contract.py::test_revert_admin_gating_matches_apply pins
    # the pairing so the two lists cannot drift apart.
    "RevertDisableTelemetry","RevertDisableActivityHistory",
    # v1.0+ Phase 2: DNS configuration lives in the adapter's
    # machine-scope settings, so both the apply and its undo need
    # elevation. Listed as a PAIR deliberately - a revert must need
    # exactly the rights its counterpart needed, or the undo blocks
    # on a machine that could perform the change.
    "SetDnsProfile","RestoreDns",
    # The shell block list is machine-scope HKLM. The SCAN
    # (ContextMenuScan) is deliberately absent - reading the menu
    # needs no rights, and gating it would prompt for elevation
    # just to look.
    "ContextMenuToggle","ContextMenuRestore"
)
