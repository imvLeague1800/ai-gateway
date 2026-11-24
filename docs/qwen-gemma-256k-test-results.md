# Qwen 3.6 35B 与 Gemma 4 26B 256K 对照测试结果

测试日期：2026-11-20。执行环境为当前仓库的隔离测试网关（127.0.0.1:4400 → Scheduler 4401），模型为 Spark `.15` 的 Qwen3.6-35B-A3B-NVFP4 与 `.16` 的 Gemma-4-26B-A4B-NVFP4。未修改生产 Gateway、生产 Scheduler、模型副本数量或 `auto` 路由规则。

## 结论摘要

- 两台引擎均已生效 `max_model_len=262144`；本轮测试每次请求显式传 `chat_template_kwargs.enable_thinking=false`，并用渲染后的 token 串确认 thinking 关闭，响应 `reasoning_tokens=0`。
- 独立代码问答、多轮工具 Agent、16K 长文档/RAG 的确定性质量检查全部通过（每个模型每个并发档位至少 3 个样本；Agent/RAG 按阶段各 3 个）。
- 256K 边界：Qwen 最高成功 260851 tokens, TTFT 137.0 s, req 0e4e1562-45b1-49e0-a622-07186f71d2d1；Gemma 最高成功 260853 tokens, TTFT 607.8 s, req eaf14f22-5f86-4f9e-97a6-28659b9abe9c。超限请求在网关侧被 400 拒绝，未派发到引擎（引擎 prefix cache queries 无增量，响应无 backend 头）。
- 约 260K 输入、单并发、3 轮的 TTFT：Qwen 约 137.0 s，Gemma 约 607.8 s；两者质量检查均通过。
- 16K RAG 冷首轮 TTFT P50：Qwen 2687 ms，Gemma 4094 ms；输出 TPS P50：Qwen 95.8，Gemma 33.8。同文档追问在 Qwen 上命中约 89.5% 前缀缓存、TTFT 降至约 0.5 s；Gemma 第 1 轮追问命中 99.6%，第 2/3 轮仅约 0.2%（见“观察与限制”）。
- 召回内容变化会打断前缀缓存：Qwen 与 Gemma 的 changed_recall 缓存命中均接近 0%，TTFT 回到冷首轮水平。
- 网关行为：显式映射、流式、工具调用、并发排队、队列压满 429、流式断开、排队取消和 mock 后端 503 均已验证；流式断开按设计进入 `uncertain_requests`，在引擎确认停止后释放，最终 inflight 与引擎 running 恢复为 0。

## 方法与口径

- **入口**：LiteLLM 隔离网关 `qwen-256k` / `gemma-256k`，Scheduler 显式模型路由到单副本 `qwen-test-a` / `gemma-test-a`。流式调用，`temperature=0`，每次请求 `max_tokens=1024`（预留 1024 输出 token）。
- **重复**：QA、Agent、RAG 每个模型每个并发档位 3 轮；模型顺序为 Qwen 全量后 Gemma 全量，未严格逐轮交替（见限制）。256K 阶梯每档 1 次，最高成功档额外复测 2 次。
- **冷/热前缀**：Agent 每轮在 system 消息加入唯一 session tag；RAG/边界每轮在文档头加入唯一 batch salt，确保冷首轮前缀唯一；续轮复用同一前缀。缓存占比为请求前后 `vllm:prefix_cache_hits_total / vllm:prefix_cache_queries_total` 的增量；并发 >1 时指标为聚合值，表格标“未知（并发聚合）”。
- **TTFT**：请求开始到首个非空 content/tool_call 增量；**完成时间**：请求开始到流结束；**输出 TPS**：`completion_tokens / (完成时间 - TTFT)`，小样本仅作相对比较，P95 在 3 样本时接近最大值。
- **质量判定**：预定义答案正则/代码字符串；Agent 额外校验 `get_service_status` 工具名与 `checkout` 参数。边界质量要求准确返回注入的 access code。
- **隔离网关配置**：`max_context_tokens=262144`、`max_output_tokens=1024`、`safe_kv_tokens=262144`、`max_concurrent_requests=2`、`max_long_requests=2`、`MAX_ACTIVE_PER_PRINCIPAL=2`、`MAX_QUEUED_PER_PRINCIPAL=2`、`QUEUE_TIMEOUT_SECONDS=600`。生产配置未改动。

