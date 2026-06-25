"""Tests for the top-level --version and --agent flags."""

from __future__ import annotations

import pytest

import mbdeploy
from mbdeploy.cli import _build_parser, _read_agent_manual


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------

class TestVersionFlag:
    def test_version_matches_package_metadata(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert mbdeploy.__version__ in out
        assert out.strip() == f"mbdeploy {mbdeploy.__version__}"

    def test_version_is_not_placeholder(self):
        # The version must come from real package metadata, not the old
        # hardcoded 0.1.0 placeholder.
        assert mbdeploy.__version__ != "0.1.0"


# ---------------------------------------------------------------------------
# --agent
# ---------------------------------------------------------------------------

class TestAgentFlag:
    def test_manual_resource_loads(self):
        text = _read_agent_manual()
        assert text.strip()
        assert "mbdeploy" in text.lower()

    def test_agent_flag_prints_manual_and_exits(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--agent"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Agent Manual" in out
        # A few anchors that prove the full document was emitted.
        assert "Recipes" in out
        assert "--force-relay" in out

    def test_agent_flag_works_without_subcommand(self, capsys):
        """--agent must short-circuit the otherwise-required subcommand."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--agent"])
        assert exc.value.code == 0
        assert capsys.readouterr().out.strip()
