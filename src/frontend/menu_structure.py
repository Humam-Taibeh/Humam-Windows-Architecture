"""
src/frontend/menu_structure.py

SINGLE SOURCE OF TRUTH for the entire GUI menu hierarchy.

Adding a new button to the app = adding ONE dict to an `items` list below.
main.py renders whatever is defined here — no UI code changes needed.

Contract with the backend (src/backend/core.ps1 + src/backend/modules/):
    Every `task` value maps 1:1 to a `switch ($TaskName)` case inside the
    Invoke-GuiTask dispatcher (src/backend/modules/30-GuiDispatcher.ps1,
    loaded by core.ps1), which must emit exactly one final
    `##PULSE##SUCCESS|message` or `##PULSE##ERROR|message` verdict line on
    stdout. The GUI always invokes core.ps1 itself - never a module file
    directly.

    Tasks starting with "@" are LOCAL actions handled by the GUI itself
    (no PowerShell process is spawned by main.py's task pipeline):
        @open_log              -> opens %LOCALAPPDATA%\\Pulse\\logs\\Pulse_Log.txt
        @open_onedrive_backup  -> opens Desktop\\Pulse_OneDriveBackup
        @playbooks             -> widgets.PlaybookDialog
        @health_report         -> widgets.HealthReportDialog
        @activation            -> widgets.ActivationStatusDialog
    The last three open a Pulse surface rather than a file. Each dialog
    runs its OWN PowerShellTask (HealthReport / ActivationStatus) rather
    than going through request_task, which is why those backend cases are
    allow-listed in tests/test_contract.py::_PROGRAMMATIC instead of being
    reachable from a card's `task`.

Item schema:
    NOTE ON `accent` (v10): a category's "accent" is a SEMANTIC MODULE KEY
    ("software", "optimization", ...), not a colour. The literal hex for
    each key lives in theme.py's per-mode token sets and is resolved at
    paint time via theme.resolve_accent(t, key), so the palette can differ
    between dark and light. It has to: the old shared hex values measured
    1.86-2.64:1 against the light-mode card, well under the 3:1 floor for
    an icon, which washed the whole colour system out in light mode.
    Widgets store the KEY and re-resolve inside apply_theme().

    icon     str   emoji shown on the card
    title    str   card headline
    desc     str   one-line explanation shown under the title
    task     str   core.ps1 -Task name, or "@local_action"
    timeout  int   seconds before the GUI declares a timeout   (default 300)
    confirm  bool  show a confirmation dialog before running   (default False)
    danger   bool  style the card/confirm dialog as destructive (default False)
    note     str   small badge, e.g. "Windows 11 only"          (default "")
    recurring int  ROUTINE task: the re-run interval in days (default absent
             = one-shot). A one-shot tweak has durable readable state and
             badges APPLIED / MODIFIED / DEFAULT from 11-StateProbe.ps1; a
             routine has none — it was run, and then time passed — so it
             badges ACTION DUE once `recurring` days have elapsed since its
             last run, and otherwise shows only its "Ran 3d ago" caption.
             See recurring_days() below and main.PulseApp._card_badge.
    wizard   str   when present, the GUI opens a dedicated multi-step wizard
             dialog instead of the catalog / confirm dialog (checked before
             both). Currently only "office" -> widgets.OfficeWizardDialog,
             which resolves a setup.exe/configuration.xml pair and passes
             them to core.ps1 as -OfficeSetupPath/-OfficeConfigPath. A task
             using "wizard" should not also set "catalog" or "confirm".
    catalog  bool   when True, the GUI opens widgets.SoftwareCatalogDialog —
             the UNIFIED software hub: one scrollable list of every
             installable app, with a sub-category tab bar (All / Browsers &
             Media / Development & Tools / Gaming Launchers / System
             Runtimes & Utilities) filtering it in place, dependency hints
             and the same per-row "..." install-options wizard every other
             selector uses. Checked before "wizard"/"confirm". Sourced from
             SOFTWARE_CATALOG below, which mirrors $Apps_CatalogAll in
             01-Catalogs.ps1 (same IDs, same
             order) - the backend is the source of truth for what winget ID
             each entry installs; the GUI list is only its mirror. The
             ticked AppIds go to core.ps1 via -AppIds, and because the tabs
             filter one list rather than paging between several, a single
             deploy can span sub-categories.

             This REPLACED a per-card `apps` list (4-tuples of AppId /
             DisplayName / Description / Url), which existed once per app
             pack and gave the app four different front doors.
    update_center  bool  when True, the GUI opens widgets.UpdateCenterDialog
             instead of every other selector — it runs its own live winget
             scan (task ScanForUpdates), shows a current-vs-available
             version audit, and hands back the ticked AppIds. main.py then
             runs "task" (UpdateSelectedApps) with those AppIds through the
             normal pipeline, exactly like a catalog selection would.
    startup_manager  bool  when True, the GUI opens
             widgets.StartupManagerDialog instead of running "task"
             directly — a self-contained optimization hub (scan, group by
             recommendation, live per-item ToggleSwitch) that never hands
             anything back; main.py just opens it and moves on.
    hub      bool + items list[dict]  when True, this entry is a container,
             not a runnable action — it has no "task" and is never passed
             to core.ps1. Clicking it opens widgets.HubDialog (a single
             sub-item skips straight to that sub-item instead) rendering
             `items` as the same GlassCards a category page uses; picking
             one runs it through request_task() exactly as if it had lived
             on the page directly. This is what lets a category collapse
             to a handful of primary cards without deleting any actions —
             see Microsoft Edge / Microsoft OneDrive / System Tools &
             Utilities in CATEGORIES["software"].
             iter_leaf_items() below expands every hub so leaf
             actions stay reachable from the Ctrl+K command palette.
             A hub may instead supply `groups` (list of
             {"title": str, "items": list[dict]}) in place of a flat
             `items` list: the HubDialog then renders each group's title as
             a small "section" header above its cards, so a hub with many
             sub-actions stays tidy and scannable. NO hub uses `groups`
             today — the System Tools hub did until the v1.0 RC lifted Edge
             and OneDrive out of it onto the page, leaving too few
             sub-actions to need headers — but both shapes stay supported,
             and hub_items() flattens either, so counters, the command
             palette and hub navigation treat grouped and flat hubs
             identically.
"""