## 引擎与 thinking 验证

| 模型 | 端点 | served model | max_model_len | KV cache tokens | block size | prefix caching | off render tokens | off 渲染尾部 |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- |
| Qwen | `http://10.169.25.15:8000` | `qwen3.6-35b-a3b-nvfp4` | 262144 | 2295745 | 2096 | True | 16 | `"<|im_start|>user\u010aReply\u0120only\u0120OK.<|im_end|>\u010a<|im_start|>assistant\u010a<think>\u010a\u010a</think>\u010a\u010a"` |
| Gemma | `http://10.169.25.16:8000` | `gemma-4-26b-a4b-nvfp4` | 262144 | 2331955 | 16 | True | 17 | `"<bos><|turn>user\nReply\u2581only\u2581OK.<turn|>\n<|turn>model\n<|channel>thought\n<channel|>"` |

thinking 关闭的模板证据：

- Qwen：`enable_thinking=false` 时在 assistant 生成提示末尾渲染 `<think>\n\n</think>\n\n`；`true` 时只渲染 `<think>\n`。
- Gemma：`enable_thinking=false` 时渲染 `<|channel>thought\n<channel|>`（空 thought 通道即关闭）；`true` 时在首个 system turn 注入 `<|think|>`。
- 两台在 `enable_thinking=false` 下的短生成均无 `reasoning_content`，usage 的 `completion_tokens_details.reasoning_tokens=0`。

## 结果表

