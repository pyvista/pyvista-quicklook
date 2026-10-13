# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pillow"]
# ///
"""Draw AppIcon.icns: a magnifier over the PyVista logo.

From 128 px up the lens holds "PyVista"; below that it holds "Vista" alone, which stays
legible where the icon is small. Run ``uv run icon/make_icon.py``;
``--logo pyvista/logo/pyvista_sq.png`` re-cuts pyvista.png from the logo.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image
from PIL import ImageChops
from PIL import ImageDraw
from PIL import ImageFilter

HERE = Path(__file__).resolve().parent
PYVISTA = HERE / 'pyvista.png'
# The lettering is kept about as wide as it is drawn.
LETTERING_WIDTH = 1100
# Pictures this wide or wider are stored with a 256-colour palette, which keeps the .icns small.
PALETTE_FROM = 512

# Drawn at twice the largest size, then scaled down, so every edge is antialiased.
S = 2048
# Where "Vista" starts and the V ends, as fractions of the lettering's width.
V_START = 1526 / 4116
V_END = 2473 / 4116

RIM_WIDTH = 0.036
HANDLE_ANGLE = 45
# The two designs: whether the lettering keeps the Py, the lens centre and glass radius as
# fractions of the canvas, the grip's length, the lettering's width as a share of the glass,
# the zoom at the centre of the glass, and the share of the radius magnified evenly before
# the zoom eases to 1x at the rim (0 bulges all the way from the centre).
LARGE = {
    'py': True,
    'centre': (0.43, 0.43),
    'radius': 0.29,
    'grip': 0.216,
    'fill': 0.9,
    'zoom': 1.85,
    'flat': 0.5,
}
SMALL = {
    'py': False,
    'centre': (0.46, 0.46),
    'radius': 0.3,
    'grip': 0.12,
    'fill': 0.84,
    'zoom': 2.0,
    'flat': 0.0,
}
# The smallest size drawn from LARGE.
LARGE_FROM = 128

LIGHT = np.array([-0.45, -0.6, 0.66])
LIGHT /= np.linalg.norm(LIGHT)
HALF = LIGHT + np.array([0, 0, 1.0])
HALF /= np.linalg.norm(HALF)

# Gloss black for the rim and neck, a softer satin black for the grip.
MATERIALS = {
    'gloss': {'base': (22, 22, 26), 'amb': 0.9, 'dif': 0.5, 'spec': 0.9, 'shin': 60, 'env': 0.25},
    'satin': {
        'base': (26, 26, 30),
        'amb': 0.9,
        'dif': 0.45,
        'spec': 0.55,
        'shin': 35,
        'env': 0.15,
    },
}

ICONSET = [
    ('icon_16x16.png', 16),
    ('icon_16x16@2x.png', 32),
    ('icon_32x32.png', 32),
    ('icon_32x32@2x.png', 64),
    ('icon_128x128.png', 128),
    ('icon_128x128@2x.png', 256),
    ('icon_256x256.png', 256),
    ('icon_256x256@2x.png', 512),
    ('icon_512x512.png', 512),
    ('icon_512x512@2x.png', 1024),
]


def cut_lettering(logo_path: Path) -> Image.Image:
    """Return the lettering from the PyVista logo, cropped to it."""
    image = Image.open(logo_path).convert('RGBA')
    image = image.crop(image.getbbox())
    return image.resize(
        (LETTERING_WIDTH, round(image.height * LETTERING_WIDTH / image.width)), Image.LANCZOS
    )


def without_py(lettering: Image.Image) -> Image.Image:
    """Return the lettering without the P and the y, cropped to what is left."""
    pixels = np.array(lettering).astype(int)
    width = pixels.shape[1]
    cols = np.arange(width)[None, :]
    pixels[..., 3] = np.where(cols < V_START * width, 0, pixels[..., 3])
    # The gold y reaches under the V, so it is dropped by colour where they overlap.
    gold = (pixels[..., 0] > pixels[..., 2] + 25) & (cols < V_END * width)
    pixels[gold, 3] = 0
    image = Image.fromarray(pixels.astype(np.uint8))
    return image.crop(image.getbbox())


def vertical_gradient(top: tuple[int, ...], bottom: tuple[int, ...]) -> Image.Image:
    """Return an S x S RGB image fading from top to bottom."""
    t = np.linspace(0, 1, S)[:, None, None]
    rows = np.array(top, float) * (1 - t) + np.array(bottom, float) * t
    return Image.fromarray(rows.repeat(S, axis=1).astype(np.uint8), 'RGB')


def radial_gradient(centre, radius, inner, outer) -> Image.Image:
    """Return an S x S RGBA image fading from inner at centre to outer at radius."""
    yy, xx = np.mgrid[0:S, 0:S]
    t = np.clip(np.hypot(xx - centre[0], yy - centre[1]) / radius, 0, 1)[..., None]
    out = np.array(inner, float) * (1 - t) + np.array(outer, float) * t
    return Image.fromarray(out.astype(np.uint8), 'RGBA')


def circle_mask(centre, radius) -> Image.Image:
    """Return an S x S mask that is opaque inside the circle."""
    mask = Image.new('L', (S, S), 0)
    box = [centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius]
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    return mask


def soft_shadow(alpha: Image.Image, dx: float, dy: float, blur: float, opacity: float):
    """Return a blurred black shadow of an alpha mask, offset by dx, dy."""
    shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda v: int(v * opacity)))
    shadow = shadow.transform(shadow.size, Image.AFFINE, (1, 0, -dx, 0, 1, -dy))
    return shadow.filter(ImageFilter.GaussianBlur(blur))


def tile() -> tuple[Image.Image, Image.Image]:
    """Return Apple's 824/1024 rounded square with its drop shadow, and its mask."""
    inset = S * 100 / 1024
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [inset, inset, S - inset, S - inset], radius=int((S - 2 * inset) * 0.225), fill=255
    )
    canvas = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    canvas.alpha_composite(soft_shadow(mask, 0, S * 0.012, S * 0.012, 0.35))
    fill = vertical_gradient((255, 255, 255), (226, 232, 240)).convert('RGBA')
    fill.putalpha(mask)
    canvas.alpha_composite(fill)
    return canvas, mask


