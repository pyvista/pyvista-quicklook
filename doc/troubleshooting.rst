Troubleshooting
===============

``pvql doctor`` checks every part of the installation and builds one real preview, and
says what to do about anything that fails.

The panel says the service is not answering
   Run ``pvql service --install``, then ``pvql doctor``; the service's own output is in
   ``~/Library/Logs/pvqld.log``. See :ref:`render-service`.

Login Items still lists ``pyvista-quicklook`` or PyVista Quick Look Extensions
   Earlier versions made those entries; reinstalling replaces them, and System Settings
   drops the old ones the next time it opens.

Nothing happens on space bar
   Confirm the type is claimed with ``pvql types``, then check that Finder resolves it:
   ``mdls -name kMDItemContentType yourfile.vtu`` should report an
   ``org.pyvista.pyvista-quicklook.*`` type.

The panel says "Extension org.pyvista.PyVistaQuickLook.QuickLook not found"
   The Finder has run since before 0.7.0 and still asks for the extension by its old
   identifier. Run ``killall Finder`` once no files are being copied or moved.

A dataset made of several files fails in Desktop, Documents or Downloads
   Those folders are private to each program; see :ref:`protected-folders`.

Files show a blank icon in the Finder
   The Finder draws a file's icon from the app that opens it. ParaView 6 says it opens
   ``.vtk``, ``.vtp``, ``.vtu``, ``.vti`` and the other VTK formats, but its bundle lacks the
   icon it names for them. Select such a file, press ⌘I, choose PyVista Quick Look under
   Open With, and click Change All.

Previews are stale
   ``pvql cache --clear``.

A preview fails
   Run ``pvql config set log true``; activity is then appended to ``pvql.log`` beside the
   config file. The extension's own log is in
   ``~/Library/Containers/org.pyvista.PyVistaQuickLook.QuickLook/Data/tmp/``.
