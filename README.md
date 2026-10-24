<p align="center">
  <img src="https://raw.githubusercontent.com/pyvista/pyvista-quicklook/main/doc/_static/logo.png" alt="PyVista Quick Look" width="160">
</p>

# PyVista Quick Look

Press the space bar on a mesh file in the Finder and turn it with the mouse, the same
way macOS already previews `.ply` and `.usdz` models. Over 80 formats PyVista reads are
claimed, each shown coloured by its active scalars with a panel of what the dataset holds
beside it.

![Pressing space on a mesh file in the Finder and turning the preview](https://raw.githubusercontent.com/pyvista/pyvista-quicklook/main/doc/_static/quicklook.gif)

## Install

macOS 15 or newer. Nothing else needs to be installed first.

```bash
curl -LsSf https://github.com/pyvista/pyvista-quicklook/releases/latest/download/install.sh | sh
```

Then select a `.vtu`, `.vtp`, or `.vtk` file in the Finder and press space.

A preview is for a quick look, not for measuring: a large surface may be thinned and a
volume cut into slices, while the dataset panel describes the file itself.

## Documentation

**[pyvista.github.io/pyvista-quicklook](https://pyvista.github.io/pyvista-quicklook/)**

- [Install](https://pyvista.github.io/pyvista-quicklook/install.html): from PyPI, what the installer does and where things go, uninstall
- [Using the preview](https://pyvista.github.io/pyvista-quicklook/usage.html): turning the surface, the dataset panel, fast previews with pyvista-zstd
- [Settings](https://pyvista.github.io/pyvista-quicklook/settings.html): themes, every setting, native formats
- [Supported files](https://pyvista.github.io/pyvista-quicklook/formats.html): every claimed extension
- [How it works](https://pyvista.github.io/pyvista-quicklook/how-it-works.html): the render service, cache, large files
- [Troubleshooting](https://pyvista.github.io/pyvista-quicklook/troubleshooting.html) and [Commands](https://pyvista.github.io/pyvista-quicklook/commands.html)

Developing or releasing PyVista Quick Look is described in [CONTRIBUTING.md](CONTRIBUTING.md).
