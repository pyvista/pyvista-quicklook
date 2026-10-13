"""Tests for the preview cache: what lands in it, what is recorded, and what is pruned."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time

import pytest

from pyvista_quicklook import cache
from pyvista_quicklook import config
from pyvista_quicklook import convert
from pyvista_quicklook import environment


def test_fill_rejects_an_empty_build(tmp_path):
    """A build that writes nothing is an error, and leaves nothing behind."""
    out = tmp_path / 'preview.png'
    with pytest.raises(environment.RenderError, match='Nothing was written'):
        cache.fill(out, {}, 'mesh.vtu', lambda scratch: scratch.touch(), ('mesh.vtu', 1, 1))
    assert not list(tmp_path.iterdir())


def test_holds_wants_a_file_with_something_in_it(tmp_path):
    """A missing entry, an empty one, or a folder in its place does not count as cached."""
    assert not cache.holds(tmp_path / 'absent.ply')
    (tmp_path / 'empty.ply').touch()
    assert not cache.holds(tmp_path / 'empty.ply')
    (tmp_path / 'folder.ply').mkdir()
    assert not cache.holds(tmp_path / 'folder.ply')
    (tmp_path / 'built.ply').write_bytes(b'x')
    assert cache.holds(tmp_path / 'built.ply')


def test_scratch_paths_sit_beside_the_entry_and_may_change_suffix(tmp_path):
    """A build's scratch file is named after its entry, this thread, and the suffix asked for."""
    out = tmp_path / 'abc.ply'
    scratch = cache.scratch_path(out)
    assert scratch.parent == tmp_path
    assert scratch.name == f'abc.{cache.scratch_tag()}.tmp.ply'
    assert cache.scratch_path(out, '.json').name == f'abc.{cache.scratch_tag()}.tmp.json'
    assert cache.scratch(scratch)


def test_scratch_files_are_named_per_thread():
    """Two builds in one process never write the same scratch file."""
    tags = []
    worker = threading.Thread(target=lambda: tags.append(cache.scratch_tag()))
    worker.start()
    worker.join(5)
    assert tags[0] != cache.scratch_tag()


def test_scenes_of_other_versions_are_discarded(tmp_path, monkeypatch):
    """An older exporter's scenes and descriptions go; current ones and images stay."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path)
    current = convert.SCENE_VERSION
    names = ('old-1.ply', 'old-1.json', f'{current}-2.ply', f'{current}-2.json', 'image.png')
    for name in names:
        (tmp_path / name).write_bytes(b'x')
    assert cache.discard_other_versions(current) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        f'{current}-2.json',
        f'{current}-2.ply',
        'image.png',
    ]


def test_stale_scratch_files_are_discarded(monkeypatch, tmp_path):
    """Scratch left by a stopped build goes once it is old; a fresh one may still be in use."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path)
    old, fresh = tmp_path / 'a.1.2.tmp.json', tmp_path / 'b.1.2.tmp.ply'
    for path in (old, fresh, tmp_path / 'kept.json'):
        path.write_text('x')
    stamp = time.time() - 700
    os.utime(old, (stamp, stamp))
    assert cache.discard_scratch(600) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ['b.1.2.tmp.ply', 'kept.json']


