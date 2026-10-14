"""Generate the Info.plist files for the app and its Quick Look extension."""

from __future__ import annotations

from pathlib import Path
import plistlib
import re
from typing import Any

from . import __version__
from .formats import FORMATS
from .formats import NATIVE_TYPES
from .formats import SYSTEM_TYPES
from .formats import uti_for

APP_NAME = 'PyVista Quick Look'
# The bundle is named for the reader, since the Finder shows its file name and ignores
# CFBundleDisplayName. Everything inside it is named for the build.
APP_BUNDLE = f'{APP_NAME}.app'
LEGACY_APP_BUNDLE = 'PyVistaQuickLook.app'
APP_BUNDLE_ID = 'org.pyvista.PyVistaQuickLook'
LEGACY_APP_BUNDLE_ID = 'io.github.user27182.PyVistaQuickLook'
APP_EXECUTABLE = 'PyVistaQuickLook'
EXT_BUNDLE_ID = f'{APP_BUNDLE_ID}.QuickLook'
LEGACY_EXT_BUNDLE_ID = f'{LEGACY_APP_BUNDLE_ID}.QuickLook'
EXT_EXECUTABLE = 'PyVistaQuickLookExtension'
EXT_PRINCIPAL_CLASS = 'PVQLPreviewViewController'
THUMB_BUNDLE_ID = f'{APP_BUNDLE_ID}.Thumbnail'
THUMB_EXECUTABLE = 'PyVistaQuickLookThumbnail'
THUMB_PRINCIPAL_CLASS = 'PVQLThumbnailProvider'
# The extensions live in an app of their own, nested in the main one and never launched: macOS
# re-registers an app without a Developer ID signature on every launch, and with it the
# extensions it embeds, stopping the one the Finder is showing a preview through.
HOLDER_BUNDLE = APP_BUNDLE
LEGACY_HOLDER_BUNDLE = f'{APP_NAME} Extensions.app'
HOLDER_BUNDLE_ID = f'{APP_BUNDLE_ID}.Extensions'
HOLDER_EXECUTABLE = 'PyVistaQuickLookExtensions'
# AppIcon.icns, which the build copies from icon/ into both apps' Resources.
ICON_FILE = 'AppIcon'
# Only these formats can carry a picture of themselves.
THUMBNAIL_EXTENSIONS = ('.pv',)


def bundle_version(version: str = __version__) -> str:
    """Return the leading numeric part of a version, which is all a bundle may carry."""
    match = re.match(r'\d+(?:\.\d+){0,2}', version)
    return match.group(0) if match else '0.0.0'


VERSION = bundle_version()
# The oldest macOS CI builds and tests on.
MIN_MACOS = '15.0'


def exported_types(extensions: list[str]) -> list[dict[str, Any]]:
    """Return ``UTExportedTypeDeclarations`` entries for the claimed extensions."""
    declarations = []
    for ext in extensions:
        description = FORMATS.get(ext)
        declarations.append(
            {
                'UTTypeIdentifier': uti_for(ext),
                'UTTypeDescription': description or f'{ext.lstrip(".").upper()} Data',
                'UTTypeConformsTo': ['public.data'],
                'UTTypeTagSpecification': {'public.filename-extension': [ext.lstrip('.')]},
            }
        )
    return declarations


def document_types(extensions: list[str]) -> list[dict[str, Any]]:
    """Return ``CFBundleDocumentTypes`` entries, so the app can be chosen to open a file."""
    return [
        {
            'CFBundleTypeName': 'Mesh',
            'CFBundleTypeRole': 'Viewer',
            # Offered under Open With, below any app that declares a type as its own default.
            'LSHandlerRank': 'Alternate',
            'LSItemContentTypes': [uti_for(e) for e in extensions] + list(NATIVE_TYPES.values()),
        }
    ]


