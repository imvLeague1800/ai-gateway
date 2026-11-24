# 双 Qwen 副本路由对照测试结果

测试日期：2026-11-20。测试范围：Spark `.15`、`.16` 和隔离 vLLM Router。生产 Gateway/Scheduler 配置、Auto 规则和模型副本分配未改动；`.16` 的 Gemma 在测试期间临时替换为第二个 Qwen 副本，测试结束后恢复。

## 结论摘要

- **部署对齐**：`.15` 与 `.16` 均运行 Qwen3.6-35B-A3B-NVFP4，单卡 TP=1，`max_model_len=262144`，`max-num-seqs=4`，`max-num-batched-tokens=8192`，prefix caching 开启，GPU 显存比例 0.4；权重、镜像、tokenizer、聊天模板一致。
- **稳定前缀（B）**：`cache_aware` 在 4 组配对回放中 12/12 续轮命中同一副本、缓存命中率约 90%，续轮 P95 TTFT 相对 `power_of_two` 下降约 **79%–84%**，远超 15% 门槛。
- **混合峰值（C）**：高负载 3.0 req/s 下，`cache_aware` 的整体 P99 TTFT 为 **5,641 ms**，`power_of_two` 为 **8,359 ms**（比值 0.675）；达标吞吐 **2.612 vs 1.673 req/s**（+56%）；4/4 组配对回放均达标，失败/超时/拒绝均为 0。
- **热副本（D）**：`cache_aware` 在热副本负载 3、另一副本空闲时，将带热前缀的新请求改派到空闲副本，代价是缓存命中率从约 90% 降到 0、TTFT 从约 0.3 s 升到约 2.1 s；交换两台角色后同样观察到改派。`power_of_two` 无前缀亲和，经 Router 注入的背景负载会均衡到两台，无法形成单热副本；直接注入的负载对 Router 不可见（`/get_load` 404，vLLM 实际暴露 `/load`）。
- **副本故障（E）**：`cache_aware` 在停止 `.16` 后 168.6s 检测到不健康（默认 60s/3），检测后 10/10 新请求全部转到 `.15`，恢复后 10/10 回到 `.16`；`power_of_two` 使用 5s/2，约 10–15s 检测，检测后 10/10 新请求成功，恢复后 `10/10` 成功并回到两台（6/4）。
- **恢复**：`.16` Qwen 已停止，Gemma 已启动并恢复原 `.env`；`.16` 直连 `/v1/models` 和短请求正常，生产 Gateway 原入口对 `gemma-4-26b-a4b-nvfp4` 返回 200（“OK”）。

## 部署与回退

### `.16` Gemma 回退快照

停止 Gemma 前已保存以下原始配置（本地 `.verification/dual-qwen/rollback/`，远端 `/home/dgx/spark-vllm/reports/dual-qwen/`）：

- `env.before`：原 `.env`（`QWEN_PORT=18000`、`QWEN_MAX_MODEL_LEN=32768`、`GEMMA_PORT=8000`、`GEMMA_MAX_MODEL_LEN=262144`）。
- `compose.before.yaml`：原 `spark-vllm/compose.yaml`。
- `gemma-container.json`：`spark-vllm-gemma4` 的完整 `docker inspect`。
- `vllm-image.json`：`spark-vllm:ba07e4a48-arm64` 镜像信息。
- `restore-gemma.sh`：停止 Qwen、恢复 `.env`、启动 Gemma 的脚本。

### 第二个 Qwen 副本

- 从 `.15` 复制 `Qwen3.6-35B-A3B-NVFP4` 到 `.16`，共 22G、23,462,477,857 bytes，平均约 111 MB/s，耗时约 3 分 21 秒。
- 17 个文件全部通过 `.15` 的 `reports/SHA256SUMS` 校验（`sha256sum -c` 全部成功）。
- `.16` 启动 Qwen 后健康检查约 310 秒通过。

