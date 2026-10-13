import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from opslens import db
from opslens.collector import docker
mode=sys.argv[1]
target=ROOT/".runtime/restart-check.json"
def state():
    with db.connect() as conn:
        counts={t:conn.execute("SELECT count(*) AS n FROM "+t).fetchone()["n"] for t in ("collections","evidence","collection_events","fault_cases")}
    others={}
    for cid in docker(["ps","-aq","--filter","label=com.docker.compose.project=agent-commerce-stage3"]).split():
        obj=json.loads(docker(["inspect",cid]))[0]
        others[cid]=obj["State"]["StartedAt"]
    return {"counts":counts,"project_one":others}
if mode=="before":
    target.write_text(json.dumps({"before":state()},indent=2),encoding="utf-8")
else:
    result=json.loads(target.read_text(encoding="utf-8"))
    result["after"]=state()
    result["passed"]=result["before"]==result["after"]
    target.write_text(json.dumps(result,indent=2),encoding="utf-8")
    assert result["passed"], result
    print("Platform records survived stop/start; project one stayed unchanged.")
