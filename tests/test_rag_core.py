from io import BytesIO
import math
import pytest
from opslens.rag.text import extract, split_pages, tokenize
from opslens.rag.ranking import bm25, rrf
from opslens.rag.provider import ModelBlocked, QuotaGuard, DashScopeProvider


def test_chunks_are_exact_source_spans_and_cover_long_unicode():
    text="# 标题\n\nworker 停止，任务等待。\n\n"+("中文正文和 Redis 地址检查。"*150)
    pages=extract("manual.md",text.encode()).pages
    chunks=split_pages(pages)
    assert len(chunks)>2
    assert all(c["content"]==pages[c["page"]-1][c["start"]:c["end"]] for c in chunks)
    assert all(0<len(c["content"])<=800 for c in chunks)
    assert all(c["line_start"]>=1 and c["line_end"]>=c["line_start"] for c in chunks)
    covered=set()
    for c in chunks: covered.update(range(c["start"],c["end"]))
    assert set(range(len(text)))<=covered


def test_parser_rejects_empty_unknown_bad_pdf_or_utf8():
    for name,raw in (("x.md",b"  "),("x.html",b"hello"),("x.pdf",b"not pdf"),("x.txt",b"\xff")):
        with pytest.raises(ValueError):
            extract(name,raw)


def test_terms_bm25_and_no_match():
    docs=[{"id":"worker","text":"worker 停止 后台任务一直等待处理"},{"id":"db","text":"数据库 PostgreSQL 服务连接失败"},{"id":"noise","text":"用户登录和页面导航"}]
    result=bm25("worker 任务等待",docs)
    assert result[0]["id"]=="worker" and result[0]["score"]>0
    assert bm25("nonexistentxyz",docs)==[]
    assert "worker" in tokenize("WORKER")
    assert "数据" in tokenize("数据库")


def test_rrf_deduplicates_and_keeps_two_route_provenance():
    result=rrf([{"id":"a","score":8},{"id":"b","score":4}],[{"id":"b","score":.9},{"id":"c","score":.8}])
    assert [r["id"] for r in result]==["b","a","c"]
    assert result[0]["routes"]==["bm25","vector"]
    assert math.isclose(result[0]["rrf_score"],1/62+1/61)
    assert len({r["id"] for r in result})==len(result)


def test_no_network_before_free_quota_is_confirmed(tmp_path):
    calls=[]
    provider=DashScopeProvider(key="unit-test-key",guard=QuotaGuard(tmp_path/"missing.json","unit-test-key"),transport=lambda *a,**k:calls.append((a,k)))
    with pytest.raises(ModelBlocked):
        provider.embed(["hello"])
    assert calls==[]


def test_pdf_page_source_extraction():
    # A tiny valid text PDF built as a deterministic fixture, not a published artifact.
    stream=b"BT /F1 12 Tf 20 100 Td (worker stopped pending jobs) Tj ET"
    objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",b"<< /Length "+str(len(stream)).encode()+b" >>\nstream\n"+stream+b"\nendstream"]
    raw=b"%PDF-1.4\n"; offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(raw));raw+=str(i).encode()+b" 0 obj\n"+obj+b"\nendobj\n"
    xref=len(raw);raw+=b"xref\n0 6\n0000000000 65535 f \n"
    raw+=b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets[1:])
    raw+=b"trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n"+str(xref).encode()+b"\n%%EOF"
    result=extract("guide.pdf",raw)
    assert "worker stopped" in result.pages[0]
    assert split_pages(result.pages)[0]["page"]==1
