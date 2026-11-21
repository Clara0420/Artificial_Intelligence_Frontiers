"""Run PP-StructureV3 on a file or directory, with document-level resume."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from time import perf_counter
import traceback


SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SCRIPT_VERSION = 1


def save_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def completed(directory):
    marker = directory / "completed.json"
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data["pages"] > 0 and bool(data["artifacts"]) and all(
            (directory / item["path"]).is_file()
            and (directory / item["path"]).stat().st_size == item["bytes"]
            for item in data["artifacts"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="PDF/image file or directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="gpu:0")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    if args.cpu_threads < 1:
        parser.error("--cpu-threads must be positive")
    if source.is_file():
        files = [source] if source.suffix.lower() in SUPPORTED else []
        root = source.parent
    elif source.is_dir():
        if output == source or source in output.parents:
            parser.error("输出目录请放在输入目录之外，避免将输出再次作为输入")
        candidates = source.rglob("*") if args.recursive else source.iterdir()
        files = sorted(p for p in candidates if p.is_file() and p.suffix.lower() in SUPPORTED)
        root = source
    else:
        parser.error(f"输入不存在：{source}")
    if not files:
        parser.error("没有找到支持的 PDF 或图片")
    output.mkdir(parents=True, exist_ok=True)

    versions = {
        name: importlib.metadata.version(name) for name in ("paddleocr", "paddlex")
    }
    import paddle
    from paddleocr import PPStructureV3

    versions["paddle"] = paddle.__version__
    settings = dict(
        device=args.device,
        cpu_threads=args.cpu_threads,
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        use_table_recognition=True,
        use_formula_recognition=False,
        use_seal_recognition=False,
        use_chart_recognition=False,
    )
    config = {"script_version": SCRIPT_VERSION, "versions": versions, "settings": settings}
    save_json(output / "run_config.json", config)
    pipeline = None
    records = []
    total_started = perf_counter()
    print(f"找到 {len(files)} 份文档；设备 {args.device}", flush=True)

    for index, path in enumerate(files, 1):
        relative = path.relative_to(root)
        record = {"file": relative.as_posix()}
        started = perf_counter()
        try:
            signature = {**config, "source_sha256": file_hash(path)}
            key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
            directory = output / relative / key[:16]
            record["output"] = str(directory.relative_to(output))
            if completed(directory):
                record["status"] = "skipped"
                print(f"[{index}/{len(files)}] 已完成，跳过 {relative}", flush=True)
            else:
                if pipeline is None:
                    print("开始初始化模型（首次运行可能下载权重）", flush=True)
                    load_started = perf_counter()
                    pipeline = PPStructureV3(**settings)
                    print(f"模型初始化耗时 {perf_counter() - load_started:.1f} 秒", flush=True)
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "completed.json").unlink(missing_ok=True)
                save_json(directory / "source.json", signature)
                print(f"[{index}/{len(files)}] 开始推理 {relative}", flush=True)
                pages = 0
                artifacts = []
                for pages, result in enumerate(pipeline.predict(input=str(path)), 1):
                    page_dir = directory / f"page-{pages:04d}"
                    page_dir.mkdir(parents=True, exist_ok=True)
                    print(f"  第 {pages} 页推理返回，开始保存", flush=True)
                    result.save_to_json(save_path=str(page_dir))
                    result.save_to_markdown(save_path=str(page_dir))
                    result.save_to_html(save_path=str(page_dir))
                    page_files = [p for p in page_dir.rglob("*") if p.is_file()]
                    if not any(p.suffix == ".json" for p in page_files):
                        raise RuntimeError(f"第 {pages} 页没有生成 JSON")
                    artifacts.extend(
                        {"path": p.relative_to(directory).as_posix(), "bytes": p.stat().st_size}
                        for p in page_files
                    )
                    print(f"  第 {pages} 页保存完成", flush=True)
                if pages == 0:
                    raise RuntimeError("流水线没有返回任何页面")
                save_json(directory / "completed.json", {"pages": pages, "artifacts": artifacts})
                record.update(status="ok", pages=pages)
                print(f"  文档完成，共 {pages} 页", flush=True)
        except Exception as exc:
            record.update(status="failed", error=str(exc), traceback=traceback.format_exc())
            print(f"  失败：{relative}：{exc}", flush=True)
            traceback.print_exc()
            # Initialization failures affect every document; stop rather than retry all files.
            if pipeline is None:
                record["seconds"] = round(perf_counter() - started, 2)
                records.append(record)
                save_json(output / "summary.json", {"total": len(files), "results": records})
                raise
        record["seconds"] = round(perf_counter() - started, 2)
        records.append(record)
        save_json(output / "summary.json", {"total": len(files), "results": records})

    failed = sum(r["status"] == "failed" for r in records)
    skipped = sum(r["status"] == "skipped" for r in records)
    print(
        f"结束：成功 {len(records) - failed - skipped}，跳过 {skipped}，失败 {failed}；"
        f"总耗时 {perf_counter() - total_started:.1f} 秒。详情见 {output / 'summary.json'}",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
