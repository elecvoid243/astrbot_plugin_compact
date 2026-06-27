# /compact 插件手工验收清单

> 适用版本: v0.1.0
> 验收人: _______
> 验收日期: _______
> AstrBot 版本: _______

本清单覆盖 `/compact` 插件的核心验收场景。由于本仓库不包含真实 AstrBot
运行时, 请在本地 AstrBot 环境中执行下列步骤并勾选确认。

## 前置条件

- [ ] 已安装 AstrBot v4.x 并能正常启动
- [ ] 已配置至少一个 LLM provider
- [ ] 已将本插件放入 AstrBot 的 `data/plugins/` 目录
- [ ] AstrBot 控制台日志可见 `[Core] INFO [plugin] loaded compact` 之类启动日志

## 1. 基本流程

| # | 操作 | 期望结果 | ✓ |
|---|------|---------|---|
| 1.1 | 在一个有 5 轮以上对话的会话中发送 `/compact` | 收到 "压缩完成" 回复, 显示 `N → M` 条消息数对比 | ☐ |
| 1.2 | 压缩后立即发送 "刚才我们聊了什么?" | LLM 能基于摘要正确回答, 不丢关键上下文 | ☐ |
| 1.3 | 在压缩前的会话中 `/compact --keep 0.20` | 仍然成功压缩, 保留比例比默认值更小 | ☐ |

## 2. focus 话题

| # | 操作 | 期望结果 | ✓ |
|---|------|---------|---|
| 2.1 | `/compact 重构鉴权` | 摘要中突出 "重构鉴权" 相关内容 | ☐ |
| 2.2 | `/compact 数据库迁移 --keep 0.1` | 同时应用 focus + keep | ☐ |
| 2.3 | 配合插件配置 `instruction_text` 后 `/compact foo` | LLM 收到 "Base + 重点关注:foo" 的组合指令 | ☐ |

## 3. Provider 回退

| # | 操作 | 期望结果 | ✓ |
|---|------|---------|---|
| 3.1 | 当前会话无 provider, 插件配置中也未填 `compress_provider_id`, 发送 `/compact` | 收到 "未找到可用的 LLM provider" 提示, 不抛异常 | ☐ |
| 3.2 | 插件配置中填 `compress_provider_id=deepseek-v3`, 发送 `/compact` | 使用 deepseek-v3 完成压缩 | ☐ |
| 3.3 | 在 3.2 基础上发送 `/compact --provider gpt-4o-mini` | 覆盖为 gpt-4o-mini (若已配置) | ☐ |

## 4. 边界条件

| # | 操作 | 期望结果 | ✓ |
|---|------|---------|---|
| 4.1 | 消息数 < `min_messages` (默认 4) 时 `/compact` | 收到 "消息数量过少" 提示, 不调用 LLM, 不修改 history | ☐ |
| 4.2 | 当前不在任何会话中 `/compact` | 收到 "当前没有进行中的会话" 提示 | ☐ |
| 4.3 | LLM 调用失败 (临时断网) 时 `/compact` | 收到 "压缩失败" 提示, 原 history 不变 | ☐ |
| 4.4 | `--keep 99.99` (越界) | 静默 clamp 到 0.3, 命令继续执行 | ☐ |
| 4.5 | `--keep abc` (非数字) | `keep_recent_ratio` 走默认值 (插件配置或 0.15) | ☐ |

## 5. WebUI 配置

| # | 操作 | 期望结果 | ✓ |
|---|------|---------|---|
| 5.1 | 打开 AstrBot WebUI → 插件 → compact → 配置 | 看到 6 个配置项 (compress_provider_id / keep_recent_ratio / instruction_text / min_messages / show_summary / summary_max_chars) | ☐ |
| 5.2 | 修改 `show_summary=false` 后 `/compact` | 只看到 "压缩完成: N → M 条消息" 一行, 无摘要预览 | ☐ |
| 5.3 | 修改 `min_messages=10` 后在 8 条消息的会话中 `/compact` | 收到 "消息数量过少" 提示 | ☐ |

## 6. 持久化验证

| # | 操作 | 期望结果 | ✓ |
|---|------|---------|---|
| 6.1 | `/compact` 后重启 AstrBot | 摘要消息仍在会话中, LLM 能继续基于其工作 | ☐ |
| 6.2 | 检查 AstrBot 数据库 `conversation.history` 字段 | 看到 `role: user, content: "Our previous history conversation summary: ..."` 字段 | ☐ |

## 7. 异常处理

| # | 操作 | 期望结果 | ✓ |
|---|------|---------|---|
| 7.1 | 在 WebUI 中把 `keep_recent_ratio` 写成字符串 `"abc"` 后保存 | 插件仍能加载, `keep_recent_ratio` 走默认值 (initialize 失败回退) | ☐ |
| 7.2 | 同时在插件配置中设 `compress_provider_id=xxx` 和 `/compact --provider yyy` | 命令行的 yyy 优先生效 | ☐ |

## 通过标准

所有 18 项必须勾选通过才能签收。
若有失败项, 请附上:
- AstrBot 版本与日志文件 (`data/logs/astrbot.log`)
- 复现步骤
- 实际收到 / 期望回复
- 是否启用 DEBUG 日志
