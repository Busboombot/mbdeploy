"""Tests for `serve --print-service`/`--install-service` (ticket 008):
systemd unit rendering, `--system`/`--user` scope selection (system is
the stakeholder's binding default), `WorkingDirectory`/`--config`
baking, `--token`-to-`--token-file` conversion, and that the bundled
unit template actually ships and is readable from the package.

No real `/etc` or real home-directory writes: every install-path test
monkeypatches the module-level `_SYSTEM_UNIT_DIR`/`_USER_UNIT_DIR`/
`_SYSTEM_TOKEN_DIR`/`_USER_TOKEN_DIR` constants to a `tmp_path`, per
`_unit_install_dir`/`_token_install_dir`'s own call-time lookup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import mbdeploy.cli as cli_mod

_SECRET = "s3cret-do-not-leak"


def _parser():
    return cli_mod._build_parser()


def _serve_args(argv: list[str]):
    return _parser().parse_args(["serve", *argv])


def _redirect_install_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli_mod, "_SYSTEM_UNIT_DIR", tmp_path / "etc" / "systemd" / "system")
    monkeypatch.setattr(cli_mod, "_USER_UNIT_DIR", tmp_path / "home" / ".config" / "systemd" / "user")
    monkeypatch.setattr(cli_mod, "_SYSTEM_TOKEN_DIR", tmp_path / "etc" / "mbdeploy")
    monkeypatch.setattr(cli_mod, "_USER_TOKEN_DIR", tmp_path / "home" / ".config" / "mbdeploy")


# ---------------------------------------------------------------------------
# Template ships in the package
# ---------------------------------------------------------------------------

class TestTemplateResource:
    def test_template_is_readable_from_the_package(self):
        """Same idiom as `_read_agent_manual`'s own test
        (`test_manual_resource_loads`): import-based, not a raw
        filesystem path, so it also proves the resource is reachable
        from an installed (not just source-checkout) package."""
        text = cli_mod._read_systemd_unit_template()
        assert text.strip()
        assert "[Unit]" in text
        assert "[Service]" in text
        assert "[Install]" in text
        assert "{exec_start}" in text
        assert "{working_directory}" in text


# ---------------------------------------------------------------------------
# --print-service
# ---------------------------------------------------------------------------

class TestPrintService:
    def test_print_service_emits_valid_unit_structure(self, capsys):
        args = _serve_args(["--print-service", "--system"])
        rc = args.func(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "[Unit]" in out
        assert "[Service]" in out
        assert "[Install]" in out
        # ExecStart is a single well-formed line: exactly one match, and
        # its value is non-empty right up to the newline.
        exec_lines = [ln for ln in out.splitlines() if ln.startswith("ExecStart=")]
        assert len(exec_lines) == 1
        assert exec_lines[0][len("ExecStart="):].strip()

    def test_print_service_touches_no_filesystem(self, monkeypatch, tmp_path):
        """Even with --system's real-looking default paths in play,
        --print-service must never call mkdir/write_text -- verified by
        redirecting the install dirs to tmp_path and asserting nothing
        appears there afterward."""
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--print-service", "--system"])
        args.func(args)
        assert list(tmp_path.rglob("*")) == []

    def test_working_directory_is_absolute(self, capsys):
        args = _serve_args(["--print-service"])
        args.func(args)
        out = capsys.readouterr().out
        wd_lines = [ln for ln in out.splitlines() if ln.startswith("WorkingDirectory=")]
        assert len(wd_lines) == 1
        wd = wd_lines[0][len("WorkingDirectory="):]
        assert Path(wd).is_absolute()
        assert Path.cwd().resolve() == Path(wd)

    def test_resolved_config_path_is_baked_into_exec_start(self, capsys, tmp_path):
        cfg = tmp_path / "devices.json"
        args = _serve_args(["--print-service", "--config", str(cfg)])
        args.func(args)
        out = capsys.readouterr().out
        exec_line = next(ln for ln in out.splitlines() if ln.startswith("ExecStart="))
        assert str(cfg) in exec_line

    def test_default_config_is_resolved_absolute_even_when_relative(self, capsys):
        args = _serve_args(["--print-service"])
        args.func(args)
        out = capsys.readouterr().out
        exec_line = next(ln for ln in out.splitlines() if ln.startswith("ExecStart="))
        assert "--config" in exec_line
        # The default is CWD-relative ("config/devices.json"); baked in,
        # it must be absolute.
        config_token = exec_line.split("--config", 1)[1].split()[0]
        assert Path(config_token).is_absolute()

    def test_exec_start_carries_daemon_flags_and_excludes_service_flags(self, capsys):
        args = _serve_args([
            "--print-service", "--system",
            "--poll-interval", "5",
            "--base-port", "9000",
            "--bind", "127.0.0.1",
            "--no-flash",
            "--target-mcu", "nrf52840",
            "--service-name", "myboard",
        ])
        args.func(args)
        out = capsys.readouterr().out
        exec_line = next(ln for ln in out.splitlines() if ln.startswith("ExecStart="))
        for expected in (
            "--poll-interval 5", "--base-port 9000", "--bind 127.0.0.1",
            "--no-flash", "--target-mcu nrf52840", "--service-name myboard",
        ):
            assert expected in exec_line, f"{expected!r} missing from ExecStart"
        # Service-management flags never apply to the running daemon.
        for excluded in (
            "--print-service", "--install-service", "--system", "--user",
        ):
            assert excluded not in exec_line

    def test_print_service_with_token_refuses_rather_than_leak(self, capsys):
        """--print-service never writes a file, so there is nowhere to
        put a --token secret -- it must refuse rather than ever emit the
        literal secret in ExecStart."""
        args = _serve_args(["--print-service", "--token", _SECRET])
        rc = args.func(args)
        assert rc != 0
        err = capsys.readouterr().err
        assert "--token" in err
        assert _SECRET not in err

    def test_print_service_with_token_file_references_it_directly(self, capsys, tmp_path):
        token_file = tmp_path / "token.txt"
        token_file.write_text(_SECRET + "\n")
        args = _serve_args(["--print-service", "--token-file", str(token_file)])
        rc = args.func(args)
        assert rc == 0
        out = capsys.readouterr().out
        exec_line = next(ln for ln in out.splitlines() if ln.startswith("ExecStart="))
        assert f"--token-file {token_file}" in exec_line
        assert _SECRET not in out


# ---------------------------------------------------------------------------
# --install-service: scope selection and path injection
# ---------------------------------------------------------------------------

class TestInstallServiceScope:
    def test_system_flag_installs_to_system_unit_dir(self, monkeypatch, tmp_path):
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--install-service", "--system"])
        rc = args.func(args)
        assert rc == 0
        expected = tmp_path / "etc" / "systemd" / "system" / "mbdeploy.service"
        assert expected.is_file()
        assert "[Unit]" in expected.read_text()

    def test_user_flag_installs_to_user_unit_dir(self, monkeypatch, tmp_path):
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--install-service", "--user"])
        rc = args.func(args)
        assert rc == 0
        expected = tmp_path / "home" / ".config" / "systemd" / "user" / "mbdeploy.service"
        assert expected.is_file()
        # System unit path must NOT have been touched.
        assert not (tmp_path / "etc" / "systemd" / "system" / "mbdeploy.service").exists()

    def test_neither_flag_defaults_to_system(self, monkeypatch, tmp_path):
        """The one behavior this ticket must get right per the
        stakeholder's binding decision: --install-service with no
        --system/--user given installs the SYSTEM unit."""
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--install-service"])
        rc = args.func(args)
        assert rc == 0
        system_path = tmp_path / "etc" / "systemd" / "system" / "mbdeploy.service"
        user_path = tmp_path / "home" / ".config" / "systemd" / "user" / "mbdeploy.service"
        assert system_path.is_file()
        assert not user_path.exists()

    def test_system_and_user_are_mutually_exclusive(self):
        with pytest.raises(SystemExit) as exc:
            _parser().parse_args(["serve", "--install-service", "--system", "--user"])
        assert exc.value.code != 0

    def test_user_install_warns_about_linger(self, monkeypatch, tmp_path, capsys):
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--install-service", "--user"])
        args.func(args)
        err = capsys.readouterr().err
        assert "linger" in err.lower()

    def test_system_install_does_not_warn_about_linger(self, monkeypatch, tmp_path, capsys):
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--install-service", "--system"])
        args.func(args)
        err = capsys.readouterr().err
        assert "linger" not in err.lower()


# ---------------------------------------------------------------------------
# --install-service: token handling
# ---------------------------------------------------------------------------

class TestInstallServiceToken:
    def test_token_becomes_token_file_never_literal_in_exec_start(self, monkeypatch, tmp_path):
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--install-service", "--system", "--token", _SECRET])
        rc = args.func(args)
        assert rc == 0

        unit_path = tmp_path / "etc" / "systemd" / "system" / "mbdeploy.service"
        content = unit_path.read_text()

        # The whole point: grep the generated content and prove the
        # literal secret string never appears in it.
        assert _SECRET not in content
        assert "--token-file" in content
        assert "--token " not in content  # no literal --token flag either

        exec_line = next(
            ln for ln in content.splitlines() if ln.startswith("ExecStart=")
        )
        # The path named after --token-file must itself contain the secret.
        token_path_str = exec_line.split("--token-file", 1)[1].split()[0]
        token_path = Path(token_path_str)
        assert token_path.is_file()
        assert token_path.read_text().strip() == _SECRET

    def test_token_file_is_written_with_owner_only_permissions(self, monkeypatch, tmp_path):
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--install-service", "--system", "--token", _SECRET])
        args.func(args)
        token_path = tmp_path / "etc" / "mbdeploy" / "token"
        assert token_path.is_file()
        mode = token_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_token_file_flag_passes_through_without_writing_a_new_file(self, monkeypatch, tmp_path):
        _redirect_install_dirs(monkeypatch, tmp_path)
        token_file = tmp_path / "external-token.txt"
        token_file.write_text(_SECRET + "\n")

        args = _serve_args(["--install-service", "--system", "--token-file", str(token_file)])
        rc = args.func(args)
        assert rc == 0

        unit_path = tmp_path / "etc" / "systemd" / "system" / "mbdeploy.service"
        content = unit_path.read_text()
        assert _SECRET not in content
        assert f"--token-file {token_file}" in content
        # No secret written under the token install dir -- --token-file
        # already named an existing file.
        assert not (tmp_path / "etc" / "mbdeploy" / "token").exists()

    def test_no_token_at_all_omits_token_file_flag(self, monkeypatch, tmp_path):
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--install-service", "--system"])
        args.func(args)
        unit_path = tmp_path / "etc" / "systemd" / "system" / "mbdeploy.service"
        assert "--token-file" not in unit_path.read_text()

    def test_install_service_with_token_and_user_scope_uses_user_token_dir(self, monkeypatch, tmp_path):
        _redirect_install_dirs(monkeypatch, tmp_path)
        args = _serve_args(["--install-service", "--user", "--token", _SECRET])
        rc = args.func(args)
        assert rc == 0
        expected_token_path = tmp_path / "home" / ".config" / "mbdeploy" / "token"
        assert expected_token_path.is_file()
        assert expected_token_path.read_text().strip() == _SECRET


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

class TestServiceArgParsing:
    def test_flags_default_to_false_and_none(self):
        args = _serve_args([])
        assert args.print_service is False
        assert args.install_service is False
        assert args.service_scope is None

    def test_help_documents_service_flags(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _parser().parse_args(["serve", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for flag in ("--print-service", "--install-service", "--system", "--user"):
            assert flag in out, f"{flag} missing from `serve --help`"
