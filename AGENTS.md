# 给 AI 的部署与对接指引（必读）

本仓库的默认使用者是 **AI Agent**（而不是人类运维）。你的目标是：在一台已经安装并运行了 **CLIProxyAPI / cliproxyapi** 的设备上，把本面板部署起来，并保证：

- 面板可访问（静态页面 + `/api/*` 正常）
- 统计数据正确（input/output/cached/total）
- 自动更新可用（能停止/替换二进制/拉起服务）
- 配置/日志/模型等功能都能用

## 1) 最快可用（推荐路径）

在目标机器上执行：

1. `bash scripts/install.sh`
2. 如果机器上已有 CLIProxyAPI，接着执行：`python3 scripts/doctor.py --write-env`
3. 重启面板服务（如果你用 systemd 安装）：`systemctl restart cliproxy-panel`

> `scripts/doctor.py` 会尽可能自动探测当前设备已有的 CLIProxyAPI（systemd 服务、config 路径、binary 路径、auth_dir、log 路径），并写入 `.env`。

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
  - `CLIPROXY_PANEL_BIND_HOST`：默认建议 `127.0.0.1`（只本机访问）；要开放到局域网再改 `0.0.0.0`
  - `CLIPROXY_PANEL_PANEL_ACCESS_KEY`：可选；设置后 `/api/*` 需要 `X-Panel-Key` 或 URL `?panel_key=...`

- CLIProxyAPI 对接
  - `CLIPROXY_PANEL_CLIPROXY_SERVICE`：systemd 服务名（自动更新/启动停止依赖它）
  - `CLIPROXY_PANEL_CLIPROXY_BINARY`：二进制路径（自动更新替换依赖它）
  - `CLIPROXY_PANEL_CLIPROXY_CONFIG`：config.yaml 路径（配置编辑/导出依赖它）
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
- 所有默认值偏安全（默认只监听本机；API 可选加密钥）
- 自动探测必须“只补缺省值”，不能破坏用户显式配置

