#!/usr/bin/env python3
"""P1 isolated entry; execute from the original project root."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import baseline
from tableqa.experiment import merge, parse_ids, protect_output
from tableqa.ocr_pipeline import load_contexts
from tableqa.pipeline import atomic_json, run
from tableqa.retrieval import index_document, select_context
from tableqa.xlsxio import read_questions, id_key

ROOT = Path(__file__).resolve().parents[2]


def engine_hash():
    paths = sorted(Path(__file__).parent.glob('tableqa/*.py')) + [Path(__file__)]
    return hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()


def prepare(questions, files, ocr, max_chars, ids, limit, output):
    selected = [q for q in questions if ids is None or id_key(q.id) in ids]
    if limit:
        selected = selected[:limit]
    full, documents = load_contexts(selected, files, ocr, max_chars)
    indexes = {d['file_name']:index_document(d['ocr_directory']) for d in documents}
    contexts, audits = {}, {}
    version = engine_hash()
    for q in selected:
        context, audit = select_context(q, indexes[q.file_name], full[q.file_name])
        # Covers retrieval/recovery/validation changes in cache identity.
        contexts[id_key(q.id)] = f'P1版本: {version}\n' + context
        audits[id_key(q.id)] = {'file_name':q.file_name, **audit}
    atomic_json(output/'ocr_audit.json', documents)
    atomic_json(output/'retrieval_audit.json', audits)
    atomic_json(output/'question_contexts.json', contexts)
    return contexts


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--ids')
    parser.add_argument('--ocr-dir', type=Path, default=ROOT/'runs/ocr_all')
    parser.add_argument('--ocr-max-chars', type=baseline.positive, default=120000)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--merge-base', type=Path)
    parser.add_argument('--merged-output', type=Path)
    options, rest = parser.parse_known_args(argv)
    try:
        if bool(options.merge_base) != bool(options.merged_output):
            raise ValueError('--merge-base 和 --merged-output 必须一起提供')
        if options.merged_output and options.merged_output.exists():
            raise ValueError('合并目标已存在，禁止覆盖')
        if options.check_only:
            check = argparse.ArgumentParser()
            check.add_argument('--tests', type=Path, default=ROOT/'data/tests.xlsx')
            check.add_argument('--files', type=Path, default=ROOT/'data/files')
            check.add_argument('--dataset-fixes', type=Path, default=ROOT/'configs/downloaded_data_fixes.json')
            check.add_argument('--sheet')
            check.add_argument('--limit', type=baseline.positive)
            check.add_argument('--output-dir', type=Path, default=ROOT/'runs/p1_check')
            args = check.parse_args(rest)
            questions = read_questions(args.tests,args.sheet,json.loads(args.dataset_fixes.read_text()))
            ids = parse_ids(options.ids,questions)
            protect_output(args.output_dir)
            contexts = prepare(questions,args.files,options.ocr_dir,options.ocr_max_chars,ids,args.limit,args.output_dir)
            print(f'P1检查完成：{len(contexts)}题，API调用=0；输出 {args.output_dir}')
            return 0
        def runner(questions, files, output, config, client, **kwargs):
            ids = parse_ids(options.ids, questions)
            if options.merge_base:
                from tableqa.xlsxio import validate_submission
                validate_submission(questions, options.merge_base, validate_scopes=False)
                if options.merge_base.resolve().is_relative_to(output.resolve()):
                    raise ValueError('合并基准不得位于本次输出目录内')
            protect_output(output)
            contexts = prepare(questions,files,options.ocr_dir,options.ocr_max_chars,ids,kwargs.get('limit'),output)
            report = run(questions,files,output,config,client,contexts=contexts,ids=ids,**kwargs)
            if options.merge_base:
                report['merge'] = merge(questions,options.merge_base,report,options.merged_output)
                report['merged_output'] = str(options.merged_output.resolve())
                atomic_json(output/'run_report.json',report)
            return report
        return baseline.main(['run',*rest],runner=runner,default_output=str(ROOT/'runs/p1_fusion'),publish=False)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(f'P1错误: {exc}',file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
