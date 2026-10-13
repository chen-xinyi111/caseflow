# Redis 地址错误与依赖配置

## 正常地址
实验 worker 的 LAB_REDIS_URL 通常为 redis://redis:6379/0，主机 redis 是所属 Compose 网络中的服务名。正常与故障实例的相同服务名通过独立网络隔离。

## 地址错误
若 worker 指向不存在的主机，Redis 容器本身可能正常，API 也能入队，但 worker 不能建立连接。日志会记录 dependency failure、连接错误或域名解析错误，任务保持等待。

## 证据与边界
核对 docker.config_summary 中 worker 的 LAB_REDIS_URL、worker 日志，以及 Redis 本身的状态和队列长度。只有“Redis 运行正常”不足以确认 worker 能访问它。完整密码和密钥不得进入配置摘要。