| 项目 | `.15` | `.16` |
| --- | --- | --- |
| served model | `qwen3.6-35b-a3b-nvfp4` | `qwen3.6-35b-a3b-nvfp4` |
| 镜像 | `spark-vllm:ba07e4a48-arm64`（`1d858604eba1`） | 同左 |
| `max_model_len` | 262144 | 262144 |
| KV cache tokens | 2,295,745 | 2,291,774 |
| block size | 2096 | 2096 |
| prefix caching | True | True |
| GPU memory utilization | 0.4 | 0.4 |
| 编译参数 | `--max-num-seqs 4 --max-num-batched-tokens 8192 --enable-chunked-prefill --enable-prefix-caching --async-scheduling` | 同左 |

### vLLM Router

- 镜像：`vllm/vllm-router:v0.1.17`
- 镜像 ID：`sha256:2376701c5b3b857d503fbcbe8e97009e43b084713fb1f5b97a7c71a01dc60c3e`
- Repo digest：`sha256:1128e09f5dc92b201753bc2f306488942d608ec747410f6c207354f0bf377c42`
- 架构：`arm64`
- 运行位置：`.16:8100`（host network），Prometheus `127.0.0.1:29100`
- 固定参数：`--policy power_of_two|cache_aware`、两个 worker `http://10.169.25.15:8000` 和 `http://10.169.25.16:8000`、`--worker-startup-check-interval 2`、`--cache-threshold 0.3`、`--balance-abs-threshold 2`、`--balance-rel-threshold 1.2`、`--log-level debug`。健康检查：A/B/C/D 与第一次 E 运行使用 Router 默认的 60s 间隔、3 次失败；第二次 E 运行使用 `5s` 间隔、2 次失败。E 的检测时间按实际运行配置分别记录。
- 测试入口：Windows 客户端经 SSH 隧道访问 `127.0.0.1:18100`（Router）和 `127.0.0.1:19100`（Router metrics）。测试路径中没有 LiteLLM 或 Scheduler，Router 是唯一的副本选择组件。
- `/get_load` 探测：两台 vLLM 均返回 404，实际暴露的是 `/load`（返回 `{"server_load":0}`）；Router 因此回退到本地 in-flight 计数。这一点影响场景 D 的直接负载注入解释。

## 预检（两副本可比性）

| 检查 | `.15` Qwen | `.16` Qwen | 差异 |
| --- | ---: | ---: | ---: |
| 短问答 TTFT 中位数 | 156 ms | 172 ms | +10.3% |
| 16K 冷文档 TTFT | 2,531 ms | 2,609 ms | +3.1% |
| 16K 同文档续轮 TTFT | 359 ms | 375 ms | +4.5% |
| 16K 续轮缓存命中率 | 92.15% | 92.15% | 0 |
| 短问答答案 | 391 | 391 | 一致 |

短问答中位 TTFT 差异 10.3%，在计划要求的 15% 以内；冷/热 16K 差异更小。之后才进入策略对照。

## A. 独立代码问答与 B. 稳定前缀

每个策略 4 组配对回放，每组包含 8 个独立短问答、3 组 16K 同文档冷/热对、3 组多轮 Agent 冷/热对；每策略共 80 个请求，160/160 质量通过。

| 策略 | 类型 | 样本 | 质量 | 续轮命中同副本 | TTFT P50 / P95 (ms) | 缓存命中率 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `power_of_two` | RAG 续轮 | 12 | 12 | 5/12 | 2,562 / 2,578 | 0% |
| `power_of_two` | Agent 续轮 | 12 | 12 | 5/12 | 1,437 / 1,438 | 0% |
| `power_of_two` | RAG 冷首轮 | 12 | 12 | — | 2,578 / 2,593 | 0% |
| `power_of_two` | Agent 冷首轮 | 12 | 12 | — | 1,531 / 1,531 | 0% |
| `cache_aware` | RAG 续轮 | 12 | 12 | 12/12 | 422 / 469 | 约 90% |
| `cache_aware` | Agent 续轮 | 12 | 12 | 12/12 | 266 / 297 | 约 90% |
| `cache_aware` | RAG 冷首轮 | 12 | 12 | — | 2,594 / 2,625 | 0% |
| `cache_aware` | Agent 冷首轮 | 12 | 12 | — | 1,531 / 1,562 | 0% |

短问答副本覆盖：`power_of_two` 22/10（`.15`/`.16`），`cache_aware` 24/8；两台均有有效请求。

### B 的配对 P95 TTFT