def shade(nx, ny, nz, material: str):
    """Light a surface by its normals: Blinn-Phong plus a bright-sky, dark-ground term."""
    m = MATERIALS[material]
    diffuse = np.clip(nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2], 0, 1)
    specular = np.clip(nx * HALF[0] + ny * HALF[1] + nz * HALF[2], 0, 1) ** m['shin']
    sky = np.clip(0.5 - 0.5 * ny, 0, 1) ** 2
    colour = np.array(m['base'], float)[None, None] * (m['amb'] + m['dif'] * diffuse)[..., None]
    colour += (m['env'] * 255 * 0.6 * sky + m['spec'] * 255 * specular)[..., None]
    return colour.clip(0, 255)


def rim(canvas: Image.Image, centre, radius: float, width: float) -> None:
    """Draw the rim as the top half of a torus, lit from the upper left."""
    x0, y0 = int(centre[0] - radius - width) - 2, int(centre[1] - radius - width) - 2
    x1, y1 = int(centre[0] + radius + width) + 2, int(centre[1] + radius + width) + 2
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(float) + 0.5
    dx, dy = xx - centre[0], yy - centre[1]
    dist = np.maximum(np.hypot(dx, dy), 1e-6)
    q = np.clip(2 * (dist - radius) / width - 1, -1, 1)
    colour = shade(dx / dist * q, dy / dist * q, np.sqrt(1 - q**2), 'gloss')
    alpha = np.clip(np.minimum(dist - radius, radius + width - dist) + 0.5, 0, 1)
    rgba = np.dstack([colour, alpha * 255]).astype(np.uint8)
    canvas.alpha_composite(Image.fromarray(rgba, 'RGBA'), (x0, y0))


