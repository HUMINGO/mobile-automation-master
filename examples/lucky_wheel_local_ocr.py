"""Local template-matching OCR for the Lucky Wheel ``You win`` amount.

Tesseract 3.02 returns blank for the stylised digits on the game's win
modal, so this module loads user-supplied digit templates (PNG glyphs
named ``0.png``..``9.png`` under a template directory) and recognises a
cropped amount image by per-glyph IoU against every template. Pillow
only — no subprocess, no Tesseract, no numpy.
"""
from pathlib import Path

from PIL import Image, ImageChops

DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "lucky_wheel_templates"
MATCH_SIZE = (32, 32)
FOREGROUND_THRESHOLD = 200
MIN_GLYPH_AREA = 20
MAX_GLYPH_AREA = 5000
MIN_GLYPH_WIDTH = 4
MIN_GLYPH_HEIGHT = 8
ACCEPT_MATCH = 0.30


def binarise(image, threshold=FOREGROUND_THRESHOLD):
    """Return a 1-bit PIL image: 255 for foreground, 0 for background."""
    return image.convert("L").point(lambda v: 255 if v >= threshold else 0, mode="1")


def find_glyphs(mask, min_area=MIN_GLYPH_AREA, max_area=MAX_GLYPH_AREA,
                min_width=MIN_GLYPH_WIDTH, min_height=MIN_GLYPH_HEIGHT):
    """Split a 1-bit mask into glyphs via 4-connected components.

    Glyphs are returned in left-to-right reading order. Components below
    the area or dimension thresholds are ignored as noise.
    """
    width, height = mask.size
    pixels = list(mask.getdata())
    visited = bytearray(len(pixels))
    glyphs = []
    for start, value in enumerate(pixels):
        if value == 0 or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        xs, ys = [], []
        while stack:
            index = stack.pop()
            x, y = index % width, index // width
            xs.append(x)
            ys.append(y)
            if x:
                neighbour = index - 1
                if pixels[neighbour] and not visited[neighbour]:
                    visited[neighbour] = 1
                    stack.append(neighbour)
            if x + 1 < width:
                neighbour = index + 1
                if pixels[neighbour] and not visited[neighbour]:
                    visited[neighbour] = 1
                    stack.append(neighbour)
            if y:
                neighbour = index - width
                if pixels[neighbour] and not visited[neighbour]:
                    visited[neighbour] = 1
                    stack.append(neighbour)
            if y + 1 < height:
                neighbour = index + width
                if pixels[neighbour] and not visited[neighbour]:
                    visited[neighbour] = 1
                    stack.append(neighbour)
        area = len(xs)
        if area < min_area or area > max_area:
            continue
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        if x1 - x0 + 1 < min_width or y1 - y0 + 1 < min_height:
            continue
        glyphs.append((x0, y0, x1, y1))
    glyphs.sort(key=lambda box: box[0])
    return glyphs


def _fit_into(image, size=MATCH_SIZE):
    """Place ``image`` centred in a ``size`` canvas, preserving aspect ratio.

    A tight crop resized to fill a square canvas would erase shape cues
    (a tall ``1`` and a wide ``0`` both become a full white square), so
    we preserve aspect ratio and centre the glyph instead.
    """
    canvas = Image.new("1", size, 0)
    src_w, src_h = image.size
    if src_w == 0 or src_h == 0:
        return canvas
    dst_w, dst_h = size
    scale = min(dst_w / src_w, dst_h / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = image.resize((new_w, new_h))
    canvas.paste(resized, ((dst_w - new_w) // 2, (dst_h - new_h) // 2))
    return canvas


def _to_match_mask(mask, box):
    """Crop a glyph and fit it into the canonical comparison canvas."""
    crop = mask.crop((box[0], box[1], box[2] + 1, box[3] + 1))
    return _fit_into(crop, MATCH_SIZE)


def _iou(mask_a, mask_b):
    """Intersection-over-union of two 1-bit PIL images of identical size."""
    intersection = ImageChops.logical_and(mask_a, mask_b)
    union = ImageChops.logical_or(mask_a, mask_b)
    inter = sum(1 for value in intersection.getdata() if value)
    union_ = sum(1 for value in union.getdata() if value)
    return inter / union_ if union_ else 0.0


def load_templates(template_dir=DEFAULT_TEMPLATE_DIR):
    """Return ``{char: [PIL.Image, ...]}`` loaded from a template directory.

    Filenames follow ``<digit>.png`` where ``<digit>`` is ``0``..``9``.
    Multiple samples per digit are supported (``0.png``, ``0_2.png``...).
    """
    template_dir = Path(template_dir)
    templates = {}
    if not template_dir.is_dir():
        return templates
    for path in sorted(template_dir.glob("*.png")):
        char = path.stem.split("_", 1)[0]
        if char not in "0123456789":
            continue
        with Image.open(path) as img:
            templates.setdefault(char, []).append(_fit_into(binarise(img), MATCH_SIZE))
    return templates


def recognise_amount(crop_image, templates, accept_match=ACCEPT_MATCH):
    """Return ``(amount_int, raw_text)`` for a cropped 'You win' amount image.

    ``crop_image`` may be RGB or L. Returns ``(None, raw)`` when no digit
    templates are loaded or no glyph survives the similarity threshold;
    glyphs below the threshold (typically comma separators) are skipped,
    so the parsed integer keeps its magnitude.
    """
    if not templates:
        return None, ""
    mask = binarise(crop_image)
    glyphs = find_glyphs(mask)
    if not glyphs:
        return None, ""
    chars = []
    for box in glyphs:
        glyph_mask = _to_match_mask(mask, box)
        scores = [(max(_iou(glyph_mask, sample) for sample in samples), char)
                  for char, samples in templates.items()]
        scores.sort(reverse=True)
        best_score, best_char = scores[0]
        if best_score < accept_match:
            continue
        chars.append(best_char)
    raw = "".join(chars)
    if not raw.isdigit():
        return None, raw
    return int(raw), raw
