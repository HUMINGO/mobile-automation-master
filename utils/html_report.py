"""Generate a standalone, local HTML report for Android UI test runs."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Iterable


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "日志不可用"


def _status_label(status: str) -> str:
    return {"passed": "通过", "failed": "失败", "timeout": "超时", "dry_run": "预览"}.get(status, status)


def generate_html_report(output_dir: Path, results: Iterable[Any]) -> Path:
    """Write ``report.html`` next to ``report.json`` and return its path."""
    output_dir = Path(output_dir)
    items = list(results)
    passed = sum(item.status == "passed" for item in items)
    failed = sum(item.status in {"failed", "timeout"} for item in items)
    case_sections = []
    for item in items:
        status = escape(item.status)
        steps = getattr(item, "steps", []) or []
        step_rows = []
        for step in steps:
            action = escape(str(step.get("action", "未命名操作")))
            detail = escape(str(step.get("detail", "")))
            timestamp = escape(str(step.get("time", "")))
            image = step.get("screenshot")
            image_html = ""
            if image:
                url = escape(str(image), quote=True)
                image_html = (
                    '<a href="{0}" target="_blank"><img src="{0}" loading="lazy" '
                    'alt="{1} 的截图"></a>'
                ).format(url, action)
            elif step.get("screenshot_error"):
                image_html = '<span class="image-error">截图采集失败：{}</span>'.format(
                    escape(str(step["screenshot_error"]))
                )
            else:
                image_html = '<span class="muted">无截图</span>'
            step_rows.append(
                "<tr><td>{}</td><td>{}</td><td><strong>{}</strong><br><span>{}</span></td><td>{}</td></tr>".format(
                    escape(str(step.get("index", "-"))), timestamp, action, detail, image_html
                )
            )
        if not step_rows:
            step_rows.append('<tr><td colspan="4" class="muted">该用例未记录步骤事件。请使用项目的 UiTree、utils 公共操作方法运行用例。</td></tr>')
        log_path = Path(item.log_path)
        log = escape(_read_text(log_path))
        error = '<p class="case-error">{}</p>'.format(escape(item.error)) if item.error else ""
        case_sections.append(
            """<section class="case {status}">
<div class="case-title"><h2>{name}</h2><span class="badge {status}">{label}</span></div>
<p class="meta">耗时：{elapsed:.2f} 秒　退出码：{return_code}</p>{error}
<h3>操作步骤与截图（{step_count}）</h3>
<div class="table-wrap"><table><thead><tr><th>#</th><th>时间</th><th>操作</th><th>截图</th></tr></thead><tbody>{steps}</tbody></table></div>
<details><summary>查看原始执行日志</summary><pre>{log}</pre></details>
</section>""".format(
                status=status,
                name=escape(item.name),
                label=_status_label(item.status),
                elapsed=item.elapsed_seconds,
                return_code="-" if item.return_code is None else item.return_code,
                error=error,
                step_count=len(steps),
                steps="\n".join(step_rows),
                log=log,
            )
        )

    document = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Android UI 自动化测试报告</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#f4f6fa;color:#172033;font-family:"Microsoft YaHei",Arial,sans-serif}}
header{{padding:30px max(24px,calc((100vw - 1260px)/2));background:#13295b;color:#fff}} h1{{margin:0 0 16px;font-size:28px}}
.summary{{display:flex;gap:12px;flex-wrap:wrap}} .summary span{{background:#ffffff1d;padding:9px 14px;border-radius:8px}}
main{{max-width:1260px;margin:24px auto;padding:0 20px}} .case{{background:#fff;border-radius:12px;padding:22px;margin-bottom:20px;box-shadow:0 2px 12px #152c5b14;border-left:6px solid #7d8ba7}}
.case.passed{{border-left-color:#1b9c5a}} .case.failed,.case.timeout{{border-left-color:#d94343}} .case-title{{display:flex;align-items:center;justify-content:space-between;gap:12px}} h2{{margin:0;font-size:21px}} h3{{margin:22px 0 10px;font-size:16px}}
.badge{{padding:5px 11px;border-radius:999px;background:#dbe3f1;color:#263957;font-weight:700}} .badge.passed{{background:#d9f6e6;color:#08743d}} .badge.failed,.badge.timeout{{background:#ffe0e0;color:#a32222}}
.meta,.muted{{color:#68758c}} .case-error{{color:#b22323;font-weight:700}} .table-wrap{{overflow:auto;border:1px solid #e1e6ef;border-radius:8px}} table{{border-collapse:collapse;width:100%;min-width:760px}} th,td{{padding:11px;border-bottom:1px solid #e8edf4;text-align:left;vertical-align:top}} th{{background:#f6f8fb;color:#4a5870}} td span{{color:#657188;font-size:13px}} img{{display:block;width:160px;max-height:250px;object-fit:contain;background:#111;border-radius:6px}} .image-error{{color:#b7791f}} details{{margin-top:14px}} summary{{cursor:pointer;color:#285ec5}} pre{{overflow:auto;background:#0f172a;color:#e4ecff;padding:14px;border-radius:8px;white-space:pre-wrap;word-break:break-word}}
</style></head><body><header><h1>Android UI 自动化测试报告</h1><div class="summary"><span>总用例：{total}</span><span>通过：{passed}</span><span>失败/超时：{failed}</span><span>生成目录：{directory}</span></div></header><main>{cases}</main></body></html>""".format(
        total=len(items), passed=passed, failed=failed,
        directory=escape(str(output_dir.resolve())), cases="\n".join(case_sections),
    )
    report_path = output_dir / "report.html"
    report_path.write_text(document, encoding="utf-8")
    return report_path