| 回放 | RAG 续轮 p2 / ca (ms) | 改善 | Agent 续轮 p2 / ca (ms) | 改善 |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 2,578 / 484 | 81.2% | 1,453 / 313 | 78.5% |
| 2 | 2,562 / 422 | 83.5% | 1,438 / 281 | 80.5% |
| 3 | 2,594 / 469 | 81.9% | 1,437 / 297 | 79.3% |
| 4 | 2,578 / 422 | 83.6% | 1,437 / 266 | 81.5% |

**B 判定：通过。** 缓存命中率从 0% 提高到约 90%，续轮 P95 TTFT 改善约 79%–84%，4/4 组达到 ≥15% 门槛。冷首轮 TTFT 在两策略间相差不超过约 2%，没有用冷启动掩盖缓存收益。

## C. 混合峰值

工作负载：40% 多轮 Agent、30% 独立代码问答、30% 长文档/RAG；RAG 一半为同文档续轮、一半为前部召回内容变化。固定到达时间、开放环回放：轻载 **1.5 req/s**、高载 **3.0 req/s**（先用 `power_of_two` 冷前缀短扫标定；轻载峰值 engine waiting=0，高载峰值 waiting=12）。每策略每档 50 请求 × 4 组 = 200 请求/档，400 请求/策略。

| 档位 | 策略 | 请求/完成 | 首轮 TTFT P50/P95 | 续轮 TTFT P50/P95 | 整体 P99 | 完成 P95 | 缓存命中 token 占比 | `.15`/`.16` 派发 | 失败/超时/拒绝 | 达标 TPS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 轻载 | `power_of_two` | 200/200 | 1,532 / 3,235 | 1,453 / 3,000 | 3,969 | 4,343 | 40.9% | 101/99 | 0/0/0 | 1.498 |
| 轻载 | `cache_aware` | 200/200 | 594 / 3,172 | 313 / 2,266 | 3,297 | 2,641 | 63.1% | 109/91 | 0/0/0 | 1.512 |
| 高载 | `power_of_two` | 200/200 | 4,156 / 8,219 | 3,531 / 7,953 | 8,359 | 9,218 | 42.3% | 101/99 | 0/0/0 | 1.673 |
| 高载 | `cache_aware` | 200/200 | 2,750 / 5,312 | 1,844 / 4,422 | 5,641 | 6,437 | 54.6% | 103/97 | 0/0/0 | 2.612 |

> 首轮 = `agent_cold` + `rag_changed`；续轮 = `agent_warm` + `rag_warm`；QA 计入整体 P99。队列峰值为同速率冷前缀校准值（轻载 0、高载 12 个 engine waiting），正式回放未逐请求采样队列；派发来自 Router 的 `processed_requests_total` 增量。

### C 的高载配对判定

| 回放 | p2 P99 | ca P99 | P99 比值 | p2 达标 TPS | ca 达标 TPS | TPS 比值 | 判定 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 101 | 7,547 | 7,281 | 0.965 | 1.808 | 2.296 | 1.270 | 通过 |
| 102 | 9,813 | 5,250 | 0.535 | 1.162 | 2.702 | 2.325 | 通过 |
| 103 | 5,828 | 5,641 | 0.968 | 1.862 | 2.512 | 1.349 | 通过 |
| 104 | 6,875 | 5,312 | 0.773 | 1.877 | 2.966 | 1.580 | 通过 |
| 合并 | 8,359 | 5,641 | 0.675 | 1.673 | 2.612 | 1.561 | 通过 |

**C 判定：通过。** 高载 P99 没有回退（4/4 组均低于 `power_of_two`），合并 P99 为基线的 67.5%；达标吞吐提高而不是下降；失败/超时/拒绝增加 0 个百分点。轻载 P99 和缓存占比也优于基线，达标吞吐基本持平（1.512 vs 1.498）。

## D. 热副本偏忙

