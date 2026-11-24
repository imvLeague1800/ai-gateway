# Qwen 长冷 RAG 准入补测与代码收敛结果

测试日期：2026-11-24。执行范围：第一版 Scheduler 隔离测试实例、`.15` 单 Qwen 副本；`.16` 已切换为独立 Qwen 副本并保留运行，原 Gemma 服务本轮停用。长冷实验只使用 `.15`，`.16` 留给后续双副本验证。

## 结论摘要

- **代码收敛**：产品代码恢复到初始提交 `2f6446e`；Auto/Auto-v2、Router v2 实验入口和旧 benchmark 脚本已移除，当前 Scheduler 单元测试 **208 passed**。测试资产、报告和冻结数据保留。
- **配置修复**：根 `config/backends.yaml` 补齐 `.15`/`.16` 两个 Spark 后端，`config/litellm.yaml` 别名与 Scheduler 的 `qwen3.8-27b` 对齐；隔离实验使用独立 `benchmarks/router/cold_rag/backends-long{1,2}.yaml`。
- **长冷实验**：短 Agent 与约 17.5K 冷文档按 **1:1** 混跑，开放环到达率 **0.23 req/s**，每组预热 15s、测量 120s，3 组配对，交替先跑上限 1/2。
- **结果**：上限 1 在短 Agent 的 P95 TTFT 上 3/3 组更低（聚合 **2,647 vs 4,091 ms**），但 P50 只在 2/3 组更低；代价是长冷文档 TTFT P95 **14,075 vs 5,639 ms**、完成 P95 **19,962 vs 16,685 ms**，整机稳态 TPS **65.0 vs 69.8（-6.8%）**。上限 1 没有在 3 组中稳定降低短 Agent P50，且长冷尾部明显变差，**建议保留上限 2**；上限 1 仅作为“短 Agent 尾部优先、可接受长冷 P95 约 20s”的候选。
- **建议**：**保留上限 2。** 3 组中上限 1 只用短 Agent P95 一致变好，短 Agent P50 未稳定改善；长冷 TTFT P95 和完成 P95 明显变差，整机 TPS 低约 6.8%。若业务明确优先短 Agent 尾部且能接受长冷 P95 约 20s，可把上限 1 作为候选，但本轮不能据此推荐。
- **没有业务 SLO**：未提供 Agent TTFT/长冷端到端目标，因此以上是两配置的性能与代价，不是事后设定的硬门槛。
- **截断**：输出上限 512 token，冷文档 7（上限 1）/ 8（上限 2）个请求达到上限；保留在原始记录，未当作成功质量样本。
- **输出长度**：短 Agent 中位 203–208 token，低于 256 token 目标；冷文档中位 359 token，7/8 个请求达到 512 上限；所有截断保留在原始记录，未按成功质量样本处理。
- **缓存**：窗口聚合命中率 10.3%/9.9%；短 Agent 共享上下文贡献主要命中，冷文档使用每记录 salt，抽检两请求命中为 0。逐请求缓存计数不可归因。
- **引擎排队**：vLLM 只有聚合 waiting，峰值 0；无法给逐请求引擎排队 P50/P95。
- **部署**：`.16` 保留 Qwen，Gemma 继续停用；`config/backends.yaml` 已包含两台 Qwen；测试实例不经过 LiteLLM。

## 代码清理与验证

| 类别 | 处理 |
| --- | --- |
| 产品代码 | `scheduler/`、根 README、`config/backends.yaml`、`config/litellm.yaml`、部署文档恢复到 `2f6446e`；补齐两个 Spark 后端并对齐 LiteLLM 别名。 |
| 实验代码 | 移除 `scheduler/app/auto.py`、`router_v2.py`、`session_stats.py`、`test_auto.py`、`test_router_v2.py`、Router v2 Compose/配置、`scheduler_principal.py`、`deploy/router-logs/`。 |
| 旧测试脚本 | 移除 `fault.py`、`hot.py`、`report.py`、`run-pairs.ps1`、`test_replay.py`；现行 `run_retest.py`、`replay.py`、`analyze_retest.py`、`run_fault_matrix.py`、`fault_retest.py`、`analyze_fault.py`、`hot_forced.py`、`smoke_*`、`transport.py`、`prompts.py`、`generate.py` 保留。 |
| CI | `scheduler/.venv/Scripts/python.exe -m pytest -q`：**208 passed**。 |
| 历史 Router v2 | `docs/router-v2.md` 保留为实验记录，顶部标记为退役；`deploy/router-logs/` 和测试配置入口不再存在。 |

清理前已保存 tracked diff、未跟踪源码 tar 和 `git status` 到 `.verification/cold-rag/backup/`；产品基线提交为 `1e4645c21940794d604036295d0532a977f79215`。

