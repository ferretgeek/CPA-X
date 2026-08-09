# Contributing to CPA-X / 参与贡献

感谢你帮助改进 CPA-X。欢迎提交缺陷报告、文档修正、兼容性改进和代码贡献。

Thank you for improving CPA-X. Bug reports, documentation fixes, compatibility work, and code contributions are welcome.

## Before opening an issue / 提交 Issue 前

- Search existing issues and confirm the problem still exists on the latest release.
- For usage questions, prefer [GitHub Discussions](https://github.com/ferretgeek/CPA-X/discussions).
- Never paste API keys, management keys, tokens, `.env` contents, or unredacted logs.
- Include the CPA-X version, operating system, Python version, deployment mode, CLIProxyAPI version, and configured log time-zone mode.

提交前请先搜索已有 Issue，并使用最新版本复现。日志必须移除密钥、Token、内网地址及其他敏感信息。

## Development setup / 开发环境

CPA-X requires Python 3.11 or newer.

```bash
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1

python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Copy `.env.example` to `.env` only for local testing. `.env` must never be committed.

## Project guardrails / 项目约束

- Keep the safe default bind host at `127.0.0.1`; non-loopback bindings must require an access key of at least 32 characters.
- Keep main-config writeback disabled by default.
- Do not restore frontend export/download entries for sensitive operational data.
- Docker mode is intended for monitoring; host service control and systemd auto-update belong to host deployments.
- Preserve UTC/RFC 3339 API timestamps and browser-side localization.
- Preserve compatibility with both CLIProxyAPI v6 cumulative usage and the v7 usage queue.
- Prefer bounded reads, atomic writes, explicit input limits, and commands without `shell=True`.

See [AGENTS.md](AGENTS.md) for the complete repository contract.

## Verification / 验证

Run all checks before opening a pull request:

```bash
python -m pytest
ruff check app.py scripts tests
python -m py_compile app.py scripts/auto_install.py scripts/doctor.py
```

For frontend changes, also verify desktop, tablet, and mobile layouts in both themes. Keep text readable and avoid fixed-height clipping.

## Pull requests / 拉取请求

1. Keep each pull request focused on one coherent change.
2. Explain the problem, the chosen solution, compatibility impact, and verification performed.
3. Add or update regression tests for behavior changes.
4. Update both `README.md` and `README_CN.md` when user-facing behavior changes.
5. Do not include generated caches, runtime data, credentials, or unrelated formatting changes.

By contributing, you agree that your contribution is licensed under the repository's [MIT License](LICENSE).
