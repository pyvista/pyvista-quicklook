"""Tests for the property lists the app and its extensions are built with."""

from __future__ import annotations

from pathlib import Path
import plistlib

from pyvista_quicklook import formats
from pyvista_quicklook import plist


def test_app_plist_declares_every_extension():
    """The app exports one uniform type per claimed extension."""
    extensions = ['.vtu', '.vtp']
    declarations = plist.app_plist(extensions)['UTExportedTypeDeclarations']
    tags = [d['UTTypeTagSpecification']['public.filename-extension'] for d in declarations]
    assert tags == [['vtu'], ['vtp']]


def test_extension_plist_matches_the_app():
    """The extension supports exactly the types the app exports."""
    extensions = ['.vtu', '.vtp']
    app = plist.app_plist(extensions)
    ext = plist.extension_plist(extensions)
    exported = [d['UTTypeIdentifier'] for d in app['UTExportedTypeDeclarations']]
    supported = ext['NSExtension']['NSExtensionAttributes']['QLSupportedContentTypes']
    assert exported == supported[: len(exported)]
    assert ext['NSExtension']['NSExtensionPrincipalClass'] == plist.EXT_PRINCIPAL_CLASS
    assert ext['NSExtension']['NSExtensionPointIdentifier'] == 'com.apple.quicklook.preview'


def test_scenes_blend_colours_as_vtk_does():
    """The app and the extension both turn off SceneKit's linear-light rendering."""
    assert plist.app_plist(['.vtu'])['SCNDisableLinearSpaceRendering'] is True
    assert plist.extension_plist(['.vtu'])['SCNDisableLinearSpaceRendering'] is True


def test_both_apps_name_the_icon_the_repo_ships():
    """The app and the extensions' holder name an icon that exists in icon/."""
    root = Path(__file__).resolve().parents[1]
    for info in (plist.app_plist(['.vtu']), plist.holder_plist()):
        assert (root / 'icon' / f'{info["CFBundleIconFile"]}.icns').is_file()


def test_thumbnail_plist_covers_only_types_that_carry_a_picture():
    """The Finder asks the thumbnail extension only for claimed types that can embed one."""
    thumb = plist.thumbnail_plist(['.pv', '.3mf', '.vtu'])
    attributes = thumb['NSExtension']['NSExtensionAttributes']
    assert attributes['QLSupportedContentTypes'] == [formats.uti_for('.pv')]
    assert thumb['NSExtension']['NSExtensionPointIdentifier'] == 'com.apple.quicklook.thumbnail'
    assert thumb['NSExtension']['NSExtensionPrincipalClass'] == plist.THUMB_PRINCIPAL_CLASS
    assert (
        plist.thumbnail_plist(['.vtu'])['NSExtension']['NSExtensionAttributes'][
            'QLSupportedContentTypes'
        ]
        == []
    )


def test_system_types_are_supported_but_not_exported():
    """A type macOS declares is claimed by the extension and never exported by the app."""
    app = plist.app_plist(['.vtu'])
    ext = plist.extension_plist(['.vtu'])
    supported = ext['NSExtension']['NSExtensionAttributes']['QLSupportedContentTypes']
    exported = [d['UTTypeIdentifier'] for d in app['UTExportedTypeDeclarations']]
    for uti in formats.SYSTEM_TYPES:
        assert uti in supported
        assert uti not in exported


def test_the_app_can_open_its_own_types_and_the_native_ones():
    """The app opens what it claims and macOS's own 3D types, which Quick Look keeps to."""
    app = plist.app_plist(['.vtu'])
    ext = plist.extension_plist(['.vtu'])
    supported = ext['NSExtension']['NSExtensionAttributes']['QLSupportedContentTypes']
    exported = [d['UTTypeIdentifier'] for d in app['UTExportedTypeDeclarations']]
    [documents] = app['CFBundleDocumentTypes']
    for ext_name, uti in formats.NATIVE_TYPES.items():
        assert uti.startswith('public.'), ext_name
        # Quick Look never asks the extension for these, so it does not offer to preview them.
        assert uti not in supported
        assert uti not in exported
    # Listed under Open With, below an app that declares a type as its default.
    assert documents['LSHandlerRank'] == 'Alternate'
    assert set(documents['LSItemContentTypes']) == set(exported) | set(
        formats.NATIVE_TYPES.values()
    )
    assert set(formats.NATIVE_TYPES).isdisjoint(formats.FORMATS)
    assert set(formats.NATIVE_TYPES).isdisjoint(formats.UNCLAIMED)


def test_plists_are_writable(tmp_path):
    """Generated plists round-trip through plistlib."""
    target = tmp_path / 'Info.plist'
    plist.write(plist.app_plist(['.vtu']), target)
    assert plistlib.loads(target.read_bytes())['CFBundleIdentifier'] == plist.APP_BUNDLE_ID


def test_bundle_version_is_numeric():
    """Bundle versions carry only the leading numeric part."""
    assert plist.bundle_version('1.2.3.dev4+gabc') == '1.2.3'
    assert plist.bundle_version('nonsense') == '0.0.0'
