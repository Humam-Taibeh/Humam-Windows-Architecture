#!/usr/bin/env python3
"""
tools/fetch_app_icons.py

BUILD-TIME asset fetcher for Software Management's brand logos.

Run this once (or when the catalog changes) to populate
assets/appicons/. Pulse itself NEVER touches the network: the app reads
only the files this script leaves behind, which is what keeps an
elevated, privacy-focused Windows utility from phoning out to draw its
own UI. See src/utils/appicons.py for the runtime half.

    python tools/fetch_app_icons.py            # fetch missing only
    python tools/fetch_app_icons.py --force    # re-fetch everything

SOURCE: Simple Icons (https://simpleicons.org), the standard brand-mark
set for exactly this problem. The SVG files are CC0; the marks themselves
remain their owners' trademarks and are used here nominatively — to
identify the software a row installs, which is the same basis every
package manager and app store relies on.

WHY A HAND-WRITTEN MAP AND NOT FUZZY MATCHING: a wrong logo is worse than
no logo. "Cursor" fuzzy-matches a mouse-cursor icon; "MSI" matches both
the hardware brand and the installer format. Every pairing below was
checked by eye against the Simple Icons index, and anything without an
authentic mark is mapped to None rather than to something that merely
looks close.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(ROOT, "assets", "appicons")
MANIFEST = os.path.join(ASSET_DIR, "manifest.json")
INDEX_URL = "https://cdn.jsdelivr.net/npm/simple-icons@13/_data/simple-icons.json"
ICON_URL = "https://cdn.jsdelivr.net/npm/simple-icons@13/icons/{slug}.svg"

#: winget AppId -> Simple Icons slug, or None when no authentic mark is
#: available. The None entries are documented, not forgotten — and every
#: one of them is now covered by a PULSE-DRAWN mark instead (see
#: DRAWN_MARKS below), so nothing in the catalog falls back to the neutral
#: placeholder:
#:
#:   Microsoft.VisualStudioCode / Microsoft.Edge / Microsoft.DirectX
#:       Simple Icons REMOVED every Microsoft product mark under
#:       Microsoft's trademark policy. Substituting VSCodium's logo for VS
#:       Code (a different product) or a generic Microsoft mark would be
#:       inaccurate.
#:   Anysphere.Cursor / BlueStacks.BlueStacks / OpenWebUI.OpenWebUI
#:       Not in the Simple Icons set at all.
#:   CPUID.* / CrystalDewWorld.* / TechPowerUp.GPU-Z
#:       Small hardware utilities with no published brand mark in any
#:       open set. (Note the trap: the index DOES contain a "crystal"
#:       slug — it is the Crystal programming language, nothing to do with
#:       CrystalDiskInfo. Exactly the mismatch this hand-written map
#:       exists to prevent.)
#:
#: Drop an <AppId>.svg into assets/appicons/ by hand to cover any of these
#: — the loader prefers a file on disk over everything except a locally
#: installed app's own icon.
ICON_MAP: dict[str, str | None] = {
    # -- browsers, chat, media, productivity ------------------------
    "Google.Chrome": "googlechrome",
    "Brave.Brave": "brave",
    "Mozilla.Firefox": "firefoxbrowser",
    "Microsoft.Edge": None,
    "Telegram.TelegramDesktop": "telegram",
    "Spotify.Spotify": "spotify",
    "Discord.Discord": "discord",
    "9NKSQCEZVDDB": "whatsapp",
    "9PKTQ5699M62": "icloud",
    "Apple.iTunes": "itunes",
    "7zip.7zip": "7zip",
    "VideoLAN.VLC": "vlcmediaplayer",
    "TheDocumentFoundation.LibreOffice": "libreoffice",
    "Notion.Notion": "notion",
    # -- runtimes ---------------------------------------------------
    "Microsoft.DirectX": None,
    # the C++ language mark, not a Microsoft one — accurate for a C++
    # redistributable and unencumbered
    "Microsoft.VCRedist.2015+.x64": "cplusplus",
    "Microsoft.DotNet.DesktopRuntime.8": "dotnet",
    # Oracle's own mark: this entry is Oracle's JRE, NOT OpenJDK
    "Oracle.JavaRuntimeEnvironment": "oracle",
    # -- gaming -----------------------------------------------------
    "Valve.Steam": "steam",
    "EpicGames.EpicGamesLauncher": "epicgames",
    "RockstarGames.Launcher": "rockstargames",
    "BlueStacks.BlueStacks": None,
    # -- diagnostics ------------------------------------------------
    "CPUID.CPU-Z": None,
    "CPUID.HWMonitor": None,
    "CrystalDewWorld.CrystalDiskInfo": None,
    "TechPowerUp.GPU-Z": None,
    "Guru3D.Afterburner": "msi",        # MSI Afterburner is MSI's product
    # -- dev hub ----------------------------------------------------
    "Python.Python.3.12": "python",
    "EclipseAdoptium.Temurin.21.JDK": "eclipseadoptium",
    "OpenJS.NodeJS.LTS": "nodedotjs",
    "Git.Git": "git",
    "MSYS2.MSYS2": "gnu",               # GCC/MinGW toolchain = the GNU mark
    "Microsoft.VisualStudioCode": None,
    "Anysphere.Cursor": None,
    "JetBrains.PyCharm.Community": "pycharm",
    "JetBrains.IntelliJIDEA.Community": "intellijidea",
    "Apache.NetBeans": "apachenetbeanside",
    "Ollama.Ollama": "ollama",
    "OpenWebUI.OpenWebUI": None,
    "DBeaver.DBeaver.Community": "dbeaver",
    "Postman.Postman": "postman",
    "Bruno.Bruno": "bruno",
    "Docker.DockerDesktop": "docker",
}


#: AppIds covered by a mark DRAWN FOR PULSE rather than fetched — the
#: entries above that map to None. These files are committed to the repo
#: and this script must never overwrite or forget them.
#:
#: THEY ARE NOT BRAND LOGOS AND MUST NOT BE PRESENTED AS ONE. Every app
#: here has no authentic mark in any open, licensed set (checked against
#: the full Simple Icons index, ~3300 marks), and drawing an approximation
#: of a real logo from memory would be worse than the placeholder it
#: replaces: an inaccurate Edge swirl or VS Code ribbon shipped as the
#: vendor's own artwork is a fabrication, and this file's own rule is that
#: a WRONG logo is worse than no logo.
#:
#: What they are instead is a set of purpose-drawn PICTOGRAMS naming what
#: the software does — a code bracket for an editor, a CPU die for CPU-Z,
#: a thermometer for HWMonitor. That keeps every catalog row a crisp,
#: distinct vector (the actual goal: no row looks broken or unfinished)
#: while claiming nothing untrue. They deliberately avoid the LETTER
#: MONOGRAM the neutral glyph replaced — a pictogram describes, a bare
#: initial pretends to be branding.
#:
#: Each entry mirrors the manifest record its SVG already carries, and is
#: written back with "drawn": true so the runtime, the tests and the next
#: person can tell the two tiers apart at a glance.
DRAWN_MARKS: dict[str, dict] = {
    "Microsoft.VisualStudioCode": {"hex": "#3C8CE0", "title": "VS Code"},
    "Anysphere.Cursor": {"hex": "#7A5CFF", "title": "Cursor IDE"},
    "Microsoft.Edge": {"hex": "#2B8FD8", "title": "Microsoft Edge"},
    "Microsoft.DirectX": {"hex": "#5B7CD8", "title": "DirectX Runtime"},
    "OpenWebUI.OpenWebUI": {"hex": "#4EA8A0", "title": "Open WebUI"},
    "BlueStacks.BlueStacks": {"hex": "#4A9BE8", "title": "BlueStacks"},
    "CPUID.CPU-Z": {"hex": "#C8803A", "title": "CPU-Z"},
    "CPUID.HWMonitor": {"hex": "#D2603C", "title": "HWMonitor"},
    "TechPowerUp.GPU-Z": {"hex": "#4E9E5C", "title": "GPU-Z"},
    "CrystalDewWorld.CrystalDiskInfo": {"hex": "#3B8FC4",
                                        "title": "CrystalDiskInfo"},
}


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "pulse-icon-fetch"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def _slugify(title: str) -> str:
    out = title.lower().replace("+", "plus").replace(".", "dot").replace("&", "and")
    return "".join(ch for ch in out if ch.isalnum())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="re-download icons that are already present")
    args = parser.parse_args()

    os.makedirs(ASSET_DIR, exist_ok=True)
    print(f"index  <- {INDEX_URL}")
    raw = json.loads(_get(INDEX_URL).decode("utf-8"))
    entries = raw["icons"] if isinstance(raw, dict) else raw
    # the published index leaves `slug` blank for most entries, so derive
    # it the same way Simple Icons does and key on that
    index = {(e.get("slug") or _slugify(e.get("title", ""))): e for e in entries}
    print(f"       {len(index)} brand marks available")

    manifest: dict[str, dict] = {}
    fetched = skipped = 0
    unmapped: list[str] = []

    for app_id, slug in sorted(ICON_MAP.items()):
        if slug is None:
            unmapped.append(app_id)
            continue
        entry = index.get(slug)
        if entry is None:
            print(f"  !! {app_id}: slug {slug!r} is not in the index")
            unmapped.append(app_id)
            continue
        # File name is the APP ID, not the slug: the runtime looks up by
        # the id it already has, and a hand-supplied override drops in at
        # the same path with no manifest edit.
        safe = app_id.replace("/", "_").replace("\\", "_")
        path = os.path.join(ASSET_DIR, f"{safe}.svg")
        if args.force or not os.path.isfile(path):
            data = _get(ICON_URL.format(slug=slug))
            if b"<svg" not in data[:400]:
                print(f"  !! {app_id}: {slug}.svg was not an SVG, skipped")
                unmapped.append(app_id)
                continue
            with open(path, "wb") as handle:
                handle.write(data)
            fetched += 1
        else:
            skipped += 1
        manifest[app_id] = {
            "file": f"{safe}.svg",
            "hex": "#" + str(entry.get("hex", "000000")).lstrip("#"),
            "title": entry.get("title", ""),
        }

    # Fold the hand-drawn marks back in BEFORE writing. The manifest is
    # rebuilt from scratch on every run, so without this step a routine
    # `python tools/fetch_app_icons.py` would silently delete ten entries
    # and drop those rows back to the neutral placeholder — the exact
    # regression this whole module exists to prevent, introduced by the
    # tool that maintains it.
    missing_assets: list[str] = []
    for app_id, record in DRAWN_MARKS.items():
        safe = app_id.replace("/", "_").replace("\\", "_")
        path = os.path.join(ASSET_DIR, f"{safe}.svg")
        if not os.path.isfile(path):
            missing_assets.append(app_id)
            continue
        manifest[app_id] = {"file": f"{safe}.svg", "hex": record["hex"],
                            "title": record["title"], "drawn": True}

    with open(MANIFEST, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True)

    drawn = sum(1 for entry in manifest.values() if entry.get("drawn"))
    print(f"\nfetched {fetched}, already present {skipped}")
    print(f"manifest -> {os.path.relpath(MANIFEST, ROOT)} "
          f"({len(manifest)} marks: {len(manifest) - drawn} brand, "
          f"{drawn} drawn for Pulse)")
    if unmapped:
        covered = [a for a in unmapped if a in DRAWN_MARKS]
        print(f"\nno authentic brand mark ({len(unmapped)}) — "
              f"{len(covered)} covered by a Pulse-drawn pictogram:")
        for app_id in unmapped:
            tag = "drawn" if app_id in DRAWN_MARKS else "NEUTRAL GLYPH"
            print(f"    {app_id:38s} {tag}")
    if missing_assets:
        print("\n!! DRAWN_MARKS names files that are not on disk — these rows "
              "will fall back to the neutral glyph:")
        for app_id in missing_assets:
            print(f"    {app_id}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
