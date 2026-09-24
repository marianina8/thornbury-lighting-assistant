#!/usr/bin/env python3
"""Package the addon as an extension .zip for Blender's
Preferences > Add-ons > Install from Disk (Blender 4.2+).

The layout matches `blender --command extension build` (manifest at the zip
root, no __pycache__); tools/../addon/tests install the result exactly the way
an artist would and run the addon from it.

    python3 tools/build_addon.py            -> dist/thornbury_lighting-<version>.zip
"""
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "addon", "thornbury_lighting")
DIST = os.path.join(ROOT, "dist")
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # reproducible zips


def main():
    manifest = open(os.path.join(SRC, "blender_manifest.toml"), encoding="utf-8").read()
    version = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
    ident = re.search(r'^id = "([^"]+)"', manifest, re.M).group(1)
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "%s-%s.zip" % (ident, version))
    files = []
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
        for f in sorted(filenames):
            if f.endswith((".pyc", ".pyo")) or f.startswith("."):
                continue
            full = os.path.join(dirpath, f)
            files.append((full, os.path.relpath(full, SRC).replace(os.sep, "/")))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for full, arc in files:
            info = zipfile.ZipInfo(arc, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as fh:
                z.writestr(info, fh.read())
    names = [a for _, a in files]
    for required in ("blender_manifest.toml", "__init__.py", "presets/presets.json"):
        if required not in names:
            sys.exit("missing %s in the package" % required)
    print("built %s (%d files, %d KB)" % (os.path.relpath(out, ROOT), len(files), os.path.getsize(out) // 1024))


if __name__ == "__main__":
    main()
