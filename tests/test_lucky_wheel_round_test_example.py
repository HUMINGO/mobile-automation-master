"""Integration tests for the Lucky Wheel round verifier.

Covers the local-OCR fallback in ``read_win_payout`` and the
modal-frame preservation in ``wait_for_result`` that together fix the
two reported symptoms: Tesseract 3.02 returning blank for the win
modal, and the failure screenshot landing on the next round's
countdown.
"""
import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image

EXAMPLES_DIR = Path(__file__).parents[1] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

EXAMPLE = EXAMPLES_DIR / "lucky_wheel_round_test.py"
SPEC = importlib.util.spec_from_file_location("lucky_wheel_round_test", str(EXAMPLE))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

import lucky_wheel_local_ocr as OCR


def _make_screenshot(path, size=(720, 1600)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (0, 0, 0)).save(path)
    return path


def test_read_win_payout_returns_none_when_tesseract_and_local_both_empty(tmp_path, monkeypatch):
    screenshot = _make_screenshot(tmp_path / "screen.png")
    monkeypatch.setattr(MODULE, "ocr_image", lambda *a, **k: "")
    amount, raw = MODULE.read_win_payout(
        screenshot, tmp_path, "tesseract", templates={}, amount_box=(0.1, 0.1, 0.9, 0.9))
    assert amount is None
    assert raw == ""


def test_read_win_payout_uses_tesseract_match_when_available(tmp_path, monkeypatch):
    screenshot = _make_screenshot(tmp_path / "screen.png")
    monkeypatch.setattr(MODULE, "ocr_image", lambda *a, **k: "You win 295,000 coins")
    amount, raw = MODULE.read_win_payout(
        screenshot, tmp_path, "tesseract", templates={}, amount_box=(0.1, 0.1, 0.9, 0.9))
    assert amount == 295000
    assert "295,000" in raw


def test_read_win_payout_falls_back_to_local_ocr(tmp_path, monkeypatch):
    screenshot = tmp_path / "screen.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (720, 1600), (0, 0, 0))
    # Draw a single 12x16 white block inside the amount box (0.28, 0.73, 0.64, 0.76).
    # abs box: x=201..460, y=1168..1216.  Place the glyph near the left edge.
    for y in range(1180, 1196):
        for x in range(210, 222):
            canvas.putpixel((x, y), (255, 255, 255))
    canvas.save(screenshot)
    monkeypatch.setattr(MODULE, "ocr_image", lambda *a, **k: "")
    digit_template = Image.new("1", (12, 16), 1)
    templates = {"7": [OCR._fit_into(digit_template, OCR.MATCH_SIZE)]}
    amount, raw = MODULE.read_win_payout(
        screenshot, tmp_path, "tesseract", templates=templates,
        amount_box=(0.28, 0.73, 0.64, 0.76))
    assert amount == 7
    assert raw.startswith("local_ocr:")


class FakeClock:
    def __init__(self, ticks):
        self.ticks = iter(ticks)

    def __call__(self):
        return next(self.ticks)


class FakeClient:
    def screenshot(self, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (720, 1600), (0, 0, 0)).save(target)


def test_wait_for_result_preserves_last_modal_frame_when_ocr_fails(tmp_path, monkeypatch):
    """The returned screenshot is the last modal-visible frame, not a fresh capture."""
    frames = []

    def fake_capture(client, directory, name):
        path = directory / name
        Image.new("RGB", (720, 1600), (0, 0, 0)).save(path)
        frames.append(path)
        return path, (720, 1600)

    winner_sequence = iter([("tiger", {}), ("tiger", {}), (None, {})])

    def fake_highlighted(image):
        return next(winner_sequence)

    def fake_read_payout(image, directory, ocr_path, templates, amount_box):
        return None, ""  # OCR always fails — simulates Tesseract 3.02 on stylised font

    monkeypatch.setattr(MODULE, "capture", fake_capture)
    monkeypatch.setattr(MODULE, "highlighted_winner", fake_highlighted)
    monkeypatch.setattr(MODULE, "read_win_payout", fake_read_payout)

    # Keep polling through the full configured result window, then return the
    # latest modal-visible frame rather than a later non-modal capture.
    clock = FakeClock([0.0, 0.0, 0.3, 0.6, 0.9])
    payout, raw, screenshot, winner = MODULE.wait_for_result(
        FakeClient(), tmp_path, "tesseract", templates={}, amount_box=(0.28, 0.73, 0.64, 0.76),
        timeout=0.8, poll_interval=0.0, sleep=lambda _: None, monotonic=clock)

    assert payout is None
    assert winner == "tiger"
    # The returned screenshot must be the last frame where the modal was visible
    # (frames[1], the second "tiger" frame), not frames[2] where the modal had gone.
    assert Path(screenshot) == frames[1]


def test_preserve_modal_screenshot_copies_source_to_destination(tmp_path):
    source = tmp_path / "modal.png"
    Image.new("RGB", (10, 10), (123, 45, 67)).save(source)
    destination = tmp_path / "subdir" / "FAIL_round_0001_ocr.png"
    MODULE.preserve_modal_screenshot(source, destination)
    assert destination.is_file()
    with Image.open(destination) as copied:
        assert copied.getpixel((0, 0)) == (123, 45, 67)


def test_preserve_modal_screenshot_handles_missing_source(tmp_path):
    destination = tmp_path / "FAIL_round_0001_ocr.png"
    MODULE.preserve_modal_screenshot(None, destination)
    assert not destination.exists()
    MODULE.preserve_modal_screenshot(tmp_path / "nonexistent.png", destination)
    assert not destination.exists()


def test_build_parser_exposes_template_dir_and_amount_box():
    parser = MODULE.build_parser()
    args = parser.parse_args([
        "--template-dir", "examples/lucky_wheel_templates",
        "--amount-box", "0.28", "0.73", "0.64", "0.76",
    ])
    assert args.template_dir == Path("examples/lucky_wheel_templates")
    assert args.amount_box == [0.28, 0.73, 0.64, 0.76]


def test_build_parser_amount_box_defaults_match_sample():
    args = MODULE.build_parser().parse_args([])
    assert tuple(args.amount_box) == MODULE.DEFAULT_AMOUNT_BOX


def test_five_animals_have_distinct_expected_returns():
    stakes = {animal: 50000 * count for animal, count in MODULE.REPEATS_PER_CYCLE.items()}
    payouts = MODULE.expected_payouts(stakes)
    assert sum(stakes.values()) == 750000
    assert len(set(payouts.values())) == 5


def test_actual_payout_identifies_one_winner_or_a_mismatch():
    stakes = {animal: 50000 * count for animal, count in MODULE.REPEATS_PER_CYCLE.items()}
    payouts = MODULE.expected_payouts(stakes)
    assert MODULE.matching_winner(payouts["hippo"], payouts) == "hippo"
    assert MODULE.matching_winner(1534, payouts) is None
