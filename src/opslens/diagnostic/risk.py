"""Conservative annotation only: this module never runs suggested operations."""
import re
STATE_CHANGE=re.compile(r'重启|重新启动|停止(?:服务|进程|容器)|终止(?:服务|进程)|修改.{0,40}(?:配置|参数)|将.{0,40}(?:改为|修改为)|启动(?:服务|实例|进程|容器)|运行.{0,30}(?:启动命令|启动脚本)|(?:docker(?:\s+compose)?|systemctl)\s+[^\n]*\b(?:restart|start|stop|up|down)\b|\b(?:taskkill|kill|reboot|Restart-Service|Stop-Service|Start-Service)\b',re.I)
def changes_state(step):return bool(step.get('changes_state')) or bool(STATE_CHANGE.search(step.get('operation','')))
