Commands
========

Every command is a subcommand of ``pvql``; ``pvql <command> --help`` lists its options.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - What it does
   * - ``pvql config``
     - List settings; get, set, or unset one (see :doc:`settings`)
   * - ``pvql doctor``
     - Check every part of the installation
   * - ``pvql preview FILE``
     - Build a preview and print its cached path
   * - ``pvql prebuild DIR``
     - Build previews for a folder ahead of time
   * - ``pvql preload``
     - Load PyVista and VTK ahead of the first preview
   * - ``pvql types``
     - List claimed extensions
   * - ``pvql cache --prune``
     - Delete previews of files that have changed or gone
   * - ``pvql cache --clear``
     - Delete every cached preview
   * - ``pvql service``
     - Manage the render service (see :ref:`render-service`)
   * - ``pvql finder``
     - Say whether the Finder must restart to show previews
   * - ``pvql install``
     - Install the app and PyVista for this release
   * - ``pvql uninstall``
     - Remove everything, asking first
