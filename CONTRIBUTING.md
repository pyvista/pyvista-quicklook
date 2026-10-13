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

A release is a tag. With `main` merged and up to date, tag it with the version behind a
`v` and push the tag:

```bash
git tag v0.9.1 main
git push origin v0.9.1
```

That is all. CI runs on the tag: the `release` job builds the app and creates the
GitHub release with `PyVistaQuickLook.zip` and `install.sh` already attached and the
notes generated from the merged pull requests, and the `publish` job uploads the helper
to PyPI through trusted publishing from the `release` environment. The two jobs are
independent, so check that both went green, then run the install one-liner from the
README once. The version comes from the tag alone, through setuptools-scm, so nothing
in the repository is edited for a release. PyPI never reuses a version, so a release
that was yanked or deleted needs the next number.

To write the notes by hand, edit them on the release once CI has created it. Do not
publish a release by hand before its assets are on it: the one-line installer downloads
`install.sh` from whichever release is the latest. A draft saved by hand is fine, as CI
attaches the assets and then publishes it. Should a release be published without its
app all the same, the installer passes it over, and any pre-release, for the newest
release that has one.
