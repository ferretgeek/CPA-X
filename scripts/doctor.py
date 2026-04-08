#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CPA-X doctor（AI 友好）

用途：
- 自动探测当前设备已有的 CLIProxyAPI / cliproxyapi 安装形态（systemd/unit/config/binary/auth/log）
- 生成/更新 .env，让面板"开箱即用"（除密钥外）
- 检测 CLIProxyAPI 配置问题并自动修复（auth-dir 路径、管理密钥格式等）
- 自动安装系统级 systemd 服务（如果只有 user-level 服务）

说明：
- doctor 无法读取已哈希的管理密钥明文，但可以生成新密钥并写入配置
- 你仍需手动注入：
  - CLIPROXY_PANEL_MANAGEMENT_KEY（或使用 --auto-gen-key 自动生成）
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
from pathlib import Path
from typing import Dict, List, Optional, Tuple


import hashlib
import secrets
import socket


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
    if not is_linux():
        return False
    code, out, _ = run_capture(["bash", "-lc", "command -v systemctl >/dev/null 2>&1; echo $?"])
    return code == 0 and out.endswith("0")


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
        if token in {"-config", "--config"} and i + 1 < len(parts):
            config_path = parts[i + 1]
            break
        if token.startswith("-config="):
            config_path = token.split("=", 1)[1]
            break
    return binary, config_path


