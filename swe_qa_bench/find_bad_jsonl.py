#!/usr/bin/env python3
import argparse
import json
import os
import sys


def iter_files(root, exts, follow_symlinks):
    for dirpath, _, filenames in os.walk(root, followlinks=follow_symlinks):
        for name in filenames:
            for ext in exts:
                if name.endswith(ext):
                    yield os.path.join(dirpath, name)
                    break


def check_jsonl(path):
    with open(path, "rb") as f:
        for line_no, raw in enumerate(f, start=1):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError as e:
                return {
                    "path": path,
                    "error_type": "decode",
                    "line": line_no,
                    "error": str(e),
                }
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                return {
                    "path": path,
                    "error_type": "json",
                    "line": line_no,
                    "error": str(e),
                }
    return None


def check_json(path):
    try:
        data = open(path, "rb").read()
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return {
            "path": path,
            "error_type": "decode",
            "line": None,
            "error": str(e),
        }
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        return {
            "path": path,
            "error_type": "json",
            "line": e.lineno,
            "error": str(e),
        }
    return None


def main():
      parser = argparse.ArgumentParser(
          description="Find invalid UTF-8 or invalid JSON in .json/.jsonl files."
      )
      parser.add_argument("--root", required=True, help="Root directory to scan.")
      parser.add_argument(
          "--extensions", default=".jsonl,.json", help="Comma-separated extensions."
      )
      parser.add_argument("--follow-symlinks", action="store_true")
      parser.add_argument("--report", choices=("text", "json"), default="text")
      parser.add_argument("--stop-at-first", action="store_true")
      args = parser.parse_args()

      exts = [e.strip() for e in args.extensions.split(",") if e.strip()]
      exts = [e if e.startswith(".") else "." + e for e in exts]

      bad = []
      total = 0
      for path in iter_files(args.root, exts, args.follow_symlinks):
          total += 1
          if path.endswith(".jsonl"):
              err = check_jsonl(path)
          else:
              err = check_json(path)
          if err:
              bad.append(err)
              if args.report == "text":
                line_info = f":{err['line']}" if err['line'] else ""
                print(f"BAD {err['error_type']} {path}{line_info}{err['error']}")
              if args.stop_at_first:
                  break

      if args.report == "json":
          print(json.dumps(bad, ensure_ascii=True, indent=2))
      print(f"Scanned {total} files, bad {len(bad)}", file=sys.stderr)
      return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())