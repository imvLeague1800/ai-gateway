> 归档说明：本报告复现时使用 `manifest.json` v1.2.0 和 `smoke.json` SHA-256 `8760e370…`。后续 v1.2.2 增加了 `transport.py` 可选参数、共享统计/异常回归和 README 元数据；本报告与 `runs/smoke-v1/` 保留旧指纹，不重算。

# Qwen Router 快速回归与单机并发测试结果

测试日期：2026-11-23。执行范围：Spark `.15` 单副本、临时替换的 `.16` 第二 Qwen 副本，以及隔离 vLLM Router。全量验收仍使用 `benchmarks/router` 的冻结 v1.2.0 数据集；本报告只覆盖快速子集和 1–4 路单机并发，不与全量结果混算。

## 结论摘要

- **快速子集**：16 个来源会话、28 个计分请求；`power_of_two` 与 `cache_aware` 均 **28/28 success**，协议和质量均 28/28。
- **缓存与召回**：`cache_aware` 窗口缓存 token 占比 **27.38%**，同副本续轮 **12/12**；`power_of_two` 为 **11.20%**、**3/12**。RAG 召回变更的 4 个衍生会话均返回新值（4/4）。
- **两副本**：两副本 `/health` 均 200。`power_of_two` 实际分流 12/16；`cache_aware` 在轻量顺序快测中全部落 `.15`，但在队列突发中两副本各承担 6/12，说明两副本均可服务，亲和策略在无负载时偏好同一副本。
- **队列/取消**：12 个并发长请求触发两个引擎各 4 running + 2 waiting；取消 2 条后等待数下降，剩余 10 条全部完成，窗口后 `running/waiting=0`，恢复 0.05–0.07s。取消请求的“等待/生成中”阶段无法由 Router/引擎聚合指标单独归因（见限制）。
- **故障摘除**：停止 `.16` 后 Router 摘除目标；10/10 探针 200，全部由 `.15` 承接；恢复 Gemma 后原入口 200。
- **单机并发**：`.15` 直连，短 Agent、16K 冷文档、16K 热续轮各测 1–4 路，3 轮同步突发 + 30s 持续；结果见下表。3 路整机窗口 TPS：Agent **95.9 vs 99.0（-3.1%）**、RAG 冷 **52.4 vs 51.4（+1.9%）**、RAG 热 **93.2 vs 79.3（+17.6%）**；仅 RAG 热续轮达到 10% 筛选线。4 路 Agent 133.4（+34.7%）、RAG 热 123.1（+55.2%），但 RAG 冷 36.5（-29.1%）回退。
- **恢复**：`.16` Qwen 已停止，Gemma 已恢复且原 Gateway 入口返回 200；`.15` Qwen 健康。

## 快速子集与冻结配置

