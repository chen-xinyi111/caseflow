import hashlib
import json
import math
import os
import re
import threading
import atexit
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
from opslens.config import ROOT

EMBED_MODEL = "text-embedding-v4"
RERANK_MODEL = "gte-rerank-v2"
DIMENSIONS = 1024
_model_http_client=None
_model_http_lock=threading.Lock()

def model_http_client():
    global _model_http_client
    with _model_http_lock:
        if _model_http_client is None or _model_http_client.is_closed:
            limits=httpx.Limits(max_connections=4,max_keepalive_connections=4,keepalive_expiry=120)
            transport=httpx.HTTPTransport(retries=0,verify=True,trust_env=False,limits=limits)
            _model_http_client=httpx.Client(timeout=httpx.Timeout(90,connect=10,write=15,pool=15),trust_env=False,follow_redirects=False,transport=transport)
        return _model_http_client

def close_model_http_client():
    global _model_http_client
    with _model_http_lock:
        if _model_http_client is not None:
            _model_http_client.close();_model_http_client=None

atexit.register(close_model_http_client)

def review_demo_payload(payload,key):
    """Opt-in local inspection of the exact body, before any real API request."""
    if os.environ.get("OPSLENS_DEMO_REVIEW")!="1":return
    from opslens.config import secrets
    body=json.dumps(payload,ensure_ascii=False)
    if any(s in body for s in secrets()+[key] if s):
        raise ModelBlocked("发送前本机检查发现密钥，已阻止请求")
    patterns=(r'\b[A-Za-z]:[\\/]',r'\\Users\\',r'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}',r'sk-[A-Za-z0-9]{12,}')
    identifiers={os.environ.get("USERNAME",""),Path(os.environ.get("USERPROFILE","")).name}
    if any(re.search(p,body,re.I) for p in patterns) or any(re.search(r'\b'+re.escape(v)+r'\b',body,re.I) for v in identifiers if v):
        raise ModelBlocked("发送前本机检查发现可能的私人标识，已阻止请求")
    path=ROOT/".runtime"/("outbound-payload-2.1-"+uuid4().hex+".json")
    path.write_text(body,encoding="utf-8")


class ModelBlocked(Exception):
    pass


class ModelFailure(Exception):
    pass


def api_base():
    value = os.environ.get("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port:
        raise ModelBlocked("模型端点必须是已核验的北京地域 HTTPS 地址")
    if parsed.hostname != "dashscope.aliyuncs.com" and not re.fullmatch(r"[a-zA-Z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com", parsed.hostname or ""):
        raise ModelBlocked("仅允许百炼北京地域端点")
    if parsed.path != "/api/v1":
        raise ModelBlocked("模型端点路径必须为 /api/v1")
    return value


class QuotaGuard:
    def __init__(self, path=None, key="", endpoint=None):
        self.path = Path(path or ROOT/".runtime/model-quota.json")
        self.key = key
        self.endpoint = endpoint or api_base()

    def check(self, model, estimated_tokens=1):
        if not self.key:
            raise ModelBlocked("未配置 DASHSCOPE_APIKEY")
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            row = record["models"][model]
            verified = datetime.fromisoformat(row["verified_at"])
            expiry = datetime.fromisoformat(row["expires_at"])
            now = datetime.now(timezone.utc)
            if verified.tzinfo is None or expiry.tzinfo is None:
                raise ValueError
            valid = (
                record["key_sha256"] == hashlib.sha256(self.key.encode()).hexdigest()
                and record["api_base"] == self.endpoint
                and row["region"] == "cn-beijing"
                and row["free_only_effective"] is True
                and verified <= now < verified + timedelta(hours=24)
                and now < expiry
                and row["remaining_tokens"] >= estimated_tokens
            )
            if not valid:
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise ModelBlocked(f"{model} 免费额度或用完即停未核验/已过期，禁止真实调用") from None
        return row


class DatabaseLedger:
    def reserve(self, model, payload, estimated, verified):
        from opslens import db
        from psycopg.types.json import Jsonb
        request_id = uuid4()
        with db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1202))",(model,))
            rows = conn.execute("SELECT status,estimated_tokens,actual_tokens,error_code FROM kb_model_calls WHERE model=%s AND created_at>=%s",(model,verified["verified_at"])).fetchall()
            if any(row["error_code"] == "quota_exhausted" for row in rows):
                raise ModelBlocked("模型已返回额度耗尽，需重新核验后才能继续")
            used = sum(row["actual_tokens"] if row["actual_tokens"] is not None else row["estimated_tokens"] for row in rows)
            if used + estimated > verified["remaining_tokens"]:
                raise ModelBlocked("本机预算不足，停止真实调用")
            conn.execute("INSERT INTO kb_model_calls(id,model,status,estimated_tokens,input_payload) VALUES (%s,%s,'reserved',%s,%s)",(request_id,model,estimated,Jsonb(payload)))
        return request_id

    def finish(self, request_id, result=None, error_code=None):
        from opslens import db
        from psycopg.types.json import Jsonb
        actual = ((result or {}).get("usage") or {}).get("total_tokens")
        if not isinstance(actual,int) or actual < 0:
            actual = None
        with db.connect() as conn:
            conn.execute("UPDATE kb_model_calls SET status=%s,actual_tokens=%s,response_payload=%s,error_code=%s,finished_at=now() WHERE id=%s",("completed" if result and not error_code else "failed",actual,Jsonb(result) if result else None,error_code,request_id))