# ============================================================
#  THE UNIFIED SOFTWARE CATALOG  (v1.0 RC)
#
#  ONE list of every installable app the GUI offers, split into the four
#  sub-categories the catalog's tab bar filters by. This replaced FOUR
#  separate cards — Essential Apps, Developer & University Hub, Gaming
#  Launchers, Hardware Diagnostics — each with its own dispatcher case and
#  its own selector dialog. That split made "where do I get Docker?" and
#  "where do I get VLC?" different questions with different answers, and
#  made a mixed selection (one browser, one IDE, one launcher) impossible
#  without three separate deploys. The tabs are a VIEW over this single
#  list, not four lists behind a shared frame.
#
#  Mirrors 01-Catalogs.ps1's $Apps_CatalogAll — same IDs, SAME ORDER,
#  section for section. The backend stays the source of truth for what
#  winget ID each entry installs; this is the GUI's mirror, extended with
#  the description / URL / dependency-hint metadata a bare (AppId,
#  DisplayName) pair cannot carry.
#
#  Section schema:
#     key    str   stable id used by the tab bar and by tests
#     icon   str   emoji shown on the tab
#     title  str   the tab's label
#     blurb  str   one line under the catalog header when the tab is active
#     groups list[(group_title, tools)]  — a group_title of "" renders the
#            tools with no sub-header (a section that needs no internal
#            division); anything else renders as a small section header
#            inside the tab, so a 16-entry tab stays scannable.
#
#  Each tool entry: (AppId, DisplayName, WhyYouNeedIt, OfficialUrl,
#                     RequiresAppId | None, RequiresDisplayName | None)
# ============================================================
SOFTWARE_CATALOG = [
    {
        "key": "browsers",
        "icon": "🌐",
        "title": "Browsers & Media",
        "blurb": "Browsers, chat, media players and productivity essentials.",
        "groups": [
            ("", [
                ("Google.Chrome", "Google Chrome",
                 "Fast, secure web browser from Google.",
                 "https://www.google.com/chrome/", None, None),
                ("Brave.Brave", "Brave Browser",
                 "Privacy-first Chromium browser with built-in ad blocking.",
                 "https://brave.com/download/", None, None),
                ("Mozilla.Firefox", "Mozilla Firefox",
                 "Fast, independent browser built on open standards.",
                 "https://www.mozilla.org/firefox/new/", None, None),
                ("Microsoft.Edge", "Microsoft Edge",
                 "Microsoft's Chromium browser — reinstalls cleanly here even after using Remove Microsoft Edge.",
                 "https://www.microsoft.com/en-us/edge/download", None, None),
                ("Telegram.TelegramDesktop", "Telegram Desktop",
                 "Fast, secure cloud-based messaging.",
                 "https://telegram.org/apps", None, None),
                ("Spotify.Spotify", "Spotify (Win32)",
                 "Music and podcast streaming client.",
                 "https://www.spotify.com/download/windows/", None, None),
                ("Discord.Discord", "Discord",
                 "Voice, video and text chat for friends and communities.",
                 "https://discord.com/download", None, None),
                ("9NKSQCEZVDDB", "WhatsApp (Store)",
                 "Official WhatsApp messenger for the desktop.",
                 "https://www.whatsapp.com/download", None, None),
                ("9PKTQ5699M62", "iCloud (Store)",
                 "Access iCloud Photos, Drive and Passwords on Windows.",
                 "https://www.apple.com/icloud/", None, None),
                ("Apple.iTunes", "iTunes",
                 "Media library and Apple device sync.",
                 "https://www.apple.com/itunes/", None, None),
                ("7zip.7zip", "7-Zip",
                 "Open-source archiver with best-in-class compression.",
                 "https://www.7-zip.org/", None, None),
                ("VideoLAN.VLC", "VLC Media Player",
                 "Plays practically every audio and video format ever made.",
                 "https://www.videolan.org/vlc/", None, None),
                ("TheDocumentFoundation.LibreOffice", "LibreOffice",
                 "Free office suite — Writer, Calc, Impress and more.",
                 "https://www.libreoffice.org/download/download-libreoffice/", None, None),
                ("Notion.Notion", "Notion",
                 "All-in-one notes, docs and project workspace.",
                 "https://www.notion.com/desktop", None, None),
            ]),
        ],
    },
    {
        "key": "development",
        "icon": "🧑‍💻",
        "title": "Development & Tools",
        "blurb": "Runtimes, IDEs, AI tooling, databases and containers — "
                 "the old Developer & University Hub, in the catalog.",
        "groups": [
            ("🧩 Core Runtimes & Compilers", [
                ("Python.Python.3.12", "Python 3.12",
                 "General-purpose language for scripting, data science and AI/ML projects.",
                 "https://www.python.org/downloads/", None, None),
                ("EclipseAdoptium.Temurin.21.JDK", "Java JDK (Temurin 21)",
                 "The Java Development Kit — compiles and runs Java projects; NetBeans and IntelliJ both need this.",
                 "https://adoptium.net/temurin/releases/", None, None),
                ("OpenJS.NodeJS.LTS", "Node.js (LTS)",
                 "JavaScript runtime for web backends, build tools and npm packages.",
                 "https://nodejs.org/en/download", None, None),
                ("Git.Git", "Git / Git Bash",
                 "Version control — track changes and collaborate on any codebase.",
                 "https://git-scm.com/downloads", None, None),
                ("MSYS2.MSYS2", "GCC / MinGW-w64 Compiler",
                 "C/C++ compiler toolchain for native Windows builds.",
                 "https://www.msys2.org/", None, None),
            ]),
            ("🛠️ IDEs & Editors", [
                ("Microsoft.VisualStudioCode", "VS Code",
                 "Lightweight, extensible code editor — the daily driver for most languages.",
                 "https://code.visualstudio.com/download", None, None),
                ("Anysphere.Cursor", "Cursor IDE",
                 "AI-native code editor built on VS Code, with built-in AI pair programming.",
                 "https://cursor.sh/", None, None),
                ("JetBrains.PyCharm.Community", "PyCharm Community",
                 "Full-featured Python IDE with debugging, refactoring and test tools.",
                 "https://www.jetbrains.com/pycharm/download/",
                 "Python.Python.3.12", "Python 3.12"),
                ("JetBrains.IntelliJIDEA.Community", "IntelliJ IDEA Community",
                 "Full-featured Java IDE with deep code intelligence and refactoring.",
                 "https://www.jetbrains.com/idea/download/",
                 "EclipseAdoptium.Temurin.21.JDK", "Java JDK"),
                ("Apache.NetBeans", "NetBeans IDE",
                 "Java IDE popular in university courses — project templates and a visual GUI builder.",
                 "https://netbeans.apache.org/download/index.html",
                 "EclipseAdoptium.Temurin.21.JDK", "Java JDK"),
            ]),
            ("🧠 AI & Local LLM Stack", [
                ("Ollama.Ollama", "Ollama (Local LLM Runner)",
                 "Run open-source LLMs (Llama, Mistral, etc.) locally — no cloud required.",
                 "https://ollama.com/download", None, None),
                ("OpenWebUI.OpenWebUI", "Open WebUI (Local Chat Interface)",
                 "A ChatGPT-style web interface for models running in Ollama.",
                 "https://openwebui.com/", None, None),
            ]),
            ("🗄️ Databases & API Tools", [
                ("DBeaver.DBeaver.Community", "DBeaver (Database Client)",
                 "Universal SQL client — browse and query almost any database.",
                 "https://dbeaver.io/download/", None, None),
                ("Postman.Postman", "Postman (API Client)",
                 "Build, test and document REST/GraphQL APIs.",
                 "https://www.postman.com/downloads/", None, None),
                ("Bruno.Bruno", "Bruno (Open-Source API Client)",
                 "A fast, open-source Postman alternative that stores collections as local files.",
                 "https://www.usebruno.com/downloads", None, None),
            ]),
            ("🐳 Containerization", [
                ("Docker.DockerDesktop", "Docker Desktop",
                 "Build and run containers — package an app with everything it needs to run anywhere.",
                 "https://www.docker.com/products/docker-desktop/", None, None),
            ]),
        ],
    },
    {
        "key": "gaming",
        "icon": "🎮",
        "title": "Gaming Launchers",
        "blurb": "Game stores and launchers — the matching GPU vendor suite "
                 "is added automatically when you pick any of these.",
        "groups": [
            ("", [
                ("Valve.Steam", "Steam",
                 "The largest PC game store and launcher.",
                 "https://store.steampowered.com/about/", None, None),
                ("EpicGames.EpicGamesLauncher", "Epic Games",
                 "Epic's store and launcher — free weekly games included.",
                 "https://store.epicgames.com/en-US/download", None, None),
                ("RockstarGames.Launcher", "Rockstar Games",
                 "Rockstar's launcher for GTA, Red Dead and more.",
                 "https://socialclub.rockstargames.com/rockstar-games-launcher", None, None),
                ("BlueStacks.BlueStacks", "BlueStacks 5",
                 "Android app player — run mobile games on Windows.",
                 "https://www.bluestacks.com/download.html", None, None),
            ]),
        ],
    },
    {
        "key": "system",
        "icon": "🧩",
        "title": "System Runtimes & Utilities",
        "blurb": "The prerequisite runtimes other software depends on, plus "
                 "hardware monitoring and diagnostics tools.",
        "groups": [
            ("⚙️ Core API Runtimes", [
                ("Microsoft.DirectX", "DirectX End-User Runtime",
                 "Legacy DirectX libraries that older games still need.",
                 "https://www.microsoft.com/en-us/download/details.aspx?id=35", None, None),
                ("Microsoft.VCRedist.2015+.x64", "Visual C++ Redistributables",
                 "C++ runtime DLLs required by countless Windows apps.",
                 "https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist", None, None),
                ("Microsoft.DotNet.DesktopRuntime.8", ".NET Desktop Runtime",
                 "Runs modern .NET desktop applications.",
                 "https://dotnet.microsoft.com/en-us/download/dotnet/8.0", None, None),
                ("Oracle.JavaRuntimeEnvironment", "Java Runtime Environment",
                 "Runs Java desktop applications.",
                 "https://www.java.com/en/download/", None, None),
            ]),
            ("🔬 Hardware Diagnostics", [
                ("CPUID.CPU-Z", "CPU-Z",
                 "CPU, motherboard and memory identification tool.",
                 "https://www.cpuid.com/softwares/cpu-z.html", None, None),
                ("TechPowerUp.GPU-Z", "GPU-Z",
                 "Graphics card information, sensors and BIOS tools.",
                 "https://www.techpowerup.com/gpuz/", None, None),
                ("CPUID.HWMonitor", "HWMonitor",
                 "Live voltages, temperatures and fan speeds.",
                 "https://www.cpuid.com/softwares/hwmonitor.html", None, None),
                ("CrystalDewWorld.CrystalDiskInfo", "CrystalDiskInfo",
                 "Drive health and S.M.A.R.T. monitoring.",
                 "https://crystalmark.info/en/software/crystaldiskinfo/", None, None),
                ("Guru3D.Afterburner", "MSI Afterburner",
                 "GPU overclocking and on-screen performance monitoring.",
                 "https://www.msi.com/Landing/afterburner", None, None),
            ]),
        ],
    },
]

