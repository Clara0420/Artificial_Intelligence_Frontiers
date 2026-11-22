#!/usr/bin/env python3
"""P2 isolated entry; execute from the original project root."""
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
from tableqa.crops import attach_geometry, choose_tables, crop_table
from tableqa.documents import source_path
from tableqa.evidence import GUIDANCE
from tableqa.xlsxio import read_questions, id_key

ROOT = Path(__file__).resolve().parents[2]


def engine_hash():
    paths = sorted(Path(__file__).parent.glob('tableqa/*.py')) + [Path(__file__)]
    return hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()


def prepare(questions, files, ocr, max_chars, ids, limit, output, *, context_mode="full", no_crops=False, max_crops=2, crop_dpi=300, crop_max_side=3200, crop_max_pixels=5760000):
    selected = [q for q in questions if ids is None or id_key(q.id) in ids]
    if limit:
        selected = selected[:limit]
    full, documents = load_contexts(selected, files, ocr, max_chars)
    indexes = {d['file_name']:attach_geometry(index_document(d['ocr_directory'])) for d in documents}
    source_hashes = {d['file_name']:d['source_sha256'] for d in documents}
    contexts, audits, supplements, crop_audit = {}, {}, {}, {}
    version = engine_hash()
    for q in selected:
        context, audit = select_context(q, indexes[q.file_name], full[q.file_name])
        audit['locator_mode']=audit['mode']
        if context_mode == 'full':
            context=GUIDANCE+'\n'+full[q.file_name]
            audit['mode']='full_document'
            audit['characters']=len(context)
        crop_audit[id_key(q.id)]={'added':[], 'skipped':[]}
        for table in ([] if no_crops else choose_tables(q,indexes[q.file_name],audit,max_crops)):
            try:
                crop=crop_table(source_path(files,q.file_name),source_hashes[q.file_name],table,output/'crops',
                                dpi=crop_dpi,max_side=crop_max_side,max_pixels=crop_max_pixels)
                crop_audit[id_key(q.id)]['added'].append(crop)
            except (ValueError,OSError,IndexError,RuntimeError) as exc:
                crop_audit[id_key(q.id)]['skipped'].append({'table_id':table['table_id'],'reason':str(exc)})
        supplements[id_key(q.id)]=crop_audit[id_key(q.id)]['added']
        # Covers retrieval/recovery/validation changes in cache identity.
        contexts[id_key(q.id)] = f'P2版本: {version}\n' + context
        audits[id_key(q.id)] = {'file_name':q.file_name, **audit}
    atomic_json(output/'ocr_audit.json', documents)
    atomic_json(output/'retrieval_audit.json', audits)
    atomic_json(output/'question_contexts.json', contexts)
    atomic_json(output/"crop_audit.json", crop_audit)
    atomic_json(output/"p2_config.json", {"context_mode":context_mode,"no_crops":no_crops,"max_crops":max_crops,"crop_dpi":crop_dpi,"crop_max_side":crop_max_side,"crop_max_pixels":crop_max_pixels,"engine_sha256":version})
    return contexts, supplements


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--p2-help',action='store_true',help='显示P2专用参数')
    parser.add_argument('--ids')
    parser.add_argument('--context-mode',choices=['full','ranked'],default='full')
    parser.add_argument('--no-crops',action='store_true')
    parser.add_argument('--max-crops',type=baseline.nonnegative,default=2)
    parser.add_argument('--crop-dpi',type=baseline.positive,default=300)
    parser.add_argument('--crop-max-side',type=baseline.positive,default=3200)
    parser.add_argument('--crop-max-pixels',type=baseline.positive,default=5760000)
    parser.add_argument('--ocr-dir', type=Path, default=ROOT/'runs/ocr_all')
    parser.add_argument('--ocr-max-chars', type=baseline.positive, default=120000)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--merge-base', type=Path)
    parser.add_argument('--merged-output', type=Path)
    options, rest = parser.parse_known_args(argv)
    if options.p2_help:
        parser.print_help()
        return 0
    try:
        if options.max_crops>4 or options.crop_dpi>600 or options.crop_max_side>4096 or options.crop_max_pixels>16777216:
            raise ValueError('max-crops上限4，crop-dpi上限600，crop-max-side上限4096，crop-max-pixels上限16777216')
        crop_options={name:getattr(options,name) for name in ('context_mode','no_crops','max_crops','crop_dpi','crop_max_side','crop_max_pixels')}
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
            check.add_argument('--output-dir', type=Path, default=ROOT/'runs/p2_check')
            args = check.parse_args(rest)
            questions = read_questions(args.tests,args.sheet,json.loads(args.dataset_fixes.read_text()))
            ids = parse_ids(options.ids,questions)
            protect_output(args.output_dir)
            contexts, supplements = prepare(questions,args.files,options.ocr_dir,options.ocr_max_chars,ids,args.limit,args.output_dir,**crop_options)
            print(f'P2检查完成：{len(contexts)}题，API调用=0；输出 {args.output_dir}')
            return 0
        def runner(questions, files, output, config, client, **kwargs):
            ids = parse_ids(options.ids, questions)
            if options.merge_base:
                from tableqa.xlsxio import validate_submission
                validate_submission(questions, options.merge_base, validate_scopes=False)
                if options.merge_base.resolve().is_relative_to(output.resolve()):
                    raise ValueError('合并基准不得位于本次输出目录内')
            protect_output(output)
            contexts, supplements = prepare(questions,files,options.ocr_dir,options.ocr_max_chars,ids,kwargs.get('limit'),output,**crop_options)
            report = run(questions,files,output,config,client,contexts=contexts,ids=ids,supplements=supplements,**kwargs)
            if options.merge_base:
                report['merge'] = merge(questions,options.merge_base,report,options.merged_output)
                report['merged_output'] = str(options.merged_output.resolve())
                atomic_json(output/'run_report.json',report)
            return report
        return baseline.main(['run',*rest],runner=runner,default_output=str(ROOT/'runs/p2_fusion'),publish=False)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(f'P2错误: {exc}',file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
