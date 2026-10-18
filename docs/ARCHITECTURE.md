# CaseFlow 架构

```mermaid
flowchart TD
 UI[项目 需求 用例 变更] --> API[FastAPI 范围与修订检查]
 API --> DB[(PostgreSQL 项目与历史)]
 API --> Queue[持久任务与租约]
 Queue --> Extract[全文分段提取]
 Queue --> Agent[LangGraph 单Agent]
 Agent --> Tools[读取需求 检查已有用例 读取原文]
 Agent --> RAG[BM25 向量 RRF 重排]
 RAG --> KB[(授权项目资料)]
 Agent --> Validate[结构与引用校验]
 Validate --> Draft[用例草稿]
 Draft --> Review[人工审核]
 Review --> Export[Excel Markdown]
 Review --> Runs[实际执行记录]
 DB --> Changes[变更候选与复审]
 Queue --> Events[SSE与查询恢复]
 Events --> UI
```

opslens.caseflow承担新业务，复用已有模型、检索和持久化基础。项目级JSONB聚合记录资料、需求、用例、变更和不可变修订快照；独立表记录任务、事件、审计和外部请求。项目行锁及乐观修订阻止迟到生成覆盖人工内容。澄清记录显式关联需求，开放问题阻止确认；决策回答保留来源，仍须人工重新确认需求。

需求提取按全文分段；用例设计采用design→tools→design循环，最终validate提交草稿。工具为read_requirements、existing_cases、search_documents、read_document。每轮最多6次生成、8次工具调用，完整上下文限制32KiB。全文提取任务按段登记并保留进度。

索引成员显式绑定本轮授权文档，始终指定索引，不使用全局活动索引。生成支持百炼免费配置与兼容接口；语义检索和重排使用已核验百炼配置。检索无结果不能证明整篇没有某项需求。

Runtime通过租约续约、执行批次和保存点控制任务。外部请求先标记issued，结果已保存则复用，未知则停止。SSE按编号补读，数据库查询决定最终状态。任务输入绑定模型配置与处理规则签名，签名包含提示、分段、检索参数及Embedding／重排模型标识；续跑和最终提交均核对范围，防止沿用失效上下文。

修改建议由服务端记录原用例修订，应用前检查该修订和需求确认状态。复审采用新的需求修订时，即使用例文字未改，也会产生新用例修订。执行记录始终绑定实际的用例快照；旧结果不能转算为新修订通过。页面同步未编辑字段，保留人工输入并提示服务器内容冲突。

当前适合本机单用户；大规模协作需进一步拆分实体表、增加权限与并发设计，不作企业规模承诺。结构及引文检查不能替代业务语义审核。
