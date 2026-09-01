from utils.run_test_scripts import discover_test_scripts, run_test_scripts


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
    script.write_text("raise RuntimeError('should not run')", encoding="utf-8")
    output_dir = tmp_path / "logs"

    results = run_test_scripts(
        [script], script_dir=script_dir, output_dir=output_dir, dry_run=True,
    )

    assert results[0].status == "dry_run"
    assert "-m test_script.test_alpha" in (output_dir / "test_alpha.log").read_text(encoding="utf-8")
