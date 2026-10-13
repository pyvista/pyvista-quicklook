#!/bin/sh
# One-line installer for PyVista Quick Look. Safe to pipe from curl.
set -eu

REPO="${PVQL_REPO:-pyvista/pyvista-quicklook}"
SUPPORT="$HOME/Library/Application Support/PyVistaQuickLook"
SRC="${PVQL_SRC:-$SUPPORT/src}"
ASSET="PyVistaQuickLook.zip"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Whether a release has the published app attached; one is tagged before CI has built it.
has_app() {
  [ "$(curl -sIL -o /dev/null -w '%{http_code}' "https://github.com/$REPO/releases/download/$1/$ASSET")" = 200 ]
}

# The newest release, pre-releases aside, with the app attached, so a release CI is still
# building is passed over. The API lists one key a line; anything else leaves no tags, and
# the caller falls back.
published_tag() {
  for tag in $(curl -sf "https://api.github.com/repos/$REPO/releases?per_page=10" \
      | awk -F'"' '/^ *"tag_name":/ { tag = $4 } /^ *"prerelease": *false/ { print tag }'); do
    if has_app "$tag"; then
      say "$tag"
      return 0
    fi
  done
  return 1
}

[ "$(uname -s)" = 'Darwin' ] || die 'PyVista Quick Look only runs on macOS.'
# The oldest macOS it is built and tested on, as plist.MIN_MACOS.
MIN_MACOS=15
MACOS=$(sw_vers -productVersion)
[ "${MACOS%%.*}" -ge "$MIN_MACOS" ] || die "PyVista Quick Look needs macOS $MIN_MACOS or newer; this Mac runs $MACOS."

# Run from the checkout this script lives in when there is one, otherwise fetch it.
HERE=''
case "${0:-}" in
  */*) HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true) ;;
esac

# A published app is only used with the scripts of its own release; a checkout or a branch
# builds its own, so the app and the helper never drift apart.
DOWNLOAD=''
if [ -n "$HERE" ] && [ -x "$HERE/scripts/install-from-source.sh" ] && [ -d "$HERE/macos" ]; then
  ROOT="$HERE"
  say "==> installing from the checkout at $ROOT"
else
  TAG="${PVQL_TAG:-}"
  if [ -z "$TAG" ] && [ -z "${PVQL_BRANCH:-}" ]; then
    # Without the API, the latest release, whose app may still be missing.
    TAG=$(published_tag || curl -sI "https://github.com/$REPO/releases/latest" | tr -d '\r' \
      | sed -n 's#^[Ll]ocation: .*/tag/##p')
  fi
  if [ -n "$TAG" ]; then
    ARCHIVE="https://github.com/$REPO/archive/refs/tags/$TAG.tar.gz"
    DOWNLOAD="https://github.com/$REPO/releases/download/$TAG/$ASSET"
    VERSION="${TAG#v}"
    say "==> installing PyVista Quick Look $TAG"
  else
    ARCHIVE="https://github.com/$REPO/archive/refs/heads/${PVQL_BRANCH:-main}.tar.gz"
    VERSION='0.0.0'
    say "==> no published release; installing from ${PVQL_BRANCH:-main}"
  fi
  say "==> downloading the installer into $SRC"
  rm -rf "$SRC"
  mkdir -p "$SRC"
  curl -LsSf "$ARCHIVE" | tar -xz -C "$SRC" --strip-components 1
  ROOT="$SRC"
  # A downloaded tarball carries no history for setuptools-scm to read.
  SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYVISTA_QUICKLOOK="${PVQL_VERSION:-$VERSION}"
  export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYVISTA_QUICKLOOK
fi

# A published build spares the reader a compiler; falling back to source needs one.
ZIP="${SUPPORT:?}/$ASSET"
UNPACKED="${SUPPORT:?}/unpacked"
APP=''
mkdir -p "$SUPPORT"
rm -rf "$ZIP" "$UNPACKED"

if [ -n "$DOWNLOAD" ] && curl -LsSf -o "$ZIP" "$DOWNLOAD"; then
  say '==> unpacking the published app'
  mkdir -p "$UNPACKED"
  if ditto -x -k "$ZIP" "$UNPACKED"; then
    APP=$(find "$UNPACKED" -maxdepth 1 -name '*.app' -print -quit)
  fi
  rm -f "$ZIP"
fi

if [ -n "$APP" ] && [ -d "$APP" ]; then
  "$ROOT/scripts/install-from-source.sh" --app "$APP" "$@" && outcome=0 || outcome=$?
else
  if [ -n "$DOWNLOAD" ]; then
    say '==> no published build for this release; building from source'
  else
    say '==> building the app from source, which needs the Xcode command line tools'
  fi
  "$ROOT/scripts/install-from-source.sh" "$@" && outcome=0 || outcome=$?
fi
# Nothing uses the downloads once the app and the helper are installed.
rm -rf "$UNPACKED"
[ "$ROOT" = "$SRC" ] && rm -rf "$SRC"
exit "$outcome"
