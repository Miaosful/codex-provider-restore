# Codex Provider Restore

Small recovery tool for Codex Desktop conversations that disappear or fail to
open after switching model providers, relay services, or custom API endpoints.

## What It Fixes

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

## Safety Model

By default, the tool runs in dry-run mode and changes nothing.

When run with `--apply`, it:

1. Reads the current `model_provider` from `~/.codex/config.toml`.
2. Creates a SQLite backup before making database changes.
3. Copies rollout files into a separate restore directory.
4. Rewrites only the copied rollout metadata to the target provider.
5. Updates `state_5.sqlite` so Codex reads the corrected copies.

The original `~/.codex/sessions` and `~/.codex/archived_sessions` files are not
modified.

## Requirements

- macOS or Linux
- Python 3.11 or newer recommended
- Codex Desktop local state at `~/.codex`

The script only uses Python standard library modules.

## Usage

Run a dry run first:

```bash
python3 codex_provider_restore.py
```

If the output looks right, apply the restore:

```bash
python3 codex_provider_restore.py --apply
```

Restart or refresh Codex Desktop after applying.

## Common Options

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

## Output

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

## Testing

Run the unit tests:

```bash
python3 -m unittest tests/test_codex_provider_restore.py
```

Run a syntax check:

```bash
python3 -m py_compile codex_provider_restore.py tests/test_codex_provider_restore.py
```

## Recovery Notes

If you need to undo a restore, close Codex Desktop and restore the SQLite backup
printed by the tool. The original rollout files remain in place because the
tool only writes corrected copies.
