import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


EXAMPLE = Path(__file__).parents[1] / "examples" / "Play_Jungle_hunt_boloup.py"
SPEC = importlib.util.spec_from_file_location("Play_Jungle_hunt_boloup", str(EXAMPLE))
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


def test_defaults_match_calibrated_jungle_hunt_screen():
    args = MODULE.build_parser().parse_args([])
    assert args.serial == "QWV8XSU4FEOZ8D9H"
    assert (args.x, args.y) == (631, 1433)
    assert (args.collect_x, args.collect_y) == (360, 1239)
    assert (args.min_delay, args.max_delay) == (1.0, 3.0)
    assert args.iterations == 0
    assert args.log_file == Path("artifacts/play_jungle_hunt_boloup/balance.log")
    assert args.output_dir == Path("artifacts/play_jungle_hunt_boloup")


def test_loop_clicks_fallback_then_spin_and_records_each_balance(capsys):
    client = FakeClient()
    readings = iter([(48501500, "48,501,500"), (48501400, "48,501,400")])
    records = []
    waits = []

    count = MODULE.run_spin_loop(
        client,
        iterations=2,
        random_delay=lambda minimum, maximum: 2.0,
        sleep=waits.append,
        balance_reader=lambda: next(readings),
        balance_recorder=lambda *values: records.append(values),
    )

    assert count == 2
    assert client.taps == [
        (360, 1239),
        (631, 1433),
        (360, 1239),
        (631, 1433),
    ]
    assert waits == [0.2, 2.0, 0.2, 2.0]
    assert records == [
        (1, 48501500, "48,501,500", ""),
        (2, 48501400, "48,501,400", ""),
    ]
    assert "余额=48,501,500" in capsys.readouterr().out


def test_ocr_failure_is_logged_without_stopping(capsys):
    client = FakeClient()
    records = []

    def fail():
        raise MODULE.BalanceReadError("无法识别")

    assert MODULE.run_spin_loop(
        client,
        iterations=1,
        random_delay=lambda minimum, maximum: 1.0,
        sleep=lambda delay: None,
        balance_reader=fail,
        balance_recorder=lambda *values: records.append(values),
    ) == 1
    assert records == [(1, None, "", "无法识别")]
    assert "余额识别失败" in capsys.readouterr().err


def test_read_balance_uses_jungle_crop_and_parses_number(tmp_path):
    source = tmp_path / "screen.png"
    MODULE.Image.new("RGB", (720, 1640), "black").save(source)
    calls = []

    def run_command(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="48,501,500\n", stderr="")

    value, raw = MODULE.read_balance(
        ScreenshotClient(source),
        output_dir=tmp_path / "output",
        ocr_path=Path("/fake/tesseract"),
        run_command=run_command,
    )

    assert value == 48501500
    assert raw == "48,501,500"
    with MODULE.Image.open(tmp_path / "output" / "latest_balance.png") as crop:
        assert crop.size == (1120, 180)
    assert calls[0][-4:] == [
        "--psm",
        "7",
        "-c",
        "tessedit_char_whitelist=0123456789,",
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"x": -1},
        {"collect_y": -1},
        {"collect_delay": -1},
        {"min_delay": 0},
        {"min_delay": 3, "max_delay": 1},
        {"iterations": -1},
    ],
)
def test_invalid_settings_are_rejected(kwargs):
    settings = {
        "x": 631,
        "y": 1433,
        "collect_x": 360,
        "collect_y": 1239,
        "collect_delay": 0.2,
        "min_delay": 1,
        "max_delay": 3,
        "iterations": 0,
    }
    settings.update(kwargs)
    with pytest.raises(ValueError):
        MODULE.validate_settings(**settings)


def test_balance_log_is_json_lines(tmp_path):
    log_path = tmp_path / "balance.log"
    MODULE.append_balance_log(log_path, 1, balance=48501500, raw_ocr="48,501,500")
    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["iteration"] == 1
    assert record["balance"] == 48501500
    assert record["status"] == "ok"
