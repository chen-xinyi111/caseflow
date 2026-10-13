# Redis 队列与入队错误

## 队列机制
API 将已保存的任务编号 RPUSH 到 lab:jobs，worker 使用 BLPOP 消费。Redis 使用独立数据卷保存队列，两个实验实例互不共享。

## Redis 停止
redis 容器停止时，新任务可能已经写入 PostgreSQL，但未能推入队列。API 保留 queued 状态并记录 queue_error=redis_unavailable。worker 日志可能出现 Redis 连接失败。

## 只读核对
同时检查 Redis 容器、redis.reachable、任务 queue_error 和 worker 日志。API 进程在线不代表队列可用。依赖恢复后可用同一 request_id 重新入队；实验规则会核对已有任务，正文变化不能复用原请求编号。
