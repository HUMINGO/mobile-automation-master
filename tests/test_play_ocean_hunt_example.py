import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


EXAMPLE = Path(__file__).parents[1] / "examples" / "Play_ocean_hunt.py"
SPEC = importlib.util.spec_from_file_location("Play_ocean_hunt", str(EXAMPLE))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeClient:
    def __init__(self):
        self.taps = []

    def tap(self, x, y):
        self.taps.append((x, y))


class ScreenshotClient:
    def __init__(self, source):
        self.source = source

    def screenshot(self, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.source.read_bytes())
        return target


def test_cli_defaults_match_calibrated_device_and_spin_position():
    args = MODULE.build_parser().parse_args([])

    assert args.serial == "QWV8XSU4FEOZ8D9H"
    assert (args.x, args.y) == (622, 1384)
    assert (args.collect_x, args.collect_y) == (341, 1239)
    assert args.collect_delay == 0.2
    assert (args.min_delay, args.max_delay) == (1.0, 3.0)
    assert args.iterations == 0
    assert args.log_file == Path("artifacts/play_ocean_hunt/balance.log")
    assert args.output_dir == Path("artifacts/play_ocean_hunt")
    assert args.ocr_path == Path("/opt/homebrew/bin/tesseract")


def test_finite_loop_clicks_spin_and_uses_random_delays_in_range():
    client = FakeClient()
    random_calls = []
    waits = []

    def random_delay(minimum, maximum):
        random_calls.append((minimum, maximum))
        return 2.0

    count = MODULE.run_spin_loop(
        client,
        iterations=3,
        random_delay=random_delay,
        sleep=waits.append,
    )

    assert count == 3
    assert client.taps == [
        (341, 1239),
        (622, 1384),
        (341, 1239),
        (622, 1384),
        (341, 1239),
        (622, 1384),
    ]
    assert random_calls == [(1.0, 3.0)] * 3
    assert waits == [0.2, 2.0, 0.2, 2.0, 0.2, 2.0]


def test_loop_accepts_coordinate_and_delay_overrides():
    client = FakeClient()
    waits = []

    count = MODULE.run_spin_loop(
        client,
        x=100,
        y=200,
        collect_x=300,
        collect_y=400,
        collect_delay=0.1,
        min_delay=0.25,
        max_delay=0.75,
        iterations=2,
        random_delay=lambda minimum, maximum: maximum,
        sleep=waits.append,
    )

    assert count == 2
    assert client.taps == [(300, 400), (100, 200), (300, 400), (100, 200)]
    assert waits == [0.1, 0.75, 0.1, 0.75]


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"x": -1}, "点击坐标不能为负数"),
        ({"y": -1}, "点击坐标不能为负数"),
        ({"collect_x": -1}, "点击坐标不能为负数"),
        ({"collect_y": -1}, "点击坐标不能为负数"),
        ({"collect_delay": -1}, "--collect-delay 不能为负数"),
        ({"min_delay": 0}, "--min-delay 必须大于 0"),
        (
            {"min_delay": 3, "max_delay": 1},
            "--max-delay 不能小于 --min-delay",
        ),
        ({"iterations": -1}, "--iterations 不能为负数"),
    ],
)
def test_invalid_settings_are_rejected(kwargs, message):
    settings = {
        "x": 622,
        "y": 1384,
        "collect_x": 341,
        "collect_y": 1239,
        "collect_delay": 0.2,
        "min_delay": 1,
        "max_delay": 3,
        "iterations": 0,
    }
    settings.update(kwargs)

    with pytest.raises(ValueError, match=message):
        MODULE.validate_settings(**settings)


def test_keyboard_interrupt_stops_safely_and_returns_click_count():
    client = FakeClient()
    sleep_calls = []

    def interrupt_after_first_spin(delay):
        sleep_calls.append(delay)
        if len(sleep_calls) == 2:
            raise KeyboardInterrupt

    count = MODULE.run_spin_loop(client, sleep=interrupt_after_first_spin)

    assert count == 1
    assert client.taps == [(341, 1239), (622, 1384)]


def test_loop_prints_and_records_balance_after_every_spin(capsys):
    client = FakeClient()
    readings = iter([(84049, "84,049"), (83969, "83,969")])
    records = []

    count = MODULE.run_spin_loop(
        client,
        iterations=2,
        random_delay=lambda minimum, maximum: 1.0,
        sleep=lambda delay: None,
        balance_reader=lambda: next(readings),
        balance_recorder=lambda *values: records.append(values),
    )

    assert count == 2
    assert records == [
        (1, 84049, "84,049", ""),
        (2, 83969, "83,969", ""),
    ]
    output = capsys.readouterr().out
    assert "第 1 次，余额=84,049" in output
    assert "第 2 次，余额=83,969" in output


def test_loop_logs_ocr_error_and_continues(capsys):
    client = FakeClient()
    attempts = 0
    records = []

    def fail_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MODULE.BalanceReadError("看不清")
        return 83949, "83,949"

    count = MODULE.run_spin_loop(
        client,
        iterations=2,
        random_delay=lambda minimum, maximum: 1.0,
        sleep=lambda delay: None,
        balance_reader=fail_once,
        balance_recorder=lambda *values: records.append(values),
    )

    assert count == 2
    assert records == [
        (1, None, "", "看不清"),
        (2, 83949, "83,949", ""),
    ]
    assert "余额识别失败：第 1 次，看不清" in capsys.readouterr().err


def test_read_balance_crops_screen_and_parses_ocr(tmp_path):
    source = tmp_path / "screen.png"
    MODULE.Image.new("RGB", (720, 1640), "black").save(source)
    calls = []

    def run_command(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="84,049\n", stderr="")

    balance, raw_ocr = MODULE.read_balance(
        ScreenshotClient(source),
        output_dir=tmp_path / "output",
        ocr_path=Path("/fake/tesseract"),
        run_command=run_command,
    )

    assert balance == 84049
    assert raw_ocr == "84,049"
    assert (tmp_path / "output" / "latest_screen.png").exists()
    assert (tmp_path / "output" / "latest_balance.png").exists()
    assert calls[0][0][-4:] == [
        "--psm",
        "7",
        "-c",
        "tessedit_char_whitelist=0123456789,",
    ]
    assert "TESSDATA_PREFIX" not in calls[0][1]["env"]


def test_append_balance_log_writes_json_lines(tmp_path):
    log_path = tmp_path / "balance.log"

    MODULE.append_balance_log(log_path, 1, balance=84049, raw_ocr="84,049")
    MODULE.append_balance_log(log_path, 2, error="看不清")

    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["iteration"] == 1
    assert records[0]["balance"] == 84049
    assert records[0]["status"] == "ok"
    assert records[1]["iteration"] == 2
    assert records[1]["balance"] is None
    assert records[1]["status"] == "ocr_error"


def test_main_reports_adb_failure(monkeypatch, capsys):
    def fail_to_connect(serial):
        raise MODULE.AdbError("device offline")

    monkeypatch.setattr(MODULE, "AdbClient", fail_to_connect)

    assert MODULE.main(["--iterations", "1"]) == 1
    assert "执行失败：device offline" in capsys.readouterr().err
