from types import SimpleNamespace

from utils.run_test_scripts import (
    discover_test_functions,
    discover_test_scripts,
    run_test_scripts,
)


def test_discover_test_scripts_only_returns_runnable_test_files(tmp_path):
    (tmp_path / "test_alpha.py").write_text("", encoding="utf-8")
    (tmp_path / "test_beta.py").write_text("", encoding="utf-8")
    (tmp_path / "helper.py").write_text("", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")

    scripts = discover_test_scripts(tmp_path)

    assert [script.name for script in scripts] == ["test_alpha.py", "test_beta.py"]


def test_dry_run_writes_a_log_without_running_the_test_case(tmp_path):
    script_dir = tmp_path / "test_script"
    script_dir.mkdir()
    script = script_dir / "test_alpha.py"
    script.write_text(
        "def test_first():\n    raise RuntimeError('should not run')\n\n"
        "def helper():\n    pass\n\n"
        "def test_second():\n    raise RuntimeError('should not run')\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "logs"

    results = run_test_scripts(
        [script], script_dir=script_dir, output_dir=output_dir, dry_run=True,
    )

    assert [result.name for result in results] == [
        "test_alpha.py::test_first",
        "test_alpha.py::test_second",
    ]
    assert all(result.status == "dry_run" for result in results)
    log = (output_dir / "test_alpha__test_first.log").read_text(encoding="utf-8")
    assert "-m utils.execute_test_case test_script.test_alpha test_first" in log


def test_discover_test_functions_only_collects_top_level_test_prefixes(tmp_path):
    script = tmp_path / "test_cases.py"
    script.write_text(
        "def test_first():\n    pass\n\n"
        "def helper():\n    pass\n\n"
        "class Tests:\n    def test_nested(self):\n        pass\n\n"
        "def test_second():\n    pass\n",
        encoding="utf-8",
    )

    assert discover_test_functions(script) == ["test_first", "test_second"]


def test_runner_continues_after_a_failed_case_by_default(tmp_path, monkeypatch):
    script_dir = tmp_path / "test_script"
    script_dir.mkdir()
    script = script_dir / "test_cases.py"
    script.write_text(
        "def test_first():\n    pass\n\n"
        "def test_second():\n    pass\n",
        encoding="utf-8",
    )
    completed = iter([
        SimpleNamespace(returncode=1, stdout="first failed"),
        SimpleNamespace(returncode=0, stdout="second passed"),
    ])
    monkeypatch.setattr(
        "utils.run_test_scripts.subprocess.run", lambda *args, **kwargs: next(completed)
    )

    results = run_test_scripts(
        [script], script_dir=script_dir, output_dir=tmp_path / "output",
    )

    assert [result.status for result in results] == ["failed", "passed"]
