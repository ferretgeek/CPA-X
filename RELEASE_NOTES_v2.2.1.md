# CPA-X v2.2.1 — 更新健康检查与 502 稳定性修复

本版本来自一次真实生产故障回归。CLIProxyAPI 可选插件被删除后，外部 systemd `ExecStartPost` 恢复钩子仍然残留，使主服务反复经历“短暂可用—启动超时—重启”，最终表现为间歇 `502 Bad Gateway`。

该残留属于服务器 unit 生命周期问题，必须在目标服务器清理；CPA-X v2.2.1 同时修复了会放大或掩盖此类故障的更新、版本检查和兼容轮询行为。

## 重点修复

- 更新成功不再只判断进程或 systemd 状态。CLIProxyAPI 必须为 `active`，且携带 Management Key 请求 `/v0/management/config` 返回 HTTP `200`。
- 新版本健康检查失败时自动恢复旧二进制；回滚后的旧版本也必须通过相同的真实管理接口检查。
- 同一版本更新失败后默认等待 6 小时，连续失败按指数退避，最长 24 小时；状态保存到 `data/auto_update_state.json`，面板重启后仍然有效。
- 新版本出现时自动清除旧版本退避；用户手动强制更新仍可立即执行。
- 未配置 GitHub Token 时优先解析官方 `releases/latest` 跳转，并通过稳定资产名下载 release 与 checksum，不再优先消耗匿名 API 限额。
- 停止启动旧 usage 后台轮询任务，清空统计也不会请求已废弃端点；实时请求数继续来自增量日志，已有 Token/费用历史继续读取本地兼容数据。
- 面板状态新增失败次数、失败版本、下次重试时间与剩余等待秒数，界面会直接说明退避原因。

## 新增默认配置

```text
CLIPROXY_PANEL_AUTO_UPDATE_FAILURE_BACKOFF_SECONDS=21600
CLIPROXY_PANEL_AUTO_UPDATE_FAILURE_BACKOFF_MAX_SECONDS=86400
CLIPROXY_PANEL_SERVICE_HEALTH_TIMEOUT_SECONDS=45
```

旧 `.env` 无需迁移；缺少字段时会自动使用以上默认值。

## 验证

- Python 编译检查通过。
- 29 项 pytest 回归测试通过。
- 新增覆盖：真实管理接口健康检查、健康失败回滚、退避指数与跨重启保存、匿名 Release 跳转优先、废弃 usage 轮询不再启动、清空统计不触发旧接口。

---

## English

CPA-X v2.2.1 hardens the update path after a production incident where a stale external systemd `ExecStartPost` hook repeatedly timed out CLIProxyAPI startup and caused intermittent `502 Bad Gateway` responses.

- Post-update success now requires both an active systemd unit and HTTP `200` from authenticated `/v0/management/config`.
- The restored binary must pass the same check after rollback.
- Failed versions back off for 6 hours initially, exponentially up to 24 hours, with retry state persisted across panel restarts.
- A different release clears stale backoff state; manual forced updates remain available.
- Anonymous release checks prefer GitHub's official `releases/latest` redirect and stable asset URLs before the API.
- Deprecated background usage polling is no longer started, and clearing statistics does not contact removed usage endpoints.
- Status responses and the UI expose the failed version, failure count, and next retry time.
