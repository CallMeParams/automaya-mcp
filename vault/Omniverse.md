---
tags: [automaya, omniverse, usd, viewport]
---
# Omniverse

The `omniverse` domain (`omni.*` bridge commands, `maya_omni_*` tools) drives NVIDIA's Omniverse Maya Native Connector when it is installed, and falls back to plain mayaUsd when it is not.

## Facts (NVIDIA docs, docs.omniverse.nvidia.com/connect/latest/maya/native.html)
- Supports Maya 2024.2 (USD 22.11) on Windows. Maya 2023.3 and 2022.4 are untested.
- Installed from the NVIDIA NGC Catalog as an `.exe` that drops a module file into `$MyDocuments/maya/202x/modules`.
- Requires Autodesk's mayaUsd plugin.
- Adds an "Omniverse" main menu (Open Content, Save Content, Export Content, Export Selection) and an Omniverse shelf with live session create, join, leave, share, end and merge.
- Content lives on Nucleus at `omniverse://server/path.usd`.
- Live sessions sync edits both ways with Omniverse Kit apps such as USD Composer, which acts as a real time RTX viewport.
- NVIDIA discontinued the connector for Maya 2025 and newer and points users to Autodesk's mayaUsd from GitHub.
- The connector's scripting API is not documented, so every command detects at runtime instead of assuming names.

## Two paths
- **connector**: Maya 2024.2 on Windows with the connector installed. Save to Nucleus, start a live session, open the same stage in USD Composer with Live mode on and the RTX Real-Time renderer. Edits flow both ways.
- **usd_only**: anything else (Maya 2025+, Linux, macOS, no connector). Export a USD layer with mayaUsd and open it in USD Composer or the Unreal USD stage; re export to refresh.

`maya_omni_status` tells you which one you are on (`path` field) and lists setup steps when the connector is missing.

## How the tools behave
- `maya_omni_status` (read): looks for "omni" in `cmds.moduleInfo(listModules=True)` and in loaded plugins, an "Omniverse" main menu through `cmds.lsUI(menus=True)`, and an Omniverse tab under `$gShelfTopLevel`. Reports mayaUsd loaded and version, Maya version, optionVars mentioning omni or live, `setup_steps` and a `warning` on 2025+.
- `maya_omni_list_commands` (read): `cmds.help` for `*omni*` and `*Omni*`, MEL `whatIs` over a candidate proc list (OmniverseExport, omniCreateLiveSession and friends), a walk of the Omniverse menu including submenus (and its postMenuCommand if the menu builds lazily), and the Omniverse shelf buttons. Each item has label, command, source type and path like `Omniverse > Live Session > Create Session`.
- `maya_omni_run_menu_item` (write): finds one item by case insensitive label substring (an exact label wins over partial ones), then runs it the way Maya would: Python callables are called, Python source is exec'd with `cmds` and `mel` in scope, MEL goes through `mel.eval`. No match or several matches is an error that lists the labels. Many items open a dialog the artist finishes.
- `maya_omni_export_usd` (write): a local `.usd/.usda/.usdc` path goes through `_util.export_selection` (mayaUsd, same as `maya_livelink_export_usd`); with no nodes it exports every top level transform except the startup cameras. An `omniverse://` URL needs the connector; the menu's Export Selection opens a dialog so it is never driven blindly. Instead any discovered proc with "export" in its name is tried as `proc "omniverse://..."` and the one that worked is reported; if none works the error says to export locally and save to Nucleus from the Omniverse menu.
- `maya_omni_live_session` (write): maps create, join, leave, end, merge, share to label keywords (whole words), requires "live" or "session" in the label or its menu path, searches the menu first and then the shelf, and runs the single best match. Without the connector it errors and points at `maya_livelink_export_usd` plus reloading the stage in Kit or Unreal. `session_name` is reported back only; the connector dialog asks for it.
- `maya_omni_nucleus_hint` (read): URL shape `omniverse://localhost/Projects/<name>/<shot>.usd` with a suggestion from the scene name, Nucleus login (Enterprise Nucleus Server; the Launcher and Nucleus Workstation were deprecated 1 Oct 2025), the RTX viewport steps and the deprecation note.

## Beside the Unreal subscriber
Omniverse live sessions are the quick RTX viewport when the show is on Maya 2024.2 and Windows: no code in the renderer, two way edits, path traced look in seconds. USD plus the Unreal subscriber ([[Unreal Real Time Viewport]]) is the path that survives Maya upgrades, since it only needs mayaUsd and our own event stream. Treat Omniverse as a bonus when it is there and build the pipeline on the USD path.

## To verify in Maya
- The actual menu, shelf tab and plugin names the connector installs (the detection is pattern based, so confirm it finds them).
- Whether any connector MEL proc takes a Nucleus path directly.

Related: [[Unreal Real Time Viewport]], [[Maya 2024 Facts]], [[Verify In Maya]]

## NVIDIA platform changes (checked 2026-09-24)
- The Omniverse Launcher and Nucleus Workstation were deprecated on 1 October 2025, along with Navigator, Nucleus Cache, Omniverse Drive and several apps. Nucleus now means the **Enterprise Nucleus Server** from NGC, which needs an Enterprise License for production.
- USD Composer and USD Explorer live on as templates in the **Kit App Template** repo on GitHub; you build the app yourself with the Kit SDK.
- Many connectors are in maintenance mode; NVIDIA prefers publishers' native USD support. For Maya that is Autodesk's **USD for Maya (mayaUsd)** plugin. The Maya Legacy Connector was retired on 1 October 2025; the Native Connector is the one for Maya 2024.2.
- What this means for AutoMaya: the live session tools only help a studio that already has Enterprise Nucleus and the Native Connector on 2024.2. The route that works everywhere is file based: mayaUsd writes the stage, a Kit app or Unreal opens it, reload to refresh. True live sync without Nucleus is our NDJSON stream plus the Unreal subscriber.

Sources: developer.nvidia.com/omniverse/legacy-tools, docs.omniverse.nvidia.com/connect/latest/maya.html, docs.omniverse.nvidia.com/connect/latest/maya/native.html
