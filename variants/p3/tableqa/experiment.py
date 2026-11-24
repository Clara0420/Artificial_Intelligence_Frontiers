"""Isolated experiment outputs and ID-safe submission merging."""
import json
from pathlib import Path
from .pipeline import atomic_json
from .xlsxio import id_key, read_rows, validate_submission, write_submission


def parse_ids(value, questions):
    if value is None:
        return None
    selected = {s.strip() for s in value.split(',') if s.strip()}
    known = {id_key(q.id) for q in questions}
    if not selected or selected - known:
        raise ValueError(f'ids 为空或包含未知题号: {sorted(selected-known)}')
    return selected


def protect_output(output):
    output = Path(output).resolve()
    marker = output / 'p3_experiment.json'
    if output.exists() and any(output.iterdir()) and not marker.is_file():
        raise ValueError('输出目录非空且不属于 P3 实验；请选择新的目录')
    if marker.exists() and json.loads(marker.read_text()).get('variant') != 'p3':
        raise ValueError('实验目录标记无效')
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(marker, {'variant':'p3'})


def merge(questions, base, report, destination, *, ids=None, fill_blanks_only=False):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('合并目标已存在，禁止覆盖；请指定新文件名')
    validate_submission(questions, Path(base), validate_scopes=False)
    answers = {id_key(r['id']):'' if r['answer'] is None else str(r['answer'])
               for r in read_rows(Path(base), {'id','answer'})}
    known = {id_key(q.id) for q in questions}
    seen, changed = set(), []
    for result in report['results']:
        key = id_key(result['id'])
        if key not in known or key in seen:
            raise ValueError(f'补跑结果含未知或重复 id={key}')
        seen.add(key)
        if ids is not None and key not in ids:
            continue
        if fill_blanks_only and answers[key].strip():
            continue
        if result['status'] == 'ok' and result.get('answer'):
            if answers[key] != result['answer']:
                changed.append(key)
            answers[key] = result['answer']
    # Validate before publishing; a failed/abstained patch never erases the base answer.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp)/'candidate.xlsx'
        write_submission(candidate, questions, answers)
        check = validate_submission(questions, candidate)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as out:
            out.write(candidate.read_bytes())
    return {'changed_ids':changed,'preserved_failed_or_abstained':sum(
            r['status'] != 'ok' or not r.get('answer') for r in report['results']), **check}
