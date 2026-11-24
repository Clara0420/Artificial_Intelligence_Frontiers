import json
from pathlib import Path
import tempfile
import unittest
from tableqa.evidence import validate_evidence, equivalent
from tableqa.schema import normalize_response, AnswerError
from tableqa.boundaries import validate_scope, complete_request
from tableqa.xlsxio import Question, write_submission, read_rows
from tableqa.experiment import merge
from tableqa.recovery import normalize_recovery

def q(text='某值',fmt='number',kind='extract'):
    return Question('1','x',kind,text,'',fmt)

class Boundaries(unittest.TestCase):
    def test_numeric(self):
        for raw,want in [('125,000','125000'),('(27,819)','-27819'),('(100)','-100')]:
            self.assertEqual(normalize_response(json.dumps({'answer':raw}),'extract','number'),want)
        self.assertTrue(equivalent(125000,'125,000'))
        self.assertTrue(equivalent('-27819','(27,819)'))
        self.assertFalse(equivalent('10%','10'))
        self.assertEqual(normalize_response('{"answer":["10"]}','extract','number'),'10')
        for value in ['85.4%','12元',['10','20']]:
            with self.assertRaises(AnswerError):normalize_response(json.dumps({'answer':value}),'extract','number')
    def test_preserve_semantics(self):
        for value in ['2026-11-22','85.4%','12万元','(100)','编号1,234A']:
            self.assertEqual(normalize_response(json.dumps({'answer':value}),'extract','string'),value)
        self.assertEqual(normalize_response('{"answer":["125,000","",null,"10%"]}','extract','json_array'),'["125000","","","10%"]')
        self.assertEqual(normalize_response('{"answer":[]}','extract','json_array'),'[]')
    def test_derived_evidence(self):
        e=[{'page':1,'table_id':'visual','row_label':'header','column_header':['A','B'],'value':['A','B']}]
        self.assertEqual(validate_evidence(json.dumps({'answer':2,'evidence':e}),q('主表列数')),[])
        e[0]['value']='English text'
        self.assertEqual(validate_evidence(json.dumps({'answer':'英语','evidence':e}),q('使用哪种语言','string')),[])
        self.assertEqual(validate_evidence('{"answer":"A","evidence":[{"page":1,"table_id":"x","row_label":"A","column_header":["值"],"value":4},{"page":1,"table_id":"x","row_label":"B","column_header":"值","value":2}]}',q('哪类更高','string')),[])
    def test_partial_scope_and_merge(self):
        question=q('整个表格中的前1行和第一列','json','structure')
        self.assertFalse(complete_request(question))
        cells=[{'text':'a','row':0,'col':0,'rowspan':2,'colspan':1},
               {'text':'b','row':0,'col':1,'rowspan':1,'colspan':1}]
        answer={'row_count':2,'col_count':2,'cells':cells}
        normalized=normalize_recovery(json.dumps({'answer':answer}),question,'grid')
        validate_scope(normalized,question)
        self.assertEqual(json.loads(normalized)['row_count'],2)
        cells.append({'text':'extra','row':1,'col':1,'rowspan':1,'colspan':1})
        with self.assertRaises(AnswerError):validate_scope(json.dumps(answer),question)
    def test_full_coverage(self):
        question=q('恢复整个表格','json','structure')
        answer={'row_count':2,'col_count':2,'cells':[{'text':'a','row':0,'col':0,'rowspan':1,'colspan':1}]}
        with self.assertRaises(AnswerError):validate_scope(json.dumps(answer),question)
        # Header depth cannot be derived from the wording; do not guess full coverage.
        validate_scope(json.dumps(answer),q('恢复表头行结构','json','structure'))
    def test_fill_blanks(self):
        qs=[q(),Question('2','x','extract','某值','','number')]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);write_submission(p/'base.xlsx',qs,{'1':'3'})
            report={'results':[{'id':'1','status':'ok','answer':'9'},{'id':'2','status':'ok','answer':'8'}]}
            merge(qs,p/'base.xlsx',report,p/'out.xlsx',ids={'1','2'},fill_blanks_only=True)
            rows=read_rows(p/'out.xlsx',{'id','answer'})
            self.assertEqual([r['answer'] for r in rows],['3','8'])
            with self.assertRaises(ValueError):merge(qs,p/'base.xlsx',report,p/'out.xlsx')


class ExistingBaseRepair(unittest.TestCase):
    def test_fix_invalid_scope_before_final_validation(self):
        question=q('恢复前1行和第一列','json','structure')
        cells=[{'text':str(r)+str(c),'row':r,'col':c,'rowspan':1,'colspan':1} for r in range(2) for c in range(2)]
        old={'row_count':2,'col_count':2,'cells':cells}
        fixed={**old,'cells':cells[:3]}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);write_submission(p/'base.xlsx',[question],{'1':json.dumps(old)})
            merge([question],p/'base.xlsx',{'results':[{'id':'1','status':'ok','answer':json.dumps(fixed)}]},p/'fixed.xlsx')
            with self.assertRaises(AnswerError):merge([question],p/'base.xlsx',{'results':[]},p/'bad.xlsx')
            self.assertFalse((p/'bad.xlsx').exists())

if __name__=="__main__":unittest.main()
