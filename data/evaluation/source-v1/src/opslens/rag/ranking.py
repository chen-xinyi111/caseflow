import math
from collections import Counter
from opslens.rag.text import tokenize

PARAMETERS = {"chunk_max_chars":800,"chunk_overlap":120,"bm25_k1":1.2,"bm25_b":.75,"rrf_k":60,"recall_per_route":20,"rerank_max":20,"evidence_max":6,"dimensions":1024,"tokenizer":"latin-identifiers+cjk-unigram-bigram-v1"}


def bm25(query, docs, limit=20):
    query_terms = set(tokenize(query))
    terms = [Counter(tokenize(doc["text"])) for doc in docs]
    lengths = [sum(term.values()) for term in terms]
    average = sum(lengths)/len(lengths) if lengths else 0
    if not query_terms or not average:
        return []
    frequency = {word:sum(word in term for term in terms) for word in query_terms}
    rows = []
    for doc, term, length in zip(docs, terms, lengths):
        score = 0.
        for word in query_terms:
            tf = term.get(word,0)
            if tf:
                idf = math.log(1 + (len(docs)-frequency[word]+.5)/(frequency[word]+.5))
                score += idf*tf*2.2/(tf+1.2*(.25+.75*length/average))
        if score > 0:
            rows.append({"id":str(doc["id"]),"score":score})
    return sorted(rows,key=lambda row:(-row["score"],row["id"]))[:limit]


def rrf(keyword, vector, limit=20):
    result = {}
    for route, rows in (("bm25",keyword),("vector",vector)):
        for rank, row in enumerate(rows,1):
            value = result.setdefault(str(row["id"]),{"id":str(row["id"]),"rrf_score":0.,"routes":[],"bm25_score":None,"vector_score":None})
            if route in value["routes"]:
                continue
            value["routes"].append(route)
            value["rrf_score"] += 1/(60+rank)
            value[route+"_score"] = row["score"]
    return sorted(result.values(),key=lambda row:(-row["rrf_score"],row["id"]))[:limit]
