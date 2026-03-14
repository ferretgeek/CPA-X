#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CPA-X 管理面板后端 v2.0.0
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
import tempfile
import tarfile
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS
import requests

# 面板自身版本（与 GitHub Release/README 同步）
PANEL_NAME = "CPA-X"
PANEL_VERSION = "2.0.0"

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
    # Token 价格自动同步（默认启用；当手动价格为 0 时会尝试从权威来源补齐）
    'pricing_auto_enabled': True,
    'pricing_auto_source': 'openrouter',  # 目前仅实现 openrouter
    'pricing_auto_model': '',  # 为空时会从 config.yaml 里挑一个模型，最后回退到 openai/gpt-4o-mini
    'quotes_path': os.path.join(DATA_DIR, 'quotes.txt'),
    'disk_path': '/',
    # 安全默认：只监听本机。如需局域网访问，把 CLIPROXY_PANEL_BIND_HOST 改为 0.0.0.0
    'bind_host': '127.0.0.1',
    'panel_access_key': '',
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
    'pricing_auto_enabled': bool,
}


def _panel_access_key_expected() -> str:
    return str(CONFIG.get('panel_access_key', '') or '').strip()


def _panel_access_key_provided() -> str:
    return str(
        request.headers.get('X-Panel-Key')
        or request.args.get('panel_key')
        or request.cookies.get('panel_key')
        or ''
    ).strip()


