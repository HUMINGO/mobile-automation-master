"""Continuously click the SPIN button in the Play Ocean Hunt game."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time

from mobile_automation import AdbClient, AdbError
from PIL import Image, UnidentifiedImageError


DEFAULT_SERIAL = "QWV8XSU4FEOZ8D9H"
DEFAULT_X = 622
DEFAULT_Y = 1384
DEFAULT_COLLECT_X = 341
DEFAULT_COLLECT_Y = 1239
DEFAULT_COLLECT_DELAY = 0.2
DEFAULT_MIN_DELAY = 1.0
DEFAULT_MAX_DELAY = 3.0
DEFAULT_LOG_FILE = Path("artifacts/play_ocean_hunt/balance.log")
DEFAULT_OUTPUT_DIR = Path("artifacts/play_ocean_hunt")
DEFAULT_OCR_PATH = Path("/opt/homebrew/bin/tesseract")
EXPECTED_SCREEN_SIZE = (720, 1640)
BALANCE_BOX = (108, 810, 198, 852)
BALANCE_SCALE = 8


class BalanceReadError(RuntimeError):
    """Raised when the current game balance cannot be recognized."""


def validate_settings(
    x,
    y,
    collect_x,
    collect_y,
    collect_delay,
    min_delay,
    max_delay,
    iterations,
):
    """Validate click coordinates, delay bounds, and the iteration limit."""
    if x < 0 or y < 0 or collect_x < 0 or collect_y < 0:
        raise ValueError("点击坐标不能为负数")
    if collect_delay < 0:
        raise ValueError("--collect-delay 不能为负数")
    if min_delay <= 0:
        raise ValueError("--min-delay 必须大于 0")
    if max_delay < min_delay:
        raise ValueError("--max-delay 不能小于 --min-delay")
    if iterations < 0:
        raise ValueError("--iterations 不能为负数；0 表示无限循环")


def read_balance(
    client,
    output_dir=DEFAULT_OUTPUT_DIR,
    ocr_path=DEFAULT_OCR_PATH,
    run_command=subprocess.run,
):
    """Capture the screen, crop the balance, and return ``(value, raw_text)``."""
    output_dir = Path(output_dir)
    screenshot_path = output_dir / "latest_screen.png"
    balance_path = output_dir / "latest_balance.png"
    client.screenshot(screenshot_path)

    try:
        with Image.open(screenshot_path) as screen:
            if screen.size != EXPECTED_SCREEN_SIZE:
                raise BalanceReadError(
                    "屏幕尺寸应为 {}x{}，实际为 {}x{}".format(
                        EXPECTED_SCREEN_SIZE[0],
                        EXPECTED_SCREEN_SIZE[1],
                        screen.width,
                        screen.height,
                    )
                )
            balance_image = screen.convert("RGB").crop(BALANCE_BOX)
            balance_image = balance_image.resize(
                (
                    balance_image.width * BALANCE_SCALE,
                    balance_image.height * BALANCE_SCALE,
                )
            )
            balance_image.save(balance_path)
    except (OSError, UnidentifiedImageError) as exc:
        raise BalanceReadError("余额截图处理失败：{}".format(exc)) from exc

    environment = os.environ.copy()
    environment.pop("TESSDATA_PREFIX", None)
    try:
        result = run_command(
            [
                str(ocr_path),
                str(balance_path),
                "stdout",
                "--psm",
                "7",
                "-c",
                "tessedit_char_whitelist=0123456789,",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BalanceReadError("OCR 执行失败：{}".format(exc)) from exc

    if result.returncode != 0:
        message = result.stderr.strip() or "Tesseract 返回非零状态"
        raise BalanceReadError("OCR 执行失败：{}".format(message))

    raw_text = result.stdout.strip()
    normalized = re.sub(r"\s+", "", raw_text)
    if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)", normalized):
        raise BalanceReadError("无法解析 OCR 结果：{!r}".format(raw_text))
    return int(normalized.replace(",", "")), raw_text


def append_balance_log(
    log_path,
    iteration,
    balance=None,
    raw_ocr="",
    error="",
):
    """Append one durable JSON record and return its timestamp."""
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    record = {
        "timestamp": timestamp,
        "iteration": iteration,
        "balance": balance,
        "raw_ocr": raw_ocr,
        "status": "ok" if error == "" else "ocr_error",
        "error": error,
    }
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return timestamp


def run_spin_loop(
    client,
    x=DEFAULT_X,
    y=DEFAULT_Y,
    collect_x=DEFAULT_COLLECT_X,
    collect_y=DEFAULT_COLLECT_Y,
    collect_delay=DEFAULT_COLLECT_DELAY,
    min_delay=DEFAULT_MIN_DELAY,
    max_delay=DEFAULT_MAX_DELAY,
    iterations=0,
    random_delay=random.uniform,
    sleep=time.sleep,
    balance_reader=None,
    balance_recorder=None,
):
    """Click SPIN until the limit is reached or the user presses Ctrl+C."""
    validate_settings(
        x,
        y,
        collect_x,
        collect_y,
        collect_delay,
        min_delay,
        max_delay,
        iterations,
    )
    click_count = 0
    try:
        while iterations == 0 or click_count < iterations:
            client.tap(collect_x, collect_y)
            if collect_delay:
                sleep(collect_delay)

            client.tap(x, y)
            click_count += 1
            print("已点击 SPIN：第 {} 次，坐标=({}, {})".format(click_count, x, y))

            delay = random_delay(min_delay, max_delay)
            print("等待 {:.2f} 秒，让游戏结果和余额完成更新……".format(delay))
            sleep(delay)

            if balance_reader is not None:
                try:
                    balance, raw_ocr = balance_reader()
                    print(
                        "余额识别：第 {} 次，余额={:,}".format(
                            click_count,
                            balance,
                        )
                    )
                    if balance_recorder is not None:
                        balance_recorder(click_count, balance, raw_ocr, "")
                except BalanceReadError as exc:
                    print(
                        "余额识别失败：第 {} 次，{}".format(click_count, exc),
                        file=sys.stderr,
                    )
                    if balance_recorder is not None:
                        balance_recorder(click_count, None, "", str(exc))
    except KeyboardInterrupt:
        pass
    return click_count


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serial",
        default=DEFAULT_SERIAL,
        help="ADB 设备序列号",
    )
    parser.add_argument("--x", type=int, default=DEFAULT_X, help="SPIN 横坐标")
    parser.add_argument("--y", type=int, default=DEFAULT_Y, help="SPIN 纵坐标")
    parser.add_argument(
        "--collect-x",
        type=int,
        default=DEFAULT_COLLECT_X,
        help="Big Win 弹窗 COLLECT 横坐标",
    )
    parser.add_argument(
        "--collect-y",
        type=int,
        default=DEFAULT_COLLECT_Y,
        help="Big Win 弹窗 COLLECT 纵坐标",
    )
    parser.add_argument(
        "--collect-delay",
        type=float,
        default=DEFAULT_COLLECT_DELAY,
        help="点击 COLLECT 后等待 SPIN 的秒数",
    )
    parser.add_argument(
        "--min-delay",
        type=float,
        default=DEFAULT_MIN_DELAY,
        help="两次点击之间的最短等待秒数",
    )
    parser.add_argument(
        "--max-delay",
        type=float,
        default=DEFAULT_MAX_DELAY,
        help="两次点击之间的最长等待秒数",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="点击次数；0 表示持续运行直到按 Ctrl+C",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help="余额识别日志文件（逐行 JSON）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="最新全屏截图和余额裁剪图目录",
    )
    parser.add_argument(
        "--ocr-path",
        type=Path,
        default=DEFAULT_OCR_PATH,
        help="Tesseract 可执行文件路径",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        validate_settings(
            args.x,
            args.y,
            args.collect_x,
            args.collect_y,
            args.collect_delay,
            args.min_delay,
            args.max_delay,
            args.iterations,
        )
        client = AdbClient(serial=args.serial)
        if not args.ocr_path.is_file() or not os.access(args.ocr_path, os.X_OK):
            raise ValueError("找不到可执行的 Tesseract：{}".format(args.ocr_path))
        print(
            "开始 Play Ocean Hunt 自动点击：设备={}，COLLECT=({}, {})，"
            "SPIN=({}, {})，间隔={:.2f}-{:.2f} 秒".format(
                args.serial,
                args.collect_x,
                args.collect_y,
                args.x,
                args.y,
                args.min_delay,
                args.max_delay,
            )
        )
        print("请保持游戏页面位于前台；按 Ctrl+C 停止。")
        print("余额日志：{}".format(args.log_file.resolve()))

        def balance_reader():
            return read_balance(
                client,
                output_dir=args.output_dir,
                ocr_path=args.ocr_path,
            )

        def balance_recorder(iteration, balance, raw_ocr, error):
            append_balance_log(
                args.log_file,
                iteration,
                balance=balance,
                raw_ocr=raw_ocr,
                error=error,
            )

        click_count = run_spin_loop(
            client,
            x=args.x,
            y=args.y,
            collect_x=args.collect_x,
            collect_y=args.collect_y,
            collect_delay=args.collect_delay,
            min_delay=args.min_delay,
            max_delay=args.max_delay,
            iterations=args.iterations,
            balance_reader=balance_reader,
            balance_recorder=balance_recorder,
        )
        if args.iterations and click_count >= args.iterations:
            print("任务完成：共点击 {} 次".format(click_count))
        else:
            print("\n任务已停止：共点击 {} 次".format(click_count))
        return 0
    except (AdbError, OSError, ValueError) as exc:
        print("执行失败：{}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
