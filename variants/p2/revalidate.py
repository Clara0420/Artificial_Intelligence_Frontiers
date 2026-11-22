#!/usr/bin/env python3
"""Revalidate only last saved responses for failed IDs; never call a model."""
import argparse
import hashlib
import json
from pathlib import Path
from tableqa.boundaries import validate_scope, project_explicit_scope
from tableqa.documents import document_info, source_path
from tableqa.evidence import validate_evidence
from tableqa.pipeline import atomic_json
from tableqa.recovery import normalize_recovery
from tableqa.schema import normalize_response, json_loads
from tableqa.xlsxio import read_questions


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--tests',type=Path,default=Path('data/tests.xlsx'))
    p.add_argument('--files',type=Path,default=Path('data/files'))
    p.add_argument('--dataset-fixes',type=Path,default=Path('configs/downloaded_data_fixes.json'))
    a=p.parse_args()
    if a.output_dir.exists():raise ValueError('输出目录已存在，禁止覆盖')
    qs={str(q.id):q for q in read_questions(a.tests,fixes=json.loads(a.dataset_fixes.read_text()))}
    original=json.loads((a.run_dir/'run_report.json').read_text())
    results=[];page_counts={}
    for old in original['results']:
        if old['status']!='failed':continue
        key=str(old['id']);q=qs[key]
        r={'id':q.id,'question_type':q.question_type,'status':'failed','answer':'',
           'original_error':old.get('error'),'offline_revalidation':True}
        try:
            if q.file_name not in page_counts:
                page_counts[q.file_name]=document_info(source_path(a.files,q.file_name))['pages']
            paths=list((a.run_dir/'responses').glob(old['fingerprint']+'-*.json'))
            if not paths:raise ValueError('无保存响应')
            # Last response only; do not cherry-pick earlier attempts to maximize pass rate.
            path=max(paths,key=lambda p:(json.loads(p.read_text()).get('created',0),p.stat().st_mtime_ns,p.name))
            r['source_response']=str(path.resolve())
            r['source_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            choice=json.loads(path.read_text())['choices'][0]
            if choice.get('finish_reason')!='stop':raise ValueError('响应未完整结束')
            raw=choice['message']['content']
            r['evidence_warnings']=validate_evidence(raw,q,page_counts[q.file_name])
            payload=json_loads(raw)
            value=payload.get('answer') if isinstance(payload,dict) else None
            if q.question_type=='structure' and isinstance(value,dict) and 'grid' in value:
                answer=normalize_recovery(raw,q,'grid')
            else:answer=normalize_response(raw,q.question_type,q.answer_format)
            answer, r["scope_removed_cells"] = project_explicit_scope(answer,q)
            validate_scope(answer,q)
            r.update(answer=answer,status='ok' if answer else 'abstained')
        except (ValueError,KeyError,TypeError,IndexError,OSError) as exc:
            r['error']=str(exc)
        results.append(r)
    from collections import Counter
    report={'api_requests_this_run':0,'source_run':str(a.run_dir.resolve()),
            'note':'仅最后保存响应的格式及范围校验，不证明内容正确；有证据警告的答案需复核',
            'statuses':dict(Counter(r['status'] for r in results)), 'results':results}
    a.output_dir.mkdir(parents=True,exist_ok=False)
    atomic_json(a.output_dir/'run_report.json',report)
    (a.output_dir/'remaining_ids.txt').write_text(','.join(str(r['id']) for r in results if r['status']!='ok')+'\n')
    atomic_json(a.output_dir/'validator_sha256.json',{
        str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.rglob('*.py'))})
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
