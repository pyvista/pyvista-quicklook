Install
=======

macOS 15 or newer. Nothing else needs to be installed first.

.. code-block:: bash

   curl -LsSf https://github.com/pyvista/pyvista-quicklook/releases/latest/download/install.sh | sh

Then select a ``.vtu``, ``.vtp``, or ``.vtk`` file in the Finder and press space.

The installer puts the ``pvql`` command in ``~/.local/bin``; if your shell does not find
it, add that folder to your ``PATH``. The install takes a few minutes, most of it
downloading about 400 MB of Python packages into a private environment: nothing on the
system is used or changed. :ref:`what-the-installer-does` lists every step and where
things go.

From PyPI
---------

The same install takes two commands; ``pvql install`` runs the installer of the release
matching the installed version:

.. code-block:: bash

   pip install pyvista-quicklook
   pvql install

The ``pvql`` that pip installs only starts the installer, which sets up its own copy in
``~/.local/bin`` and its own PyVista environment, so the environment pip installed into
can later be changed or removed. ``pip uninstall pyvista-quicklook`` afterwards leaves the
installed ``pvql`` as the one your shell finds.

.. _what-the-installer-does:

What the installer does
-----------------------

#. Downloads the newest release that has the prebuilt app attached, a 2 MB zip, along
   with that release's installer scripts, about 1 MB.
#. Installs `uv <https://docs.astral.sh/uv/>`_ if it is missing or updates it if it is
   too old, then a Python 3.14 if uv finds none on the machine.
#. Installs the ``pvql`` helper as a uv tool.
#. Creates a private Python environment and installs
   `PyVista <https://github.com/pyvista/pyvista>`_ 0.49 with its io extras, among them
   `meshio <https://github.com/nschloe/meshio>`_ and
   `pyvista-frd-reader <https://github.com/pyvista/pyvista-frd-reader>`_, along with
   `cvista <https://github.com/pyvista/cvista>`_ and
   `pyvista-cad <https://github.com/pyvista/pyvista-cad>`_. This is the step that takes
   a while: about 400 MB of wheels.
#. Writes the configuration file and loads PyVista once, so the first preview is quick.
#. Copies the app from the download into ``~/Applications`` and registers it with Launch
   Services and Quick Look. The downloads are removed once everything is installed.
#. Installs the render service, a launch agent in ``~/Library/LaunchAgents``. It appears
   as ``pyvista-quicklook-daemon`` under Login Items & Extensions in System Settings.

No system Python and no existing environment is used or changed.

Where things go
---------------

.. list-table::
   :header-rows: 1
   :widths: 45 45 10

   * - Path
     - What
     - Size
   * - ``~/Library/Application Support/PyVistaQuickLook/venv``
     - The PyVista environment
     - 370 MB
   * - ``~/Library/Application Support/PyVistaQuickLook/config.json``
     - Configuration
     - 4 KB
   * - ``~/Applications/PyVista Quick Look.app``
     - The app, with its Quick Look extensions and the render service in a nested
       ``PyVista Quick Look.app`` that is never launched
     - 4 MB
   * - ``~/Library/Application Support/uv/tools/pyvista-quicklook``
     - The ``pvql`` helper
     - 400 KB
   * - ``~/.local/bin/pvql``, ``~/.local/bin/pyvista-quicklook-daemon``
     - Links to the helper
     -
   * - ``~/Library/Logs/pvqld.log``
     - The service's output
     - grows slowly
   * - ``~/Library/Containers/org.pyvista.PyVistaQuickLook.QuickLook/Data/tmp``
     - The extension's log, its requests to the service, staged copies and a copy of the
       settings, in a sandbox folder that macOS creates and keeps
     - small
   * - ``~/Library/Caches/PyVistaQuickLook``
     - Built previews, one per file and settings
     - grows with use
   * - ``~/.local/bin/uv``, ``~/Library/Application Support/uv``
     - uv, and Python 3.14 if uv had to fetch one
     - 45 MB, plus Python
   * - ``~/.cache/uv``
     - uv's download cache; the environment's files are clones of it, not copies
     - shared

About 400 MB in total, or 445 MB when uv is installed too. The environment is the bulk
of it:

- `cvista <https://github.com/pyvista/cvista>`_ ``[all]``, a VTK fork used in place of
  stock VTK, 137 MB
- cascadio, `pyvista-cad <https://github.com/pyvista/pyvista-cad>`_'s STEP reader, which
  reads IGES too, 69 MB
- matplotlib, 28 MB
- numpy, 25 MB
- ezdxf, pyvista-cad's DXF reader, 20 MB
- PyVista 0.49, `meshio <https://github.com/nschloe/meshio>`_, and the remaining
  dependencies, about 110 MB

From source
-----------

From a checkout, ``./scripts/install-from-source.sh`` does the same and builds the app
from source, which needs the Xcode command line tools (``xcode-select --install``):

.. code-block:: bash

   ./scripts/install-from-source.sh --prefix /Applications                # put the app there instead of ~/Applications
   ./scripts/install-from-source.sh --app /path/to/PyVistaQuickLook.app   # skip the build

Uninstall
---------

.. code-block:: bash

   pvql uninstall

It lists what it is about to remove and asks first: the app, the render service, the
PyVista environment, the cache, and the ``pvql`` command itself. The configuration file
is kept unless you pass ``--all``, and uv is left in place, with a note on how to remove
that too if the installer is what put it there. The app's own window has an Uninstall
button that does the same.
