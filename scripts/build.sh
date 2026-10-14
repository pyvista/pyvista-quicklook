#!/usr/bin/env bash
# Build PyVistaQuickLook.app with its Quick Look extensions in a nested, never-launched app.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD="$ROOT/build"
# The bundle carries the reader's name; the binary inside carries the build's.
BUNDLE_NAME="PyVista Quick Look"
APP_NAME="PyVistaQuickLook"
EXT_NAME="PyVistaQuickLookExtension"
APP_ID="org.pyvista.PyVistaQuickLook"
EXT_ID="$APP_ID.QuickLook"
THUMB_NAME="PyVistaQuickLookThumbnail"
THUMB_ID="$APP_ID.Thumbnail"
HOLDER_BUNDLE_NAME="$BUNDLE_NAME"
HOLDER_NAME="PyVistaQuickLookExtensions"
HOLDER_ID="$APP_ID.Extensions"
# The oldest macOS CI builds and tests on, as plist.MIN_MACOS.
DEPLOYMENT_TARGET="15.0"

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

# The package needs its own Python, which the helper carries; uv provides it otherwise.
if [[ -x "$HELPER" ]]; then
  PLIST_COMMAND=("$HELPER")
elif command -v uv >/dev/null 2>&1; then
  PLIST_COMMAND=(uv run --project "$ROOT" python -m pyvista_quicklook)
else
  echo "neither the pvql helper ($HELPER) nor uv was found; one of them generates the Info.plist files" >&2
  exit 1
fi

# Only a helper that exists is named in the bundle; the app finds one on PATH otherwise.
BAKED_HELPER=""
if [[ -x "$HELPER" ]]; then
  BAKED_HELPER="$HELPER"
fi

APP="$BUILD/$BUNDLE_NAME.app"
# macOS re-registers an app without a Developer ID signature, and the extensions it embeds,
# on every launch; held by an app that is never launched, the extensions stay registered.
HOLDER="$APP/Contents/Library/$HOLDER_BUNDLE_NAME.app"
APPEX="$HOLDER/Contents/PlugIns/$EXT_NAME.appex"
THUMBEX="$HOLDER/Contents/PlugIns/$THUMB_NAME.appex"
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
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$HOLDER/Contents/MacOS" \
  "$HOLDER/Contents/Resources" \
  "$APPEX/Contents/MacOS" "$THUMBEX/Contents/MacOS"
cp "$ROOT/icon/AppIcon.icns" "$APP/Contents/Resources/"
cp "$ROOT/icon/AppIcon.icns" "$HOLDER/Contents/Resources/"

echo "==> generating Info.plist files (helper: $HELPER)"
"${PLIST_COMMAND[@]}" plist \
  --app "$APP/Contents/Info.plist" \
  --extension "$APPEX/Contents/Info.plist" \
  --thumbnail "$THUMBEX/Contents/Info.plist" \
  --holder "$HOLDER/Contents/Info.plist" \
  --helper "$BAKED_HELPER" >/dev/null
printf 'APPL????' > "$APP/Contents/PkgInfo"
printf 'APPL????' > "$HOLDER/Contents/PkgInfo"
printf 'XPC!????' > "$APPEX/Contents/PkgInfo"
printf 'XPC!????' > "$THUMBEX/Contents/PkgInfo"

# The preview code, which the app and its preview extension both carry.
SHARED_SOURCES=(
  "$ROOT/macos/Shared/Helper.swift"
  "$ROOT/macos/Shared/EmbeddedThumbnail.swift"
  "$ROOT/macos/Shared/Camera.swift"
  "$ROOT/macos/QuickLookExtension/InfoPanel.swift"
  "$ROOT/macos/QuickLookExtension/PreviewViewController.swift"
)
ENTITLEMENTS="$ROOT/macos/Shared/QuickLook.entitlements"

echo "==> compiling $APP_NAME"
compile "$APP/Contents/MacOS/$APP_NAME" -O \
  "${SHARED_SOURCES[@]}" \
  "$ROOT/macos/App/main.swift" \
  -framework AppKit -framework QuickLookUI -framework SceneKit

echo "==> compiling RenderScene"
compile "$BUILD/RenderScene" -O -parse-as-library \
  "$ROOT/macos/Shared/Camera.swift" \
  "$ROOT/macos/Tools/RenderScene.swift" \
  -framework AppKit -framework SceneKit \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
  -Xlinker "$ROOT/macos/Tools/RenderScene-Info.plist"

echo "==> compiling pvql-modelio"
compile "$APP/Contents/MacOS/pvql-modelio" -O -parse-as-library \
  "$ROOT/macos/Tools/ModelIOExport.swift" \
  -framework ModelIO
cp "$APP/Contents/MacOS/pvql-modelio" "$BUILD/pvql-modelio"

echo "==> compiling $EXT_NAME"
compile "$APPEX/Contents/MacOS/$EXT_NAME" -O -parse-as-library \
  "${SHARED_SOURCES[@]}" \
  -framework QuickLookUI \
  -Xlinker -e -Xlinker _NSExtensionMain

echo "==> compiling $HOLDER_NAME"
compile "$HOLDER/Contents/MacOS/$HOLDER_NAME" -O "$ROOT/macos/Holder/main.swift"

echo "==> compiling $THUMB_NAME"
compile "$THUMBEX/Contents/MacOS/$THUMB_NAME" -O -parse-as-library \
  "$ROOT/macos/Shared/EmbeddedThumbnail.swift" \
  "$ROOT/macos/ThumbnailExtension/ThumbnailProvider.swift" \
  -framework QuickLookThumbnailing \
  -Xlinker -e -Xlinker _NSExtensionMain

echo "==> signing"
codesign --force --sign - --identifier "$EXT_ID" --timestamp=none --entitlements "$ENTITLEMENTS" "$APPEX"
codesign --force --sign - --identifier "$THUMB_ID" --timestamp=none --entitlements "$ENTITLEMENTS" "$THUMBEX"
codesign --force --sign - --identifier "$APP_ID.modelio" --timestamp=none "$APP/Contents/MacOS/pvql-modelio"
codesign --force --sign - --identifier "$HOLDER_ID" --timestamp=none "$HOLDER"
codesign --force --sign - --identifier "$APP_ID" --timestamp=none "$APP"
codesign --verify --deep --strict "$APP"

echo "built $APP"
