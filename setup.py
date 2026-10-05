"""
py2app build script.

Usage:
    source build_venv/bin/activate
    python3 setup.py py2app

py2app rewrites ffmpeg/ffprobe's dylib dependencies to @executable_path/../Frameworks/...,
which is only correct for a binary living directly in Contents/MacOS/. Our binaries live one
level deeper at Contents/Resources/bin/, so that path silently resolves to a nonexistent
Contents/Resources/Frameworks/ and dyld aborts at runtime (SIGABRT) the first time a rarely
-exercised code path (e.g. a hardware encoder) touches an affected symbol. _fix_bundled_binaries
repairs this post-build by pointing ffmpeg/ffprobe at Contents/Frameworks/ (two levels up) and
making every dylib-to-dylib reference inside Frameworks/ self-relative via @loader_path, then
re-signs everything ad hoc so Gatekeeper doesn't reject the now-modified binaries.
"""
import glob
import os
import subprocess
import sys

from setuptools import setup

APP = ["app.py"]
DATA_FILES = [
    ("bin", ["bin/ffmpeg", "bin/ffprobe"]),
]
OPTIONS = {
    "argv_emulation": False,
    "packages": ["gpxpy", "PIL"],
    "includes": ["gpx_parser", "overlay_renderer", "video_builder"],
    "iconfile": "AppIcon.icns",
    "plist": {
        "CFBundleName": "GPX Video Overlay",
        "CFBundleDisplayName": "GPX Video Overlay",
        "CFBundleIdentifier": "de.ebeling-hoppe.gpxoverlay",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
    },
}


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def _deps(path):
    out = subprocess.run(["otool", "-L", path], check=True, capture_output=True, text=True).stdout
    return [line.strip().split(" ")[0] for line in out.splitlines()[1:]]


def _fix_bundled_binaries(app_path):
    contents = os.path.join(app_path, "Contents")
    bin_dir = os.path.join(contents, "Resources", "bin")
    frameworks_dir = os.path.join(contents, "Frameworks")
    if not os.path.isdir(frameworks_dir):
        return

    for binary in ("ffmpeg", "ffprobe"):
        path = os.path.join(bin_dir, binary)
        if not os.path.isfile(path):
            continue
        for dep in _deps(path):
            if dep.startswith("@executable_path/../Frameworks/"):
                name = dep.rsplit("/", 1)[-1]
                _run(["install_name_tool", "-change", dep,
                      f"@executable_path/../../Frameworks/{name}", path])

    for lib_path in glob.glob(os.path.join(frameworks_dir, "*.dylib")):
        for dep in _deps(lib_path):
            if dep.startswith("@executable_path/../Frameworks/"):
                name = dep.rsplit("/", 1)[-1]
                _run(["install_name_tool", "-change", dep, f"@loader_path/{name}", lib_path])
        id_out = subprocess.run(["otool", "-D", lib_path], check=True,
                                 capture_output=True, text=True).stdout.splitlines()
        current_id = id_out[1].strip() if len(id_out) > 1 else ""
        if current_id.startswith("@executable_path/../Frameworks/"):
            name = current_id.rsplit("/", 1)[-1]
            _run(["install_name_tool", "-id", f"@loader_path/{name}", lib_path])

    binaries = [os.path.join(bin_dir, b) for b in ("ffmpeg", "ffprobe") if os.path.isfile(os.path.join(bin_dir, b))]
    dylibs = glob.glob(os.path.join(frameworks_dir, "*.dylib"))
    for target in binaries + dylibs:
        _run(["codesign", "--force", "--sign", "-", target])


setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

if __name__ == "__main__" and "py2app" in sys.argv:
    _app_path = os.path.join("dist", "GPX Video Overlay.app")
    if os.path.isdir(_app_path):
        print("Fixing bundled ffmpeg/ffprobe dylib paths...")
        _fix_bundled_binaries(_app_path)
        print("Done.")
