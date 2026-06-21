#!/usr/bin/env python3
"""
Restore Codex Desktop thread visibility after switching model providers.

Default mode is dry-run. Use --apply to write backups, update the SQLite
provider index, and rewrite rollout provider metadata in place.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
DEFAULT_BACKUP_RETENTION = 5
RUN_DIR_PATTERN = re.compile(r"^\d{8}-\d{6}$")
SQLITE_BACKUP_NAME = "state_5.sqlite.before-provider-restore.sqlite"


@dataclass(frozen=True)
class RestoreResult:
    target_provider: str
    updated_threads: int
    rewritten_rollouts: int
    missing_rollouts: int
    backup_path: Path | None
    run_dir: Path
    rollout_backup_paths: dict[str, str]
    removed_backup_runs: list[Path]


@dataclass(frozen=True)
class RollbackResult:
    run_dir: Path
    sqlite_backup_path: Path
    restored_rollouts: int
    missing_rollouts: int
    current_backup_path: Path | None


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


TIME_COLUMNS = ("updated_at", "updated_at_ms", "recency_at", "recency_at_ms")


def snapshot_thread_times(conn: sqlite3.Connection) -> tuple[list[str], dict[str, tuple[int | None, ...]]]:
    columns = [column for column in TIME_COLUMNS if column in table_columns(conn, "threads")]
    if not columns:
        return [], {}

    selected_columns = ", ".join(["id", *columns])
    return columns, {
        row[0]: tuple(row[1:])
        for row in conn.execute(f"SELECT {selected_columns} FROM threads")
    }


def restore_thread_times(
    conn: sqlite3.Connection,
    columns: list[str],
    thread_times: dict[str, tuple[int | None, ...]],
) -> None:
    if not columns:
        return

    assignments = ", ".join(f"{column} = ?" for column in columns)
    conn.executemany(
        f"UPDATE threads SET {assignments} WHERE id = ?",
        ((*values, thread_id) for thread_id, values in thread_times.items()),
    )


def fetch_threads(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    columns = table_columns(conn, "threads")
    order_by = "updated_at_ms DESC" if "updated_at_ms" in columns else "id"
    return [
        (thread_id, rollout_path, model_provider)
        for thread_id, rollout_path, model_provider in conn.execute(
            f"SELECT id, rollout_path, model_provider FROM threads ORDER BY {order_by}"
        )
    ]


def original_path_from_restore_copy(rollout_path: str, output_root: Path) -> str:
    path = Path(rollout_path).expanduser()
    root = output_root.expanduser()
    path_parts = path.parts
    root_parts = root.parts
    rollouts_index = len(root_parts) + 1

    if (
        len(path_parts) > rollouts_index + 1
        and path_parts[: len(root_parts)] == root_parts
        and path_parts[rollouts_index] == "rollouts"
    ):
        original_path = Path(path.anchor, *path_parts[rollouts_index + 1 :])
        if original_path.exists():
            return str(original_path)

    return rollout_path


def make_run_dir(output_root: Path, timestamp: str) -> Path:
    return output_root.expanduser().resolve() / timestamp


def sqlite_backup_path_for_run(run_dir: Path) -> Path:
    return run_dir / "backups" / SQLITE_BACKUP_NAME


def list_backup_runs(output_root: Path) -> list[Path]:
    output_root = output_root.expanduser()
    if not output_root.exists():
        return []

    return sorted(
        path
        for path in output_root.iterdir()
        if path.is_dir()
        and RUN_DIR_PATTERN.match(path.name)
        and sqlite_backup_path_for_run(path).exists()
    )


def timestamped_run_dirs(output_root: Path) -> list[Path]:
    output_root = output_root.expanduser()
    if not output_root.exists():
        return []

    return sorted(
        path
        for path in output_root.iterdir()
        if path.is_dir() and RUN_DIR_PATTERN.match(path.name)
    )


def latest_backup_run(output_root: Path) -> Path | None:
    backup_runs = list_backup_runs(output_root)
    return backup_runs[-1] if backup_runs else None


def cleanup_backup_runs(output_root: Path, keep: int = DEFAULT_BACKUP_RETENTION) -> list[Path]:
    output_root = output_root.expanduser()
    if keep < 0:
        raise ValueError("keep must be 0 or greater")
    if not output_root.exists():
        return []

    run_dirs = timestamped_run_dirs(output_root)
    remove_count = max(0, len(run_dirs) - keep)
    removed = run_dirs[:remove_count]
    for run_dir in removed:
        shutil.rmtree(run_dir)
    return removed


def destination_for_rollout_backup(run_dir: Path, source_path: Path) -> Path:
    source_path = source_path.expanduser()
    if source_path.is_absolute():
        relative_parts = source_path.parts[1:]
    else:
        relative_parts = source_path.parts
    return run_dir / "rollout-backups" / Path(*relative_parts)


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
        if (
            isinstance(payload, dict)
            and "model_provider" in payload
            and payload["model_provider"] != target_provider
        ):
            payload["model_provider"] = target_provider
            output_lines.append(json.dumps(record, ensure_ascii=False) + newline)
            continue

        output_lines.append(line)
    return "".join(output_lines)


def rewrite_rollouts_in_place(
    rollout_paths: Iterable[str],
    run_dir: Path,
    target_provider: str,
    apply: bool,
) -> tuple[dict[str, str], int]:
    backup_paths: dict[str, str] = {}
    missing_count = 0

    for rollout_path in sorted(set(path for path in rollout_paths if path)):
        source_path = Path(rollout_path).expanduser()
        if not source_path.exists():
            missing_count += 1
            continue

        original_text = source_path.read_text(encoding="utf-8")
        rewritten_text = rewrite_rollout_text(original_text, target_provider)
        if rewritten_text == original_text:
            continue

        backup_path = destination_for_rollout_backup(run_dir, source_path)
        backup_paths[str(source_path)] = str(backup_path)
        if not apply:
            continue

        original_stat = source_path.stat()
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(original_text, encoding="utf-8")
        source_path.write_text(rewritten_text, encoding="utf-8")
        os.utime(source_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    return backup_paths, missing_count


def backup_database(conn: sqlite3.Connection, run_dir: Path, state_path: Path) -> Path:
    backup_path = sqlite_backup_path_for_run(run_dir)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_conn = sqlite3.connect(backup_path)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()
    return backup_path


def rollout_restore_destinations(run_dir: Path) -> list[tuple[Path, Path]]:
    backup_root = run_dir / "rollout-backups"
    if not backup_root.exists():
        return []

    destinations: list[tuple[Path, Path]] = []
    for backup_path in sorted(path for path in backup_root.rglob("*") if path.is_file()):
        relative_path = backup_path.relative_to(backup_root)
        destinations.append((backup_path, Path(backup_path.anchor, *relative_path.parts)))
    return destinations


def backup_current_state_for_rollback(run_dir: Path, state_path: Path) -> Path:
    backup_path = (
        run_dir
        / "rollback-backups"
        / timestamp_now()
        / f"{state_path.name}.before-rollback.sqlite"
    )
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(state_path, backup_path)
    return backup_path


def remove_sqlite_sidecars(state_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar_path = Path(str(state_path) + suffix)
        if sidecar_path.exists():
            sidecar_path.unlink()


def rollback_backup_run(run_dir: Path, *, state_path: Path, apply: bool) -> RollbackResult:
    run_dir = run_dir.expanduser().resolve()
    state_path = state_path.expanduser().resolve()
    sqlite_backup_path = sqlite_backup_path_for_run(run_dir)
    if not sqlite_backup_path.exists():
        raise FileNotFoundError(f"SQLite backup not found: {sqlite_backup_path}")

    rollout_destinations = rollout_restore_destinations(run_dir)
    current_backup_path = None
    if apply:
        current_backup_path = backup_current_state_for_rollback(run_dir, state_path)
        remove_sqlite_sidecars(state_path)
        shutil.copy2(sqlite_backup_path, state_path)
        for backup_path, destination_path in rollout_destinations:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, destination_path)

    return RollbackResult(
        run_dir=run_dir,
        sqlite_backup_path=sqlite_backup_path,
        restored_rollouts=len(rollout_destinations),
        missing_rollouts=0,
        current_backup_path=current_backup_path,
    )


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
        normalized_threads = [
            (thread_id, original_path_from_restore_copy(rollout_path, output_root), rollout_path, model_provider)
            for thread_id, rollout_path, model_provider in threads
        ]
        rollout_backup_paths, missing_rollouts = rewrite_rollouts_in_place(
            (normalized_rollout_path for _, normalized_rollout_path, _, _ in normalized_threads),
            run_dir,
            target_provider,
            apply,
        )
        updated_threads = sum(
            1
            for _, normalized_rollout_path, db_rollout_path, model_provider in normalized_threads
            if (
                model_provider != target_provider
                or db_rollout_path != normalized_rollout_path
                or (normalized_rollout_path and normalized_rollout_path in rollout_backup_paths)
            )
        )

        backup_path = None
        removed_backup_runs: list[Path] = []
        if apply:
            run_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_database(conn, run_dir, state_path)
            conn.execute("BEGIN IMMEDIATE")
            time_columns, thread_times = snapshot_thread_times(conn)
            conn.execute(
                "UPDATE threads SET model_provider = ? WHERE model_provider <> ?",
                (target_provider, target_provider),
            )
            for _, normalized_rollout_path, db_rollout_path, _ in normalized_threads:
                if db_rollout_path != normalized_rollout_path:
                    conn.execute(
                        "UPDATE threads SET rollout_path = ? WHERE rollout_path = ? AND rollout_path <> ?",
                        (normalized_rollout_path, db_rollout_path, normalized_rollout_path),
                    )
            restore_thread_times(conn, time_columns, thread_times)
            conn.commit()
            removed_backup_runs = cleanup_backup_runs(output_root)

        return RestoreResult(
            target_provider=target_provider,
            updated_threads=updated_threads,
            rewritten_rollouts=len(rollout_backup_paths),
            missing_rollouts=missing_rollouts,
            backup_path=backup_path,
            run_dir=run_dir,
            rollout_backup_paths=rollout_backup_paths,
            removed_backup_runs=removed_backup_runs,
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
        help="Directory where SQLite and rollout backups are stored.",
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
    parser.add_argument(
        "--cleanup-backups",
        action="store_true",
        help="Only remove old backup run directories under --output-root, keeping the newest 5.",
    )
    parser.add_argument(
        "--list-backups",
        action="store_true",
        help="List backup run directories that can be used with --rollback.",
    )
    parser.add_argument(
        "--rollback",
        type=Path,
        help="Restore from the specified backup run directory. Add --apply to write changes.",
    )
    parser.add_argument(
        "--rollback-latest",
        action="store_true",
        help="Restore from the newest backup run under --output-root. Add --apply to write changes.",
    )
    return parser


def print_result(result: RestoreResult, apply: bool) -> None:
    mode = "APPLIED" if apply else "DRY RUN"
    print(f"Mode: {mode}")
    print(f"Target provider: {result.target_provider}")
    print(f"Threads to update: {result.updated_threads}")
    print(f"Rollouts to rewrite: {result.rewritten_rollouts}")
    print(f"Missing rollout files: {result.missing_rollouts}")
    print(f"Run directory: {result.run_dir}")
    if result.backup_path:
        print(f"SQLite backup: {result.backup_path}")
    if apply:
        print(f"Old backup runs removed: {len(result.removed_backup_runs)}")
    if not apply:
        print("No files or database rows were changed. Re-run with --apply to restore.")


def print_cleanup_result(output_root: Path, removed_backup_runs: list[Path]) -> None:
    print(f"Cleanup mode: backups only")
    print(f"Backup root: {output_root.expanduser().resolve()}")
    print(f"Keeping newest backup runs: {DEFAULT_BACKUP_RETENTION}")
    print(f"Old backup runs removed: {len(removed_backup_runs)}")


def print_backup_runs(output_root: Path, backup_runs: list[Path]) -> None:
    print(f"Backup root: {output_root.expanduser().resolve()}")
    print(f"Backup runs: {len(backup_runs)}")
    for backup_run in backup_runs:
        print(backup_run)


def print_rollback_result(result: RollbackResult, apply: bool) -> None:
    mode = "APPLIED" if apply else "DRY RUN"
    print(f"Rollback mode: {mode}")
    print(f"Backup run: {result.run_dir}")
    print(f"SQLite backup: {result.sqlite_backup_path}")
    print(f"Rollouts to restore: {result.restored_rollouts}")
    print(f"Missing rollout backups: {result.missing_rollouts}")
    if result.current_backup_path:
        print(f"Current SQLite backup: {result.current_backup_path}")
    if not apply:
        print("No files or database rows were changed. Re-run with --apply to rollback.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    action_count = sum(
        bool(action)
        for action in (
            args.cleanup_backups,
            args.list_backups,
            args.rollback is not None,
            args.rollback_latest,
        )
    )
    if action_count > 1:
        parser.error("Choose only one of --cleanup-backups, --list-backups, --rollback, or --rollback-latest.")

    if args.cleanup_backups:
        removed_backup_runs = cleanup_backup_runs(args.output_root)
        print_cleanup_result(args.output_root, removed_backup_runs)
        return 0

    if args.list_backups:
        print_backup_runs(args.output_root, list_backup_runs(args.output_root))
        return 0

    if args.rollback is not None or args.rollback_latest:
        rollback_run_dir = args.rollback
        if args.rollback_latest:
            rollback_run_dir = latest_backup_run(args.output_root)
            if rollback_run_dir is None:
                parser.error(f"No backup runs found under {args.output_root.expanduser().resolve()}")
        result = rollback_backup_run(
            rollback_run_dir,
            state_path=args.state,
            apply=args.apply,
        )
        print_rollback_result(result, args.apply)
        return 0

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
