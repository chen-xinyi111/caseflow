# CaseFlow 前端

Vue、TypeScript与Vite。项目首页、需求、用例、变更及设置组成日常工作台。API位于/api/v1/caseflow，类型通过后端OpenAPI生成，不手工修改生成文件。

```powershell
pnpm run types
pnpm test
pnpm run build
```

生产构建由本机FastAPI提供。界面不展示内部开发批次，处理记录与引用详情按需展开。
