"""Visual end-to-end verifier for the Lucky Wheel Android game.

The script never relies on its own start time: it OCRs the number in the wheel
centre and places bets only while it reads a countdown from 20 through 1. It
uses the 50,000 chip and repeatedly covers five animals. Start with the game
page open; use an authorised sandbox/test balance only.

The win amount is OCR'd by Tesseract first; when the bundled 3.02 engine
returns blank for the stylised win-modal digits, a local template-matching
OCR (``lucky_wheel_local_ocr``) takes over using user-supplied digit
templates under ``examples/lucky_wheel_templates/``. Generate them once
with ``examples/extract_lucky_wheel_templates.py``. While OCR fails, the
last frame in which the modal was visible is preserved for diagnosis —
not a fresh capture that would land on the next round's countdown.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from PIL import Image, ImageEnhance
from mobile_automation import AdbClient, AdbError

from lucky_wheel_local_ocr import DEFAULT_TEMPLATE_DIR, load_templates, recognise_amount

REFERENCE_SIZE = (720, 1600)
DEFAULT_OCR_PATH = Path(r"D:\Program Files (x86)\Tesseract-OCR\tesseract.exe")
DEFAULT_AMOUNT_BOX = (0.28, 0.73, 0.64, 0.76)
DEFAULT_ANIMALS = ("tiger", "fox", "elephant", "rabbit", "hippo")
# Different repeat counts make each expected payout unique, including 5.9x odds.
REPEATS_PER_CYCLE = {"tiger": 1, "fox": 2, "elephant": 3, "rabbit": 4, "hippo": 5}
MINIMUM_BETTING_SECONDS = 9
ANIMALS = {
    "tiger": ((130, 1268), 5.9), "fox": ((360, 1268), 5.9),
    "elephant": ((590, 1268), 5.9), "rabbit": ((130, 1370), 8.8),
    "hippo": ((360, 1370), 17.8), "frog": ((590, 1370), 2.98),
}
CHIPS = {50000: (650, 1460)}
# The result dialog highlights the card of the drawn animal with a bright frame.
CARD_BOXES = {
    "tiger": (20, 1220, 240, 1310), "fox": (250, 1220, 470, 1310),
    "elephant": (480, 1220, 700, 1310), "rabbit": (20, 1320, 240, 1410),
    "hippo": (250, 1320, 470, 1410), "frog": (480, 1320, 700, 1410),
}
_RAPID_OCR = None


def scaled(point, size):
    return round(point[0] * size[0] / REFERENCE_SIZE[0]), round(point[1] * size[1] / REFERENCE_SIZE[1])


def capture(client, directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    client.screenshot(path)
    with Image.open(path) as image:
        return path, image.size


def log(path, **entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    entry["timestamp"] = datetime.now().astimezone().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def ocr_image(image_path, crop_box, output_path, ocr_path, whitelist, psm=11,
              run_command=subprocess.run):
    """OCR a relative screen crop and save the crop for diagnosis."""
    with Image.open(image_path) as image:
        width, height = image.size
        left, top, right, bottom = crop_box
        crop = image.crop((int(width * left), int(height * top), int(width * right), int(height * bottom))).convert("L")
        crop = ImageEnhance.Contrast(crop).enhance(3).resize((crop.width * 4, crop.height * 4))
        crop.save(output_path)
    # This workstation has Tesseract 3.02.  It uses ``-psm`` (one dash) and
    # treats modern ``--psm``/``-c`` flags as filenames, silently yielding no
    # useful OCR result.
    result = run_command([str(ocr_path), str(output_path), "stdout", "-psm", str(psm)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         check=False, timeout=12)
    if result.returncode:
        raise ValueError("Tesseract 失败：{}".format(result.stderr.strip() or "返回非零状态"))
    return re.sub(r"\s+", " ", result.stdout).strip()


def visual_countdown_present(screenshot):
    """Detect the large white digit inside the centre circle without OCR.

    OCR is still attempted first, but the legacy engine occasionally misses the
    stylised game font.  The tight crop excludes wheel labels and the button
    ring; a countdown digit is one large white connected component there,
    whereas the ``draw`` label has several smaller letter components.
    """
    with Image.open(screenshot) as image:
        width, height = image.size
        crop = image.convert("L").crop((int(width * 0.45), int(height * 0.505),
                                         int(width * 0.55), int(height * 0.555)))
    pixels, width, height = list(crop.getdata()), crop.width, crop.height
    foreground = [value >= 210 for value in pixels]
    visited, components = bytearray(len(foreground)), []
    for start, is_white in enumerate(foreground):
        if not is_white or visited[start]:
            continue
        stack, area = [start], 0
        visited[start] = 1
        while stack:
            index = stack.pop()
            area += 1
            x, y = index % width, index // width
            for neighbor in (index - 1 if x else -1, index + 1 if x + 1 < width else -1,
                             index - width if y else -1, index + width if y + 1 < height else -1):
                if neighbor >= 0 and foreground[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    stack.append(neighbor)
        if area >= max(30, width * height // 100):
            components.append(area)
    white_ratio = sum(foreground) / len(foreground)
    # Anti-aliasing can split a stylised digit into a few bright islands.
    return 1 <= len(components) <= 3 and 0.04 <= white_ratio <= 0.55


def read_countdown(screenshot, directory, ocr_path):
    """Return centre countdown 1..20, otherwise None (draw/animation state)."""
    with Image.open(screenshot) as image:
        width, height = image.size
    # RapidOCR reads the actual stylised number (for example 14), unlike the
    # old Tesseract fallback which can only tell that a number-shaped glyph is
    # present. Restrict by position so status-bar/card numbers are ignored.
    for box, text, _score in rapid_ocr_items(screenshot):
        center_x = sum(point[0] for point in box) / len(box)
        center_y = sum(point[1] for point in box) / len(box)
        compact = text.replace(" ", "")
        if (
            re.fullmatch(r"\d{1,2}", compact)
            and width * 0.40 <= center_x <= width * 0.60
            and height * 0.43 <= center_y <= height * 0.61
        ):
            value = int(compact)
            if 1 <= value <= 20:
                return value, "rapid-countdown"
    raw = ocr_image(screenshot, (0.37, 0.35, 0.63, 0.60), directory / "latest_countdown_ocr.png", ocr_path, "0123456789", psm=10)
    values = re.findall(r"\d{1,2}", raw)
    value = int(values[-1]) if values else None
    if value is not None and 1 <= value <= 20:
        return value, raw
    if visual_countdown_present(screenshot):
        return 1, "visual-countdown (legacy OCR returned {!r})".format(raw)
    return None, raw


def draw_visible(screenshot):
    """Return true only when OCR finds the centre button's literal ``draw``."""
    with Image.open(screenshot) as image:
        width, height = image.size
    for box, text, _score in rapid_ocr_items(screenshot):
        center_x = sum(point[0] for point in box) / len(box)
        center_y = sum(point[1] for point in box) / len(box)
        if (
            text.strip().casefold() == "draw"
            and width * 0.40 <= center_x <= width * 0.60
            and height * 0.43 <= center_y <= height * 0.61
        ):
            return True
    return False


