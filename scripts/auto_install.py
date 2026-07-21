import argparse
import os
import shutil
import subprocess
import sys
import re
import tempfile
from pathlib import Path


def run(cmd, cwd=None):
    if isinstance(cmd, (str, bytes)):
        raise TypeError("run() requires an argument sequence")
    result = subprocess.run([str(part) for part in cmd], cwd=cwd, shell=False, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def ensure_venv(project_root: Path, python_bin: str):
    venv_dir = project_root / ".venv"
    if not venv_dir.exists():
        run([python_bin, "-m", "venv", ".venv"], cwd=str(project_root))
    return venv_dir


def venv_python(venv_dir: Path, is_windows: bool):
    if is_windows:
        return str(venv_dir / "Scripts" / "python.exe")
    return str(venv_dir / "bin" / "python")


def install_requirements(project_root: Path, venv_py: str):
    run([venv_py, "-m", "pip", "install", "-r", "requirements.txt"], cwd=str(project_root))


def ensure_env(project_root: Path, venv_py: str):
    env_file = project_root / ".env"
    example = project_root / ".env.example"
    created_from_example = not env_file.exists() and example.exists()
    if created_from_example:
        env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            env_file.chmod(0o600)
        except OSError:
            pass

    # AI-friendly: try auto-detect to fill paths/service name (keys still need manual input)
    doctor = project_root / "scripts" / "doctor.py"
    if doctor.exists():
        try:
            python_bin = venv_py if venv_py and Path(venv_py).exists() else sys.executable
            doctor_args = [python_bin, str(doctor), "--write-env", f"--env-path={env_file}"]
            if created_from_example:
                # The example contains realistic-looking placeholder paths; on
                # first install they must be replaced by detected values.
                doctor_args.append("--overwrite-existing")
            subprocess.run(
                doctor_args,
                cwd=str(project_root),
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[installer] doctor failed: {exc}", file=sys.stderr)


def systemd_quote(value: Path | str) -> str:
    raw = str(value)
    if any(char in raw for char in ("\r", "\n", "\x00")):
        raise ValueError("systemd value contains an invalid control character")
    escaped = raw.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def install_systemd(project_root: Path, venv_py: str, service_name: str, start_service: bool):
    service_name = service_name.removesuffix(".service")
    if not re.fullmatch(r"(?:[A-Za-z0-9_.@:-]|\\x[0-9A-Fa-f]{2})+", service_name) or service_name.startswith("-"):
        raise SystemExit("无效的 systemd 服务名。")
    service_path = Path("/etc/systemd/system") / f"{service_name}.service"
    content = "\n".join([
        "[Unit]",
        "Description=CLIProxy Management Panel",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={systemd_quote(project_root)}",
        f"ExecStart={systemd_quote(venv_py)} {systemd_quote(project_root / 'app.py')}",
        "Restart=always",
        "RestartSec=5",
        "Environment=PYTHONUNBUFFERED=1",
        "Environment=PYTHONDONTWRITEBYTECODE=1",
        "UMask=0077",
        "PrivateTmp=true",
        "TimeoutStopSec=20",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])
    fd, temp_name = tempfile.mkstemp(prefix=f".{service_name}.", suffix=".tmp", dir=service_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, service_path)
    except Exception:
        try:
            os.remove(temp_name)
        except OSError:
            pass
        raise
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", service_name])
    if start_service:
        run(["systemctl", "restart", service_name])


def start_windows(project_root: Path, venv_py: str):
    app_path = project_root / "app.py"
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NO_WINDOW
    )
    subprocess.Popen(
        [venv_py, str(app_path)],
        cwd=str(project_root),
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-service", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--start", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-sudo", action="store_true")
    parser.add_argument("--service-name", default="cliproxy-panel")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    is_windows = os.name == "nt"
    python_bin = sys.executable
    if sys.version_info < (3, 11):
        raise SystemExit("CPA-X 需要 Python 3.11 或更高版本。")

    venv_dir = ensure_venv(project_root, python_bin)
    venv_py = venv_python(venv_dir, is_windows)
    install_requirements(project_root, venv_py)
    ensure_env(project_root, venv_py)

    if not is_windows and args.install_service:
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            if not args.no_sudo and shutil.which("sudo"):
                sudo_args = [
                    "sudo",
                    python_bin,
                    str(Path(__file__).resolve()),
                    "--install-service",
                    f"--service-name={args.service_name}",
                    "--no-sudo",
                ]
                sudo_args.append("--start" if args.start else "--no-start")
                os.execvp("sudo", sudo_args)
            raise SystemExit("需要 root 权限安装 systemd 服务，请用 sudo 运行。")
        install_systemd(project_root, venv_py, args.service_name, args.start)
        return

    if args.start:
        if is_windows:
            start_windows(project_root, venv_py)
        else:
            run([venv_py, "app.py"], cwd=str(project_root))


if __name__ == "__main__":
    main()
