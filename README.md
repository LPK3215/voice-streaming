# voice-streaming

> **所属专项**：多模态Agent（D08 语音流式响应）
> **设计与实验文档**：笔记库 `02-AI/AI专项/多模态Agent/`（总纲第十章实验口径）
> **开源**：https://github.com/LPK3215/voice-streaming（公开 · MIT）

## 要回答的问题

**怎样缩短用户等到第一声回复的时间？** D08 的最小实验：

- **full**（基线）：等完整回答生成完，再整段合成一次、一次播放
- **stream**（增强）：LLM 流式 → 按标点分句 → 每句立即合成（线程池并发）→ 排队播放
- **fixed**（对照）：同样流式，但用固定字符窗口切分，刻意不在语义边界断

比较：TTFA（说完到首个可听音频）、makespan（末句播完）、播放停顿、分句保真（漏读/重复）、TTS 调用数与耗时、LLM 首字与总时延、token。

## 测量口径

- **真实测量**：LLM 每个 chunk 的到达时刻、每段 TTS 的真实合成耗时、每段音频的真实时长（`wave` 读帧）、合成并发执行。
- **纯计算**：播放调度 = `可播时刻 = max(该段合成完, 上一段播完)`，无不确定性，可复现。
- **固定条件**：同一批 12 题（短/中/长三档，取自本专题真实知识点）、固定 `<provider>/<model>`、temperature=0、固定 SAPI 中文音色与语速。
- **默认串行跑**（workers=1）：时延数字要求无 CPU 竞争。
- 用户说完到发送问题之间无 ASR/VAD：本实验只测「生成到发声」这段（总纲 2.1 语音链路的后两段），ASR 与 VAD 不在范围内。

## 结构

```
data/questions.json  12 题测试集（tier=short/medium/long + expect 人工核验要点）
src/
  config.py    .env 三件套 + 固定参数（并发数、分句长度、停顿阈值）
  llm.py       OpenAI 流式，逐 chunk 记到达时刻，带 usage
  tts.py       Windows SAPI 离线合成 wav（中文音色），真实播放用 winsound
  segment.py   SentenceSegmenter（标点/逗号二分）与 FixedSegmenter（固定窗口）
  player.py    SegEvent 时间线 → ttfa / makespan / pauses / idle
  pipeline.py  三臂编排（full / stream / fixed），B/C 臂线程池并发合成
  bench.py     批量 + 逐题落盘 + 汇总（分臂/分档/成对提前量）
  cli.py       selftest / bench / play / demo / probe
  selftest.py  机制自测（分句保真、时间线、SAPI、配置，不调 LLM）
  probe.py     环境探测（音色枚举、合成计时、并发合成、LLM 流式）
runs/<bench>/  per_query.jsonl / summary.json / meta.json / audio/*.wav
```

## 运行

```bash
pip install -r requirements.txt
cp .env.example .env   # 填 OpenAI 兼容端点三件套；对照实验固定单条 <provider>/<model>

python -m src.cli selftest          # 机制自测，不调 LLM
python -m src.cli demo --qid q06    # 单题三臂冒烟（真实调 LLM + TTS）
python -m src.cli bench             # 全量：12 题 × 3 臂，串行
python -m src.cli play runs/<dir>/audio/q06-stream/seg-000.wav   # 真实听感
python -m src.cli probe             # 环境与能力探测
```

## 结果

> 权威数据：`runs/bench-01/`（`summary.json` + 36 条 `per_query.jsonl` + `meta.json`），2026-11-16 实测。模型 `qwen-cn/deepseek-v4.1-flash-1017`（temperature=0），SAPI 中文 Huihui 离线合成，2 线程并发，12 题 × 3 臂串行跑。

### 三臂汇总（n=12）

| 臂 | TTFA p50 | TTFA 均值 | TTFA max | makespan 均值 | 停顿次数 | 平均段数 | 保真 |
|---|---|---|---|---|---|---|---|
| full（整段） | 4.028s | 4.243s | 6.305s | 43.379s | 0 | 1.0 | 12/12 |
| stream（按句） | 3.328s | 3.525s | 5.107s | 42.820s | 0 | 3.9 | 12/12 |
| fixed（固定窗口） | 3.412s | 3.436s | 5.127s | 48.011s | 0 | 8.2 | 12/12 |

### 成对比较（TTFA 提前量，正数 = 比 full 快）

| 对比 | 均值提前 | p50 提前 | 负收益 |
|---|---|---|---|
| stream − full | **0.717s** | 0.641s | 1/12（q02） |
| fixed − full | 0.807s | 0.779s | 1/12（q06） |

### 分档（TTFA 均值）

| 档 | n | full | stream | fixed |
|---|---|---|---|---|
| short | 3 | 3.116s | 3.013s | 2.837s |
| medium | 4 | 4.271s | 3.602s | 3.493s |
| long | 5 | 4.897s | 3.772s | 3.749s |

### 结论

1. **流式确实缩短首声**：stream 全档平均提前 0.72s、p50 提前 0.64s；回答越长收益越大，long 档提前 1.13s。
2. **代价在调用不在总时长**：makespan 几乎不变（43.38 → 42.82s），但段数从 1 段变 3.9 段（fixed 8.2 段），TTS 调用翻 4~8 倍、fixed 的音频总时长膨胀 13.9%。
3. **失效条件实测成立**：极短回答（q02，50 字单句）stream 反而慢 0.242s——第一句就是全文，分句与调度开销白付。
4. **fixed 在时延上不输 stream**（均值还快 0.09s），输在可懂度：固定窗口跨词切断，且因逐段重渲音频总时长膨胀。TTFA 不是全部，听感也是指标。
5. **本测试机上 TTS 不是瓶颈**：SAPI 离线渲染 ~50ms 出 5.1s 音频，2 线程并发下 36 轮 0 停顿；真实云 TTS 按句首包延迟会逐段累加，这一点本实验未覆盖。

### 数据口径说明

- 同题三臂共享**同一次** LLM chunk 流的到达时刻（`shared_llm_stream=true`），臂间差异只来自切分与调度，不含 LLM 生成速度的随机性。
- 首段等待计入 TTFA、不计入停顿；停顿只发生在「上一段播完、下一段未合成好」。36 轮全部为 0。
- 音频 wav（62MB）本地保留、未入仓；要真实听感重跑 `python -m src.cli demo --qid q06` 即可复现。

## 相关

- 笔记库 `02-AI/AI专项/多模态Agent/` — 语音技术栈、D08 设计与实测（`语音流式响应实测.md`）
- `02-AI/AI知识库/多模态Agent.html` — 结论层答题卡（语音流式一组 2026-11-16 已回填）