# There are NO quick-select bundles. CATALOG_BUNDLES / CATALOG_BUNDLE_SECTION
# used to declare three stacks ("Java / University", "AI / Python", "Web
# Dev") that the catalog rendered as a second strip of buttons under the
# tab bar, scoped to the Development & Tools tab. They were a third way to
# narrow a list that already has two, they existed on one tab out of five,
# and the tab itself answers the same question by being read. The row went
# with them — along with $Script:DevHubBundles, which mirrored it.


def catalog_tools(section_key: str = "") -> list[tuple]:
    """Every tool 6-tuple in `section_key`, or in the WHOLE catalog when
    the key is empty (the "All" tab). Flattens group structure — callers
    that need the group headers walk SOFTWARE_CATALOG directly."""
    return [tool
            for section in SOFTWARE_CATALOG
            if not section_key or section["key"] == section_key
            for _group_title, tools in section["groups"]
            for tool in tools]


def catalog_app_ids(section_key: str = "") -> list[str]:
    """AppIds in catalog order — the mirror of $Apps_CatalogAll when called
    with no section key."""
    return [tool[0] for tool in catalog_tools(section_key)]

# ============================================================
#  CATEGORIES  (rendered top-to-bottom in the sidebar)
# ============================================================
CATEGORIES = [
    # --------------------------------------------------------
    #  1. SOFTWARE MANAGEMENT
    # --------------------------------------------------------
    {
        "id": "software",
        "icon": "📦",
        "glyph": "package",
        "title": "Software Management",
        "tagline": "Deploy apps, runtimes and audit startup programs",
        "accent": "software",
        "items": [
            # -- THE CATALOG: every installable app, one card -------------
            #
            # `catalog: True` opens widgets.SoftwareCatalogDialog — the
            # tabbed hub sourced from SOFTWARE_CATALOG above. It is a
            # RUNNABLE action, not a `hub` container: there is exactly one
            # destination behind it, so wrapping it in a HubDialog would
            # add a click that asks nothing. Featured position (index 0)
            # gives it the bento hero treatment on the page.
            {"icon": "📦", "glyph": "package", "title": "Software Catalog",
             "desc": "Every app in one place — browsers, dev tools, games "
                     "and runtimes, filtered by sub-category.",
             "task": "InstallCatalogApps", "timeout": 3600, "catalog": True},
            {"icon": "📄", "title": "Microsoft Office Suite",
             "desc": "Word, Excel, PowerPoint and Outlook via the official ODT.",
             "glyph": "document", "task": "InstallOfficeODT", "timeout": 3600,
             "wizard": "office"},
            # Office stays OUTSIDE the catalog deliberately: it ships as one
            # Click-to-Run bundle with no per-app winget package, so it
            # cannot be a row in a list whose every other row is a winget
            # id. It gets the ODT wizard instead (see the 01-Catalogs.ps1
            # note), and a catalog row that silently behaved completely
            # differently from its neighbours would be the worse lie.
            # -- THE BUNDLED MICROSOFT APPS: one card per PRODUCT ---------
            #
            # v1.0 RC: Edge and OneDrive were two titled groups buried
            # inside the System Tools hub, which put them two clicks deep
            # and filed the two apps most people actually want to remove
            # under "utilities". They are products, not utilities — the
            # same kind of thing as the Catalog and Office — so they are
            # peers of those cards now, one card each.
            #
            # Each stays a HUB rather than becoming two flat cards, because
            # the remove/restore pair is the whole point: the teardown is
            # only safe to offer BESIDE its counterpart restore, and a
            # top-level page carrying a lone red "Purge OneDrive" would be
            # advertising the destructive half. The hub keeps the pair
            # together and keeps the danger one click in, while the card
            # itself reads as calm and product-shaped on the page.
            #
            # A flat `items` list, not `groups`: two sub-actions need no
            # section headers, and HubDialog's flat branch gives 2-4 cards
            # the equal-stretch treatment that fills the panel properly.
            {"icon": "🌐", "glyph": "globe", "title": "Microsoft Edge",
             "desc": "Purge Chromium Edge from Windows — or put it back. "
                     "A backup is kept either way, so the removal is reversible.",
             "hub": True,
             "items": [
                 {"icon": "🌐", "title": "Remove Microsoft Edge",
                  "desc": "Force-purge Chromium Edge, with a backup kept.",
                  "glyph": "delete", "task": "RemoveEdge", "timeout": 900, "confirm": True, "danger": True},
                 {"icon": "🔁", "title": "Reinstall Microsoft Edge",
                  "desc": "Reinstall Edge and restore your backed-up settings.",
                  "glyph": "sync", "task": "RestoreEdge", "timeout": 1800},
             ]},
            {"icon": "☁️", "glyph": "cloud", "title": "Microsoft OneDrive",
             "desc": "Uninstall OneDrive with your local files rescued first — "
                     "or reinstall it and pick syncing back up.",
             "hub": True,
             "items": [
                 # glyph `delete`, not `cloud`: this is the destructive half
                 # of a pair, and it should carry the same glyph as its Edge
                 # counterpart above rather than repeating the product glyph
                 # its own hub card now owns.
                 {"icon": "☁️", "title": "Purge OneDrive",
                  "desc": "Back up local files, then uninstall OneDrive.",
                  "glyph": "delete", "task": "RemoveOneDrive", "timeout": 900, "confirm": True, "danger": True},
                 {"icon": "🔁", "title": "Install / Restore OneDrive",
                  "desc": "Reinstall OneDrive so it's back and syncing.",
                  "glyph": "sync", "task": "RestoreOneDrive", "timeout": 1800},
             ]},
            # -- HUB: environment repair and audits -----------------------
            #
            # Flat `items` since the v1.0 RC extraction. This hub used to
            # carry three titled `groups` — MAINTENANCE & AUDITS plus the
            # Edge and OneDrive pairs — and the grouping existed only
            # because those pairs made the list long enough to need it.
            # With the two products lifted out to their own cards above,
            # what remains is one coherent set of three tools, and a lone
            # section header over a hub's entire contents labels nothing.
            #
            # Hardware Diagnostics is NO LONGER here either: CPU-Z, GPU-Z,
            # HWMonitor, CrystalDiskInfo and Afterburner are installable
            # apps, so they belong in the catalog (System Runtimes &
            # Utilities tab) with every other installable app. What stays
            # here is what this hub is actually for — tools that INSPECT or
            # REPAIR this machine rather than download something.
            {"icon": "🛠️", "glyph": "tools", "title": "System Tools & Utilities",
             "desc": "Environment repair, startup audits and update scans.",
             "hub": True,
             "items": [
                 {"icon": "🧭", "title": "PATH Doctor",
                  "desc": "Makes Windows find your dev tools by name in any terminal.",
                  "glyph": "terminal", "task": "VerifyEnvironment", "timeout": 300},
                 {"icon": "🚀", "title": "Startup Manager",
                  "desc": "Boot-impact audit with instant enable/disable toggles.",
                  "glyph": "boot", "task": "StartupReport", "timeout": 300, "startup_manager": True},
                 {"icon": "🔄", "title": "Check for Updates",
                  "desc": "Live scan of installed apps — update exactly what you pick.",
                  "glyph": "refresh", "task": "UpdateSelectedApps", "timeout": 3600, "update_center": True},
             ]},
        ],
    },
    # --------------------------------------------------------
    #  2. SYSTEM & TWEAKS  (v1.0: optimization + privacy, merged)
    #
    #  Both halves answer the same question — "change how Windows
    #  behaves" — and both are one-shot, probeable, revertible registry
    #  tweaks. Split across two rails they read as two thin modules; here
    #  they are one substantial one, ordered performance-first then
    #  privacy, with the full-pass action closing the page.
    # --------------------------------------------------------
    {
        "id": "system",
        "icon": "⚡",
        "glyph": "bolt",
        "title": "System & Tweaks",
        "tagline": "Performance, interface and privacy tweaks",
        "accent": "optimization",
        # BANDED (v1.0+). Twelve cards in one undifferentiated grid was the
        # densest page in the app and gave no clue that it answers three
        # different questions — how fast is it, how does it look, what does
        # it leak. The bands are the merge comment above, finally made
        # visible: the module stays one module, and its three halves stop
        # having to be inferred from card order.
        "groups": [
            {"title": "PERFORMANCE", "items": [
                {"icon": "🕹️", "title": "Game Mode & Game Bar",
                 "desc": "Enable Game Mode, kill background recording.",
                 "glyph": "game", "task": "GameMode", "timeout": 120},
                # DESKTOP ONLY, and the copy says so before the badge does:
                # the plan parks the CPU at maximum and the task also pins
                # display and sleep timeouts to Never on AC, which on a
                # battery-powered machine means a hot laptop that never
                # sleeps in a bag. `confirm` is set for the same reason —
                # this is no longer a one-line scheme switch, so the user
                # sees the full description and agrees before anything
                # changes.
                {"icon": "⚡", "title": "Ultimate Power Plan",
                 "desc": "Unlock the hidden high-performance scheme. Designed "
                         "strictly for Desktop PCs. Not recommended for "
                         "laptops/mobile devices — also disables display and "
                         "sleep timeouts while on AC power.",
                 "glyph": "bolt", "task": "UltimatePowerPlan", "timeout": 300,
                 "note": "Desktop PCs only", "confirm": True},
                {"icon": "📡", "title": "Network & Ping Optimizer",
                 "desc": "Flush DNS and reset Winsock for lower latency.",
                 "glyph": "network", "task": "NetworkOptimization", "timeout": 300, "confirm": True},
                # Per-adapter, and always reversible — the dialog carries
                # 'Restore Automatic DNS' beside every profile. See
                # 15-Network.ps1 for why "all adapters" is not offered.
                {"icon": "🛰️", "title": "DNS & Network Profiles",
                 "desc": "Switch a connection to Cloudflare, Quad9 or AdGuard — with a one-click way back.",
                 "glyph": "network", "task": "NetworkProfiles", "timeout": 300,
                 "dns_switcher": True},
                {"icon": "🖱️", "title": "Disable Mouse Acceleration",
                 "desc": "Raw pointer precision — no speed curves or thresholds.",
                 "glyph": "mouse", "task": "DisableMouseAccel", "timeout": 120},
            ]},
            {"title": "INTERFACE", "items": [
                {"icon": "🌙", "title": "Global Dark Mode",
                 "desc": "Force the dark theme across Windows and all apps.",
                 "glyph": "moon", "task": "DarkMode", "timeout": 120},
                {"icon": "📌", "title": "Minimalist Taskbar",
                 "desc": "Left-aligned, widget-free, chat-free taskbar.",
                 "glyph": "pin", "task": "MinimalistTaskbar", "timeout": 120, "note": "Windows 11 only"},
                {"icon": "📋", "title": "Classic Context Menu",
                 "desc": "Restore the full Windows 10 right-click menu.",
                 "glyph": "list", "task": "ClassicContextMenu", "timeout": 120, "note": "Windows 11 only"},
                # Distinct from the card above, which switches Windows 11
                # between its short menu and the classic one. This prunes
                # the ENTRIES inside whichever menu is showing, using
                # Windows' own block list — see 16-ContextMenu.ps1.
                {"icon": "🧹", "title": "Context Menu Manager",
                 "desc": "See every right-click entry and hide the ones you don't use — fully reversible.",
                 "glyph": "list", "task": "ContextMenuScan", "timeout": 300,
                 "context_menu": True},
            ]},
            {"title": "PRIVACY & CLEANUP", "items": [
                {"icon": "📦", "title": "Remove Bloatware",
                 "desc": "Uninstall pre-loaded Store apps you never asked for.",
                 "glyph": "delete", "task": "RemoveBloatware", "timeout": 900, "confirm": True},
                {"icon": "🛡️", "title": "Disable Telemetry",
                 "desc": "Stop diagnostic data collection and scheduled tasks.",
                 "glyph": "shieldplain", "task": "DisableTelemetry", "timeout": 300},
                {"icon": "🎯", "title": "Disable Advertising ID",
                 "desc": "Remove the per-user identifier that ad networks track.",
                 "glyph": "target", "task": "DisableAdvertisingID", "timeout": 120},
                {"icon": "🕓", "title": "Disable Activity History",
                 "desc": "Stop Timeline activity sync to Microsoft servers.",
                 "glyph": "history", "task": "DisableActivityHistory", "timeout": 120},
                {"icon": "🔒", "title": "Apply ALL Privacy Settings",
                 "desc": "Run every privacy hardening action in one pass.",
                 "glyph": "defender", "task": "ApplyAllPrivacy", "timeout": 1800, "confirm": True},
            ]},
        ],
    },
    # --------------------------------------------------------
    #  3. MAINTENANCE & SECURITY  (v1.0: maintenance + safety, merged)
    #
    #  Upkeep and recovery in one module: the routines that keep a machine
    #  healthy, and the undo paths for when something needs putting back.
    #  They belong together because they are the same mental mode — "look
    #  after this system" — and because Create Restore Point is the hinge
    #  between them (it is BOTH routine upkeep and the safety net every
    #  rollback depends on). That card used to be duplicated verbatim in
    #  two modules; it now exists exactly once, here.
    # --------------------------------------------------------
    {
        "id": "maintenance",
        "icon": "🔧",
        "glyph": "repair",
        "title": "Maintenance & Security",
        "tagline": "Repair, clean-up, restore points and rollback",
        "accent": "maintenance",
        # BANDED (v1.0+). The ROUTINE UPKEEP band is the load-bearing one:
        # it collects exactly the four `recurring` tasks, so "what is due?"
        # — the question this module exists to answer — is a glance at one
        # band instead of a scan for ACTION DUE badges scattered through
        # eleven cards.
        "groups": [
            {"title": "ROUTINE UPKEEP", "items": [
                {"icon": "🛠️", "title": "System Repair (SFC + DISM)",
                 "desc": "Repair protected system files and the component store.",
                 "glyph": "repair", "task": "RunSFC", "timeout": 3600, "recurring": 90},
                {"icon": "🧹", "title": "Aggressive Cache Clean",
                 "desc": "Wipe temp, Windows Update and system caches.",
                 "glyph": "broom", "task": "CleanCache", "timeout": 900, "confirm": True,
                 "recurring": 30},
                {"icon": "💾", "title": "Optimize All Drives",
                 "desc": "TRIM SSDs and defragment HDDs — drive by drive.",
                 "glyph": "disk", "task": "OptimizeDrives", "timeout": 1800, "recurring": 30},
                {"icon": "🛟", "title": "Create Restore Point",
                 "desc": "Manual System Restore checkpoint — your safety net before big changes.",
                 "glyph": "restorepoint", "task": "CreateRestorePoint", "timeout": 600,
                 "recurring": 30},
            ]},
            {"title": "DISK & SPACE", "items": [
                {"icon": "📈", "title": "Drive Space Report",
                 "desc": "Free / used space snapshot for every fixed drive.",
                 "glyph": "chart", "task": "DriveSpaceReport", "timeout": 120},
                {"icon": "🔭", "title": "Storage Analyzer",
                 "desc": "Find what is actually filling a drive — largest folders and files, read-only.",
                 "glyph": "search", "task": "StorageScan", "timeout": 1800,
                 "storage_analyzer": True},
                {"icon": "🗑️", "title": "Remove Windows.old",
                 "desc": "Reclaim gigabytes from a previous Windows install.",
                 "glyph": "delete", "task": "RemoveWindowsOld", "timeout": 1800, "confirm": True, "danger": True},
                {"icon": "😴", "title": "Disable Hibernation",
                 "desc": "Delete hiberfil.sys and free disk space.",
                 "glyph": "sleep", "task": "DisableHibernation", "timeout": 120},
                {"icon": "🔋", "title": "Enable Hibernation",
                 "desc": "Bring hibernation (and hiberfil.sys) back.",
                 "glyph": "battery", "task": "EnableHibernation", "timeout": 120},
            ]},
            {"title": "RECOVERY", "items": [
                # Pulse creates restore points and calls them the safety net
                # every rollback depends on, but until now offered no way to
                # see whether any exist. A guarantee with no receipt is not a
                # guarantee — this is the receipt. Read-only: it lists what
                # Windows already has and hands off to rstrui.exe, because
                # performing a rollback is Microsoft's own surface's job.
                {"icon": "🛡️", "title": "Restore Point Browser",
                 "desc": "Every System Restore checkpoint on this PC — verify your safety net, read-only.",
                 "glyph": "restorepoint", "task": "@restore_points"},
                {"icon": "↩️", "title": "Reset All Tweaks",
                 "desc": "Revert every registry tweak to your backed-up values.",
                 "glyph": "restore", "task": "ResetTweaks", "timeout": 300, "confirm": True},
                {"icon": "🔧", "title": "Restore Services",
                 "desc": "Re-enable Windows services disabled by the optimizer.",
                 "glyph": "repair", "task": "RestoreServices", "timeout": 300},
                {"icon": "☁️", "title": "OneDrive Backup Folder",
                 "desc": "Open files rescued before OneDrive removal.",
                 "glyph": "folder", "task": "@open_onedrive_backup"},
            ]},
        ],
    },
    # --------------------------------------------------------
    #  4. UTILITIES & TOOLS  (v1.0: information + automation, merged)
    #
    #  Everything that REPORTS on the machine or REPLAYS work against it,
    #  none of which changes a setting on its own. Automation's two cards
    #  used to be a module of their own — a two-card page with a nav entry
    #  to itself — and they read far better here, beside the inspectors
    #  whose output they summarise.
    # --------------------------------------------------------
    {
        "id": "utilities",
        "icon": "📊",
        "glyph": "info",
        "title": "Utilities & Tools",
        "tagline": "Reports, licence status, driver tools and playbooks",
        "accent": "information",
        # BANDED (v1.0+). The split is what each card DOES to the machine:
        # the inspection band only ever reads, the automation band replays
        # and records. Worth stating visually in the one module whose whole
        # promise is "nothing here changes a setting on its own".
        "groups": [
            {"title": "REPORTS & INSPECTION", "items": [
                {"icon": "📊", "title": "System Info Snapshot",
                 "desc": "Hardware, uptime and drive space — written to the log.",
                 "glyph": "chartline", "task": "SystemInfo", "timeout": 300},
                {"icon": "🩺", "title": "Health & Drift Report",
                 "desc": "Snapshot applied tweaks, drives and startup load; export HTML or JSON.",
                 "glyph": "chart", "task": "@health_report"},
                # Read-only, and deliberately so: it reports what Windows'
                # licensing service already knows and hands off to
                # Microsoft's own surfaces for anything that needs changing.
                # Nothing in Pulse activates, keys, or alters a licence.
                {"icon": "🔑", "title": "Activation Status",
                 "desc": "Windows and Office licence state, channel and expiry — read-only.",
                 "glyph": "key", "task": "@activation"},
                # Same read-only contract. powercfg already computes battery
                # wear and cycle count; it just buries them in an HTML file
                # nobody generates, so a user finds out their battery is at
                # 62% of design capacity when it dies rather than before.
                {"icon": "🔋", "title": "Battery & Power Health",
                 "desc": "Battery wear, cycle count and the active power plan — read-only.",
                 "glyph": "battery", "task": "@power_health"},
            ]},
            {"title": "AUTOMATION & LOGS", "items": [
                {"icon": "📘", "title": "Playbooks",
                 "desc": "Run a saved sequence of tasks — preview it first with a dry run.",
                 "glyph": "boot", "task": "@playbooks"},
                {"icon": "💿", "title": "Driver Backup",
                 "desc": "Export every current hardware driver to your Desktop.",
                 "glyph": "save", "task": "DriverBackup", "timeout": 1800, "recurring": 180},
                {"icon": "🔍", "title": "Missing Driver Scan",
                 "desc": "Check Windows Update for drivers you're missing.",
                 "glyph": "search", "task": "DriverScan", "timeout": 900, "recurring": 90},
                {"icon": "📜", "title": "View Operation Log",
                 "desc": "Open the full Pulse operation log.",
                 "glyph": "log", "task": "@open_log"},
            ]},
        ],
    },
]

