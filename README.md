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

这个工具会把数据库索引和 rollout 元数据统一到当前 provider，并保持
`rollout_path` 指向原始会话文件。旧版本曾把数据库指向恢复目录中的 rollout
副本，这会让 Codex Desktop 的左侧会话列表继续丢失；当前版本会识别并修回这种
错误路径。

### 安全策略

默认运行是 dry-run，不会修改任何文件或数据库。

使用 `--apply` 后，工具会：

1. 从 `~/.codex/config.toml` 读取当前 `model_provider`。
2. 修改数据库前创建 SQLite 备份。
3. 修改 rollout 前把原始 rollout 备份到单独的恢复目录。
4. 原地更新 rollout 元数据里的 provider。
5. 更新 `state_5.sqlite` 的 provider，并把旧版本造成的恢复目录路径修回原始
   `~/.codex/sessions` 或 `~/.codex/archived_sessions` 路径。

数据库中的 `rollout_path` 不会被改到恢复目录；恢复目录只保存备份。

### 环境要求

- 仅适用于 macOS
- 推荐 Python 3.11 或更新版本
- Codex Desktop 本地状态目录位于 `~/.codex`

脚本只使用 Python 标准库。

### 使用方法

完整流程：

1. 拉取工具仓库：

```bash
git clone https://github.com/Miaosful/codex-provider-restore.git
cd codex-provider-restore
```

如果已经克隆过，进入目录后更新到最新版本：

```bash
cd codex-provider-restore
git pull
```

2. 确认 Python 可用：

```bash
python3 --version
```

3. 可选：运行测试，确认脚本在当前环境可执行：

```bash
python3 -m unittest tests/test_codex_provider_restore.py
```

4. 先 dry-run 查看影响范围。dry-run 不会修改任何文件或数据库：

```bash
python3 codex_provider_restore.py
```

5. 确认输出符合预期后，建议先退出 Codex Desktop，再执行修复：

```bash
python3 codex_provider_restore.py --apply
```

6. 重新打开 Codex Desktop，或在应用内刷新窗口。左侧历史会话应恢复，旧会话点击时不应再因为旧 provider 报错。

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
Rollouts to rewrite: 172
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
`state_5.sqlite`，并按需从该次运行目录里的 `rollout-backups` 还原 rollout 文件。

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

This tool realigns both layers to the current provider while keeping
`rollout_path` pointed at the original session files. Older versions pointed
the database at rollout copies under the restore directory, which can keep the
Codex Desktop conversation list hidden; the current version detects and repairs
that path shape.

### Safety Model

By default, the tool runs in dry-run mode and changes nothing.

When run with `--apply`, it:

1. Reads the current `model_provider` from `~/.codex/config.toml`.
2. Creates a SQLite backup before making database changes.
3. Backs up rollout files before changing them.
4. Rewrites rollout provider metadata in place.
5. Updates `state_5.sqlite` provider values and repairs restore-directory
   `rollout_path` values back to the original session paths.

The restore directory is used only for backups; database `rollout_path` values
stay on the original session or archived-session files.

### Requirements

- macOS only
- Python 3.11 or newer recommended
- Codex Desktop local state at `~/.codex`

The script only uses Python standard library modules.

### Usage

Full flow:

1. Clone the tool repository:

```bash
git clone https://github.com/Miaosful/codex-provider-restore.git
cd codex-provider-restore
```

If you already cloned it, update it first:

```bash
cd codex-provider-restore
git pull
```

2. Check that Python is available:

```bash
python3 --version
```

3. Optional: run the tests to confirm the script works in your environment:

```bash
python3 -m unittest tests/test_codex_provider_restore.py
```

4. Run a dry run first. Dry-run mode does not change files or database rows:

```bash
python3 codex_provider_restore.py
```

5. If the output looks right, quit Codex Desktop and apply the restore:

```bash
python3 codex_provider_restore.py --apply
```

6. Reopen Codex Desktop or refresh the app window. The left-side conversation list should be restored, and older threads should no longer fail because of missing legacy providers.

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
Rollouts to rewrite: 172
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
printed by the tool. Restore rollout files from that run's `rollout-backups`
directory if you need to undo rollout metadata changes too.
