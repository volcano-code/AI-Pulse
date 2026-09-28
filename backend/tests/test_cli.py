import json
from app.cli import main


def test_cli_init_and_demo(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL",f"sqlite:///{tmp_path/'cli.db'}")
    monkeypatch.setenv("DATA_MODE","replay")
    monkeypatch.setenv("LLM_MODE","extractive")
    monkeypatch.setattr("sys.argv",["app.cli","init"])
    assert main()==0
    assert json.loads(capsys.readouterr().out)["initialized"]
    monkeypatch.setattr("sys.argv",["app.cli","demo"])
    assert main()==0
    assert json.loads(capsys.readouterr().out)["brief"]["items"]==6
    monkeypatch.setattr("sys.argv",["app.cli","brief"])
    assert main()==0
    assert json.loads(capsys.readouterr().out)["status"]=="succeeded"