def handle(canvas: Image.Image, start, angle_deg: float, segments) -> None:
    """Draw the handle as cylinders end to end; segments are (length, width, material)."""
    a = math.radians(angle_deg)
    d = np.array([math.cos(a), math.sin(a)])
    p = np.array([-math.sin(a), math.cos(a)])
    total = sum(length for length, _, _ in segments)
    widest = max(width for _, width, _ in segments)
    end = np.array(start) + d * total
    x0 = int(min(start[0], end[0]) - widest) - 2
    y0 = int(min(start[1], end[1]) - widest) - 2
    x1 = int(max(start[0], end[0]) + widest) + 2
    y1 = int(max(start[1], end[1]) + widest) + 2
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(float) + 0.5
    along = (xx - start[0]) * d[0] + (yy - start[1]) * d[1]
    across = (xx - start[0]) * p[0] + (yy - start[1]) * p[1]
    out = np.zeros((*along.shape, 4))
    offset = 0.0
    for i, (length, width, material) in enumerate(segments):
        half = width / 2
        s = along - offset
        in_body = (s >= 0) & (s <= length)
        # The last segment ends in a rounded cap.
        in_cap = (
            (s > length) & (np.hypot(s - length, across) <= half)
            if i == len(segments) - 1
            else False
        )
        u = np.where(in_cap, np.clip(s - length, 0, None) / half, 0)
        v = np.clip(across / half, -1, 1)
        nz = np.sqrt(np.clip(1 - u**2 - v**2, 0, 1))
        colour = shade(v * p[0] + u * d[0], v * p[1] + u * d[1], nz, material)
        edge = np.where(
            in_cap,
            np.clip(half - np.hypot(s - length, across) + 0.5, 0, 1),
            np.clip(half - np.abs(across) + 0.5, 0, 1),
        )
        alpha = np.where(in_body | in_cap, edge, 0)[..., None]
        out[..., :3] = out[..., :3] * (1 - alpha) + colour * alpha
        out[..., 3:] = np.maximum(out[..., 3:], alpha)
        offset += length
    rgba = np.dstack([out[..., :3], out[..., 3] * 255]).clip(0, 255).astype(np.uint8)
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    layer.alpha_composite(Image.fromarray(rgba, 'RGBA'), (x0, y0))
    canvas.alpha_composite(
        soft_shadow(layer.getchannel('A'), S * 0.012, S * 0.018, S * 0.012, 0.4)
    )
    canvas.alpha_composite(layer)


def magnify(src: Image.Image, centre, radius: float, zoom: float, flat: float) -> Image.Image:
    """Return src seen through a lens: zoom at the middle, easing to 1x at the rim."""
    a = np.asarray(src).astype(float)
    x0, y0 = int(centre[0] - radius) - 2, int(centre[1] - radius) - 2
    x1, y1 = int(centre[0] + radius) + 2, int(centre[1] + radius) + 2
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(float)
    dx, dy = xx - centre[0], yy - centre[1]
    t = np.clip(np.hypot(dx, dy) / radius, 0, 1)
    ease = np.clip((t - flat) / (1 - flat), 0, 1) ** 2
    k = 1 / zoom + (1 - 1 / zoom) * ease
    sx, sy = centre[0] + dx * k, centre[1] + dy * k
    ix = np.clip(np.floor(sx).astype(int), 0, S - 2)
    iy = np.clip(np.floor(sy).astype(int), 0, S - 2)
    fx, fy = (sx - ix)[..., None], (sy - iy)[..., None]
    out = (
        a[iy, ix] * (1 - fx) * (1 - fy)
        + a[iy, ix + 1] * fx * (1 - fy)
        + a[iy + 1, ix] * (1 - fx) * fy
        + a[iy + 1, ix + 1] * fx * fy
    )
    patch = Image.fromarray(out.clip(0, 255).astype(np.uint8), 'RGBA')
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    layer.paste(patch, (x0, y0), circle_mask(centre, radius).crop((x0, y0, x1, y1)))
    return layer


def white(mask: Image.Image) -> Image.Image:
    """Return white, with the given mask as its opacity."""
    layer = Image.new('RGBA', (S, S), (255, 255, 255, 0))
    layer.putalpha(mask)
    return layer


def reflections(canvas: Image.Image, centre, radius: float) -> None:
    """Add the glass: a darker band inside the rim, a crescent, a glint and a bounce."""
    cx, cy, r = centre[0], centre[1], radius
    lens = circle_mask(centre, radius)
    # Thick glass refracts the rim, so the glass darkens just inside it.
    yy, xx = np.mgrid[0:S, 0:S]
    t = np.hypot(xx - cx, yy - cy) / r
    band = np.zeros((S, S, 4), np.uint8)
    band[..., :3] = (20, 40, 70)
    band[..., 3] = (np.clip((t - 0.82) / 0.18, 0, 1) ** 2 * 70).astype(np.uint8)
    band_layer = Image.fromarray(band, 'RGBA')
    band_layer.putalpha(ImageChops.multiply(band_layer.getchannel('A'), lens))
    canvas.alpha_composite(band_layer)
    # A broad crescent along the upper left.
    crescent = Image.new('L', (S, S), 0)
    draw = ImageDraw.Draw(crescent)
    draw.ellipse([cx - r * 0.86, cy - r * 0.86, cx + r * 0.86, cy + r * 0.86], fill=70)
    draw.ellipse([cx - r * 0.78, cy - r * 0.66, cx + r * 0.98, cy + r * 1.1], fill=0)
    crescent = crescent.filter(ImageFilter.GaussianBlur(r * 0.03))
    canvas.alpha_composite(white(ImageChops.multiply(crescent, lens)))
    # A small glint where the light strikes.
    glint = Image.new('L', (S, S), 0)
    ImageDraw.Draw(glint).ellipse(
        [cx - r * 0.62, cy - r * 0.66, cx - r * 0.36, cy - r * 0.46], fill=200
    )
    glint = glint.filter(ImageFilter.GaussianBlur(r * 0.04))
    canvas.alpha_composite(white(ImageChops.multiply(glint, lens)))
    # Light bouncing back along the lower right.
    bounce = Image.new('L', (S, S), 0)
    draw = ImageDraw.Draw(bounce)
    draw.ellipse([cx - r * 0.9, cy - r * 0.9, cx + r * 0.9, cy + r * 0.9], fill=55)
    draw.ellipse([cx - r * 1.05, cy - r * 1.05, cx + r * 0.72, cy + r * 0.72], fill=0)
    bounce = bounce.filter(ImageFilter.GaussianBlur(r * 0.04))
    canvas.alpha_composite(white(ImageChops.multiply(bounce, lens)))


