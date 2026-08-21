"""Tests for the env-var catalog + `promptry env`."""
import re

from typer.testing import CliRunner

from promptry import envvars
from promptry.cli import app


def test_catalog_covers_real_vars():
    # Every PROMPTRY_* the catalog names should be a plausible upper-snake var.
    names = {e.name for e in envvars.CATALOG}
    assert "PROMPTRY_STORAGE_MODE" in names
    assert "PROMPTRY_TRUST_PROXY" in names
    assert all(n.startswith("PROMPTRY_") and n.isupper() for n in names)
    assert len(names) == len(envvars.CATALOG)  # no dupes


def test_inspect_masks_secrets(monkeypatch):
    monkeypatch.setenv("PROMPTRY_AUTH_TOKEN", "supersecretvalue")
    monkeypatch.setenv("PROMPTRY_STORAGE_MODE", "async")
    rows = {r["name"]: r for r in envvars.inspect()}
    assert rows["PROMPTRY_AUTH_TOKEN"]["set"] is True
    assert "supersecretvalue" not in rows["PROMPTRY_AUTH_TOKEN"]["value"]
    assert rows["PROMPTRY_STORAGE_MODE"]["value"] == "async"  # non-secret shown


def test_env_command_runs_and_shows_precedence():
    r = CliRunner().invoke(app, ["env"])
    assert r.exit_code == 0
    out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout)
    assert "Precedence" in out
    assert "PROMPTRY_DB" in out
