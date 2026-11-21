#!/usr/bin/env python3
"""OCR-fused QA. All baseline run flags are available."""
import argparse
from functools import partial
from pathlib import Path
import sys

import baseline
from tableqa.ocr_pipeline import load_contexts, run_ocr
from tableqa.pipeline import atomic_json
from tableqa.schema import json_loads
from tableqa.xlsxio import read_questions


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--ocr-dir", type=Path, default=Path("runs/ocr_all"))
    parser.add_argument("--ocr-max-chars", type=baseline.positive, default=120000)
    parser.add_argument("--check-only", action="store_true")
    args, rest = parser.parse_known_args()
    if args.check_only:
        check = argparse.ArgumentParser(description="验证 OCR 文件，不调用 API")
        check.add_argument("--tests", type=Path, default=Path("data/tests.xlsx"))
        check.add_argument("--files", type=Path, default=Path("data/files"))
        check.add_argument("--sheet")
        check.add_argument("--dataset-fixes", type=Path, default=Path("configs/downloaded_data_fixes.json"))
        check.add_argument("--output-dir", type=Path, default=Path("runs/ocr_fusion"))
        options = check.parse_args(rest)
        try:
            fixes = json_loads(options.dataset_fixes.read_text(encoding="utf-8"))
            questions = read_questions(options.tests, options.sheet, fixes)
            _, audit = load_contexts(questions, options.files, args.ocr_dir, args.ocr_max_chars)
            atomic_json(options.output_dir / "ocr_audit.json", audit)
            print(f"检查通过：{len(questions)} 题，{len(audit)} 份文档，"
                  f"{sum(item['pages'] for item in audit)} 页；API 调用为 0")
            return 0
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"OCR 检查失败：{exc}", file=sys.stderr)
            return 2
    runner = partial(run_ocr, ocr_dir=args.ocr_dir, max_chars=args.ocr_max_chars)
    return baseline.main(["run", *rest], runner=runner,
                         default_output="runs/ocr_fusion", publish=False)


if __name__ == "__main__":
    raise SystemExit(main())
