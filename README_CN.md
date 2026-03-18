# CPA-X 管理面板（v2.1.1）

[English](README.md) | 中文

一个用于 **CLIProxyAPI** 的监控与管理面板，支持健康检查、资源监控、日志查看、更新管理、请求统计与定价显示等功能。

> 当前安全策略：**前端已移除所有导出入口，主配置写回默认关闭**；配置区仅保留查看/校验能力，如确需恢复写回，必须在 `.env` 中显式设置 `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true`。

> **AI 优先**：本仓库主要面向 AI Agent 部署/运维（而不是人类手动部署）。
> - AI 部署手册：`AI_DEPLOY_CN.md`
> - Agent 指引：`AGENTS.md`
> - 更新说明：`RELEASE_NOTES_v2.1.1.md`

## 预览图

### 深色主题
![CPA-X 深色预览](docs/images/preview-dark.png)

### 浅色主题
![CPA-X 浅色预览](docs/images/preview-light.png)

## 适用环境
- **推荐：Linux**（面板含 `systemctl` 相关功能）
- Python 3.11+
- 需要能访问 CLIProxyAPI 的管理接口（默认 `http://127.0.0.1:8317`）

> Windows 也可以运行，但"服务控制/自动更新"等 systemd 相关功能不可用。

## 一条龙安装（新手版）

### 0) 一键安装（推荐）
```bash
# Linux（会自动注册 systemd 服务；安装器会尽力自动探测并补齐 .env）
bash scripts/install.sh

# 如需手动再跑一次自动探测（推荐）
python3 scripts/doctor.py --write-env
```

```powershell
# Windows（后台启动）
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

### 1) 克隆项目
```bash
git clone https://github.com/ferretgeek/CPA-X.git
cd CPA-X
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
- `CLIPROXY_PANEL_MANAGEMENT_KEY` / `CLIPROXY_PANEL_MODELS_API_KEY`（如上游启用了密钥）
- `CLIPROXY_PANEL_CLIPROXY_SERVICE` / `CLIPROXY_PANEL_CLIPROXY_BINARY`（自动更新需要）
- `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED`（默认 `false`；只在你明确接受风险时才开启主配置写回）
- `CLIPROXY_PANEL_GITHUB_TOKEN`（可选：提高 GitHub 限流额度，减少 `latest=unknown`）
- `CLIPROXY_PANEL_PRICING_*`（可选：费用估算；默认支持自动同步 OpenRouter 定价，可用 `CLIPROXY_PANEL_PRICING_AUTO_ENABLED=false` 关闭）

### 4) 启动面板
```bash
python app.py
```

打开浏览器访问：
```
http://127.0.0.1:8080
```

## Docker/容器部署（可选）

适用场景：你只需要“监控与查看”（状态/统计/模型/日志/配置读取）。  
不适用场景：你需要“自动更新/服务控制”（容器里通常没有 systemd，默认做不了）。

仓库已提供：
- `Dockerfile`
- `docker-compose.yml`
- `.env.docker.example`

最短路径（推荐用 compose）：
```bash
docker compose up -d --build
```

如需“日志/配置/auth 文件”等功能，请按 `docker-compose.yml` 的注释挂载宿主机文件/目录，并把相关 `CLIPROXY_PANEL_*` 环境变量改成容器内路径。注意：这里的“配置”默认仅支持读取与校验，不会写回宿主机主配置。

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
- 面板当前默认监听 `0.0.0.0`，方便局域网访问；如果只用于本机，建议把 `CLIPROXY_PANEL_BIND_HOST` 改成 `127.0.0.1`
- 如需对面板加一道访问门槛，可设置 `CLIPROXY_PANEL_PANEL_ACCESS_KEY`（启用后 `/api/*` 需要 `X-Panel-Key` 或 URL 参数 `panel_key`）
- 前端已移除所有导出入口，避免把敏感内容通过浏览器下载链接暴露出去
- 主配置写回默认关闭；如你非常确定要恢复，才在 `.env` 中显式设置 `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true`

## 许可协议
MIT License（见 `LICENSE`）
