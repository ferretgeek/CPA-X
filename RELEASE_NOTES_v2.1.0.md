# CPA-X v2.1.0 Release Notes / 更新说明

> This file keeps GitHub Release notes and README references in sync.  
> 这个文件用于让 GitHub Release 和 README 保持同步。

## 中文（v2.1.0）

### 一句话说明

这次更新主要做了三件事：
**让自动更新更好懂、让高风险操作默认更安全、让文档和预览图全部同步到最新状态。**

### 你能直接感受到的变化

- 自动更新卡片现在会直接告诉你：
  - 有没有新版本
  - 现在是不是空闲
  - 还要等多久才会进入空闲
  - 下次自动检查还要等多久
  - 为什么现在还没有自动更新
- 前端已经移除导出入口，避免把敏感内容通过浏览器下载链接带出去。
- 主配置写回默认关闭，面板现在更偏“查看 + 自动更新”，不再默认改线上主配置。

### 修复了什么问题

- 修复空闲判断时间不准的问题。
  以前有时会把“已经空闲”误判成“还在忙”，现在改好了。
- 自动更新状态不再只给一个模糊结果，前端会显示更具体的原因和倒计时。
- 文档、README、Release 说明、预览图都已经和当前界面同步。

### 如果你是普通用户，需要知道什么

- 想看状态、日志、统计、模型：照常使用。
- 想用自动更新：照常使用，状态说明会比以前更清楚。
- 想修改主配置：现在默认不允许。
  只有你明确接受风险，才需要在 `.env` 里手动设置：
  `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true`

### 这版还同步了什么

- README 英文版、中文版都换成了最新界面预览图。
- 旧的历史说明文档已经清理，仓库里的说明文件更清楚，不容易看混。

## English (v2.1.0)

### Short version

This release focuses on three things:
**clearer auto-update status, safer defaults for risky actions, and fully synced docs / screenshots.**

### What users will notice

- The auto-update card now clearly shows:
  - whether a new version is available
  - whether the system is idle right now
  - how long until the idle condition is met
  - how long until the next auto-check
  - why auto-update has not started yet
- Frontend export entries are removed to reduce the risk of exposing sensitive data through browser download links.
- Main-config writeback is now disabled by default. The panel is safer out of the box and no longer edits the live main config unless you explicitly allow it.

### What was fixed

- Fixed incorrect idle-time calculation.
  In some cases, the panel could think the system was still busy even when it was already idle.
- Auto-update status is now easier to understand, with clearer reasons and countdowns.
- Docs, README, release notes, and preview screenshots are now aligned with the current UI.

### What normal users need to know

- If you only need status, logs, stats, or models: nothing gets harder.
- If you use auto-update: it should now be much easier to understand what it is waiting for.
- If you want to edit the main config: it is blocked by default.
  Only enable it if you fully accept the risk by setting:
  `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true`

### Also updated in this release

- English and Chinese README files now use the latest built-in UI screenshots.
- Old historical docs were removed so the repo is easier to understand.