def category_items(category: dict) -> list[dict]:
    """Flat top-level card list for a category, whether it declares them
    directly under `items` or split across titled `groups`.

    SECTION BANDS (v1.0+). A category may now carry
    `groups: [{"title": str, "items": [...]}]` instead of a flat `items`
    list; CategoryPage renders each title as a band header spanning the
    grid, with that band's cards beneath it. This is the SAME shape a
    grouped hub already uses (see hub_items) — deliberately, so there is
    one grouping idea in the app rather than two that look alike.

    Bands add NO navigation depth: they are rhythm inside a page the user
    is already on, not another click. That is the whole point. System &
    Tweaks and Maintenance & Security had twelve and eleven undifferen-
    tiated cards; the fix for a wall is structure, and the fix is
    emphatically not another hub, which would trade a wall for a maze.

    Every consumer that needs the cards — the operation counter, the
    command palette, task lookup, accent resolution — goes through here,
    so a banded and an unbanded category behave identically everywhere
    except in how they are drawn.
    """
    if category.get("groups"):
        return [item for group in category["groups"]
                for item in group.get("items", [])]
    return category.get("items", [])


def category_bands(category: dict) -> list[tuple[str, list[dict]]]:
    """(band_title, items) pairs in render order. An unbanded category
    yields a single untitled band, so CategoryPage has exactly one code
    path to draw instead of a branch per shape."""
    if category.get("groups"):
        return [(g.get("title", ""), g.get("items", []))
                for g in category["groups"]]
    return [("", category.get("items", []))]


