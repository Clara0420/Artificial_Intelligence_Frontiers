import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tableqa.evidence import validate_evidence
from tableqa.experiment import parse_ids, protect_output, merge
from tableqa.retrieval import select_context
from tableqa.schema import AnswerError, structure_from_grid
from tableqa.xlsxio import Question, write_submission, read_rows
from tableqa.pipeline import solve, Config, run
from tableqa.documents import Document

Q = Question('26','x.pdf','extract','1年以内账款，期末与期初余额是多少？','应收账款表','json_array')

def good():
    return {'answer':['10','20'],'evidence':[
        {'page':1,'table_id':'visual','row_label':'1年以内','column_header':h,'value':v}
        for h,v in zip(['期末余额','期初余额'],['10','20'])]}

class Client:
    def __init__(self, outputs):
        self.outputs=iter(outputs); self.request_count=0; self.usage={}; self.messages=[]
    def complete(self,messages,*args,**kwargs):
        self.request_count+=1; self.messages.append(list(messages))
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(next(self.outputs),ensure_ascii=False)}}]}

class Tests(unittest.TestCase):
    def test_contract(self):
        validate_evidence(json.dumps(good()),Q)
        for data in [{'answer':10}]:
            with self.assertRaises(AnswerError):validate_evidence(json.dumps(data),Q)
        d=good(); d['evidence'].reverse()
        self.assertTrue(validate_evidence(json.dumps(d),Q))
        d=good();d['evidence'][0]['value']='99'
        self.assertTrue(validate_evidence(json.dumps(d),Q))
        validate_evidence('{"answer":""}',Q)
    def test_scalar(self):
        q=Question('1','x','extract','某值','','number')
        self.assertEqual(validate_evidence('{"answer":10}',q),[])
        d=good(); d['answer']='10';d['evidence']=d['evidence'][:1]
        validate_evidence(json.dumps(d),q)
    def test_retrieval(self):
        tables=[{'table_id':'p1_t1','page':1,'notes':'单位元','html':'<table>应收账款 1年以内 期末 期初 10 20</table>'},
                {'table_id':'p1_t2','page':1,'notes':'','html':'其他项目'}]
        context,audit=select_context(Q,tables,'FULL')
        self.assertEqual(audit['mode'],'ranked_tables');self.assertIn('单位元',context)
        self.assertIn('期末 期初',context)
        _,audit=select_context(Q,[tables[0],dict(tables[0],table_id='p2_t1')],'FULL')
        self.assertEqual(audit['mode'],'full_document')
        q=Question('1','x','thinking','求和','','number')
        self.assertEqual(select_context(q,tables,'FULL')[1]['mode'],'full_document')
    def test_repair_and_recovery(self):
        with tempfile.TemporaryDirectory() as tmp, patch('tableqa.pipeline.image_content',return_value=[]), patch('tableqa.pipeline.visual_input_audit',return_value={}):
            client=Client([{'answer':10},good()])
            r=solve(Q,Document('sha',(Path('render/page.jpg'),)),Config(repair_attempts=0),client,Path(tmp),context='P1')
            self.assertEqual(r['status'],'ok');self.assertEqual(r['generation_mode'],'array')
            self.assertEqual(client.request_count,2)
            self.assertEqual(len(list((Path(tmp)/'responses').glob('*.json'))),2)
    def test_ids_and_safe_merge(self):
        q2=Question('2','x','extract','数值','','number');qs=[Q,q2]
        self.assertEqual(parse_ids('26',qs),{'26'})
        with self.assertRaises(ValueError):parse_ids('999',qs)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/'base.xlsx';out=root/'new.xlsx'
            write_submission(base,qs,{'26':'["1","2"]','2':'3'})
            report={'results':[{'id':'26','status':'ok','answer':'["10","20"]'},
                               {'id':'2','status':'failed','answer':''}]}
            result=merge(qs,base,report,out)
            self.assertEqual(result['rows'],2)
            self.assertEqual(read_rows(out,{'id','answer'})[1]['answer'],'3')
            with self.assertRaises(ValueError):merge(qs,base,report,out)
            with self.assertRaises(ValueError):protect_output(root)
    def test_subset_run(self):
        qs=[Q,Question('2','x','extract','数值','','number')]
        with tempfile.TemporaryDirectory() as tmp, patch('tableqa.pipeline.source_path',return_value=Path('x')), patch('tableqa.pipeline.prepare_document',return_value=Document('sha',(Path('render/page.jpg'),))), patch('tableqa.pipeline.image_content',return_value=[]), patch('tableqa.pipeline.visual_input_audit',return_value={}):
            client=Client([good()])
            report=run(qs,Path('.'),Path(tmp),Config(),client,ids={'26'},contexts={'26':'TARGET'},progress=lambda *a:None)
            self.assertEqual(report['selected_questions'],1)
            self.assertEqual(report['submission_check']['rows'],2)
            self.assertEqual(client.request_count,1)
            self.assertIn('TARGET',str(client.messages))
    def test_grid_diagnostic(self):
        with self.assertRaisesRegex(AnswerError,'实际行数=1'):
            structure_from_grid({'row_count':2,'col_count':1,'grid':[['a']],'texts':{'a':'x'}})
        with self.assertRaisesRegex(AnswerError,'texts 必须为对象'):
            structure_from_grid({'row_count':1,'col_count':1,'grid':[['a']],'texts':[]})

if __name__=='__main__': unittest.main()
