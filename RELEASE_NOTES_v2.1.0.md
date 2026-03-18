# CPA-X v2.1.0 Release Notes / 更新说明

> This file keeps GitHub Release notes and README references in sync.  
> 这个文件用于让 GitHub Release 和 README 保持同步。

## 中文（v2.1.0）

### 本次重点

- 自动更新卡片新增“状态可视化”：
  - 现在有没有新版本
  - 当前是否空闲
  - 距离满足空闲条件还差多久
  - 距离下一次自动检查还差多久
  - 为什么当前没有触发自动更新

### 后端改动

- 修复空闲判断的时区问题：不再用 `datetime.utcnow()` 计算日志空闲时长，改为按服务器本地时间计算。
- 新增 `get_idle_state()`：统一输出 `is_idle / idle_for_seconds / idle_wait_seconds / last_request_time`。
- 新增 `get_auto_update_state()`：统一输出自动更新阶段、摘要、倒计时、上次检查时间、下次检查时间。
- `auto_update_worker()` 现在会记录：
  - `last_auto_update_check_time`
  - `next_auto_update_check_time`
  - 跳过更新的原因日志

### 前端改动

- 自动更新卡片新增“当前状态”和“详细说明”区域。
- 可直接看到：
  - 等待空闲，还差多久
  - 下一次自动检查还差多久
  - 最近请求时间
  - 上次自动检查时间

### 部署与文档同步

- 面板版本统一升级为 `v2.1.0`。
- README 中英文、安装描述、前端界面版本徽标、GitHub Release Notes 已同步到 `v2.1.0`。
- 当前默认监听地址说明同步为 `0.0.0.0`；如只想本机访问，请显式改成 `127.0.0.1`。
- 当前安全策略已同步到文档：
  - 前端移除所有导出入口
  - 主配置写回默认关闭，需要显式设置 `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true` 才会恢复
- README 预览图已更新为仓库内置的最新界面截图（深色 / 浅色）。
- 已清理过期历史说明文档，避免与当前文档混淆。

## English (v2.1.0)

### Highlights

- Added auto-update status visualization:
  - whether a new release is available
  - whether the system is currently idle
  - how long until the idle condition is satisfied
  - how long until the next automatic check
  - why auto-update has not triggered yet

### Backend

- Fixed idle-time calculation timezone issue by using local server time instead of `datetime.utcnow()`.
- Added `get_idle_state()` for a single source of truth around idle timing.
- Added `get_auto_update_state()` for summarized auto-update state, countdowns, and timestamps.
- `auto_update_worker()` now records:
  - `last_auto_update_check_time`
  - `next_auto_update_check_time`
  - explicit skip reasons in logs

### Frontend

- The auto-update card now shows a short status line and a detailed explanation block.
- Users can now directly see:
  - how long until the system becomes idle
  - how long until the next automatic check
  - last request time
  - last auto-update check time

### Sync

- Panel version bumped to `v2.1.0`.
- English README, Chinese README, install metadata, UI badge, and GitHub Release notes are now synced to `v2.1.0`.
- Default bind-host documentation is synced to `0.0.0.0`; switch to `127.0.0.1` if you want local-only access.
- Current security posture is now reflected in docs:
  - all frontend export entries are removed
  - main-config writeback stays disabled by default unless `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true` is explicitly set
- README preview images are refreshed to the latest built-in UI screenshots (dark / light).
- Outdated historical docs are removed to reduce confusion.