def hub_items(hub: dict) -> list[dict]:
    """Flat list of a hub's runnable sub-actions, regardless of whether the
    hub stores them directly under `items` or split across titled `groups`
    (which the HubDialog renders under section headers — no hub declares
    `groups` today, see the module docstring). Every consumer that needs
    the leaf actions — the command palette, the operation counter, hub
    navigation — goes through here so grouped and flat hubs behave
    identically, which is what let the System Tools hub drop from `groups`
    to `items` without a single caller changing."""
    if hub.get("groups"):
        return [sub for group in hub["groups"] for sub in group["items"]]
    return hub.get("items", [])


def find_action(cat_index: int, task: str) -> tuple[dict | None, str]:
    """(item, category_accent_KEY) for a runnable action located by its `task`
    name within CATEGORIES[cat_index], expanding hub containers recursively.
    Powers the Welcome dashboard's Quick Actions band — direct shortcuts to
    the highest-value single operations, deliberately DISTINCT from the
    sidebar's module navigation (which the dashboard no longer duplicates).
    Returns (None, accent) when the task can't be found, so a renamed or
    removed task degrades gracefully instead of crashing the landing
    screen."""
    cat = CATEGORIES[cat_index]

    def walk(items: list[dict]) -> dict | None:
        for it in items:
            if it.get("hub"):
                found = walk(hub_items(it))
                if found is not None:
                    return found
            elif it.get("task") == task:
                return it
        return None

    return walk(category_items(cat)), cat["accent"]