| 实验 | 预热副本 | 背景负载（Router 可见） | 探针落点 | 探针缓存命中 | 探针 TTFT | 是否改派 |
| --- | --- | --- | --- | ---: | ---: | --- |
| `power_of_two` | `.15` | 2/2 均衡 | `.15` | 92.9% | 328 ms | 否 |
| `cache_aware`（第一轮） | `.15` | `.15`=3，`.16`=0 | `.16` | 0% | 2,125 ms（热时 344 ms） | 是 |
| `cache_aware` 角色交换 A | `.15` | `.15`=3，`.16`=0 | `.16` | 0% | 2,718 ms（热时 484 ms） | 是 |
| `cache_aware` 角色交换 B | `.16` | `.16`=3，`.15`=0 | `.15` | 0% | 2,094 ms（热时 313 ms） | 是 |

- `cache_aware` 在两个方向都观察到：热副本负载达到 3、另一台为 0 时，新请求被改派到空闲副本；代价是重新 prefill，缓存命中率从约 90% 降到 0，TTFT 从约 0.3 s 升到约 2.1 s。
- `power_of_two` 没有前缀亲和，经 Router 注入的 3–4 个背景请求会被分散到两台（本次 2/2），无法形成单热副本；直接注入到单台的负载不会被 Router 的本地 in-flight 计数看到（`/get_load` 404），因此不能作为有效偏斜信号。该策略的 D 结论记为“没有可观察的持久热副本”，不代表负载均衡失效。

## E. 副本故障与恢复

### `cache_aware`

- 停止 `.16` Qwen 后，Router 在 **168.6s** 检测到 `.16` 不健康（第一轮使用默认 60s 检查间隔、3 次失败阈值）。
- 停止前在途：`.15`=1、`.16`=3。4 个请求均收到 200 响应头，其中 `.16` 上的 3 个流被中断，客户端记录 3 个 `RemoteProtocolError: peer closed connection without sending complete message body`；`.15` 上的 1 个请求正常完成。
- 检测后 10/10 个新请求全部派发到 `.15`，全部 HTTP 200。
- 恢复：`.16` 直接 `/health` 在 **223.7s** 返回 200，Router 在 **316.4s** 将 `.16` 标记回健康；恢复后 10/10 个新请求全部派发到 `.16`，全部 HTTP 200。
- **判定：通过。** 检测窗口后没有新请求派往失效副本；健康副本继续接单；恢复后 `.16` 重新接单。

### `power_of_two`

- 停止 `.16` Qwen 后按 **5s/2** 健康检查，检测窗口按配置约 10–15s；脚本在 40s 在途流超时后才读取状态，因此 JSON 里的 `detection_s=0.02` 表示“读取时已为 0”，不是完整的检测耗时。
- 停止前在途：`.15`=1、`.16`=3。4 个请求均收到 200 响应头，其中 `.16` 上的 3 个流被中断（`RemoteProtocolError`），`.15` 上的 1 个正常完成。
- 检测后 10/10 个新请求 HTTP 200；本次逐请求 `worker_delta` 未捕获到归因，但 `.16` 已停止，200 响应只能来自 `.15`。
- 恢复：`.16` 直接 `/health` 在 **223.6s** 返回 200，Router 在 **229.6s** 恢复健康；恢复后 10/10 个新请求全部 200，派发为 `.16` 6 个、`.15` 4 个。
- **判定：通过。** 检测窗口后不再向失效副本派发，恢复后两台均可接单；worker 归因缺失作为证据限制记录。

## 验收与决策

| 门槛 | 结果 | 判定 |
| --- | --- | --- |
| B：缓存命中提高，续轮 P95 TTFT 下降 ≥15% | 缓存 0%→约 90%；P95 改善 79%–84%；4/4 组 | 通过 |
| C：高载 P99 回退 ≤10% | 合并 0.675×；4/4 组均低于基线 | 通过 |
| C：达标吞吐下降 ≤5% | 合并 1.561×；4/4 组均高于基线 | 通过 |
| C：失败/超时/拒绝增加 ≤1pp | 0；200/200 完成 | 通过 |
| 至少 3 组配对回放中的 2 组达标，且合并达标 | B/C 均 4/4，合并达标 | 通过 |
| D：热副本忙时改派到空闲副本 | `cache_aware` 双向可观察；`power_of_two` 无可持久热副本 | 通过（含基线限制） |
| E：检测窗口后不再派新请求，恢复后可接单 | 通过 | 两策略检测后新请求均成功并停止走向 `.16`；恢复后均能重新接单。`power_of_two` 的逐请求 worker 归因缺失。 |
| 测试后 Gemma 可从原入口调用 | 通过 | 直连和生产 Gateway 原入口均 200；Router 已移除，`.15` Qwen 仍健康。 |

