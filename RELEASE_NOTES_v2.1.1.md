# CPA-X v2.1.1 Release Notes / 更新说明

> This file keeps GitHub Release notes and README references in sync.
> 这个文件用于让 GitHub Release 和 README 保持同步。

## 中文（v2.1.1）

### 一句话说明

这是一个**补丁版更新**。主要目的有两个：  
一是把“当前主分支最新代码”和“GitHub 最新发行版”重新对齐，避免你看到“代码已经变了，但发行版还是几天前”的混淆；  
二是把这段时间已经完成的界面、文档和安全收口，统一体现在一个新的正式发行版里。

### 你能直接感受到的变化

- 自动更新卡片现在会直接告诉你：
  - 有没有新版本
  - 现在是不是空闲
  - 还要等多久才会进入空闲
  - 下次自动检查还要等多久
  - 为什么现在还没有自动更新
- 前端已经移除导出入口，避免把敏感内容通过浏览器下载链接带出去。
- 主配置写回默认关闭，面板现在更偏“查看 + 自动更新”，不再默认改线上主配置。

### 这次补发版主要解决什么

- 之前 `v2.1.0` 的 Release 发布时间还是旧的，所以很容易让人误以为“发行版没更新”。
- 现在单独发出 `v2.1.1`，这样 GitHub 上看到的最新 Release、发布时间、说明文字、主分支代码就一致了。
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
- 这次 Release 同时附带中文和英文说明，而且改成了更容易读懂的写法。

## English (v2.1.1)

### Short version

This is a **patch release**. It has two goals:  
first, to realign the latest code on `main` with the latest GitHub Release, so users do not get confused by “the code has changed, but the release still looks old”;  
second, to ship the recent UI, docs, and safety updates as one clear official release.

### What users will notice

- The auto-update card now clearly shows:
  - whether a new version is available
  - whether the system is idle right now
  - how long until the idle condition is met
  - how long until the next auto-check
  - why auto-update has not started yet
- Frontend export entries are removed to reduce the risk of exposing sensitive data through browser download links.
- Main-config writeback is now disabled by default. The panel is safer out of the box and no longer edits the live main config unless you explicitly allow it.

### What this patch release fixes

- The old `v2.1.0` release could look outdated because the release page still showed an older publish time.
- `v2.1.1` is published so the latest Release page, publish time, release notes, and current `main` branch all line up again.
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
- The release notes are provided in both Chinese and English, using simpler wording.
