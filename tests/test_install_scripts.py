from pathlib import Path

import pytest

from scripts import auto_install, doctor


def test_doctor_env_write_is_atomic_and_respects_overwrite(tmp_path):
    env_path = tmp_path / '.env'
    env_path.write_text('CLIPROXY_PANEL_PANEL_PORT=9000\n', encoding='utf-8')

    doctor.upsert_env_file(env_path, {'panel_port': '8080', 'bind_host': '127.0.0.1'}, False)
    content = env_path.read_text(encoding='utf-8')
    assert 'CLIPROXY_PANEL_PANEL_PORT=9000' in content
    assert 'CLIPROXY_PANEL_BIND_HOST=127.0.0.1' in content

    doctor.upsert_env_file(env_path, {'panel_port': '8080'}, True)
    assert 'CLIPROXY_PANEL_PANEL_PORT=8080' in env_path.read_text(encoding='utf-8')
    assert not list(Path(tmp_path).glob('.*.tmp'))


def test_installer_command_runner_never_accepts_shell_strings():
    with pytest.raises(TypeError):
        auto_install.run('echo unsafe')


def test_doctor_parses_long_config_flag():
    binary, config = doctor.extract_config_from_cmdline(
        '/usr/local/bin/cliproxyapi --config="/etc/cliproxy api/config.yaml"'
    )
    assert binary == '/usr/local/bin/cliproxyapi'
    assert config == '/etc/cliproxy api/config.yaml'

    _, short_config = doctor.extract_config_from_cmdline('/usr/bin/cliproxyapi -c=relative.yaml')
    assert short_config == 'relative.yaml'


def test_doctor_resolves_relative_auth_directory(tmp_path):
    config = tmp_path / 'config.yaml'
    config.write_text('port: 8317\nauth-dir: ./auths\n', encoding='utf-8')
    detected = doctor.detect_from_config(str(config))
    assert detected['cliproxy_api_port'] == '8317'
    assert Path(detected['auth_dir']) == (tmp_path / 'auths').resolve()


def test_compose_defaults_to_loopback_and_requires_panel_key():
    compose = (Path(__file__).parents[1] / 'docker-compose.yml').read_text(encoding='utf-8')
    assert '${CLIPROXY_PANEL_PUBLISH_HOST:-127.0.0.1}' in compose
    assert '${CLIPROXY_PANEL_PANEL_ACCESS_KEY:?' in compose
    assert 'CLIPROXY_PANEL_PANEL_ACCESS_KEY: "change-me"' not in compose
