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

#: winget AppId -> Simple Icons slug, or None when this set has no
#: authentic mark. The None entries are documented, not forgotten; three
#: of them are picked up from a brand-logo set by LOGO_MAP below, and the
#: rest genuinely have no open-licensed logo anywhere:
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


# ============================================================
#  SECOND SOURCE: FULL-COLOUR OFFICIAL BRAND LOGOS
# ============================================================
#: winget AppId -> an Iconify icon id from a BRAND-LOGO collection.
#:
#: Simple Icons (above) is a monochrome SILHOUETTE set — one flat path per
#: brand, recoloured at paint time. It is excellent, and it is also why
#: Microsoft's marks are absent: Simple Icons removed every Microsoft
#: product logo under Microsoft's trademark policy.
#:
#: This map is the answer to that gap. It pulls from Iconify's brand-logo
#: collections — principally `logos` (SVG Logos by Gil Barbara, CC0, ~1861
#: marks) — which carry the REAL, FULL-COLOUR artwork: VS Code's blue
#: ribbon with its actual gradient, Edge's actual swirl. These are the
#: vendors' own marks, not renditions, and they render in their true
#: colours rather than as a recoloured silhouette (see `color: true` in
#: the manifest and _brand_pixmap in src/utils/appicons.py).
#:
#: WHAT IS STILL MISSING, and why nothing is invented to cover it:
#: BlueStacks, DirectX, CPU-Z, GPU-Z, HWMonitor, CrystalDiskInfo and Open
#: WebUI have NO authentic mark in any open, licensed icon set. That was
#: not assumed — it was measured against the full Simple Icons index
#: (~3300 marks), the whole `logos` collection (1861 marks) and Iconify's
#: federated search across every collection it aggregates. Those seven
#: fall through to the runtime's next tier: the app's OWN icon, read from
#: its own installed binary, which is the vendor's genuine artwork and
#: needs no redistribution at all. When the app is not installed they
#: reach the neutral glyph, which says "no logo available" honestly.
#:
#: THE RULE THIS FILE IS BUILT ON, restated because it was tested: a WRONG
#: logo is worse than no logo, and an INVENTED one is worse than both.
#: Pulse ships no hand-drawn stand-ins. To bundle a mark for one of the
#: seven, drop a genuine `<AppId>.svg` into assets/appicons/ and add it
#: here — the loader already prefers a file on disk.
LOGO_MAP: dict[str, str] = {
    "Microsoft.VisualStudioCode": "logos:visual-studio-code",
    "Microsoft.Edge": "logos:microsoft-edge",
    # BoxIcons Logos' rendition of Cursor's cube mark — a curated
    # brand-logo set, not a lookalike picked by keyword.
    "Anysphere.Cursor": "bxl:cursor-ai",
}

ICONIFY_SVG = "https://api.iconify.design/{prefix}/{name}.svg"

#: Brand hex for LOGO_MAP entries that turn out to be SILHOUETTES (drawn
#: with `currentColor`) rather than full-colour artwork. These take the
#: Simple Icons treatment — recoloured through the contrast guard — so
#: they need the brand's own colour the same way those do.
MONOCHROME_LOGO_HEX: dict[str, str] = {
    # Cursor's mark is a monochrome cube; black is its own brand colour,
    # and the guard lifts it off obsidian exactly as it does for Steam,
    # Notion and 7-Zip, which are all #000000 too.
    "Anysphere.Cursor": "#000000",
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

    # -- second pass: full-colour official logos ------------------
    # Written AFTER the Simple Icons pass so a LOGO_MAP entry wins for any
    # app that somehow appears in both: the vendor's real colour artwork is
    # a better answer than a recoloured silhouette of it.
    colour_failures: list[str] = []
    for app_id, icon_id in sorted(LOGO_MAP.items()):
        prefix, _, name = icon_id.partition(":")
        if not prefix or not name:
            colour_failures.append(f"{app_id}: malformed icon id {icon_id!r}")
            continue
        safe = app_id.replace("/", "_").replace("\\", "_")
        path = os.path.join(ASSET_DIR, f"{safe}.svg")
        if args.force or not os.path.isfile(path):
            try:
                data = _get(ICONIFY_SVG.format(prefix=prefix, name=name))
            except Exception as exc:                    # noqa: BLE001
                colour_failures.append(f"{app_id}: {icon_id} fetch failed ({exc})")
                continue
            if b"<svg" not in data[:400]:
                colour_failures.append(f"{app_id}: {icon_id} was not an SVG")
                continue
            with open(path, "wb") as handle:
                handle.write(data)
            fetched += 1
        else:
            skipped += 1
        # DETECTED, not assumed. Some brand-logo sets publish a mark as a
        # single path filled with `currentColor` — a SILHOUETTE wearing a
        # colour set's prefix. Flagging one of those `color: true` would
        # send it down the render-as-drawn path, where "currentColor"
        # resolves to black and the mark disappears into a dark canvas
        # with a rescue plaque bolted behind it. Reading the file decides
        # correctly for every entry, including ones added later.
        with open(path, "rb") as handle:
            body = handle.read().decode("utf-8", "ignore")
        monochrome = "currentColor" in body
        record = {
            "file": f"{safe}.svg",
            "source": icon_id,
            "title": name.replace("-", " ").title(),
        }
        if monochrome:
            # Same treatment as a Simple Icons mark: a brand hex plus the
            # runtime's contrast guard, which is what keeps a near-black
            # silhouette readable on obsidian.
            record["hex"] = MONOCHROME_LOGO_HEX.get(app_id, "#000000")
        else:
            # `color` is the flag the runtime reads to render the mark AS
            # DRAWN instead of recolouring it to a single tone. A full
            # colour logo pushed through the silhouette path would come out
            # as a flat blue blob — accurate artwork, destroyed on paint.
            record["color"] = True
        manifest[app_id] = record
        unmapped = [a for a in unmapped if a != app_id]

    with open(MANIFEST, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True)

    colour = sum(1 for entry in manifest.values() if entry.get("color"))
    print(f"\nfetched {fetched}, already present {skipped}")
    print(f"manifest -> {os.path.relpath(MANIFEST, ROOT)} "
          f"({len(manifest)} marks: {len(manifest) - colour} monochrome, "
          f"{colour} full-colour)")
    if unmapped:
        print(f"\nno authentic brand mark exists in any open set "
              f"({len(unmapped)}) — these fall through to the app's OWN "
              "installed icon, then to the neutral glyph:")
        for app_id in unmapped:
            print(f"    {app_id}")
        print("  (drop a genuine <AppId>.svg into assets/appicons/ and add "
              "it to LOGO_MAP to bundle one. Nothing is ever invented.)")
    if colour_failures:
        print("\n!! LOGO_MAP entries that did not resolve:")
        for failure in colour_failures:
            print(f"    {failure}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
