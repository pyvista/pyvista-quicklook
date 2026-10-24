Supported files
===============

.. formats-start

82 extensions are claimed, so pressing space on any of these files opens
this preview. A file that turns out not to be a mesh, such as a ``.dat`` holding a
table of numbers, is shown as plain text instead, the way Quick Look would have shown
it.

.. list-table::
   :header-rows: 1

   * - Format
     - Extensions
   * - 3D Studio Model
     - ``.3ds``
   * - 3MF Model
     - ``.3mf``
   * - AVS UCD Data
     - ``.inp``
   * - AVS UCD Mesh
     - ``.avs``
   * - Binary Marching Cubes Surface
     - ``.tri``
   * - BYU Geometry
     - ``.g``
   * - CalculiX Result
     - ``.frd``
   * - CGNS Data
     - ``.cgns``
   * - Digital Elevation Model
     - ``.dem``
   * - DXF Drawing
     - ``.dxf``
   * - Eclipse GRDECL Grid
     - ``.grdecl``
   * - EnSight Case
     - ``.case``
   * - Exodus II Data
     - ``.e``, ``.ex2``, ``.exii``, ``.exo``
   * - Facet Surface
     - ``.facet``
   * - FLAC3D Grid
     - ``.f3grid``
   * - Fluent Case
     - ``.cas``
   * - GAMBIT Neutral Mesh
     - ``.neu``
   * - Gaussian Cube
     - ``.cube``
   * - GE Signa MR Image
     - ``.mr``
   * - Gmsh Mesh
     - ``.msh``
   * - HMF Mesh
     - ``.hmf``
   * - IGES Model
     - ``.iges``, ``.igs``
   * - Kratos Model Part
     - ``.mdpa``
   * - MED Mesh
     - ``.med``
   * - Medit Mesh
     - ``.mesh``, ``.meshb``
   * - MetaImage Volume
     - ``.mha``, ``.mhd``
   * - MFIX Result
     - ``.res``
   * - MINC Volume
     - ``.mnc``
   * - MOAB Mesh
     - ``.h5m``
   * - Nastran Bulk Data
     - ``.bdf``, ``.fem``, ``.nas``
   * - Nek5000 Data
     - ``.nek5000``
   * - Netgen Mesh
     - ``.vol``
   * - NIfTI Volume
     - ``.nii``
   * - NRRD Volume
     - ``.nhdr``, ``.nrrd``
   * - Object File Format Mesh
     - ``.off``
   * - OpenFOAM Case
     - ``.foam``
   * - ParaView Data Collection
     - ``.pvd``
   * - PERMAS Data
     - ``.dato``, ``.post``
   * - PLOT3D Metadata
     - ``.p3d``
   * - Point Cloud
     - ``.pts``
   * - ProStar Mesh
     - ``.vrt``
   * - Protein Data Bank
     - ``.pdb``
   * - PyVista Zstandard Data
     - ``.pv``, ``.zvtk``
   * - SEG-Y Seismic Data
     - ``.segy``, ``.sgy``
   * - SLC Volume
     - ``.slc``
   * - STEP Model
     - ``.step``, ``.stp``
   * - Tecplot ASCII Data
     - ``.tec``
   * - Tecplot Data
     - ``.dat``
   * - TetGen Mesh
     - ``.ele``, ``.node``
   * - VRML Model
     - ``.vrml``, ``.wrl``
   * - VTK File Series
     - ``.series``
   * - VTK HDF
     - ``.vtkhdf``
   * - VTK Image Data
     - ``.vti``
   * - VTK Legacy Data
     - ``.vtk``
   * - VTK MultiBlock
     - ``.vtm``, ``.vtmb``
   * - VTK Parallel Image Data
     - ``.pvti``
   * - VTK Parallel Legacy Data
     - ``.pvtk``
   * - VTK Parallel Rectilinear Grid
     - ``.pvtr``
   * - VTK Parallel Unstructured Grid
     - ``.pvtu``
   * - VTK Partitioned Dataset
     - ``.vtpd``
   * - VTK PolyData
     - ``.vtp``
   * - VTK Rectilinear Grid
     - ``.vtr``
   * - VTK Structured Grid
     - ``.vts``
   * - VTK Unstructured Grid
     - ``.vtu``
   * - XDMF Data
     - ``.xdmf``
   * - XDMF Mesh
     - ``.xmf``

4 more are claimed through a type macOS declares, since Launch
Services sees only the last suffix of a compressed dataset:

- Gzip Archive (``org.gnu.gnu-zip-archive``): ``.dato.gz``, ``.nii.gz``, ``.post.gz``, ``.vol.gz``

A folder of DICOM slices is claimed the same way, through ``public.folder``.

7 more have a type and a preview of their own in macOS, which Quick
Look prefers; the ``native_formats`` setting previews them here (see
:ref:`native-formats`):

- ``.abc`` (``public.alembic``)
- ``.obj`` (``public.geometry-definition-format``)
- ``.ply`` (``public.polygon-file-format``)
- ``.stl`` (``public.standard-tesselated-geometry-format``)
- ``.usd`` (``com.pixar.universal-scene-description``)
- ``.usda`` (``com.pixar.universal-scene-description-utf8``)
- ``.usdc`` (``com.pixar.universal-scene-description-binary``)

PyVista can also read these, which are not claimed:

- macOS previews it: ``.bmp``, ``.dcm``, ``.gif``, ``.glb``, ``.gltf``, ``.hdr``, ``.jpeg``, ``.jpg``, ``.png``, ``.pnm``, ``.tif``, ``.tiff``
- disk images own the extension: ``.img``
- camera raw images own the extension: ``.raw``
- XML owns the extension: ``.xml``
- needs a full OpenCascade kernel: ``.brep``, ``.brp``, ``.fcstd``
- needs ifcopenshell: ``.ifc``
- needs the openscad program: ``.scad``
- a general HDF5 container: ``.h5``, ``.hdf``
- meshio fails to read it: ``.su2``, ``.ugrid``
- meshio hangs on it: ``.wkt``
- meshio writes it but does not read it: ``.svg``

.. formats-end

Claims on an extension that another kind of file owns, such as ``.xml``, are ignored by
macOS, which is why those are not claimed. :ref:`claiming` adds or removes claims.
