# CaseFlow 部署与模型配置

## 本机运行

需要Windows、PowerShell 7、Docker Desktop Linux容器、Python 3.12–3.14、Node.js 24及pnpm 11，依赖使用锁文件。

```powershell
./scripts/local/Setup.ps1
./scripts/local/Start.ps1
./scripts/local/Status.ps1
```

打开 http://127.0.0.1:18180/ 。数据库绑定127.0.0.1:18283，Compose项目为caseflow-platform；默认仅启动平台，不需要其他业务项目。应用仅接受本机和同源请求。

## 模型配置

服务端.env或进程环境中配置密钥，页面设置只填写变量名，不接收密钥本身。明确启用模型后才允许处理授权资料。

百炼免费模式检查账户指纹、北京端点、逐模型免费额度和用完即停，核验超过24小时即阻断；不得直接更改时间绕过核验。生成、Embedding和重排分别核验。

先登录百炼北京地域的“费用用量→免费额度”，逐模型确认剩余Token、到期日和“用完即停”已开启，将截图留在本机.runtime内。在.env填写DASHSCOPE_APIKEY，重启平台。按实际观察值登记，以下数值为命令占位，不能直接当成账户额度：

```powershell
. ./scripts/local/Common.ps1
Import-LocalSettings
./.venv/Scripts/python.exe scripts/record_free_quota.py --model qwen3.8-max-0902 --remaining <向下取整的剩余Token> --expires <控制台到期日> --evidence .runtime/quota.png --confirmed-free-only
```

登记工具不访问控制台，也不替你开启用完即停；没有真实核验时禁止运行。需要语义检索时，还须分别登记text-embedding-v4与gte-rerank-v2。保留原模型名称，切换其他型号须由使用者明确选择，平台不自动换模型。

兼容服务填写以/v1结尾的HTTPS基础地址，本机模型允许HTTP；模型须支持工具调用。兼容模式可能产生费用，由用户明确选择，平台不自动切换。语义检索与重排目前使用百炼，无法配置时选择关键词策略。

默认每日20次请求、单次最多2048输出Token，更正和工具中的外部模型请求也计数。次数不是金额上限；响应未知时保留登记，不自动重试。

## 停止与备份

```powershell
./scripts/local/Stop.ps1
./scripts/local/Backup.ps1 -VerifyRestore
```

停止保留数据库卷。备份、完整模型请求与账户配置留本机，密钥单独保管。

## 开发检查

```powershell
./.venv/Scripts/python.exe -m pytest --ignore=tests/test_integration.py --basetemp=.runtime/pytest-local
./.venv/Scripts/python.exe scripts/export_openapi.py
cd frontend
pnpm run types
pnpm test
pnpm run build
```

启动日志位置由.runtime/platform-logs.json记录，分享前需脱敏。
