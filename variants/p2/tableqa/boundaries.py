"""Question-aware structural scope; never infer dimensions or repair missing cells."""
import re
from .schema import AnswerError, json_loads


def complete_request(q):
    text=re.sub(r'“[^”]*”|"[^"]*"','',q.question)
    if re.search(r'表头|前[\d一二两三四五六七八九十]+[行列]|第[\d一二两三四五六七八九十]+[行列]|局部',text):
        return False
    return bool(re.search(r'整个表|完整表格|全部表格',text))


def validate_scope(answer,q):
    if q.question_type!='structure' or not answer:
        return
    value=json_loads(answer)
    cells=value['cells']
    if complete_request(q):
        area=sum(c['rowspan']*c['colspan'] for c in cells)
        if area!=value['row_count']*value['col_count']:
            raise AnswerError('完整表格请求存在未覆盖位置；请依据原图补齐，不能自动填格')
        return
    text=re.sub(r'“[^”]*”|"[^"]*"','',q.question)
    numbers={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
    scopes={'行':set(),'列':set()}
    for prefix,n,axis in re.findall(r'(前|第)(\d+|[一二两三四五六七八九十])([行列])',text):
        n=int(n) if n.isdigit() else numbers[n]
        if n<1: raise AnswerError('请求范围必须为正整数')
        scopes[axis].update(range(n) if prefix=='前' else [n-1])
    if not any(scopes.values()):
        return  # Header depth must be determined visually, never assumed to be one row.
    # Only simple unions are supported; do not reinterpret intersection/range language.
    if re.search(r'交集|交叉|至|到|仅.*内',text):
        return
    for c in cells:
        rows=set(range(c['row'],c['row']+c['rowspan']))
        cols=set(range(c['col'],c['col']+c['colspan']))
        if not (rows & scopes['行'] or cols & scopes['列']):
            raise AnswerError(f"局部结构包含范围外单元格 ({c['row']},{c['col']})；保留完整表尺寸但只输出指定范围")


def project_explicit_scope(answer,q):
    """Remove extra cells only for an unambiguous simple row/column union."""
    if q.question_type!='structure' or not answer:
        return answer, []
    text=re.sub(r'“[^”]*”|"[^"]*"','',q.question)
    if re.search(r'交集|交叉|至|到|仅.*内|除了|除外|不含|不包括',text):
        return answer, []
    matches=re.findall(r'(前|第)(\d+|[一二两三四五六七八九十])([行列])',text)
    if not matches or '表头' in text:
        return answer, []
    # Reuse the same membership semantics as the strict validator, one cell at a time.
    value=json_loads(answer); kept=[]; removed=[]
    for cell in value['cells']:
        single={**value,'cells':[cell]}
        try:
            validate_scope(__import__('json').dumps(single),q)
            kept.append(cell)
        except AnswerError as exc:
            if '局部结构包含范围外单元格' not in str(exc):raise
            removed.append(cell)
    value['cells']=kept
    from .schema import compact_json, validate_structure
    return compact_json(validate_structure(value)), removed
