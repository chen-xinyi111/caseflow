"""Real isolated socket/startup errors; no Docker, model, business or repair calls."""
import json
import socket
import traceback
from pathlib import Path
from datetime import datetime,timezone

def capture(operation):
    try:operation()
    except Exception:return traceback.format_exc()
    raise AssertionError('Expected a real failure')

def reproduce():
    cases=[]
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));endpoint=reservation.getsockname()
    def connection():
        with socket.socket() as s:s.settimeout(5);s.connect(endpoint)
    failure=capture(connection)
    if "ConnectionRefusedError" not in failure:raise AssertionError("Refusal was not observed; do not label a timeout as refusal")
    with socket.socket() as service:
        service.bind(endpoint);service.listen()
        with socket.create_connection(endpoint,timeout=1):result='connected successfully to isolated listener'
    cases.append({'id':'connection_refused','problem':'本机依赖连接被拒绝','log':failure,'check':'核对依赖目标地址和端口，并确认监听是否存在；仅查看状态','followup':result})
    with socket.socket() as occupied:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):occupied.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        occupied.bind(('127.0.0.1',0));occupied.listen();port=occupied.getsockname()[1]
        def startup():
            with socket.socket() as service:service.bind(('127.0.0.1',port))
        failure=capture(startup)
        with socket.socket() as alternate:
            alternate.bind(('127.0.0.1',0));result='bound successfully using an available port in the isolated fixture'
    cases.append({'id':'port_conflict','problem':'服务启动时端口占用','log':failure,'check':'查看监听端口的进程归属，确认配置是否重复；不要自动停止进程','followup':result})
    def configured_start(value):
        port=int(value)
        with socket.socket() as service:service.bind(('127.0.0.1',port))
    failure=capture(lambda:configured_start('invalid-port'))
    configured_start('0')
    cases.append({'id':'invalid_configuration','problem':'端口配置导致启动失败','log':failure,'check':'检查端口字段是否为合法整数；提供脱敏配置字段即可','followup':'startup configuration accepted; isolated listener bound successfully'})
    return cases

if __name__=='__main__':
    target=Path('.runtime/product-0.5-real-cases.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat(),'source':'real isolated socket/startup reproduction','model_calls':0,'cases':reproduce()},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(target))
