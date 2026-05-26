# CPA-X v2.1.2 Release Notes / 更新说明

## 中文

这是一次稳定性与安全保护补丁，重点修复自动备份堆积、CPA 管理密钥错误重试、以及默认语录库缺失问题。

### 修复

- 自动更新创建 CLIProxyAPI 二进制备份后，现在只保留最近 5 个备份。
- 旧的同名备份会自动清理，避免长时间运行后备份文件无限增长。
- CPA 管理密钥连续 10 次返回 401/403 后，面板会暂停继续请求管理接口，避免错误密钥无限重试导致上游封禁。
- 保存新的 CPA 管理密钥后，会自动解除暂停并重置失败计数。

### 改进

- 面板新增显眼的 CPA 管理密钥错误横幅，并提供手动保存新密钥入口。
- `/api/status` 和 `/api/config/management-key` 会返回管理密钥状态，便于前端提示和自动化检查。
- 默认名人语录库改为项目根目录 `X.txt`，并随仓库发布，避免默认语录为空。
- `.env.example`、`.env.docker.example` 和 README 已补充 `CLIPROXY_PANEL_QUOTES_PATH` 说明。

## English

This is a stability and safety patch focused on backup retention, CPA management key retry protection, and the default quote library.

### Fixes

- Auto-update binary backups now keep only the latest 5 backup files.
- Older matching backups are removed automatically to prevent unbounded disk growth.
- After 10 consecutive 401/403 responses from the CPA management API, the panel stops further management API requests to avoid repeated invalid-key retries.
- Saving a new CPA management key resets the failure counter and unlocks management API requests.

### Improvements

- Added a prominent management-key error banner with a manual key update field.
- `/api/status` and `/api/config/management-key` now expose management key health for UI and automation checks.
- The default quote library now uses repository-root `X.txt`, which is included in the release.
- `.env.example`, `.env.docker.example`, and README now document `CLIPROXY_PANEL_QUOTES_PATH`.
