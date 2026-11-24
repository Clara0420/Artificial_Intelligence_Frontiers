#!/usr/bin/env python3
"""P3 isolated entry; execute from the original project root."""
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
from tableqa.evidence import guidance_for, task_kind
from tableqa.relations import relation_context
from tableqa.xlsxio import read_questions, id_key

ROOT = Path(__file__).resolve().parents[2]


def engine_hash():
    paths = sorted(Path(__file__).parent.glob('tableqa/*.py')) + [Path(__file__)]
    return hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()


def prepare(questions, files, ocr, max_chars, ids, limit, output, *, context_mode="full", no_crops=False, max_crops=2, crop_dpi=300, crop_max_side=3200, crop_max_pixels=5760000, evidence_priority="visual_primary"):
    selected = [q for q in questions if ids is None or id_key(q.id) in ids]
    if limit:
        selected = selected[:limit]
    full, documents = load_contexts(selected, files, ocr, max_chars, evidence_priority)
    indexes = {d['file_name']:attach_geometry(index_document(d['ocr_directory'])) for d in documents}
    source_hashes = {d['file_name']:d['source_sha256'] for d in documents}
    contexts, audits, supplements, crop_audit, relations, relations_audit = {}, {}, {}, {}, {}, {}
    version = engine_hash()
    for q in selected:
        priority = "visual_primary" if task_kind(q) == "visual" else evidence_priority
        context, audit = select_context(q, indexes[q.file_name], full[q.file_name], priority)
        audit['locator_mode']=audit['mode']
        if context_mode == 'full':
            context=guidance_for(priority)+'\n'+full[q.file_name]
            audit['mode']='full_document'
            audit['characters']=len(context)
        score_map={x['table_id']:x['score'] for x in audit['scores']}
        relation, relation_audit = relation_context(q,indexes[q.file_name],score_map)
        relations[id_key(q.id)]=relation
        relations_audit[id_key(q.id)]=relation_audit
        if relation: context += '\n'+relation
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
        contexts[id_key(q.id)] = f'P3版本: {version}\n' + context
        audits[id_key(q.id)] = {'file_name':q.file_name, **audit}
    atomic_json(output/'ocr_audit.json', documents)
    atomic_json(output/'relation_audit.json', relations_audit)
    atomic_json(output/'retrieval_audit.json', audits)
    atomic_json(output/'question_contexts.json', contexts)
    atomic_json(output/"crop_audit.json", crop_audit)
    atomic_json(output/"p3_visual_config.json", {"context_mode":context_mode,"evidence_priority":evidence_priority,"no_crops":no_crops,"max_crops":max_crops,"crop_dpi":crop_dpi,"crop_max_side":crop_max_side,"crop_max_pixels":crop_max_pixels,"engine_sha256":version})
    return contexts, supplements, relations


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--p3-help',action='store_true')
    parser.add_argument('--ids')
    parser.add_argument('--no-reasoning-evidence',action='store_true')
    parser.add_argument('--no-review',action='store_true')
    parser.add_argument('--review-limit',type=baseline.nonnegative,default=160)
    parser.add_argument('--review-source',type=Path,default=ROOT/'runs/p2_full/run_report.json')
    parser.add_argument('--review-compare',type=Path,default=ROOT/'runs/ocr_fusion/run_report.json')
    parser.add_argument('--context-mode',choices=['full','ranked'],default='full')
    parser.add_argument('--visual-mode',choices=['page_only','page_plus_tables','table_chunks'],default='page_plus_tables',
                        help='输入策略：整页；整页+PP-Structure完整表格裁剪；按完整表格分别分块输入（保留原表头/单位上下文）')
    parser.add_argument('--evidence-priority',choices=['visual_primary','ocr_primary'],default='visual_primary',
                        help='证据优先级：默认视觉优先；ocr_primary 先按OCR定位/读取，再用原图核对（纯视觉题仍视觉优先）')
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
    if options.p3_help:
        parser.print_help()
        return 0
    try:
        if options.max_crops>6 or options.crop_dpi>600 or options.crop_max_side>4096 or options.crop_max_pixels>16777216:
            raise ValueError('max-crops上限6，crop-dpi上限600，crop-max-side上限4096，crop-max-pixels上限16777216')
        crop_options={name:getattr(options,name) for name in ('context_mode','no_crops','max_crops','crop_dpi','crop_max_side','crop_max_pixels')}
        crop_options['evidence_priority']=options.evidence_priority
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
            check.add_argument('--output-dir', type=Path, default=ROOT/'runs/p3_check')
            args = check.parse_args(rest)
            questions = read_questions(args.tests,args.sheet,json.loads(args.dataset_fixes.read_text()))
            ids = parse_ids(options.ids,questions)
            protect_output(args.output_dir)
            contexts, supplements, relations = prepare(questions,args.files,options.ocr_dir,options.ocr_max_chars,ids,args.limit,args.output_dir,**crop_options)
            visual_config_path=args.output_dir/"p3_visual_config.json"
            atomic_json(visual_config_path,{**json.loads(visual_config_path.read_text()),"visual_mode":options.visual_mode})
            print(f'P3检查完成：{len(contexts)}题，API调用=0；输出 {args.output_dir}')
            return 0
        def runner(questions, files, output, config, client, **kwargs):
            ids = parse_ids(options.ids, questions)
            if options.merge_base:
                from tableqa.xlsxio import validate_submission
                validate_submission(questions, options.merge_base, validate_scopes=False)
                if options.merge_base.resolve().is_relative_to(output.resolve()):
                    raise ValueError('合并基准不得位于本次输出目录内')
            protect_output(output)
            contexts, supplements, relations = prepare(questions,files,options.ocr_dir,options.ocr_max_chars,ids,kwargs.get('limit'),output,**crop_options)
            references, review_ids = {}, set()
            if not options.no_review and options.review_source.is_file() and options.review_compare.is_file():
                left=json.loads(options.review_source.read_text())
                right=json.loads(options.review_compare.read_text())
                lmap={str(r['id']):r for r in left.get('results',[])}
                rmap={str(r['id']):r for r in right.get('results',[])}
                candidates=[k for k,v in lmap.items() if k in rmap and v.get('status')=='ok' and rmap[k].get('status')=='ok' and v.get('answer') and rmap[k].get('answer') and v.get('answer')!=rmap[k].get('answer')]
                candidates=sorted(candidates,key=int)[:options.review_limit]
                review_ids=set(candidates)
                references={k:{'p2':lmap[k]['answer'],'ocr':rmap[k]['answer']} for k in candidates}
            atomic_json(output/'p3_config.json',{'reasoning_evidence':not options.no_reasoning_evidence,'review_enabled':not options.no_review,'review_candidates':sorted(review_ids,key=int),'review_limit':options.review_limit,'review_source':str(options.review_source),'review_compare':str(options.review_compare),'relation_context':True,'evidence_priority':options.evidence_priority})
            report = run(questions,files,output,config,client,contexts=contexts,ids=ids,supplements=supplements,relations=relations,reasoning_evidence=not options.no_reasoning_evidence,reference_answers=references,review_ids=review_ids,visual_mode=options.visual_mode,evidence_priority=options.evidence_priority,**kwargs)
            if options.merge_base:
                report['merge'] = merge(questions,options.merge_base,report,options.merged_output)
                report['merged_output'] = str(options.merged_output.resolve())
                atomic_json(output/'run_report.json',report)
            return report
        return baseline.main(['run',*rest],runner=runner,default_output=str(ROOT/'runs/p3_fusion'),publish=False)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(f'P3错误: {exc}',file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
