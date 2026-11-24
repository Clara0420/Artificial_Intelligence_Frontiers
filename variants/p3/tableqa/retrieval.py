"""Question-specific table ranking, retaining complete tables and fallback pages."""
import json
import re
from pathlib import Path
from .evidence import GUIDANCE, guidance_for, expected_headers, task_kind


def norm(text):
    return re.sub(r'\s+', '', text).lower()


def tokens(text):
    parts = re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]+', text.lower())
    return set(t for p in parts for t in ([p] if len(p) <= 3 or p.isascii()
               else [p[i:i+2] for i in range(len(p)-1)]))


def index_document(directory):
    tables = []
    for page_dir in sorted(Path(directory).glob('page-*')):
        page = int(page_dir.name.split('-')[-1])
        for path in sorted(page_dir.glob('*_res.json')):
            data = json.loads(path.read_text())
            blocks = data.get('parsing_res_list', [])
            # Block order preserves nearby captions, units and footnotes.
            surroundings = '\n'.join(str(b.get('block_content', '')) for b in blocks
                                     if b.get('block_label') != 'table')
            for i, table in enumerate(data.get('table_res_list', []), 1):
                html = table.get('pred_html', '')
                if not isinstance(html, str) or not html.strip():
                    continue
                tables.append({'table_id':f'p{page}_t{len(tables)+1}', 'page':page,
                               'html':html, 'notes':surroundings,
                               'source':str(path), 'width':data.get('width'),
                               'height':data.get('height')})
    return tables


def select_context(q, tables, fallback, evidence_priority="visual_primary"):
    query = tokens(q.question + ' ' + q.table_hint)
    ranked = []
    for table in tables:
        text = norm(re.sub('<[^>]+>', ' ', table['html']))
        score = sum(1 for t in query if t in text)
        if q.table_hint and norm(q.table_hint) in norm(table['notes'] + text):
            score += 5
        headers = expected_headers(q)
        score += sum(3 for h in headers if h in text)
        anchors = re.findall(r'[“\"]([^”\"]+)[”\"]|((?:\d+年|\d+个月)(?:以内|以上))', q.question)
        score += sum(8 for pair in anchors if norm(pair[0] or pair[1]) in text)
        ranked.append((score, table))
    ranked.sort(key=lambda pair:pair[0], reverse=True)
    # Never narrow structure, comparisons or calculations; ambiguous retrieval falls back.
    broad = q.question_type != 'extract' or task_kind(q) != 'direct' or any(x in q.question for x in
              ('全部','所有','最大','最小','合计','求和','总计','排名','排序','是否'))
    uncertain = not ranked or ranked[0][0] < 3 or (len(ranked)>1 and ranked[0][0]-ranked[1][0]<2)
    if broad or uncertain:
        context = fallback
        mode = 'full_document'
        chosen = [t for _,t in ranked]
    else:
        # Keep top two whole tables, including all headers, rows, units and page notes.
        chosen = [t for _, t in ranked[:2]]
        context = '\n\n'.join(f"【page={t['page']} table_id={t['table_id']}】\n"
                              + t['notes'] + '\n' + t['html'] for t in chosen)
        mode = 'ranked_tables'
    lead = ('先用 OCR/表格关系定位和读取候选单元格，再用原图/裁剪核验疑点；冲突时回看对应局部。\n'
            if evidence_priority == "ocr_primary" else
            'OCR仅为候选证据，必须结合所有原图核对；候选未含目标时回到原图。\n')
    prompt = lead + guidance_for(evidence_priority) + '\n' + context
    audit = {'mode':mode,'selected_tables':[t['table_id'] for t in chosen],
             'scores':[{'table_id':t['table_id'],'score':s} for s,t in ranked],
             'expected_headers':expected_headers(q), 'characters':len(prompt)}
    return prompt, audit
