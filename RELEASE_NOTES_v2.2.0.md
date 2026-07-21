# CPA-X v2.2.0 Release Notes / 更新说明

发布日期 / Release date: 2026-07-21

## 中文

这是一次完整的稳定性、时区、磁盘、性能与界面修复版本。

### 时区与自动更新

- CLIProxyAPI 的无偏移本地日志时间不再被错误追加 `Z`；API 统一输出 UTC RFC 3339，浏览器再转换为本地时间。
- 新增 `auto`、本机、固定 UTC 偏移和 IANA 时区支持；`auto` 可处理面板容器与日志宿主机时区不同的场景。
- 日志缺失、时钟超前或时区无法可靠判断时，不再误判为空闲并打断流量。
- 更新版本改为语义比较，本地版本更高、`v` 前缀差异和预发布版本不会再误触发更新。
- 下载/校验或隔离构建在服务在线时完成；停机窗口只执行原子替换、启动和稳定性检查，失败自动回滚。
- release 默认强制校验 SHA-256，并拒绝不安全归档路径、链接、特殊文件、超大下载和超大解压内容。

### 磁盘与统计

- 二进制回滚点默认只保留 2 个，并同时限制保留天数和总逻辑大小；排序使用备份名中的创建时间，不再受 `copy2` 保留 mtime 干扰。
- 清空统计不再复制或截断可能非常大的 CLIProxy 日志，彻底移除该路径造成的磁盘膨胀和日志丢失。
- 首次日志扫描默认限制为最近 64 MiB，之后按 inode/偏移增量解析，支持轮转、截断和未写完的尾行。
- 上游用量接口暂时断线时，磁盘快照只用于显示，不再被当成计数器归零；并发刷新、恢复连接和上游重启不再重复累计。
- 同时兼容 CLIProxyAPI v6 累计用量接口与 v7 短期用量队列；队列按 15 秒轮询并立即持久化，模式切换不会重复计数。
- 上游离线期间清空统计会等待首个实时快照作为新基线，避免旧数据“复活”。

### 默认语录与后端质量

- 仓库根目录 `X.txt` 现在始终强制加载；兼容缺少 `出自：` 和重复 `出自：` 的历史行，完整加载当前 181 条内容。
- 配置、统计、历史记录与 `.env` 改用同目录临时文件 + `fsync` + 原子替换；密钥文件默认使用严格权限。
- 移除 shell 命令拼接，校验 systemd unit、URL、价格、请求头、配置大小和 API 测试目标。
- 默认同源 API；跨域必须显式配置来源。新增安全响应头和无需密钥、只暴露最小信息的 `/api/healthz`。
- 生产启动优先使用 Waitress；后台标签页暂停前端轮询，上游故障增加短暂退避，降低无效请求与阻塞。
- 修复安装器首次复制示例配置后无法写入自动探测路径的问题，并可发现已停止的 systemd unit。

### 前端

- 移除 Google Fonts 网络依赖，采用跨平台系统字体与等宽数字字体栈。
- 重做深色/浅色配色、表面层次、字体对比度、间距和焦点状态。
- 移除卡片固定高度与整页 `overflow: hidden`，新增桌面、平板和手机布局，长语录、设置和日志不再裁切。
- 字号下限提高到 12px；语录默认 17px，取消长时间打字机动画，优先保证阅读。
- 补齐按钮类型、输入框名称、键盘开关状态与减少动画偏好；修复验证结果和模型字段的 HTML 注入风险。
- 日志区“清空”现在只清除当前浏览器显示，不再修改服务日志文件。

### 验证

- 新增 pytest 回归测试，覆盖语录、时区、URL、语义版本、日志增量/轮转/半行、空闲安全、断线统计、备份淘汰、访问控制、安装器和响应式 DOM。
- Python 编译、Ruff、Bandit（无高危项）、JavaScript 语法、DOM/可访问性和 Docker Compose 配置均纳入发布检查。

## English

This is a comprehensive stability, time-zone, disk-usage, performance, and UI repair release.

### Time zones and updates

- Offset-less CLIProxyAPI local timestamps are no longer mislabeled with `Z`; APIs emit UTC RFC 3339 and browsers localize it.
- Added automatic inference, system-local, fixed-offset, and IANA time-zone modes, including host/container time-zone mismatches.
- Missing logs or clock skew can no longer be mistaken for idle time and interrupt live traffic.
- Update checks now use semantic version ordering.
- Downloads/checks or isolated source builds happen while the service remains online; only atomic replacement and startup validation require downtime, with rollback on failure.
- SHA-256 verification is required by default, and unsafe or oversized release archives are rejected.

### Disk and statistics

- Binary rollback points default to two and are capped by count, age, and total logical size.
- Clearing statistics no longer copies or truncates large service logs.
- Initial log parsing is bounded to the latest 64 MiB, then continues incrementally across partial lines, truncation, and rotation.
- Cached outage snapshots no longer reset or double-count live usage, and offline clears establish a fresh baseline on recovery.
- Supports both the CLIProxyAPI v6 cumulative endpoint and v7 short-lived usage queue, with 15-second durable polling and transition baselines.

### Quotes, backend, and deployment

- Repository-root `X.txt` is always loaded; all 181 current lines are preserved, including legacy malformed source markers.
- Persistent files and `.env` use atomic writes; shell command composition was removed and sensitive inputs are validated.
- API access is same-origin by default, security headers were added, and `/api/healthz` provides a minimal unauthenticated liveness probe.
- Waitress is the preferred production server. The installer now applies detected paths on first install and detects inactive systemd units.

### Frontend and verification

- Removed Google Fonts, redesigned both themes for higher contrast, removed clipping/fixed card heights, and added complete desktop/tablet/mobile layouts.
- Improved minimum font sizes, focus states, input labels, keyboard switch semantics, reduced-motion support, and dynamic-content escaping.
- Added regression coverage for the critical time-zone, update-safety, disk, usage, installer, security, quote, log, and responsive-DOM paths.