| 场景 | 实际模型和副本 | 并发 | 样本数 | 输入/输出 token | 质量通过 | TTFT P50/P95 (ms) | 完成 P50/P95 (ms) | 输出 TPS | 缓存 token 占比 | 错误备注 |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 独立代码问答 / 3 题合计 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 3 | 28–40 / 2–4 | 3/3 | 109 / 125 | 203 / 218 | 43.0 | 0.0% (3) | - |
| 独立代码问答 / 3 题合计 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 2 | 3 | 28–40 / 2–4 | 3/3 | 125 / 156 | 219 / 250 | 42.5 | 未知（并发聚合） | - |
| 独立代码问答 / 3 题合计 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 3 | 27–39 / 2–4 | 3/3 | 109 / 109 | 156 / 156 | 85.1 | 0.0% (3) | - |
| 独立代码问答 / 3 题合计 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 2 | 3 | 27–39 / 2–4 | 3/3 | 157 / 157 | 172 / 188 | 129.0 | 未知（并发聚合） | - |
| 多轮编码 Agent / 冷首轮 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 3 | 120–123 / 17 | 3/3 | 422 / 422 | 656 / 657 | 72.7 | 0.0% (3) | - |
| 多轮编码 Agent / 冷首轮 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 2 | 3 | 120–123 / 17 | 3/3 | 406 / 468 | 625 / 672 | 83.3 | 未知（并发聚合） | - |
| 多轮编码 Agent / 冷首轮 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 3 | 327–329 / 27 | 3/3 | 328 / 328 | 563 / 563 | 114.9 | 0.0% (3) | - |
| 多轮编码 Agent / 冷首轮 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 2 | 3 | 326–329 / 27 | 3/3 | 500 / 516 | 797 / 812 | 91.2 | 未知（并发聚合） | - |
| 多轮编码 Agent / 稳定前缀续轮（错误率） | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 3 | 200–203 / 5 | 3/3 | 125 / 140 | 265 / 265 | 40.0 | 47.5% (3) | - |
| 多轮编码 Agent / 稳定前缀续轮（错误率） | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 2 | 3 | 200–203 / 5 | 3/3 | 141 / 187 | 297 / 312 | 35.5 | 未知（并发聚合） | - |
| 多轮编码 Agent / 稳定前缀续轮（错误率） | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 3 | 412–414 / 5 | 3/3 | 234 / 250 | 297 / 297 | 106.4 | 0.0% (3) | - |
| 多轮编码 Agent / 稳定前缀续轮（错误率） | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 2 | 3 | 411–414 / 5 | 3/3 | 328 / 375 | 453 / 453 | 64.1 | 未知（并发聚合） | - |
| 多轮编码 Agent / 稳定前缀续轮（版本） | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 3 | 222–225 / 6 | 3/3 | 94 / 109 | 266 / 266 | 34.9 | 85.7% (3) | - |
| 多轮编码 Agent / 稳定前缀续轮（版本） | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 2 | 3 | 222–225 / 6 | 3/3 | 109 / 140 | 281 / 296 | 34.9 | 未知（并发聚合） | - |
| 多轮编码 Agent / 稳定前缀续轮（版本） | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 3 | 434–436 / 6 | 3/3 | 234 / 235 | 297 / 313 | 95.2 | 0.0% (3) | - |
| 多轮编码 Agent / 稳定前缀续轮（版本） | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 2 | 3 | 433–436 / 6 | 3/3 | 344 / 344 | 422 / 469 | 76.9 | 未知（并发聚合） | - |
| 长文档/RAG / 同文档首轮 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 3 | 16352–16355 / 9 | 3/3 | 4094 / 4172 | 4344 / 4438 | 33.8 | 0.0% (3) | - |
| 长文档/RAG / 同文档首轮 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 2 | 3 | 16354–16355 / 9 | 3/3 | 4250 / 8078 | 8109 / 8344 | 31.9 | 未知（并发聚合） | - |
| 长文档/RAG / 同文档首轮 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 3 | 16352–16383 / 9 | 3/3 | 2687 / 2734 | 2781 / 2844 | 95.8 | 0.0% (3) | - |
| 长文档/RAG / 同文档首轮 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 2 | 3 | 16381–16383 / 9 | 3/3 | 4671 / 5172 | 5313 / 5578 | 63.8 | 未知（并发聚合） | - |
| 长文档/RAG / 同文档追问 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 3 | 16382–16385 / 9 | 3/3 | 4110 / 4110 | 4375 / 4375 | 34.0 | 0.2% (3) | - |
| 长文档/RAG / 同文档追问 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 2 | 3 | 16384–16385 / 9 | 3/3 | 4297 / 7922 | 8172 / 8188 | 33.8 | 未知（并发聚合） | - |
| 长文档/RAG / 同文档追问 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 3 | 16382–16413 / 9 | 3/3 | 515 / 547 | 609 / 656 | 82.6 | 89.6% (3) | - |
| 长文档/RAG / 同文档追问 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 2 | 3 | 16411–16413 / 9 | 3/3 | 703 / 890 | 1047 / 1047 | 57.3 | 未知（并发聚合） | - |
| 长文档/RAG / 召回内容变化 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 3 | 16352–16355 / 9 | 3/3 | 4094 / 4375 | 4375 / 4641 | 32.0 | 0.2% (3) | - |
| 长文档/RAG / 召回内容变化 | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 2 | 3 | 16354–16355 / 9 | 3/3 | 4219 / 7860 | 8094 / 8125 | 32.0 | 未知（并发聚合） | - |
| 长文档/RAG / 召回内容变化 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 3 | 16352–16383 / 9 | 3/3 | 2687 / 2953 | 2781 / 3062 | 82.6 | 0.0% (3) | - |
| 长文档/RAG / 召回内容变化 | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 2 | 3 | 16381–16383 / 9 | 3/3 | 3906 / 5203 | 5328 / 5344 | 72.0 | 未知（并发聚合） | - |
| 256K 边界 / 阶梯 32768 tokens | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 1 | 32498 / 8 | 1/1 | 13031 / 13031 | 13250 / 13250 | 36.5 | 0.1% (1) | - |
| 256K 边界 / 阶梯 32768 tokens | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 1 | 32495 / 9 | 1/1 | 5922 / 5922 | 6016 / 6016 | 95.8 | 0.0% (1) | - |
| 256K 边界 / 阶梯 65536 tokens | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 1 | 65255 / 8 | 1/1 | 44516 / 44516 | 44750 / 44750 | 34.2 | 0.1% (1) | - |
| 256K 边界 / 阶梯 65536 tokens | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 1 | 65254 / 9 | 1/1 | 14828 / 14828 | 14922 / 14922 | 95.8 | 0.0% (1) | - |
| 256K 边界 / 阶梯 131072 tokens | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 1 | 130805 / 9 | 1/1 | 162188 / 162188 | 162453 / 162453 | 34.0 | 0.0% (1) | - |
| 256K 边界 / 阶梯 131072 tokens | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 1 | 130801 / 10 | 1/1 | 42203 / 42203 | 42313 / 42313 | 90.9 | 0.0% (1) | - |
| 256K 边界 / 阶梯 196608 tokens | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 1 | 196322 / 9 | 1/1 | 353266 / 353266 | 353485 / 353485 | 41.1 | 0.0% (1) | - |
| 256K 边界 / 阶梯 196608 tokens | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 1 | 196321 / 10 | 1/1 | 83094 / 83094 | 83219 / 83219 | 80.0 | 0.0% (1) | - |
| 256K 边界 / 阶梯 245760 tokens | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 1 | 245473 / 9 | 1/1 | 542125 / 542125 | 542313 / 542313 | 47.9 | 0.0% (1) | - |
| 256K 边界 / 阶梯 245760 tokens | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 1 | 245473 / 10 | 1/1 | 122625 / 122625 | 122735 / 122735 | 90.9 | 0.0% (1) | - |
| 256K 边界 / 261120 tokens（3 轮） | gemma-4-26b-a4b-nvfp4 / gemma-test-a / 10.169.25.16 | 1 | 3 | 260848–260853 / 9 | 3/3 | 607766 / 607859 | 607938 / 608047 | 47.9 | 0.0% (3) | - |
| 256K 边界 / 261120 tokens（3 轮） | qwen3.6-35b-a3b-nvfp4 / qwen-test-a / 10.169.25.15 | 1 | 3 | 260846–260851 / 10 | 3/3 | 137016 / 137031 | 137093 / 137141 | 106.4 | 0.0% (3) | - |