def find_action_anywhere(task: str) -> tuple[dict | None, str]:
    """(item, category_accent_KEY) for `task` in ANY category, hubs
    expanded — the index-free form of find_action.

    The dashboard's Quick Actions used to be declared as (category_index,
    task) pairs, which silently broke the moment the v1.0 restructure
    renumbered the modules from seven to four: index 4 and 5 simply
    stopped existing and two quick actions vanished from the dashboard
    with no error. A task name is stable across any amount of
    re-shelving; a position is not.
    """
    for index in range(len(CATEGORIES)):
        item, accent = find_action(index, task)
        if item is not None:
            return item, accent
    return None, ""


def recurring_days(item: dict) -> int | None:
    """The re-run interval of a ROUTINE task, or None for a one-shot one.

    The distinction drives the card badge (see main.PulseApp._card_badge):
    a one-shot tweak has readable state, so it reports APPLIED / MODIFIED
    / DEFAULT from the probe; a routine like a cache clean has no such
    state — it was done, and then time passed — so it reports how long ago
    it last ran and whether that is overdue. Showing APPLIED on a cache
    clean was the category error this key exists to fix.
    """
    value = item.get("recurring")
    return int(value) if isinstance(value, int) and value > 0 else None


# ============================================================
#  ADMIN-GATED TASKS — GUI mirror of the backend gate
# ============================================================
# MUST stay in sync with $Script:AdminRequiredTasks in
# src/backend/modules/01-Catalogs.ps1 — the backend is the authority (it
# still rejects an admin task with a clean ERROR verdict even if this drifts),
# but the GUI mirrors the list so it can PRE-CHECK before spawning PowerShell:
# a non-elevated admin action shows an inline "relaunch elevated" prompt
# instead of a spawn-then-fail round trip, and admin-gated Quick Action cards
# can show a lock affordance up front. An automated equality check
# (tests/scratchpad) guards the two lists against drift.
#
# Software install/update tasks are deliberately absent for the same reason
# the backend omits them: winget + each installer handle their own elevation,
# and blanket-requiring admin breaks user-scope / elevation-prohibited
# packages (e.g. Spotify). See the 01-Catalogs.ps1 comment for the full why.
ADMIN_REQUIRED_TASKS = frozenset({
    "RunSFC", "CleanCache", "RemoveBloatware", "OptimizeDrives", "RemoveWindowsOld",
    "DisableHibernation", "EnableHibernation", "DisableTelemetry", "DisableActivityHistory",
    "NetworkOptimization", "UltimatePowerPlan", "RemoveOneDrive", "RemoveEdge",
    "CreateRestorePoint", "DriverBackup", "RestoreServices", "RestoreEdge", "RestoreOneDrive",
    "ApplyAllPrivacy", "ResetTweaks", "InstallOfficeODT", "InstallOfficeODTAuto",
    "StartupDisableItem", "StartupEnableItem",
    # v1.0+ Phase 2 DNS switcher — mirrors 01-Catalogs.ps1.
    "SetDnsProfile", "RestoreDns",
    "ContextMenuToggle", "ContextMenuRestore",
})


