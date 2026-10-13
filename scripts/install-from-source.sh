#!/usr/bin/env bash
# Install the pvql helper and its PyVista environment, build the app, and register it.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUNDLE_NAME="PyVista Quick Look"
# Installations before 0.4.1 named the bundle after the build.
LEGACY_BUNDLE_NAME="PyVistaQuickLook"
EXT_NAME="PyVistaQuickLookExtension"
EXT_ID="org.pyvista.PyVistaQuickLook.QuickLook"
THUMB_NAME="PyVistaQuickLookThumbnail"
THUMB_ID="org.pyvista.PyVistaQuickLook.Thumbnail"
HOLDER_BUNDLE_NAME="$BUNDLE_NAME Extensions"
SUPPORT="$HOME/Library/Application Support/PyVistaQuickLook"
VENV="$SUPPORT/venv"
DEST="$HOME/Applications"
# The one version this is built for.
PYTHON_VERSION="${PVQL_PYTHON:-3.14}"
# --excludes, which drops PyVista's stock VTK requirement, landed in uv 0.10.
UV_MIN="0.10"
# The versions in pyproject.toml, so the tests run what the installer provisions.
PYVISTA_SPEC="${PVQL_PYVISTA_SPEC:-pyvista[io,io-override]==0.49.0}"
CVISTA_SPEC="${PVQL_CVISTA_SPEC:-cvista[all]==9.7.0.4}"
# STEP, DXF, and 3MF readers; cascadio, the STEP kernel, reads IGES too.
CAD_SPEC="${PVQL_CAD_SPEC:-pyvista-cad[step-light,3mf]}"
# meshio's MOAB, HMF, MED, and XDMF readers.
H5PY_SPEC="${PVQL_H5PY_SPEC:-h5py}"
PYTHON=""
PREBUILT=""
SKIP_HELPER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) DEST="$2"; shift 2 ;;
    --app) PREBUILT="$2"; shift 2 ;;
    --skip-helper) SKIP_HELPER=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Only building needs a compiler; a prebuilt app is copied as it is.
if [[ -z "$PREBUILT" ]] && ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "Xcode command line tools are needed to build the extension." >&2
  echo "Install them with:  xcode-select --install" >&2
  echo "Or install a prebuilt app with ./install.sh" >&2
  exit 1
fi

# Ask, on descriptor 3, whether to restart the Finder, and restart it only on a yes.
restart_finder() {
  local answer=""
  echo
  echo "Previews won't work until the Finder restarts. Make sure no files are currently being moved or copied."
  printf 'Restart the Finder now with "killall Finder"? [y/N] '
  read -r answer <&3 || true
  if [[ "$answer" == [yY] || "$answer" == [yY][eE][sS] ]]; then
    /usr/bin/killall Finder || true
  else
    echo "Restart it later with \"killall Finder\", or log out and back in."
  fi
}

# uv provisions both the helper and the PyVista environment.
find_uv() {
  local candidate
  if candidate=$(command -v uv 2>/dev/null); then
    echo "$candidate"
    return
  fi
  for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return
    fi
  done
}

# Whether uv version $1 is older than $2.
uv_older_than() {
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" != "$2" ]]
}

UV=$(find_uv)
if [[ -z "$UV" ]]; then
  echo "==> installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV=$(find_uv)
  [[ -n "$UV" ]] || { echo "uv could not be installed" >&2; exit 1; }
fi

UV_VERSION=$("$UV" --version | awk '{print $2}')
if uv_older_than "$UV_VERSION" "$UV_MIN"; then
  echo "==> updating uv ${UV_VERSION:-unknown}, which is older than $UV_MIN"
  "$UV" self update || true
  UV_VERSION=$("$UV" --version | awk '{print $2}')
fi
if uv_older_than "$UV_VERSION" "$UV_MIN"; then
  echo "uv ${UV_VERSION:-unknown} at $UV is older than $UV_MIN; update it and rerun the installer." >&2
  exit 1
fi

if [[ "$SKIP_HELPER" -eq 0 ]]; then
  echo "==> installing the pvql helper"
  "$UV" tool install --force --reinstall --quiet --python "$PYTHON_VERSION" "$ROOT"
fi

HELPER=$(command -v pvql || echo "$HOME/.local/bin/pvql")
if [[ ! -x "$HELPER" ]]; then
  echo "pvql was not found after installation; expected at $HELPER" >&2
  exit 1
fi

# PyVista lives in an environment of its own, so the install never depends on what is
# already on this machine.
echo "==> preparing the PyVista environment in $VENV"
echo "    (about 400 MB the first time)"
mkdir -p "$SUPPORT"
"$UV" venv --quiet --allow-existing --python "$PYTHON_VERSION" "$VENV"
# An environment that has been through a Python change keeps the packages of the Python
# it was built for, which nothing reads any more.
for stale in "$VENV"/lib/python*; do
  if [[ -d "$stale" && "$(basename "$stale")" != "python$PYTHON_VERSION" ]]; then
    echo "    (removing $(basename "$stale") packages, left by an earlier Python)"
    rm -rf "$stale"
  fi