def aged(path, seconds=3600):
    """Return ``path`` with its modification time moved into the past."""
    past = time.time() - seconds
    os.utime(path, (past, past))
    return path


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    """Return a function that caches an entry for a new source file, recording its identity."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path / 'cache')

    def make(name, suffix='.ply'):
        source = tmp_path / name
        source.write_text('mesh')
        identity = cache.identity_of(source)
        out = config.CACHE_DIR / f'{cache.digest(identity, [])}{suffix}'
        return source, cache.fill(out, {}, name, lambda s: s.write_text('built'), identity)

    return make


def test_fill_records_the_source_beside_the_entry(recorded):
    """Each entry has a record naming the file, its time, and its size."""
    source, entry = recorded('mesh.vtu')
    record = json.loads(cache.record_path(entry).read_text())
    assert record == dict(zip(('path', 'mtime', 'size'), cache.identity_of(source), strict=True))


def test_prune_keeps_entries_of_unchanged_files(recorded):
    """A file that is still as it was keeps its preview."""
    _, entry = recorded('mesh.vtu')
    assert cache.prune() == 0
    assert entry.is_file()
    assert cache.record_path(entry).is_file()


def test_prune_drops_entries_of_deleted_files(recorded):
    """A file that has gone takes its preview and record with it."""
    source, _ = recorded('mesh.vtu')
    _, kept = recorded('other.vtu', '.png')
    source.unlink()
    assert cache.prune() == 1
    assert sorted(p.name for p in config.CACHE_DIR.iterdir()) == sorted(
        [kept.name, cache.record_path(kept).name]
    )


def test_prune_drops_entries_of_changed_files(recorded):
    """A file edited since its preview was built has that preview dropped, same size or not."""
    source, entry = recorded('mesh.vtu', '.json')
    stamp = source.stat().st_mtime
    source.write_text('MESH')
    os.utime(source, (stamp + 5, stamp + 5))
    assert cache.prune() == 1
    assert not entry.exists()


def test_prune_follows_a_link_to_its_target(recorded, tmp_path):
    """A link is recorded by what it points to, so a rewritten target outdates its preview."""
    _, entry = recorded('target.vtu')
    link = tmp_path / 'link.vtu'
    link.symlink_to(tmp_path / 'target.vtu')
    stat = link.stat()
    record = {'path': str(link), 'mtime': int(stat.st_mtime), 'size': stat.st_size}
    cache.record_path(entry).write_text(json.dumps(record))
    os.utime(link, (1, 1), follow_symlinks=False)
    assert cache.prune() == 0
    (tmp_path / 'target.vtu').write_text('a larger mesh')
    assert cache.prune() == 1
    assert not entry.exists()


def test_prune_keeps_entries_it_cannot_check(recorded, tmp_path):
    """A file in a folder that cannot be looked in is given the benefit of the doubt."""
    _, entry = recorded('mesh.vtu')
    locked = tmp_path / 'locked'
    locked.mkdir()
    record = {'path': str(locked / 'mesh.vtu'), 'mtime': 1, 'size': 1}
    cache.record_path(entry).write_text(json.dumps(record))
    locked.chmod(0)
    try:
        assert cache.prune() == 0
    finally:
        locked.chmod(0o755)
    assert entry.is_file()


def test_prune_leaves_files_it_is_told_not_to_look_at(recorded):
    """A file the caller says not to look at keeps its preview, even once it has gone."""
    source, entry = recorded('mesh.vtu')
    source.unlink()
    assert cache.prune(may_look=lambda path: False) == 0
    assert entry.is_file()
    assert cache.prune(may_look=lambda path: True) == 1


def test_prune_keeps_entries_on_unplugged_disks(recorded):
    """A file on a volume that is not mounted may come back, so its preview stays."""
    _, entry = recorded('mesh.vtu')
    absent = '/Volumes/pvql-not-mounted-disk/mesh.vtu'
    cache.record_path(entry).write_text(json.dumps({'path': absent, 'mtime': 1, 'size': 1}))
    assert cache.prune() == 0
    assert entry.is_file()


@pytest.mark.parametrize(
    ('path', 'reach'),
    [
        ('/Volumes/pvql-not-mounted-disk/mesh.vtu', True),
        ('/net/server/mesh.vtu', True),
        ('/Network/Servers/server/mesh.vtu', True),
        ('/Volumes', False),
        ('/Users/mesh.vtu', False),
    ],
)
def test_paths_that_would_mount_or_miss_a_disk_are_out_of_reach(path, reach):
    """Unplugged disks and automounted shares are not looked at."""
    assert cache.out_of_reach(Path(path)) is reach


def test_prune_drops_unrecorded_entries_and_lone_records_once_settled(tmp_path, monkeypatch):
    """Entries from before records were kept go, and so do records without an entry."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path)
    old = tmp_path / 'old.png'
    old.write_bytes(b'x')
    aged(old)
    lone = tmp_path / 'gone.ply.src'
    lone.write_text('{}')
    aged(lone)
    fresh = tmp_path / 'landing.ply'
    fresh.write_bytes(b'x')
    arriving = tmp_path / 'arriving.ply.src'
    arriving.write_text('{}')
    building = [
        tmp_path / 'a.1.2.tmp.json',
        tmp_path / 'b.1.2.tmp.ply',
        tmp_path / 'c.1.2.tmp.src',
    ]
    for path in building:
        path.write_text('x')
        aged(path, 120)
    (tmp_path / '.preloaded').touch()
    assert cache.prune() == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        ['.preloaded', 'landing.ply', 'arriving.ply.src', *(p.name for p in building)]
    )


def test_discarding_an_old_scene_takes_its_record(tmp_path, monkeypatch):
    """A scene from an older exporter is discarded along with its record."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path)
    (tmp_path / 'old-1.ply').write_bytes(b'x')
    (tmp_path / 'old-1.ply.src').write_text('{}')
    assert cache.discard_other_versions(convert.SCENE_VERSION) == 1
    assert not list(tmp_path.iterdir())


def test_clear_removes_records(tmp_path, monkeypatch):
    """Clearing the cache leaves no records behind."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path)
    (tmp_path / 'a.png').write_bytes(b'x')
    (tmp_path / 'a.png.src').write_text('{}')
    assert cache.clear() == 1
    assert not list(tmp_path.iterdir())


def test_prune_drops_an_entry_whose_record_is_unreadable_and_logs_it(recorded, monkeypatch):
    """A record that cannot be read vouches for nothing, and the removal is logged."""
    _, entry = recorded('mesh.vtu')
    cache.record_path(entry).write_text('not json')
    logged = []
    monkeypatch.setattr(cache, 'log', lambda config, message: logged.append(message))
    assert cache.prune({'log': True}) == 1
    assert logged == ['pruned 1 previews and descriptions of changed or deleted files']


def test_a_record_that_cannot_be_written_leaves_nothing(tmp_path, monkeypatch):
    """A failed record write removes its scratch and the build's, and lands no entry."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path)

    def full(self, text):
        Path.touch(self)
        raise OSError(28, 'No space left on device')

    out = tmp_path / 'preview.png'
    monkeypatch.setattr(Path, 'write_text', full)
    with pytest.raises(OSError, match='No space'):
        cache.fill(out, {}, 'mesh.vtu', lambda scratch: scratch.write_bytes(b'x'), ('m', 1, 1))
    assert not list(tmp_path.iterdir())


def test_a_record_lands_before_its_entry(tmp_path, monkeypatch):
    """Pruning never sees a landed entry without its record."""
    monkeypatch.setattr(cache.config_mod, 'CACHE_DIR', tmp_path)
    out = tmp_path / 'preview.png'
    seen = []
    remember = cache.remember

    def watched(entry, identity):
        seen.append(entry.exists())
        remember(entry, identity)

    monkeypatch.setattr(cache, 'remember', watched)
    cache.fill(out, {}, 'mesh.vtu', lambda scratch: scratch.write_bytes(b'x'), ('m', 1, 1))
    assert seen == [False]
    assert cache.record_path(out).is_file()


def test_a_file_that_vanished_is_not_settled(tmp_path):
    """A file removed between listing and looking is left to whoever removed it."""
    assert not cache.settled(tmp_path / 'gone.png', time.time())
