# 统计规则与应用版本核对

## 正常业务规则
v1 统计 Unicode 字符数，包括空白；词数按 Unicode 空白分隔的非空片段计数，不是中文分词。预填文本 OpsLens AI makes evidence clear 的正确结果为 31 字符、5 个词。

## 专门标注的错误版本
实验版本 v0-bug 会错误地将词数固定为 1。任务可显示 completed，但结果违反正常业务规则。因此“任务完成”不等于“业务正确”。

## 只读核对
对照任务 result、worker_version、docker.config_summary 的 LAB_WORKER_VERSION 和实际业务输入。判断版本问题要结合结果偏差和运行版本，不依据手册中的版本名字单独确认。受控回退只允许已保存、已知的应用版本。
