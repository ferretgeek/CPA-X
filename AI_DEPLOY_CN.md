# CPA-X（AI 部署手册）

> 这份文档写给会跑命令的 AI Agent：目标是 **自动把面板部署到设备上**，并与设备上已存在的 **CLIProxyAPI / cliproxyapi** 正常对接。

## 0) 你要解决的“最小闭环”

部署完成后必须满足：

1. `GET /api/healthz` 返回 `ok=true`，`GET /` 能打开页面
2. `GET /api/status` 返回 `health=healthy|degraded|unhealthy` 且不报错
3. `GET /api/models` 能返回 models（如果上游要求 key，必须配置）
4. “请求统计”里的 token 口径正确：`total = input + output (+ reasoning)`，不重复计算 cached
5. 自动更新：在空闲时可完成 `在线准备并校验 -> stop -> 原子替换 -> start -> 稳定性检查`，失败时自动回滚
6. 费用估算有意义：Token 价格非 0（支持自动同步 OpenRouter 定价或手动设置）

## 1) 一键安装（推荐）

在目标机器执行：

```bash
bash scripts/install.sh
python3 scripts/doctor.py --write-env
systemctl restart cliproxy-panel
```

> 如果没有 systemd（或不想装服务），可以直接运行：`python3 app.py`

## 1.1) Docker/容器部署（适合“监控”，不适合“全功能自动更新”）

容器部署的关键现实：

- 容器里通常没有 systemd，也没有权限操作宿主机服务，因此**自动更新/服务控制功能默认不可用**（建议设置 `CLIPROXY_PANEL_AUTO_UPDATE_ENABLED=false`）。
- 容器内服务要被端口映射访问，必须监听 `0.0.0.0`（因此需要 `CLIPROXY_PANEL_BIND_HOST=0.0.0.0`）。

仓库已提供：

- `Dockerfile`
- `docker-compose.yml`
- `.env.docker.example`

你需要确保两类对接都“可达”：

1) 面板 → 上游 CLIProxyAPI 管理接口  
   - 典型场景是上游跑在宿主机：容器里要能访问宿主机地址（Docker Desktop 常用 `host.docker.internal`；Linux 需要做 host-gateway 映射）

2) 面板 → 文件路径（可选但强烈建议）  
   - 如果你希望“日志/配置/auth 文件列表”等功能正常：把宿主机的 `config.yaml`、`main.log`、`auth_dir` 挂载到容器，并把环境变量指向容器内路径（例如 `/mnt/cliproxy/...`）
   - 注意：这里的配置能力默认是**只读/校验**，不会写回宿主机主配置

## 1.2) 非 systemd 部署（例如 nohup/supervisor/pm2）

如果目标机器没有 systemd（或你不想装 systemd 服务），面板仍可运行，但会有功能差异：

- 仍可用：页面、状态、统计、模型、日志/配置读取与校验（前提是 `.env` 中路径与上游地址正确）
- 不可用/受限：`systemctl` 相关的服务控制；自动更新通常不可用（建议设置 `CLIPROXY_PANEL_AUTO_UPDATE_ENABLED=false`）

推荐最小启动方式：

- 用虚拟环境运行：`.venv/bin/python app.py`
- 后台守护：建议交给 supervisor/pm2 等进程管理器（由你的环境决定）

## 2) doctor：自动探测并生成 .env

`scripts/doctor.py` 会尝试：

- 找到已加载的 CLIProxyAPI systemd unit（包括已停止的 unit，如 `cliproxyapi@freecodex.service` 或 `cli-proxy-api.service`）
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

- `GET /api/healthz`：无需面板密钥的最小存活探针，不泄露配置细节
- `GET /api/health`：健康检查与细项
- `GET /api/status`：聚合状态（含版本、统计、更新状态）
- `GET /api/check-update`：检查最新版本
- `POST /api/update`：触发更新（注意需要 root/systemd/二进制可写）

## 5) GitHub 限流的应对

如果未配置 token，GitHub API 可能 403 限流（60 次/小时）。  
面板已内置回退策略，但建议配置：

- `CLIPROXY_PANEL_GITHUB_TOKEN=<PAT>`（只用于读 release 信息，提高限额）

## 5.1) Token 价格自动同步（OpenRouter）

面板用于“费用估算”的 Token 价格口径是 **美元/百万Tokens**。

- 默认开启自动同步：当手动价格为 0 时，面板会从 OpenRouter 获取 `prompt/completion/input_cache_read` 的定价并换算后使用
- 如需严格使用手动价格：设置 `CLIPROXY_PANEL_PRICING_AUTO_ENABLED=false`（或在页面里关闭“自动同步价格”开关）

## 5.2) 跨时区日志与自动更新

- 保持 `CLIPROXY_PANEL_LOG_TIMEZONE=auto` 时，面板会比较 CLIProxy 日志中的本地时间与文件 mtime，按 15 分钟粒度推断 `UTC-12:00` 到 `UTC+14:00` 的偏移。
- 如果部署地使用夏令时或日志文件不可用于推断，显式设置 IANA 时区（例如 `America/New_York`）最可靠。
- 面板对外输出的日志、更新历史和健康检查时间统一为带 `Z` 的 UTC RFC 3339；浏览器负责转换为访问者本地时间。
- 请求日志缺失时，面板不会把“未知”误判成“空闲”，因此自动更新会等待而不是中断可能存在的流量。

## 5.3) 更新与磁盘上限

- 默认仅保留 2 个 CLIProxy 二进制回滚点，同时受 14 天和 512 MiB 总逻辑大小限制。
- 默认要求 release 的 `checksums.txt` 中存在匹配 SHA-256；校验失败不会停服务或替换二进制。
- “清空统计”不再复制或截断巨型服务日志；页面日志区的“清空”也只影响当前浏览器显示。

## 5.4) CLIProxyAPI v6/v7 用量兼容

- v6 的累计 `/v0/management/usage` 与 v7 的 `/v0/management/usage-queue` 会被自动识别，切换计数模式时先建立基线，避免重复累计。
- v7 队列默认只短期保留记录，面板会每 15 秒拉取并立即原子持久化；必须在 CLIProxyAPI 的 `config.yaml` 中设置 `usage-statistics-enabled: true`。
- `/api/health` 会在该开关被显式关闭时给出警告。

## 6) 安全默认值（AI 不要破坏）

- 当前默认 `CLIPROXY_PANEL_BIND_HOST=0.0.0.0`
- 如需外网访问，建议同时设置 `CLIPROXY_PANEL_PANEL_ACCESS_KEY`
- 跨域 API 默认关闭；只为明确来源配置 `CLIPROXY_PANEL_CORS_ORIGINS`
- 前端已移除导出入口，不要再帮用户恢复浏览器侧导出按钮
- 主配置写回默认关闭；如用户明确要求恢复，才引导其在 `.env` 中设置 `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true`
