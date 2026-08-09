# Security Policy / 安全策略

## Supported versions / 支持版本

Security fixes target the latest release and the current `main` branch. Older releases should be upgraded before a report is reproduced.

安全修复面向最新版本与当前 `main`；复现旧版本问题前请先升级。

## Report privately / 私密报告

Use GitHub **Security → Report a vulnerability**. Do not open a public issue containing keys, logs, configuration files, server addresses, account data, or exploit details.

请使用 GitHub **Security → Report a vulnerability** 私密报告。不要在公开 Issue 中提交密钥、日志、配置、服务器地址、账号数据或利用细节。

Include the affected version, deployment mode, minimal reproduction, and expected impact. Replace all identities, addresses, paths, and secrets with reserved examples.

请提供受影响版本、部署方式、最小复现与预期影响，并把身份、地址、路径和秘密全部替换为保留示例值。

## Secure deployment baseline / 安全部署基线

- Direct local runs bind to `127.0.0.1` by default.
- Any non-loopback bind requires a panel access key of at least 32 characters.
- Docker publishes to host loopback by default and requires an access key.
- Remote access must use an HTTPS reverse proxy; do not expose the application port directly.
- Keep config writeback disabled unless the operator explicitly accepts the risk.

- 本地直接运行默认监听 `127.0.0.1`。
- 非回环监听必须配置至少 32 字符的面板访问密钥。
- Docker 默认只发布到宿主机回环地址，并强制访问密钥。
- 远程访问必须经过 HTTPS 反向代理，不要直接暴露应用端口。
- 除非部署者明确接受风险，否则保持主配置写回关闭。