> 输入/输出 token 为成功样本的 usage 范围；QA 每个并发档位合并 3 道题；边界阶梯每档 1 次，261120 行合并 1 次阶梯 + 2 次复测共 3 个样本。并发 >1 的缓存列因引擎指标为聚合增量，未做单请求归因。

## 网关行为验证清单

| 模型 | 4 并发排队状态 | queue_wait (s) | 5 并发压满状态 | 流式断开确认后 inflight | 排队取消队列 1→0 | mock 503 |
| --- | --- | --- | --- | ---: | --- | ---: |
| qwen-256k | [200, 200, 200, 200] | 8.422, 0.0, 0.0, 8.391 | [200, 200, 200, 200, 429] | 0 | 1.0→0.0 | 503 |
| gemma-256k | [200, 200, 200, 200] | 0.0, 14.891, 14.828, 0.0 | [429, 200, 200, 200, 200] | 0 | 1.0→0.0 | 503 |

- [x] 两台引擎和网关的 256K 配置均生效，thinking 均已关闭。 — **通过**：引擎 /v1/models 均返回 262144；`/tokenize` 渲染模板分别出现 Qwen `<think>\n\n</think>` 和 Gemma `<|channel>thought\n<channel|>`；响应 reasoning_tokens=0。
- [x] 显式模型映射正确，流式响应和工具调用可用。 — **通过**：qwen 请求 079d038c-9715-42ff-9918-cb86ae1170d0 → qwen-test-a；gemma 请求 e2d08503-777c-4b4f-9fc6-b08ab14c89d2 → gemma-test-a；工具调用请求 1abf61fa-e54d-4b0c-8f64-66a6e5302db5 / 03ab0a7b-bf5a-4fec-bc54-dda11e4b1b0a。
- [x] 近上限请求通过，或记录资源限制。超限请求被网关拒绝且未派发。 — **通过**：最高成功：qwen 260851 tokens, TTFT 137.0 s, req 0e4e1562-45b1-49e0-a622-07186f71d2d1；gemma 260853 tokens, TTFT 607.8 s, req eaf14f22-5f86-4f9e-97a6-28659b9abe9c。超限请求 4783fa47-f6e1-4e6d-89cd-d68650dc4692 返回 400，prompt=261271，max_tokens=1024，错误信息为上下文超限，engine cache queries delta=0.0，无 backend 头。
- [x] 达到并发上限后，排队、超时或拒绝符合配置。已完成请求释放容量。 — **通过**：4 并发请求状态 [200, 200, 200, 200]，queue_wait=['8.422', '0.0', '0.0', '8.391']；5 并发压满状态 [200, 200, 200, 200, 429]；完成后 inflight=0/0。600 秒队列超时未单独触发，以 queue_full 拒绝验证。
- [x] 排队取消和流式断开后，检查队列项、引擎状态和预留额度是否恢复。 — **通过**：流式断开 cb2e26b1-dfed-4323-b7ce-eb421f213207 设计上进入 uncertain_requests（safe_kv_tokens + 上游未正常结束）；引擎 running=0 后调用 /admin/confirmed-stopped 释放，inflight=0。排队取消（clean rerun）队列 1.0→0.0，inflight_after=0。
- [x] 稳定前缀续轮和 RAG 内容变化的缓存表现有记录。 — **通过**：Qwen 续轮 5abc3f8c-53e6-41fb-a01a-da31249b746a / 变化 03de18b7-27e2-431c-a471-7062da84fceb；Gemma 续轮 5508226f-9402-4d01-8cad-1a0c7f461005 / 变化 bd317b01-d98b-4e03-8be8-249efe267f1c。详见结果表缓存列。
- [x] 显式模型不可用时的行为已验证，并标明使用实机、mock 还是隔离故障注入。 — **通过**：使用隔离 mock（/health 正常、推理返回 503），请求 4388f25d-23d1-409a-8dfb-db0d2df4f76d 返回 503，backend=mock-down-a，inflight_after=0。

