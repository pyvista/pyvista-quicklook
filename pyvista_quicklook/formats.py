"""File extensions claimed by the Quick Look extension."""

from __future__ import annotations

UTI_PREFIX = 'org.pyvista.pyvista-quicklook'

# Types macOS declares itself, which the extension supports rather than exports. A
# compressed dataset is named .nii.gz or .vol.gz, but Launch Services sees only .gz and
# keeps its own type for it, so the type is what has to be claimed. Nothing else
# previews a gzip, so this claim wins; files that are not meshes show their details.
SYSTEM_TYPES: dict[str, str] = {
    'org.gnu.gnu-zip-archive': 'Gzip Archive',
    # A DICOM series is a folder of slices, so the folder is what gets previewed. The
    # extension answers for one only when it holds DICOM files, and shows the folder's
    # own details otherwise, so previewing an ordinary folder stays immediate.
    'public.folder': 'Folder',
}

# Every extension the environment reads that is worth claiming, with the format's name.
# Files a claim catches that turn out not to be meshes are shown as text by the extension.
FORMATS: dict[str, str] = {
    # VTK serial formats
    '.vtk': 'VTK Legacy Data',
    '.vti': 'VTK Image Data',
    '.vtr': 'VTK Rectilinear Grid',
    '.vts': 'VTK Structured Grid',
    '.vtp': 'VTK PolyData',
    '.vtu': 'VTK Unstructured Grid',
    '.vtm': 'VTK MultiBlock',
    '.vtmb': 'VTK MultiBlock',
    '.vtpd': 'VTK Partitioned Dataset',
    '.vtkhdf': 'VTK HDF',
    # Zstandard-compressed VTK, written by pyvista-zstd.
    '.pv': 'PyVista Zstandard Data',
    '.zvtk': 'PyVista Zstandard Data',
    # VTK parallel formats
    '.pvtk': 'VTK Parallel Legacy Data',
    '.pvti': 'VTK Parallel Image Data',
    '.pvtr': 'VTK Parallel Rectilinear Grid',
    '.pvtu': 'VTK Parallel Unstructured Grid',
    '.pvd': 'ParaView Data Collection',
    '.xdmf': 'XDMF Data',
    # Simulation and CFD formats
    '.case': 'EnSight Case',
    '.exo': 'Exodus II Data',
    '.ex2': 'Exodus II Data',
    '.exii': 'Exodus II Data',
    '.foam': 'OpenFOAM Case',
    '.cgns': 'CGNS Data',
    '.nek5000': 'Nek5000 Data',
    '.p3d': 'PLOT3D Metadata',
    '.cas': 'Fluent Case',
    '.frd': 'CalculiX Result',
    '.neu': 'GAMBIT Neutral Mesh',
    '.grdecl': 'Eclipse GRDECL Grid',
    # Geometry, volume and misc formats
    '.facet': 'Facet Surface',
    '.slc': 'SLC Volume',
    '.dem': 'Digital Elevation Model',
    '.cube': 'Gaussian Cube',
    '.pdb': 'Protein Data Bank',
    '.segy': 'SEG-Y Seismic Data',
    '.sgy': 'SEG-Y Seismic Data',
    '.tri': 'Binary Marching Cubes Surface',
    '.3ds': '3D Studio Model',
    '.wrl': 'VRML Model',
    '.vrml': 'VRML Model',
    # CAD formats, read by pyvista-cad.
    '.step': 'STEP Model',
    '.stp': 'STEP Model',
    '.iges': 'IGES Model',
    '.igs': 'IGES Model',
    '.dxf': 'DXF Drawing',
    '.3mf': '3MF Model',
    # Mesh formats read by meshio.
    '.avs': 'AVS UCD Mesh',
    '.bdf': 'Nastran Bulk Data',
    '.f3grid': 'FLAC3D Grid',
    '.fem': 'Nastran Bulk Data',
    '.mdpa': 'Kratos Model Part',
    '.mesh': 'Medit Mesh',
    '.meshb': 'Medit Mesh',
    '.nas': 'Nastran Bulk Data',
    '.off': 'Object File Format Mesh',
    '.tec': 'Tecplot ASCII Data',
    '.vol': 'Netgen Mesh',
    '.msh': 'Gmsh Mesh',
    '.dato': 'PERMAS Data',
    '.post': 'PERMAS Data',
    '.node': 'TetGen Mesh',
    '.ele': 'TetGen Mesh',
    '.h5m': 'MOAB Mesh',
    '.hmf': 'HMF Mesh',
    '.med': 'MED Mesh',
    '.xmf': 'XDMF Mesh',
    # Medical volumes
    '.nii': 'NIfTI Volume',
    '.mha': 'MetaImage Volume',
    '.mhd': 'MetaImage Volume',
    '.nrrd': 'NRRD Volume',
    '.nhdr': 'NRRD Volume',
    '.mnc': 'MINC Volume',
    '.mr': 'GE Signa MR Image',
    '.dat': 'Tecplot Data',
    '.vrt': 'ProStar Mesh',
    '.inp': 'AVS UCD Data',
    '.res': 'MFIX Result',
    '.pts': 'Point Cloud',
    '.series': 'VTK File Series',
    '.g': 'BYU Geometry',
    '.e': 'Exodus II Data',
}


