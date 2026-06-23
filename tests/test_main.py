import subprocess
import sys
from pathlib import Path


def test_main_runs_minimal_experiment(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "--input",
            "examples/minimal_experiment.json",
            "--output-dir",
            str(output_dir),
            "--pretty",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    run_dir = output_dir / "minimal_experiment"
    assert (run_dir / "report.json").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "logs.json").exists()


def test_main_returns_one_for_missing_input(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "--input",
            str(tmp_path / "missing.json"),
            "--output-dir",
            str(tmp_path / "outputs"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1


def test_main_rejects_missing_input_without_benchmark(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "main.py", "--output-dir", str(tmp_path / "outputs")],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1


def test_main_accepts_benchmark_help() -> None:
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--benchmark" in result.stdout
    assert "--data-root" in result.stdout