done
# PyVista requires stock VTK, which cvista replaces; the exclusion drops that requirement.
"$UV" pip uninstall --quiet --python "$VENV/bin/python" vtk >/dev/null 2>&1 || true
# uv before 0.13 splits a requirements-file path on spaces, and $SUPPORT has one.
"$UV" pip install --quiet --python "$VENV/bin/python" --upgrade \
  --excludes <(echo vtk) "$PYVISTA_SPEC" "$CVISTA_SPEC" "$CAD_SPEC" "$H5PY_SPEC"
PYTHON="$VENV/bin/python"

echo "==> recording configuration"
"$HELPER" config --init --helper "$HELPER" --python "$PYTHON" >/dev/null

echo "==> warming PyVista and VTK"
"$HELPER" warmup || echo "warm-up skipped; the first preview will be slower" >&2

if [[ -n "$PREBUILT" ]]; then
  SOURCE_APP="$PREBUILT"
else
  "$ROOT/scripts/build.sh" --helper "$HELPER"
  SOURCE_APP="$ROOT/build/$BUNDLE_NAME.app"
fi

APP="$DEST/$BUNDLE_NAME.app"
LEGACY_APP="$DEST/$LEGACY_BUNDLE_NAME.app"
echo "==> installing $APP"
mkdir -p "$DEST"
rm -rf "$APP"
cp -R "$SOURCE_APP" "$APP"

if [[ -d "$LEGACY_APP" ]]; then
  echo "==> removing the previous $LEGACY_APP"
  /usr/bin/pluginkit -r "$LEGACY_APP/Contents/PlugIns/$EXT_NAME.appex" 2>/dev/null || true
  rm -rf "$LEGACY_APP"
fi

LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
# The extensions are held by a nested app that is never launched, so the re-registration
# macOS gives an app without a Developer ID on every launch leaves them alone.
PLUGINS="$APP/Contents/Library/$HOLDER_BUNDLE_NAME.app/Contents/PlugIns"
echo "==> retiring registrations of other copies"
# A record of a copy that was removed or lives elsewhere, the build folder's included, keeps
# Quick Look from starting the extension at all.
"$LSREGISTER" -dump 2>/dev/null \
  | sed -n 's/^[[:space:]]*path:[[:space:]]*\(.*\/PyVista Quick Look\( Extensions\)\{0,1\}\.app\) (0x.*/\1/p' | sort -u \
  | while IFS= read -r other; do
      [[ "$other" == "$APP" || "$other" == "$APP/"* ]] && continue
      "$LSREGISTER" -u "$other" >/dev/null 2>&1 || true
    done || true
# Including the records of an earlier layout of this very copy, under Contents/PlugIns.
/usr/bin/pluginkit -mAvvv 2>/dev/null \
  | sed -n 's/^[[:space:]]*Path = \(.*\/PyVistaQuickLook[A-Za-z]*\.appex\)$/\1/p' \
  | while IFS= read -r other; do
      [[ "$other" == "$PLUGINS/"* ]] && continue
      /usr/bin/pluginkit -r "$other" >/dev/null 2>&1 || true
    done || true

for folder in "$HOME/Applications" "/Applications"; do
  for name in "$BUNDLE_NAME" "$LEGACY_BUNDLE_NAME"; do
    other="$folder/$name.app"
    if [[ -d "$other" && "$other" != "$APP" ]]; then
      echo "note: Launch Services will find $other again; remove it if this copy should answer" >&2
    fi
  done
done
if [[ ! -d "$PLUGINS" ]]; then
  echo "warning: $APP does not hold its extensions in $HOLDER_BUNDLE_NAME.app; a bundle built before 0.9 will not be registered" >&2
fi

echo "==> registering with Launch Services and Quick Look"
"$LSREGISTER" -f -R -trusted "$APP"
/usr/bin/pluginkit -a "$PLUGINS/$EXT_NAME.appex" 2>/dev/null || true
/usr/bin/pluginkit -e use -i "$EXT_ID" 2>/dev/null || true
/usr/bin/pluginkit -a "$PLUGINS/$THUMB_NAME.appex" 2>/dev/null || true
/usr/bin/pluginkit -e use -i "$THUMB_ID" 2>/dev/null || true
/usr/bin/qlmanage -r >/dev/null 2>&1 || true
/usr/bin/qlmanage -r cache >/dev/null 2>&1 || true
# Quick Look keeps the extension running between previews, and would go on using the
# copy that was replaced; it starts the new one on the next preview.
/usr/bin/pkill -x "$EXT_NAME" 2>/dev/null || true
# Quick Look's own service caches which extension answers; it restarts on the next preview.
/usr/bin/killall QuickLookUIService 2>/dev/null || true

# After the app, so macOS lists the service under the app's name.
echo "==> installing the render service"
"$HELPER" service --install --helper "$HELPER"

# A Finder started before the old extension was retired still asks for it by its old identifier.
if [[ "$("$HELPER" finder 2>/dev/null)" == stale ]]; then
  # Under curl | sh, stdin is the script being run, so the answer comes from the terminal.
  if { exec 3</dev/tty; } 2>/dev/null; then
    restart_finder
    exec 3<&-
  else
    echo
    echo "Restart the Finder with \"killall Finder\", or log out and back in, to finish installing."
  fi
fi

echo
echo "Installed. Select a .vtu, .vtp, or .vtk file in the Finder and press space."
if ! command -v pvql >/dev/null 2>&1; then
  echo "The pvql command lives at $HELPER; add ~/.local/bin to PATH to use it by name."
fi