- 子集文件：`benchmarks/router/smoke.json`，版本 1.0.0，SHA-256 `8760e3704ae9b273ce51840eadd839c0cb20d9fa043da4e336a2cae19d26933f`；来源 manifest 为 v1.2.0，SHA-256 `489dd56308ef7393164dea257745345c2708dc5f6e4d339d093cf42bdf899f1a`。
- 选择：`qa-000..003` 4 个、`agent-000..003` 4 个、`rag-000..003` 4 个、`rag-004..007` 的召回变体 4 个；共 **16 个来源会话、28 个计分请求**。
- 召回变体：保留 `rag-004..007` 的原会话 ID，衍生 ID 为 `rag-004-smoke..rag-007-smoke`；第二轮前部 recall block 的 access code 从 `RAG-00N-AAAAAA` 替换为 `RAG-00N-SMOKE`，预期答案和评分同步更新。规则和原 ID 记录在 `smoke.json`。
- 每个策略使用独立的 15 字符中性 namespace（`NS-` + 12 hex），替换 `BENCH_NAMESPACE`；两策略的请求结构和 token 长度一致，避免跨策略前缀缓存污染。策略顺序为 `cache_aware` → `power_of_two`；记录见各自 `summary.json` 的 `namespace`。
- 模型：`qwen3.6-35b-a3b-nvfp4`；vLLM `0.26.1rc1.dev1046+gba07e4a48`，镜像 `spark-vllm:ba07e4a48-arm64`，`max_model_len=262144`，thinking 关闭。
- Router：`vllm/vllm-router:v0.1.17`，digest `sha256:1128e09f5dc92b201753bc2f306488942d608ec747410f6c207354f0bf377c42`；健康检查 5s / 失败 2 / 成功 2，worker startup check 2s，`cache_threshold=0.3`、`balance_abs_threshold=2`、`balance_rel_threshold=1.2`。
- 快测入口：`.16:8100`，本地 SSH 隧道 18100（API）/19100（metrics）；两策略共用同一 Router 镜像和 worker 列表。
- 单机并发每个请求使用独立的 `httpx.AsyncClient`（单连接、禁用 keepalive 复用）。
- 快测总耗时：路由核心 `cache_aware` 55.45s、`power_of_two` 61.06s；单机并发矩阵 12 档共 1,279.4s（65.7–185.6s/档）。
- 探索轮在 3 路和 4 路长上下文持续阶段出现过客户端 stale-socket `RemoteProtocolError`，同时服务端访问日志仍为 200；正式矩阵改为每请求独立客户端后复测，避免连接池复用造成的客户端假失败。

## 路由快速回归

### 总量与缓存

| 策略 | 请求 | success | protocol | quality | 实际副本 `.15`/`.16` | 窗口缓存 token 占比 | 同副本续轮 | 召回新值 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `power_of_two` | 28 | 28 | 28 | 28 | 12 / 16 | 11.20% | 3/12 | 4/4 |
| `cache_aware` | 28 | 28 | 28 | 28 | 28 / 0 | 27.38% | 12/12 | 4/4 |

`cache_aware` 的轻量顺序快测全部落在 `.15`；这不是副本不可用，队列突发中两副本各承接 6/12，且直接 `/health` 两副本均 200。`power_of_two` 顺序分流 12/16，说明两副本均能服务。

- 请求级缓存命中（28/28 有完整观测）：`power_of_two` **3/28（10.7%）**，`cache_aware` **8/28（28.6%）**；命中集中在 warm 轮。

### 分组延迟

| 场景 | 请求数 | p2 TTFT P50 / 最慢 (ms) | ca TTFT P50 / 最慢 (ms) | p2 完成 P50 (ms) | ca 完成 P50 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| QA | 4 | 142 / 144 | 143 / 144 | 198 | 192 |
| Agent（冷+续） | 8 | 1,510 / 1,558 | 1,545 / 1,578 | 1,754 | 1,786 |
| RAG（冷+续） | 8 | 2,584 / 2,637 | 2,616 / 2,649 | 2,730 | 2,753 |
| RAG 召回变更 | 8 | 2,588 / 2,591 | 2,622 / 2,628 | 2,730 | 2,770 |

- 稳定前缀续轮的缓存收益明显：`cache_aware` Agent warm TTFT **254–298ms**（冷 **1,545–1,578ms**），RAG warm **408–418ms**（冷 **2,616–2,649ms**）；`power_of_two` 同副本 warm 为 233/397/441ms，跨副本 warm 为 1,437–1,444/2,554ms。召回变更的 warm 轮仍走新 prefill（TTFT 约 2.62s），符合“返回新文档值”的预期。
- 所有 28 个请求的 `finish_reason` 均非 `length`，没有截断；质量检查全部通过。

### 队列、取消与摘流

12 个长请求（120 行输出）通过 Router 同时发出，1.2s 后取消 `queue-11`，再 1.0s 后取消 `queue-0`。

| 策略 | 取消前 15/16 (running/waiting) | 第一次取消后 | 第二次取消后 | 剩余完成 | 恢复 running/waiting=0 |
| --- | --- | --- | --- | ---: | ---: |
| `power_of_two` | 4/2, 4/2 | 4/2, 4/1 | 4/1, 4/1 | 10/10 success | 0.07s |
| `cache_aware` | 4/2, 4/2 | 4/1, 4/2 | 4/0, 4/2 | 10/10 success | 0.05s |

