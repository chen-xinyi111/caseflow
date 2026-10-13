import math,statistics
def retrieval_score(case,hits):
    sources=[h['document']['source'] for h in hits[:6]];gold=set(case['relevant_sources'])
    rank=next((i for i,s in enumerate(sources,1) if s in gold),None)
    return {'hit_at_6':int(rank is not None),'mrr_at_6':1/rank if rank else 0,'recall_at_6':len(set(sources)&gold)/len(gold),'expected_rank':rank}
def investigation_score(case,report,refs):
    report=report or {};by_id={r['id']:r for r in refs};valid=0;total=0;sources=set();kinds=set()
    for finding in report.get('findings',[]):
        for q in finding.get('references',[]):
            total+=1;r=by_id.get(q.get('id'));quote=q.get('quote','')
            if r and len(quote)>=2 and quote in r['content']:
                valid+=1;sources.add(r['source']);kinds.add(r['kind'])
    refusal=report.get('assessment')=='insufficient' and report.get('root_cause') is None and bool(report.get('missing_information'))
    root=(report.get('root_cause') or '').lower()
    matched=all(any(word.lower() in root for word in group) for group in case['root_groups'])
    correct=refusal if case['expected']=='insufficient' else report.get('assessment')=='supported' and matched and total>0 and valid==total and 'runtime' in kinds and case['evidence_source'] in sources
    return {'rule_correct':bool(correct),'appropriate_refusal':bool(refusal and case['expected']=='insufficient'),'refused':report.get('assessment')=='insufficient','root_terms_matched':matched if case['expected']=='supported' else None,'quote_valid':valid,'quote_total':total,'quote_match_rate':valid/total if total else None,'required_evidence_cited':case['evidence_source'] in sources if case['evidence_source'] else None,'semantic_support':'requires_review','reason':'规则匹配且必要原文存在；语义支持待人工复核' if correct else '回答未满足预声明根因/拒答/必要原文规则'}
def percentile(values,p):
    if not values:return None
    v=sorted(values);return v[max(0,math.ceil(p*len(v))-1)]
def summarize(rows):
    groups={}
    for row in rows:groups.setdefault((row['kind'],row['mode'],row['split']),[]).append(row)
    results=[]
    for (kind,mode,split),items in sorted(groups.items()):
        done=[r for r in items if r['status']=='completed'];scores=[r.get('score') or {} for r in done]
        correct=sum(bool(s.get('rule_correct',s.get('hit_at_6',False))) for s in scores)
        latency=[r['metrics']['elapsed_ms'] for r in done if isinstance((r.get('metrics') or {}).get('elapsed_ms'),(int,float))]
        tokens=[r['metrics']['actual_tokens'] for r in items if isinstance((r.get('metrics') or {}).get('actual_tokens'),int)]
        result={'kind':kind,'mode':mode,'split':split,'planned':len(items),'completed':len(done),'failed':sum(r['status']=='failed' for r in items),'blocked':sum(r['status']=='blocked' for r in items),'pending':sum(r['status'] in ('pending','running','interrupted') for r in items),'correct':correct,'success_rate':correct/len(items),'completed_success_rate':correct/len(done) if done else None,'latency_n':len(latency),'median_ms':statistics.median(latency) if latency else None,'p95_ms':percentile(latency,.95),'actual_tokens':sum(tokens),'token_rows':len(tokens),'request_count':sum((r.get('metrics') or {}).get('request_count',0) for r in items),'independent_groups':len({r.get('source_group') for r in items if r.get('source_group')})}
        if kind=='retrieval':
            for name in ('hit_at_6','mrr_at_6','recall_at_6'):result[name]=sum(s.get(name,0) for s in scores)/len(items)
        else:
            result.update(refusal_count=sum(s.get('refused',False) for s in scores),appropriate_refusal=sum(s.get('appropriate_refusal',False) for s in scores),quote_valid=sum(s.get('quote_valid',0) for s in scores),quote_total=sum(s.get('quote_total',0) for s in scores),semantic_review='pending')
        results.append(result)
    return results
