# worker 与等待处理任务

## 职责与业务路径
正常流程是 API 保存 jobs 记录，然后将任务编号推入 Redis 的 lab:jobs 列表。后台 worker 使用 BLPOP 消费编号，从 PostgreSQL 读取文本，计算字符数和词数，再提交完成结果。worker 运行版本正常为 v1。

## worker 停止后任务一直等待
worker 容器为 exited 时，没有后台进程消费队列。只要 API、PostgreSQL 和 Redis 仍可用，提交请求可以成功，任务保持 queued（等待处理），不会自行完成。因此需要同时核对 worker 容器状态、队列长度和任务记录，不能只凭“queued”判断根因。

## 只读核对
查看 docker.services 中 worker 的 state、docker.logs.worker 中最后运行时间，以及 lab.diagnostics 中 Redis 可达性、队列长度、任务状态。Redis 或数据库故障也可能造成任务等待，需排除这些依赖问题。记录时间必须与当前调查一致。