- 队列中的 waiting 数下降，running 保持 4（引擎在取消后立即回填等待请求），最终两引擎 `running=0`、`waiting=0`，Router `running=0`。
- 两次取消均收到 HTTP 200 响应头（Router 立即转发 headers），因此仅凭 headers 和聚合指标不能判断被取消请求当时是 waiting 还是 generating；该细分标记为**未验证**，但队列排空和取消后计数恢复已验证。
- 故障摘除：停止 `.16` 后停止命令 2.4s，Router 在 **8.02s** 标记目标不健康，直连 `/health` 为 0；随后 **10/10** 探针返回 200，TTFT 64.6–70.0ms。目标已停，逐探针 worker 归因未捕获，因此 200 只能由 `.15` 承接。

**路由快测判定：通过。** 两策略 28/28 质量与协议通过；两副本均健康且均有实际服务（`power_of_two` 顺序分流 12/16，队列突发两副本各 6/12）；稳定前缀续轮缓存命中、召回变更新值、队列排空和副本摘除均符合预期。唯一未验证项是取消请求当时处于 waiting 还是 generating。

## 单机并发测试

直连 `.15`，保持 `max_model_len=262144`、prefix caching、`max-num-seqs=4`、`max-num-batched-tokens=8192`、thinking 关闭。每个场景 1–4 路，先做 1 次未计分预热突发，再做 **3 轮同步突发**，随后 **30s 持续发送**；下表汇总计分突发和持续窗口。

| 场景 | 并发 | 样本 (burst+sustain) | 实际并发 均值/最大; wait 最大 | TTFT P50 / 最慢 (ms) | 每路 TPS 中位 / P10 | 30s 窗口 TPS | 缓存占比 | 输出 token min/中位/max | 失败 / 截断 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Agent | 1 | 8 (3+5) | 0.95/1; 0 | 238 / 240 | 76.1 / 76.0 | 58.8 | 90.17% | 412/464/500 | 0 / 0 |
| Agent | 2 | 14 (6+8) | 1.90/2; 0 | 352 / 439 | 58.9 / 57.5 | 99.0 | 90.17% | 467/500/503 | 0 / 0 |
| Agent | 3 | 18 (9+9) | 2.79/3; 0 | 449 / 662 | 48.7 / 45.5 | 95.9 | 90.17% | 337/500/500 | 0 / 0 |
| Agent | 4 | 24 (12+12) | 3.80/4; 0 | 722 / 800 | 54.4 / 50.2 | 133.4 | 90.17% | 500/500/501 | 0 / 0 |
| RAG 冷 | 1 | 6 (3+3) | 0.90/1; 0 | 2,613 / 2,654 | 73.8 / 73.5 | 35.4 | 0.00% | 512/547/997 | 0 / 0 |
| RAG 冷 | 2 | 11 (6+5) | 1.65/2; 1 | 4,356 / 5,128 | 55.4 / 51.3 | 51.4 | 0.00% | 511/519/998 | 0 / 0 |
| RAG 冷 | 3 | 15 (9+6) | 2.68/3; 2 | 6,667 / 7,661 | 40.1 / 34.1 | 52.4 | 0.00% | 511/547/999 | 0 / 0 |
| RAG 冷 | 4 | 18 (12+6) | 3.44/4; 3 | 6,709 / 10,395 | 38.0 / 30.9 | 36.5 | 0.00% | 546/549/999 | 0 / 0 |
| RAG 热 | 1 | 8 (3+5) | 0.92/1; 0 | 419 / 741 | 73.8 / 73.7 | 61.4 | 89.00% | 298/514/517 | 0 / 0 |
| RAG 热 | 2 | 14 (6+8) | 1.87/2; 0 | 558 / 798 | 54.9 / 53.3 | 79.3 | 88.99% | 295/382/999 | 0 / 0 |
| RAG 热 | 3 | 17 (9+8) | 2.72/3; 0 | 891 / 1,116 | 43.3 / 41.4 | 93.2 | 89.00% | 300/516/998 | 0 / 0 |
| RAG 热 | 4 | 24 (12+12) | 3.55/4; 0 | 1,404 / 1,438 | 42.1 / 37.3 | 123.1 | 88.98% | 291/469/548 | 0 / 0 |