# Extensions claimed through a type in SYSTEM_TYPES rather than by their own, since
# Launch Services sees only the last suffix of a compressed dataset.
VIA_SYSTEM_TYPE: dict[str, str] = {
    '.dato.gz': 'org.gnu.gnu-zip-archive',
    '.nii.gz': 'org.gnu.gnu-zip-archive',
    '.post.gz': 'org.gnu.gnu-zip-archive',
    '.vol.gz': 'org.gnu.gnu-zip-archive',
}

# Formats macOS has a type and a preview of its own for, which Quick Look keeps to; the
# app declares them so it can open them, and the extension so a panel of its own can.
NATIVE_TYPES: dict[str, str] = {
    '.ply': 'public.polygon-file-format',
    '.stl': 'public.standard-tesselated-geometry-format',
    '.obj': 'public.geometry-definition-format',
}

# Extensions the environment can read that are deliberately not claimed, and why.
UNCLAIMED: dict[str, str] = {
    # macOS previews these itself, and Launch Services keeps its own type for them.
    '.glb': 'macOS previews it',
    '.gltf': 'macOS previews it',
    '.bmp': 'macOS previews it',
    '.gif': 'macOS previews it',
    '.hdr': 'macOS previews it',
    '.jpeg': 'macOS previews it',
    '.jpg': 'macOS previews it',
    '.png': 'macOS previews it',
    '.pnm': 'macOS previews it',
    '.tif': 'macOS previews it',
    '.tiff': 'macOS previews it',
    '.dcm': 'macOS previews it',
    # Launch Services gives the extension to another kind of file.
    '.img': 'disk images own the extension',
    '.raw': 'camera raw images own the extension',
    '.xml': 'XML owns the extension',
    # Readers pyvista-cad registers whose kernels the installer leaves out.
    '.brep': 'needs a full OpenCascade kernel',
    '.brp': 'needs a full OpenCascade kernel',
    '.fcstd': 'needs a full OpenCascade kernel',
    '.ifc': 'needs ifcopenshell',
    '.scad': 'needs the openscad program',
    # General containers, often huge, in which mesh files are the exception.
    '.h5': 'a general HDF5 container',
    '.hdf': 'a general HDF5 container',
    # meshio lists these but cannot read them.
    '.su2': 'meshio fails to read it',
    '.ugrid': 'meshio fails to read it',
    '.wkt': 'meshio hangs on it',
    '.svg': 'meshio writes it but does not read it',
}


def uti_for(ext: str) -> str:
    """Return the exported uniform type identifier for an extension."""
    return f'{UTI_PREFIX}.{ext.lstrip(".").replace(".", "-")}'


def default_extensions() -> list[str]:
    """Return the extensions claimed unless the config overrides them."""
    return sorted(FORMATS)


def resolve_extensions(add: list[str] | None = None, remove: list[str] | None = None) -> list[str]:
    """Return the claimed extensions after applying config additions and removals."""
    claimed = set(default_extensions())
    claimed |= {normalize(e) for e in add or []}
    claimed -= {normalize(e) for e in remove or []}
    return sorted(claimed)


def normalize(ext: str) -> str:
    """Return an extension lowercased and prefixed with a single dot."""
    return '.' + ext.strip().lstrip('.').lower()
