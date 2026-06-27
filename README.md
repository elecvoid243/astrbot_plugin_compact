# AstrBot /compact Plugin

> Manual LLM-summary context compaction for AstrBot.
> 在 AstrBot 会话中手动触发 LLM 摘要式上下文压缩，释放 token 配额。

[![Test Coverage](https://img.shields.io/badge/tests-33%2F33-brightgreen)](./tests)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](.)
[![Ruff](https://img.shields.io/badge/ruff-passing-green)](.)

---

## 功能

`/compact` slash 命令复用 AstrBot 内置的 `LLMSummaryCompressor`, 把当前会话
的早期消息压缩为一段摘要, 同时保留最近的对话原文。这样:

- 上下文窗口压力得到缓解, 远未触达 token 上限
- 用户可以主动选择何时压缩, 而不必等待 `compression_threshold=0.82` 的自动触发
- 压缩失败的极端情况下, 原始 history 不丢失

支持参数:

| 形式 | 含义 |
|------|------|
| `/compact` | 用插件默认配置压缩当前会话 |
| `/compact <focus 话题>` | 摘要时重点关注该话题 |
| `/compact --keep 0.20` | 临时覆盖保留比例 (0–0.3, 默认 0.15) |
| `/compact --provider <id>` | 临时指定 LLM provider (优先级最高) |

`--keep` 与 `<focus>` 可组合, 如 `/compact 重构鉴权 --keep 0.1 --provider deepseek-v3`。

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

修改后需重启 AstrBot (或点击 "重载插件") 让配置生效。

---

## Provider 解析优先级

当用户触发 `/compact` 时, 按以下顺序逐级查找 LLM provider:

1. **命令行 `--provider <id>`** — 最高优先级, 临时切换
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
你: /compact --keep 0.05
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
你: /compact 重构鉴权 --keep 0.20 --provider deepseek-v3
bot: ✅ 压缩完成: 24 → 6 条消息。 (使用 deepseek-v3 生成)
```

---

## 工作原理

1. 解析命令参数 → `parse_compact_args`
2. 四级查找 provider → `resolve_provider`
3. 从 AstrBot 的 `ConversationManager` 取出当前会话 history
4. `bind_checkpoint_messages` 把持久化的 `list[dict]` 反序列化为 `list[Message]`
5. 把消息打包成 `Compactor(provider, keep_recent_ratio, instruction_text)`
6. `Compactor.__call__` 内部使用 AstrBot 的 `LLMSummaryCompressor` 生成摘要
7. `dump_messages_with_checkpoints` 把压缩后的消息序列化为 `list[dict]`
8. `update_conversation(umo, cid, history)` 写回数据库

### 错误处理

| 场景 | 行为 |
|------|------|
| 找不到任何 provider | 提示消息, history 不变 |
| 当前没有会话 | 提示消息, history 不变 |
| 消息数 < `min_messages` | 提示消息, history 不变 |
| LLM 调用抛异常 | inner compressor 吞掉异常返回原 messages, history 写回 (内容相同) |
| 用户传入越界 `--keep 99` | 静默 clamp 到 0.3 |
| 用户传入非数字 `--keep abc` | `keep_recent_ratio` 走默认值 |

---

## 开发

### 安装依赖

```bash
pip install -e .
```

或仅安装测试依赖:

```bash
pip install pytest pytest-asyncio ruff
```

### 运行测试

```bash
pytest                   # 全部测试 (33 个)
pytest tests/test_args.py # 单个模块
pytest -v                # 详细输出
```

### Lint & Format

```bash
ruff check .   # 静态检查
ruff format .  # 自动格式化
```

### 项目结构

```
astrbot_plugin_compact/
├── main.py              # 入口 + CompactPlugin + handler
├── compressor.py        # Compactor 包装层 (re-export LLMSummaryCompressor)
├── _conf_schema.json    # WebUI 配置 schema
├── metadata.yaml        # AstrBot 插件元数据
├── README.md            # 本文件
├── MANUAL_QA.md         # 手工验收清单
├── .gitignore
├── pyproject.toml
├── pytest.ini
└── tests/               # 33 个单元 + 集成测试
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
