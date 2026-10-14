"""Scan all submitted text locally. Extracted evidence retains source line ranges."""
import hashlib
import re
from pathlib import Path
from opslens.external import sanitize
MAX_FILE=5*1024*1024
MAX_CONTEXT=32*1024
EXTENSIONS={'.log','.txt','.json','.jsonl'}
ERROR=re.compile(r'\b(?:error|exception|traceback|caused by|fatal|failed|refused|timeout|timed out|cannot|unreachable|eaddrinuse|permission denied|not found|syntaxerror)\b|[A-Za-z]+(?:Error|Exception)\b|失败|拒绝|异常',re.I)

def clean(text,extra_secrets=()):
    from opslens.security import redact
    value=sanitize(redact(text,extra_secrets))
    return value.replace('\r\n','\n').replace('\r','\n')

def decode_file(name,raw):
    if Path(name).suffix.lower() not in EXTENSIONS:raise ValueError('仅支持 .log、.txt、.json、.jsonl 文本文件')
    if not raw or len(raw)>MAX_FILE:raise ValueError('文件为空或超过5MiB')
    try:value=raw.decode('utf-8-sig')
    except UnicodeError:raise ValueError('请将文件转换为UTF-8编码后上传') from None
    if '\x00' in value:raise ValueError('不支持二进制文件')
    return value

def summarize(text):
    lines=text.splitlines() or ['']
    groups={}
    for n,line in enumerate(lines,1):
        if not ERROR.search(line) or re.search(r'\b(?:0 errors?|no errors?)\b',line,re.I):continue
        signature=re.sub(r'\b\d+(?:[.:/-]\d+)*\b','<n>',line.strip())[:240]
        group=groups.setdefault(signature,{'signature':signature,'count':0,'first_line':n,'last_line':n})
        group['count']+=1;group['last_line']=n
    urls=list(dict.fromkeys(re.findall(r'https://[^\s<>"\)\]]+',text)))[:20]
    return {'lines':len(lines),'bytes':len(text.encode()),'groups':sorted(groups.values(),key=lambda g:(-g['count'],g['first_line']))[:100],
            'message':'发现报错线索；不能仅凭关键词确定原因' if groups else '没有识别到明确报错，请补充现象或选择相关日志', 'links':urls}

def auto_ranges(source,query):
    lines=source['content'].splitlines() or ['']
    if source['kind'] in ('document','official_doc','history'):
        from opslens.rag.ranking import bm25
        chunks=[{'id':str(n),'text':'\n'.join(lines[n:n+12])} for n in range(0,len(lines),10)]
        hits=bm25(query,chunks,3)
        ranges=[(int(h['id'])+1,min(len(lines),int(h['id'])+12)) for h in hits]
        return ranges or [(1,min(len(lines),12))]
    groups=source['summary']['groups']
    ranges=[]
    for g in groups[:12]:
        n=g['last_line'];end=min(len(lines),n+(30 if 'traceback' in g['signature'].lower() else 4))
        ranges.append((max(1,n-4),end))
    if not ranges:ranges=[(max(1,len(lines)-19),len(lines))] if source['kind']=='check_result' else [(1,min(len(lines),20))]
    merged=[]
    for a,b in sorted(ranges):
        if merged and a<=merged[-1][1]+1:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return merged

def excerpt(raw,budget):
    if len(raw.encode())<=budget:return raw
    match=ERROR.search(raw);offset=max(0,match.start()-100) if match else 0
    return raw[offset:].encode()[:budget].decode('utf-8',errors='ignore')

def segments(sources,ranges,query):
    # Reserve one excerpt per source before expanding any large source.
    result=[];explicit={};candidates=[]
    for r in ranges:explicit.setdefault(str(r.source_id),[]).append((r.start_line,r.end_line))
    if not set(explicit)<= {str(s['id']) for s in sources}:raise ValueError('行范围不属于选中资料')
    for source in sorted(sources,key=lambda s:s['kind']!='check_result'):
        lines=source['content'].splitlines() or ['']
        chosen=explicit.get(str(source['id'])) or auto_ranges(source,query)
        entries=[]
        for a,b in chosen:
            if not 1<=a<=b<=len(lines):raise ValueError('资料行范围无效')
            raw='\n'.join(lines[a-1:b])
            entries.append({'id':'S'+str(source['id'])+':'+str(a)+'-'+str(b),'source_id':str(source['id']),
                'name':source['name'],'kind':source['kind'],'version':source['version'],'start_line':a,'end_line':b,
                'content':raw,'omitted_characters':0,'provenance':source['provenance']})
        if not entries:raise ValueError('选中资料没有可用片段，请调整范围')
        entries.sort(key=lambda e:not bool(ERROR.search(e['content'])))
        candidates.append(entries)
    if not candidates:return []
    remaining=24000;share=min(8000,max(256,remaining//len(candidates)-500))
    def append(entry,budget):
        nonlocal remaining
        raw=entry['content'];piece=excerpt(raw,budget)
        if not piece:raise ValueError('选中片段没有可用文本，请减少选择')
        result.append(entry|{'content':piece,'omitted_characters':len(raw)-len(piece)})
        remaining-=len(piece.encode())+500
    for entries in candidates:append(entries[0],share)
    for index in range(1,max(map(len,candidates))):
        for entries in candidates:
            if index<len(entries) and remaining>=756:append(entries[index],min(8000,remaining-500))
    return result


def fit_context(context,size,limit=MAX_CONTEXT-1024):
    # Keep a correction margin and never remove the sole excerpt of a source.
    pieces=context['sources']
    while size()>limit:
        counts={p['source_id']:sum(q['source_id']==p['source_id'] for q in pieces) for p in pieces}
        extras=[p for p in pieces if counts[p['source_id']]>1]
        if extras:
            pieces.remove(extras[-1]);continue
        largest=max(pieces,key=lambda p:len(p['content'].encode()))
        length=len(largest['content'].encode())
        if length<=256:raise ValueError('所选资料与续轮信息无法容纳于32KiB，请缩短描述或减少资料选择')
        before=largest['content'];largest['content']=excerpt(before,max(256,length-512))
        largest['omitted_characters']+=len(before)-len(largest['content'])
    return pieces

def sha(text):return hashlib.sha256(text.encode()).hexdigest()
