"""USD and Alembic files are read through Model I/O by the pvql-modelio tool."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from conftest import compiled
import numpy as np
import pytest
import pyvista as pv

from pyvista_quicklook import _scene_export as export_mod
from pyvista_quicklook import formats

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'tests' / 'data'

needs_macos = pytest.mark.skipif(sys.platform != 'darwin', reason='Model I/O is macOS only')


@pytest.fixture(scope='session')
def tool():
    """Compile the pvql-modelio tool from its source."""
    source = ROOT / 'macos' / 'Tools' / 'ModelIOExport.swift'
    return compiled('pvql-modelio', (source,), ('-parse-as-library', '-framework', 'ModelIO'))


@pytest.fixture
def modelio(tool, monkeypatch):
    """Point the exporter at the compiled tool."""
    monkeypatch.setenv('PVQL_MODELIO', str(tool))
    return tool


def test_the_modelio_formats_are_native_types_with_a_reader():
    """Every Model I/O format is a native type, named, and registered with PyVista."""
    for suffix in export_mod.MODELIO_SUFFIXES:
        assert suffix in formats.NATIVE_TYPES
        assert suffix in formats.NATIVE_NAMES
    registered = {entry.extension for entry in pv.registered_readers()}
    assert set(export_mod.MODELIO_SUFFIXES) <= registered


def test_a_missing_tool_is_an_environment_fault(monkeypatch, tmp_path):
    """Without the tool, reading fails as a missing dependency, not as a bad file."""
    monkeypatch.setenv('PVQL_MODELIO', str(tmp_path / 'absent'))
    monkeypatch.setattr(export_mod, 'APP_TOOL_PATHS', ())
    with pytest.raises(ImportError, match='pvql-modelio'):
        export_mod.modelio_tool()


def test_the_tools_report_is_the_reason_a_file_fails(monkeypatch, tmp_path):
    """A file the tool rejects fails with what the tool said."""
    fake = tmp_path / 'fake-tool'
    fake.write_text('#!/bin/sh\necho "nothing in there" >&2\nexit 2\n')
    fake.chmod(0o755)
    monkeypatch.setenv('PVQL_MODELIO', str(fake))
    with pytest.raises(ValueError, match='nothing in there'):
        export_mod.read_modelio(str(tmp_path / 'scene.usd'))


def test_the_manifest_names_each_block(monkeypatch, tmp_path):
    """Each mesh the tool writes becomes a block named as the manifest says."""
    staged = tmp_path / 'staged'
    staged.mkdir()
    pv.Sphere().save(staged / 'mesh-0.ply')
    pv.Cube().triangulate().save(staged / 'mesh-1.ply')
    (staged / 'manifest.json').write_text(
        '[{"name": "Root/Ball", "file": "mesh-0.ply"}, {"name": "Root/Box", "file": "mesh-1.ply"}]'
    )
    fake = tmp_path / 'fake-tool'
    fake.write_text(f'#!/bin/sh\nmkdir -p "$2"\ncp {staged}/* "$2"/\n')
    fake.chmod(0o755)
    monkeypatch.setenv('PVQL_MODELIO', str(fake))
    blocks = export_mod.read_modelio(str(tmp_path / 'scene.usd'))
    assert blocks.keys() == ['Root/Ball', 'Root/Box']
    assert blocks['Root/Ball'].n_points == pv.Sphere().n_points


@needs_macos
@pytest.mark.parametrize('name', ['scene.usda', 'scene.usdc', 'scene.usdz'])
def test_usd_files_are_read_as_named_blocks_in_world_space(modelio, name):
    """A USD stage reads as one block per mesh, named by its prim path, moved by its transform."""
    blocks = pv.read(DATA / name)
    assert isinstance(blocks, pv.MultiBlock)
    assert blocks.keys() == ['Root/Cube', 'Root/Wing']
    cube, wing = blocks['Root/Cube'], blocks['Root/Wing']
    assert cube.n_points == 8
    assert cube.n_faces == 12  # six quads, triangulated by Model I/O
    assert 'RGB' in cube.point_data
    assert len(np.unique(cube['RGB'], axis=0)) == 8
    # The wing is translated by three along x in its own transform.
    assert wing.bounds.x_min == pytest.approx(3.0)
    assert wing.bounds.x_max == pytest.approx(5.0)
    assert tuple(wing['RGB'][0]) == (255, 128, 0)


@needs_macos
def test_usd_files_export_in_colour(modelio, tmp_path):
    """The exporter draws a USD file coloured by its display colours."""
    out = tmp_path / 'out.ply'
    export_mod.export(str(DATA / 'scene.usdz'), str(out), 2_000_000)
    scene = pv.read(out)
    assert scene.n_faces == 13
    assert len(np.unique(scene['RGB'], axis=0)) > 1


@needs_macos
def test_a_file_without_meshes_is_empty(modelio, tmp_path):
    """A USD stage holding no mesh reports that there is no surface to show."""
    empty = tmp_path / 'empty.usda'
    empty.write_text('#usda 1.0\ndef Xform "Root" {}\n')
    with pytest.raises(export_mod.NotAMeshError, match='no surface to show'):
        export_mod.export(str(empty), str(tmp_path / 'out.ply'), 2_000_000)


@needs_macos
def test_the_tool_refuses_what_modelio_does_not_read(tool, tmp_path):
    """A file of a kind Model I/O does not read is refused by name, before anything is written."""
    completed = subprocess.run(
        [str(tool), str(tmp_path / 'mesh.vtu'), str(tmp_path / 'out')],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert 'does not read .vtu' in completed.stderr
    assert not (tmp_path / 'out').exists()


def test_the_installed_app_is_searched_for_the_tool(monkeypatch, tmp_path):
    """Without PVQL_MODELIO the tool is taken from the installed app."""
    monkeypatch.delenv('PVQL_MODELIO', raising=False)
    placed = tmp_path / 'pvql-modelio'
    placed.write_text('')
    monkeypatch.setattr(export_mod, 'APP_TOOL_PATHS', (tmp_path / 'absent', placed))
    assert export_mod.modelio_tool() == placed