def list_running_services() -> list[str]:
    if not has_systemd():
        return []
    code, out, _ = run_capture(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend"])
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
    if isinstance(port, int) and port > 0:
        ret["cliproxy_api_port"] = str(port)

    auth_dir = config.get("auth-dir") or config.get("auth_dir")
    if isinstance(auth_dir, str) and auth_dir.strip():
        expanded = os.path.expanduser(auth_dir.strip())
        ret["auth_dir"] = expanded

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

    wanted = {env_key(k): v for k, v in updates.items() if v is not None}
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

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ==================== User-level systemd service detection ====================

def list_user_running_services() -> list[str]:
    """List running user-level systemd services (systemctl --user)."""
    if not has_systemd():
        return []
    code, out, _ = run_capture(
        ["bash", "-lc", "systemctl --user list-units --type=service --state=running --no-legend 2>/dev/null"],
        timeout=10
    )
    if code != 0 or not out:
        return []
    units = []
    for line in out.splitlines():
        unit = line.split(None, 1)[0].strip()
        if unit.endswith(".service"):
            units.append(unit)
    return units


def systemctl_user_value(unit: str, prop: str) -> str:
    """Get property value from user-level systemd service."""
    code, out, _ = run_capture(
        ["bash", "-lc", f"systemctl --user show {shlex.quote(unit)} -p {shlex.quote(prop)} --value 2>/dev/null"],
        timeout=10
    )
    return out if code == 0 else ""


def pick_cliproxy_unit_user(units: list[str]) -> Optional[str]:
    """Pick CLIProxyAPI unit from user-level services."""
    for u in units:
        if u.startswith("cliproxyapi@") and u.endswith(".service"):
            return u
    for u in units:
        if u in {"cli-proxy-api.service", "cliproxyapi.service"}:
            return u
    for u in units:
        if u.startswith("cliproxyapi") and u.endswith(".service"):
            return u
    return None


# ==================== Config validation and auto-fix ====================

def is_hashed_secret_key(value: str) -> bool:
    """Check if a value looks like a bcrypt hash (CLIProxyAPI hashed secret-key)."""
    return value.startswith("$2") and len(value) > 50


def validate_config(config_path: str, auto_fix: bool = False) -> Tuple[bool, List[str], Dict]:
    """
    Validate CLIProxyAPI config.yaml for common issues.
    Returns (is_valid, warnings, fixes_applied).
    """
    p = Path(config_path)
    if not p.exists():
        return False, [f"Config file not found: {config_path}"], {}

    config = try_load_yaml(p)
    if not isinstance(config, dict):
        return False, ["Config is not a valid YAML dictionary"], {}

    warnings = []
    fixes = {}

    # Check auth-dir
    auth_dir = config.get("auth-dir")
    if not auth_dir or not isinstance(auth_dir, str) or not auth_dir.strip():
        warnings.append("auth-dir is empty or missing - CLIProxyAPI will fail to start")
        if auto_fix:
            # Try to find existing auth directory from common locations
            candidates = [
                Path("/root/.cli-proxy-api"),
                Path.home() / ".cli-proxy-api",
                Path("/var/lib/cliproxyapi/auths"),
                Path("/opt/CLIProxyAPI/data"),
            ]
            for c in candidates:
                if c.exists():
                    fixes["auth-dir"] = str(c)
                    warnings.append(f"  -> Auto-fixing auth-dir to: {c}")
                    break
            if "auth-dir" not in fixes:
                # Create default
                default = Path.home() / ".cli-proxy-api"
                default.mkdir(parents=True, exist_ok=True)
                fixes["auth-dir"] = str(default)
                warnings.append(f"  -> Auto-fixing auth-dir to: {default}")
    elif "~" in auth_dir:
        expanded = os.path.expanduser(auth_dir)
        if not os.path.isdir(expanded):
            os.makedirs(expanded, exist_ok=True)
            warnings.append(f"Created missing auth directory: {expanded}")

    # Check remote-management.secret-key
    mgmt = config.get("remote-management", {})
    if not isinstance(mgmt, dict):
        mgmt = {}
    secret_key = mgmt.get("secret-key", "")
    if not secret_key or not isinstance(secret_key, str) or not secret_key.strip():
        warnings.append("remote-management.secret-key is empty - management API will return 404")

    # Check for incorrect nested structure (the "enabled" vs "allow-remote" issue)
    if mgmt.get("enabled") is not None and mgmt.get("allow-remote") is None:
        warnings.append(
            "remote-management uses 'enabled' key instead of 'allow-remote' - "
            "this may cause the management API to not work"
        )

    return len(warnings) == 0 or auto_fix, warnings, fixes


def generate_management_key() -> str:
    """Generate a random management key (32 hex chars)."""
    return secrets.token_hex(16)


def apply_config_fixes(config_path: str, fixes: Dict[str, str]) -> bool:
    """Apply fixes to config.yaml. Returns True if successful."""
    try:
        import yaml  # type: ignore
    except ImportError:
        print("[doctor] ERROR: pyyaml not installed, cannot modify config")
        print("[doctor] Install with: pip install pyyaml")
        return False

    p = Path(config_path)
    config = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        config = {}

    for key, value in fixes.items():
        if key == "auth-dir":
            config["auth-dir"] = value

    # Write back preserving comments is hard, so write clean YAML
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, width=120)

    return True


# ==================== System-level service installation ====================