测试资产版本为 `benchmarks/router/manifest.json` v1.2.1；与归档的 2026-11-23 smoke 报告使用的 v1.2.0 相比，差异仅为 `transport.py` 的可选 `user`/`max_tokens`/响应头捕获和 README 元数据，数据、校准、预算文件哈希未变。

## 部署与准入配置

- `.15`：`qwen3.6-35b-a3b-nvfp4`，vLLM `0.26.1rc1.dev1046+gba07e4a48`，`max_model_len=262144`，prefix caching，`max-num-seqs=4`，`max-num-batched-tokens=8192`，thinking 关闭。
- `.16`：同一 Qwen 镜像/权重/参数，`max_model_len=262144`；本轮测试结束后保留 Qwen，Gemma 停用并保留启动配置备份。
- Scheduler：第一版 `scheduler/app`，独立实例 `127.0.0.1:18080`；`max_concurrent_requests=2`，`max_long_requests` 分别 1/2，`long_request_threshold_tokens=8192`；`max_active_per_principal=1`，客户端使用 8 个独立 principal，避免每 Key 上限把总准入压到 1。
- 认证：`SCHEDULER_INTERNAL_API_KEY=cold-rag-test-key`；不经过 LiteLLM，不叠加 Router；客户端直接调用 Scheduler `/v1/chat/completions` 并携带 `x-gateway-queue-wait-seconds` 响应头。
- 配置：`benchmarks/router/cold_rag/backends-long1.yaml` / `backends-long2.yaml`。

## 重放与指标

- 短 Agent 从 `agent-000-cold` 的系统/工具结构派生，项目上下文裁剪到约 **4.2K token**（低于 8192），输出约 25 行。
- 冷文档从 `rag-000-cold` 派生，约 **17.5K token**，每请求使用不同且等长的早期 marker/code，并对每个 archive record 加等长 salt（单个早期标记不足以阻断 vLLM block cache）；目标缓存接近冷基线。
- 输出上限固定 `max_tokens=512`；所有请求关闭 thinking，固定采样。
- 到达表为固定开放环 `t_i = start + i / 0.23`；短 Agent 与冷文档按索引奇偶 1:1 交替；预热 15s，测量 120s，之后停止注入并排空。
- 网关排队：Scheduler 响应头 `x-gateway-queue-wait-seconds`（逐请求）。引擎排队：vLLM 只提供聚合 `num_requests_waiting`，无逐请求队列时间，表中标为**未验证**，另报采样峰值。
- 整机稳态 TPS 用 `.15` 引擎 `vllm:generation_tokens_total` 的窗口区间增量除以 120s；同时报告完成请求的 usage 输出 token 作为交叉检查。

## 结果

| 长冷上限 | 场景 | 请求数 | TTFT P50/P95 (ms) | 每路 TPS P50/P10 | 网关排队 P50/P95 (ms) | 引擎排队 | 完成 P95 (ms) | 稳态整机 TPS | 失败/超时/截断 |
| ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 1 | 短 Agent | 42 | 518 / 2,647 | 55.9 / 33.3 | 0.05 / 0.06 | 未验证（waiting 峰值 0） | 7,451 | 65.0 | 0 / 0 / 0 |
| 1 | 冷文档 | 42 | 7,025 / 14,075 | 61.0 / 57.3 | 3,853 / 10,942 | 未验证（waiting 峰值 0） | 19,962 | 65.0 | 0 / 0 / 7 |
| 2 | 短 Agent | 42 | 1,134 / 4,091 | 55.9 / 36.3 | 587 / 3,602 | 未验证（waiting 峰值 0） | 9,794 | 69.8 | 0 / 0 / 0 |
| 2 | 冷文档 | 42 | 3,400 / 5,639 | 39.8 / 37.5 | 333 / 2,633 | 未验证（waiting 峰值 0） | 16,685 | 69.8 | 0 / 0 / 8 |

> 每格为 3 组 × 14 个窗口请求的合并结果；稳态整机 TPS 为每组 `.15` 的 `vllm:generation_tokens_total` 窗口增量合计除以 3×120s。引擎 waiting 峰值均为 0，队列在 Scheduler；引擎逐请求排队时间不可得，标为未验证。`截断` 是输出达到 512 token 上限的请求；`失败/超时` 为 0。

### 配对明细

| 组 | 整机 TPS 上限 1 / 2 | 短 Agent TTFT P50/P95 上限 1 vs 2 (ms) | 冷文档 TTFT P50/P95 上限 1 vs 2 (ms) | 冷文档完成 P95 上限 1 / 2 (ms) |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 65.6 / 70.3 | 481 / 2,679 vs 1,089 / 3,498 | 3,977 / 9,990 vs 3,639 / 5,126 | 15,813 / 16,076 |
| 2 | 65.8 / 67.7 | 962 / 1,589 vs 517 / 2,294 | 11,341 / 14,174 vs 3,110 / 3,427 | 21,028 / 14,906 |
| 3 | 63.6 / 71.4 | 1,411 / 2,623 vs 1,822 / 4,091 | 6,825 / 8,988 vs 4,424 / 5,641 | 15,000 / 16,685 |

