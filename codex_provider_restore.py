#!/usr/bin/env python3
"""
Restore Codex Desktop thread visibility after switching model providers.

Default mode is dry-run. Use --apply to write a SQLite backup, create corrected
rollout copies, and point the thread index at those copies.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback.
    tomllib = None


DEFAULT_CODEX_HOME = Path.home() / ".codex"


@dataclass(frozen=True)
class RestoreResult:
    target_provider: str
    updated_threads: int
    copied_rollouts: int
    missing_rollouts: int
    backup_path: Path | None
    run_dir: Path
    copied_paths: dict[str, str]


def read_model_provider(config_path: Path) -> str:
    config_text = config_path.read_text(encoding="utf-8")
    if tomllib is not None:
        parsed = tomllib.loads(config_text)
        provider = parsed.get("model_provider")
        if isinstance(provider, str) and provider:
            return provider

    match = re.search(r'(?m)^model_provider\s*=\s*"([^"]+)"\s*$', config_text)
    if not match:
        raise ValueError(f"Could not find top-level model_provider in {config_path}")
    return match.group(1)


def timestamp_now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def fetch_threads(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    columns = table_columns(conn, "threads")
    order_by = "updated_at_ms DESC" if "updated_at_ms" in columns else "id"
    return [
        (thread_id, rollout_path, model_provider)
        for thread_id, rollout_path, model_provider in conn.execute(
            f"SELECT id, rollout_path, model_provider FROM threads ORDER BY {order_by}"
        )
    ]


def make_run_dir(output_root: Path, timestamp: str) -> Path:
    return output_root.expanduser().resolve() / timestamp


def destination_for_rollout(run_dir: Path, source_path: Path) -> Path:
    source_path = source_path.expanduser()
    if source_path.is_absolute():
        relative_parts = source_path.parts[1:]
    else:
        relative_parts = source_path.parts
    return run_dir / "rollouts" / Path(*relative_parts)


def rewrite_rollout_text(text: str, target_provider: str) -> str:
    output_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        body = line[:-1] if newline else line
        try:
            record = json.loads(body)
        except json.JSONDecodeError:
            output_lines.append(line)
            continue

        payload = record.get("payload") if isinstance(record, dict) else None
        if isinstance(payload, dict) and "model_provider" in payload:
            payload["model_provider"] = target_provider
        output_lines.append(json.dumps(record, ensure_ascii=False) + newline)
    return "".join(output_lines)


def copy_rewritten_rollouts(
    rollout_paths: Iterable[str],
    run_dir: Path,
    target_provider: str,
    apply: bool,
) -> tuple[dict[str, str], int]:
    copied_paths: dict[str, str] = {}
    missing_count = 0

    for rollout_path in sorted(set(path for path in rollout_paths if path)):
        source_path = Path(rollout_path).expanduser()
        if not source_path.exists():
            missing_count += 1
            continue

        destination_path = destination_for_rollout(run_dir, source_path)
        copied_paths[str(source_path)] = str(destination_path)
        if not apply:
            continue

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        rewritten_text = rewrite_rollout_text(source_path.read_text(encoding="utf-8"), target_provider)
        destination_path.write_text(rewritten_text, encoding="utf-8")

    return copied_paths, missing_count


def backup_database(conn: sqlite3.Connection, run_dir: Path, state_path: Path) -> Path:
    backup_path = run_dir / "backups" / f"{state_path.name}.before-provider-restore.sqlite"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_conn = sqlite3.connect(backup_path)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()
    return backup_path


def restore_threads(
    *,
    state_path: Path,
    config_path: Path,
    output_root: Path,
    apply: bool,
    target_provider: str | None = None,
    timestamp: str | None = None,
) -> RestoreResult:
    state_path = state_path.expanduser().resolve()
    config_path = config_path.expanduser().resolve()
    target_provider = target_provider or read_model_provider(config_path)
    run_dir = make_run_dir(output_root, timestamp or timestamp_now())

    conn = sqlite3.connect(state_path)
    try:
        threads = fetch_threads(conn)
        copied_paths, missing_rollouts = copy_rewritten_rollouts(
            (rollout_path for _, rollout_path, _ in threads),
            run_dir,
            target_provider,
            apply,
        )
        updated_threads = sum(
            1
            for _, rollout_path, model_provider in threads
            if model_provider != target_provider or (rollout_path and rollout_path in copied_paths)
        )

        backup_path = None
        if apply:
            run_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_database(conn, run_dir, state_path)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE threads SET model_provider = ?", (target_provider,))
            for source_path, destination_path in copied_paths.items():
                conn.execute(
                    "UPDATE threads SET rollout_path = ? WHERE rollout_path = ?",
                    (destination_path, source_path),
                )
            conn.commit()

        return RestoreResult(
            target_provider=target_provider,
            updated_threads=updated_threads,
            copied_rollouts=len(copied_paths),
            missing_rollouts=missing_rollouts,
            backup_path=backup_path,
            run_dir=run_dir,
            copied_paths=copied_paths,
        )
    except Exception:
        if apply:
            conn.rollback()
        raise
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore Codex Desktop conversations after model provider changes.",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_CODEX_HOME / "state_5.sqlite",
        help="Path to Codex state SQLite database.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CODEX_HOME / "config.toml",
        help="Path to Codex config.toml used to read the current model_provider.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_CODEX_HOME / "provider-restore-rollouts",
        help="Directory where backups and rewritten rollout copies are stored.",
    )
    parser.add_argument(
        "--provider",
        help="Override target provider instead of reading model_provider from config.toml.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag, the tool only prints what it would do.",
    )
    return parser


def print_result(result: RestoreResult, apply: bool) -> None:
    mode = "APPLIED" if apply else "DRY RUN"
    print(f"Mode: {mode}")
    print(f"Target provider: {result.target_provider}")
    print(f"Threads to update: {result.updated_threads}")
    print(f"Rollout copies: {result.copied_rollouts}")
    print(f"Missing rollout files: {result.missing_rollouts}")
    print(f"Run directory: {result.run_dir}")
    if result.backup_path:
        print(f"SQLite backup: {result.backup_path}")
    if not apply:
        print("No files or database rows were changed. Re-run with --apply to restore.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    result = restore_threads(
        state_path=args.state,
        config_path=args.config,
        output_root=args.output_root,
        apply=args.apply,
        target_provider=args.provider,
    )
    print_result(result, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