def bet_closed_tip_visible(screenshot):
    """Return true for the game's explicit 'Game can not bet now' dialog."""
    text = " ".join(item[1] for item in rapid_ocr_items(screenshot))
    normalized = re.sub(r"\s+", " ", text).casefold()
    return "game can not bet now" in normalized


def wait_for_draw_frame(client, directory, timeout, poll_interval, sleep, monotonic,
                        fallback_image):
    """After a closure tip, obtain the first ``draw`` frame of the same round.

    The tip can arrive before the UI flushes the final accepted wager.  No more
    taps are sent here; ``draw`` remains visible for the current round's draw
    phase, so it is the correct accounting frame.
    """
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        image, _ = capture(client, directory, "latest_draw_frame.png")
        if draw_visible(image):
            return image
        sleep(poll_interval)
    return fallback_image


def read_win_payout(screenshot, directory, ocr_path, templates, amount_box):
    """Return (payout, OCR text); payout is None when no amount is recognised.

    RapidOCR is tried first because it recognises the game's stylised modal
    reliably. Tesseract 3.02 and the local templates remain fallbacks.
    """
    result = rapid_ocr_items(screenshot)
    raw = " ".join(item[1] for item in result)
    match = re.search(r"you\s*win\D{0,24}([\d,]+)", raw, re.I)
    if match:
        return int(match.group(1).replace(",", "")), raw
    legacy_raw = ocr_image(
        screenshot, (0.12, 0.40, 0.88, 0.56), directory / "latest_win_ocr.png",
        ocr_path, "YouwinYOUWIN0123456789, :", psm=11
    )
    match = re.search(r"you\s*win\D{0,24}([\d,]+)", legacy_raw, re.I)
    if match:
        return int(match.group(1).replace(",", "")), legacy_raw
    if templates:
        with Image.open(screenshot) as image:
            width, height = image.size
            left, top, right, bottom = amount_box
            crop = image.crop((int(width * left), int(height * top),
                               int(width * right), int(height * bottom)))
        amount, local_raw = recognise_amount(crop, templates)
        if amount is not None:
            return amount, "local_ocr:" + local_raw
    return None, raw


