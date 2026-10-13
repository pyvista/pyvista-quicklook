# Developing PyVista Quick Look

```bash
uv sync --group dev
uv run pytest tests/          # helper and exporter tests, with coverage
uvx --python 3.14 pre-commit run --all-files
./scripts/build.sh            # compile and sign the app bundle
./scripts/install-from-source.sh   # build, install and register the result
```

Everything runs on one Python, 3.14: the tests, the `pvql` helper in its uv tool
environment, and the PyVista environment beside it. `[tool.uv]` in `pyproject.toml`
excludes stock VTK from the resolve, so the test environment holds the same packages the
installer provisions.

PyVista and cvista are pinned to one release each, in `pyproject.toml` and
`scripts/install-from-source.sh` alike. Bumping either reruns a test that compares every
extension the environment can read with the format table, so a reader that PyVista adds,
drops, or moves to a package of its own shows up as a failure to resolve in `formats.py`.
The README's format tables are generated from PyVista's reader tables and checked by
another test:

```bash
uv run python -m pyvista_quicklook._formats_table README.md
```

## Names

One project, three names, each where its own conventions apply: `pyvista-quicklook` is
the package on PyPI and the program the render service runs as, `pvql` is the command,
and `PyVista Quick Look` is what the reader sees, from the app bundle to the panel. The
build's own files keep the compact `PyVistaQuickLook` spelling: the bundle identifier,
the executables, and the downloadable zip.

## Layout of the app

The Quick Look extensions live in a second `PyVista Quick Look.app`, nested inside the
main app and never launched. macOS re-registers an app without a Developer ID signature
on every launch, and with it the extensions it embeds, which stops the extension that is
showing a preview; held by an app that is never launched, they stay registered. The
installer unregisters every other copy of the app before registering, since a stale
Launch Services record keeps Quick Look from starting the extension at all.

The nested app also carries the render service, as a launch agent in
`Contents/Library/LaunchAgents` that runs `Contents/MacOS/pvqld`, which starts `pvql
daemon`. The installer runs the nested app's binary directly, never through Launch
Services, to register the agent with `SMAppService`; Login Items then lists the
extensions and the service as one entry, under the nested bundle's file name.

## Releasing

`main` is protected by a pre-commit hook, so work on a branch.

Releases are published by CI: push a `v*` tag and the `publish` job uploads the helper
to PyPI through trusted publishing from the `release` environment, while the `release`
job creates the tag's GitHub release if there is none and attaches the built app to it,
which is what the installer downloads. The two jobs are independent, so check both.
