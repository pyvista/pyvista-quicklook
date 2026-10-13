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

The render service is a launch agent in `~/Library/LaunchAgents` that runs `pvql
daemon`, and Login Items lists it as `pvql`, apart from the app. Version 0.9.0 instead
registered it from inside the nested app with `SMAppService`, which made one Login
Items entry but had launchd pin the service to that build's binary, so the next build
would not start. The nested app still carries that agent's property list, and its
binary unregisters it, so that upgrades from 0.9.0 retire it.

## Releasing

`main` is protected by a pre-commit hook, so work on a branch.

Releases are published by CI: push a `v*` tag and the `publish` job uploads the helper
to PyPI through trusted publishing from the `release` environment, while the `release`
job creates the tag's GitHub release if there is none and attaches the built app to it,
which is what the installer downloads. The two jobs are independent, so check both.
