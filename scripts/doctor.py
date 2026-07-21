#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CPA-X doctor（AI 友好）

用途：
- 自动探测当前设备已有的 CLIProxyAPI / cliproxyapi 安装形态（systemd/unit/config/binary/auth/log）
- 生成/更新 .env，让面板“开箱即用”（除密钥外）

说明：
- doctor 不会也无法自动获取明文密钥（通常配置中存的是 hash）
- 你仍需手动注入：
  - CLIPROXY_PANEL_MANAGEMENT_KEY
  - CLIPROXY_PANEL_MODELS_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple


ENV_PREFIX = "CLIPROXY_PANEL_"


def run_capture(args, timeout: int = 8) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)


def which(cmd: str) -> bool:
    return bool(shutil.which(cmd))


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def has_systemd() -> bool:
    return is_linux() and which("systemctl")


def systemctl_value(unit: str, prop: str) -> str:
    code, out, _ = run_capture(["systemctl", "show", unit, "-p", prop, "--value"], timeout=10)
    return out if code == 0 else ""


def parse_execstart(execstart_value: str) -> Optional[str]:
    """
    systemctl show ExecStart 的输出可能包含：
    { path=/usr/bin/foo ; argv[]=/usr/bin/foo -config /path ; ... }
    这里尽量提取 argv[] 的命令行。
    """
    if not execstart_value:
        return None
    m = re.search(r"argv\[\]=(.*?)(?:\s*;\s*|\s*$)", execstart_value)
    if m:
        return m.group(1).strip()
    # 某些环境可能直接返回命令行
    return execstart_value.strip()


def extract_config_from_cmdline(cmdline: str) -> Tuple[Optional[str], Optional[str]]:
    if not cmdline:
        return None, None
    try:
        parts = shlex.split(cmdline)
    except Exception:
        parts = cmdline.split()
    if not parts:
        return None, None

    binary = parts[0]
    config_path = None
    for i, token in enumerate(parts):
        if token in {"-config", "--config", "-c"} and i + 1 < len(parts):
            config_path = parts[i + 1]
            break
        if token.startswith(("-config=", "--config=", "-c=")):
            config_path = token.split("=", 1)[1]
            break
    return binary, config_path


def list_services() -> list[str]:
    if not has_systemd():
        return []
    code, out, _ = run_capture([
        "systemctl", "list-units", "--all", "--type=service", "--no-legend", "--no-pager"
    ])
    if code != 0 or not out:
        return []
    units = []
    for line in out.splitlines():
        unit = line.split(None, 1)[0].strip()
        if unit.endswith(".service"):
            units.append(unit)
    return units


def pick_cliproxy_unit(units: list[str]) -> Optional[str]:
    """
    优先级：
    1) cliproxyapi@*.service
    2) cli-proxy-api.service / cliproxyapi*.service
    3) 任意 ExecStart 包含 cli-proxy-api/cliproxyapi 的 service
    """
    for u in units:
        if u.startswith("cliproxyapi@") and u.endswith(".service"):
            return u
    for u in units:
        if u in {"cli-proxy-api.service", "cliproxyapi.service"}:
            return u
    for u in units:
        if u.startswith("cliproxyapi") and u.endswith(".service"):
            return u
    # slow path: inspect ExecStart
    for u in units:
        execstart = systemctl_value(u, "ExecStart")
        cmdline = parse_execstart(execstart) or ""
        if "cli-proxy-api" in cmdline or "cliproxyapi" in cmdline:
            return u
    return None


def try_load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8", errors="ignore")) or {}
    except Exception:
        return {}


def detect_from_config(config_path: Optional[str]) -> Dict[str, str]:
    if not config_path:
        return {}
    p = Path(config_path)
    config = try_load_yaml(p)
    if not isinstance(config, dict):
        config = {}

    ret: Dict[str, str] = {}
    # port / host
    port = config.get("port")
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port <= 65535:
        ret["cliproxy_api_port"] = str(port)

    auth_dir = config.get("auth-dir") or config.get("auth_dir")
    if isinstance(auth_dir, str) and auth_dir.strip():
        expanded = Path(os.path.expandvars(os.path.expanduser(auth_dir.strip())))
        if not expanded.is_absolute():
            expanded = p.parent / expanded
        ret["auth_dir"] = str(expanded.resolve(strict=False))

    return ret


