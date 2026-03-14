# CPA-X v2.0.0 Release Notes / 更新说明

> 本文件用于 GitHub Release 与 README 的同步更新（中英双语）。  
> This file is intended to keep GitHub Releases and README in sync (bilingual).

---

## 中文（v2.0.0）

### 亮点（面向 AI 部署/运维）

- 新增 `scripts/doctor.py`：自动探测 systemd unit / 二进制路径 / config.yaml / auth_dir / 日志路径，并 **只补缺省值** 写入 `.env`（避免覆盖用户显式配置）。
- 仓库文档升级为 AI-first：`AGENTS.md`、`AI_DEPLOY_CN.md` 提供可执行闭环与验收方式。
- 增加 Docker/容器部署支持：`Dockerfile`、`docker-compose.yml`、`.env.docker.example`、`.dockerignore`（明确容器场景的功能边界）。

### 正确性与体验

- Token 口径修正：`total_tokens` 不再重复计入 `cached_tokens`；费用计算不再对 cached 重复计费。
- Tokens 单位自适应：总 tokens 显示按规模自动切换 **百万 → 千万 → 亿**（超过亿固定为亿）。
- 自动更新设置体验修复：输入框不再被 5 秒轮询刷新“打回原值”。

### 更新稳定性

- GitHub 限流鲁棒性：更新检测/下载在 API 失败时具备 fallback，减少 `latest=unknown`。
- Release 更新增强：支持多种二进制名识别；更新前备份，启动失败自动回滚。
- 安全解压：防止 tar 路径穿越与链接条目。

### 定价（费用估算）

- 新增“价格自动同步”（默认开启）：当手动价格为 0 时，从 OpenRouter 同步权威定价并换算为“美元/百万Tokens”；手动价格仍然优先，可一键关闭自动同步。

### 安全默认值（可能影响部署方式）

- 面板默认监听 `127.0.0.1`（更安全）。如需局域网访问，请显式设置 `CLIPROXY_PANEL_BIND_HOST=0.0.0.0`。
- 可选设置 `CLIPROXY_PANEL_PANEL_ACCESS_KEY`，启用后 `/api/*` 需要携带 `X-Panel-Key` 或 `panel_key`。

---

## English (v2.0.0)

### Highlights (AI-first deployment & operations)

- Added `scripts/doctor.py`: auto-detects systemd unit / binary / config.yaml / auth_dir / log path and writes missing defaults into `.env` (never overwrites non-empty values by default).
- Upgraded documentation for AI agents: `AGENTS.md` and `AI_DEPLOY_CN.md` describe the full deploy + verify loop.
- Added Docker/container support: `Dockerfile`, `docker-compose.yml`, `.env.docker.example`, `.dockerignore` (with clear feature boundaries for container mode).

### Correctness & UX

- Fixed token accounting: `total_tokens` no longer double-counts `cached_tokens`; cost estimation no longer double-charges cached tokens.
- Adaptive token units: total tokens auto switches **M → 10M → 100M** (stays at 100M scale above that).
- Fixed auto-update settings UX: inputs no longer get overwritten by the 5s polling refresh.

### Update robustness

- GitHub rate-limit resilience: version check/download includes a fallback path to reduce `latest=unknown`.
- Release update hardening: supports multiple binary names; backup + rollback on start failure.
- Safer tar extraction: blocks path traversal and link entries.

### Pricing (cost estimation)

- Auto pricing sync (enabled by default): when manual pricing is 0, fetches authoritative pricing from OpenRouter and converts to “USD per 1M tokens”. Manual overrides remain priority, and auto sync can be disabled.

### Security defaults (may affect deployments)

- Default bind host is `127.0.0.1` (safer). For LAN access, set `CLIPROXY_PANEL_BIND_HOST=0.0.0.0`.
- Optional `CLIPROXY_PANEL_PANEL_ACCESS_KEY`: when set, `/api/*` requires `X-Panel-Key` or `panel_key`.

