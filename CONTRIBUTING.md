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

PyVista, cvista and pyvista-zstd are pinned to one release each, in `pyproject.toml` and
`scripts/install-from-source.sh` alike. Bumping PyVista or cvista reruns a test that compares every
extension the environment can read with the format table, so a reader that PyVista adds,
drops, or moves to a package of its own shows up as a failure to resolve in `formats.py`.
The README's format tables are generated from PyVista's reader tables and checked by
another test:

```bash
uv run python -m pyvista_quicklook._formats_table README.md
```

## Names

One project, three names, each where its own conventions apply: `pyvista-quicklook` is
the package on PyPI and, as `pyvista-quicklook-daemon`, the program the render service
runs as, `pvql` is the command,
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
daemon` as `pyvista-quicklook-daemon`, which is how Login Items lists it, apart from
the app. It is not registered from inside the app with `SMAppService`, which would make
one Login Items entry: without a Developer ID, launchd pins such an agent to the build
that registered it, and the next build's service does not start.

## Releasing

`main` is protected by a pre-commit hook, so work on a branch.

A release is a tag: the version behind a `v`, pushed from an up-to-date `main`.

```bash
git tag v0.9.1 main
git push origin v0.9.1
```

CI does the rest. The `release` job builds the app and creates the GitHub release with
`PyVistaQuickLook.zip` and `install.sh` attached and generated notes; the `publish` job
uploads the helper to PyPI. The two are independent, so check both. The version comes
from the tag through setuptools-scm, and PyPI never reuses one.

A release can also be made from the GitHub web interface, since publishing it creates
the tag. Save it as a draft, though: the one-line installer downloads from the latest
release, and a release published outright has no assets until CI attaches them. CI
publishes the draft once it has.