def rapid_ocr_items(screenshot):
    """Return ``[(box, text, score), ...]`` from the robust local OCR engine."""
    global _RAPID_OCR
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise ValueError(
            "缺少 rapidocr-onnxruntime；请重新执行 python -m pip install -e ."
        ) from exc
    if _RAPID_OCR is None:
        _RAPID_OCR = RapidOCR()
    result, _ = _RAPID_OCR(str(screenshot))
    return result or []


def read_card_stakes(screenshot):
    """Read actual per-animal stake labels shown in the six bottom cards.

    Amounts are mapped by OCR bounding-box position, not by the number of ADB
    taps. This makes the calculation accurate when a last-second tap is
    rejected by the game server.
    """
    with Image.open(screenshot) as image:
        width, height = image.size
    stakes = {animal: 0 for animal in CARD_BOXES}
    recognised = []
    for box, text, score in rapid_ocr_items(screenshot):
        normalized = text.replace(" ", "")
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", normalized):
            continue
        center_x = sum(point[0] for point in box) / len(box)
        center_y = sum(point[1] for point in box) / len(box)
        for animal, (left, top, right, bottom) in CARD_BOXES.items():
            left, right = left * width / REFERENCE_SIZE[0], right * width / REFERENCE_SIZE[0]
            top, bottom = top * height / REFERENCE_SIZE[1], bottom * height / REFERENCE_SIZE[1]
            # Multipliers sit in the upper-right; actual stakes are in the
            # lower half of each card.
            if left <= center_x <= right and top + (bottom - top) * 0.35 <= center_y <= bottom:
                amount = int(normalized.replace(",", ""))
                # This test always selects the 50,000 chip. Ignore OCR noise
                # such as a stray "1" rather than treating it as a wager.
                if amount and amount % 50000:
                    break
                stakes[animal] = amount
                recognised.append((animal, normalized, round(float(score), 3)))
                break
    return stakes, recognised


def expected_payouts(stakes):
    return {animal: stake * ANIMALS[animal][1] for animal, stake in stakes.items()}


def matching_winner(payout, expected):
    matches = [animal for animal, amount in expected.items() if abs(payout - amount) < 0.01]
    return matches[0] if len(matches) == 1 else None