上限 1 的短 Agent P95 在 3 组均更低，但 P50 在组 2 反而更高；冷文档在上限 1 下的 TTFT P95 在 3 组均更差，组 2 的完成 P95 也明显更高。

## 推荐与限制

- **选择：保留上限 2。** 3 组中上限 1 只用短 Agent P95 一致变好，短 Agent P50 未稳定改善；长冷 TTFT P95 和完成 P95 明显变差，整机 TPS 低约 6.8%。若业务明确优先短 Agent 尾部且能接受长冷 P95 约 20s，可把上限 1 作为候选，但本轮不能据此推荐。
- **没有业务 SLO**：未提供 Agent TTFT/长冷端到端目标，因此以上是两配置的性能与代价，不是事后设定的硬门槛。
- **截断**：输出上限 512 token，冷文档 7（上限 1）/ 8（上限 2）个请求达到上限；保留在原始记录，未当作成功质量样本。
- **输出长度**：短 Agent 中位 203–208 token，低于 256 token 目标；冷文档中位 359 token，7/8 个请求达到 512 上限；所有截断保留在原始记录，未按成功质量样本处理。
- **缓存**：窗口聚合命中率 10.3%/9.9%；短 Agent 共享上下文贡献主要命中，冷文档使用每记录 salt，抽检两请求命中为 0。逐请求缓存计数不可归因。
- **引擎排队**：vLLM 只有聚合 waiting，峰值 0；无法给逐请求引擎排队 P50/P95。
- **部署**：`.16` 保留 Qwen，Gemma 继续停用；`config/backends.yaml` 已包含两台 Qwen；测试实例不经过 LiteLLM。

- **采集边界**：Engine queue P50/P95 无法逐请求取得，不能把网关排队当成引擎排队；表中的 engine queue 为未知。
- **缓存**：并发窗口只能聚合引擎 hits/queries；短 Agent 与冷文档分开无法逐请求归因，报告窗口聚合值。
- **合成混合**：1:1 是合成比例，不代表生产流量；长冷请求的输入长度和输出长度按固定目标控制，但不能外推到任意文档。
- **单副本**：只验证 `.15` 和第一版 Scheduler 准入；`.16` 双副本、真实 Agent 和故障场景留给下一阶段。
- **稳定性**：0.23 req/s 由 30s 预演选定（0.2 无排队、0.25 在 120s 下长冷队列持续增长）；正式 3 组 120s 的结果仍需结合长冷等待和吞吐判断，不能据此宣称最大容量。

### 证据指纹

| 文件 | SHA-256 |
| --- | --- |
| `manifest.json` v1.2.1 | `d20dce14cc5715b43ae2a334afa391d7538428de5f76d6090ca7203bc25eaa82` |
| `cold_rag/backends-long1.yaml` | `f1828755b041787feb9c5d8d48ffd803a26911d47728aadd99724ba4b94ef177` |
| `cold_rag/backends-long2.yaml` | `1c321def91e7ef033f86a8a97038524219e0ca7961236cbabefe49090920265a` |
| `cold-rag-analysis.json` | `f6f9387160455537503fe08e5dfb2bd3542ba4c53e98e09478d497db8e70d9f2` |
| `runs/cold-rag-v1/run-index.jsonl` | `f6aabf7d3d327c1d5f18d5e4aab15a8deb1e32383c2f5b279539320b37412e02` |
| `runs/cold-rag-v1/evidence-sha256.txt` | `02a9c9f79a18529fa62f01f9aa71cf65396d25aad4bda2f51eab0b7d8021d642` |
| `runs/cold-rag-v1/environment.json` | `2954eddc36fef2d586e3c92cfdaa5a47ddcdefbf71a9beda08492eb88cb01340` |

代码 revision：`1e4645c21940794d604036295d0532a977f79215`（产品清理提交）。原始矩阵目录共 31 个登记文件。

## 原始记录

- `.verification/dual-qwen/cold-rag/matrix/`：6 个配对 run 的 requests.jsonl、metrics.jsonl、summary.json、Scheduler/client 日志。
- `.verification/dual-qwen/cold-rag/rehearsal-*`：0.2/0.23/0.25/0.3/0.4 预演记录；`matrix-aborted-*` 为正式矩阵前的失败/无效尝试。
- 分析结果：`benchmarks/router/cold-rag-analysis.json`。
- 测试脚本：`benchmarks/router/cold_rag/cold_rag_replay.py`、`run_cold_rag_matrix.py`、`analyze_cold_rag.py`。
