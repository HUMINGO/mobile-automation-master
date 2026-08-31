"""Automatic test player for the Crash Star rocket game.

Open the game's page on an authorised Android test account before starting this
script.  The script observes the screen instead of estimating the round from
wall-clock time: it bets only while OCR sees ``starts in 1..14 second`` and
waits for the rocket/result phase before starting another round.

By default it submits one bet per round and lets the rocket explode.  Pass
``--cash-out-at`` to test the optional TAKE interaction at a chosen multiplier.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import random
import re
import sys
import time

from PIL import Image
from mobile_automation import AdbClient, AdbError


REFERENCE_SIZE = (480, 1068)
CHIPS = {100: (155, 950), 1000: (210, 950), 5000: (267, 950), 50000: (320, 950)}
TAKE_BUTTON = (240, 955)
# Safe inner area of the blue "Please Betting" panel.  The outer panel is
# intentionally avoided because it contains decorative controls and labels.
BET_AREA = (185, 700, 395, 810)
BETTING_WORDS = ("please betting", "starts in")
RESULT_WORDS = ("you win", "you lose", "game over", "round result")
_OCR = None


def scaled(point, screen_size):
    """Convert the coordinates calibrated from the supplied 480x1068 video."""
    return (
        round(point[0] * screen_size[0] / REFERENCE_SIZE[0]),
        round(point[1] * screen_size[1] / REFERENCE_SIZE[1]),
    )


def random_bet_points(count, screen_size, rng=random):
    """Pick distinct-looking positions inside the calibrated betting panel."""
    left, top, right, bottom = BET_AREA
    return [
        scaled((rng.randint(left, right), rng.randint(top, bottom)), screen_size)
        for _ in range(count)
    ]


def capture(client, output_dir, name):
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / name
    client.screenshot(path)
    with Image.open(path) as image:
        return path, image.size


def ocr_items(image_path):
    """Return ``(text, centre_x_ratio, centre_y_ratio)`` OCR items."""
    global _OCR
    if _OCR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise RuntimeError(
                "缺少 rapidocr-onnxruntime；请执行 python -m pip install -e ."
            ) from exc
        _OCR = RapidOCR()
    result, _ = _OCR(str(image_path))
    with Image.open(image_path) as image:
        width, height = image.size
    values = []
    for box, text, _score in result or []:
        centre_x = sum(point[0] for point in box) / (4 * width)
        centre_y = sum(point[1] for point in box) / (4 * height)
        values.append((text.strip(), centre_x, centre_y))
    return values


def countdown_from_items(items):
    """Read the 1..14 second betting countdown from the middle game panel."""
    relevant = [
        text for text, x, y in items
        if 0.30 <= x <= 0.90 and 0.55 <= y <= 0.82
    ]
    text = " ".join(relevant).casefold()
    match = re.search(r"starts\s+in\s*(\d{1,2})\s*second", text)
    if match:
        value = int(match.group(1))
        return value if 1 <= value <= 14 else None
    return None


def flight_multiplier_from_items(items):
    """Read the centre multiplier only during the rocket flight phase."""
    for text, x, y in items:
        normalized = text.replace(" ", "").replace(",", ".").casefold()
        if 0.25 <= x <= 0.88 and 0.58 <= y <= 0.88:
            match = re.fullmatch(r"(\d+(?:\.\d+)?)x", normalized)
            if match:
                return float(match.group(1))
    return None


def state_from_items(items):
    text = " ".join(item[0] for item in items).casefold()
    countdown = countdown_from_items(items)
    if countdown is not None:
        return "betting", countdown
    if any(word in text for word in RESULT_WORDS):
        return "result", flight_multiplier_from_items(items)
    multiplier = flight_multiplier_from_items(items)
    if multiplier is not None or "take" in text:
        return "flying", multiplier
    return "unknown", None


def append_log(path, **entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    entry["time"] = datetime.now().astimezone().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def play(client, chip, cash_out_at, iterations, output_dir, poll_interval,
         min_bets, max_bets, rng=random):
    """Run rounds until ``iterations`` is reached; zero means indefinitely."""
    round_number = 0
    in_round = False
    took = False
    last_state = None
    screen_size = None
    log_file = output_dir / "rounds.jsonl"

    while iterations == 0 or round_number < iterations:
        screenshot, screen_size = capture(client, output_dir, "latest.png")
        items = ocr_items(screenshot)
        state, value = state_from_items(items)

        if state != last_state:
            print("检测到游戏状态：{}{}".format(
                state, "（{} 秒）".format(value) if state == "betting" else ""
            ))
            last_state = state

        if state == "betting" and not in_round:
            round_number += 1
            # Choose the stake first, then tap the actual central betting area.
            chip_point = scaled(CHIPS[chip], screen_size)
            client.tap(*chip_point)
            bet_count = rng.randint(min_bets, max_bets)
            bet_points = random_bet_points(bet_count, screen_size, rng)
            for point in bet_points:
                client.tap(*point)
                time.sleep(rng.uniform(0.08, 0.18))
            in_round, took = True, False
            print("第 {} 轮：检测到投注倒计时 {} 秒，已选择筹码 {}，在投注区随机点击 {} 次。".format(
                round_number, value, chip, bet_count
            ))
            append_log(log_file, round=round_number, event="bet", chip=chip,
                       countdown=value, chip_coordinate=list(chip_point),
                       bet_count=bet_count, bet_coordinates=[list(point) for point in bet_points])

        elif state == "flying" and in_round and cash_out_at and not took:
            if value is not None and value >= cash_out_at:
                point = scaled(TAKE_BUTTON, screen_size)
                client.tap(*point)
                took = True
                print("第 {} 轮：倍率 {:.2f}x，已点击 TAKE。".format(round_number, value))
                append_log(log_file, round=round_number, event="take",
                           multiplier=value, coordinate=list(point))

        elif state == "result" and in_round:
            result_path = output_dir / "round_{:04d}_result.png".format(round_number)
            screenshot.replace(result_path)
            print("第 {} 轮：检测到火箭结果弹窗。".format(round_number))
            append_log(log_file, round=round_number, event="result",
                       multiplier=value, screenshot=str(result_path), took=took)
            in_round = False

        # Some versions dismiss the result pop-up very quickly.  Treat the
        # next betting countdown as the definitive round boundary as well.
        elif state == "betting" and in_round:
            print("第 {} 轮：未捕获到结果弹窗，已进入下一轮投注期。".format(round_number))
            append_log(log_file, round=round_number, event="next_betting_seen",
                       took=took)
            in_round = False

        time.sleep(poll_interval)


def build_parser():
    parser = argparse.ArgumentParser(description="Crash Star 火箭游戏自动测试")
    parser.add_argument("--serial", help="Android 设备序列号；唯一设备时可省略")
    parser.add_argument("--chip", type=int, choices=sorted(CHIPS), default=100,
                        help="每轮下注筹码，默认 100")
    parser.add_argument("--cash-out-at", type=float, default=0,
                        help="达到该倍率后点击 TAKE；0 表示不提前取出，等待爆炸")
    parser.add_argument("--min-bets", type=int, default=2,
                        help="每轮随机点击投注区的最少次数，默认 2")
    parser.add_argument("--max-bets", type=int, default=10,
                        help="每轮随机点击投注区的最多次数，默认 10")
    parser.add_argument("--iterations", type=int, default=0,
                        help="运行轮数；0 表示持续运行")
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/crash_star"))
    return parser


def select_client(serial):
    client = AdbClient(serial=serial)
    if serial:
        return client
    devices = [device for device in client.devices() if device.state == "device"]
    if len(devices) != 1:
        raise ValueError("请用 --serial 指定设备；当前可用设备数为 {}".format(len(devices)))
    print("自动选择设备：{}".format(devices[0].serial))
    return AdbClient(serial=devices[0].serial)


def main(argv=None):
    args = build_parser().parse_args(argv)
    if (args.iterations < 0 or args.poll_interval <= 0 or args.cash_out_at < 0
            or args.min_bets < 1 or args.max_bets < args.min_bets or args.max_bets > 10):
        raise SystemExit("参数无效：每轮投注点击次数必须在 1 到 10 之间")
    try:
        play(select_client(args.serial), args.chip, args.cash_out_at,
             args.iterations, args.output_dir, args.poll_interval,
             args.min_bets, args.max_bets)
    except (AdbError, OSError, RuntimeError, ValueError) as exc:
        print("执行失败：{}".format(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