@app.before_request
def _enforce_panel_access_key():
    expected = _panel_access_key_expected()
    if not expected:
        return None

    # 允许 CORS 预检请求通过
    if request.method == 'OPTIONS':
        return None

    # 只保护 API（静态页面可访问，但无法读取/操作数据）
    if not request.path.startswith('/api'):
        return None

    if _panel_access_key_provided() != expected:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    return None


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
    # 上次从 CLIProxyAPI 读取的快照值（用于计算增量）
    'last_snapshot': {
        'input_tokens': 0,
        'output_tokens': 0,
        'cached_tokens': 0,
        'total_requests': 0,
        'success': 0,
        'failure': 0,
    },
    # 面板独立累加的统计数据（持久化保存，不受 CLIProxyAPI 重启影响）
    'accumulated_stats': {
        'input_tokens': 0,
        'output_tokens': 0,
        'cached_tokens': 0,
        'total_requests': 0,
        'success': 0,
        'failure': 0,
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
            # 加载累计统计值
            if 'accumulated_stats' in data and isinstance(data['accumulated_stats'], dict):
                for key in state['accumulated_stats']:
                    if key in data['accumulated_stats']:
                        state['accumulated_stats'][key] = safe_int(data['accumulated_stats'][key])
            # 加载上次快照值
            if 'last_snapshot' in data and isinstance(data['last_snapshot'], dict):
                for key in state['last_snapshot']:
                    if key in data['last_snapshot']:
                        state['last_snapshot'][key] = safe_int(data['last_snapshot'][key])
            # 同步 request_count
            state['request_count'] = state['stats']['total_requests']
        print(f"Loaded persistent stats: accumulated={state['accumulated_stats']}, last_snapshot={state['last_snapshot']}")
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
                'accumulated_stats': dict(state.get('accumulated_stats', {})),
                'last_snapshot': dict(state.get('last_snapshot', {})),
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

    def invalidate(self, key=None):
        """使缓存失效（key=None 表示清空全部）"""
        with self._lock:
            if key is None:
                self._cache.clear()
            else:
                self._cache.pop(key, None)


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
    key = str(CONFIG.get('management_key', '') or '').strip()
    # AI 友好兜底：很多部署把管理密钥与 API Key 设为同一个值
    if not key:
        key = str(CONFIG.get('models_api_key', '') or '').strip()
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

    top_total = _safe_int(usage.get('total_requests', usage.get('total', 0)))
    top_success = _safe_int(usage.get('success', usage.get('successful_requests', usage.get('success_count', 0))))
    top_failure = _safe_int(usage.get('failure', usage.get('failed_requests', usage.get('failure_count', 0))))

    def extract_tokens(obj):
        if not isinstance(obj, dict):
            return 0, 0, 0, 0
        tokens = obj.get('tokens') or obj.get('usage') or obj
        input_tokens = _safe_int(tokens.get('input_tokens', tokens.get('input', tokens.get('prompt_tokens', 0))))
        output_tokens = _safe_int(tokens.get('output_tokens', tokens.get('output', tokens.get('completion_tokens', 0))))
        cached_tokens = _safe_int(tokens.get('cached_tokens', tokens.get('cache', 0)))
        reasoning_tokens = _safe_int(tokens.get('reasoning_tokens', tokens.get('reasoning', 0)))
        total_tokens = _safe_int(tokens.get('total_tokens', tokens.get('total', obj.get('total_tokens', 0))))
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens + reasoning_tokens
        return input_tokens, output_tokens, cached_tokens, total_tokens

    apis = usage.get('apis', [])
    if isinstance(apis, dict):
        apis = list(apis.values())
    if not isinstance(apis, list):
        apis = []

    sum_total = 0
    sum_success = 0
    sum_failure = 0

    for api in apis:
        if not isinstance(api, dict):
            continue
        sum_total += _safe_int(api.get('total_requests', api.get('total', api.get('requests', 0))))
        sum_success += _safe_int(api.get('success', api.get('successful_requests', api.get('success_count', 0))))
        sum_failure += _safe_int(api.get('failure', api.get('failed_requests', api.get('failure_count', 0))))

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

    # 请求数/成功/失败：优先使用 usage 顶层汇总，避免与 apis breakdown 叠加导致双计数
    if top_total > 0:
        reqs['total_requests'] = top_total
        reqs['success'] = top_success
        reqs['failure'] = top_failure
    else:
        reqs['total_requests'] = sum_total
        reqs['success'] = sum_success
        reqs['failure'] = sum_failure

    return totals, reqs


def compute_usage_costs(tokens, pricing):
    input_price = _safe_float(pricing.get('input', 0.0))
    output_price = _safe_float(pricing.get('output', 0.0))
    cache_price = _safe_float(pricing.get('cache', 0.0))

    input_tokens = _safe_int(tokens.get('input_tokens', 0))
    cached_tokens = _safe_int(tokens.get('cached_tokens', 0))
    billable_input_tokens = max(input_tokens - cached_tokens, 0)

    input_cost = billable_input_tokens / 1_000_000 * input_price
    output_tokens = _safe_int(tokens.get('output_tokens', 0))
    output_cost = output_tokens / 1_000_000 * output_price
    cache_cost = cached_tokens / 1_000_000 * cache_price
    total_cost = input_cost + output_cost + cache_cost

    return {
        'input': input_cost,
        'output': output_cost,
        'cache': cache_cost,
        'total': total_cost,
    }


def _parse_float_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _fetch_openrouter_models():
    """
    从 OpenRouter 获取模型列表（带长缓存）。
    OpenRouter pricing 字段为“美元/Token”，本面板内部价格口径为“美元/百万Tokens”。
    """
    cache_key = 'openrouter_models_v1'
    cached = cache.get(cache_key, max_age=6 * 3600)
    if cached is not None:
        return cached

    url = 'https://openrouter.ai/api/v1/models'
    try:
        resp = requests.get(url, timeout=15, headers={'User-Agent': 'CPA-X Panel'})
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        models = payload.get('data', []) if isinstance(payload, dict) else []
        if not isinstance(models, list):
            models = []
        cache.set(cache_key, models)
        return models
    except Exception as e:
        print(f'Warning: failed to fetch openrouter models: {e}')
        cache.set(cache_key, [])
        return []


def _openrouter_pricing_per_million(model_id: str):
    if not model_id:
        return None

    models = _fetch_openrouter_models()
    for m in models:
        if not isinstance(m, dict):
            continue
        if (m.get('id') or '') != model_id:
            continue
        pricing = m.get('pricing') if isinstance(m.get('pricing'), dict) else {}
        prompt = _parse_float_or_none(pricing.get('prompt'))
        completion = _parse_float_or_none(pricing.get('completion'))
        cache_read = _parse_float_or_none(pricing.get('input_cache_read'))

        if prompt is None or completion is None:
            return None

        # OpenRouter pricing is USD/token; panel uses USD / 1M tokens
        per_million = {
            'input': prompt * 1_000_000,
            'output': completion * 1_000_000,
        }
        # cached tokens price is optional
        if cache_read is not None:
            per_million['cache'] = cache_read * 1_000_000
        else:
            # 兜底：如果来源不提供 cache 价格，先按 input 计（用户可手动改）
            per_million['cache'] = per_million['input']

        return {
            'pricing': per_million,
            'model': model_id,
            'source': 'openrouter',
        }
    return None


def _pick_pricing_auto_model_id():
    configured = (str(CONFIG.get('pricing_auto_model', '') or '').strip())
    if configured:
        return configured
    # 尝试从 config.yaml 拿一个模型 id（不依赖上游接口）
    try:
        models, _ = get_models_from_config()
        if isinstance(models, list) and models:
            mid = (models[0].get('id') if isinstance(models[0], dict) else None) or ''
            mid = str(mid).strip()
            if mid:
                return mid
    except Exception:
        pass
    # 最终回退
    return 'openai/gpt-4o-mini'


def get_effective_pricing():
    """
    返回本次用于展示/计算的价格（USD / 1M tokens）。
    规则：
    - 手动价格 > 0：优先使用手动
    - 手动价格为 0：且开启自动同步时，尝试从来源补齐（目前为 OpenRouter）
    """
    manual = {
        'input': _safe_float(CONFIG.get('pricing_input', 0.0)),
        'output': _safe_float(CONFIG.get('pricing_output', 0.0)),
        'cache': _safe_float(CONFIG.get('pricing_cache', 0.0)),
    }

    meta = {
        'mode': 'manual',
        'source': 'manual',
        'model': None,
        'fields': {'input': 'manual', 'output': 'manual', 'cache': 'manual'},
        'auto_enabled': _parse_bool(CONFIG.get('pricing_auto_enabled', True)),
        'auto_source': str(CONFIG.get('pricing_auto_source', 'openrouter') or 'openrouter').strip().lower(),
        'auto_model': (str(CONFIG.get('pricing_auto_model', '') or '').strip() or None),
    }

    if not _parse_bool(CONFIG.get('pricing_auto_enabled', True)):
        return manual, meta

    need_auto = any(manual.get(k, 0.0) <= 0 for k in ('input', 'output', 'cache'))
    if not need_auto:
        return manual, meta

    source = str(CONFIG.get('pricing_auto_source', 'openrouter') or 'openrouter').strip().lower()
    if source != 'openrouter':
        return manual, meta

    model_id = _pick_pricing_auto_model_id()
    suggested = _openrouter_pricing_per_million(model_id)
    if not suggested:
        # 如果 config.yaml 挑的模型在 OpenRouter 找不到，尝试回退到固定模型
        if model_id != 'openai/gpt-4o-mini':
            suggested = _openrouter_pricing_per_million('openai/gpt-4o-mini')
    if not suggested:
        return manual, meta

    eff = dict(manual)
    fields = dict(meta['fields'])
    for k in ('input', 'output', 'cache'):
        if eff.get(k, 0.0) <= 0:
            eff[k] = _safe_float(suggested['pricing'].get(k, eff[k]))
            fields[k] = 'openrouter'

    meta = {
        'mode': 'mixed' if any(v == 'openrouter' for v in fields.values()) and any(v == 'manual' for v in fields.values()) else 'auto',
        'source': suggested.get('source', 'openrouter'),
        'model': suggested.get('model'),
        'fields': fields,
        'auto_enabled': True,
        'auto_source': source,
        'auto_model': (str(CONFIG.get('pricing_auto_model', '') or '').strip() or None),
    }
    return eff, meta


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
        service_name = CONFIG.get("cliproxy_service")
        success, stdout, _ = run_cmd(f'systemctl is-active {service_name}')
        is_running = success and stdout == 'active'
        _, status_out, _ = run_cmd(f'systemctl status {service_name} --no-pager -l 2>/dev/null | head -20')
        # 尽量用 systemd 的 MainPID（比 pgrep 更准确，且不依赖进程名）
        ok_pid, pid_value, _ = run_cmd(f'systemctl show {service_name} -p MainPID --value 2>/dev/null')
        if ok_pid:
            pid_value = (pid_value or '').strip()
            if pid_value and pid_value != '0':
                pid_out = pid_value
    else:
        status_out = 'Not supported on this platform'

    # fallback：没有 systemd 或无法获取 MainPID 时再尝试 pgrep
    if not pid_out and command_available('pgrep'):
        _, pid_out, _ = run_cmd('pgrep -f "cli-proxy-api|cliproxyapi|cliproxy" | head -1')

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
        repo = 'router-for-me/CLIProxyAPI'
        api_url = f'https://api.github.com/repos/{repo}/releases/latest'
        html_latest_url = f'https://github.com/{repo}/releases/latest'

        def api_headers():
            headers = {
                'User-Agent': 'CLIProxyPanel',
                'Accept': 'application/vnd.github+json',
            }
            token = (os.environ.get('CLIPROXY_PANEL_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN') or '').strip()
            if token:
                headers['Authorization'] = 'Bearer ' + token
            return headers

        # 1) 优先用 GitHub API（有 token 时限额更高）
        try:
            resp = requests.get(api_url, headers=api_headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json() if resp.content else {}
                version = (data.get('tag_name') if isinstance(data, dict) else None) or 'unknown'
                cache.set(cache_key, version)
                return version
        except Exception as e:
            print(f'get_github_release_version api error: {e}')

        # 2) 回退：解析 /releases/latest 的 302 跳转（不依赖 GitHub API，避免 rate limit）
        try:
            resp = requests.get(
                html_latest_url,
                headers={'User-Agent': 'CLIProxyPanel'},
                timeout=10,
                allow_redirects=False,
            )
            location = resp.headers.get('Location', '')
            m = re.search(r'/tag/(v[^/?#]+)', location)
            if not m:
                # 极端情况下不返回 302，则跟随跳转后从最终 URL 解析
                resp2 = requests.get(
                    html_latest_url,
                    headers={'User-Agent': 'CLIProxyPanel'},
                    timeout=10,
                    allow_redirects=True,
                )
                m = re.search(r'/tag/(v[^/?#]+)', str(getattr(resp2, 'url', '') or ''))
            if m:
                version = m.group(1)
                cache.set(cache_key, version)
                return version
        except Exception as e:
            print(f'get_github_release_version fallback error: {e}')
    except Exception as e:
        print(f'get_github_release_version error: {e}')
        return 'unknown'

    return 'unknown'


def _normalize_release_version(version):
    if version is None:
        return ''
    v = str(version).strip()
    if not v:
        return ''
    if v.lower() == 'unknown':
        return 'unknown'
    if v.lower() == 'dev':
        return 'dev'
    if v.startswith(('v', 'V')) and len(v) > 1:
        return v[1:]
    return v


def _decorate_version_tag(version):
    """统一显示为 vX.Y.Z（如果看起来像语义版本）"""
    raw = str(version).strip() if version is not None else ''
    if not raw:
        return raw
    if raw.lower() in {'unknown', 'dev'}:
        return raw.lower()
    normalized = _normalize_release_version(raw)
    # 只对形如 1.2.3 这样的做装饰
    if re.match(r'^\d+(\.\d+){1,3}$', normalized):
        return f'v{normalized}'
    return raw


def _cliproxy_management_get(path, timeout=6):
    base_url = _build_management_base_url()
    url = f'{base_url}{path}'
    headers = _management_headers()
    try:
        return requests.get(url, headers=headers, timeout=timeout)
    except Exception:
        return None


def _get_local_version_from_management():
    """优先从 CLIProxyAPI 管理接口响应头读取版本号（适用于二进制安装）"""
    cache_key = 'local_version_mgmt'
    cached = cache.get(cache_key, max_age=10)
    if cached:
        return cached

    resp = _cliproxy_management_get('/v0/management/config', timeout=5)
    if resp is None:
        return None
    try:
        if resp.status_code != 200:
            return None
        header_value = resp.headers.get('X-Cpa-Version') or resp.headers.get('X-CPA-VERSION')
        if not header_value:
            return None
        version = _decorate_version_tag(header_value)
        # 避免把上游的 dev/unknown 当成“可用版本”
        if _normalize_release_version(version) in {'unknown', 'dev', ''}:
            return None
        if version:
            cache.set(cache_key, version)
            return version
    except Exception:
        return None
    return None


def _is_git_repo(path):
    try:
        return bool(path) and os.path.isdir(path) and os.path.isdir(os.path.join(path, '.git'))
    except Exception:
        return False


def _is_semver_like(version) -> bool:
    """是否看起来像 release 版本号（支持 v 前缀）"""
    normalized = _normalize_release_version(version)
    if not normalized or normalized in {'unknown', 'dev'}:
        return False
    return bool(re.match(r'^\d+(\.\d+){1,3}$', str(normalized)))


def _get_last_successful_release_version_from_history():
    """从 update_history.json 中取最近一次成功更新的 release 版本号（用于兜底显示）"""
    try:
        path = UPDATE_HISTORY_PATH
        if not path or not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            history = json.load(f)
        if not isinstance(history, list):
            return None
        for entry in reversed(history):
            if not isinstance(entry, dict):
                continue
            if entry.get('success') is not True:
                continue
            v = entry.get('version')
            if _is_semver_like(v):
                return _decorate_version_tag(v)
    except Exception:
        return None
    return None


def get_local_version():
    """获取本地版本号"""
    cache_key = 'local_version'
    cached = cache.get(cache_key, max_age=30)
    if cached:
        return cached

    mgmt_candidate = None

    # 1) 优先：从管理接口读取（适配 release 二进制安装场景）
    mgmt_version = _get_local_version_from_management()
    if mgmt_version:
        # 如果是规范的 release 版本号，直接返回
        if _is_semver_like(mgmt_version):
            cache.set(cache_key, mgmt_version)
            return mgmt_version
        mgmt_candidate = mgmt_version

    # 2) 其次：本地 git 仓库
    cliproxy_dir = CONFIG.get('cliproxy_dir')
    if _is_git_repo(cliproxy_dir) and command_available('git'):
        # 确保本地有最新的 tag 信息
        run_cmd(f'cd {cliproxy_dir} && git fetch --tags 2>/dev/null', timeout=10)

        version_file = os.path.join(cliproxy_dir, 'VERSION')
        if os.path.exists(version_file):
            try:
                with open(version_file, 'r') as f:
                    version = f.read().strip()
                    if version and _is_semver_like(version):
                        decorated = _decorate_version_tag(version)
                        cache.set(cache_key, decorated)
                        return decorated
            except Exception:
                pass

        ok, stdout, _ = run_cmd(f'cd {cliproxy_dir} && git describe --tags --abbrev=0 2>/dev/null')
        if ok and stdout and _is_semver_like(stdout):
            decorated = _decorate_version_tag(stdout)
            cache.set(cache_key, decorated)
            return decorated

        ok, stdout, _ = run_cmd(f'cd {cliproxy_dir} && git rev-parse --short HEAD 2>/dev/null')
        if ok and stdout:
            mgmt_candidate = mgmt_candidate or stdout

    # 3) 兜底：如果上游/本地无法得到 release 版本号，尝试从更新历史中读取
    history_version = _get_last_successful_release_version_from_history()
    if history_version:
        cache.set(cache_key, history_version)
        return history_version

    # 4) 再兜底：如果管理接口返回了 hash 等信息，至少返回它；否则 unknown
    if mgmt_candidate:
        cache.set(cache_key, mgmt_candidate)
        return mgmt_candidate

    cache.set(cache_key, 'unknown')
    return 'unknown'

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
    current = get_local_version()
    latest = get_github_release_version()

    # 统一展示版本格式
    current_display = _decorate_version_tag(current)
    latest_display = _decorate_version_tag(latest)
    state['current_version'] = current_display
    state['latest_version'] = latest_display

    # 用规范化结果比较，避免 v 前缀导致误判
    current_norm = _normalize_release_version(current_display)
    latest_norm = _normalize_release_version(latest_display)
    result = current_norm != latest_norm and latest_norm not in {'unknown', '', 'dev'} and current_norm not in {'unknown', '', 'dev'}
    # 只缓存判断结果，不阻断 state 更新（否则 UI 可能卡在 unknown）
    if use_cache:
        cache.set('update_check', result)
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

        # ===== 更新策略选择 =====
        # A) 源码安装：git pull + go build（旧逻辑）
        # B) release 二进制：下载最新 release 并替换二进制（推荐）
        cliproxy_dir = CONFIG.get('cliproxy_dir')
        cliproxy_bin = CONFIG.get('cliproxy_binary') or ''
        backup_path = None
        updated_release_version = None

        use_source_update = _is_git_repo(cliproxy_dir) and command_available('git') and command_available('go')

        if use_source_update:
            result['details'].append('Pulling latest code...')
            success, stdout, stderr = run_cmd(f'cd {cliproxy_dir} && git fetch --tags && git pull origin main')
            if not success:
                result['message'] = f'Pull failed: {stderr}'
                return False, result
            result['details'].append(stdout)

            result['details'].append('Rebuilding...')
            success, stdout, stderr = run_cmd(
                f'cd {cliproxy_dir} && go build -o cliproxy ./cmd/server',
                timeout=300
            )
            if not success:
                result['message'] = f'Build failed: {stderr}'
                run_cmd(f'systemctl start {CONFIG["cliproxy_service"]}')
                return False, result
            result['details'].append('Build successful')
        else:
            if not cliproxy_bin:
                result['message'] = 'Binary path not set (CLIPROXY_PANEL_CLIPROXY_BINARY)'
                run_cmd(f'systemctl start {CONFIG["cliproxy_service"]}')
                return False, result

            # 为回滚做备份（更新后启动失败时可恢复）
            try:
                if os.path.exists(cliproxy_bin):
                    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
                    backup_path = f'{cliproxy_bin}.bak.{ts}'
                    shutil.copy2(cliproxy_bin, backup_path)
                    result['details'].append(f'Backup created: {backup_path}')
            except Exception as e:
                result['message'] = f'Backup failed: {e}'
                run_cmd(f'systemctl start {CONFIG["cliproxy_service"]}')
                return False, result

            result['details'].append('Downloading latest release...')
            ok, msg, updated_release_version = update_from_github_release(binary_path=cliproxy_bin)
            if not ok:
                result['message'] = msg or 'Release update failed'
                run_cmd(f'systemctl start {CONFIG["cliproxy_service"]}')
                return False, result
            result['details'].append(msg or 'Release binary updated')

        def _rollback_release_binary(reason: str) -> bool:
            if not backup_path or not cliproxy_bin:
                return False
            result['details'].append(f'Rollback: {reason}')
            try:
                shutil.copy2(backup_path, cliproxy_bin)
                try:
                    os.chmod(cliproxy_bin, 0o755)
                except Exception:
                    pass
                run_cmd(f'systemctl restart {CONFIG["cliproxy_service"]}')
                time.sleep(2)
                status2 = get_service_status()
                if status2.get('running'):
                    result['details'].append('Rollback successful')
                    return True
                result['details'].append('Rollback attempted but service still not running')
            except Exception as e:
                result['details'].append(f'Rollback failed: {e}')
            return False

        result['details'].append('Starting service...')
        success, _, stderr = run_cmd(f'systemctl start {CONFIG["cliproxy_service"]}')
        if not success:
            rolled_back = _rollback_release_binary('start failed after update')
            result['message'] = f'Start failed: {stderr}' + (' (rolled back)' if rolled_back else '')
            return False, result

        time.sleep(2)

        status = get_service_status()
        if not status['running']:
            rolled_back = _rollback_release_binary('service not running after start')
            result['message'] = 'Service not running after start' + (' (rolled back)' if rolled_back else '')
            return False, result

        result['success'] = True
        result['message'] = 'Update successful'
        result['details'].append('Service is running')

        state['last_update_time'] = datetime.now().isoformat()
        state['last_update_result'] = result
        state['current_version'] = get_local_version()
        # 如果本地/上游返回的是 unknown/hash 等，优先用本次 release 更新的版本号展示
        if updated_release_version and not _is_semver_like(state.get('current_version')):
            state['current_version'] = _decorate_version_tag(updated_release_version)

        # 记录更新历史
        try:
            record_update_history(state['current_version'])
        except Exception as e:
            print(f"Failed to record update history: {e}")
        # 清除版本缓存
        try:
            cache.invalidate('local_version')
            cache.invalidate('github_release')
            cache.invalidate('update_check')
        except Exception:
            pass

        return True, result

    except Exception as e:
        result['message'] = f'Update error: {str(e)}'
        run_cmd(f'systemctl start {CONFIG["cliproxy_service"]}')
        return False, result
    finally:
        state['update_in_progress'] = False


def _guess_goarch():
    machine = (platform.machine() or '').lower()
    if machine in {'aarch64', 'arm64'}:
        return 'arm64'
    if machine in {'x86_64', 'amd64'}:
        return 'amd64'
    if machine.startswith('armv7') or machine == 'armv7l':
        return 'armv7'
    return 'arm64'  # 默认偏向 arm64（常见于 N1 等设备）


def update_from_github_release(binary_path=''):
    """
    下载并安装 CLIProxyAPI 最新 release（二进制安装场景）。
    依赖：systemd + curl/requests 可用；需要当前进程有写入 binary_path 的权限（通常为 root）。

    返回：(ok, message, release_tag)
    """
    try:
        if not binary_path:
            return False, 'Binary path not set', None

        goarch = _guess_goarch()

        repo = 'router-for-me/CLIProxyAPI'
        api_error = None
        data = {}

        # 1) 优先：GitHub API（可能遇到未认证限流）
        try:
            headers = {'User-Agent': 'CLIProxyPanel', 'Accept': 'application/vnd.github+json'}
            token = (os.environ.get('CLIPROXY_PANEL_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN') or '').strip()
            if token:
                headers['Authorization'] = 'Bearer ' + token

            resp = requests.get(
                f'https://api.github.com/repos/{repo}/releases/latest',
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
        except Exception as e:
            api_error = e
            data = {}
            print(f'Warning: failed to fetch release info via GitHub API: {e}')

        resolved_tag = _decorate_version_tag((data.get('tag_name') or '') if isinstance(data, dict) else '')

        assets = data.get('assets', []) if isinstance(data, dict) else []

        asset_url = ''
        checksum_url = ''
        for a in assets:
            name = (a.get('name') or '')
            url = (a.get('browser_download_url') or '')
            if not url:
                continue
            if name.endswith(f'linux_{goarch}.tar.gz'):
                asset_url = url
            elif name == 'checksums.txt':
                checksum_url = url

        # 2) 回退：如果 API 拿不到资产列表（被限流/网络问题），用 tag + 固定命名规则拼装下载链接
        if not asset_url:
            if not resolved_tag:
                resolved_tag = _decorate_version_tag(get_github_release_version())
            tag_display = resolved_tag
            tag_number = _normalize_release_version(tag_display)
            if not tag_number or tag_number in {'unknown', 'dev'}:
                if api_error:
                    return False, f'Failed to fetch latest release info (GitHub API limited): {api_error}', None
                return False, 'Failed to resolve latest release tag', None

            asset_name = f'CLIProxyAPI_{tag_number}_linux_{goarch}.tar.gz'
            asset_url = f'https://github.com/{repo}/releases/download/{tag_display}/{asset_name}'
            checksum_url = f'https://github.com/{repo}/releases/download/{tag_display}/checksums.txt'
        elif not resolved_tag:
            # 极端兜底：有 asset_url 但拿不到 tag（理论上不应发生）
            resolved_tag = _decorate_version_tag(get_github_release_version())

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, 'cliproxyapi.tar.gz')
            # 下载 tarball
            with requests.get(asset_url, timeout=60, stream=True) as r:
                r.raise_for_status()
                with open(tar_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

            # 校验 sha256（如果 checksums 可用）
            if checksum_url:
                try:
                    c = requests.get(checksum_url, timeout=15)
                    if c.status_code != 200:
                        raise RuntimeError(f'checksums status: {c.status_code}')
                    expected = None
                    for line in c.text.splitlines():
                        parts = line.strip().split()
                        if len(parts) >= 2 and parts[-1].endswith(f'linux_{goarch}.tar.gz'):
                            expected = parts[0]
                            break
                    if expected:
                        import hashlib
                        h = hashlib.sha256()
                        with open(tar_path, 'rb') as f:
                            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                                h.update(chunk)
                        actual = h.hexdigest()
                        if actual.lower() != expected.lower():
                            return False, 'Checksum mismatch (download may be corrupted)', resolved_tag or None
                except Exception as e:
                    # 不阻断更新，但记录一下
                    print(f'Warning: checksum verify skipped/failed: {e}')

            # 解压并找到二进制
            try:
                def _safe_extract(tar, target_dir):
                    target_dir_abs = os.path.abspath(target_dir)
                    for member in tar.getmembers():
                        # 防御更严格：拒绝符号链接/硬链接，避免“先解出链接再写文件”绕过路径校验
                        if getattr(member, 'issym', lambda: False)() or getattr(member, 'islnk', lambda: False)():
                            raise RuntimeError(f'Unsafe link in tar: {member.name}')
                        member_path = os.path.abspath(os.path.join(target_dir_abs, member.name))
                        if not member_path.startswith(target_dir_abs + os.sep) and member_path != target_dir_abs:
                            raise RuntimeError(f'Unsafe path in tar: {member.name}')
                    for member in tar.getmembers():
                        tar.extract(member, target_dir_abs)

                with tarfile.open(tar_path, 'r:gz') as tf:
                    _safe_extract(tf, tmpdir)
            except Exception as e:
                return False, f'Extract failed: {e}', resolved_tag or None

            def _looks_like_elf(path: str) -> bool:
                try:
                    if not os.path.isfile(path):
                        return False
                    with open(path, 'rb') as f:
                        head = f.read(4)
                    return head == b'\x7fELF'
                except Exception:
                    return False

            def _find_extracted_binary(extract_root: str) -> str:
                preferred_names = [
                    'cli-proxy-api',
                    'cliproxyapi',
                    'cliproxy',
                    'CLIProxyAPI',
                    'cli_proxy_api',
                ]

                # 1) 精确名字优先
                by_name = []
                for root, _, files in os.walk(extract_root):
                    for name in files:
                        if name in preferred_names:
                            by_name.append((preferred_names.index(name), os.path.join(root, name)))
                by_name.sort(key=lambda x: x[0])
                for _, p in by_name:
                    try:
                        if _looks_like_elf(p) or os.path.getsize(p) > 128 * 1024:
                            return p
                    except Exception:
                        continue

                # 2) 兜底：找 ELF 可执行文件
                elf_paths = []
                for root, _, files in os.walk(extract_root):
                    for name in files:
                        p = os.path.join(root, name)
                        if _looks_like_elf(p):
                            elf_paths.append(p)

                if len(elf_paths) == 1:
                    return elf_paths[0]

                if elf_paths:
                    def score(p: str) -> tuple:
                        base = os.path.basename(p).lower()
                        s = 0
                        if 'cliproxy' in base:
                            s += 3
                        if 'proxy' in base:
                            s += 2
                        if 'api' in base:
                            s += 2
                        try:
                            s_size = os.path.getsize(p)
                        except Exception:
                            s_size = 0
                        return (s, s_size)

                    elf_paths.sort(key=score, reverse=True)
                    return elf_paths[0]

                return ''

            extracted_bin = _find_extracted_binary(tmpdir)
            if not extracted_bin:
                return False, 'No binary found in release package', resolved_tag or None

            # 原子替换
            tmp_target = f'{binary_path}.tmp'
            shutil.copy2(extracted_bin, tmp_target)
            os.chmod(tmp_target, 0o755)
            os.replace(tmp_target, binary_path)

        tag_for_message = resolved_tag or 'unknown'
        return True, f'Release updated to {tag_for_message} for linux_{goarch}', resolved_tag or None
    except Exception as e:
        return False, f'Release update error: {e}', None

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
    has_update = check_for_updates()
    log_requests = get_request_count_from_logs()
    snapshot = fetch_usage_snapshot()
    token_totals, usage_reqs = aggregate_usage_snapshot(snapshot)
    pricing, pricing_meta = get_effective_pricing()

    # 获取当前 CLIProxyAPI 的值
    current_input = token_totals.get('input_tokens', 0)
    current_output = token_totals.get('output_tokens', 0)
    current_cached = token_totals.get('cached_tokens', 0)
    current_requests = usage_reqs.get('total_requests', 0) or 0
    current_success = usage_reqs.get('success', 0) or 0
    current_failure = usage_reqs.get('failure', 0) or 0

    # 获取上次快照值
    last = state.get('last_snapshot', {})
    last_input = last.get('input_tokens', 0)
    last_output = last.get('output_tokens', 0)
    last_cached = last.get('cached_tokens', 0)
    last_requests = last.get('total_requests', 0)
    last_success = last.get('success', 0)
    last_failure = last.get('failure', 0)

    # 计算增量（如果当前值 < 上次值，说明 CLIProxyAPI 重启了，增量就是当前值）
    if current_input >= last_input:
        delta_input = current_input - last_input
    else:
        delta_input = current_input  # CLIProxyAPI 重启了
    
    if current_output >= last_output:
        delta_output = current_output - last_output
    else:
        delta_output = current_output
    
    if current_cached >= last_cached:
        delta_cached = current_cached - last_cached
    else:
        delta_cached = current_cached
    
    if current_requests >= last_requests:
        delta_requests = current_requests - last_requests
    else:
        delta_requests = current_requests
    
    if current_success >= last_success:
        delta_success = current_success - last_success
    else:
        delta_success = current_success
    
    if current_failure >= last_failure:
        delta_failure = current_failure - last_failure
    else:
        delta_failure = current_failure

    # 累加到面板的统计数据
    acc = state.get('accumulated_stats', {})
    acc['input_tokens'] = acc.get('input_tokens', 0) + delta_input
    acc['output_tokens'] = acc.get('output_tokens', 0) + delta_output
    acc['cached_tokens'] = acc.get('cached_tokens', 0) + delta_cached
    acc['total_requests'] = acc.get('total_requests', 0) + delta_requests
    acc['success'] = acc.get('success', 0) + delta_success
    acc['failure'] = acc.get('failure', 0) + delta_failure
    state['accumulated_stats'] = acc

    # 更新上次快照值
    state['last_snapshot'] = {
        'input_tokens': current_input,
        'output_tokens': current_output,
        'cached_tokens': current_cached,
        'total_requests': current_requests,
        'success': current_success,
        'failure': current_failure,
    }

    # 使用累计值作为显示值
    display_input_tokens = acc['input_tokens']
    display_output_tokens = acc['output_tokens']
    display_cached_tokens = acc['cached_tokens']
    display_total_tokens = display_input_tokens + display_output_tokens
    display_total_requests = acc['total_requests']
    display_success = acc['success']
    display_failure = acc['failure']

    # 使用显示值计算费用
    display_token_totals = {
        'input_tokens': display_input_tokens,
        'output_tokens': display_output_tokens,
        'cached_tokens': display_cached_tokens,
    }
    usage_costs = compute_usage_costs(display_token_totals, pricing)

    with stats_lock:
        state['stats']['input_tokens'] = display_input_tokens
        state['stats']['output_tokens'] = display_output_tokens
        state['stats']['cached_tokens'] = display_cached_tokens

    # 触发持久化保存
    save_persistent_stats()

    # 如果没有从 API 获取到请求数，使用日志统计
    final_count = display_total_requests if display_total_requests > 0 else log_requests.get('count', 0)
    final_success = display_success if display_success > 0 else log_requests.get('success', 0)
    final_failed = display_failure if display_failure > 0 else log_requests.get('failed', 0)

    return jsonify({
        'panel': {
            'name': PANEL_NAME,
            'version': f'v{PANEL_VERSION}',
        },
        'service': service,
        'version': {
            'current': state['current_version'],
            'latest': state['latest_version'],
            'has_update': has_update
        },
        'requests': {
            'count': final_count,
            'last_time': log_requests.get('last_time'),
            'success': final_success,
            'failed': final_failed,
            'is_idle': is_idle(),
            'input_tokens': display_input_tokens,
            'output_tokens': display_output_tokens,
            'cached_tokens': display_cached_tokens,
            'total_tokens': display_total_tokens,
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
        'pricing_meta': pricing_meta,
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
        cache.invalidate('request_count_logs')
    except Exception:
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


@app.route('/api/config/pricing-auto', methods=['POST'])
def api_set_pricing_auto():
    """开启/关闭 Token 价格自动同步（默认开启；关闭后严格使用手动价格）"""
    data = request.json or {}
    enabled_raw = data.get('enabled', CONFIG.get('pricing_auto_enabled', True))
    enabled = enabled_raw if isinstance(enabled_raw, bool) else _parse_bool(enabled_raw)
    CONFIG['pricing_auto_enabled'] = enabled
    _update_dotenv_values({'pricing_auto_enabled': enabled})
    # 返回当前 effective 价格，方便前端立即刷新显示
    effective, pricing_meta = get_effective_pricing()
    return jsonify({'success': True, 'pricing_auto_enabled': enabled, 'effective_pricing': effective, 'pricing_meta': pricing_meta})


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
        # 手动保存后，effective 价格也会随之变化（除非仍为 0 且开启自动同步）
        effective, pricing_meta = get_effective_pricing()
        return jsonify({
            'success': True,
            'pricing': {'input': input_price, 'output': output_price, 'cache': cache_price},
            'effective_pricing': effective,
            'pricing_meta': pricing_meta,
        })

    manual = {
        'input': _safe_float(CONFIG.get('pricing_input', 0.0)),
        'output': _safe_float(CONFIG.get('pricing_output', 0.0)),
        'cache': _safe_float(CONFIG.get('pricing_cache', 0.0)),
    }
    effective, pricing_meta = get_effective_pricing()
    return jsonify({
        'success': True,
        'pricing': manual,
        'effective_pricing': effective,
        'pricing_meta': pricing_meta,
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
    """清空请求统计（清空累计值，更新快照为当前值）"""
    # 先获取当前 CLIProxyAPI 的值，作为新的快照起点
    snapshot = fetch_usage_snapshot(use_cache=False)
    token_totals, usage_reqs = aggregate_usage_snapshot(snapshot)

    # 更新快照为当前值（这样下次计算增量时从0开始）
    state['last_snapshot'] = {
        'input_tokens': token_totals.get('input_tokens', 0),
        'output_tokens': token_totals.get('output_tokens', 0),
        'cached_tokens': token_totals.get('cached_tokens', 0),
        'total_requests': usage_reqs.get('total_requests', 0) or 0,
        'success': usage_reqs.get('success', 0) or 0,
        'failure': usage_reqs.get('failure', 0) or 0,
    }

    # 清空累计统计值
    state['accumulated_stats'] = {
        'input_tokens': 0,
        'output_tokens': 0,
        'cached_tokens': 0,
        'total_requests': 0,
        'success': 0,
        'failure': 0,
    }

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

    # 清空 usage_snapshot.json
    usage_path = CONFIG.get('usage_snapshot_path')
    if usage_path and os.path.exists(usage_path):
        try:
            os.remove(usage_path)
        except Exception as e:
            print(f"Error removing usage snapshot: {e}")

    # 清除所有缓存
    try:
        cache.invalidate()
    except Exception:
        pass

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

    print(f'{PANEL_NAME} Panel v{PANEL_VERSION} started on port {CONFIG["panel_port"]}')
    app.run(host=str(CONFIG.get('bind_host', '0.0.0.0') or '0.0.0.0'), port=CONFIG['panel_port'], debug=False)