> 样本数为计分突发与持续请求合计；实际并发是持续窗口 0.5s 采样的 running 均值/最大，wait 最大是该窗口引擎 waiting 峰值；TTFT/每路 TPS 汇总所有计分包，30s 窗口 TPS 为窗口内完成请求的 usage 输出 token / 30s，排空另记但未计入；缓存占比为该突发/持续窗口引擎 hits/queries。所有 12 档均无失败、无截断。

**3 路相对 2 路（优先比较档）**

- **Agent**：整机 **95.9 vs 99.0 TPS（-3.1%）**，每路 TPS 中位 **48.7 vs 58.9（-17.4%）**，TTFT P50 **449 vs 352ms（+27.5%）**；未达到 10% 筛选线。
- **RAG 冷文档**：整机 **52.4 vs 51.4 TPS（+1.9%）**，每路 **40.1 vs 55.4（-27.7%）**，TTFT P50 **6,667 vs 4,356ms（+53.1%）**；未达到筛选线，长冷 prefill 是主要瓶颈。
- **RAG 热续轮**：整机 **93.2 vs 79.3 TPS（+17.6%）**，每路 **43.3 vs 54.9（-21.2%）**，TTFT P50 **891 vs 558ms（+59.7%）**；达到 10% 筛选线，但单路体验明显下降。

**4 路参考**

- Agent：**133.4 TPS（相对 2 路 +34.7%，相对 3 路 +39.1%）**，TTFT P50 722ms，每路 54.4 TPS。
- RAG 冷：**36.5 TPS（相对 2 路 -29.1%）**，TTFT P50 6,709ms，每路 38.0 TPS；出现明显回退。
- RAG 热：**123.1 TPS（相对 2 路 +55.2%，相对 3 路 +32.1%）**，TTFT P50 1,404ms，每路 42.1 TPS。

**判断**：3 路只在 RAG 热续轮达到 10% 整机增益；Agent 和长冷文档未达，长冷文档在 4 路进一步回退。下一步应单独验证长冷请求的并发限制（限制长冷在途数或调整 `max-num-seqs`），不能据此宣称长冷文档可随并发扩展；Agent/RAG 热若要上 3–4 路，需要接受每路 TPS 和 TTFT 的体验代价。

## 限制与未验证项

1. **`cache_aware` 顺序快测只落 `.15`**：轻量顺序请求没有触发转移；队列突发和 `power_of_two` 已证明两副本可服务，但该策略在真实业务低载下的分流偏好仍需更长流量观察。
2. **取消阶段不可归因**：Router 立即返回 200 headers，引擎只提供聚合 running/waiting；无法确认两条被取消请求分别处于 waiting 还是 generating。已确认队列下降、剩余请求完成和计数恢复。
3. **单机并发缓存口径**：并发窗口内引擎缓存计数是聚合值，表中缓存比例按整个突发/持续窗口的 hits/queries 计算，不逐请求归因；`agent` 的系统上下文重复导致约 90% 命中，不是冷启动缓存。
4. **输出长度**：派生长输出用例请求 40 行，实际输出分布约 **291–999 token**（Agent 中位约 464–500；RAG 冷中位 519–549；RAG 热中位 381–516），部分样本接近 1,024 上限但 `finish_reason=stop`，未发生截断；RAG 冷/热的中位略高于 512 token 目标。
5. **快速样本量**：每种策略仅 28 个请求；单机并发每档 6–24 个计分请求（含 3 轮突发和 30s 持续）。用于发现明显回退，不作为 P99 或最大吞吐验收。全量结论见 `docs/qwen-dual-replica-retest-results.md`。
6. **真实 Agent**：本轮仍为合成派生用例，真实 Agent 会话为 0；不能替代真实任务验收。
7. **原始证据**：`.verification/dual-qwen/smoke/` 为独立运行目录；本轮未迁入公司内部持久存储，报告和 `runs/smoke-v1/` 登记路径与哈希。
8. **长冷文档并发**：3 路仅 +1.9% 整机增益、TTFT +53%，4 路整机 -29.1%、TTFT +54%；长冷 prefill 在 2 路以上没有可证明的整机收益。下一轮应单独验证长冷请求的并发限制，不能据此宣称长冷可扩展。

