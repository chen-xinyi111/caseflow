# 本机部署与排错

支持 Windows、PowerShell 7、Docker Desktop Linux 容器、Python 3.12–3.14、Node.js 24 和 pnpm 11。依赖由 `requirements.lock` 与前端锁文件固定；安装脚本也可识别 Codex 附带运行时。

## 首次安装

```powershell
./scripts/local/Setup.ps1
./scripts/local/Start.ps1
```

在仓库根目录使用 PowerShell 7。Setup 创建虚拟环境、安装后端依赖，并为新目录生成随机数据库密码与诊断密钥。已有 `.env` 和实验配置保留；不会显示密码。首次下载依赖和镜像需要网络。

Start 默认只启动平台与平台数据库，构建前端，入口为 http://127.0.0.1:18180/ 。它导入内置实验手册及关键词索引，并初始化接入项目的文本资料，不调用模型。接入项目只登记精确 Compose 项目名称，不自动启动或匹配其他项目。

## 真实模型与知识索引

平台从本机用户环境变量 `DASHSCOPE_APIKEY` 读取百炼密钥。可在 Windows 的“编辑账户的环境变量”中自行配置，随后重新启动平台；不要提交密钥或粘贴完整 `.env`。

使用 AI 调查前，在百炼账户页面核对各模型的免费余额、到期与用完即停设置。本版本没有额度配置表单：维护者需在被 Git 忽略的 `.runtime/model-quota.json` 保存实际核验记录。知识库的“导入文档与管理索引”区域用于文档和索引管理，只显示模型就绪状态。

记录结构如下；占位符需由自己的账户核验事实替换，`free_only_effective` 只有实际确认用完即停后才能设为 `true`。默认调查生成模型为 `qwen3.8-max`；向量与重排分别使用 `text-embedding-v4`、`gte-rerank-v2`，按需要在 `models` 中添加同结构条目。

```json
{
  "key_sha256": "SHA256_OF_YOUR_LOCAL_KEY",
  "api_base": "https://dashscope.aliyuncs.com/api/v1",
  "models": {
    "qwen3.8-max": {
      "region": "cn-beijing",
      "free_only_effective": false,
      "verified_at": "YOUR_ACTUAL_VERIFICATION_TIME_WITH_UTC_OFFSET",
      "expires_at": "YOUR_ACCOUNT_EXPIRY_TIME_WITH_UTC_OFFSET",
      "remaining_tokens": 0
    }
  }
}
```

`key_sha256` 是密钥 UTF-8 字节的 SHA-256 摘要；文件不保存密钥本身。余额填写核验时实际剩余 token 数，时间使用带时区的 ISO 8601。平台在核验后 24 小时、账户到期或预算不足时停止调用，不自动付费降级；不要把示例占位符当作已核验配置。

外部项目在本机使用所选项目资料的 BM25 检索，无需语义索引；AI 调查仍需有效模型额度和针对当前快照的发送确认。实验语义检索需在知识库构建向量索引，向量和重排使用相应模型额度。确认自己愿意向百炼北京接口发送所选内容后再操作；配置密钥本身不等于同意发送业务资料。

克隆源码不会获得原维护者的数据库、索引、账户记录、演示调查或真实评测结果。

## 启停与范围

```powershell
./scripts/local/Check.ps1 -Scope Platform
./scripts/local/Start.ps1 -Scope Platform
./scripts/local/RestartPlatform.ps1
./scripts/local/Status.ps1
./scripts/local/Stop.ps1 -Scope Platform
```

默认 `Platform` 只作用于平台。显式 `Fault` 包含平台及故障实验，`All` 才包含正常实验。完整 Start 会启动所选实验服务；准备好故障后应使用 `RestartPlatform.ps1` 或仅平台范围，以免改变故障状态。Stop 保留数据卷。

平台 HTTP 为 18180，正常／故障实验 API 为 18181／18182，平台数据库为 18183，均只绑定 127.0.0.1。预检要求 Linux 引擎、Docker 内存至少 2 GiB、本机可用内存至少 2 GiB、项目磁盘至少 4 GiB 空闲；建议 Docker 分配 4 GiB 或更多。

## 常见问题

| 提示 | 处理 |
| --- | --- |
| Docker 不可用或不是 Linux 引擎 | 打开 Docker Desktop，等待引擎就绪，确认 Linux 容器模式。 |
| Python、Node.js 或 pnpm 缺失 | 安装上述版本，重新打开终端并运行 Setup。 |
| 端口被其他服务占用 | 核对占用程序；脚本不会替你终止其他进程。 |
| 平台未就绪或数据库不可用 | 运行 Status，查看 `.runtime` 中的平台 stderr 日志，避免公开配置和密钥。 |
| 模型额度未核验或已失效 | 在模型账户核对后，更新本机核验记录；不会自动续费。 |
| 外部项目未发现容器 | 检查登记的精确项目名和服务名；缺失采集范围不等于已确认业务根因。 |
| 恢复方案过期或当前状态变化 | 重新核对并生成方案，不反复执行旧动作。 |
| 执行或模型响应未知 | 保留记录并核对已有结果，不盲目重复请求。 |

运行日志、数据库卷、模型响应及账户配置仅在本机保存。私有备份见 [备份与还原](ARCHIVE.md)，用户操作见 [使用说明](PRODUCT.md)。
