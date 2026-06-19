# Codex Provider Restore

- 切换模型中转站、provider 或自定义 API endpoint 后，用于找回 Codex Desktop
历史对话的小工具。

- English documentation is available below.

## 中文说明

### 用途

Codex Desktop 会把 provider 元数据保存在两处：

- `~/.codex/state_5.sqlite`：用于左侧会话列表。
- `~/.codex/sessions` 或 `~/.codex/archived_sessions` 下的 rollout JSONL 文件：
  用于打开某个具体会话。

当 `config.toml` 里的 provider ID 发生变化，例如从 `custom` 切换到
`anyrouter`，旧会话可能会在左侧消失，或者点击时出现类似错误：

```text
failed to load configuration: Model provider `custom` not found
```

这个工具会把数据库索引和 rollout 元数据统一到当前 provider，同时不修改原始
rollout 文件。

### 安全策略

默认运行是 dry-run，不会修改任何文件或数据库。

使用 `--apply` 后，工具会：

1. 从 `~/.codex/config.toml` 读取当前 `model_provider`。
2. 修改数据库前创建 SQLite 备份。
3. 把 rollout 文件复制到单独的恢复目录。
4. 只改复制出来的 rollout 元数据。
5. 更新 `state_5.sqlite`，让 Codex 读取修正后的副本。

原始 `~/.codex/sessions` 和 `~/.codex/archived_sessions` 文件不会被修改。

### 环境要求

- macOS 或 Linux
- 推荐 Python 3.11 或更新版本
- Codex Desktop 本地状态目录位于 `~/.codex`

脚本只使用 Python 标准库。

### 使用方法

先 dry-run 查看影响范围：

```bash
python3 codex_provider_restore.py
```

确认输出符合预期后执行修复：

```bash
python3 codex_provider_restore.py --apply
```

执行后刷新或重启 Codex Desktop。

### 常用参数

手动指定目标 provider，而不是读取 `config.toml`：

```bash
python3 codex_provider_restore.py --provider anyrouter --apply
```

指定自定义路径：

```bash
python3 codex_provider_restore.py \
  --state ~/.codex/state_5.sqlite \
  --config ~/.codex/config.toml \
  --output-root ~/.codex/provider-restore-rollouts \
  --apply
```

### 输出示例

```text
Mode: DRY RUN
Target provider: anyrouter
Threads to update: 172
Rollout copies: 172
Missing rollout files: 0
Run directory: /Users/you/.codex/provider-restore-rollouts/20260619-144125
No files or database rows were changed. Re-run with --apply to restore.
```

在 `--apply` 模式下，输出中还会包含 SQLite 备份路径。

### 测试

运行单元测试：

```bash
python3 -m unittest tests/test_codex_provider_restore.py
```

运行语法检查：

```bash
python3 -m py_compile codex_provider_restore.py tests/test_codex_provider_restore.py
```

### 回滚

如果需要撤销恢复，请先关闭 Codex Desktop，然后使用工具输出的 SQLite 备份还原
`state_5.sqlite`。原始 rollout 文件没有被改动，因为工具只写入修正副本。

## English

### Purpose

Small recovery tool for Codex Desktop conversations that disappear or fail to
open after switching model providers, relay services, or custom API endpoints.

Codex Desktop stores provider metadata in two places:

- `~/.codex/state_5.sqlite`, which powers the conversation list.
- rollout JSONL files under `~/.codex/sessions` or `~/.codex/archived_sessions`,
  which are loaded when a conversation is opened.

When `config.toml` changes from one provider ID to another, older conversations
can appear missing or fail with errors like:

```text
failed to load configuration: Model provider `custom` not found
```

This tool realigns both layers to the current provider while keeping the
original rollout files untouched.

### Safety Model

By default, the tool runs in dry-run mode and changes nothing.

When run with `--apply`, it:

1. Reads the current `model_provider` from `~/.codex/config.toml`.
2. Creates a SQLite backup before making database changes.
3. Copies rollout files into a separate restore directory.
4. Rewrites only the copied rollout metadata to the target provider.
5. Updates `state_5.sqlite` so Codex reads the corrected copies.

The original `~/.codex/sessions` and `~/.codex/archived_sessions` files are not
modified.

### Requirements

- macOS or Linux
- Python 3.11 or newer recommended
- Codex Desktop local state at `~/.codex`

The script only uses Python standard library modules.

### Usage

Run a dry run first:

```bash
python3 codex_provider_restore.py
```

If the output looks right, apply the restore:

```bash
python3 codex_provider_restore.py --apply
```

Restart or refresh Codex Desktop after applying.

### Common Options

Use a specific provider instead of reading `config.toml`:

```bash
python3 codex_provider_restore.py --provider anyrouter --apply
```

Use custom paths:

```bash
python3 codex_provider_restore.py \
  --state ~/.codex/state_5.sqlite \
  --config ~/.codex/config.toml \
  --output-root ~/.codex/provider-restore-rollouts \
  --apply
```

### Output

The tool prints a summary like:

```text
Mode: DRY RUN
Target provider: anyrouter
Threads to update: 172
Rollout copies: 172
Missing rollout files: 0
Run directory: /Users/you/.codex/provider-restore-rollouts/20260619-144125
No files or database rows were changed. Re-run with --apply to restore.
```

In apply mode, it also prints the SQLite backup path.

### Testing

Run the unit tests:

```bash
python3 -m unittest tests/test_codex_provider_restore.py
```

Run a syntax check:

```bash
python3 -m py_compile codex_provider_restore.py tests/test_codex_provider_restore.py
```

### Recovery Notes

If you need to undo a restore, close Codex Desktop and restore the SQLite backup
printed by the tool. The original rollout files remain in place because the
tool only writes corrected copies.
