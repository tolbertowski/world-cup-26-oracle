from world_cup_oracle import __version__
from world_cup_oracle.cli import main


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_version(capsys) -> None:
    assert main(["--version"]) == 0
    assert "0.1.0" in capsys.readouterr().out
