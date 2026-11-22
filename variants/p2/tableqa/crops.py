"""Deterministic source-resolution table crops with verified coordinate mapping."""
import hashlib
import io
import json
import math
from pathlib import Path
import re
import pymupdf as fitz
from PIL import Image
from .evidence import task_kind


def attach_geometry(tables):
    cache={}
    for table in tables:
        source=table['source']
        if source not in cache:cache[source]=json.loads(Path(source).read_text())
        data=cache[source]
        pre=data.get('doc_preprocessor_res',{})
        settings=pre.get('model_settings',{})
        safe=(settings.get('use_doc_orientation_classify') is False
              and settings.get('use_doc_unwarping') is False and pre.get('angle') in (-1,0))
        compact=lambda s:re.sub(r'\s+','',s)
        matches=[b for b in data.get('parsing_res_list',[]) if b.get('block_label')=='table'
                 and compact(str(b.get('block_content','')))==compact(table['html'])]
        table['mapping_safe']=safe
        table['bbox']=matches[0].get('block_bbox') if len(matches)==1 else None
    return tables


def choose_tables(q,tables,audit,max_crops):
    if task_kind(q)=='visual' or max_crops==0:return []
    scores={item['table_id']:item['score'] for item in audit['scores']}
    ranked=sorted(tables,key=lambda t:scores.get(t['table_id'],0),reverse=True)
    if len(ranked)>1 and scores.get(ranked[0]['table_id'],0)<3:return []
    return ranked[:max_crops]


def crop_table(source,source_sha,table,output,*,dpi=300,max_side=3200,max_pixels=5760000,padding=.025):
    if not table.get('mapping_safe'):raise ValueError('OCR旋转/去畸变设置不明确，跳过裁剪')
    w,h=table.get('width'),table.get('height');box=table.get('bbox')
    if not isinstance(w,(int,float)) or not isinstance(h,(int,float)) or w<=0 or h<=0:
        raise ValueError('OCR尺寸无效')
    if not isinstance(box,list) or len(box)!=4 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in box):
        raise ValueError('无法唯一匹配表格坐标')
    x0,y0,x1,y1=box
    if not (0<=x0<x1<=w and 0<=y0<y1<=h):raise ValueError('OCR表格坐标越界')
    rect=[max(0,x0/w-padding),max(0,y0/h-padding),min(1,x1/w+padding),min(1,y1/h+padding)]
    settings={'source_sha':source_sha,'page':table['page'],'rect':rect,'dpi':dpi,
              'max_side':max_side,'max_pixels':max_pixels,'version':2}
    key=hashlib.sha256(json.dumps(settings,sort_keys=True).encode()).hexdigest()
    target=Path(output)/f'{key}.jpg'
    def verify_ratio(sw,sh):
        if abs((sw/sh)/(w/h)-1)>.02:raise ValueError('原图与OCR宽高比不一致，跳过裁剪')
    # Validate source mapping even on a cache hit.
    rotation=0
    if source.suffix.lower()=='.pdf':
        with fitz.open(source) as pdf:
            page=pdf[table['page']-1]
            if page.rotation:raise ValueError('PDF含旋转元数据，未验证坐标映射')
            sw,sh=page.rect.width,page.rect.height;verify_ratio(sw,sh)
            clip=fitz.Rect(rect[0]*sw,rect[1]*sh,rect[2]*sw,rect[3]*sh)
            directions=[tuple(round(v) for v in line.get('dir',(1,0)))
                        for block in page.get_text('dict',clip=clip,flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']
                        for line in block.get('lines',[])]
            from collections import Counter
            counts=Counter(directions)
            if len(directions)>=5:
                direction,count=counts.most_common(1)[0]
                if count/len(directions)>=.8:
                    rotation={(0,-1):-90,(0,1):90,(-1,0):180}.get(direction,0)
            scale=min(dpi/72,max_side/max(clip.width,clip.height),math.sqrt(max_pixels/(clip.width*clip.height)))
            if target.exists():
                with Image.open(target) as image:size=list(image.size)
            else:
                pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False,colorspace=fitz.csRGB)
                image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
                if rotation:image=image.rotate(rotation,expand=True)
                size=save(image,target,max_side,max_pixels)
        origin='pdf_render'
    else:
        with Image.open(source) as image:
            image.seek(table['page']-1)
            if image.getexif().get(274,1)!=1:raise ValueError('图像含EXIF旋转，未验证OCR映射')
            sw,sh=image.size;verify_ratio(sw,sh)
            if target.exists():
                with Image.open(target) as saved:size=list(saved.size)
            else:
                # Original raster pixels only; never manufacture detail by upscaling.
                box=(math.floor(rect[0]*sw),math.floor(rect[1]*sh),math.ceil(rect[2]*sw),math.ceil(rect[3]*sh))
                size=save(image.crop(box),target,max_side,max_pixels)
        origin='original_raster'
    return {'path':str(target.resolve()),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'page':table['page'],'table_id':table['table_id'],'bbox_ocr':box if origin=='pdf_render' else table['bbox'],
            'rotation_ccw':rotation,'ocr_size':[w,h],'normalized_crop':rect,'size':size,'origin':origin,
            'label':f"补充局部图：原文档第{table['page']}页，候选表{table['table_id']}（非新增页，补充图旋转{rotation}度）；完整表及周边，请结合原页核对。"}


def save(image,target,max_side,max_pixels):
    if image.mode!='RGB':
        rgba=image.convert('RGBA');background=Image.new('RGBA',image.size,'white');background.alpha_composite(rgba);image=background.convert('RGB')
    ratio=min(1,max_side/max(image.size),math.sqrt(max_pixels/(image.width*image.height)))
    if ratio<1:image=image.resize((max(1,int(image.width*ratio)),max(1,int(image.height*ratio))),Image.Resampling.LANCZOS)
    output=io.BytesIO();image.save(output,'JPEG',quality=95,optimize=True)
    if output.tell()>6*1024*1024:raise ValueError('裁剪JPEG超过6MiB')
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.tmp');temporary.write_bytes(output.getvalue());temporary.replace(target)
    return list(image.size)
