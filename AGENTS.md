# 给 AI 的部署与对接指引（必读）

本仓库的默认使用者是 **AI Agent**（而不是人类运维）。你的目标是：在一台已经安装并运行了 **CLIProxyAPI / cliproxyapi** 的设备上，把本面板部署起来，并保证：

- 面板可访问（静态页面 + `/api/*` 正常）
- 统计数据正确（input/output/cached/total）
- 自动更新可用（能停止/替换二进制/拉起服务）
- 配置查看/校验、日志、模型等功能都能用

## 1) 最快可用（推荐路径）

在目标机器上执行（**宿主机 / systemd 部署**，可用全部功能）：

1. `bash scripts/install.sh`
2. 如果机器上已有 CLIProxyAPI，接着执行：`python3 scripts/doctor.py --write-env`
3. 重启面板服务（如果你用 systemd 安装）：`systemctl restart cliproxy-panel`

> `scripts/doctor.py` 会尽可能自动探测当前设备已有的 CLIProxyAPI（systemd 服务、config 路径、binary 路径、auth_dir、log 路径），并写入 `.env`。

## 1.1) Docker/容器部署（需要在文档里明确的限制）

如果你用 Docker/容器部署面板，请先做出选择：

- **要“全功能”（自动更新/服务控制）**：不要用容器。因为容器里通常没有 systemd，也不具备操作宿主机服务与替换宿主机二进制的权限。
- **只要“监控与查看”（状态/统计/模型/日志/配置读取）**：可以用容器，但你必须显式提供：
  - 面板监听：`CLIPROXY_PANEL_BIND_HOST=0.0.0.0`（否则端口映射无法访问）
  - 上游管理接口地址：`CLIPROXY_PANEL_CLIPROXY_API_BASE` / `CLIPROXY_PANEL_CLIPROXY_API_PORT`
  - 需要读取的文件：把 `config.yaml`、`main.log`、`auth_dir` 等挂载进容器，并把对应环境变量指向容器内路径

推荐直接用仓库内的：

- `Dockerfile`
- `docker-compose.yml`
- `.env.docker.example`

## 1.2) 其他部署方式（无 systemd）

如果目标环境没有 systemd（或你不想安装服务），可以直接运行面板进程，但自动更新/服务控制会受限：

- 直接运行：`python3 app.py`（或用虚拟环境：`.venv/bin/python app.py`）
- 建议：使用 supervisor/pm2 等进程管理器做守护重启（由环境决定）

## 2) 你必须知道/补齐的“密钥”

自动探测无法凭空得到明文密钥（因为 CLIProxyAPI 配置里通常存的是 hash）。

为了让面板“全功能”正常，通常至少要补齐：

- `CLIPROXY_PANEL_MANAGEMENT_KEY`：用于访问 `http://127.0.0.1:8317/v0/management/*`
- `CLIPROXY_PANEL_MODELS_API_KEY`：用于访问 `http://127.0.0.1:8317/v1/models`

常见做法：这两个值相同（比如同一个 key），那就都填同一个即可。

## 3) 全功能所需的关键环境变量（.env）

`scripts/doctor.py` 会自动写大部分路径/服务名，但你需要确保这些最终正确：

- 面板自身
  - `CLIPROXY_PANEL_PANEL_PORT`：面板端口
  - `CLIPROXY_PANEL_BIND_HOST`：当前默认 `0.0.0.0`（方便局域网访问）；如只本机访问可改成 `127.0.0.1`
  - `CLIPROXY_PANEL_PANEL_ACCESS_KEY`：可选；设置后 `/api/*` 需要 `X-Panel-Key` 或 URL `?panel_key=...`
  - `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED`：主配置写回开关；默认 `false`，只有你明确接受风险时才改成 `true`

- 费用估算（可选，但建议配置）
  - `CLIPROXY_PANEL_PRICING_INPUT` / `CLIPROXY_PANEL_PRICING_OUTPUT` / `CLIPROXY_PANEL_PRICING_CACHE`：手动价格（美元/百万Tokens）
  - `CLIPROXY_PANEL_PRICING_AUTO_ENABLED`：是否启用自动同步（默认开启；手动价格为 0 时会尝试从 OpenRouter 补齐）

- CLIProxyAPI 对接
  - `CLIPROXY_PANEL_CLIPROXY_SERVICE`：systemd 服务名（自动更新/启动停止依赖它）
  - `CLIPROXY_PANEL_CLIPROXY_BINARY`：二进制路径（自动更新替换依赖它）
  - `CLIPROXY_PANEL_CLIPROXY_CONFIG`：config.yaml 路径（配置读取/校验依赖它；主配置写回默认禁用）
  - `CLIPROXY_PANEL_AUTH_DIR`：auth 目录（凭证文件列表/健康检查依赖它）
  - `CLIPROXY_PANEL_CLIPROXY_LOG`：日志文件（请求统计/日志面板依赖它）
  - `CLIPROXY_PANEL_CLIPROXY_API_BASE` / `CLIPROXY_PANEL_CLIPROXY_API_PORT`：管理接口地址

## 4) 自检（你应该在部署后立即跑）

用 curl 验证（示例 key 请替换）：

- 管理接口：
  - `curl -sS -o /dev/null -w '%{http_code}\n' -H 'X-Management-Key: <KEY>' http://127.0.0.1:8317/v0/management/usage`
- 模型列表：
  - `curl -sS -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer <KEY>' http://127.0.0.1:8317/v1/models`
- 面板状态：
  - `curl -sS http://127.0.0.1:<PANEL_PORT>/api/status | head -c 200`

## 5) 自动更新为什么会失败（常见原因）

- GitHub API 限流（未认证只有 60 次/小时）：建议配置 `CLIPROXY_PANEL_GITHUB_TOKEN`（可选）
- 面板无 root 权限：无法 `systemctl stop/start`、无法写入二进制路径
- 服务名不对：`CLIPROXY_PANEL_CLIPROXY_SERVICE` 需要与真实 unit 一致

## 6) 面板面向 AI 的设计约束

- 文档优先“可执行”与“可验证”（给命令/期望输出/失败处理）
- 默认值优先“可部署”与“高风险操作默认关闭”（主配置写回默认关闭；如需只本机访问请显式把监听地址改成 `127.0.0.1`）
- 自动探测必须“只补缺省值”，不能破坏用户显式配置
- 不要恢复前端导出入口；主配置写回默认保持关闭