def detect_cliproxy_binary_from_common_paths() -> Optional[str]:
    """Check common CLIProxyAPI install locations if systemd detection failed."""
    candidates = [
        "/root/cliproxyapi/cli-proxy-api",
        "/usr/local/bin/cliproxyapi",
        "/usr/local/bin/cli-proxy-api",
        "/opt/CLIProxyAPI/cliproxy",
        "/opt/CLIProxyAPI/cli-proxy-api",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def detect_cliproxy_config_from_binary(binary: str) -> Optional[str]:
    """Find config.yaml in the same directory as the binary."""
    if not binary:
        return None
    binary_dir = os.path.dirname(binary)
    candidates = [
        os.path.join(binary_dir, "config.yaml"),
        "/etc/cliproxyapi/config.yaml",
        "/etc/cliproxyapi/freecodex/config.yaml",
        "/root/cliproxyapi/config.yaml",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def install_system_service(binary: str, config: str, working_dir: str) -> Optional[str]:
    """
    Install a system-level systemd service for CLIProxyAPI.
    Returns the service name if successful, None otherwise.
    """
    if not os.getuid() == 0:
        print("[doctor] WARN: Not root, cannot install system-level service")
        return None

    service_content = f"""[Unit]
Description=CLIProxyAPI Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={shlex.quote(working_dir)}
ExecStart={shlex.quote(binary)} -config {shlex.quote(config)}
Restart=always
RestartSec=10
Environment=HOME=/root

[Install]
WantedBy=multi-user.target
"""

    service_path = "/etc/systemd/system/cliproxyapi.service"
    try:
        Path(service_path).write_text(service_content, encoding="utf-8")
        run_capture(["systemctl", "daemon-reload"], timeout=10)
        run_capture(["systemctl", "enable", "cliproxyapi.service"], timeout=10)
        return "cliproxyapi"
    except Exception as e:
        print(f"[doctor] ERROR: Failed to install system service: {e}")
        return None


# ==================== Management endpoint probe ====================

def probe_management_endpoint(port: int, key: Optional[str] = None) -> Tuple[bool, str]:
    """
    Try to reach the CLIProxyAPI management endpoint.
    Returns (is_reachable, message).
    """
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{port}/v0/management/usage"
    headers = {}
    if key:
        headers["X-Management-Key"] = key

    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status == 200:
            return True, "Management API is reachable and authenticated"
        return False, f"Management API returned {resp.status}"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Management API reachable but authentication failed (wrong key)"
        if e.code == 404:
            return False, "Management API not found (remote-management not enabled or secret-key empty)"
        return False, f"Management API returned HTTP {e.code}"
    except Exception as e:
        return False, f"Cannot reach management API: {e}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-env", action="store_true", help="写入/更新 .env（默认只输出建议）")
    parser.add_argument("--env-path", default=".env", help="env 文件路径（默认 .env）")
    parser.add_argument("--overwrite-existing", action="store_true", help="覆盖已存在的非空配置（默认只补缺失/空值）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于 AI 解析）")
    parser.add_argument("--auto-fix-config", action="store_true", help="自动修复配置问题（auth-dir 路径等）")
    parser.add_argument("--auto-gen-key", action="store_true", help="自动生成管理密钥并写入配置")
    parser.add_argument("--install-system-service", action="store_true", help="安装系统级 systemd 服务（需 root）")
    parser.add_argument("--probe-management", action="store_true", help="探测管理接口是否可达")
    parser.add_argument("--key", help="用于探测的管理密钥（明文）")
    args = parser.parse_args()

    result: Dict[str, str] = {}
    notes: List[str] = []

    # Panel defaults (AI safe)
    result["bind_host"] = "127.0.0.1"
    result["panel_port"] = "8080"
    result["cliproxy_api_base"] = "http://127.0.0.1"

    unit = None
    binary = None
    config_path = None
    working_dir = None
    is_user_service = False

    if has_systemd():
        # 1) Try system-level services first
        units = list_running_services()
        unit = pick_cliproxy_unit(units)

        # 2) Fallback: user-level services (systemctl --user)
        if not unit:
            user_units = list_user_running_services()
            unit = pick_cliproxy_unit_user(user_units)
            if unit:
                is_user_service = True
                if not args.json:
                    print("[doctor] Found user-level service: " + unit)

        # 3) Fallback: check common binary paths
        if not unit:
            binary_fallback = detect_cliproxy_binary_from_common_paths()
            if binary_fallback:
                binary = binary_fallback
                working_dir = os.path.dirname(binary)
                config_path = detect_cliproxy_config_from_binary(binary)
                if not args.json:
                    print("[doctor] No systemd service found, detected binary at: " + binary)
                notes.append("No systemd service detected; using binary discovery path")

        if unit and not binary:
            # Get details from whichever service type we found
            if is_user_service:
                execstart = systemctl_user_value(unit, "ExecStart")
                working_dir = systemctl_user_value(unit, "WorkingDirectory") or None
            else:
                execstart = systemctl_value(unit, "ExecStart")
                working_dir = systemctl_value(unit, "WorkingDirectory") or None

            cmdline = parse_execstart(execstart) or ""
            binary, config_path = extract_config_from_cmdline(cmdline)

            # If no explicit -config flag, look for config.yaml next to binary
            if not config_path and binary:
                config_path = detect_cliproxy_config_from_binary(binary)

            # Service name for panel
            if unit.endswith(".service"):
                result["cliproxy_service"] = unit[:-8]
            else:
                result["cliproxy_service"] = unit

    if binary:
        result["cliproxy_binary"] = binary
    if config_path:
        result["cliproxy_config"] = config_path
        config_detect = detect_from_config(config_path)
        result.update(config_detect)

        # Validate config
        is_valid, config_warnings, config_fixes = validate_config(config_path, auto_fix=args.auto_fix_config)
        if config_warnings:
            for w in config_warnings:
                notes.append("[config] " + w)

        if config_fixes and args.auto_fix_config:
            if apply_config_fixes(config_path, config_fixes):
                notes.append("[config] Fixes applied to " + config_path)
                # Re-detect after fix
                config_detect2 = detect_from_config(config_path)
                result.update(config_detect2)
            else:
                notes.append("[config] Failed to apply fixes")

        # Auto-generate management key if requested
        if args.auto_gen_key:
            mgmt = try_load_yaml(Path(config_path)).get("remote-management", {}) or {}
            secret_key = mgmt.get("secret-key", "")
            if not secret_key or not isinstance(secret_key, str) or not secret_key.strip():
                new_key = generate_management_key()
                notes.append(f"[key] Generated management key: {new_key}")
                notes.append(f"[key] Add to .env: CLIPROXY_PANEL_MANAGEMENT_KEY={new_key}")

                # Try to write into config
                try:
                    import yaml  # type: ignore
                    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
                    if not isinstance(cfg, dict):
                        cfg = {}
                    if "remote-management" not in cfg:
                        cfg["remote-management"] = {}
                    cfg["remote-management"]["secret-key"] = new_key
                    with open(config_path, "w", encoding="utf-8") as f:
                        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, width=120)
                    notes.append("[key] Key written to config.yaml (will be hashed on next CLIProxyAPI start)")
                except Exception as e:
                    notes.append(f"[key] Failed to write key to config: {e}")

    # Install system-level service if requested
    if args.install_system_service and binary and config_path and working_dir:
        if is_user_service or not unit:
            svc_name = install_system_service(binary, config_path, working_dir)
            if svc_name:
                result["cliproxy_service"] = svc_name
                notes.append("[service] Installed system-level systemd service: " + svc_name + ".service")
                notes.append("[service] Start with: systemctl start " + svc_name + ".service")
            else:
                notes.append("[service] Could not install system service (check permissions)")

    auth_dir = result.get("auth_dir")
    log_path = detect_log_path(auth_dir, working_dir)
    if log_path:
        result["cliproxy_log"] = log_path

    if working_dir:
        result["cliproxy_dir"] = working_dir

    # Probe management endpoint
    if args.probe_management:
        port = int(result.get("cliproxy_api_port", "8317"))
        key = args.key
        reachable, msg = probe_management_endpoint(port, key)
        notes.append("[probe] " + msg)
        if not reachable and not key:
            notes.append("[probe] Tip: use --key <MANAGEMENT_KEY> to test authentication")

    # Output results
    if args.json:
        output = {"env": result, "notes": notes}
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for k in sorted(result.keys()):
            print(f"{env_key(k)}={result[k]}")
        if notes:
            print()
            for n in notes:
                print(f"[doctor] {n}")

    if args.write_env:
        env_path = Path(args.env_path)
        upsert_env_file(env_path, result, overwrite_existing=args.overwrite_existing)
        if not args.json:
            print(f"\n[doctor] Written to: {env_path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
