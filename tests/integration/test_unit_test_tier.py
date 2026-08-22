from pathlib import Path

import pytest
from scripts.check_unit_test_tier import main


def test_unit_tier_repository_check_reports_the_offending_test_and_rule(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    test_root = tmp_path / "tests" / "unit"
    test_root.mkdir(parents=True)
    (test_root / "test_effect.py").write_text(
        "from pathlib import Path\n\n"
        "def test_writes_state() -> None:\n"
        '    Path("state.json").write_text("{}", encoding="utf-8")\n',
        encoding="utf-8",
    )

    exit_code = main(("--root", str(tmp_path)))

    assert exit_code == 1
    assert capsys.readouterr().err == (
        "tests/unit/test_effect.py:4: test_writes_state [filesystem-call]: Path.write_text\n"
    )