def requires_admin(task: str | None) -> bool:
    """True if `task` writes HKLM / services / machine state and therefore
    needs an elevated Pulse. Used by the GUI to pre-check before running an
    admin-gated action (see main.PulseApp.request_task)."""
    return bool(task) and task in ADMIN_REQUIRED_TASKS


def _count_leaves(items: list[dict]) -> int:
    return sum(_count_leaves(hub_items(it)) if it.get("hub") else 1
               for it in items)


def category_operations(category: dict) -> int:
    """Runnable operations inside one category, counting THROUGH hub
    containers — a hub card is a container, not an operation, so a naive
    len(category_items(category)) under-reports a hub-bearing module by
    of three. Powers the category header's count chip."""
    return _count_leaves(category_items(category))


def search_haystack(item: dict) -> str:
    """Everything a search surface should match an item against, lowercased
    — shared by the category-page filter AND the Ctrl+K palette, so the two
    can never disagree about whether something is findable.

    Three levels fold in, each for the same reason: hiding a genuine match
    because it lives one layer down is indistinguishable from the feature
    not existing. Hub containers carry their sub-items' full haystacks; and
    the `catalog` card carries EVERY app in the unified catalog plus its
    sub-category titles — typing "spotify", "docker" or "gaming" must all
    surface the one card that installs them, which is the whole point of
    collapsing four app cards into one.

    Kept as the "is this findable at all?" predicate. RANKING must not use
    it — see search_contents() and the note there."""
    parts = [item.get("title", ""), item.get("desc", ""), item.get("note", "")]
    parts.extend(search_contents(item))
    return " ".join(parts).lower()


