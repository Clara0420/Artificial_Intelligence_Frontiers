#!/usr/bin/env python3
"""Merge only successful results into a complete base, never overwrite files."""
import argparse
import json
from pathlib import Path
from tableqa.experiment import merge, parse_ids
from tableqa.xlsxio import read_questions

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--tests',type=Path,default=Path('data/tests.xlsx'))
    p.add_argument('--dataset-fixes',type=Path,default=Path('configs/downloaded_data_fixes.json'))
    p.add_argument('--sheet')
    p.add_argument('--ids', help='仅合并指定题号，逗号分隔')
    p.add_argument('--fill-blanks-only', action='store_true', help='只填空答案，不替换已有答案')
    a=p.parse_args()
    questions=read_questions(a.tests,a.sheet,json.loads(a.dataset_fixes.read_text()))
    print(json.dumps(merge(questions,a.base,json.loads(a.report.read_text()),a.output,ids=parse_ids(a.ids,questions),fill_blanks_only=a.fill_blanks_only),ensure_ascii=False,indent=2))

if __name__=='__main__': main()
