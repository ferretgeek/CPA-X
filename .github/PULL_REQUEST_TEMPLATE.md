## 改动说明 / Summary

<!-- 说明问题、解决方案和改动范围。 -->

## 验证 / Verification

<!-- 列出执行过的测试、命令和手动验证。 -->

## 兼容性 / Compatibility

<!-- 说明对配置、CLIProxyAPI 版本、操作系统、Docker 或 systemd 的影响。 -->

## Checklist

- [ ] 改动聚焦于一个明确问题，没有混入无关格式化。
- [ ] 已添加或更新相关回归测试。
- [ ] `python -m pytest` 通过。
- [ ] `ruff check app.py scripts tests` 通过。
- [ ] 用户可见改动已同步更新中英文文档。
- [ ] 未提交 `.env`、密钥、Token、运行数据、日志或缓存。
- [ ] 前端改动已检查桌面与移动端可读性。
- [ ] 未恢复敏感数据导出入口或默认主配置写回。
