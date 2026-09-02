from pathlib import Path
from types import SimpleNamespace

from utils.html_report import generate_html_report


def test_html_report_includes_case_steps_and_screenshots(tmp_path: Path):
    log_path = tmp_path / "test_demo.log"
    log_path.write_text("步骤执行完成", encoding="utf-8")
    image_path = tmp_path / "steps" / "test_demo" / "001_click.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"not-a-real-image")
    result = SimpleNamespace(
        name="test_demo.py",
        status="passed",
        elapsed_seconds=1.25,
        return_code=0,
        error="",
        log_path=str(log_path),
        steps=[{
            "index": 1,
            "time": "2026-09-01 10:00:00",
            "action": "点击元素",
            "detail": "Me，坐标 (980, 2249)",
            "screenshot": "steps/test_demo/001_click.png",
        }],
    )

    report = generate_html_report(tmp_path, [result])
    content = report.read_text(encoding="utf-8")

    assert report.name == "report.html"
    assert "test_demo.py" in content
    assert "点击元素" in content
    assert 'src="steps/test_demo/001_click.png"' in content
    assert "步骤执行完成" in content
    assert "展开用例" in content
    assert 'class="case-body" hidden' in content
