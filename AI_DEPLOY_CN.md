# CPA-X（AI 部署手册）

> 这份文档写给会跑命令的 AI Agent：目标是 **自动把面板部署到设备上**，并与设备上已存在的 **CLIProxyAPI / cliproxyapi** 正常对接。

## 0) 你要解决的“最小闭环”

部署完成后必须满足：

1. `GET /` 能打开页面
2. `GET /api/status` 返回 `health=healthy|degraded|unhealthy` 且不报错
3. `GET /api/models` 能返回 models（如果上游要求 key，必须配置）
4. “请求统计”里的 token 口径正确：`total = input + output (+ reasoning)`，不重复计算 cached
5. 自动更新：在空闲时可完成 `stop -> 下载 -> 替换二进制 -> start`

## 1) 一键安装（推荐）

在目标机器执行：

```bash
bash scripts/install.sh
python3 scripts/doctor.py --write-env
systemctl restart cliproxy-panel
```

> 如果没有 systemd（或不想装服务），可以直接运行：`python3 app.py`

## 2) doctor：自动探测并生成 .env

`scripts/doctor.py` 会尝试：

- 找到正在运行的 CLIProxyAPI systemd unit（如 `cliproxyapi@freecodex.service` 或 `cli-proxy-api.service`）
- 解析 `ExecStart`，推导：
  - `CLIPROXY_PANEL_CLIPROXY_BINARY`
  - `CLIPROXY_PANEL_CLIPROXY_CONFIG`
- 读取 config.yaml（如果可读），推导：
  - `CLIPROXY_PANEL_AUTH_DIR`
  - `CLIPROXY_PANEL_CLIPROXY_API_PORT`
- 在常见位置寻找日志文件（如 `.../logs/main.log` 或 `.../auths/logs/main.log`）

但它**不会**自动填明文密钥，因此你必须人工/外部注入：

- `CLIPROXY_PANEL_MANAGEMENT_KEY`
- `CLIPROXY_PANEL_MODELS_API_KEY`

## 3) 常见设备形态（给 AI 的映射模板）

### A) Ubuntu/Debian + 模板服务

- unit：`cliproxyapi@<instance>.service`
- config：`/etc/cliproxyapi/<instance>/config.yaml`
- working dir：`/var/lib/cliproxyapi/<instance>`
- auth：`/var/lib/cliproxyapi/<instance>/auths`
- log（常见）：`/var/lib/cliproxyapi/<instance>/auths/logs/main.log`

### B) Armbian/N1 + 单服务

- unit：`cli-proxy-api.service`
- config：`/var/lib/cli-proxy-api/config.yaml`
- working dir：`/var/lib/cli-proxy-api`
- auth：`/var/lib/cli-proxy-api/auths`
- log（常见）：`/var/lib/cli-proxy-api/logs/main.log`

## 4) 面板 API 的“可验证性”

面板提供了这些关键 API（用于你做自动化验收）：

- `GET /api/health`：健康检查与细项
- `GET /api/status`：聚合状态（含版本、统计、更新状态）
- `GET /api/check-update`：检查最新版本
- `POST /api/update`：触发更新（注意需要 root/systemd/二进制可写）

## 5) GitHub 限流的应对

如果未配置 token，GitHub API 可能 403 限流（60 次/小时）。  
面板已内置回退策略，但建议配置：

- `CLIPROXY_PANEL_GITHUB_TOKEN=<PAT>`（只用于读 release 信息，提高限额）

## 6) 安全默认值（AI 不要破坏）

- 默认 `CLIPROXY_PANEL_BIND_HOST=127.0.0.1`
- 如需外网访问，建议同时设置 `CLIPROXY_PANEL_PANEL_ACCESS_KEY`

