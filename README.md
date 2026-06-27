# AstrBot /compact Plugin

> Manual LLM-summary context compaction for AstrBot.
> 在 AstrBot 会话中手动触发 LLM 摘要式上下文压缩，释放 token 配额。

[![Test Coverage](https://img.shields.io/badge/tests-58%2F58-brightgreen)](./tests)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](.)
[![Ruff](https://img.shields.io/badge/ruff-passing-green)](.)

---

## 功能

`/compact` slash 命令复用 AstrBot 内置的 `LLMSummaryCompressor`, 把当前会话
的早期消息压缩为一段摘要, 同时保留最近的对话原文。这样:

- 上下文窗口压力得到缓解, 远未触达 token 上限
- 用户可以主动选择何时压缩, 而不必等待 `compression_threshold=0.82` 的自动触发
- 压缩失败的极端情况下, 原始 history 不丢失

**设计原则:** 全部用自然语言 / 短词, 完全不使用 `--flag` 风格。

---

## 子命令总览

| 子命令 | 用途 | 示例 |
|--------|------|------|
| `/compact run [选项] [聚焦]` | 真正执行压缩(核心功能) | `/compact run 鉴权重构` |
| `/compact preview [选项] [聚焦]` | 预览效果, 不调用 LLM | `/compact preview keep 0.3` |
| `/compact status` | 查看当前会话历史条数 | `/compact status` |
| `/compact config` | 列出当前生效的配置 | `/compact config` |
| `/compact set key value [...]` | 持久化修改配置项 | `/compact set keep 0.20` |
| `/compact help` | 显示帮助 | `/compact help` |

`run` 与 `preview` 支持的位置参数(无横线、无 flag):

| 短词 | 含义 | 合法值 |
|------|------|--------|
| `keep` | 临时覆盖保留比例 | `0` ~ `0.3` |
| `provider` | 临时覆盖 LLM provider | provider id 字符串 |
| `min` | 临时覆盖最小消息阈值 | 正整数 |
| `summary` | 临时覆盖是否显示摘要 | `on` / `off` |
| `chars` | 临时覆盖摘要预览最大字符 | 正整数 |
| `focus` | 设置持久化 default_focus | 任意文本 |
| 其它自由文本 | 作为本次聚焦话题 | 任意文本 |

---

## 用法速查

### 90% 用户的日常使用

```
/compact
/compact 鉴权重构
/compact 这次先聊聊性能优化
```

### 临时调整一次

```
/compact run keep 0.20 鉴权
/compact run provider deepseek-r1 鉴权
/compact run keep 0.05 provider deepseek-r1 数据库迁移
/compact preview keep 0.3
```

### 持久化调整(下次起自动生效)

```
/compact set keep 0.20
/compact set provider deepseek-r1
/compact set min 6
/compact set summary off
/compact set chars 500
/compact set focus 鉴权重构
/compact set keep 0.20 provider deepseek-r1   # 一次多个
```

`/compact set` 立即在当前进程生效, 同时写到 AstrBot 插件配置文件, 重启不丢。

---

## 安装

将本目录复制到 AstrBot 的插件目录:

```bash
cp -r astrbot_plugin_compact <AstrBot>/data/plugins/astrbot_plugin_compact
```

重启 AstrBot, WebUI → 插件 → compact → 启用。

---

## 配置

打开 WebUI → 插件 → compact → 配置面板:

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `compress_provider_id` | string | `""` | 用于压缩的 provider ID, 留空则走回退链 |
| `keep_recent_ratio` | float (0–0.3) | `0.15` | 保留最近多少比例的 token 作为原文 |
| `instruction_text` | string | `""` | 自定义摘要指令, 留空使用 AstrBot 默认指令 |
| `min_messages` | int | `4` | 低于此消息数拒绝压缩 |
| `show_summary` | bool | `true` | 是否在回复中显示摘要预览 |
| `summary_max_chars` | int | `800` | 摘要预览的最大字符数 (超出截断) |
| `default_focus` | string | `""` | 每次压缩默认附加的 focus 话题 |

`/compact set` 与 WebUI 双向同步, 改一边另一边都会更新。

---

## Provider 解析优先级

当用户触发 `/compact` 时, 按以下顺序逐级查找 LLM provider:

1. **`provider <id>`(本次 run 的临时参数)** — 最高优先级
2. **插件配置 `compress_provider_id`** — 在插件面板里设置的默认值
3. **核心配置 `provider_settings.llm_compress_provider_id`** — AstrBot 全局设置
4. **当前会话绑定的 chat provider** — fallback, 不需额外配置

任一级找到即返回。全部失败时插件会返回提示消息而非抛异常。

---

## 使用示例

### 基本

```
你: /compact
bot: ✅ 压缩完成: 18 → 4 条消息。
     📝 摘要预览:
     主要讨论了鉴权重构，结论: 使用 JWT + RBAC 组合...
```

### 指定保留比例

```
你: /compact run keep 0.05
bot: ✅ 压缩完成: 28 → 3 条消息。
     ...
```

### 重点话题

```
你: /compact 数据库迁移
bot: ✅ 压缩完成: 14 → 5 条消息。
     📝 摘要预览:
     (摘要会更突出数据库迁移相关内容)
```

### 组合 + 临时 provider

```
你: /compact run 重构鉴权 keep 0.20 provider deepseek-v3
bot: ✅ 压缩完成: 24 → 6 条消息。 (使用 deepseek-v3 生成)
```

### 持久化配置

```
你: /compact set keep 0.10
bot: ✅ 配置已持久化保存:
     - keep_recent_ratio = 0.1

你: /compact
bot: ✅ 压缩完成: 30 → 4 条消息。  # 用新的 keep=0.10
```

---

## 工作原理

1. 解析位置 key-value 与 focus → `parse_compact_overrides`
2. 把 `ParseResult` 拼装为 `CompactArgs` → `_build_compact_args`
3. 应用所有临时覆盖(min_messages / show_summary / summary_max_chars / default_focus)
4. 四级查找 provider → `resolve_provider`
5. 从 AstrBot 的 `ConversationManager` 取出当前会话 history
6. `bind_checkpoint_messages` 把持久化的 `list[dict]` 反序列化为 `list[Message]`
7. 把消息打包成 `Compactor(provider, keep_recent_ratio, instruction_text)`
8. `Compactor.__call__` 内部使用 AstrBot 的 `LLMSummaryCompressor` 生成摘要
9. `dump_messages_with_checkpoints` 把压缩后的消息序列化为 `list[dict]`
10. `update_conversation(umo, cid, history)` 写回数据库
11. `/compact set` 走 `astrbot.core.star.config.update_config` 写盘

### 错误处理

| 场景 | 行为 |
|------|------|
| 找不到任何 provider | 提示消息, history 不变 |
| 当前没有会话 | 提示消息, history 不变 |
| 消息数 < `min_messages` | 提示消息, history 不变 |
| LLM 调用抛异常 | inner compressor 吞掉异常返回原 messages, history 写回 (内容相同) |
| 用户传入越界 `keep 99` | 静默 clamp 到 0.3 |
| 用户传入非数字 `keep abc` | 跳过该项, 其余继续 |
| `/compact set` 写盘失败 | 本进程内仍生效, 提示用户重启后会丢失 |

---

## 开发

### 运行测试

```bash
pytest                   # 全部测试 (58 个)
pytest tests/test_args.py # 单个模块
pytest -v                # 详细输出
pytest -k "set"          # 只跑 /compact set 相关
```

### Lint & Format

```bash
ruff check .   # 静态检查
ruff format .  # 自动格式化
```

### 项目结构

```
astrbot_plugin_compact/
├── main.py              # 入口 + CompactPlugin + handler + 解析器
├── compressor.py        # Compactor 包装层 (re-export LLMSummaryCompressor)
├── _conf_schema.json    # WebUI 配置 schema
├── metadata.yaml        # AstrBot 插件元数据
├── README.md            # 本文件
├── MANUAL_QA.md         # 手工验收清单
├── pytest.ini
└── tests/               # 58 个单元 + 集成测试
    ├── test_args.py
    ├── test_compressor.py
    ├── test_handler.py
    ├── test_initialize.py
    ├── test_integration.py
    └── test_resolve_provider.py
```

---

## 兼容性

- AstrBot v4.x
- Python 3.12+
- 依赖项 (AstrBot 内置): `astrbot.core.agent.context.compressor.LLMSummaryCompressor`

无需额外第三方依赖。

---

## License

MIT

---

## 贡献

欢迎通过 Issue / PR 改进本插件。请先在 Issue 中描述需求, 避免大型 PR 难以 review。
