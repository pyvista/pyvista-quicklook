# PyVista Quick Look

Press the space bar on a mesh file in the Finder and turn it with the mouse, the same
way macOS already previews `.ply` and `.usdz` models.

Selecting `flow.vtu` and pressing space shows its surface in the Quick Look panel,
coloured by the active scalars and free to rotate and zoom.

## Install

macOS 12 or newer. Nothing else needs to be installed first.

```bash
curl -LsSf https://github.com/pyvista/pyvista-quicklook/releases/latest/download/install.sh | sh
```

Then select a `.vtu`, `.vtp`, or `.vtk` file in the Finder and press space. Check the
result with `pvql doctor`.

### What the installer does

1. Downloads the latest release: the installer scripts, about 1 MB, and the prebuilt
   app, about 200 KB.
2. Installs [uv](https://docs.astral.sh/uv/) if it is missing or updates it if it is too
   old, then a Python 3.14 if uv finds none on the machine.
3. Installs the `pvql` helper as a uv tool.
4. Creates a private Python environment and installs
   [PyVista](https://github.com/pyvista/pyvista) 0.49 with its io extras, among them
   [meshio](https://github.com/nschloe/meshio) and
   [pyvista-frd-reader](https://github.com/pyvista/pyvista-frd-reader), along with
   [cvista](https://github.com/pyvista/cvista) and
   [pyvista-cad](https://github.com/pyvista/pyvista-cad). This is the step that takes a
   while: about 400 MB of wheels.
5. Writes the configuration file and loads PyVista once, so the first preview is quick.
6. Copies the app from the download into `~/Applications` and registers it with Launch
   Services and Quick Look. The downloads are removed once everything is installed.
7. Installs the render service as a launch agent. It appears as PyVista Quick Look
   under Login Items in System Settings.

No system Python and no existing environment is used or changed.

### Where things go

| Path | What | Size |
| --- | --- | --- |
| `~/Library/Application Support/PyVistaQuickLook/venv` | The PyVista environment | 370 MB |
| `~/Library/Application Support/PyVistaQuickLook/config.json` | Configuration | 4 KB |
| `~/Applications/PyVista Quick Look.app` | The app and its Quick Look extension | 500 KB |
| `~/Library/Application Support/uv/tools/pyvista-quicklook` | The `pvql` helper | 400 KB |
| `~/.local/bin/pvql`, `~/.local/bin/pyvista-quicklook` | Links to the helper | |
| `~/Library/LaunchAgents/org.pyvista.pvqld.plist` | The render service | 4 KB |
| `~/Library/Logs/pvqld.log` | The service's output | grows slowly |
| `~/Library/Containers/org.pyvista.PyVistaQuickLook.QuickLook/Data/tmp` | The extension's log and staged copies, in a sandbox folder that macOS creates and keeps | small |
| `~/Library/Caches/PyVistaQuickLook` | One built preview per file previewed | grows with use |
| `~/.local/bin/uv`, `~/Library/Application Support/uv` | uv, and Python 3.14 if uv had to fetch one | 45 MB, plus Python |
| `~/.cache/uv` | uv's download cache; the environment's files are clones of it, not copies | shared |

About 395 MB in total, or 440 MB when uv is installed too. The environment is the bulk
of it:

- [cvista](https://github.com/pyvista/cvista)`[all]`, a VTK fork used in place of
  stock VTK, 137 MB
- cascadio, [pyvista-cad](https://github.com/pyvista/pyvista-cad)'s STEP reader, which
  reads IGES too, 69 MB
- matplotlib, 28 MB
- numpy, 25 MB
- ezdxf, pyvista-cad's DXF reader, 20 MB
- PyVista 0.49, [meshio](https://github.com/nschloe/meshio), and the remaining
  dependencies, about 110 MB

`pvql cache --clear` empties the preview cache. `pvql uninstall` removes everything
above except the configuration file and uv.

From a checkout, `./scripts/install-from-source.sh` does the same and builds the app from source,
which needs the Xcode command line tools (`xcode-select --install`):

```bash
./scripts/install-from-source.sh --prefix /Applications                # install for all users
./scripts/install-from-source.sh --app /path/to/PyVistaQuickLook.app   # skip the build
```

## Supported files

82 extensions are claimed, so pressing space on any of these files opens
this preview. A file that turns out not to be a mesh, such as a `.dat` holding a table
of numbers, is shown as plain text instead, the way Quick Look would have shown it.

| Format | Extensions | Format | Extensions |
| --- | --- | --- | --- |
| 3D Studio Model | `.3ds` | NRRD Volume | `.nhdr`, `.nrrd` |
| 3MF Model | `.3mf` | Object File Format Mesh | `.off` |
| AVS UCD Data | `.inp` | OpenFOAM Case | `.foam` |
| AVS UCD Mesh | `.avs` | ParaView Data Collection | `.pvd` |
| Binary Marching Cubes Surface | `.tri` | PERMAS Data | `.dato`, `.post` |
| BYU Geometry | `.g` | PLOT3D Metadata | `.p3d` |
| CalculiX Result | `.frd` | Point Cloud | `.pts` |
| CGNS Data | `.cgns` | ProStar Mesh | `.vrt` |
| Digital Elevation Model | `.dem` | Protein Data Bank | `.pdb` |
| DXF Drawing | `.dxf` | PyVista Zstandard Data | `.pv`, `.zvtk` |
| Eclipse GRDECL Grid | `.grdecl` | SEG-Y Seismic Data | `.segy`, `.sgy` |
| EnSight Case | `.case` | SLC Volume | `.slc` |
| Exodus II Data | `.e`, `.ex2`, `.exii`, `.exo` | STEP Model | `.step`, `.stp` |
| Facet Surface | `.facet` | Tecplot ASCII Data | `.tec` |
| FLAC3D Grid | `.f3grid` | Tecplot Data | `.dat` |
| Fluent Case | `.cas` | TetGen Mesh | `.ele`, `.node` |
| GAMBIT Neutral Mesh | `.neu` | VRML Model | `.vrml`, `.wrl` |
| Gaussian Cube | `.cube` | VTK File Series | `.series` |
| GE Signa MR Image | `.mr` | VTK HDF | `.vtkhdf` |
| Gmsh Mesh | `.msh` | VTK Image Data | `.vti` |
| HMF Mesh | `.hmf` | VTK Legacy Data | `.vtk` |
| IGES Model | `.iges`, `.igs` | VTK MultiBlock | `.vtm`, `.vtmb` |
| Kratos Model Part | `.mdpa` | VTK Parallel Image Data | `.pvti` |
| MED Mesh | `.med` | VTK Parallel Legacy Data | `.pvtk` |
| Medit Mesh | `.mesh`, `.meshb` | VTK Parallel Rectilinear Grid | `.pvtr` |
| MetaImage Volume | `.mha`, `.mhd` | VTK Parallel Unstructured Grid | `.pvtu` |
| MFIX Result | `.res` | VTK Partitioned Dataset | `.vtpd` |
| MINC Volume | `.mnc` | VTK PolyData | `.vtp` |
| MOAB Mesh | `.h5m` | VTK Rectilinear Grid | `.vtr` |
| Nastran Bulk Data | `.bdf`, `.fem`, `.nas` | VTK Structured Grid | `.vts` |
| Nek5000 Data | `.nek5000` | VTK Unstructured Grid | `.vtu` |
| Netgen Mesh | `.vol` | XDMF Data | `.xdmf` |
| NIfTI Volume | `.nii` | XDMF Mesh | `.xmf` |

4 more are claimed through a type macOS declares, since Launch Services sees only
the last suffix of a compressed dataset:

- Gzip Archive (`org.gnu.gnu-zip-archive`): `.dato.gz`, `.nii.gz`, `.post.gz`, `.vol.gz`

A folder of DICOM slices is claimed the same way, through `public.folder`.

3 more have a type and a preview of their own in macOS, which Quick Look always uses;
the app can open them instead (see [Native formats](#native-formats)):

- `.obj` (`public.geometry-definition-format`)
- `.ply` (`public.polygon-file-format`)
- `.stl` (`public.standard-tesselated-geometry-format`)

PyVista can also read these, which are not claimed:

- macOS previews it: `.bmp`, `.dcm`, `.gif`, `.glb`, `.gltf`, `.hdr`, `.jpeg`, `.jpg`, `.png`, `.pnm`, `.tif`, `.tiff`
- disk images own the extension: `.img`
- camera raw images own the extension: `.raw`
- XML owns the extension: `.xml`
- needs a full OpenCascade kernel: `.brep`, `.brp`, `.fcstd`
- needs ifcopenshell: `.ifc`
- needs the openscad program: `.scad`
- a general HDF5 container: `.h5`, `.hdf`
- meshio fails to read it: `.su2`, `.ugrid`
- meshio hangs on it: `.wkt`
- meshio writes it but does not read it: `.svg`

Claims on an extension that another kind of file owns, such as `.xml`, are ignored by
macOS, which is why those are not claimed. To claim fewer, or to add one of the others,
change the claims and run the install command again, which rebuilds the app with them:

```bash
pvql config set extensions.add .h5
pvql config set extensions.remove .dat
```

### Native formats

macOS previews `.ply`, `.stl` and `.obj` itself, showing the shape but nothing about the
data, and Quick Look uses that viewer for any 3D file before it consults extensions, so
pressing space on one never shows this preview. The app is offered for them under Open
With instead: select a `.ply` in the Finder, press ⌘I, choose PyVista Quick Look under
Open With, and click Change All. Double-clicking such a file then opens this preview,
render and dataset panel alike, in a window of its own.

The claimed formats open the same way. The app is their default opener unless another
app that reads them, such as ParaView, says it is, and Open With chooses between them.
The compressed formats claimed through gzip and DICOM folders are not opened by the app,
since it would otherwise offer to open every gzip archive and folder.

## How it works

The app bundle contains a Quick Look extension that declares a uniform type identifier
for each claimed extension. When the Finder previews one of those files, the extension
hands it to a background render service, which reads it with PyVista, extracts the
surface, colours the vertices by the active scalars, and writes a PLY. The extension
shows that PLY in a SceneKit view, which is what makes the preview turnable.
A mesh without scalars is drawn in PyVista's theme colour, and the scene is lit by VTK's
light kit, which turns with the camera, so its colours match PyVista's own renders.

Point data is interpolated across each face. Cell data is drawn flat: the cells are
split apart first, so each keeps its own colour. Lines are drawn as tubes and point
clouds as spheres, sized to the spacing between points. A volume that carries scalars
is cut into three slices through its centre; one without is shown as its outer surface.
Open surfaces, the slices among them, are written with a second, flipped side, so they
are drawn and lit from wherever the camera looks.

Previews are cached under `~/Library/Caches/PyVistaQuickLook`, keyed by the file's
path, size, and modification time, so editing a file invalidates its preview, and by the
version of the converter, so an update rebuilds them rather than serving old ones.

The service loads PyVista and VTK in the background when it starts, which is at login
and whenever it is reinstalled, so the first preview does not wait for them. `pvql
warmup` does the same on demand, and `pvql config set warm_on_start false` turns the
automatic pass off.

Surfaces are sent whole up to `max_scene_points`. Above that, images and volumes are
thinned on their lattice and other surfaces are decimated. Set it to `0` to never
thin.

### Large files

A `.pv` file is read with only the array it is coloured by. Its other arrays are read
one at a time for their ranges, so a file never needs its arrays and its mesh in memory
at once. A VTK XML file above 256 MB is read the same way, its other ranges taken from
the header. A vector's range is that of its magnitude, shown as `||v||`, in every
format.

For a file of 32 MB or more, the info panel appears as soon as the header is read, with
a spinner where the render goes until the preview is ready.

Previews are built side by side while their estimated memory, about six times each
file's size, fits within half the Mac's memory; the rest wait their turn, and a file too
big for that budget is built on its own. Moving on from a slow file does not cancel it,
so it is ready when you come back, unless its conversion runs past `timeout`. To change
the budget:

```bash
pvql config set max_conversion_memory_mb 16000
```

A file above `max_file_size_mb` is not converted or copied when it is selected. The panel
shows what its header says, and a Preview Anyway button previews it once. To preview
files that large without asking:

```bash
pvql config set max_file_size_mb 4096
```

A claimed file that turns out not to be a mesh is shown the way Quick Look would have
shown it: as plain text when it reads as text, such as a `.dat` holding a table of
numbers, and otherwise as its icon, name, size, and date, with a line saying why it
holds no mesh. Any other failure, such as a render service that is not running or a
conversion that timed out, is reported in full instead.

### Compressed and multi-file datasets

Two kinds of dataset are not a single file with a claimable extension, and both are
reached by claiming the type macOS gives them instead.

A compressed dataset is named `.nii.gz` or `.vol.gz`, but Launch Services sees only
`.gz` and keeps its own type for it, so declaring those extensions does nothing. The
extension claims the gzip type: press space on a gzipped NIfTI, PERMAS, Netgen, or
Medit file and it is drawn like any other. Every other gzip, a tarball or a compressed
log, shows its details.

A DICOM series is a folder of slices, so the folder is what gets previewed. Press space
on one in the Finder and the slices are read as a single volume, cut through its centre
and coloured, the same as any other volume. Folders are claimed for this, so the
extension checks whether a folder holds DICOM files before it asks the render service:
it looks for the `DICM` marker in the first few entries, whatever they are named, and an
ordinary folder shows its own details immediately, as the Finder would.

### Pictures first

Pressing space shows a picture of the dataset, its details, and an **Interactive View**
button that loads the surface to turn and zoom. A `.pv` or `.3mf` file that carries a
picture of itself shows that one at once, without reading the data; any other file is
rendered by `pyvista plot --off-screen --screenshot`, which keeps the scalar bar and axes
the interactive view leaves out. After `pvql config set interactive true`, every preview
starts as the interactive view instead. Opening a file with the app always shows it in 3D.

### The render service

macOS runs Quick Look extensions in a sandbox that VTK cannot run inside, so reading
and conversion happen in a launch agent instead. The installer sets it up.

```bash
pvql service            # report whether it is loaded
pvql service --install  # (re)install and start it
pvql service --uninstall
```

It appears as PyVista Quick Look under Login Items in System Settings, and its output
goes to `~/Library/Logs/pvqld.log`. macOS names it `pyvista-quicklook` when it asks
whether to let it read a folder, since that is the program the service runs as.

### Files in the Desktop, Documents, and Downloads folders

macOS keeps those folders private to each program, and the render service cannot read
them. The Quick Look extension copies the file it was asked to preview into its own
container so that the service can convert it anyway.

A dataset that points at neighbouring files — `.pvd`, `.vtm`, `.case`, `.foam`, `.xdmf`,
`.xmf` — needs those neighbours, which the copy does not include. Keep such datasets
outside those three folders, or grant the render service Full Disk Access in System
Settings under Privacy & Security.

## Configuration

Settings are changed with `pvql config`, which checks each value before storing it:

```bash
pvql config                              # list every setting and its value
pvql config set max_file_size_mb 4096
pvql config get max_file_size_mb
pvql config unset max_file_size_mb       # back to the default
```

They are kept in `~/Library/Application Support/PyVistaQuickLook/config.json`.

| Key | Default | Effect |
| --- | --- | --- |
| `python` | set at install | Interpreter of the PyVista environment |
| `pvql` | discovered | Absolute path to the `pvql` helper |
| `interactive` | `false` | Start previews as the turnable surface rather than a picture |
| `info_panel` | `automatic` | Where the dataset's details go: `side`, `below`, `compact`, `hidden`, or `automatic` to choose by the panel's size |
| `max_scene_points` | `2000000` | Thin surfaces above this many points; `0` never does |
| `max_glyph_points` | `20000` | Draw at most this many points of a point cloud; `0` draws all |
| `max_file_size_mb` | `2048` | Files above this size show their header and a Preview Anyway button instead of a preview; `0` for no limit |
| `max_conversion_memory_mb` | half the memory | Memory previews may use while built side by side; `0` for no limit |
| `timeout` | `60` | Seconds before a conversion is abandoned |
| `warm_on_start` | `true` | Load PyVista and VTK when the render service starts |
| `window_size` | `1024 1024` | Still image size in pixels |
| `background` | none | Background colour passed to `pyvista plot` |
| `extra_args` | none | Extra arguments appended to `pyvista plot` |
| `extensions.add`, `extensions.remove` | none | Extensions to claim or stop claiming, on the next install |
| `cache` | `true` | Reuse previously built previews |
| `log` | `false` | Append activity to `pvql.log` beside the config file |

Changing `extensions` requires a reinstall, because the claimed types are baked into the
app bundle. Every other key takes effect on the next preview.

## Commands

```bash
pvql preview FILE     # build a preview and print its cached path
pvql warm DIR         # build previews for a directory ahead of time
pvql warmup           # load PyVista and VTK ahead of the first preview
pvql types            # list claimed extensions
pvql doctor           # check every part of the integration
pvql finder           # say whether the Finder must restart to show previews
pvql service          # manage the render service
pvql config           # list settings; get, set, or unset one
pvql config --init    # write a config file with discovered defaults
pvql cache --clear    # delete cached previews
pvql uninstall        # remove everything, asking first
```

## Troubleshooting

Run `pvql doctor` first; it checks the helper, the app, the extension registration, the
Finder, the service, and a real preview.

- **The panel says the service is not answering.** Run `pvql service --install`.
- **Nothing happens on space bar.** Confirm the type is claimed with `pvql types`, then
  check that Finder resolves it: `mdls -name kMDItemContentType yourfile.vtu` should
  report an `org.pyvista.pyvista-quicklook.*` type.
- **The panel says "Extension org.pyvista.PyVistaQuickLook.QuickLook not found".** The
  Finder has run since before 0.7.0 and still asks for the extension by its old
  identifier. Run `killall Finder` once no files are being copied or moved.
- **Previews are stale.** `pvql cache --clear`.
- **A preview fails.** Run `pvql config set log true`; activity is then appended to
  `pvql.log` beside the config file. The extension's own log is in
  `~/Library/Containers/org.pyvista.PyVistaQuickLook.QuickLook/Data/tmp/`.

## Development

```bash
uv sync --group dev
uv run pytest tests/          # helper and exporter tests, with coverage
uv run pre-commit run --all-files
./scripts/build.sh            # compile and sign the app bundle
```

Everything runs on one Python, 3.14: the tests, the `pvql` helper in its uv tool
environment, and the PyVista environment beside it. `[tool.uv]` in `pyproject.toml`
excludes stock VTK from the resolve, so the test environment holds the same packages the
installer provisions.

PyVista and cvista are pinned to one release each, in `pyproject.toml` and
`scripts/install-from-source.sh` alike. Bumping either reruns a test that compares every extension
the environment can read with the format table, so a reader that PyVista adds, drops, or
moves to a package of its own shows up as a failure to resolve in `formats.py`. The README's format tables are generated from
PyVista's reader tables and checked by another test:

```bash
uv run python -m pyvista_quicklook._formats_table README.md
```

One project, three names, each where its own conventions apply: `pyvista-quicklook` is
the package on PyPI and the program the render service runs as, `pvql` is the command,
and `PyVista Quick Look` is what the reader sees, from the app bundle to the panel. The
build's own files keep the compact `PyVistaQuickLook` spelling: the bundle identifier,
the executables, and the downloadable zip.

`main` is protected by a pre-commit hook, so work on a branch.

Releases are published by CI: push a `v*` tag and the `publish` job uploads the helper
to PyPI through trusted publishing from the `release` environment, while the `release`
job attaches the built app to the tag's GitHub release, which is what the installer
downloads.

## Uninstall

```bash
pvql uninstall
```

It lists what it is about to remove and asks first: the app, the render service, the
PyVista environment, the cache, and the `pvql` command itself. The configuration file
is kept unless you pass `--all`, and uv is left in place, with a note on how to remove
that too if the installer is what put it there. The app's own window has an Uninstall
button that does the same.
