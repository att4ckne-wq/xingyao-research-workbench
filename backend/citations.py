"""Deterministic paragraph citation projection shared by UI and exports."""
import hashlib,json,re
from urllib.parse import urlsplit,urlunsplit
from . import db,manuscript

def _identity(row):
    try:meta=json.loads(row.get('rationale') or '{}').get('metadata',{})
    except (ValueError,AttributeError):meta={}
    doi=str(meta.get('doi') or '').strip().lower()
    doi=re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)','',doi)
    if doi and re.fullmatch(r'10\.\d{4,9}/\S+',doi):key='doi:'+doi
    else:
        u=urlsplit(row['source_url'])
        key='url:'+urlunsplit((u.scheme.lower(),u.netloc.lower(),u.path,u.query,'')) if u.scheme in ('http','https') and u.netloc else 'record:'+row['id']
    return key,meta

def projection(project_id):
    did=manuscript.document_id(project_id); anchors=manuscript.anchors(project_id)
    sources=db.fetch_all("SELECT * FROM "+db.REFERENCE_SOURCE+" WHERE project_id=?",(project_id,))
    by_id={r['id']:r for r in sources}; decisions={}
    for d in db.fetch_all("SELECT * FROM decisions WHERE project_id=? AND target_type='citation' ORDER BY created_at,rowid",(project_id,)):decisions[d['target_id']]=d
    placements={ (r['anchor_id'],r['reference_id']):r for r in db.fetch_all("SELECT * FROM citation_placements WHERE project_id=?",(project_id,)) }
    for row in sources:
        d=decisions.get(row['id'])
        if row['anchor_id'] and d and d['action']=='accept':placements.setdefault((row['anchor_id'],row['id']),{'anchor_id':row['anchor_id'],'reference_id':row['id'],'action':'accept','accepted_at':d['created_at']})
    identities={r['id']:_identity(r) for r in sources}
    equivalent={}
    for rid,(key,meta) in identities.items():equivalent.setdefault(key,[]).append(rid)
    bibliography=[]; numbers={}; mapping={}; reference_mapping={}
    for anchor in anchors:
        rows=sorted((v for (aid,rid),v in placements.items() if aid==anchor['id'] and v['action']=='accept' and rid in by_id),key=lambda r:(r['accepted_at'],r['reference_id']))
        refs=[]; used=[]
        for place in rows:
            row=by_id[place['reference_id']];key,meta=identities[row['id']]
            if key not in numbers:
                numbers[key]=len(numbers)+1
                bibliography.append({'reference_key':'cite_'+hashlib.sha256(key.encode()).hexdigest()[:20], 'number':numbers[key], 'reference_ids':sorted(equivalent[key]),'anchor_id':anchor['id'],'asset_id':row['id'],'title':row['title'],'author':row.get('author'),'source_url':row['source_url'],'provider':row['provider'],'accessed_at':row['accessed_at'],**{k:v for k,v in meta.items() if k in ('doi','journal','year','verification_status')}})
            used.append(numbers[key]);refs.append(row['id'])
        mapping[anchor['id']]=sorted(set(used));reference_mapping[anchor['id']]=refs
    unplaced=[];unplaced_keys=set()
    for row in sorted(sources,key=lambda r:(r['accessed_at'],r['id'])):
        decision=decisions.get(row['id']);key,meta=identities[row['id']]
        if not decision or decision['action']!='accept' or key in numbers or key in unplaced_keys:continue
        unplaced_keys.add(key)
        unplaced.append({'reference_key':'cite_'+hashlib.sha256(key.encode()).hexdigest()[:20],'number':None,'unplaced':True,'reference_ids':sorted(equivalent[key]),'anchor_id':None,'asset_id':row['id'],'title':row['title'],'author':row.get('author'),'source_url':row['source_url'],'provider':row['provider'],'accessed_at':row['accessed_at'],**{k:v for k,v in meta.items() if k in ('doi','journal','year','verification_status')}})
    content={'document_id':did,'bibliography':bibliography,'unplaced_references':unplaced,'paragraph_citations':mapping,'paragraph_reference_ids':reference_mapping}
    content['version']=hashlib.sha256(json.dumps({'projection':content,'anchors':[(a['id'],a['text_hash']) for a in anchors]},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return content

def rendered(anchors,view):
    return [{**a,'text':a['text']+''.join('['+str(n)+']' for n in view['paragraph_citations'].get(a['id'],[]))} for a in anchors]