**决策：推荐将 `cache_aware` 带到 GLM 多副本环境复测。** 本池的稳定前缀和混合峰值两项都达到预设门槛，且没有失败率回退；`power_of_two` 保留为基线。D 的改派会牺牲缓存，生产上应把 `balance-abs-threshold`/`balance-rel-threshold` 从本次的 2/1.2 作为起点重新按 GLM 池校准，而不是直接照搬。

## 测试后恢复

- `.16`：`spark-vllm-gemma4` 已恢复运行且 healthy，`/v1/models` 返回 `gemma-4-26b-a4b-nvfp4`、`max_model_len=262144`；短请求返回 200 “OK”。`.env` 已恢复为 `QWEN_PORT=18000`、`QWEN_MAX_MODEL_LEN=32768`、`GEMMA_PORT=8000`、`GEMMA_MAX_MODEL_LEN=262144`。
- Router：`vllm-router-test` 容器已移除；`spark-vllm-qwen36` 已停止。
- 生产入口：通过 `.15:4000` 原 Gateway 对 `gemma-4-26b-a4b-nvfp4` 发起短请求，HTTP 200、内容 “OK”。
- `.15`：原 Qwen 仍为 healthy，端口 8000 正常。

## 限制与证据说明

1. **前缀隔离方式**：vLLM 0.26 没有可用的 prefix-cache reset 接口（`/reset_prefix_cache` 404），无法在两组策略之间清空引擎 KV 缓存。为避免第一组策略的引擎缓存污染第二组，正式回放使用结构等价、仅带 2 字符策略标记（`p2`/`ca`）的前缀。请求字节不完全相同，但长度、内容结构和到达序列一致；这是本次对照的主要限制。
2. **模型顺序**：每台 Spark 独立运行，A/B 和 C 的策略执行顺序按配对轮换（p2→ca / ca→p2），但同一轮内仍是顺序执行；TTFT 是同一环境的相对对照，不是绝对性能基准。
3. **队列采样**：C 的队列峰值来自同速率冷前缀短扫，不是正式回放的逐请求采样；正式回放记录了 TTFT/完成时间/派发/达标吞吐，未逐请求记录 engine waiting。
4. **Router 负载信号**：Router 探测 `/get_load` 得到 404，vLLM 实际暴露 `/load`；Router 回退到本地 in-flight 计数，因此不能看到绕过 Router 的直接引擎负载。场景 D 的 `power_of_two` 结论受此限制。
5. **测试路径**：客户端直连 Router，没有经过 LiteLLM/Scheduler 的鉴权、队列和账本；生产 Gateway 的 Auto/Scheduler 未参与。计划允许保留 LiteLLM 但只转发到一个 Router 入口；本次选择直连以排除第二个选址组件。
6. **工作负载**：混合峰值是合成回放（40/30/30），不是生产流量录制；质量判定为预定义小样本，不代表业务准确率。
7. **Gemma 可用性**：`.16` Gemma 测试期间被替换为 Qwen，生产 Gemma 别名临时不可用；测试结束已恢复并验证。

## 原始数据与运行手册

- `.verification/dual-qwen/precheck.json`：两副本短问答/16K 冷热预检。
- `.verification/dual-qwen/router-results.jsonl`：A/B 与 C 的逐请求记录（含 TTFT、usage、质量、实际 worker/cache 增量）。
- `.verification/dual-qwen/ab-analysis.json`、`mixed-sessions.jsonl`、`mixed-analysis.json`：A/B 与 C 聚合。
- `.verification/dual-qwen/hot-replica-power_of_two.json`、`hot-replica-cache_aware.json`、`hot-replica-role-swap.json`：D 明细。
- `.verification/dual-qwen/failure-recovery-*.json`：E 明细。
- `.verification/dual-qwen/calibration.json`：冷前缀速率校准。
- `.verification/dual-qwen/run-router.sh`、`restore-gemma.sh`：Router 启停与 Gemma 恢复脚本（远端同路径）。
