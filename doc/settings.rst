Settings
========

Settings are changed with ``pvql config``, which checks each value before storing it:

.. code-block:: bash

   pvql config                              # list every setting and its value
   pvql config set theme document
   pvql config get theme
   pvql config unset theme                  # back to the default

Every setting takes effect on the next preview, except ``extensions``, which needs the
install command run again. They are kept in
``~/Library/Application Support/PyVistaQuickLook/config.json``.

Theme
-----

Previews follow a PyVista theme: its colour for a mesh without scalars, its colormap for
one with, its shading and lighting, and its point size and line width. Name one of
PyVista's own:

.. code-block:: bash

   pvql config set theme document

Or save your own from PyVista and name the file:

.. code-block:: python

   import pyvista as pv

   theme = pv.themes.DocumentTheme()
   theme.color = 'tan'
   theme.cmap = 'viridis'
   theme.smooth_shading = True
   theme.lighting_params.specular = 0.4
   theme.save('mytheme.json')

.. code-block:: bash

   pvql config set theme mytheme.json

Editing the file redraws previews on the next look. Without a theme, previews look like
PyVista's own plots.

.. image:: _static/preview-document-theme.png
   :alt: A preview drawn in PyVista's document theme
   :align: center

Every setting
-------------

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Key
     - Default
     - Effect
   * - ``theme``
     - PyVista's own
     - A PyVista theme by name, such as ``document`` or ``paraview``, or the path of one
       saved with ``Theme.save``
   * - ``interactive``
     - ``true``
     - Show a turnable surface; ``false`` renders a still image instead
   * - ``force_interactive``
     - ``false``
     - Open a ``.pv`` file on the surface even when it carries a picture
   * - ``info_panel``
     - ``automatic``
     - Where the dataset's details go: ``side``, ``below``, ``compact``, ``hidden``, or
       ``automatic`` to choose by the panel's size
   * - ``max_file_size_mb``
     - ``2048``
     - Files above this size show their header and a Preview Anyway button instead of a
       preview; ``0`` for no limit
   * - ``max_scene_points``
     - ``2000000``
     - Thin surfaces above this many points; ``0`` never does
   * - ``max_conversion_memory_mb``
     - half the memory
     - Memory previews may use while built side by side; ``0`` for no limit
   * - ``timeout``
     - ``60``
     - Seconds before a conversion is abandoned
   * - ``window_size``
     - ``1024 1024``
     - Still image size in pixels
   * - ``background``
     - none
     - Background colour of still images
   * - ``extra_args``
     - none
     - Extra arguments appended to ``pyvista plot`` for still images
   * - ``extensions.add``, ``extensions.remove``
     - none
     - Extensions to claim or stop claiming, on the next install
   * - ``native_formats``
     - ``false``
     - Preview ``.ply``, ``.stl``, ``.obj``, USD and ``.abc`` here instead of in macOS's own
       3D viewer
   * - ``cache``
     - ``true``
     - Reuse previously built previews
   * - ``cache_check_minutes``
     - ``1440`` (a day)
     - How often the service deletes previews of changed or deleted files; ``0`` never does
   * - ``preload_on_start``
     - ``true``
     - Load PyVista and VTK when the render service starts
   * - ``log``
     - ``false``
     - Append activity to ``pvql.log`` beside the config file
   * - ``python``
     - set at install
     - Interpreter of the PyVista environment
   * - ``pvql``
     - discovered
     - Absolute path to the ``pvql`` helper

.. _claiming:

Claiming more or fewer extensions
---------------------------------

To claim fewer, or to add one of the unclaimed ones listed under :doc:`formats`, change
the claims and run the install command again, which rebuilds the app with them:

.. code-block:: bash

   pvql config set extensions.add .h5
   pvql config set extensions.remove .dat

.. _native-formats:

Native formats
--------------

macOS previews ``.ply``, ``.stl``, ``.obj``, USD and Alembic files itself, showing the
shape but nothing about the data, and Quick Look ranks its own viewer above any extension
claiming those types. To preview them here instead:

.. code-block:: bash

   pvql config set native_formats true

The setting stands macOS's viewer down for your account
(``pluginkit -e ignore -i com.apple.HydraQLPreviewExtension``), which also takes away its
preview of MaterialX files; the extension already claims the types, and Quick Look
honours the claim once the viewer is down. USD and Alembic files are read through macOS's
own Model I/O by a tool the app carries, one block per mesh named by its path in the
file; USDZ packages stay with AR Quick Look. ``pvql doctor`` reports the state. Setting it
back to ``false`` restores the viewer at once.

Without the setting, the app is still offered for them under Open With: select a ``.ply``
in the Finder, press ⌘I, choose PyVista Quick Look under Open With, and click Change All.
Double-clicking such a file then opens this preview, render and dataset panel alike, in a
window of its own.
