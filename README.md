# CPA-XXX 管理面板
（本面板完全是vibe coding的产物。初期构建由opus-4.5完成，后续修改更新由gpt5.2-codex-high完成）

一个用于 **CLIProxyAPI** 的监控与管理面板，浅色深色双主题，支持健康检查、资源监控、日志查看、更新管理、请求统计与定价显示等功能。
<img width="2555" height="1230" alt="CPA-XXX" src="https://github.com/user-attachments/assets/07a2ed19-7a93-4e43-9057-3d12f1737188" />
<img width="2554" height="1228" alt="CPA-XXX-2" src="https://github.com/user-attachments/assets/0b1d502f-e66d-4a73-a55e-b50c4e6bf760" />


## 适用环境
- **推荐：Linux**（面板含 `systemctl` 相关功能）
- Python 3.11+
- 需要能访问 CLIProxyAPI 的管理接口（默认 `http://127.0.0.1:8317`）

> Windows 也可以运行，但“服务控制/自动更新”等 systemd 相关功能不可用。

## 一条龙安装（新手版）

### 0) 一键安装（推荐）
```bash
# Linux（会自动注册 systemd 服务）
bash scripts/install.sh
```

```powershell
# Windows（后台启动）
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

### 1) 克隆项目
```bash
git clone <你的仓库地址>
cd CPA-XXX
```

### 2) 创建虚拟环境并安装依赖
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 3) 配置环境变量
复制示例文件并按需修改：
```bash
# Windows
copy .env.example .env
# Linux / macOS
cp .env.example .env
```

重点配置：
- `CLIPROXY_PANEL_CLIPROXY_DIR` / `CLIPROXY_PANEL_CLIPROXY_CONFIG`
- `CLIPROXY_PANEL_CLIPROXY_LOG`
- `CLIPROXY_PANEL_CLIPROXY_API_BASE` / `CLIPROXY_PANEL_CLIPROXY_API_PORT`
- `CLIPROXY_PANEL_MANAGEMENT_KEY`（如 CLIProxy API 有管理密钥）

### 4) 启动面板
```bash
python app.py
```

打开浏览器访问：
```
http://127.0.0.1:8080
```

## 常见问题
### 1) 页面能打开但数据为空
检查 CLIProxy 是否在运行，并确认 `.env` 中的 `CLIPROXY_PANEL_CLIPROXY_API_BASE/PORT` 指向正确。

### 2) 健康检查超时
`/api/status` 会触发更多检查，首次可能稍慢；可先用 `/api/resources` 验证服务可访问。

### 3) systemd 相关功能不可用
这是 Linux 专用功能，Windows 环境下会自动失败但不会影响面板启动。

## 安全提示
- **不要把 `.env` 提交到仓库**（已在 `.gitignore` 中忽略）
- 管理密钥、模型密钥等敏感字段请只放在 `.env`

## 许可协议
MIT License（见 `LICENSE`）