def app_plist(extensions: list[str], helper: str | None = None) -> dict[str, Any]:
    """Return the host application's Info.plist contents."""
    plist: dict[str, Any] = {
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': APP_NAME,
        'CFBundleIdentifier': APP_BUNDLE_ID,
        'CFBundleExecutable': APP_EXECUTABLE,
        'CFBundleIconFile': ICON_FILE,
        'CFBundlePackageType': 'APPL',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleDevelopmentRegion': 'en',
        'CFBundleSupportedPlatforms': ['MacOSX'],
        'DTPlatformName': 'macosx',
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'LSMinimumSystemVersion': MIN_MACOS,
        'LSApplicationCategoryType': 'public.app-category.developer-tools',
        'NSPrincipalClass': 'NSApplication',
        # SceneKit then blends colours as VTK does, rather than in linear light.
        'SCNDisableLinearSpaceRendering': True,
        'NSHighResolutionCapable': True,
        'UTExportedTypeDeclarations': exported_types(extensions),
        'CFBundleDocumentTypes': document_types(extensions),
    }
    if helper:
        plist['PVQLHelperPath'] = helper
    return plist


def extension_plist(extensions: list[str], helper: str | None = None) -> dict[str, Any]:
    """Return the Quick Look extension's Info.plist contents."""
    plist: dict[str, Any] = {
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': f'{APP_NAME} Extension',
        'CFBundleIdentifier': EXT_BUNDLE_ID,
        'CFBundleExecutable': EXT_EXECUTABLE,
        'CFBundlePackageType': 'XPC!',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleDevelopmentRegion': 'en',
        'CFBundleSupportedPlatforms': ['MacOSX'],
        'DTPlatformName': 'macosx',
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'LSMinimumSystemVersion': MIN_MACOS,
        'SCNDisableLinearSpaceRendering': True,
        'NSExtension': {
            'NSExtensionPointIdentifier': 'com.apple.quicklook.preview',
            'NSExtensionPrincipalClass': EXT_PRINCIPAL_CLASS,
            'NSExtensionAttributes': {
                # The native types are claimed always; Quick Look only lets the claim count
                # once the native_formats setting stands macOS's own viewer down.
                'QLSupportedContentTypes': [uti_for(e) for e in extensions]
                + list(SYSTEM_TYPES)
                + list(NATIVE_TYPES.values()),
                'QLSupportsSearchableItems': False,
            },
        },
    }
    if helper:
        plist['PVQLHelperPath'] = helper
    return plist


def holder_plist(helper: str | None = None) -> dict[str, Any]:
    """Return the Info.plist contents of the app that holds the extensions and the service."""
    plist: dict[str, Any] = {
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': APP_NAME,
        'CFBundleIdentifier': HOLDER_BUNDLE_ID,
        'CFBundleExecutable': HOLDER_EXECUTABLE,
        'CFBundleIconFile': ICON_FILE,
        'CFBundlePackageType': 'APPL',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleDevelopmentRegion': 'en',
        'CFBundleSupportedPlatforms': ['MacOSX'],
        'DTPlatformName': 'macosx',
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'LSMinimumSystemVersion': MIN_MACOS,
        'LSBackgroundOnly': True,
    }
    if helper:
        plist['PVQLHelperPath'] = helper
    return plist


def plugins_folder(app: Path, holder: str = HOLDER_BUNDLE) -> Path:
    """Return the folder of an installed app that holds its Quick Look extensions."""
    return app / 'Contents' / 'Library' / holder / 'Contents' / 'PlugIns'


def thumbnail_plist(extensions: list[str]) -> dict[str, Any]:
    """Return the Finder thumbnail extension's Info.plist contents."""
    pictured = [uti_for(e) for e in extensions if e in THUMBNAIL_EXTENSIONS]
    return {
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': f'{APP_NAME} Thumbnails',
        'CFBundleIdentifier': THUMB_BUNDLE_ID,
        'CFBundleExecutable': THUMB_EXECUTABLE,
        'CFBundlePackageType': 'XPC!',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleDevelopmentRegion': 'en',
        'CFBundleSupportedPlatforms': ['MacOSX'],
        'DTPlatformName': 'macosx',
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'LSMinimumSystemVersion': MIN_MACOS,
        'NSExtension': {
            'NSExtensionPointIdentifier': 'com.apple.quicklook.thumbnail',
            'NSExtensionPrincipalClass': THUMB_PRINCIPAL_CLASS,
            'NSExtensionAttributes': {
                'QLSupportedContentTypes': pictured,
                'QLThumbnailMinimumDimension': 0,
            },
        },
    }


def write(plist: dict[str, Any], destination: str | Path) -> Path:
    """Write a plist dictionary to disk in XML format and return its path."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as handle:
        plistlib.dump(plist, handle, sort_keys=False)
    return path
