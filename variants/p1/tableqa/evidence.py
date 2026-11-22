"""Submission constraints are hard errors; model evidence is an audit signal."""
import re
from decimal import Decimal, InvalidOperation
from .schema import AnswerError, json_loads

GUIDANCE = '''按原题的格式、顺序、单位输出 answer。证据仅用于内部审计，不属于提交答案。
可以另给 evidence 数组：page 从1开始、table_id、row_label、column_header、value。
column_header 可以是完整路径字符串或字符串数组。value 记录原始证据，不必照抄最终答案。
直接取值时给对应单元格；计数时可给多个表头或项目；比较筛选时给全部比较对象及数值；
语言和外观判断可给可见文字或特征。一个答案可由多项证据支持，不要求数量一一对应。
数字去除千分位逗号；日期、金额、百分比和单位严格按题目要求，不擅自改变倍率或单位。
多结果输出 JSON 数组，真实空白项用空字符串并保留位置。无法确定的答案返回空字符串。
期末与期初余额按问题指定顺序输出，不得漏项。'''


def expected_headers(q):
    if q.answer_format.lower() != 'json_array':
        return []
    m = re.search(r'(期末|期初)\s*(?:与|和|及|、)\s*(期末|期初)\s*余额', q.question)
    return list(m.groups()) if m and m[1] != m[2] else []


def task_kind(q):
    text = q.question
    if re.search(r'列数|行数|数量|多少个|多少列|多少行|支持数|页数|一共有|共有', text):
        return 'count'
    if re.search(r'语言|是否|横向|纵向|颜色|蓝色|二维码|logo', text, re.I):
        return 'visual'
    if re.search(r'哪些|哪种|哪类|最高|最低|最大|最小|最多|最少|更高|更低|不低于|不高于|相同|排序', text):
        return 'comparison'
    return 'direct'


def equivalent(a, b):
    if isinstance(a, list) or isinstance(b, list):
        return isinstance(a, list) and isinstance(b, list) and len(a)==len(b) and all(equivalent(x,y) for x,y in zip(a,b))
    if isinstance(a, (dict, bool)) or isinstance(b, (dict, bool)):
        return False
    def number(x):
        text = str(x).strip()
        if re.fullmatch(r'\(\d+(?:,\d{3})*(?:\.\d+)?\)',text):
            text='-'+text[1:-1]
        if re.fullmatch(r'[+-]?\d{1,3}(?:[,，]\d{3})+(?:\.\d+)?',text):
            text=text.replace(',','').replace('，','')
        try:
            value=Decimal(text)
            return value if value.is_finite() else None
        except InvalidOperation:
            return None
    na,nb=number(a),number(b)
    if na is not None and nb is not None:
        return na==nb
    return isinstance(a,str) and isinstance(b,str) and ' '.join(a.split())==' '.join(b.split())


def validate_evidence(raw, q, pages=None):
    """Return warnings without treating model-written provenance as ground truth."""
    if q.question_type != 'extract':
        return []
    match=re.fullmatch(r"```(?:json)?\s*(.*?)\s*```",raw.strip(),flags=re.S|re.I)
    payload=json_loads(match.group(1) if match else raw)
    if not isinstance(payload,dict) or 'answer' not in payload:
        raise AnswerError('模型必须返回包含 "answer" 的 JSON 对象')
    answer=payload['answer']
    if answer in (None,''):
        return []
    if q.answer_format.lower()=='json_array':
        if isinstance(answer,str): answer=json_loads(answer)
        if not isinstance(answer,list): raise AnswerError('多结果必须输出 JSON 数组，不能包装单个标量')
    headers=expected_headers(q)
    if headers and (not isinstance(answer,list) or len(answer)!=len(headers)):
        raise AnswerError(f'答案数量错误：题目按顺序要求 {headers}，需要 {len(headers)} 项')
    evidence=payload.get('evidence')
    if not isinstance(evidence,list) or not evidence:
        return ['missing_evidence']
    warnings=[]
    values=answer if isinstance(answer,list) else [answer]
    kind=task_kind(q)
    for i,item in enumerate(evidence):
        if not isinstance(item,dict):
            warnings.append(f'evidence[{i}]:invalid_object'); continue
        page=item.get('page')
        if type(page) is not int or page<1 or (pages is not None and page>pages):
            warnings.append(f'evidence[{i}]:invalid_page')
        header=item.get('column_header')
        if isinstance(header,list) and all(isinstance(v,str) for v in header): header=' > '.join(header)
        if not isinstance(header,str) or not header.strip(): warnings.append(f'evidence[{i}]:missing_header')
        for field in ('table_id','row_label'):
            if not isinstance(item.get(field),str) or not item[field].strip(): warnings.append(f'evidence[{i}]:missing_{field}')
        if kind=='direct' and len(evidence)==len(values) and not equivalent(item.get('value'),values[i]):
            warnings.append(f'evidence[{i}]:value_difference')
        if headers and len(evidence)==len(headers) and (not isinstance(header,str) or headers[i] not in header):
            warnings.append(f'evidence[{i}]:order_unverified')
    return warnings
