import io

from steadygate.cli import main


def test_init_with_flags_writes_both(tmp_path, capsys):
    assert main(["init", "--dir", str(tmp_path), "--format", "junit"]) == 0
    assert (tmp_path / "steadygate.yml").exists()
    assert (tmp_path / ".github/workflows/steadygate.yml").exists()
    out = capsys.readouterr().out
    assert "steadygate.yml" in out and "git add" in out


def test_init_keeps_existing_without_force(tmp_path, capsys):
    (tmp_path / "steadygate.yml").write_text("old", encoding="utf-8")
    assert main(["init", "--dir", str(tmp_path), "--format", "jsonl"]) == 0
    assert (tmp_path / "steadygate.yml").read_text(encoding="utf-8") == "old"
    assert "kept existing" in capsys.readouterr().out
    assert main(["init", "--dir", str(tmp_path), "--format", "jsonl", "--force"]) == 0
    assert (tmp_path / "steadygate.yml").read_text(encoding="utf-8") != "old"


def test_init_without_format_and_no_tty_is_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["init", "--dir", str(tmp_path)]) == 3


def test_init_rejects_bad_tolerance(tmp_path):
    assert main(["init", "--dir", str(tmp_path), "--format", "jsonl", "--tolerance", "5"]) == 3


def test_init_interactive(tmp_path, monkeypatch):
    class TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", TTY(""))
    answers = iter(["promptfoo", "", "", "", ""])  # format, then accept the defaults
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert "score" in (tmp_path / "steadygate.yml").read_text(encoding="utf-8")


def test_init_interactive_bad_format_and_overrides(tmp_path, monkeypatch):
    class TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", TTY(""))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "nope")
    assert main(["init", "--dir", str(tmp_path)]) == 3
    answers = iter(["jsonl", "out.jsonl", "acc", "0.1", "make eval"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert "acc" in (tmp_path / "steadygate.yml").read_text(encoding="utf-8")


def test_init_interactive_bad_tolerance(tmp_path, monkeypatch):
    class TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", TTY(""))
    answers = iter(["jsonl", "", "", "7", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main(["init", "--dir", str(tmp_path)]) == 3