## 观察与限制

1. **Gemma RAG 续轮缓存异常**：第 1 轮同文档追问命中 16,320/16,382 tokens（99.6%），但第 2/3 轮追问仅命中 32 tokens（0.2%）。首轮冷 prefill 与追问使用完全相同的前缀，理论上应复用；该现象需要对 Gemma 的 prefix cache 保留/淘汰行为单独复现，当前不将其解释为模型能力差异。
2. **Qwen 短前缀不命中**：Qwen cache block size 为 2,096 tokens，Agent 的 system+tools+user 约 300–450 tokens，不足一个 block，因此 Agent 续轮缓存为 0%；其 16K RAG 续轮可稳定命中约 89.5%。
3. **召回内容变化**：changed_recall 在 Qwen/Gemma 上均接近 0% 命中且 TTFT 回到冷首轮，说明当前 vLLM 前缀缓存对该变更位置之后的块没有复用；测试仍返回了变更后的正确 code。
4. **并发缓存**：并发档位的缓存占比为引擎级聚合增量，无法归因到单个请求，表格已标“未知（并发聚合）”。
5. **模型顺序**：受两机独立运行限制，本轮按 Qwen 全量、Gemma 全量执行，未做到逐轮交替；TTFT/TPS 受运行窗口、缓存状态和网络影响，属于同环境相对对照，不是绝对性能基准。
6. **队列超时**：`QUEUE_TIMEOUT_SECONDS=600` 未单独触发；以“5 并发压满时第 5 个请求返回 429 queue_full”验证拒绝路径，排队与释放路径由 4 并发请求验证。
7. **流式断开的预算保留**：配置 `safe_kv_tokens` 时，客户端断开且上游未正常结束会按设计进入 `uncertain_requests`，保留并发槽和 KV 预算；本次在引擎 `requests_running=0` 后调用 `/admin/confirmed-stopped/{request_id}` 释放，inflight 恢复 0。生产上需要把“引擎确认停止”纳入自动流程或排水流程。
8. **不可用后端**：使用隔离 mock（`/health` 正常、`/v1/chat/completions` 返回 503），未停止真实 Spark 引擎；验证了 503 返回、backend 头和容量释放，不代表真实引擎崩溃时的全部行为。
9. **质量范围**：质量判定为预定义小样本测试，不代表模型在真实业务数据集上的准确率；长上下文边界质量只验证了注入 code 的检索。

## 原始数据

- `.verification/256k/results.jsonl`：逐请求原始记录（含 request_id、backend_id、TTFT、usage、缓存增量、质量判定）。
- `.verification/256k/summary-phases.json`：按模型/场景/阶段/并发聚合。
- `.verification/256k/verification.json`：引擎配置与 thinking 渲染证据。
- `.verification/256k/gateway-checks-qwen-256k.json`、`gateway-checks-gemma-256k.json`：网关行为检查明细。
- `.verification/256k/queue-cancel-clean.json`：干净状态下的排队取消复测。
- `.verification/256k/run_256k_tests.py`：测试 harness；`.verification/router-v2/backends.live.yaml`、`litellm.host.yaml`：隔离网关配置。
