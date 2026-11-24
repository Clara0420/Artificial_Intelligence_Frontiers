import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pymupdf as fitz
from PIL import Image
from tableqa.crops import crop_table, choose_tables
from tableqa.documents import Document,image_content
from tableqa.pipeline import fingerprint,Config,solve,run
from tableqa.boundaries import project_explicit_scope,validate_scope
from tableqa.schema import normalize_response,AnswerError
from tableqa.xlsxio import Question
from tableqa.evidence import validate_evidence

Q=Question('1','x.pdf','extract','提取某值','','number')
class Tests(unittest.TestCase):
    def test_raster_mapping_no_upscale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'a.png';im=Image.new('RGB',(1000,500),'white')
            im.paste('red',(200,100,600,300));im.save(p)
            t={'mapping_safe':True,'width':1000,'height':500,'bbox':[200,100,600,300],'page':1,'table_id':'p1_t1'}
            result=crop_table(p,'hash',t,root/'crops',padding=0,vertical_padding=0)
            self.assertEqual(result['size'],[400,200]);self.assertEqual(result['origin'],'original_raster')
            with Image.open(result['path']) as crop:self.assertGreater(crop.getpixel((200,100))[0],240)
            self.assertEqual(crop_table(p,'hash',t,root/'crops',padding=0,vertical_padding=0)['sha256'],result['sha256'])
            with self.assertRaises(ValueError):crop_table(p,'hash',{**t,'height':1000},root/'crops')
            with self.assertRaises(ValueError):crop_table(p,'hash',{**t,'mapping_safe':False},root/'crops')
    def test_pdf_resolution_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'a.pdf'
            with fitz.open() as pdf:
                page=pdf.new_page(width=600,height=800);page.draw_rect(fitz.Rect(100,100,300,300),color=(1,0,0),fill=(1,0,0));pdf.save(p)
            t={'mapping_safe':True,'width':1200,'height':1600,'bbox':[200,200,600,600],'page':1,'table_id':'t'}
            result=crop_table(p,'hash',t,root/'crops',dpi=300,padding=0,vertical_padding=0,max_pixels=1000000)
            self.assertGreater(result['size'][0],800)
            self.assertLessEqual(result['size'][0]*result['size'][1],1000000)
    def test_original_pages_and_crop_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'a.jpg';Image.new('RGB',(20,20)).save(p)
            crop={'path':str(p),'sha256':'a','page':1,'label':'补充局部图，非新增页'}
            doc=Document('hash',(p,), (crop,))
            self.assertEqual(len(doc.images),1)
            content=image_content(doc,1000)
            self.assertEqual(sum(c['type']=='image_url' for c in content),2)
            self.assertIn('第 1 页',content[0]['text']);self.assertIn('非新增页',content[2]['text'])
            self.assertNotEqual(fingerprint(Q,doc,Config()),fingerprint(Q,Document('hash',(p,),({**crop,'sha256':'b'},)),Config()))
    def test_scope_projection(self):
        q=Question('1','x','structure','恢复前1行和第一列','','json')
        cells=[{'text':str(r)+str(c),'row':r,'col':c,'rowspan':1,'colspan':1} for r in range(2) for c in range(2)]
        answer=json.dumps({'row_count':2,'col_count':2,'cells':cells})
        result,removed=project_explicit_scope(answer,q)
        validate_scope(result,q);self.assertEqual(len(removed),1)
        self.assertEqual(json.loads(result)['row_count'],2)
        q=Question('1','x','structure','恢复表头行','','json')
        self.assertEqual(project_explicit_scope(answer,q),(answer,[]))
    def test_relaxed_boundary(self):
        self.assertEqual(normalize_response('["1","", "2"]','extract','json_array'),'["1","","2"]')
        self.assertEqual(normalize_response('{"answer":["1,250"]}','extract','number'),'1250')
        with self.assertRaises(AnswerError):normalize_response('{"answer":[1,2]}','extract','number')
        self.assertEqual(validate_evidence('{"answer":4}',Q),[])
    def test_crops_in_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'a.jpg';Image.new('RGB',(20,20)).save(p)
            doc=Document('hash',(p,),({'path':str(p),'sha256':'a','label':'crop'},))
            class Client:
                def __init__(self):self.calls=[]
                def complete(self,messages,*args,**kwargs):
                    self.calls.append(messages)
                    raw='{"answer":[1,2]}' if len(self.calls)==1 else '{"answer":["3"]}'
                    return {'choices':[{'finish_reason':'stop','message':{'content':raw}}]}
            client=Client();result=solve(Q,doc,Config(repair_attempts=0),client,Path(tmp))
            self.assertEqual(result['answer'],'3')
            self.assertEqual(len(client.calls),2)
            for messages in client.calls:
                self.assertEqual(sum(x['type']=='image_url' for x in messages[0]['content']),2)

class MoreTests(unittest.TestCase):
    def test_native_pdf_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'a.pdf'
            with fitz.open() as pdf:
                page=pdf.new_page(width=600,height=800)
                for i in range(6):page.insert_text((120+10*i,600),f'Column {i}',rotate=90,fontsize=8)
                pdf.save(p)
            t={'mapping_safe':True,'width':600,'height':800,'bbox':[100,100,200,700],'page':1,'table_id':'t'}
            crop=crop_table(p,'hash',t,root/'crops',padding=0,vertical_padding=0)
            self.assertEqual(crop['rotation_ccw'],-90)
            self.assertGreater(crop['size'][0],crop['size'][1])
    def test_run_routes_supplements(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'a.jpg';Image.new('RGB',(20,20)).save(p)
            class Client:
                request_count=0
                usage={}
                def complete(self,messages,*args,**kwargs):
                    self.request_count+=1
                    assert sum(x['type']=='image_url' for x in messages[0]['content'])==2
                    return {'choices':[{'finish_reason':'stop','message':{'content':'{"answer":3}'}}]}
            with patch('tableqa.pipeline.source_path',return_value=p), patch('tableqa.pipeline.prepare_document',return_value=Document('sha',(p,))):
                report=run([Q],Path(tmp),Path(tmp)/'out',Config(),Client(),progress=lambda *a:None,
                    supplements={'1':[{'path':str(p),'sha256':'x','label':'crop'}]})
            self.assertEqual(report['statuses'],{'ok':1})

if __name__=='__main__':unittest.main()
