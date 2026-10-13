# 事务阻塞与任务行锁

## 任务处理事务
worker 更新任务行的状态和统计结果时需要取得行锁。如果另一个未提交事务持有该任务行锁，worker 可能等待，即使容器、数据库和 Redis 都在线。

## 专用实验阻塞
故障准备进程 application_name 为 opslens-injected-blocker，只锁定专门准备的任务编号。worker 的数据库连接标记为 opslens-worker。

## 只读核对
在 pg_stat_activity 诊断中查看 application_name、state、wait_event_type、wait_event 和 pg_blocking_pids。worker 存在 blocking_pids 且等待 Lock 时，有直接的事务阻塞依据。不要随意终止数据库里的其他会话；恢复只能针对专门注入的阻塞事务，后续还需要批准与实际业务验证。
