"""Unit tests for the local template-matching OCR engine."""
import importlib.util
from pathlib import Path

import pytest
from PIL import Image

EXAMPLE = Path(__file__).parents[1] / "examples" / "lucky_wheel_local_ocr.py"
SPEC = importlib.util.spec_from_file_location("lucky_wheel_local_ocr", str(EXAMPLE))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _solid(size, value):
    return Image.new("L", size, value)


def _mask_from_pixels(pixels, width, height):
    image = Image.new("1", (width, height))
    image.putdata([1 if p else 0 for p in pixels])
    return image


def test_binarise_keeps_only_bright_pixels():
    image = Image.new("L", (4, 1))
    image.putdata([50, 200, 199, 255])
    mask = MODULE.binarise(image)
    assert list(mask.getdata()) == [0, 255, 0, 255]


def test_binarise_threshold_override():
    image = Image.new("L", (3, 1))
    image.putdata([100, 150, 200])
    mask = MODULE.binarise(image, threshold=150)
    assert list(mask.getdata()) == [0, 255, 255]


def test_find_glyphs_returns_two_separated_blocks():
    mask = Image.new("1", (30, 12))
    for x in range(8):
        for y in range(12):
            mask.putpixel((x, y), 1)
    for x in range(22, 30):
        for y in range(12):
            mask.putpixel((x, y), 1)
    glyphs = MODULE.find_glyphs(mask)
    assert glyphs == [(0, 0, 7, 11), (22, 0, 29, 11)]


def test_find_glyphs_drops_noise_below_min_area():
    mask = Image.new("1", (30, 12))
    mask.putpixel((0, 0), 1)
    for x in range(10, 18):
        for y in range(12):
            mask.putpixel((x, y), 1)
    glyphs = MODULE.find_glyphs(mask)
    assert glyphs == [(10, 0, 17, 11)]


def test_find_glyphs_sorted_left_to_right():
    mask = Image.new("1", (20, 3))
    for x in range(15, 18):
        for y in range(3):
            mask.putpixel((x, y), 1)
    for x in range(2, 5):
        for y in range(3):
            mask.putpixel((x, y), 1)
    glyphs = MODULE.find_glyphs(mask)
    assert [box[0] for box in glyphs] == sorted(box[0] for box in glyphs)


def test_iou_identical_masks_score_one():
    mask = Image.new("1", (4, 4), 1)
    assert MODULE._iou(mask, mask) == 1.0


def test_iou_disjoint_masks_score_zero():
    a = Image.new("1", (4, 4))
    b = Image.new("1", (4, 4))
    for y in range(2):
        for x in range(4):
            a.putpixel((x, y), 1)
            b.putpixel((x, y + 2), 1)
    assert MODULE._iou(a, b) == 0.0


def test_iou_partial_overlap_between_zero_and_one():
    a = Image.new("1", (4, 4), 1)
    b = Image.new("1", (4, 4), 0)
    for y in range(2):
        for x in range(4):
            b.putpixel((x, y), 1)
    score = MODULE._iou(a, b)
    assert 0.0 < score < 1.0


def test_load_templates_returns_empty_for_missing_dir(tmp_path):
    assert MODULE.load_templates(tmp_path / "does_not_exist") == {}


def test_load_templates_loads_digit_files(tmp_path):
    Image.new("L", (32, 32), 255).save(tmp_path / "0.png")
    Image.new("L", (32, 32), 255).save(tmp_path / "3.png")
    Image.new("L", (32, 32), 255).save(tmp_path / "ignored.png")
    templates = MODULE.load_templates(tmp_path)
    assert set(templates.keys()) == {"0", "3"}
    assert all(len(samples) == 1 for samples in templates.values())


def test_load_templates_supports_multiple_samples_per_digit(tmp_path):
    Image.new("L", (32, 32), 255).save(tmp_path / "0.png")
    Image.new("L", (32, 32), 255).save(tmp_path / "0_2.png")
    templates = MODULE.load_templates(tmp_path)
    assert len(templates["0"]) == 2


def test_recognise_amount_returns_none_without_templates():
    crop = Image.new("L", (40, 20), 255)
    amount, raw = MODULE.recognise_amount(crop, {})
    assert amount is None
    assert raw == ""


def test_recognise_amount_returns_none_when_no_glyphs():
    templates = {"0": [Image.new("1", MODULE.MATCH_SIZE, 1)]}
    crop = Image.new("L", (40, 20), 0)
    amount, raw = MODULE.recognise_amount(crop, templates)
    assert amount is None
    assert raw == ""


def _tight_template(width, height):
    """Build a tight all-foreground template, matching load_templates' output."""
    return MODULE._fit_into(Image.new("1", (width, height), 1), MODULE.MATCH_SIZE)


def test_recognise_amount_matches_a_perfect_template():
    templates = {"7": [_tight_template(12, 16)]}
    canvas = Image.new("L", (60, 40), 0)
    for y in range(8, 24):
        for x in range(10, 22):
            canvas.putpixel((x, y), 255)
    amount, raw = MODULE.recognise_amount(canvas, templates)
    assert amount == 7
    assert raw == "7"


def test_recognise_amount_concatenates_multiple_digits():
    templates = {
        "2": [_tight_template(8, 20)],
        "5": [_tight_template(20, 8)],
    }
    canvas = Image.new("L", (80, 32), 0)
    for y in range(6, 26):
        for x in range(2, 10):
            canvas.putpixel((x, y), 255)
    for y in range(12, 20):
        for x in range(30, 50):
            canvas.putpixel((x, y), 255)
    amount, raw = MODULE.recognise_amount(canvas, templates)
    assert amount == 25
    assert raw == "25"
