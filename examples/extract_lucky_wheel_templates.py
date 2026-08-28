"""Extract digit templates from a Lucky Wheel ``You win`` screenshot.

Run this on a screenshot where the win modal is fully visible. The script
binarises the supplied amount region, segments it into glyphs, saves each
glyph to ``lucky_wheel_templates/glyph_<idx>.png``, and prints an ASCII
preview of every glyph so you can rename the files to ``0.png``...
``9.png`` once you recognise each digit.

Example
-------

    python examples/extract_lucky_wheel_templates.py \
        --screenshot examples/artifacts/lucky_wheel/round_001_result.png \
        --amount-box 0.28 0.73 0.64 0.76

The four ``--amount-box`` values are relative coordinates (0..1) of the
left/top/right/bottom edges of the digit band inside the screenshot.
The defaults above are calibrated for a 720x1600 device; adjust them
if your screenshot has a different layout. After running, ``cd
examples/lucky_wheel_templates`` and rename ``glyph_00.png`` to the
digit it shows (e.g. ``2.png``), and delete any glyph that is a comma
or noise. Re-run on more screenshots to collect samples for every
digit; multiple files per digit are allowed (``0.png``,
``0_2.png``...).
"""
import argparse
from pathlib import Path

from PIL import Image

from lucky_wheel_local_ocr import (
    DEFAULT_TEMPLATE_DIR, FOREGROUND_THRESHOLD, binarise, find_glyphs,
)


def ascii_preview(mask, width=28, height=14):
    """Render a 1-bit mask as a downsampled ASCII preview."""
    src_w, src_h = mask.size
    lines = []
    for ry in range(height):
        y0 = ry * src_h // height
        y1 = (ry + 1) * src_h // height
        line = []
        for rx in range(width):
            x0 = rx * src_w // width
            x1 = (rx + 1) * src_w // width
            block = mask.crop((x0, y0, x1, y1))
            line.append("#" if any(block.getdata()) else " ")
        lines.append("".join(line).rstrip())
    return "\n".join(lines)


def extract(screenshot, amount_box, output_dir):
    """Save glyphs from the amount region and return their count."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(screenshot) as img:
        width, height = img.size
        left, top, right, bottom = amount_box
        box = (int(width * left), int(height * top),
               int(width * right), int(height * bottom))
        crop = img.crop(box).convert("L")
    mask = binarise(crop)
    mask_path = output_dir / "_binarised_region.png"
    mask.save(mask_path)
    glyphs = find_glyphs(mask)
    print("screenshot size: {}x{}".format(width, height))
    print("amount box (abs): {} -> binarised at {}".format(box, mask_path))
    print("glyphs found: {}".format(len(glyphs)))
    print()
    for i, (x0, y0, x1, y1) in enumerate(glyphs):
        glyph_mask = mask.crop((x0, y0, x1 + 1, y1 + 1))
        out = output_dir / "glyph_{:02d}.png".format(i)
        glyph_mask.save(out)
        print("=== glyph {}: {} x=[{}..{}] y=[{}..{}] w={} h={} ===".format(
            i, out.name, x0, x1, y0, y1, x1 - x0 + 1, y1 - y0 + 1))
        print(ascii_preview(glyph_mask))
        print()
    return len(glyphs)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--screenshot", type=Path, required=True,
                        help="截图路径（中奖弹窗完整可见）")
    parser.add_argument("--amount-box", type=float, nargs=4, required=True,
                        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                        help="金额数字带在截图中的相对坐标 0..1（左上右下）")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_TEMPLATE_DIR,
                        help="模板输出目录，默认 examples/lucky_wheel_templates/")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.screenshot.is_file():
        raise SystemExit("找不到截图：{}".format(args.screenshot))
    left, top, right, bottom = args.amount_box
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        raise SystemExit("--amount-box 必须满足 0<=left<right<=1 且 0<=top<bottom<=1")
    extract(args.screenshot, args.amount_box, args.output_dir)
    print("已保存到 {}；请按 ASCII 字符画识别每个数字并重命名为 0.png..9.png。".format(args.output_dir))
    print("逗号或噪点可以直接删除。多次运行可叠加多个样本。")


if __name__ == "__main__":
    raise SystemExit(main())