def detect_log_path(auth_dir: Optional[str], working_dir: Optional[str]) -> Optional[str]:
    candidates = []
    if auth_dir:
        candidates.append(os.path.join(auth_dir, "logs", "main.log"))
    if working_dir:
        candidates.append(os.path.join(working_dir, "logs", "main.log"))
        candidates.append(os.path.join(working_dir, "auths", "logs", "main.log"))

    for c in candidates:
        try:
            if os.path.exists(c):
                return c
        except Exception:
            continue

    # 兜底：返回最可能的一个（即使暂时不存在）
    if auth_dir:
        return os.path.join(auth_dir, "logs", "main.log")
    if working_dir:
        return os.path.join(working_dir, "logs", "main.log")
    return None


def env_key(k: str) -> str:
    return f"{ENV_PREFIX}{k.upper()}"


def _is_effectively_empty(value: str) -> bool:
    v = (value or "").strip()
    if v in {"", '""', "''"}:
        return True
    return False


def upsert_env_file(path: Path, updates: Dict[str, str], overwrite_existing: bool) -> None:
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    wanted = {}
    for key, value in updates.items():
        if value is None:
            continue
        value_text = str(value)
        if any(char in value_text for char in ("\r", "\n", "\x00")):
            continue
        wanted[env_key(key)] = value_text
    if not wanted:
        return

    new_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key, existing_value = line.split("=", 1)
        key = key.strip()
        if key in wanted:
            if overwrite_existing or _is_effectively_empty(existing_value):
                new_lines.append(f"{key}={wanted[key]}")
            else:
                new_lines.append(line)
            seen.add(key)
        else:
            new_lines.append(line)

    for k, v in wanted.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")

    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = None
    try:
        existing_mode = path.stat().st_mode & 0o777
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(new_lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, existing_mode if existing_mode is not None else 0o600)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.remove(temp_name)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-env", action="store_true", help="写入/更新 .env（默认只输出建议）")
    parser.add_argument("--env-path", default=".env", help="env 文件路径（默认 .env）")
    parser.add_argument("--overwrite-existing", action="store_true", help="覆盖已存在的非空配置（默认只补缺失/空值）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于 AI 解析）")
    args = parser.parse_args()

    result: Dict[str, str] = {}

    # Panel defaults (AI safe)
    result["bind_host"] = "0.0.0.0"
    result["panel_port"] = "8080"
    result["cliproxy_api_base"] = "http://127.0.0.1"

    unit = None
    binary = None
    config_path = None
    working_dir = None

    if has_systemd():
        units = list_services()
        unit = pick_cliproxy_unit(units)
        if unit:
            execstart = systemctl_value(unit, "ExecStart")
            cmdline = parse_execstart(execstart) or ""
            binary, config_path = extract_config_from_cmdline(cmdline)
            working_dir = systemctl_value(unit, "WorkingDirectory") or None

            if working_dir:
                working_dir = str(Path(working_dir).expanduser().resolve(strict=False))
            if config_path and not os.path.isabs(config_path):
                config_path = str(
                    ((Path(working_dir) if working_dir else Path.cwd()) / config_path).resolve(strict=False)
                )
            if binary and not os.path.isabs(binary):
                binary = str(
                    ((Path(working_dir) if working_dir else Path.cwd()) / binary).resolve(strict=False)
                )

            # CLIProxyAPI can start without an explicit --config flag. Prefer a
            # config beside its declared working directory before leaving the
            # example placeholder in place.
            if not config_path and working_dir:
                for name in ("config.yaml", "config.yml"):
                    candidate = Path(working_dir) / name
                    if candidate.is_file():
                        config_path = str(candidate.resolve())
                        break

            # systemctl 命令接受带/不带 .service；面板里推荐不带
            if unit.endswith(".service"):
                result["cliproxy_service"] = unit[:-8]
            else:
                result["cliproxy_service"] = unit

    if binary:
        result["cliproxy_binary"] = binary
    else:
        detected_binary = next(
            (shutil.which(name) for name in ("cli-proxy-api", "cliproxyapi", "cliproxy") if shutil.which(name)),
            None,
        )
        if detected_binary:
            result["cliproxy_binary"] = detected_binary
    if config_path:
        result["cliproxy_config"] = config_path
        config_detect = detect_from_config(config_path)
        result.update(config_detect)

    auth_dir = result.get("auth_dir")
    log_path = detect_log_path(auth_dir, working_dir)
    if log_path:
        result["cliproxy_log"] = log_path

    if working_dir:
        result["cliproxy_dir"] = working_dir

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for k in sorted(result.keys()):
            print(f"{env_key(k)}={result[k]}")

    if args.write_env:
        env_path = Path(args.env_path)
        upsert_env_file(env_path, result, overwrite_existing=args.overwrite_existing)
        if not args.json:
            print(f"\n[doctor] 已写入: {env_path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