def highlighted_winner(screenshot):
    """Read the drawn animal from the result dialog's highlighted card frame."""
    with Image.open(screenshot) as image:
        image = image.convert("RGB")
        width, height = image.size
        scores = {}
        for animal, (left, top, right, bottom) in CARD_BOXES.items():
            left, right = round(left * width / REFERENCE_SIZE[0]), round(right * width / REFERENCE_SIZE[0])
            top, bottom = round(top * height / REFERENCE_SIZE[1]), round(bottom * height / REFERENCE_SIZE[1])
            samples = []
            for x in range(left, right):
                samples.extend((image.getpixel((x, top)), image.getpixel((x, bottom - 1))))
            for y in range(top, bottom):
                samples.extend((image.getpixel((left, y)), image.getpixel((right - 1, y))))
            scores[animal] = sum(sum(pixel) / 3 for pixel in samples) / len(samples)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if ordered[0][1] >= 55 and ordered[0][1] > ordered[1][1] * 1.25:
        return ordered[0][0], scores
    return None, scores


def wait_for_countdown(client, directory, ocr_path, timeout, poll_interval, sleep, monotonic,
                       min_betting_seconds=MINIMUM_BETTING_SECONDS):
    """Wait until a real game countdown is visible, avoiding blind time alignment."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        image, _ = capture(client, directory, "latest_phase.png")
        value, raw = read_countdown(image, directory, ocr_path)
        if value is not None and not raw.startswith("visual-countdown"):
            if value >= min_betting_seconds:
                print("检测到投注倒计时：{} 秒，开始本轮投注。".format(value))
                return value
            print("检测到倒计时仅剩 {} 秒，跳过本期并等待下一轮投注开始。".format(value))
        sleep(poll_interval)
    raise ValueError("{} 秒内未能从转盘中心识别到 1–20 倒计时；请检查 artifacts/lucky_wheel/latest_countdown_ocr.png".format(timeout))


def bet_one_cycle(client, chip_point, points, stakes, tap_pause, sleep):
    """Place one 50,000-chip pattern and accumulate amounts actually tapped."""
    client.tap(*chip_point)
    sleep(tap_pause)
    for animal in DEFAULT_ANIMALS:
        for _ in range(REPEATS_PER_CYCLE[animal]):
            client.tap(*points[animal])
            stakes[animal] += 50000
            sleep(tap_pause)


def wait_for_result(client, directory, ocr_path, templates, amount_box, timeout, poll_interval, sleep, monotonic):
    """Poll the 10-second draw; preserve the last modal frame for diagnosis.

    Returns ``(payout, raw, screenshot, winner)``. The screenshot is the
    last frame in which the win modal was visible — even when OCR fails —
    so callers always have the modal's own pixels rather than the next
    round's countdown captured after the modal has gone.
    """
    deadline = monotonic() + timeout
    last_raw, displayed_winner, modal_image = "", None, None
    while monotonic() < deadline:
        image, _ = capture(client, directory, "latest_result.png")
        winner, _ = highlighted_winner(image)
        if winner:
            displayed_winner = winner
            modal_image = image
        payout, raw = read_win_payout(image, directory, ocr_path, templates, amount_box)
        if payout is not None:
            return payout, raw, image, winner or displayed_winner
        last_raw = raw
        # Keep retrying for the entire configured result window.  The result
        # overlay may still be animating or a frame can be blurred when its
        # animal highlight has already appeared but its amount is unreadable.
        sleep(poll_interval)
    return None, last_raw, modal_image or image, displayed_winner


def run_rounds(client, iterations=0, tap_pause=0.06, poll_interval=0.35,
               phase_timeout=60.0, result_timeout=15.0, dry_run=False,
               output_dir=Path("artifacts/lucky_wheel"),
               log_file=Path("artifacts/lucky_wheel/rounds.jsonl"),
               ocr_path=DEFAULT_OCR_PATH,
               template_dir=None, amount_box=DEFAULT_AMOUNT_BOX,
               sleep=time.sleep, monotonic=time.monotonic):
    if iterations < 0 or min(tap_pause, poll_interval, phase_timeout, result_timeout) <= 0:
        raise ValueError("时间参数和 iterations 必须有效")
    if not Path(ocr_path).is_file():
        raise ValueError("找不到 Tesseract：{}".format(ocr_path))
    output_dir, log_file = Path(output_dir), Path(log_file)
    templates = load_templates(template_dir) if template_dir else load_templates()
    _, size = capture(client, output_dir, "initial.png")
    chip_point = scaled(CHIPS[50000], size)
    points = {animal: scaled(ANIMALS[animal][0], size) for animal in DEFAULT_ANIMALS}
    index = 0
    while iterations == 0 or index < iterations:
        index += 1
        wait_for_countdown(client, output_dir, ocr_path, phase_timeout, poll_interval, sleep, monotonic)
        stakes = {animal: 0 for animal in DEFAULT_ANIMALS}
        cycles = 0
        betting_closed_by_tip = False
        betting_window_closed_logged = False
        while True:
            phase_image, _ = capture(client, output_dir, "latest_phase.png")
            # The only authoritative betting-close signal is the centre button
            # changing to the literal word "draw". A transient countdown OCR
            # miss must not freeze amounts early.
            if draw_visible(phase_image):
                print("检测到 draw：第 {} 轮停止投注，开始读取动物实际投注金额。".format(index))
                break
            if bet_closed_tip_visible(phase_image):
                # A final tap can race the server-side cutoff. The dialog is
                # authoritative: freeze this round immediately. Do not tap
                # outside it — the host game treats that as closing the game.
                betting_closed_by_tip = True
                print("检测到投注关闭提示：第 {} 轮停止投注，开始读取动物实际投注金额。".format(index))
                break
            countdown, _ = read_countdown(phase_image, output_dir, ocr_path)
            if countdown is None:
                sleep(poll_interval)
                continue
            if countdown <= 8:
                if not betting_window_closed_logged:
                    print("第 {} 轮倒计时剩 {} 秒，停止新投注并等待 draw。".format(index, countdown))
                    betting_window_closed_logged = True
                sleep(poll_interval)
                continue
            if not dry_run:
                bet_one_cycle(client, chip_point, points, stakes, tap_pause, sleep)
            cycles += 1
            if cycles == 1 or cycles % 5 == 0:
                print(
                    "第 {} 轮投注进行中：已完成 {} 轮下注，等待转盘中心出现 draw。".format(
                        index, cycles
                    )
                )
        tapped_stakes = stakes
        if betting_closed_by_tip:
            print("等待同一轮出现 draw 后读取最终投注金额。")
            phase_image = wait_for_draw_frame(
                client, output_dir, timeout=8.0, poll_interval=poll_interval,
                sleep=sleep, monotonic=monotonic, fallback_image=phase_image,
            )
        # ``phase_image`` is the exact frame that first proved betting closed
        # (centre draw, or the first draw after the server's closure dialog).
        card_stakes, card_ocr = read_card_stakes(phase_image)
        late_image = output_dir / "round_{:04d}_bet_end.png".format(index)
        preserve_modal_screenshot(phase_image, late_image)
        stakes = {animal: card_stakes[animal] for animal in DEFAULT_ANIMALS}
        total = sum(stakes.values())
        if not card_ocr:
            raise ValueError("投注结束后未能从动物卡片读取任何实际投注金额")
        expected = expected_payouts(stakes)
        log(log_file, round=index, phase="bet_end", cycles=cycles,
            betting_closed_by_tip=betting_closed_by_tip, total_bet=total,
            stakes=stakes, tapped_stakes=tapped_stakes, card_ocr=card_ocr,
            expected_payouts=expected, screenshot=str(late_image))
        print("第 {} 轮投注结束：{} 次循环，实际总投注={}".format(index, cycles, total))
        print("第 {} 轮实际投注明细：{}".format(index, stakes))
        payout, raw, result_image, displayed_winner = wait_for_result(
            client, output_dir, ocr_path, templates, amount_box,
            result_timeout, poll_interval, sleep, monotonic)
        if payout is None:
            if displayed_winner and stakes.get(displayed_winner, 0) == 0:
                log(log_file, round=index, phase="result", status="no_win", actual_payout=0,
                    winning_animal=displayed_winner, ocr=raw, screenshot=str(result_image))
                print("第 {} 轮开奖结果：中奖动物={}，该动物未投注，中奖金额=0。".format(index, displayed_winner))
                continue
            if displayed_winner is None:
                log(log_file, round=index, phase="result", status="result_not_detected",
                    actual_payout=None, winning_animal=None, ocr=raw,
                    screenshot=str(result_image))
                print("第 {} 轮开奖期内未识别到结果弹窗，已记录并继续监听下一轮。".format(index))
                continue
            failure = output_dir / "FAIL_round_{:04d}_ocr.png".format(index)
            preserve_modal_screenshot(result_image, failure)
            log(log_file, round=index, phase="result", status="ocr_failed", actual_payout=None,
                winning_animal=displayed_winner, ocr=raw, screenshot=str(failure))
            print("第 {} 轮检测到中奖动物={}，但开奖期内未 OCR 到中奖金额；已保留弹窗截图：{}。".format(
                index, displayed_winner or "unknown", failure))
            return False
        winner = displayed_winner or matching_winner(payout, expected)
        status = "matched" if winner else "mismatch"
        log(log_file, round=index, phase="result", status=status, actual_payout=payout,
            winning_animal=winner, ocr=raw, screenshot=str(result_image))
        print(
            "第 {} 轮开奖结果：中奖动物={}，中奖金额={}".format(
                index, winner or "无法按预期金额唯一识别", payout
            )
        )
        if winner:
            print("第 {} 轮通过：中奖动物={}，中奖金额={}".format(index, winner, payout))
            continue
        failure = output_dir / "FAIL_round_{:04d}.png".format(index)
        preserve_modal_screenshot(result_image, failure)
        print("第 {} 轮不一致，已保留弹窗截图：{}；OCR={!r}".format(index, failure, raw))
        return False
    return True


def preserve_modal_screenshot(source_path, destination_path):
    """Copy the preserved modal frame into the failure-screenshot path.

    We deliberately do not call ``client.screenshot`` here: by the time the
    caller has decided the round failed, the win modal has usually gone,
    so a fresh capture would land on the next round's countdown instead.
    """
    destination_path = Path(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path is None:
        return
    source_path = Path(source_path)
    if not source_path.is_file():
        return
    with Image.open(source_path) as src:
        src.save(destination_path)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="ADB serial；仅有一台已授权设备时可省略")
    parser.add_argument("--iterations", type=int, default=0, help="0 表示一直运行直到不一致")
    parser.add_argument("--tap-pause", type=float, default=0.06)
    parser.add_argument("--poll-interval", type=float, default=0.35)
    parser.add_argument("--phase-timeout", type=float, default=60.0)
    parser.add_argument("--result-timeout", type=float, default=15.0,
                        help="draw 出现后等待并识别结果弹窗的秒数（默认 15）")
    parser.add_argument("--ocr-path", type=Path, default=DEFAULT_OCR_PATH)
    parser.add_argument("--template-dir", type=Path, default=None,
                        help="数字模板目录；默认 examples/lucky_wheel_templates/")
    parser.add_argument("--amount-box", type=float, nargs=4,
                        default=list(DEFAULT_AMOUNT_BOX),
                        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                        help="中奖金额数字带在截图中的相对坐标 0..1")
    parser.add_argument("--dry-run", action="store_true", help="不下注，只验证倒计时识别")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/lucky_wheel"))
    parser.add_argument("--log-file", type=Path, default=Path("artifacts/lucky_wheel/rounds.jsonl"))
    return parser


def select_client(serial):
    if serial:
        return AdbClient(serial=serial)
    probe = AdbClient()
    devices = [item for item in probe.devices() if item.state == "device"]
    if len(devices) != 1:
        raise ValueError("需要 --serial：已授权设备数量为 {}".format(len(devices)))
    print("自动选择设备：{}".format(devices[0].serial))
    return AdbClient(serial=devices[0].serial)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        client = select_client(args.serial)
        options = vars(args).copy()
        options.pop("serial")
        return 0 if run_rounds(client, **options) else 2
    except (AdbError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("执行失败：{}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