def search_contents(item: dict) -> list[str]:
    """The named things `item` CONTAINS, each as its own string — catalog
    app names, a hub's sub-action titles.

    Separate from search_haystack because collapsing them into one blob is
    what broke the command palette's ranking. The Software Catalog card
    contains 43 app names, so its flat haystack runs ~1500 characters; a
    subsequence matcher over a string that long matches nearly any query
    by accident, while scoring far WORSE than a short title that matched
    the same letters by coincidence. Measured before this split: "spotify"
    ranked Startup Manager first and "docker" did not surface the catalog
    at all — even though the catalog is the only thing that installs
    either, and the palette's own docstring promised it would.

    Kept as a LIST so a scorer can ask "does any single contained name
    contain the query?" (a real, precise hit) instead of "do these letters
    appear somewhere across everything this card knows about" (noise).
    """
    names: list[str] = []
    if item.get("catalog"):
        names.extend(section["title"] for section in SOFTWARE_CATALOG)
        names.extend(tool[1] for tool in catalog_tools())
    if item.get("hub"):
        for sub in hub_items(item):
            names.append(sub.get("title", ""))
            names.extend(search_contents(sub))
    return [n for n in names if n]


def accent_for_task(task: str | None) -> str:
    """The module accent KEY of whichever category owns `task` (hubs
    expanded), or "" when nothing matches. Lets a consumer that only has a
    task name — the Recent Operations trail, which is re-read from storage
    with no widget context — colour it in its own module's accent."""
    if not task:
        return ""
    for cat in CATEGORIES:
        def walk(items: list[dict]) -> bool:
            for it in items:
                if it.get("hub"):
                    if walk(hub_items(it)):
                        return True
                elif it.get("task") == task:
                    return True
            return False

        if walk(category_items(cat)):
            return cat["accent"]
    return ""


def iter_leaf_items():
    """Yields (item, breadcrumb) for every runnable action, expanding hub
    containers — used by the Ctrl+K command palette so a hub's sub-actions
    (e.g. 'Microsoft Office Suite', tucked inside the Browsers & Daily Apps
    hub) stay searchable even though the category page now shows only the
    hub card itself."""
    for cat in CATEGORIES:
        for item in category_items(cat):
            if item.get("hub"):
                for sub in hub_items(item):
                    yield sub, f"{cat['title']} › {item['title']}"
            else:
                yield item, cat["title"]
