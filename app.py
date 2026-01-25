#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CPA-XX 管理面板后端 v3 (Performance Optimized)
功能: 为 CLIProxyAPI 提供监控统计、健康检查、资源监控、配置管理、API测试、模型管理
优化: 缓存机制、预编译正则、非阻塞监控、减少shell调用
"""

import os
import json
import time
import subprocess
import threading
import re
import platform
import shutil
from datetime import datetime, timedelta
from collections import deque
from functools import lru_cache, wraps
from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS
import requests

# ==================== 预编译正则表达式 ====================
# 日志格式: [2026-01-17 05:21:09] [--------] [info ] [gin_logger.go:92] 200 |            0s |       127.0.0.1 | GET     "/v1/models"
REQUEST_LOG_PATTERN = re.compile(
    r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*\[gin_logger\.go:\d+\]\s+(\d+)\s+\|\s+(\S+)\s+\|([\d\s.]+)\|\s+(\w+)\s+"([^"]+)"'
)
HASH_VERSION_PATTERN = re.compile(r'^[0-9a-f]{7,40}$', re.IGNORECASE)
EXCLUDED_LOG_PATHS = (
    '"/v0/management/usage"',
    '"/v0/management/',
    '"/v1/models"',
)

# 可选依赖
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    print("Warning: psutil not installed. Resource monitoring will be limited.")

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    print("Warning: pyyaml not installed. Config validation will be limited.")

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

# 配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')

CONFIG = {
    'cliproxy_dir': '/opt/CLIProxyAPI',
    'cliproxy_config': '/opt/CLIProxyAPI/config.yaml',
    'cliproxy_binary': '/opt/CLIProxyAPI/cliproxy',
    'cliproxy_log': '/opt/CLIProxyAPI/logs/main.log',  # CLIProxy 主日志
    'cliproxy_stderr': '/var/log/cliproxy/stderr.log',
    'auth_dir': '/opt/CLIProxyAPI/data',
    'cliproxy_service': 'cliproxy',
    'panel_port': 8080,
    'idle_threshold_seconds': 1800,  # 30分钟
    'auto_update_check_interval': 300,
    'auto_update_enabled': True,
    'cliproxy_api_port': 8317,  # CLIProxy API端口
    'cliproxy_api_base': 'http://127.0.0.1',
    'models_api_key': '',
    'management_key': '',
    'usage_snapshot_path': os.path.join(DATA_DIR, 'usage_snapshot.json'),
    'log_stats_path': os.path.join(DATA_DIR, 'log_stats.json'),
    'persistent_stats_path': os.path.join(DATA_DIR, 'persistent_stats.json'),
    'pricing_input': 0.0,
    'pricing_output': 0.0,
    'pricing_cache': 0.0,
    'quotes_path': os.path.join(DATA_DIR, 'quotes.txt'),
    'disk_path': '/',
}

ENV_PREFIX = 'CLIPROXY_PANEL_'

CONFIG_TYPES = {
    'panel_port': int,
    'idle_threshold_seconds': int,
    'auto_update_check_interval': int,
    'auto_update_enabled': bool,
    'cliproxy_api_port': int,
    'pricing_input': float,
    'pricing_output': float,
    'pricing_cache': float,
}


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    value_str = str(value).strip().lower()
    if value_str in {'1', 'true', 'yes', 'on'}:
        return True
    if value_str in {'0', 'false', 'no', 'off'}:
        return False
    return False


def _parse_float(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _load_dotenv():
    env_path = os.path.join(BASE_DIR, '.env')
    if not os.path.exists(env_path):
        return {}
    values = {}
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                values[key] = value
    except Exception as e:
        print(f"Warning: failed to load .env: {e}")
    return values


def _format_env_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _update_dotenv_values(updates):
    env_path = os.path.join(BASE_DIR, '.env')
    env_updates = {f'{ENV_PREFIX}{key.upper()}': _format_env_value(val) for key, val in updates.items()}
    lines = []

    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
        except Exception as e:
            print(f"Warning: failed to read .env: {e}")

    updated = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            new_lines.append(line)
            continue
        key, _ = line.split('=', 1)
        key = key.strip()
        if key in env_updates:
            new_lines.append(f"{key}={env_updates[key]}")
            updated.add(key)
        else:
            new_lines.append(line)

    for key, value in env_updates.items():
        if key not in updated:
            new_lines.append(f"{key}={value}")

    try:
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(new_lines) + '\n')
        return True
    except Exception as e:
        print(f"Warning: failed to save .env: {e}")
        return False


def _apply_overrides(overrides):
    for key, value in overrides.items():
        if key not in CONFIG:
            continue
        caster = CONFIG_TYPES.get(key)
        if caster is None:
            CONFIG[key] = value
            continue
        if caster is bool:
            CONFIG[key] = _parse_bool(value)
        else:
            try:
                CONFIG[key] = caster(value)
            except Exception:
                pass


def load_config_overrides():
    env_overrides = {}
    for key in CONFIG.keys():
        env_key = f'{ENV_PREFIX}{key.upper()}'
        if env_key in os.environ:
            env_overrides[key] = os.environ[env_key]

    dotenv_raw = _load_dotenv()
    dotenv_overrides = {}
    for key in CONFIG.keys():
        env_key = f'{ENV_PREFIX}{key.upper()}'
        if env_key in dotenv_raw:
            dotenv_overrides[key] = dotenv_raw[env_key]

    _apply_overrides(dotenv_overrides)
    _apply_overrides(env_overrides)


load_config_overrides()

UPDATE_HISTORY_PATH = os.path.join(DATA_DIR, 'update_history.json')

# 全局状态
state = {
    'last_request_time': None,
    'request_count': 0,
    'update_in_progress': False,
    'last_update_time': None,
    'last_update_result': None,
    'current_version': 'unknown',
    'latest_version': 'unknown',
    'auto_update_enabled': CONFIG['auto_update_enabled'],
    'request_log': [],
    # 统计数据
    'stats': {
        'total_requests': 0,
        'successful_requests': 0,
        'failed_requests': 0,
        'input_tokens': 0,
        'output_tokens': 0,
        'cached_tokens': 0,
        'total_response_time': 0,
        'requests_per_minute': deque(maxlen=60),
        'requests_per_hour': deque(maxlen=24),
        'model_usage': {},
        'error_types': {},
        'hourly_stats': deque(maxlen=24),
    },
    'last_health_check': None,
    'health_status': 'unknown',
    'log_stats': {
        'initialized': False,
        'offset': 0,
        'last_size': 0,
        'last_mtime': None,
        'total': 0,
        'success': 0,
        'failed': 0,
        'last_time': None,
        'buffer': '',
        'base_total': 0,
        'base_success': 0,
        'base_failed': 0,
        'last_saved_ts': 0
    },
    'log_stats_loaded': False,
}

log_lock = threading.Lock()
log_stats_lock = threading.Lock()
stats_lock = threading.Lock()
persistent_stats_lock = threading.Lock()

# ==================== 持久化统计系统 ====================
PERSISTENT_STATS_FIELDS = (
    'total_requests',
    'successful_requests',
    'failed_requests',
    'input_tokens',
    'output_tokens',
    'cached_tokens',
    'model_usage',
)


def load_persistent_stats():
    """从磁盘加载持久化统计数据"""
    def safe_int(v, default=0):
        try:
            return int(v)
        except:
            return default
    
    path = CONFIG.get('persistent_stats_path')
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        with stats_lock:
            for key in PERSISTENT_STATS_FIELDS:
                if key in data:
                    if key == 'model_usage':
                        state['stats'][key] = data[key] if isinstance(data[key], dict) else {}
                    else:
                        state['stats'][key] = safe_int(data[key])
            # 同步 request_count
            state['request_count'] = state['stats']['total_requests']
        print(f"Loaded persistent stats: {state['stats']['total_requests']} total requests")
        return True
    except Exception as e:
        print(f"Warning: failed to load persistent stats: {e}")
        return False


def save_persistent_stats(force=False):
    """保存统计数据到磁盘"""
    path = CONFIG.get('persistent_stats_path')
    if not path:
        return False
    with persistent_stats_lock:
        now = time.time()
        # 限制保存频率，除非强制保存
        last_saved = getattr(save_persistent_stats, '_last_saved', 0)
        if not force and now - last_saved < 10:
            return False
        save_persistent_stats._last_saved = now
    
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with stats_lock:
            payload = {
                'total_requests': state['stats'].get('total_requests', 0),
                'successful_requests': state['stats'].get('successful_requests', 0),
                'failed_requests': state['stats'].get('failed_requests', 0),
                'input_tokens': state['stats'].get('input_tokens', 0),
                'output_tokens': state['stats'].get('output_tokens', 0),
                'cached_tokens': state['stats'].get('cached_tokens', 0),
                'model_usage': dict(state['stats'].get('model_usage', {})),
                'saved_at': datetime.now().isoformat(),
            }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Warning: failed to save persistent stats: {e}")
        return False


def _persistent_stats_worker():
    """后台线程：定期保存统计数据"""
    while True:
        time.sleep(30)  # 每30秒保存一次
        try:
            save_persistent_stats()
        except Exception as e:
            print(f"Warning: persistent stats worker error: {e}")


def start_persistent_stats_worker():
    """启动持久化统计后台线程"""
    thread = threading.Thread(target=_persistent_stats_worker, daemon=True)
    thread.start()


# ==================== 缓存系统 ====================
class CacheManager:
    """轻量级缓存管理器"""
    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, key, max_age=5):
        """获取缓存值，max_age为秒数"""
        with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                if time.time() - timestamp < max_age:
                    return value
        return None

    def set(self, key, value):
        """设置缓存值"""
        with self._lock:
            self._cache[key] = (value, time.time())


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _build_management_base_url():
    base_url = CONFIG.get('cliproxy_api_base', 'http://127.0.0.1').rstrip('/')
    api_port = CONFIG.get('cliproxy_api_port')
    if api_port:
        base_url = f'{base_url}:{api_port}'
    return base_url


def _management_headers():
    key = CONFIG.get('management_key', '')
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['X-Management-Key'] = key
    return headers


def load_usage_snapshot_from_disk():
    path = CONFIG.get('usage_snapshot_path')
    if not path:
        return None
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Warning: failed to load usage snapshot: {e}")
    return None


def save_usage_snapshot(snapshot):
    path = CONFIG.get('usage_snapshot_path')
    if not path or snapshot is None:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Warning: failed to save usage snapshot: {e}")
        return False


LOG_STATS_PERSIST_FIELDS = (
    'initialized',
    'offset',
    'last_size',
    'last_mtime',
    'total',
    'success',
    'failed',
    'last_time',
    'base_total',
    'base_success',
    'base_failed',
)


def _ensure_parent_dir(path):
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return True
    except Exception as e:
        print(f"Warning: failed to create directory for {path}: {e}")
        return False


def load_log_stats_state():
    path = CONFIG.get('log_stats_path')
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        with log_stats_lock:
            log_state = state.get('log_stats', {}).copy()
            for key in LOG_STATS_PERSIST_FIELDS:
                if key in data:
                    log_state[key] = data[key]
            log_state['buffer'] = ''
            state['log_stats'] = log_state
            state['log_stats_loaded'] = True
        return True
    except Exception as e:
        print(f"Warning: failed to load log stats: {e}")
        return False


def save_log_stats_state(force=False):
    path = CONFIG.get('log_stats_path')
    if not path:
        return False
    with log_stats_lock:
        log_state = state.get('log_stats', {})
        now = time.time()
        last_saved = _safe_float(log_state.get('last_saved_ts', 0), 0.0)
        if not force and now - last_saved < 5:
            return False
        payload = {key: log_state.get(key) for key in LOG_STATS_PERSIST_FIELDS}
        log_state['last_saved_ts'] = now
        state['log_stats'] = log_state
    if not _ensure_parent_dir(path):
        return False
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Warning: failed to save log stats: {e}")
        return False


def fetch_usage_snapshot(use_cache=True):
    cache_key = 'usage_snapshot'
    if use_cache:
        cached = cache.get(cache_key, max_age=2)
        if cached is not None:
            return cached

    base_url = _build_management_base_url()
    url = f'{base_url}/v0/management/usage'
    headers = _management_headers()
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        snapshot = resp.json()
        cache.set(cache_key, snapshot)
        save_usage_snapshot(snapshot)
        return snapshot
    except Exception:
        snapshot = load_usage_snapshot_from_disk()
        if snapshot is not None:
            cache.set(cache_key, snapshot)
        return snapshot


def aggregate_usage_snapshot(snapshot):
    totals = {
        'input_tokens': 0,
        'output_tokens': 0,
        'cached_tokens': 0,
        'total_tokens': 0,
    }
    reqs = {
        'total_requests': 0,
        'success': 0,
        'failure': 0,
    }
    if not snapshot:
        return totals, reqs

    usage = snapshot.get('usage') if isinstance(snapshot, dict) else None
    if not isinstance(usage, dict):
        usage = snapshot if isinstance(snapshot, dict) else {}

    reqs['total_requests'] += _safe_int(usage.get('total_requests', usage.get('total', 0)))
    reqs['success'] += _safe_int(usage.get('success', usage.get('successful_requests', usage.get('success_count', 0))))
    reqs['failure'] += _safe_int(usage.get('failure', usage.get('failed_requests', usage.get('failure_count', 0))))

    def extract_tokens(obj):
        if not isinstance(obj, dict):
            return 0, 0, 0, 0
        tokens = obj.get('tokens') or obj.get('usage') or obj
        input_tokens = _safe_int(tokens.get('input_tokens', tokens.get('input', tokens.get('prompt_tokens', 0))))
        output_tokens = _safe_int(tokens.get('output_tokens', tokens.get('output', tokens.get('completion_tokens', 0))))
        cached_tokens = _safe_int(tokens.get('cached_tokens', tokens.get('cache', 0)))
        total_tokens = _safe_int(tokens.get('total_tokens', tokens.get('total', obj.get('total_tokens', 0))))
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens + cached_tokens
        return input_tokens, output_tokens, cached_tokens, total_tokens

    apis = usage.get('apis', [])
    if isinstance(apis, dict):
        apis = list(apis.values())
    if not isinstance(apis, list):
        apis = []

    for api in apis:
        if not isinstance(api, dict):
            continue
        reqs['total_requests'] += _safe_int(api.get('total_requests', api.get('total', api.get('requests', 0))))
        reqs['success'] += _safe_int(api.get('success', api.get('successful_requests', api.get('success_count', 0))))
        reqs['failure'] += _safe_int(api.get('failure', api.get('failed_requests', api.get('failure_count', 0))))

        models = api.get('models', [])
        if isinstance(models, dict):
            models = list(models.values())
        if not isinstance(models, list):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            details = model.get('details')
            if isinstance(details, list) and details:
                for detail in details:
                    input_tokens, output_tokens, cached_tokens, total_tokens = extract_tokens(detail)
                    totals['input_tokens'] += input_tokens
                    totals['output_tokens'] += output_tokens
                    totals['cached_tokens'] += cached_tokens
                    totals['total_tokens'] += total_tokens
            else:
                input_tokens, output_tokens, cached_tokens, total_tokens = extract_tokens(model)
                totals['input_tokens'] += input_tokens
                totals['output_tokens'] += output_tokens
                totals['cached_tokens'] += cached_tokens
                totals['total_tokens'] += total_tokens

    if totals['total_tokens'] == 0:
        totals['total_tokens'] = _safe_int(usage.get('total_tokens', 0))

    return totals, reqs


def compute_usage_costs(tokens, pricing):
    input_price = _safe_float(pricing.get('input', 0.0))
    output_price = _safe_float(pricing.get('output', 0.0))
    cache_price = _safe_float(pricing.get('cache', 0.0))

    input_cost = tokens.get('input_tokens', 0) / 1_000_000 * input_price
    output_cost = tokens.get('output_tokens', 0) / 1_000_000 * output_price
    cache_cost = tokens.get('cached_tokens', 0) / 1_000_000 * cache_price
    total_cost = input_cost + output_cost + cache_cost

    return {
        'input': input_cost,
        'output': output_cost,
        'cache': cache_cost,
        'total': total_cost,
    }


def import_usage_snapshot(snapshot):
    if not snapshot:
        return False
    base_url = _build_management_base_url()
    url = f'{base_url}/v0/management/usage/import'
    headers = _management_headers()
    try:
        resp = requests.post(url, headers=headers, json=snapshot, timeout=8)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Warning: usage import failed: {e}")
        return False


def _usage_snapshot_worker():
    snapshot = load_usage_snapshot_from_disk()
    if snapshot:
        import_usage_snapshot(snapshot)
    while True:
        try:
            fetch_usage_snapshot(use_cache=False)
        except Exception:
            pass
        time.sleep(60)


def start_usage_snapshot_worker():
    thread = threading.Thread(target=_usage_snapshot_worker, daemon=True)
    thread.start()


def _read_file_first_line(path):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.readline().strip()
    except Exception:
        pass
    return None


def get_system_info():
    info = {
        'cpu_model': None,
        'os_version': None,
        'cloud_vendor': None,
    }
    if is_linux():
        try:
            with open('/proc/cpuinfo', 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if 'model name' in line:
                        info['cpu_model'] = line.split(':', 1)[-1].strip()
                        break
        except Exception:
            pass

        try:
            with open('/etc/os-release', 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        info['os_version'] = line.split('=', 1)[-1].strip().strip('"')
                        break
        except Exception:
            pass

        vendor = _read_file_first_line('/sys/class/dmi/id/sys_vendor')
        product = _read_file_first_line('/sys/class/dmi/id/product_name')
        if vendor or product:
            info['cloud_vendor'] = ' '.join([v for v in [vendor, product] if v])

    info['cpu_model'] = info['cpu_model'] or platform.processor() or 'unknown'
    info['os_version'] = info['os_version'] or platform.platform()
    info['cloud_vendor'] = info['cloud_vendor'] or 'unknown'
    return info


def get_cliproxy_process_usage():
    if not HAS_PSUTIL:
        return {'cpu_percent': 0.0, 'memory_bytes': 0, 'memory_percent': 0.0}
    target = CONFIG.get('cliproxy_service', 'cliproxy')
    cpu_percent = 0.0
    memory_bytes = 0
    memory_percent = 0.0
    try:
        for proc in psutil.process_iter(['name', 'cmdline', 'memory_info', 'memory_percent']):
            name = (proc.info.get('name') or '').lower()
            cmdline = ' '.join(proc.info.get('cmdline') or []).lower()
            if target in name or target in cmdline:
                try:
                    cpu_percent = proc.cpu_percent(interval=0.0)
                    mem_info = proc.info.get('memory_info')
                    if mem_info:
                        memory_bytes = getattr(mem_info, 'rss', 0)
                    memory_percent = _safe_float(proc.info.get('memory_percent', 0.0))
                    break
                except Exception:
                    continue
    except Exception:
        pass
    return {
        'cpu_percent': cpu_percent,
        'memory_bytes': memory_bytes,
        'memory_percent': memory_percent,
    }


def _normalize_quote_text(text):
    if not text:
        return text
    has_en = any('A' <= ch <= 'Z' or 'a' <= ch <= 'z' for ch in text)
    has_cn = any('\u4e00' <= ch <= '\u9fff' for ch in text)
    if has_en and has_cn and '（' in text and '）' in text:
        prefix, rest = text.split('（', 1)
        inside, suffix = rest.split('）', 1)
        prefix = prefix.strip()
        inside = inside.strip()
        if prefix and inside:
            prefix_has_en = any('A' <= ch <= 'Z' or 'a' <= ch <= 'z' for ch in prefix)
            inside_has_en = any('A' <= ch <= 'Z' or 'a' <= ch <= 'z' for ch in inside)
            if not prefix_has_en and inside_has_en:
                return f"{inside}（{prefix}）{suffix}".strip()
    return text.strip()


def load_quotes():
    path = CONFIG.get('quotes_path')
    if not path or not os.path.exists(path):
        return []
    quotes = []
    seen = set()
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        markers = list(re.finditer('出自：', content))
        if not markers:
            return []
        last_end = 0
        for idx, marker in enumerate(markers):
            quote = content[last_end:marker.start()].strip()
            next_marker_pos = markers[idx + 1].start() if idx + 1 < len(markers) else len(content)
            author_block = content[marker.end():next_marker_pos]
            author_line = author_block.split('\n', 1)[0].strip()
            if len(author_line) > 80:
                cut_positions = [author_line.find(p) for p in ['。', '！', '？', '!', '?', '；', ';']]
                cut_positions = [p for p in cut_positions if p != -1]
                if cut_positions:
                    author_line = author_line[:min(cut_positions)].strip()
            quote = _normalize_quote_text(quote)
            if quote and author_line:
                key = f"{quote}||{author_line}"
                if key not in seen:
                    seen.add(key)
                    quotes.append({'text': quote, 'author': author_line})
            last_end = marker.end() + len(author_line)
    except Exception as e:
        print(f"Warning: failed to load quotes: {e}")
    return quotes


def get_random_quote():
    cached = cache.get('quotes_cache', max_age=30)
    if cached is None:
        cached = load_quotes()
        cache.set('quotes_cache', cached)
    if not cached:
        return {'text': '欢迎回来，祝你今天高效完成任务。', 'author': '系统'}
    import random
    return random.choice(cached)

    def invalidate(self, key=None):
        """使缓存失效"""
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()

cache = CacheManager()

# ==================== 后台资源监控 ====================
class ResourceMonitor:
    """非阻塞资源监控器"""
    def __init__(self):
        self._cpu_percent = 0.0
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        """启动后台监控线程"""
        if self._running:
            return
        self._running = True
        thread = threading.Thread(target=self._monitor_loop, daemon=True)
        thread.start()

    def _monitor_loop(self):
        """后台监控循环"""
        while self._running:
            try:
                if HAS_PSUTIL:
                    cpu = psutil.cpu_percent(interval=1)  # 1秒采样
                    with self._lock:
                        self._cpu_percent = cpu
            except:
                pass
            time.sleep(2)  # 每3秒更新一次(1秒采样+2秒等待)

    def get_cpu_percent(self):
        """获取CPU使用率（非阻塞）"""
        with self._lock:
            return self._cpu_percent

resource_monitor = ResourceMonitor()

def run_cmd(cmd, timeout=60):
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', 'Command timed out'
    except Exception as e:
        return False, '', str(e)


def is_linux():
    return platform.system().lower() == 'linux'


def command_available(command):
    return shutil.which(command) is not None

def get_service_status(use_cache=True):
    """获取服务状态（带缓存）"""
    cache_key = 'service_status'
    if use_cache:
        cached = cache.get(cache_key, max_age=1)
        if cached:
            return cached

    status_out = ''
    pid_out = ''
    is_running = False

    if is_linux() and command_available('systemctl'):
        success, stdout, _ = run_cmd(f'systemctl is-active {CONFIG["cliproxy_service"]}')
        is_running = success and stdout == 'active'
        _, status_out, _ = run_cmd(f'systemctl status {CONFIG["cliproxy_service"]} --no-pager -l 2>/dev/null | head -20')
    else:
        status_out = 'Not supported on this platform'

    if command_available('pgrep'):
        _, pid_out, _ = run_cmd('pgrep -f "cliproxy -config" | head -1')

    memory = 'N/A'
    cpu = 'N/A'
    uptime = 'N/A'

    if pid_out:
        if HAS_PSUTIL:
            try:
                proc = psutil.Process(int(pid_out))
                memory = f'{proc.memory_info().rss / 1024 / 1024:.1f} MB'
                # 使用后台监控的CPU数据，避免阻塞
                cpu = f'{resource_monitor.get_cpu_percent():.1f}%'
                uptime_seconds = time.time() - proc.create_time()
                uptime = format_uptime(uptime_seconds)
            except:
                pass
        elif command_available('ps'):
            _, mem_out, _ = run_cmd(f'ps -o rss= -p {pid_out}')
            if mem_out:
                try:
                    memory = f'{int(mem_out) / 1024:.1f} MB'
                except:
                    pass

    result = {
        'running': is_running,
        'status': 'running' if is_running else 'stopped',
        'pid': pid_out if pid_out else None,
        'memory': memory,
        'cpu': cpu,
        'uptime': uptime,
        'details': status_out
    }

    cache.set(cache_key, result)
    return result

def format_uptime(seconds):
    if seconds < 60:
        return f'{int(seconds)}秒'
    elif seconds < 3600:
        return f'{int(seconds/60)}分钟'
    elif seconds < 86400:
        hours = int(seconds / 3600)
        mins = int((seconds % 3600) / 60)
        return f'{hours}小时{mins}分'
    else:
        days = int(seconds / 86400)
        hours = int((seconds % 86400) / 3600)
        return f'{days}天{hours}小时'


def get_github_release_version():
    """从GitHub releases获取最新版本号（带缓存）"""
    cache_key = 'github_release'
    cached = cache.get(cache_key, max_age=300)
    if cached:
        return cached

    try:
        import urllib.request
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            'https://api.github.com/repos/router-for-me/CLIProxyAPI/releases/latest',
            headers={'User-Agent': 'CLIProxyPanel'}
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            version = data.get('tag_name', 'unknown')
            cache.set(cache_key, version)
            return version
    except Exception as e:
        print(f'get_github_release_version error: {e}')
        return 'unknown'

def get_local_version():
    """获取本地版本号"""
    cache_key = 'local_version'
    cached = cache.get(cache_key, max_age=30)
    if cached:
        return cached

    # 确保本地有最新的 tag 信息
    run_cmd(f'cd {CONFIG["cliproxy_dir"]} && git fetch --tags 2>/dev/null', timeout=10)

    version_file = os.path.join(CONFIG['cliproxy_dir'], 'VERSION')
    if os.path.exists(version_file):
        try:
            with open(version_file, 'r') as f:
                version = f.read().strip()
                if version:
                    cache.set(cache_key, version)
                    return version
        except:
            pass

    _, stdout, _ = run_cmd(f'cd {CONFIG["cliproxy_dir"]} && git describe --tags --abbrev=0 2>/dev/null')
    if stdout:
        cache.set(cache_key, stdout)
        return stdout

    _, stdout, _ = run_cmd(f'cd {CONFIG["cliproxy_dir"]} && git rev-parse --short HEAD')
    result = stdout if stdout else 'dev'
    cache.set(cache_key, result)
    return result

def _reset_log_stats_state():
    with log_stats_lock:
        state['log_stats'] = {
            'initialized': False,
            'offset': 0,
            'last_size': 0,
            'last_mtime': None,
            'total': 0,
            'success': 0,
            'failed': 0,
            'last_time': None,
            'buffer': '',
            'base_total': 0,
            'base_success': 0,
            'base_failed': 0,
            'last_saved_ts': 0
        }
    save_log_stats_state(force=True)


def read_log_tail(log_file, max_lines=100, chunk_size=4096):
    """尾部读取日志，避免全量读取"""
    if not os.path.exists(log_file):
        return []
    if max_lines <= 0:
        return []

    try:
        with open(log_file, 'rb') as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            remaining = file_size
            data = b''
            while remaining > 0 and data.count(b'\n') <= max_lines:
                read_size = chunk_size if remaining >= chunk_size else remaining
                remaining -= read_size
                f.seek(remaining)
                data = f.read(read_size) + data
            text = data.decode('utf-8', errors='ignore')
            return text.splitlines()[-max_lines:]
    except Exception:
        return []


def get_request_count_from_logs():
    """从日志获取请求统计（增量解析）"""
    cache_key = 'request_count_logs'
    cached = cache.get(cache_key, max_age=2)
    if cached:
        return cached

    if not state.get('log_stats_loaded'):
        load_log_stats_state()

    log_file = CONFIG['cliproxy_log']
    if not os.path.exists(log_file):
        with log_stats_lock:
            log_state = state.get('log_stats', {})
            result = {
                'count': _safe_int(log_state.get('base_total', 0)),
                'last_time': log_state.get('last_time'),
                'success': _safe_int(log_state.get('base_success', 0)),
                'failed': _safe_int(log_state.get('base_failed', 0))
            }
        cache.set(cache_key, result)
        return result

    try:
        stat = os.stat(log_file)
        file_size = stat.st_size
        mtime = stat.st_mtime
    except Exception:
        result = {'count': 0, 'last_time': None, 'success': 0, 'failed': 0}
        cache.set(cache_key, result)
        return result

    needs_save = False
    with log_stats_lock:
        log_state = state.get('log_stats', {})
        initialized = log_state.get('initialized')
        last_size = log_state.get('last_size', 0)
        last_mtime = log_state.get('last_mtime')
        offset = log_state.get('offset', 0)

        rotated = False
        if not initialized:
            rotated = True
        elif file_size < last_size:
            rotated = True
        elif last_mtime and mtime < last_mtime:
            rotated = True

        if rotated:
            if log_state.get('initialized'):
                log_state['base_total'] = _safe_int(log_state.get('base_total', 0)) + _safe_int(log_state.get('total', 0))
                log_state['base_success'] = _safe_int(log_state.get('base_success', 0)) + _safe_int(log_state.get('success', 0))
                log_state['base_failed'] = _safe_int(log_state.get('base_failed', 0)) + _safe_int(log_state.get('failed', 0))
            offset = 0
            log_state['buffer'] = ''
            log_state['total'] = 0
            log_state['success'] = 0
            log_state['failed'] = 0
            log_state['last_time'] = None
        changed = rotated

        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                if offset:
                    f.seek(offset)
                new_data = f.read()
                new_offset = f.tell()
        except Exception:
            result = {'count': 0, 'last_time': None, 'success': 0, 'failed': 0}
            cache.set(cache_key, result)
            return result

        buffer = log_state.get('buffer', '') + new_data
        lines = buffer.splitlines(keepends=True)
        if lines and not lines[-1].endswith('\n'):
            log_state['buffer'] = lines[-1]
            lines = lines[:-1]
        else:
            log_state['buffer'] = ''

        for line in lines:
            if '[gin_logger.go' in line and ('POST' in line or 'GET' in line):
                if any(path in line for path in EXCLUDED_LOG_PATHS):
                    continue
                log_state['total'] += 1
                match = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
                if match:
                    log_state['last_time'] = match.group(1)
                status_match = re.search(r'\s(\d{3})\s', line)
                if status_match:
                    code = int(status_match.group(1))
                    if 200 <= code < 300:
                        log_state['success'] += 1
                    elif code >= 400:
                        log_state['failed'] += 1
                changed = True

        log_state['initialized'] = True
        log_state['offset'] = new_offset
        log_state['last_size'] = file_size
        log_state['last_mtime'] = mtime
        state['log_stats'] = log_state

        needs_save = changed

        result = {
            'count': _safe_int(log_state.get('base_total', 0)) + _safe_int(log_state.get('total', 0)),
            'last_time': log_state['last_time'],
            'success': _safe_int(log_state.get('base_success', 0)) + _safe_int(log_state.get('success', 0)),
            'failed': _safe_int(log_state.get('base_failed', 0)) + _safe_int(log_state.get('failed', 0))
        }
        cache.set(cache_key, result)

    if needs_save:
        save_log_stats_state()
    log_stats_path = CONFIG.get('log_stats_path')
    if log_stats_path and not os.path.exists(log_stats_path):
        save_log_stats_state(force=True)
    return result


def resolve_version_label(version):
    if not version:
        return version
    version_str = str(version).strip()
    if not HASH_VERSION_PATTERN.match(version_str):
        return version_str
    if not command_available('git'):
        return version_str
    _, tags_out, _ = run_cmd(
        f'cd {CONFIG["cliproxy_dir"]} && git tag --contains {version_str}',
        timeout=10
    )
    if not tags_out:
        return version_str
    tags = [t.strip() for t in tags_out.splitlines() if t.strip()]
    if not tags:
        return version_str
    def parse_version_key(tag):
        cleaned = tag.lstrip('vV')
        parts = re.split(r'[^0-9]+', cleaned)
        nums = [int(p) for p in parts if p.isdigit()]
        return nums or [0]
    tags.sort(key=parse_version_key)
    return tags[-1]


def get_current_commit():
    """获取当前commit（带缓存）"""
    cache_key = 'current_commit'
    cached = cache.get(cache_key, max_age=30)
    if cached:
        return cached
    if not command_available('git'):
        cache.set(cache_key, 'unknown')
        return 'unknown'
    _, stdout, _ = run_cmd(f'cd {CONFIG["cliproxy_dir"]} && git rev-parse --short HEAD')
    result = stdout if stdout else 'unknown'
    cache.set(cache_key, result)
    return result

def get_latest_commit():
    """获取最新commit（带缓存，减少网络请求）"""
    cache_key = 'latest_commit'
    cached = cache.get(cache_key, max_age=120)  # 2分钟缓存
    if cached:
        return cached
    if not command_available('git'):
        cache.set(cache_key, 'unknown')
        return 'unknown'
    run_cmd(f'cd {CONFIG["cliproxy_dir"]} && git fetch origin main --quiet', timeout=10)
    _, stdout, _ = run_cmd(f'cd {CONFIG["cliproxy_dir"]} && git rev-parse --short origin/main')
    result = stdout if stdout else 'unknown'
    cache.set(cache_key, result)
    return result

def check_for_updates(use_cache=True):
    """检查更新（使用GitHub releases）"""
    cache_key = 'update_check'
    if use_cache:
        cached = cache.get(cache_key, max_age=60)
        if cached is not None:
            return cached

    current = get_local_version()
    latest = get_github_release_version()
    state['current_version'] = current
    state['latest_version'] = latest
    result = current != latest and latest != 'unknown' and current != 'unknown'
    cache.set(cache_key, result)
    return result

def is_idle():
    """检查系统是否空闲（基于日志中的最后请求时间）"""
    # 从日志获取最后请求时间
    stats = get_request_count_from_logs()
    last_time_str = stats.get('last_time')

    if not last_time_str:
        return True  # 没有请求记录，认为空闲

    try:
        # 解析时间字符串 "2026-01-18 23:56:20"
        last_time = datetime.strptime(last_time_str, '%Y-%m-%d %H:%M:%S')
        # 服务器使用UTC时间，计算空闲秒数
        idle_seconds = (datetime.utcnow() - last_time).total_seconds()
        return idle_seconds > CONFIG['idle_threshold_seconds']
    except:
        return True  # 解析失败，认为空闲

def perform_update():
    if state['update_in_progress']:
        return False, 'Update already in progress'

    if not (is_linux() and command_available('systemctl')):
        return False, {'success': False, 'message': 'Update only supported on Linux with systemd', 'details': []}

    state['update_in_progress'] = True
    result = {'success': False, 'message': '', 'details': []}

    try:
        result['details'].append('Stopping service...')
        run_cmd(f'systemctl stop {CONFIG["cliproxy_service"]}')
        time.sleep(2)

        result['details'].append('Pulling latest code...')
        success, stdout, stderr = run_cmd(f'cd {CONFIG["cliproxy_dir"]} && git fetch --tags && git pull origin main')
        if not success:
            result['message'] = f'Pull failed: {stderr}'
            return False, result
        result['details'].append(stdout)

        result['details'].append('Rebuilding...')
        success, stdout, stderr = run_cmd(
            f'cd {CONFIG["cliproxy_dir"]} && go build -o cliproxy ./cmd/server',
            timeout=300
        )
        if not success:
            result['message'] = f'Build failed: {stderr}'
            run_cmd(f'systemctl start {CONFIG["cliproxy_service"]}')
            return False, result
        result['details'].append('Build successful')

        result['details'].append('Starting service...')
        success, _, stderr = run_cmd(f'systemctl start {CONFIG["cliproxy_service"]}')
        if not success:
            result['message'] = f'Start failed: {stderr}'
            return False, result

        time.sleep(2)

        status = get_service_status()
        if not status['running']:
            result['message'] = 'Service not running after start'
            return False, result

        result['success'] = True
        result['message'] = 'Update successful'
        result['details'].append('Service is running')

        state['last_update_time'] = datetime.now().isoformat()
        state['last_update_result'] = result
        state['current_version'] = get_local_version()

        # 记录更新历史
        try:
            record_update_history(state['current_version'])
        except Exception as e:
            print(f"Failed to record update history: {e}")
        # 清除版本缓存
        try:
            cache._cache.pop('local_version', None)
            cache._cache.pop('github_release', None)
            cache._cache.pop('update_check', None)
        except:
            pass

        return True, result

    except Exception as e:
        result['message'] = f'Update error: {str(e)}'
        run_cmd(f'systemctl start {CONFIG["cliproxy_service"]}')
        return False, result
    finally:
        state['update_in_progress'] = False

def auto_update_worker():
    while True:
        time.sleep(CONFIG['auto_update_check_interval'])

        if not state['auto_update_enabled']:
            continue

        if state['update_in_progress']:
            continue

        try:
            has_update = check_for_updates()
            if has_update and is_idle():
                print(f'[{datetime.now()}] Update detected and system idle, starting auto-update...')
                perform_update()
        except Exception as e:
            print(f'[{datetime.now()}] Auto-update check failed: {e}')

def parse_log_file(log_file, max_lines=100):
    """解析日志文件（优化：Python原生读取，提取实际时间戳）"""
    if not os.path.exists(log_file):
        return []

    # 匹配日志时间格式: [2026-01-18 23:48:53]
    time_pattern = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')

    try:
        lines = read_log_tail(log_file, max_lines=max_lines)

        logs = []
        for line in lines:
            line = line.strip()
            if line:
                # 尝试从日志中提取时间
                time_match = time_pattern.search(line)
                if time_match:
                    # 解析日志中的时间（服务器是UTC）
                    log_time_str = time_match.group(1)
                    try:
                        log_time = datetime.strptime(log_time_str, '%Y-%m-%d %H:%M:%S')
                        # 标记为UTC时间
                        time_iso = log_time.isoformat() + 'Z'
                    except:
                        time_iso = datetime.utcnow().isoformat() + 'Z'
                else:
                    time_iso = datetime.utcnow().isoformat() + 'Z'

                logs.append({
                    'time': time_iso,
                    'message': line[:500]
                })

        return logs[-50:]
    except:
        return []

def parse_request_logs(max_lines=200, use_cache=True):
    """解析 CLIProxy 请求日志（优化：预编译正则+缓存+原生读取）"""
    cache_key = 'request_logs'
    empty_stats = {'total': 0, 'success': 0, 'failed': 0}

    if use_cache:
        cached = cache.get(cache_key, max_age=2)
        if cached:
            return cached

    log_file = CONFIG['cliproxy_log']

    if not os.path.exists(log_file):
        return [], empty_stats

    try:
        lines = read_log_tail(log_file, max_lines=max_lines)

        logs = []
        # 使用预编译的正则表达式
        for line in lines:
            match = REQUEST_LOG_PATTERN.search(line)
            if match:
                timestamp, status, duration, client_ip, method, path = match.groups()
                client_ip = client_ip.strip()
                logs.append({
                    'time': timestamp,
                    'status': int(status),
                    'duration': duration,
                    'client': client_ip,
                    'method': method,
                    'path': path,
                    'message': f'{method} {path} - {status} ({duration})'
                })

        # 统计
        total = len(logs)
        success = sum(1 for l in logs if l['status'] < 400)
        failed = total - success

        result = (logs[-50:], {'total': total, 'success': success, 'failed': failed})
        cache.set(cache_key, result)
        return result
    except Exception as e:
        print(f'parse_request_logs error: {e}')
        return [], empty_stats

def get_paths_info():
    return {
        'config': CONFIG['cliproxy_config'],
        'auth_dir': CONFIG['auth_dir'],
        'binary': CONFIG['cliproxy_binary'],
        'logs': os.path.dirname(CONFIG['cliproxy_log']),
        'project_dir': CONFIG['cliproxy_dir']
    }

def load_cliproxy_config(use_cache=True):
    """加载CLIProxy配置文件（优化：带缓存）"""
    cache_key = 'cliproxy_config'
    if use_cache:
        cached = cache.get(cache_key, max_age=30)
        if cached:
            return cached

    config_path = CONFIG['cliproxy_config']
    if not os.path.exists(config_path):
        return None, 'Config file not found'

    if not HAS_YAML:
        # 没有yaml模块时返回原始内容
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                result = ({'_raw': f.read()}, None)
                cache.set(cache_key, result)
                return result
        except Exception as e:
            return None, str(e)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            result = (config, None)
            cache.set(cache_key, result)
            return result
    except Exception as e:
        return None, str(e)

def validate_yaml_config(content):
    """验证YAML配置格式"""
    if not HAS_YAML:
        return {
            'valid': True,
            'errors': [],
            'warnings': ['pyyaml未安装，无法进行深度验证'],
            'config': None
        }

    try:
        config = yaml.safe_load(content)
        errors = []
        warnings = []

        # 基本结构检查
        if not isinstance(config, dict):
            errors.append('配置必须是一个字典/对象')
            return {'valid': False, 'errors': errors, 'warnings': warnings}

        # 检查必需字段
        required_fields = ['port']
        for field in required_fields:
            if field not in config:
                errors.append(f'缺少必需字段: {field}')

        # 检查端口
        if 'port' in config:
            port = config['port']
            if not isinstance(port, int) or port < 1 or port > 65535:
                errors.append('端口必须是1-65535之间的整数')

        # 检查providers
        if 'providers' in config:
            if not isinstance(config['providers'], list):
                errors.append('providers必须是一个数组')
            else:
                for i, provider in enumerate(config['providers']):
                    if not isinstance(provider, dict):
                        errors.append(f'provider[{i}] 必须是一个对象')
                        continue
                    if 'name' not in provider:
                        warnings.append(f'provider[{i}] 缺少name字段')
                    if 'type' not in provider:
                        warnings.append(f'provider[{i}] 缺少type字段')

        # 检查路由策略
        if 'routing' in config:
            valid_strategies = ['round-robin', 'fill-first']
            strategy = config['routing'].get('strategy', '')
            if strategy and strategy not in valid_strategies:
                warnings.append(f'未知的路由策略: {strategy}，有效值: {", ".join(valid_strategies)}')

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'config': config if len(errors) == 0 else None
        }
    except yaml.YAMLError as e:
        return {
            'valid': False,
            'errors': [f'YAML解析错误: {str(e)}'],
            'warnings': []
        }

def get_system_resources(use_cache=True):
    """获取系统资源（优化：非阻塞CPU+缓存）"""
    cache_key = 'system_resources'
    if use_cache:
        cached = cache.get(cache_key, max_age=2)
        if cached:
            return cached

    disk_path = CONFIG.get('disk_path') or '/'
    system_info = get_system_info()
    cliproxy_usage = get_cliproxy_process_usage()

    if not HAS_PSUTIL:
        # 没有psutil时使用命令行获取基本信息
        resources = {
            'cpu': {'percent': 0, 'cores': 1},
            'memory': {'total': 0, 'used': 0, 'percent': 0, 'available': 0},
            'disk': {'total': 0, 'used': 0, 'percent': 0, 'free': 0, 'path': disk_path},
            'network': {'bytes_sent': 0, 'bytes_recv': 0},
            'system': system_info,
            'cliproxy': cliproxy_usage,
            'timestamp': datetime.now().isoformat(),
            'limited': True
        }

        # 尝试获取内存信息（Linux）
        if is_linux() and command_available('free'):
            _, mem_out, _ = run_cmd('free -b 2>/dev/null | grep Mem')
            if mem_out:
                parts = mem_out.split()
                if len(parts) >= 4:
                    try:
                        total = int(parts[1])
                        used = int(parts[2])
                        resources['memory']['total'] = total
                        resources['memory']['used'] = used
                        resources['memory']['available'] = total - used
                        resources['memory']['percent'] = round(used / total * 100, 1) if total > 0 else 0
                    except:
                        pass

        # 尝试获取磁盘信息（Linux）
        try:
            usage = shutil.disk_usage(disk_path)
            total = usage.total
            used = usage.used
            resources['disk']['total'] = total
            resources['disk']['used'] = used
            resources['disk']['free'] = usage.free
            resources['disk']['percent'] = round(used / total * 100, 1) if total > 0 else 0
        except Exception:
            if is_linux() and command_available('df'):
                _, disk_out, _ = run_cmd(f'df {disk_path} 2>/dev/null | tail -1')
                if disk_out:
                    parts = disk_out.split()
                    if len(parts) >= 5:
                        try:
                            total = int(parts[1]) * 1024
                            used = int(parts[2]) * 1024
                            resources['disk']['total'] = total
                            resources['disk']['used'] = used
                            resources['disk']['free'] = total - used
                            resources['disk']['percent'] = round(used / total * 100, 1) if total > 0 else 0
                        except Exception:
                            pass

        cache.set(cache_key, resources)
        return resources

    try:
        # 使用后台监控的CPU数据，避免阻塞
        cpu_percent = resource_monitor.get_cpu_percent()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(disk_path)

        # 网络IO
        net_io = psutil.net_io_counters()

        # 获取更详细的CPU信息
        cpu_freq = psutil.cpu_freq()
        cpu_times = psutil.cpu_times_percent(interval=0)
        per_cpu = psutil.cpu_percent(percpu=True)

        # 获取更详细的内存信息
        swap = psutil.swap_memory()

        # 获取系统负载（Linux）
        try:
            load_avg = psutil.getloadavg()
        except:
            load_avg = (0, 0, 0)

        # 获取进程数
        try:
            process_count = len(psutil.pids())
        except:
            process_count = 0

        result = {
            'cpu': {
                'percent': cpu_percent,
                'cores': psutil.cpu_count(),
                'cores_logical': psutil.cpu_count(logical=True),
                'cores_physical': psutil.cpu_count(logical=False) or psutil.cpu_count(),
                'freq_current': cpu_freq.current if cpu_freq else 0,
                'freq_max': cpu_freq.max if cpu_freq and cpu_freq.max else 0,
                'per_cpu': per_cpu,
                'user': cpu_times.user if cpu_times else 0,
                'system': cpu_times.system if cpu_times else 0,
                'idle': cpu_times.idle if cpu_times else 0,
                'iowait': getattr(cpu_times, 'iowait', 0),
                'load_1m': round(load_avg[0], 2),
                'load_5m': round(load_avg[1], 2),
                'load_15m': round(load_avg[2], 2),
                'process_count': process_count,
            },
            'memory': {
                'total': memory.total,
                'used': memory.used,
                'percent': memory.percent,
                'available': memory.available,
                'free': memory.free,
                'cached': getattr(memory, 'cached', 0),
                'buffers': getattr(memory, 'buffers', 0),
                'shared': getattr(memory, 'shared', 0),
                'swap_total': swap.total,
                'swap_used': swap.used,
                'swap_percent': swap.percent,
                'swap_free': swap.free,
            },
            'disk': {
                'total': disk.total,
                'used': disk.used,
                'percent': round(disk.used / disk.total * 100, 1) if disk.total > 0 else 0,
                'free': disk.free,
                'path': disk_path,
            },
            'network': {
                'bytes_sent': net_io.bytes_sent,
                'bytes_recv': net_io.bytes_recv,
            },
            'system': system_info,
            'cliproxy': cliproxy_usage,
            'timestamp': datetime.now().isoformat()
        }
        cache.set(cache_key, result)
        return result
    except Exception as e:
        return {'error': str(e)}

def perform_health_check(use_cache=True):
    """执行健康检查（优化：带缓存）"""
    cache_key = 'health_check'
    if use_cache:
        cached = cache.get(cache_key, max_age=10)
        if cached:
            return cached

    results = {
        'timestamp': datetime.now().isoformat(),
        'checks': [],
        'checks_map': {},
        'overall': 'healthy'
    }

    # 1. 服务状态检查
    service = get_service_status()
    service_check = {
        'name': '服务状态',
        'status': 'pass' if service['running'] else 'fail',
        'message': '服务运行中' if service['running'] else '服务未运行',
        'details': service
    }
    results['checks'].append(service_check)
    results['checks_map']['service'] = service_check

    # 2. 配置文件检查
    config, error = load_cliproxy_config()
    config_check = {
        'name': '配置文件',
        'status': 'pass' if config else 'fail',
        'message': '配置文件有效' if config else f'配置错误: {error}'
    }
    results['checks'].append(config_check)
    results['checks_map']['config'] = config_check

    # 3. 磁盘空间检查
    if HAS_PSUTIL:
        try:
            disk = psutil.disk_usage('/')
            disk_ok = disk.percent < 90
            disk_check = {
                'name': '磁盘空间',
                'status': 'pass' if disk_ok else 'warn',
                'message': f'已使用 {disk.percent}%',
                'details': {'percent': disk.percent}
            }
            results['checks'].append(disk_check)
            results['checks_map']['disk'] = disk_check
        except:
            disk_check = {
                'name': '磁盘空间',
                'status': 'unknown',
                'message': '无法获取磁盘信息'
            }
            results['checks'].append(disk_check)
            results['checks_map']['disk'] = disk_check
    else:
        # 使用df命令获取磁盘信息（Linux）
        if is_linux() and command_available('df'):
            _, disk_out, _ = run_cmd('df / 2>/dev/null | tail -1')
            if disk_out:
                parts = disk_out.split()
                if len(parts) >= 5:
                    try:
                        percent = int(parts[4].replace('%', ''))
                        disk_ok = percent < 90
                        disk_check = {
                            'name': '磁盘空间',
                            'status': 'pass' if disk_ok else 'warn',
                            'message': f'已使用 {percent}%',
                            'details': {'percent': percent}
                        }
                        results['checks'].append(disk_check)
                        results['checks_map']['disk'] = disk_check
                    except:
                        disk_check = {
                            'name': '磁盘空间',
                            'status': 'unknown',
                            'message': '无法获取磁盘信息'
                        }
                        results['checks'].append(disk_check)
                        results['checks_map']['disk'] = disk_check
                else:
                    disk_check = {
                        'name': '磁盘空间',
                        'status': 'unknown',
                        'message': '无法获取磁盘信息'
                    }
                    results['checks'].append(disk_check)
                    results['checks_map']['disk'] = disk_check
            else:
                disk_check = {
                    'name': '磁盘空间',
                    'status': 'unknown',
                    'message': '无法获取磁盘信息'
                }
                results['checks'].append(disk_check)
                results['checks_map']['disk'] = disk_check
        else:
            disk_check = {
                'name': '磁盘空间',
                'status': 'unknown',
                'message': '无法获取磁盘信息'
            }
            results['checks'].append(disk_check)
            results['checks_map']['disk'] = disk_check

    # 4. 内存检查
    if HAS_PSUTIL:
        try:
            memory = psutil.virtual_memory()
            mem_ok = memory.percent < 90
            memory_check = {
                'name': '内存使用',
                'status': 'pass' if mem_ok else 'warn',
                'message': f'已使用 {memory.percent}%',
                'details': {'percent': memory.percent}
            }
            results['checks'].append(memory_check)
            results['checks_map']['memory'] = memory_check
        except:
            memory_check = {
                'name': '内存使用',
                'status': 'unknown',
                'message': '无法获取内存信息'
            }
            results['checks'].append(memory_check)
            results['checks_map']['memory'] = memory_check
    else:
        # 使用free命令获取内存信息（Linux）
        if is_linux() and command_available('free'):
            _, mem_out, _ = run_cmd('free 2>/dev/null | grep Mem')
            if mem_out:
                parts = mem_out.split()
                if len(parts) >= 3:
                    try:
                        total = int(parts[1])
                        used = int(parts[2])
                        percent = round(used / total * 100, 1) if total > 0 else 0
                        mem_ok = percent < 90
                        memory_check = {
                            'name': '内存使用',
                            'status': 'pass' if mem_ok else 'warn',
                            'message': f'已使用 {percent}%',
                            'details': {'percent': percent}
                        }
                        results['checks'].append(memory_check)
                        results['checks_map']['memory'] = memory_check
                    except:
                        memory_check = {
                            'name': '内存使用',
                            'status': 'unknown',
                            'message': '无法获取内存信息'
                        }
                        results['checks'].append(memory_check)
                        results['checks_map']['memory'] = memory_check
                else:
                    memory_check = {
                        'name': '内存使用',
                        'status': 'unknown',
                        'message': '无法获取内存信息'
                    }
                    results['checks'].append(memory_check)
                    results['checks_map']['memory'] = memory_check
            else:
                memory_check = {
                    'name': '内存使用',
                    'status': 'unknown',
                    'message': '无法获取内存信息'
                }
                results['checks'].append(memory_check)
                results['checks_map']['memory'] = memory_check
        else:
            memory_check = {
                'name': '内存使用',
                'status': 'unknown',
                'message': '无法获取内存信息'
            }
            results['checks'].append(memory_check)
            results['checks_map']['memory'] = memory_check

    # 5. 认证文件检查
    auth_dir = CONFIG['auth_dir']
    if os.path.exists(auth_dir):
        auth_files = [f for f in os.listdir(auth_dir) if os.path.isfile(os.path.join(auth_dir, f))]
        auth_check = {
            'name': '认证文件',
            'status': 'pass' if len(auth_files) > 0 else 'warn',
            'message': f'找到 {len(auth_files)} 个凭证文件',
            'details': {'count': len(auth_files)}
        }
        results['checks'].append(auth_check)
        results['checks_map']['auth'] = auth_check
    else:
        auth_check = {
            'name': '认证文件',
            'status': 'fail',
            'message': '认证目录不存在'
        }
        results['checks'].append(auth_check)
        results['checks_map']['auth'] = auth_check

    # 6. API端口检查
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', CONFIG['cliproxy_api_port']))
        sock.close()
        port_open = result == 0
        port_check = {
            'name': 'API端口',
            'status': 'pass' if port_open else 'fail',
            'message': f'端口 {CONFIG["cliproxy_api_port"]} {"开放" if port_open else "关闭"}'
        }
        results['checks'].append(port_check)
        results['checks_map']['api_port'] = port_check
    except:
        port_check = {
            'name': 'API端口',
            'status': 'unknown',
            'message': '无法检测端口状态'
        }
        results['checks'].append(port_check)
        results['checks_map']['api_port'] = port_check

    # 计算整体状态
    statuses = [c['status'] for c in results['checks']]
    if 'fail' in statuses:
        results['overall'] = 'unhealthy'
    elif 'warn' in statuses:
        results['overall'] = 'degraded'
    else:
        results['overall'] = 'healthy'

    state['last_health_check'] = results
    state['health_status'] = results['overall']

    cache.set(cache_key, results)
    return results

def get_models_from_config():
    """从配置中获取模型列表"""
    config, error = load_cliproxy_config()
    if not config:
        return [], error

    # 如果没有yaml，无法解析模型
    if '_raw' in config:
        return [], 'pyyaml未安装，无法解析模型列表'

    models = []
    providers = config.get('providers', [])

    for provider in providers:
        provider_name = provider.get('name', 'unknown')
        provider_models = provider.get('models', [])

        for model in provider_models:
            if isinstance(model, str):
                models.append({
                    'id': model,
                    'provider': provider_name,
                    'name': model
                })
            elif isinstance(model, dict):
                models.append({
                    'id': model.get('id', model.get('name', 'unknown')),
                    'provider': provider_name,
                    'name': model.get('name', model.get('id', 'unknown')),
                    'aliases': model.get('aliases', [])
                })

    return models, None

# ==================== API 路由 ====================

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/status')
def api_status():
    service = get_service_status()
    check_for_updates()
    log_requests = get_request_count_from_logs()
    snapshot = fetch_usage_snapshot()
    token_totals, usage_reqs = aggregate_usage_snapshot(snapshot)
    pricing = {
        'input': _safe_float(CONFIG.get('pricing_input', 0.0)),
        'output': _safe_float(CONFIG.get('pricing_output', 0.0)),
        'cache': _safe_float(CONFIG.get('pricing_cache', 0.0)),
    }
    usage_costs = compute_usage_costs(token_totals, pricing)

    with stats_lock:
        state['stats']['input_tokens'] = token_totals.get('input_tokens', 0)
        state['stats']['output_tokens'] = token_totals.get('output_tokens', 0)
        state['stats']['cached_tokens'] = token_totals.get('cached_tokens', 0)

    # 触发持久化保存
    save_persistent_stats()

    return jsonify({
        'service': service,
        'version': {
            'current': state['current_version'],
            'latest': state['latest_version'],
            'has_update': state['current_version'] != state['latest_version']
        },
        'requests': {
            'count': usage_reqs.get('total_requests') or log_requests.get('count'),
            'last_time': log_requests.get('last_time'),
            'success': usage_reqs.get('success') or log_requests.get('success', 0),
            'failed': usage_reqs.get('failure') or log_requests.get('failed', 0),
            'is_idle': is_idle(),
            'input_tokens': token_totals.get('input_tokens', 0),
            'output_tokens': token_totals.get('output_tokens', 0),
            'cached_tokens': token_totals.get('cached_tokens', 0),
            'total_tokens': token_totals.get('total_tokens', 0),
        },
        'update': {
            'in_progress': state['update_in_progress'],
            'last_time': state['last_update_time'],
            'last_result': state['last_update_result'],
            'auto_enabled': state['auto_update_enabled']
        },
        'config': {
            'idle_threshold': CONFIG['idle_threshold_seconds'],
            'check_interval': CONFIG['auto_update_check_interval']
        },
        'pricing': pricing,
        'usage_costs': usage_costs,
        'paths': get_paths_info(),
        'health': state['health_status']
    })

@app.route('/api/logs')
def api_logs():
    logs = parse_log_file(CONFIG['cliproxy_log'])
    return jsonify({'logs': logs, 'count': len(logs)})

@app.route('/api/cliproxy-logs')
def api_cliproxy_logs():
    """获取 CLIProxy 完整日志"""
    logs = parse_log_file(CONFIG['cliproxy_log'], max_lines=150)
    if len(logs) < 10:
        stderr_logs = parse_log_file(CONFIG['cliproxy_stderr'], max_lines=50)
        logs = logs + stderr_logs
    return jsonify({'logs': logs[-50:], 'count': len(logs)})

@app.route('/api/cliproxy-logs/clear', methods=['POST'])
def api_clear_cliproxy_logs():
    """清空 CLIProxy 日志"""
    log_files = [CONFIG.get('cliproxy_log'), CONFIG.get('cliproxy_stderr')]
    cleared = False
    errors = []

    for log_file in log_files:
        if not log_file or not os.path.exists(log_file):
            continue
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('')
            cleared = True
        except Exception as e:
            errors.append(f"{log_file}: {e}")

    _reset_log_stats_state()
    try:
        cache._cache.pop('request_count_logs', None)
    except:
        pass

    if errors:
        return jsonify({'success': False, 'message': '清空失败', 'errors': errors}), 500
    if not cleared:
        return jsonify({'success': True, 'message': '暂无日志可清空'})
    return jsonify({'success': True, 'message': '日志已清空'})

@app.route('/api/request-logs')
def api_request_logs():
    """获取解析后的 HTTP 请求日志"""
    logs, stats = parse_request_logs(max_lines=300)
    return jsonify({
        'logs': logs,
        'count': len(logs),
        'stats': stats
    })

@app.route('/api/paths')
def api_paths():
    return jsonify(get_paths_info())


@app.route('/api/update-history')
def api_update_history():
    """获取更新历史"""
    history_file = UPDATE_HISTORY_PATH
    try:
        os.makedirs(os.path.dirname(history_file), exist_ok=True)
        if os.path.exists(history_file):
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        else:
            history = []

        # 计算每次更新距今多少小时
        now = datetime.utcnow()
        for entry in history:
            try:
                update_time = datetime.strptime(entry['time'], '%Y-%m-%d %H:%M:%S')
                hours_ago = (now - update_time).total_seconds() / 3600
                entry['hours_ago'] = round(hours_ago, 1)
            except:
                entry['hours_ago'] = None
            entry['version'] = resolve_version_label(entry.get('version'))

        return jsonify({
            'success': True,
            'history': history[-10:]  # 返回最近10条
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def record_update_history(version, success=True):
    """记录更新历史"""
    history_file = UPDATE_HISTORY_PATH
    try:
        os.makedirs(os.path.dirname(history_file), exist_ok=True)
        if os.path.exists(history_file):
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        else:
            history = []

        history.append({
            'version': version,
            'time': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'success': success
        })

        # 只保留最近50条
        history = history[-50:]

        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

        return True
    except Exception as e:
        print(f"Error recording update history: {e}")
        return False

@app.route('/api/update', methods=['POST'])
def api_update():
    force = request.json.get('force', False) if request.json else False

    if not force and not is_idle():
        return jsonify({
            'success': False,
            'message': 'System has active requests. Wait for idle or use force update.'
        }), 400

    if state['update_in_progress']:
        return jsonify({'success': False, 'message': 'Update already in progress'}), 400

    def do_update():
        perform_update()

    thread = threading.Thread(target=do_update)
    thread.start()

    return jsonify({'success': True, 'message': 'Update started, please refresh to check status'})

@app.route('/api/service/<action>', methods=['POST'])
def api_service(action):
    if action not in ['start', 'stop', 'restart']:
        return jsonify({'success': False, 'message': 'Invalid action'}), 400

    if not (is_linux() and command_available('systemctl')):
        return jsonify({'success': False, 'message': 'Service control not supported on this platform'}), 400

    success, stdout, stderr = run_cmd(f'systemctl {action} {CONFIG["cliproxy_service"]}')
    time.sleep(2)

    status = get_service_status()
    return jsonify({'success': success, 'message': stdout or stderr, 'status': status})

@app.route('/api/config/auto-update', methods=['POST'])
def api_toggle_auto_update():
    data = request.json or {}
    enabled_raw = data.get('enabled', not state['auto_update_enabled'])
    enabled = enabled_raw if isinstance(enabled_raw, bool) else _parse_bool(enabled_raw)
    state['auto_update_enabled'] = enabled
    CONFIG['auto_update_enabled'] = enabled
    _update_dotenv_values({'auto_update_enabled': enabled})
    return jsonify({'success': True, 'auto_update_enabled': state['auto_update_enabled']})

@app.route('/api/config/idle-threshold', methods=['POST'])
def api_set_idle_threshold():
    data = request.json or {}
    threshold = data.get('threshold', 60)

    if not isinstance(threshold, int) or threshold < 10:
        return jsonify({'success': False, 'message': 'Threshold must be integer >= 10'}), 400

    CONFIG['idle_threshold_seconds'] = threshold
    _update_dotenv_values({'idle_threshold_seconds': CONFIG['idle_threshold_seconds']})
    return jsonify({'success': True, 'idle_threshold': CONFIG['idle_threshold_seconds']})

@app.route('/api/config/check-interval', methods=['POST'])
def api_set_check_interval():
    """设置自动更新检查间隔"""
    data = request.json or {}
    interval = data.get('interval', 300)
    if not isinstance(interval, (int, float)) or interval < 60:
        return jsonify({'success': False, 'error': 'Invalid interval (min 60 seconds)'}), 400
    CONFIG['auto_update_check_interval'] = int(interval)
    _update_dotenv_values({'auto_update_check_interval': CONFIG['auto_update_check_interval']})
    return jsonify({'success': True, 'check_interval': CONFIG['auto_update_check_interval']})


@app.route('/api/pricing', methods=['GET', 'POST'])
def api_pricing():
    if request.method == 'POST':
        data = request.json or {}
        input_price = _parse_float(data.get('input', CONFIG.get('pricing_input', 0.0)))
        output_price = _parse_float(data.get('output', CONFIG.get('pricing_output', 0.0)))
        cache_price = _parse_float(data.get('cache', CONFIG.get('pricing_cache', 0.0)))
        CONFIG['pricing_input'] = input_price
        CONFIG['pricing_output'] = output_price
        CONFIG['pricing_cache'] = cache_price
        _update_dotenv_values({
            'pricing_input': input_price,
            'pricing_output': output_price,
            'pricing_cache': cache_price,
        })
        return jsonify({'success': True, 'pricing': {'input': input_price, 'output': output_price, 'cache': cache_price}})

    return jsonify({
        'success': True,
        'pricing': {
            'input': _safe_float(CONFIG.get('pricing_input', 0.0)),
            'output': _safe_float(CONFIG.get('pricing_output', 0.0)),
            'cache': _safe_float(CONFIG.get('pricing_cache', 0.0)),
        }
    })


@app.route('/api/quote', methods=['GET', 'POST'])
def api_quote():
    if request.method == 'POST':
        data = request.json or {}
        line = (data.get('line') or '').strip()
        if not line or '出自：' not in line:
            return jsonify({'success': False, 'error': '格式错误，请使用“内容 出自：作者”'}), 400
        path = CONFIG.get('quotes_path')
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                if not line.endswith('\n'):
                    line = line + '\n'
                f.write(line)
            cache.set('quotes_cache', load_quotes())
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    quote = get_random_quote()
    return jsonify({'text': quote.get('text', ''), 'author': quote.get('author', '')})

@app.route('/api/record-request', methods=['POST'])
def api_record_request():
    with log_lock:
        state['last_request_time'] = time.time()
        state['request_count'] += 1

        data = request.json or {}
        state['request_log'].append({
            'time': datetime.now().isoformat(),
            'model': data.get('model', 'unknown'),
            'client': request.remote_addr,
            'status': data.get('status', 'unknown'),
            'response_time': data.get('response_time', 0)
        })

        if len(state['request_log']) > 100:
            state['request_log'] = state['request_log'][-100:]

        # 更新统计
        with stats_lock:
            state['stats']['total_requests'] += 1
            if data.get('status') == 'success':
                state['stats']['successful_requests'] += 1
            else:
                state['stats']['failed_requests'] += 1

            model = data.get('model', 'unknown')
            state['stats']['model_usage'][model] = state['stats']['model_usage'].get(model, 0) + 1

    # 触发持久化保存（后台线程会定期保存，这里只是触发检查）
    save_persistent_stats()

    return jsonify({'success': True})

@app.route('/api/request-history')
def api_request_history():
    return jsonify({
        'history': state['request_log'][-50:],
        'total_count': state['request_count'],
        'last_time': state['last_request_time']
    })

@app.route('/api/check-update')
def api_check_update():
    has_update = check_for_updates()
    return jsonify({
        'has_update': has_update,
        'current': state['current_version'],
        'latest': state['latest_version']
    })

@app.route('/api/auth-files')
def api_auth_files():
    auth_dir = CONFIG['auth_dir']
    if not os.path.exists(auth_dir):
        return jsonify({'files': [], 'error': 'Auth directory not found'})

    try:
        files = []
        for f in os.listdir(auth_dir):
            filepath = os.path.join(auth_dir, f)
            if os.path.isfile(filepath):
                stat = os.stat(filepath)
                files.append({
                    'name': f,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
        return jsonify({'files': files, 'path': auth_dir})
    except Exception as e:
        return jsonify({'files': [], 'error': str(e)})

@app.route('/api/config', methods=['GET'])
def api_get_config():
    config_path = CONFIG['cliproxy_config']
    if not os.path.exists(config_path):
        return jsonify({'success': False, 'error': 'Config file not found', 'path': config_path}), 404

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'success': True, 'content': content, 'path': config_path})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/config', methods=['POST'])
def api_upload_config():
    config_path = CONFIG['cliproxy_config']

    if 'file' in request.files:
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400

        try:
            backup_path = config_path + '.bak'
            if os.path.exists(config_path):
                import shutil
                shutil.copy2(config_path, backup_path)

            file.save(config_path)
            return jsonify({
                'success': True,
                'message': 'Config uploaded successfully',
                'path': config_path,
                'backup': backup_path
            })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    data = request.json
    if data and 'content' in data:
        try:
            backup_path = config_path + '.bak'
            if os.path.exists(config_path):
                import shutil
                shutil.copy2(config_path, backup_path)

            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(data['content'])

            return jsonify({
                'success': True,
                'message': 'Config saved successfully',
                'path': config_path,
                'backup': backup_path
            })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({'success': False, 'error': 'No file or content provided'}), 400

@app.route('/api/config/restore', methods=['POST'])
def api_restore_config():
    config_path = CONFIG['cliproxy_config']
    backup_path = config_path + '.bak'

    if not os.path.exists(backup_path):
        return jsonify({'success': False, 'error': 'No backup file found'}), 404

    try:
        import shutil
        shutil.copy2(backup_path, config_path)
        return jsonify({
            'success': True,
            'message': 'Config restored from backup',
            'path': config_path
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== 新增API ====================

@app.route('/api/config/validate', methods=['POST'])
def api_validate_config():
    """验证配置文件格式"""
    data = request.json or {}
    content = data.get('content', '')

    if not content:
        # 验证当前配置文件
        config_path = CONFIG['cliproxy_config']
        if not os.path.exists(config_path):
            return jsonify({'success': False, 'error': 'Config file not found'}), 404
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()

    result = validate_yaml_config(content)
    return jsonify(result)

@app.route('/api/config/reload', methods=['POST'])
def api_reload_config():
    """重新加载配置（发送SIGHUP信号）"""
    if not command_available('pgrep'):
        return jsonify({'success': False, 'message': 'Reload not supported on this platform'}), 400

    _, pid_out, _ = run_cmd('pgrep -f "cliproxy -config" | head -1')

    if not pid_out:
        return jsonify({'success': False, 'message': '服务未运行'}), 400

    try:
        if command_available('kill'):
            success, stdout, stderr = run_cmd(f'kill -HUP {pid_out}')
        else:
            success, stdout, stderr = (False, '', 'kill not available')

        if success:
            return jsonify({'success': True, 'message': '配置重载信号已发送'})
        else:
            # 如果SIGHUP不支持，尝试重启服务（Linux/systemd）
            if is_linux() and command_available('systemctl'):
                run_cmd(f'systemctl restart {CONFIG["cliproxy_service"]}')
                time.sleep(2)
                status = get_service_status()
                return jsonify({
                    'success': status['running'],
                    'message': '已重启服务以应用配置',
                    'status': status
                })

            return jsonify({'success': False, 'message': 'Reload not supported on this platform'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/config/routing', methods=['GET'])
def api_get_routing():
    """获取当前路由策略"""
    config, error = load_cliproxy_config()
    if not config:
        return jsonify({'success': False, 'error': error}), 500

    # 如果没有yaml，返回默认值
    if '_raw' in config:
        return jsonify({
            'success': True,
            'strategy': 'round-robin',
            'available': ['round-robin', 'fill-first'],
            'note': 'pyyaml未安装，无法解析配置'
        })

    routing = config.get('routing', {})
    return jsonify({
        'success': True,
        'strategy': routing.get('strategy', 'round-robin'),
        'available': ['round-robin', 'fill-first']
    })

@app.route('/api/config/routing', methods=['POST'])
def api_set_routing():
    """设置路由策略"""
    if not HAS_YAML:
        return jsonify({'success': False, 'error': 'pyyaml未安装，无法修改配置'}), 400

    data = request.json or {}
    strategy = data.get('strategy')

    valid_strategies = ['round-robin', 'fill-first']
    if strategy not in valid_strategies:
        return jsonify({'success': False, 'error': f'无效的策略，可选: {", ".join(valid_strategies)}'}), 400

    config_path = CONFIG['cliproxy_config']
    config, error = load_cliproxy_config()
    if not config:
        return jsonify({'success': False, 'error': error}), 500

    # 更新路由策略
    if 'routing' not in config:
        config['routing'] = {}
    config['routing']['strategy'] = strategy

    try:
        # 备份
        import shutil
        backup_path = config_path + '.bak'
        if os.path.exists(config_path):
            shutil.copy2(config_path, backup_path)

        # 写入新配置
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

        return jsonify({'success': True, 'message': f'路由策略已设置为 {strategy}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/health')
def api_health():
    """健康检查"""
    results = perform_health_check()
    return jsonify(results)

@app.route('/api/resources')
def api_resources():
    """获取系统资源"""
    get_request_count_from_logs()
    resources = get_system_resources()
    return jsonify(resources)

@app.route('/api/stats')
def api_stats():
    """获取统计数据"""
    with stats_lock:
        stats = {
            'total_requests': state['stats']['total_requests'],
            'successful_requests': state['stats']['successful_requests'],
            'failed_requests': state['stats']['failed_requests'],
            'success_rate': (state['stats']['successful_requests'] / max(state['stats']['total_requests'], 1)) * 100,
            'model_usage': dict(state['stats']['model_usage']),
            'error_types': dict(state['stats']['error_types']),
            'request_log': state['request_log'][-20:],
        }

    return jsonify(stats)

@app.route('/api/stats/clear', methods=['POST'])
def api_clear_stats():
    """清空请求统计（包括日志中的记录和持久化文件）"""
    with stats_lock:
        state['stats']['total_requests'] = 0
        state['stats']['successful_requests'] = 0
        state['stats']['failed_requests'] = 0
        state['stats']['input_tokens'] = 0
        state['stats']['output_tokens'] = 0
        state['stats']['cached_tokens'] = 0
        state['stats']['model_usage'].clear()
        state['stats']['error_types'].clear()
        state['request_log'].clear()
        state['request_count'] = 0

    # 保存清空后的状态到持久化文件
    save_persistent_stats(force=True)

    # 清空日志文件中的请求记录
    log_file = CONFIG['cliproxy_log']
    try:
        if os.path.exists(log_file):
            # 备份并清空日志
            backup_file = log_file + '.bak'
            import shutil
            shutil.copy2(log_file, backup_file)
            # 清空日志文件
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('')
            _reset_log_stats_state()
            # 清除缓存
            try:
                cache._cache.pop('request_count_logs', None)
            except:
                pass
    except Exception as e:
        print(f"Error clearing log file: {e}")

    return jsonify({'success': True, 'message': '统计数据已清空'})

@app.route('/api/models')
def api_models():
    """获取模型列表"""
    base_url = CONFIG.get('cliproxy_api_base', 'http://127.0.0.1').rstrip('/')
    api_port = CONFIG.get('cliproxy_api_port')
    api_key = CONFIG.get('models_api_key', '')

    if api_port:
        base_url = f'{base_url}:{api_port}'

    models_url = f'{base_url}/v1/models'
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    try:
        resp = requests.get(models_url, headers=headers, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        models = payload.get('data', []) if isinstance(payload, dict) else []
        return jsonify({'success': True, 'models': models, 'count': len(models)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'models': []}), 502

@app.route('/api/test/connection', methods=['POST'])
def api_test_connection():
    """测试连接"""
    data = request.json or {}
    target = data.get('target', 'api')

    results = {'success': True, 'tests': []}

    if target in ['api', 'all']:
        # 测试API端口
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            start = time.time()
            result = sock.connect_ex(('127.0.0.1', CONFIG['cliproxy_api_port']))
            latency = (time.time() - start) * 1000
            sock.close()

            results['tests'].append({
                'name': 'API端口',
                'success': result == 0,
                'latency': f'{latency:.1f}ms' if result == 0 else None,
                'message': f'端口 {CONFIG["cliproxy_api_port"]} 正常' if result == 0 else '连接失败'
            })
        except Exception as e:
            results['tests'].append({
                'name': 'API端口',
                'success': False,
                'message': str(e)
            })

    if target in ['internet', 'all']:
        # 测试外网连接
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            start = time.time()
            result = sock.connect_ex(('8.8.8.8', 53))
            latency = (time.time() - start) * 1000
            sock.close()

            results['tests'].append({
                'name': '外网连接',
                'success': result == 0,
                'latency': f'{latency:.1f}ms' if result == 0 else None,
                'message': '网络正常' if result == 0 else '无法连接外网'
            })
        except Exception as e:
            results['tests'].append({
                'name': '外网连接',
                'success': False,
                'message': str(e)
            })

    # 整体结果
    results['success'] = all(t['success'] for t in results['tests'])

    return jsonify(results)

@app.route('/api/test/api', methods=['POST'])
def api_test_api():
    """API测试器"""
    data = request.json or {}
    endpoint = data.get('endpoint', '/v1/models')
    method = data.get('method', 'GET')
    body = data.get('body')
    headers = data.get('headers', {})

    base_url = f'http://127.0.0.1:{CONFIG["cliproxy_api_port"]}'
    url = base_url + endpoint

    try:
        import urllib.request
        import urllib.error

        start_time = time.time()

        req_data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=req_data, method=method)

        for key, value in headers.items():
            req.add_header(key, value)

        if body:
            req.add_header('Content-Type', 'application/json')

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                response_time = (time.time() - start_time) * 1000
                response_body = response.read().decode('utf-8')

                try:
                    response_json = json.loads(response_body)
                except:
                    response_json = None

                return jsonify({
                    'success': True,
                    'status': response.status,
                    'response_time': f'{response_time:.1f}ms',
                    'headers': dict(response.headers),
                    'body': response_json if response_json else response_body[:2000]
                })
        except urllib.error.HTTPError as e:
            response_time = (time.time() - start_time) * 1000
            return jsonify({
                'success': False,
                'status': e.code,
                'response_time': f'{response_time:.1f}ms',
                'error': str(e),
                'body': e.read().decode('utf-8')[:1000] if e.fp else None
            })
        except urllib.error.URLError as e:
            return jsonify({
                'success': False,
                'error': f'连接失败: {str(e.reason)}'
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/api/export/<data_type>')
def api_export(data_type):
    """数据导出"""
    if data_type == 'logs':
        logs = state['request_log']
        content = json.dumps(logs, indent=2, ensure_ascii=False)
        return Response(
            content,
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename=logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'}
        )

    elif data_type == 'stats':
        with stats_lock:
            stats = {
                'exported_at': datetime.now().isoformat(),
                'total_requests': state['stats']['total_requests'],
                'successful_requests': state['stats']['successful_requests'],
                'failed_requests': state['stats']['failed_requests'],
                'model_usage': dict(state['stats']['model_usage']),
            }
        content = json.dumps(stats, indent=2, ensure_ascii=False)
        return Response(
            content,
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename=stats_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'}
        )

    elif data_type == 'config':
        config_path = CONFIG['cliproxy_config']
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return Response(
                content,
                mimetype='application/x-yaml',
                headers={'Content-Disposition': f'attachment; filename=config_{datetime.now().strftime("%Y%m%d_%H%M%S")}.yaml'}
            )
        return jsonify({'error': 'Config not found'}), 404

    elif data_type == 'health':
        health = perform_health_check()
        content = json.dumps(health, indent=2, ensure_ascii=False)
        return Response(
            content,
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename=health_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'}
        )

    return jsonify({'error': 'Unknown data type'}), 400

# 启动后台任务
def background_tasks():
    """后台任务：定期健康检查和资源监控"""
    while True:
        try:
            perform_health_check()
            get_request_count_from_logs()
        except Exception as e:
            print(f'[{datetime.now()}] Health check failed: {e}')
        time.sleep(60)

if __name__ == '__main__':
    state['current_version'] = get_current_commit()

    # 确保 data 目录存在
    os.makedirs(DATA_DIR, exist_ok=True)

    # 加载持久化统计数据（最重要的，放在最前面）
    load_persistent_stats()
    # 立即保存一次，确保文件存在
    save_persistent_stats(force=True)

    load_log_stats_state()
    try:
        get_request_count_from_logs()
        save_log_stats_state(force=True)
    except Exception as e:
        print(f"Warning: failed to initialize log stats: {e}")

    # 启动资源监控器（非阻塞CPU监控）
    resource_monitor.start()

    # 启动自动更新线程
    auto_thread = threading.Thread(target=auto_update_worker, daemon=True)
    auto_thread.start()

    # 启动后台任务线程
    bg_thread = threading.Thread(target=background_tasks, daemon=True)
    bg_thread.start()

    # 启动 usage 持久化线程
    start_usage_snapshot_worker()

    # 启动统计数据持久化线程
    start_persistent_stats_worker()

    # 预加载语录并做数量检查
    quotes = load_quotes()
    if quotes:
        cache.set('quotes_cache', quotes)
        author_count = len({q.get('author') for q in quotes if q.get('author')})
        if len(quotes) < 300 or author_count < 30:
            print(f"Warning: quotes count {len(quotes)}, authors {author_count}")

    print(f'CPA-XX Management Panel v3 (Optimized) started on port {CONFIG["panel_port"]}')
    app.run(host='0.0.0.0', port=CONFIG['panel_port'], debug=False)