## 测试后恢复

- `.16`：Qwen 已停止；Gemma 已恢复并 healthy（约 200s 启动），`/v1/models` 返回 `gemma-4-26b-a4b-nvfp4`、`max_model_len=262144`，短请求 200 “OK”。
- 生产入口：`.15:4000` 原 Gateway 对 `gemma-4-26b-a4b-nvfp4` 返回 200 “OK”。
- `.16` `.env` 已恢复为 `QWEN_PORT=18000`、`QWEN_MAX_MODEL_LEN=32768`、`GEMMA_PORT=8000`、`GEMMA_MAX_MODEL_LEN=262144`；测试 Router 已移除；`.15` Qwen health 200；SSH 隧道已关闭。

### 证据指纹

| 文件 | SHA-256 |
| --- | --- |
| `smoke.json` | `8760e3704ae9b273ce51840eadd839c0cb20d9fa043da4e336a2cae19d26933f` |
| `runs/smoke-v1/run-index.jsonl` | `5e3dd116ce26040a073c57a4b60aad8ed15e2e67b93cc8e5d398b8eeb01052e7` |
| `runs/smoke-v1/evidence-sha256.txt` | `1c789d9893c28eebdfc23c520874edeb2baf35be6df85e9a3926798cb5a9348c` |
| `runs/smoke-v1/environment.json` | `6bd6af0cfc41ae1f372a7b71fd1da120bd744468dcf1d4c0989a434038448cb4` |
| `runs/smoke-v1/analysis/smoke-analysis.json` | `d42628c426e12176aa6f4989b0bac60960a3c405397b8e5fce6353a8f6172ede` |

> 注：以上为该次运行的历史指纹。2026-11-24 的 v1.2.6 资产调整后，`smoke.json` 当前 SHA-256 为 `c670daffbf68524b35037505f623a467b7642af7f0f1d3958eb369b196abd439`，本次 smoke 入口验收记录见 `docs/router-benchmark-organization-results.md`；`smoke-entry-v1` 已迁出 Git，按 run ID 在持久归档 `X:\damonlu\ai-gateway\router-runs-archive\20261125`（`INDEX.json`）中定位。

代码 revision：`88a9c4426f0d8a3859037ba1fe582602f13ac81a`。原始 `.verification/dual-qwen/smoke/` 树共 59 个登记文件；完整逐文件哈希见 `runs/smoke-v1/evidence-sha256.txt`，运行索引同时记录每个 summary/requests 的哈希。

## 原始记录

- `benchmarks/router/smoke.json`：快速子集、来源 manifest hash、召回变体规则。
- `.verification/dual-qwen/smoke/`：`cache_aware/`、`power_of_two/` 的 `requests.jsonl`/`summary.json`，`queue-*/`，`concurrency/*` 的 requests/summary，`failure-summary.json` 和运行日志。
- 证据登记：`benchmarks/router/runs/smoke-v1/`（摘要、运行索引和原始树 SHA-256 清单）。
- 分析脚本：`benchmarks/router/smoke_replay.py`、`smoke_queue.py`、`smoke_concurrency.py`、`smoke_failure.py`、`run_smoke_concurrency.py`、`analyze_smoke.py`。