def draw_icon(design: dict) -> Image.Image:
    """Return the icon at S x S."""
    canvas, mask = tile()
    content = canvas.copy()
    centre = (S * design['centre'][0], S * design['centre'][1])
    radius = S * design['radius']
    width = S * RIM_WIDTH

    lettering = Image.open(PYVISTA).convert('RGBA')
    if not design['py']:
        lettering = without_py(lettering)
    letters = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    lw = int(2 * radius * design['fill'])
    lh = int(lettering.height * lw / lettering.width)
    letters.alpha_composite(
        lettering.resize((lw, lh), Image.LANCZOS),
        (int(centre[0] - lw / 2), int(centre[1] - lh / 2)),
    )

    lens_shadow = circle_mask((centre[0] + S * 0.012, centre[1] + S * 0.02), radius + width)
    content.alpha_composite(soft_shadow(lens_shadow, 0, 0, S * 0.02, 0.45))
    a = math.radians(HANDLE_ANGLE)
    reach = radius + width * 0.6
    start = (centre[0] + reach * math.cos(a), centre[1] + reach * math.sin(a))
    handle(
        content,
        start,
        HANDLE_ANGLE,
        [(S * 0.03, S * 0.045, 'gloss'), (S * design['grip'], S * 0.075, 'satin')],
    )

    glass = radial_gradient(
        (centre[0] - radius * 0.35, centre[1] - radius * 0.4),
        radius * 1.7,
        (255, 255, 255, 255),
        (214, 228, 242, 255),
    )
    content.alpha_composite(
        Image.composite(
            glass, Image.new('RGBA', (S, S), (0, 0, 0, 0)), circle_mask(centre, radius)
        )
    )
    content.alpha_composite(magnify(letters, centre, radius, design['zoom'], design['flat']))
    reflections(content, centre, radius)
    rim(content, centre, radius - 1, width)

    # Everything stays inside the rounded square; the square keeps its own shadow.
    clipped = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    clipped.paste(content, (0, 0), mask)
    canvas.alpha_composite(clipped)
    return canvas


def write_icns(large: Image.Image, small: Image.Image, destination: Path) -> None:
    """Write every size macOS asks for into an .iconset and pack it with iconutil."""
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / 'AppIcon.iconset'
        iconset.mkdir()
        for name, size in ICONSET:
            icon = large if size >= LARGE_FROM else small
            picture = icon.resize((size, size), Image.LANCZOS)
            if size >= PALETTE_FROM:
                picture = picture.quantize(256, method=Image.Quantize.FASTOCTREE)
            picture.save(iconset / name, optimize=True)
        subprocess.run(
            ['iconutil', '--convert', 'icns', '--output', str(destination), str(iconset)],
            check=True,
        )


def main() -> None:
    """Cut the lettering if asked, then write the .icns, beside this script by default."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--logo', type=Path, help='PyVista logo to cut the lettering from')
    parser.add_argument('--output', type=Path, default=HERE / 'AppIcon.icns')
    args = parser.parse_args()
    if args.logo:
        cut_lettering(args.logo).save(PYVISTA, optimize=True)
    if shutil.which('iconutil') is None:
        msg = 'iconutil not found; the .icns can only be built on macOS'
        raise SystemExit(msg)
    write_icns(draw_icon(LARGE), draw_icon(SMALL), args.output)


if __name__ == '__main__':
    main()
