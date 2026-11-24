import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from tableqa.fusion import build_evidence
from tableqa.stages import Stages
from tableqa.pipeline import Config, solve, run
from tableqa.documents import Document
from tableqa.xlsxio import Question
from tableqa.cloud import StopRun

Q=Question('1','a.png','extract','提取收入','','number')
class Client:
    def __init__(self, responses):
        self.responses=iter(responses); self.messages=[]; self.request_count=0; self.usage={}
    def complete(self,messages,*args,**kwargs):
        self.messages.append(messages); self.request_count+=1
        value=next(self.responses)
        if isinstance(value,Exception): raise value
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}

class Tests(unittest.TestCase):
    def doc(self,root):
        p=root/'a.png';Image.new('RGB',(100,100),'white').save(p)
        return Document('sha',(p,))

    def test_review_blind_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc=self.doc(root)
            client=Client([{'answer':1},{'answer':2,'evidence':[{'page':1}]},
                           {'answer':2,'resolved':True,'evidence':[{'page':1,'row':'收入','value':'2'}]}])
            stages=Stages({'1':['ambiguous_location']},{},localization=False)
            result=solve(Q,doc,Config(),client,root,context='OCR_SECRET',stages=stages)
            self.assertEqual(result['answer'],'2'); self.assertTrue(result['review']['changed'])
            self.assertNotIn('OCR_SECRET',str(client.messages[1]))
            self.assertNotIn('候选A',str(client.messages[1]))
            client2=Client([])
            result2=solve(Q,doc,Config(),client2,root,context='OCR_SECRET',stages=Stages({'1':['ambiguous_location']},{},localization=False))
            self.assertEqual(result2['answer'],'2');self.assertEqual(client2.request_count,0)

    def test_review_error_keeps_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc=self.doc(root)
            result=solve(Q,doc,Config(),Client([{'answer':1},StopRun('budget')]),root,
                         stages=Stages({'1':['cross_page']},{},localization=False))
            self.assertEqual(result['answer'],'1');self.assertEqual(result['review']['status'],'error')

    def test_unresolved_keeps_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc=self.doc(root)
            result=solve(Q,doc,Config(),Client([{'answer':1},{'answer':2},{'answer':2,'resolved':False}]),root,
                         stages=Stages({'1':['cross_page']},{},localization=False))
            self.assertEqual(result['answer'],'1');self.assertEqual(result['review']['status'],'unresolved')

    def test_malformed_adjudication_preserves_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc=self.doc(root)
            result=solve(Q,doc,Config(),Client([{'answer':1},{'answer':2},[]]),root,
                         stages=Stages({'1':['cross_page']},{},localization=False))
            self.assertEqual(result['answer'],'1');self.assertEqual(result['review']['status'],'error')

    def test_review_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc=self.doc(root);client=Client([{'answer':1}])
            result=solve(Q,doc,Config(),client,root,stages=Stages({'1':['cross_page']},{},review_limit=0,localization=False))
            self.assertEqual(result['review']['status'],'budget_skipped');self.assertEqual(client.request_count,1)

    def test_visual_localization_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc=self.doc(root)
            stages=Stages({'1':['missing_crop']},{'a.png':str(doc.images[0])})
            out,audit=stages.locate(Q,doc,Config(),Client([{'boxes':[{'page':1,'bbox':[10,10,90,90]}]}]),root)
            self.assertEqual(len(out.supplements),1);self.assertEqual(out.images,doc.images)
            self.assertEqual(out.visual_mode,'page_plus_tables')
            bad=Stages({'1':['missing_crop']},{'a.png':str(doc.images[0])},refresh=True)
            with self.assertRaises(ValueError):
                bad.locate(Q,doc,Config(),Client([{'boxes':[{'page':1,'bbox':[0,0,1000,1000]}]}]),root)

    def test_complete_table_and_fallback(self):
        tables=[{'table_id':f't{i}','page':i+1,'notes':'单位万元','html':'<table><tr><th>收入</th></tr><tr><td>123</td></tr></table>'} for i in range(3)]
        q=Question('1','a','thinking','求所有收入合计','','number')
        context,audit,chosen=build_evidence(q,tables,'FULL')
        self.assertEqual(len(chosen),3); self.assertEqual(context.count('123'),3)
        self.assertIn('cross_page',audit['risks'])
        context,audit,_=build_evidence(q,tables,'FULL',max_chars=1)
        self.assertIn('FULL',context);self.assertEqual(audit['mode'],'full_document_fallback')
        self.assertEqual(build_evidence(q,tables,'FULL',mode='none')[0],'')

    def test_full_run_mock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.doc(root)
            stages=Stages({}, {'a.png':str(root/'a.png')},localization=False)
            report=run([Q],root,root/'out',Config(),Client([{'answer':7}]),stages=stages,progress=lambda *a:None)
            self.assertEqual(report['statuses'],{'ok':1})
            self.assertEqual(report['submission_check']['nonempty'],1)

if __name__=='__main__': unittest.main()
