# PyVista Quick Look

Press the space bar on a mesh file in the Finder and turn it with the mouse, the same
way macOS already previews `.ply` and `.usdz` models.

Selecting `flow.vtu` and pressing space shows its surface in the Quick Look panel,
coloured by the active scalars and free to rotate and zoom, with a panel of what the
dataset holds beside it.

## Install

macOS 15 or newer. Nothing else needs to be installed first.

```bash
curl -LsSf https://github.com/pyvista/pyvista-quicklook/releases/latest/download/install.sh | sh
```

Then select a `.vtu`, `.vtp`, or `.vtk` file in the Finder and press space.

The installer puts the `pvql` command in `~/.local/bin`; if your shell does not find it,
add that folder to your `PATH`. The install takes a few minutes, most of it downloading
about 400 MB of Python packages into a private environment: nothing on the system is
used or changed. [What the installer does](#what-the-installer-does) lists every step
and where things go.

## Settings

Settings are changed with `pvql config`, which checks each value before storing it:

```bash
pvql config                              # list every setting and its value
pvql config set theme document
pvql config get theme
pvql config unset theme                  # back to the default
```

Every setting takes effect on the next preview, except `extensions`, which needs the
install command run again.

### Theme

Previews follow a PyVista theme: its colour for a mesh without scalars, its colormap for
one with, its shading and lighting, and its point size and line width. Name one of PyVista's own:

```bash
pvql config set theme document
```

Or save your own from PyVista and name the file:

```python
import pyvista as pv

theme = pv.themes.DocumentTheme()
theme.color = 'tan'
theme.cmap = 'viridis'
theme.smooth_shading = True
theme.lighting_params.specular = 0.4
theme.save('mytheme.json')
```

```bash
pvql config set theme mytheme.json
```

Editing the file redraws previews on the next look. Without a theme, previews look like
PyVista's own plots.

### Turning the surface

The mouse moves the camera as it does in PyVista: drag to turn the surface, drag with
shift held or with the middle button to pan, drag with the right button, with shift and
control held, roll the wheel or pinch to zoom, and drag with control held to spin it
about the line of sight.

### Turnable or still

By default every preview is a surface to turn and zoom. To get still images instead,
rendered by `pyvista plot` with the scalar bar and axes that the turnable view leaves out:

```bash
pvql config set interactive false
```

Still images take `window_size`, `background` and `extra_args` from the settings, and a
theme by name only; a saved theme file always draws the turnable view.

### The dataset panel

The panel listing the dataset's type, points, cells, arrays and bounds sits beside or
below the preview as the panel's size allows. Drag the line between them to resize it.
The button on that line folds the panel away and back: to the line alone beside the
preview, or to one line below it. The preview remembers both. `pvql config set info_panel below` fixes
it below, `side` beside, `compact` keeps one line, and `hidden` leaves it out.

### Every setting

| Key | Default | Effect |
| --- | --- | --- |
| `theme` | PyVista's own | A PyVista theme by name, such as `document` or `paraview`, or the path of one saved with `Theme.save` |
| `interactive` | `true` | Show a turnable surface; `false` renders a still image instead |
| `force_interactive` | `false` | Open a `.pv` file on the surface even when it carries a picture, without the tabs |
| `info_panel` | `automatic` | Where the dataset's details go: `side`, `below`, `compact`, `hidden`, or `automatic` to choose by the panel's size |
| `max_file_size_mb` | `2048` | Files above this size show their header and a Preview Anyway button instead of a preview; `0` for no limit |
| `max_scene_points` | `2000000` | Thin surfaces above this many points; `0` never does |
| `max_conversion_memory_mb` | half the memory | Memory previews may use while built side by side; `0` for no limit |
| `timeout` | `60` | Seconds before a conversion is abandoned |
| `window_size` | `1024 1024` | Still image size in pixels |
| `background` | none | Background colour of still images |
| `extra_args` | none | Extra arguments appended to `pyvista plot` for still images |
| `extensions.add`, `extensions.remove` | none | Extensions to claim or stop claiming, on the next install |
| `cache` | `true` | Reuse previously built previews |
| `cache_check_minutes` | `1440` (a day) | How often the service deletes previews of changed or deleted files; `0` never does |
| `preload_on_start` | `true` | Load PyVista and VTK when the render service starts |
| `log` | `false` | Append activity to `pvql.log` beside the config file |
| `python` | set at install | Interpreter of the PyVista environment |
| `pvql` | discovered | Absolute path to the `pvql` helper |

They are kept in `~/Library/Application Support/PyVistaQuickLook/config.json`.

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

## Pictures a file carries

[pyvista-zstd](https://github.com/pyvista/pyvista-zstd) can embed a picture of the dataset
in the `.pv` file it writes:

```python
import pyvista_zstd

pyvista_zstd.write(mesh, 'mesh.pv', thumbnail=True)
```

A file with a current picture shows it in the Finder as its icon, and opens on it
without reading the data. **Static** and **Interactive** tabs above the preview
switch between the picture and the turnable surface, which opens on the same view. Every
other file, and a `.pv` file whose picture is older than its data, goes straight to the
turnable surface, without tabs. `pvql config set force_interactive true` opens every file
that way, and a picture then only stands in while the surface loads. Opening a file with
the app always shows it in 3D.

`thumbnail=True` renders a 512 × 512 PNG with PyVista's default camera, in the global
theme, cropped to the dataset on a transparent background; PNG bytes of your own are
embedded as they are. The picture sits uncompressed near the start of the file, with a
checksum of the file's index, so the extension reads it with a few small reads and can
tell when the data was changed after the picture was taken. This needs a pyvista-zstd
release with thumbnail support.

## How it works

The app bundle contains a Quick Look extension that declares a uniform type identifier
for each claimed extension. When the Finder previews one of those files, the extension
hands it to a background render service, which reads it with PyVista, extracts the
surface, colours the vertices by the active scalars, and writes a PLY. The extension
shows that PLY in a SceneKit view, which is what makes the preview turnable. The scene
is lit by VTK's light kit, which turns with the camera, so its colours match PyVista's
own renders; faces are drawn flat unless the theme shades smoothly.

Point data is interpolated across each face. Cell data is drawn flat: the cells are
split apart first, so each keeps its own colour. Lines and point clouds are drawn as
PyVista draws them: unlit, lines at the theme's `line_width` and points as round points of
its `point_size`, both in pixels, so a saved theme sets them. Lines are never drawn
thinner than two pixels, since one pixel is a hairline on a Retina display. A volume that carries scalars
is cut into three slices through its centre; one without is shown as its outer surface.
Both sides of every face are drawn and lit, as PyVista draws them, so an open surface
or a slice shows from wherever the camera looks.

Previews are cached under `~/Library/Caches/PyVistaQuickLook`, keyed by the file's
path, size, and modification time, so editing a file invalidates its preview, and by the
version of the converter, so an update rebuilds them rather than serving old ones. The
render service deletes previews of files that have changed or gone when it starts and
then once a day (`cache_check_minutes`); `pvql cache --prune` does it now, dropping previews built by an older version too,
and `pvql cache --clear` empties the cache. Changing a setting that previews depend on,
such as the theme, leaves the previews built under the old one until they are pruned or cleared. A preview is kept whenever the service cannot tell: its file
is on a disk that is not plugged in, a share that is not mounted, or in a folder macOS
keeps private and no preview has read since the service started, so a check never
raises a permission prompt of its own.

The service loads PyVista and VTK in the background when it starts, which is at login
and whenever it is reinstalled, so the first preview does not wait for them. `pvql
preload` does the same on demand.

Surfaces are sent whole up to `max_scene_points`. Above that, images and volumes are
thinned on their lattice and other surfaces are decimated.

### Large files

A `.pv` file is read with only the array it is coloured by. Its other arrays are read
one at a time for their ranges, so a file never needs its arrays and its mesh in memory
at once. A VTK XML file above 256 MB is read the same way, its other ranges taken from
the header. A vector's range is that of its magnitude, shown as `||v||`, in every
format.

For a file of 32 MB or more, the dataset panel appears as soon as the header is read,
with a spinner where the render goes until the preview is ready.

Previews are built side by side while their estimated memory, about six times each
file's size, fits within half the Mac's memory (`max_conversion_memory_mb`); the rest
wait their turn, and a file too big for that budget is built on its own. Moving on from
a slow file does not cancel it, so it is ready when you come back, unless its conversion
runs past `timeout`.

A file above `max_file_size_mb` is not converted or copied when it is selected. The panel
shows what its header says, and a Preview Anyway button previews it once.

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
and coloured, the same as any other volume. An ordinary folder shows its own details
immediately, as the Finder would.

### The render service

macOS runs Quick Look extensions in a sandbox that VTK cannot run inside, so reading
and conversion happen in a launch agent instead, which the installer writes to
`~/Library/LaunchAgents` and starts.

```bash
pvql service            # report whether it is loaded
pvql service --install  # (re)install and start it
pvql service --uninstall
```

It appears as `pyvista-quicklook-daemon` under Login Items & Extensions in System
Settings, next to the app's own entry, where it can be switched off; its output goes to
`~/Library/Logs/pvqld.log`.

### Files in the Desktop, Documents, and Downloads folders

macOS keeps those folders private to each program, and the render service cannot read
them. The Quick Look extension copies the file it was asked to preview into its own
container so that the service can convert it anyway.

A dataset that points at neighbouring files — `.pvd`, `.vtm`, `.case`, `.foam`, `.xdmf`,
`.xmf` — needs those neighbours, which the copy does not include. Keep such datasets
outside those three folders, or grant the render service Full Disk Access in System
Settings under Privacy & Security. The service runs from the app nested at
`~/Applications/PyVista Quick Look.app/Contents/Library/PyVista Quick Look.app`; in the
file picker that opens from the + button, press Cmd-Shift-G and paste that path.

## Troubleshooting

`pvql doctor` checks every part of the installation and builds one real preview, and
says what to do about anything that fails.

- **The panel says the service is not answering.** Run `pvql service --install`, then
  `pvql doctor`; the service's own output is in `~/Library/Logs/pvqld.log`.
- **Login Items still lists `pyvista-quicklook` or PyVista Quick Look Extensions.**
  Earlier versions made those entries; reinstalling replaces them, and System Settings
  drops the old ones the next time it opens.
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

## Commands

```bash
pvql config           # list settings; get, set, or unset one
pvql doctor           # check every part of the installation
pvql preview FILE     # build a preview and print its cached path
pvql prebuild DIR     # build previews for a folder ahead of time
pvql preload          # load PyVista and VTK ahead of the first preview
pvql types            # list claimed extensions
pvql cache --prune    # delete previews of files that have changed or gone
pvql cache --clear    # delete every cached preview
pvql service          # manage the render service
pvql finder           # say whether the Finder must restart to show previews
pvql uninstall        # remove everything, asking first
```

## What the installer does

1. Downloads the newest release that has the prebuilt app attached, a 2 MB zip, along
   with that release's installer scripts, about 1 MB.
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
7. Installs the render service, a launch agent in `~/Library/LaunchAgents`. It appears
   as `pyvista-quicklook-daemon` under Login Items & Extensions in System Settings.

No system Python and no existing environment is used or changed.

| Path | What | Size |
| --- | --- | --- |
| `~/Library/Application Support/PyVistaQuickLook/venv` | The PyVista environment | 370 MB |
| `~/Library/Application Support/PyVistaQuickLook/config.json` | Configuration | 4 KB |
| `~/Applications/PyVista Quick Look.app` | The app, with its Quick Look extensions and the render service in a nested `PyVista Quick Look.app` that is never launched | 4 MB |
| `~/Library/Application Support/uv/tools/pyvista-quicklook` | The `pvql` helper | 400 KB |
| `~/.local/bin/pvql`, `~/.local/bin/pyvista-quicklook-daemon` | Links to the helper | |
| `~/Library/Logs/pvqld.log` | The service's output | grows slowly |
| `~/Library/Containers/org.pyvista.PyVistaQuickLook.QuickLook/Data/tmp` | The extension's log, its requests to the service, staged copies and a copy of the settings, in a sandbox folder that macOS creates and keeps | small |
| `~/Library/Caches/PyVistaQuickLook` | Built previews, one per file and settings | grows with use |
| `~/.local/bin/uv`, `~/Library/Application Support/uv` | uv, and Python 3.14 if uv had to fetch one | 45 MB, plus Python |
| `~/.cache/uv` | uv's download cache; the environment's files are clones of it, not copies | shared |

About 400 MB in total, or 445 MB when uv is installed too. The environment is the bulk
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

From a checkout, `./scripts/install-from-source.sh` does the same and builds the app
from source, which needs the Xcode command line tools (`xcode-select --install`):

```bash
./scripts/install-from-source.sh --prefix /Applications                # put the app there instead of ~/Applications
./scripts/install-from-source.sh --app /path/to/PyVistaQuickLook.app   # skip the build
```

## Uninstall

```bash
pvql uninstall
```

It lists what it is about to remove and asks first: the app, the render service, the
PyVista environment, the cache, and the `pvql` command itself. The configuration file
is kept unless you pass `--all`, and uv is left in place, with a note on how to remove
that too if the installer is what put it there. The app's own window has an Uninstall
button that does the same.

Developing or releasing PyVista Quick Look is described in [CONTRIBUTING.md](CONTRIBUTING.md).
