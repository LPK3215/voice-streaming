# runs/ — 实验产物

每次运行的原始输出落在这里，命名 `<run-name>/`。数据文件口径与结论见项目 README 的「结果」节与 `过程文档.md`。

| 目录 | 状态 | 内容 |
|---|---|---|
| `bench-01/` | **权威数据**（2026-11-16） | 全量三臂对照：`summary.json`（汇总）、`per_query.jsonl`（12 题 × 3 臂逐条时间线）、`meta.json`（运行条件：t0 / 模型 / 音色 / seed）。wav 音频本地保留、未入库 |
| `probe-20261116-102828/` | 过程记录 | 候选模型探测（真实性与时长） |
| `probe-20261116-102939/` | 过程记录 | SAPI 渲染耗时与 2 线程并发探测 |
| `demo/` | 过程记录 | `cli demo` 可听性回放（含 `q06-stream` 等 wav） |

复现：`python -m src.cli bench`（全量）、`python -m src.cli demo --qid q06`（单题可听）。