class DashScopeProvider:
    def __init__(self, key=None, guard=None, transport=None, ledger=None):
        self.key = key if key is not None else os.environ.get("DASHSCOPE_APIKEY","")
        self.base = api_base()
        self.guard = guard or QuotaGuard(key=self.key,endpoint=self.base)
        self.transport = transport
        self.ledger = ledger or DatabaseLedger()

    def _url(self,path):
        return self.base+path

    def _normalize(self,data):
        return data

    def _call(self, model, path, payload, estimate):
        if not self.transport:review_demo_payload(payload,self.key)
        verified = self.guard.check(model,estimate)
        request_id = self.ledger.reserve(model,payload,estimate,verified)
        try:
            if self.transport:
                data = self.transport(path,payload)
            else:
                response = model_http_client().post(self._url(path),headers={"Authorization":"Bearer "+self.key},json=payload)
                if response.status_code != 200:
                    code = "quota_exhausted" if response.status_code in (402,403) else "model_http_"+str(response.status_code)
                    try:error_data=response.json()
                    except ValueError:error_data={"http_status":response.status_code}
                    self.ledger.finish(request_id,result=error_data,error_code=code)
                    if code == "quota_exhausted":
                        raise ModelBlocked("模型额度或权限已阻止调用；停止后续请求")
                    raise ModelFailure("模型请求失败："+code)
                data = response.json()
            data=self._normalize(data)
            if not isinstance(data,dict) or "output" not in data:
                raise ValueError
            self.ledger.finish(request_id,result=data)
            return data
        except (ModelBlocked, ModelFailure):
            raise
        except Exception as exc:
            from opslens.security import redact
            from opslens.config import secrets
            chain=[];current=exc
            while current is not None and len(chain)<4:
                chain.append({"type":type(current).__name__,"message":redact(str(current),secrets()+[self.key])[:600],
                    "errno":getattr(current,"errno",None),"winerror":getattr(current,"winerror",None)})
                current=current.__cause__
            # Local failure diagnostics only; never insert them into a model context.
            self.ledger.finish(request_id,result={"diagnostic":chain},error_code="model_exception_"+type(exc).__name__)
            raise ModelFailure("模型超时或响应异常；已保留调用记录，不自动重试") from None

    def embed(self, texts, text_type="document"):
        if not 1 <= len(texts) <= 10 or any(not text.strip() or len(text.encode())>8192 for text in texts):
            raise ModelFailure("向量化输入超出本次批次或文本限制")
        payload={"model":EMBED_MODEL,"input":{"texts":texts},"parameters":{"dimension":DIMENSIONS,"text_type":text_type,"output_type":"dense"}}
        data=self._call(EMBED_MODEL,"/services/embeddings/text-embedding/text-embedding",payload,sum(len(text.encode()) for text in texts))
        try:
            rows=data["output"]["embeddings"]
            mapping={row["text_index"]:row["embedding"] for row in rows}
            if len(mapping)!=len(texts) or set(mapping)!=set(range(len(texts))):
                raise ValueError
            vectors=[mapping[i] for i in range(len(texts))]
            if any(len(v)!=DIMENSIONS or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in v) or not any(v) for v in vectors):
                raise ValueError
            return vectors
        except (ValueError,KeyError,TypeError):
            raise ModelFailure("嵌入响应维度、顺序或数值不合法") from None

    def rerank(self, query, texts):
        if not texts or len(texts)>20:
            raise ModelFailure("重排候选必须为 1–20 条")
        estimate=len(query.encode())*len(texts)+sum(len(text.encode()) for text in texts)
        if estimate>29000:
            raise ModelFailure("重排输入超出保守预算，请减少候选长度")
        payload={"model":RERANK_MODEL,"input":{"query":query,"documents":texts},"parameters":{"top_n":len(texts),"return_documents":False}}
        data=self._call(RERANK_MODEL,"/services/rerank/text-rerank/text-rerank",payload,estimate)
        try:
            rows=data["output"]["results"]
            indexes=[row["index"] for row in rows]
            if len(rows)!=len(texts) or set(indexes)!=set(range(len(texts))):
                raise ValueError
            if any(not isinstance(row["relevance_score"],(int,float)) or not math.isfinite(row["relevance_score"]) or not 0 <= row["relevance_score"] <= 1 for row in rows):
                raise ValueError
            return rows
        except (ValueError,KeyError,TypeError):
            raise ModelFailure("重排响应索引或分数不合法") from None
