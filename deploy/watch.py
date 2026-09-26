#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf2zh-watch — "看目录"自动翻译外壳（pdf2zh-next 补丁版配套）

职责:
  扫描目标目录中的新 PDF → 逐个用 pdf2zh-next 独立子进程翻译 → 输出到 Translated/
  翻译失败重试 / 失败标记 / 日志裁剪 / 单实例保护

与旧系统（v1 定制外壳）的关系:
  - 扫描、marker、日志的"约定"完全沿用（历史文件无缝兼容）
  - 翻译执行改为调用 pdf2zh-next（本机 .venv），每文件独立进程 —— 内存不累积
  - 不包含: 输出改名、防睡眠声明

用法:
  python watch.py                    # 常驻监视
  python watch.py --once             # 只扫一轮后退出（测试用）
  python watch.py --once --dry-run   # 只列出候选，不翻译（零成本）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TOKEN_RE = re.compile(
    r"Total Token Usage:\s*Total\s*(\d+),\s*Prompt\s*(\d+),\s*Cache Hit Prompt\s*(\d+),\s*Completion\s*(\d+)"
)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


class Watch:
    def __init__(self, args: argparse.Namespace):
        self.dir = Path(args.dir)
        self.out = Path(args.output)
        self.log_path = Path(args.log_file)
        self.pdf2zh = Path(args.pdf2zh)
        self.config = Path(args.config)
        self.interval = args.interval
        self.keep_lines = args.keep_log_lines
        self.max_attempts = args.max_attempts
        self.delays = [int(x) for x in str(args.retry_delays).split(",") if x.strip()]
        self.timeout = args.timeout
        self.proxy = args.proxy
        self.dry_run = args.dry_run

    # ---------- logging ----------
    def log(self, msg: str) -> None:
        line = f"{now_str()} {msg}"
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        if sys.stdout and sys.stdout.isatty():
            print(line, flush=True)

    def prune_log(self) -> None:
        try:
            if not self.log_path.exists():
                return
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            if len(lines) <= self.keep_lines:
                return
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.writelines(lines[-self.keep_lines:])
        except OSError:
            pass

    # ---------- scanning ----------
    @staticmethod
    def _marker_paths(file_path: Path) -> tuple[Path, Path]:
        base = file_path.with_suffix("")
        return (
            Path(str(base) + ".pdf2zh.translating"),
            Path(str(base) + ".pdf2zh.failed.json"),
        )

    def _is_translated(self, stem: str, names_lower: set[str]) -> bool:
        """已翻译判定: 兼容旧命名 {stem}-dual.pdf 与新命名 {stem}.xxx.dual.pdf"""
        pat = re.compile("^" + re.escape(stem.lower()) + r"[-.]")
        for n in names_lower:
            if n.endswith("dual.pdf") and pat.match(n):
                return True
        return False

    def find_candidates(self) -> list[Path]:
        if not self.dir.is_dir():
            self.log(f"watch_path_not_found path={self.dir}")
            return []
        out_abs = self.out.resolve()
        out_names_lower: set[str] = set()
        try:
            out_names_lower = {p.name.lower() for p in self.out.iterdir() if p.is_file()}
        except OSError:
            pass
        results: list[Path] = []
        for root, _, files in os.walk(self.dir):
            root_p = Path(root)
            try:
                if root_p.resolve().is_relative_to(out_abs):
                    continue
            except (OSError, ValueError):
                pass
            names_lower = {f.lower() for f in files}
            for f in files:
                fl = f.lower()
                if not fl.endswith(".pdf"):
                    continue
                if fl.endswith("-dual.pdf") or fl.endswith("-mono.pdf"):
                    continue
                if fl.endswith("dual.pdf") or fl.endswith("mono.pdf"):
                    continue
                stem = f[:-4]
                translating, failed = self._marker_paths(root_p / f)
                if translating.exists() or failed.exists():
                    continue
                if self._is_translated(stem, names_lower) or self._is_translated(stem, out_names_lower):
                    continue
                results.append(root_p / f)
        results.sort()
        return results

    # ---------- translation ----------
    def translate_once(self, file_path: Path) -> tuple[bool, str]:
        """调用 pdf2zh-next 翻译单个文件。返回 (成功?, 摘要信息)。"""
        cmd = [
            str(self.pdf2zh),
            str(file_path),
            "--config-file", str(self.config),
            "--deepseek",
            "--term-deepseek",
            "--output", str(self.out),
        ]
        env = os.environ.copy()
        if self.proxy:
            env["HTTP_PROXY"] = self.proxy
            env["HTTPS_PROXY"] = self.proxy
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=env,
            )
            raw = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            return False, f"timeout after {self.timeout}s"
        except OSError as e:
            return False, f"spawn failed: {e}"

        out_clean = strip_ansi(raw)
        m = TOKEN_RE.search(out_clean)
        token_note = ""
        if m:
            token_note = (
                f" tokens_total={m.group(1)} prompt={m.group(2)}"
                f" cache_hit={m.group(3)} completion={m.group(4)}"
            )

        # 以"输出文件是否出现"为准（CLI 的退出码不可靠: 个别文件失败仍可能返回 0）
        stem = file_path.stem
        try:
            names_lower = {f.lower() for f in os.listdir(self.out)}
        except OSError:
            names_lower = set()
        if self._is_translated(stem, names_lower):
            return True, f"rc={proc.returncode}{token_note}"
        tail = out_clean.strip().splitlines()[-6:]
        return False, f"rc={proc.returncode}; tail: {' / '.join(x.strip() for x in tail)[:400]}"

    def process(self, file_path: Path) -> None:
        translating, failed = self._marker_paths(file_path)
        try:
            translating.write_text(
                json.dumps(
                    {
                        "status": "translating",
                        "started_at": datetime.now().isoformat(),
                        "service": "deepseek",
                        "model": "deepseek-flash",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

        last_err = ""
        for attempt in range(1, self.max_attempts + 1):
            self.log(f"translate_start file={file_path} attempt={attempt}")
            t0 = time.time()
            ok, info = self.translate_once(file_path)
            dur = time.time() - t0
            if ok:
                try:
                    translating.unlink(missing_ok=True)
                except OSError:
                    pass
                self.log(f"translate_success file={file_path} attempt={attempt} duration={dur:.1f}s {info}".rstrip())
                return
            last_err = info
            self.log(f"translate_fail file={file_path} attempt={attempt} duration={dur:.1f}s error={info}")
            if attempt < self.max_attempts and self.delays:
                time.sleep(self.delays[min(attempt - 1, len(self.delays) - 1)])

        try:
            translating.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            failed.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "failed_at": datetime.now().isoformat(),
                        "error": last_err[:500],
                        "attempts": self.max_attempts,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        self.log(f"translate_final_fail file={file_path} error={last_err[:300]}")

    # ---------- main loop ----------
    def run(self, once: bool) -> int:
        while True:
            self.log(f"scan_start interval={self.interval}s directory={self.dir}")
            candidates = self.find_candidates()
            self.log(f"scan_candidates count={len(candidates)}")
            if self.dry_run:
                for c in candidates:
                    print(f"[dry-run candidate] {c}")
            else:
                for c in candidates:
                    self.process(c)
            self.log("scan_end")
            self.prune_log()
            if once:
                break
            time.sleep(self.interval)
        return 0


def acquire_singleton_lock(lock_path: Path):
    """文件锁单实例保护。返回保持打开的文件对象（进程退出自动释放）。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print(f"another instance is running (lock: {lock_path})")
        sys.exit(0)
    return fh


def build_parser() -> argparse.ArgumentParser:
    home = Path(os.path.expanduser("~"))
    daily = home / "OneDrive" / "文档" / "Daily_paper"
    p = argparse.ArgumentParser(description="Watch a directory and translate new PDFs with pdf2zh-next.")
    p.add_argument("--dir", default=str(daily), help="directory to watch")
    p.add_argument("--output", default=str(daily / "Translated"), help="output directory")
    p.add_argument("--log-file", default=str(daily / "pdf2zh-watch.log"), help="log file path")
    p.add_argument("--pdf2zh", default=str(APP_DIR / ".venv" / "Scripts" / "pdf2zh_next.exe"), help="pdf2zh_next executable")
    p.add_argument("--config", default=str(APP_DIR / "config.toml"), help="config file for pdf2zh-next")
    p.add_argument("--interval", type=int, default=120, help="scan interval in seconds")
    p.add_argument("--keep-log-lines", type=int, default=100, help="keep last N log lines")
    p.add_argument("--max-attempts", type=int, default=3, help="max attempts per file")
    p.add_argument("--retry-delays", default="10,30,60", help="retry delays in seconds, comma separated")
    p.add_argument("--timeout", type=int, default=7200, help="per-file translation timeout in seconds")
    p.add_argument("--proxy", default="", help="optional HTTP(S) proxy for the translate subprocess")
    p.add_argument("--once", action="store_true", help="run a single scan cycle then exit")
    p.add_argument("--dry-run", action="store_true", help="only list candidates, do not translate")
    p.add_argument("--no-lock", action="store_true", help="skip singleton lock (for tests)")
    return p


def main() -> int:
    args = build_parser().parse_args()

    lock_fh = None
    if not args.no_lock:
        lock_fh = acquire_singleton_lock(APP_DIR / "watch.lock")

    w = Watch(args)
    try:
        rc = w.run(once=args.once)
    finally:
        if lock_fh is not None:
            lock_fh.close()
    return rc


def _main_guarded() -> int:
    try:
        return main()
    except SystemExit:
        raise
    except Exception:
        import traceback

        tb = traceback.format_exc()
        try:
            with open(APP_DIR / "watch-crash.log", "a", encoding="utf-8") as f:
                f.write(f"{now_str()} CRASH:\n{tb}\n")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(_main_guarded())
