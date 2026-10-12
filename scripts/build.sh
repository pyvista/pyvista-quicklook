#!/usr/bin/env bash
# Build PyVistaQuickLook.app with its Quick Look extension embedded.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD="$ROOT/build"
# The bundle carries the reader's name; the binary inside carries the build's.
BUNDLE_NAME="PyVista Quick Look"
APP_NAME="PyVistaQuickLook"
EXT_NAME="PyVistaQuickLookExtension"
APP_ID="org.pyvista.PyVistaQuickLook"
EXT_ID="$APP_ID.QuickLook"
DEPLOYMENT_TARGET="12.0"

HELPER=""
UNIVERSAL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --helper) HELPER="$2"; shift 2 ;;
    --output) BUILD="$2"; shift 2 ;;
    --universal) UNIVERSAL=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$HELPER" ]]; then
  HELPER=$(command -v pvql || true)
fi
if [[ -z "$HELPER" ]]; then
  HELPER="$HOME/.local/bin/pvql"
fi

APP="$BUILD/$BUNDLE_NAME.app"
APPEX="$APP/Contents/PlugIns/$EXT_NAME.appex"
TARGET="$(uname -m)-apple-macos$DEPLOYMENT_TARGET"

# Compile one binary, for this machine or for both architectures at once.
compile() {
  local output="$1"
  shift
  if [[ "$UNIVERSAL" -eq 0 ]]; then
    swiftc -target "$TARGET" "$@" -o "$output"
    return
  fi
  local slices=()
  for arch in arm64 x86_64; do
    swiftc -target "$arch-apple-macos$DEPLOYMENT_TARGET" "$@" -o "$output.$arch"
    slices+=("$output.$arch")
  done
  lipo -create "${slices[@]}" -output "$output"
  rm -f "${slices[@]}"
}

echo "==> cleaning $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APPEX/Contents/MacOS"

echo "==> generating Info.plist files (helper: $HELPER)"
PYTHONPATH="$ROOT" python3 -m pyvista_quicklook plist \
  --app "$APP/Contents/Info.plist" \
  --extension "$APPEX/Contents/Info.plist" \
  --helper "$HELPER" >/dev/null
printf 'APPL????' > "$APP/Contents/PkgInfo"
printf 'XPC!????' > "$APPEX/Contents/PkgInfo"

echo "==> compiling $APP_NAME"
compile "$APP/Contents/MacOS/$APP_NAME" -O \
  "$ROOT/macos/Shared/Helper.swift" \
  "$ROOT/macos/Shared/Camera.swift" \
  "$ROOT/macos/QuickLookExtension/InfoPanel.swift" \
  "$ROOT/macos/QuickLookExtension/PreviewViewController.swift" \
  "$ROOT/macos/App/main.swift" \
  -framework AppKit -framework QuickLookUI -framework SceneKit

echo "==> compiling RenderScene"
compile "$BUILD/RenderScene" -O -parse-as-library \
  "$ROOT/macos/Shared/Camera.swift" \
  "$ROOT/macos/Tools/RenderScene.swift" \
  -framework AppKit -framework SceneKit

echo "==> compiling $EXT_NAME"
compile "$APPEX/Contents/MacOS/$EXT_NAME" -O -parse-as-library \
  "$ROOT/macos/Shared/Helper.swift" \
  "$ROOT/macos/Shared/Camera.swift" \
  "$ROOT/macos/QuickLookExtension/InfoPanel.swift" \
  "$ROOT/macos/QuickLookExtension/PreviewViewController.swift" \
  -framework QuickLookUI \
  -Xlinker -e -Xlinker _NSExtensionMain

echo "==> signing"
codesign --force --sign - --identifier "$EXT_ID" --timestamp=none \
  --entitlements "$ROOT/macos/QuickLookExtension/QuickLook.entitlements" "$APPEX"
codesign --force --sign - --identifier "$APP_ID" --timestamp=none "$APP"
codesign --verify --deep --strict "$APP"

echo "built $APP"
