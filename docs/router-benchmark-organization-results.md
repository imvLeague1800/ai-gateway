# Router benchmark 整理结果

执行日期：2026-11-24。计划：[router-benchmark-organization-plan.md](router-benchmark-organization-plan.md)；手动演练规范：[router-fault-drill-authorization.md](router-fault-drill-authorization.md)。

## 1. 入口收敛

| 组 | 入口 | 说明 |
| --- | --- | --- |
| 日常 Smoke | `benchmarks/router/run_smoke.py` | 默认候选策略 16 会话/28 请求和一次有界队列/取消；不切换代理健康状态、不停止 Qwen、不包含 1–4 路矩阵。 |
| 日常 Stress | `benchmarks/router/run_stress.py` | 按 `--modes replay concurrency cold-rag` 选择；无故障模式，不停止任何引擎。 |
| 手动实机故障演练 | `benchmarks/router/run_fault_drill.py` | 仅在用户明确要求时执行；只停启 `.16` Qwen，支持 `--cycles` 单次/多次，默认 `cache_aware`；预检只读，不推断授权，`--preflight-only` 不启动、不删除、不停止任何组件。 |

Smoke 可选 `--baseline` 在相同子集跑另一策略，结束后在 `finally` 中恢复候选策略；`--no-restart` 用于调用方已启动 Router 的场景。整组写入 `.verification/dual-qwen/<run-id>/`，并生成 `summary.json`、各阶段日志和 `analysis/smoke-analysis.json`。摘要只包含非故障阶段；没有 `failover` 键，也不会调用代理脚本。

压测下各场景保留独立入口：

```bash
python run_retest.py --run-id <id> --output <dir>/replay
python run_smoke_concurrency.py --run-id <id> --output <dir>/concurrency
python cold_rag/run_cold_rag_matrix.py --run-id <id> --output <dir>/cold-rag
```

手动实机故障演练：完整运行需要 `--maintenance-window-ack`；`--preflight-only` 是只读检查，不需要该确认。

```bash
python run_fault_drill.py --preflight-only --run-id <id>
python run_fault_drill.py --cycles 1 --maintenance-window-ack --run-id <id>
python run_fault_drill.py --cycles 3 --maintenance-window-ack --run-id <id>
```

预检只做只读检查：`dgx@10.169.25.16` 批量登录与执行账号（`id -un`）、`/home/dgx/spark-vllm` Compose 项目下 `qwen` 服务的标签与运行状态、两台引擎直连 health、`.16` 模型身份，以及隔离 Router 的就绪状态和 policy。仅当基础只读检查全部通过后，完整运行才在需要时启动 Router；已有同 policy 的就绪 Router 直接复用。预检不运行 Compose dry-run，不做 Docker socket/组或 sudo 推断，没有 `--authorization-record` 分支。任一检查失败时退出码 2，不进入周期、不启动或删除 Router、不发出真实 stop/start。

压测回放失败不会被吞掉：`run_retest.py` 把失败轮的组号、场景、策略、原始退出码和日志路径写入 `run-index.jsonl` 并最终返回非零；`run_stress.py` 将 `replay` 段和整组标记为失败并返回非零，摘要保留 `replay` 退出码、日志和索引路径。该路径本轮没有实跑，详见 2d 节。

## 2. 日常 smoke 验收（smoke-daily-v6，当前）

运行命令（从 benchmark 目录，manifest v1.3.0 `9590ba02…`）：

```bash
python run_smoke.py --policy cache_aware --run-id smoke-daily-v6
```

| 阶段 | 耗时 | 结果 |
| --- | ---: | --- |
| router_ready | 0.94 s | 隔离 Router 就绪，两台引擎直连 health 200 |
| core | 55.35 s | 28/28 success、协议 28、质量 28；`policy_ok=true`；召回 4/4；缓存命中 92,224 / 336,848 tokens（0.2738） |
| queue | 33.75 s | 12 个长请求、取消 2 个（`queue-0`、`queue-11`）、完成 10 个（全部 success）；在途计数 0.05 s 恢复 |
| analysis | 0.25 s | 生成 `smoke-analysis.json` |
| **合计** | **90.30 s** | 低于 10 分钟目标；顶层 `pass=true`，退出码 0 |

本次运行没有 `failover` 键，没有代理健康切换，也没有停止 Qwen；两台引擎直连在结束时均为 200，Router 在途计数归零。`core.both_workers_routed=false` 与上一轮 v5 相同，不影响 `policy_ok` 和顶层通过。

证据：

- 顶层：`.verification/dual-qwen/smoke-daily-v6/summary.json`
- 登记：`benchmarks/router/runs/smoke-daily-v6/`

### 历史 smoke 验收（v5 / v4 / v3）

| run ID | manifest | 合计 | 结果 |
| --- | --- | ---: | --- |
| `smoke-daily-v5` | v1.2.9 `d1cffb2a…` | 88.42 s | core 28/28，queue 2 取消/10 完成，退出码 0 |
| `smoke-daily-v4` | v1.2.9 中间版本 | 92.07 s | core 28/28，queue 2 取消/10 完成，退出码 0 |
| `smoke-daily-v3` | v1.2.8 | 87.18 s | core 28/28，queue 2 取消/10 完成，退出码 0 |

`smoke-daily-v1`（91.31 s）和 `smoke-daily-v2`（88.08 s）同样保留在 `runs/`。这些运行在代理/替身用例删除之前，登记内容保持不变。

## 2b. 手动演练只读预检验收（preflight-20261124）

命令：

```bash
python run_fault_drill.py --preflight-only --run-id preflight-20261124-a
python run_fault_drill.py --preflight-only --run-id preflight-20261124-b
```

| run ID | 运行前 Router | 退出码 | 总耗时 | 停/启/删 Router |
| --- | --- | ---: | ---: | --- |
| `preflight-20261124-a` | 未就绪 | 2 | 4.44 s | 0 / 0 / 0 |
| `preflight-20261124-b` | 就绪，`cache_aware`，容器 `40d86e0a8b4e` | 0 | 3.29 s | 0 / 0 / 0 |

两次运行均确认：`dgx@10.169.25.16` 登录与 `id -un`=`dgx`；Compose 项目 `/home/dgx/spark-vllm`、服务 `qwen`、状态 `running`（容器 `535c2a8243d4…`）；两台引擎直连 `{"15": 200, "16": 200}`；`.16` 模型 `qwen3.6-35b-a3b-nvfp4`。Run A 因 Router 未就绪而拒绝启动它，退出码 2；Run B 找到同 policy 的就绪 Router 并通过。两次都未向 `actual_engines_stopped` 追加 `.16`，也未尝试删除 Router。

证据：

- 原始：`.verification/dual-qwen/preflight-20261124-a/summary.json`、`.verification/dual-qwen/preflight-20261124-b/summary.json`
- 登记：`benchmarks/router/runs/preflight-20261124/`

## 2c. 历史手动实机故障演练（fault-20261124-1x-r2，通过）

该轮在 2026-11-24 由用户明确要求 1 个停启周期并确认维护窗口；只停启 `.16` 的 Qwen 服务，`.15` 与网关持续运行；隔离 Router 使用 `cache_aware`、两台直连 worker、健康检查 5 s/2/2，周期内保持同一容器 `eacbab0d9872`。

```bash
python run_fault_drill.py --cycles 1 --maintenance-window-ack --run-id fault-20261124-1x-r2
```

该轮预检用的是已被现行规范删除的旧方法（Compose dry-run + 对运行中容器的一次真实 `docker start`）；它不发出真实 stop，也不代表现行预检。现行预检只做只读检查，见第 1 节和 2b 节。

| 项目 | 实测 | 判定 |
| --- | ---: | --- |
| 停止前 `.16` 经 Router 完整响应 | 20/20 探针由 `.16` 返回，首条 0.54 s | 通过 |
| 停止命令 / 直连失败 | 11.12 s / 2.85 s | — |
| Router 摘除（自停止命令起算） | 8.93 s | ≤20 s |
| 摘除后探针 | 20/20 由 `.15` 完整返回 | 通过 |
| 在途 4 条长请求 | 2 条 HTTP 200 后断流，2 条 `truncated`（`finish_reason=length`） | 按实际状态记录 |
| 启动→首次直连 200 | 197.49 s（引擎加载） | — |
| 直连 200→Router 重新接纳 | 18.83 s | — |
| **直连 200→首条完整 `.16` 响应结束** | **19.469 s**（09:42:01.702Z → 09:42:21.171Z） | **≤20.00 s 通过** |
| 首条恢复响应 | 请求 `50a06a4b32d74562a81afb64873e756a`，worker `http://10.169.25.16:8000`，`done=true`，`finish_reason=stop` | 通过 |
| 同一 Router 容器 | `eacbab0d9872` 保持不变 | 通过 |
| 结束状态 | 两台直连 200，`.16` 经 Router 接单，隔离 Router 已移除 | 通过 |

演练返回码 0，`summary.json` 的 `pass=true`、`cycles_completed=1`、`actual_engines_stopped=[".16"]`。登记副本在 `benchmarks/router/runs/fault-20261124-1x-r2/`，原始证据在 `.verification/dual-qwen/fault-20261124-1x-r2/summary.json` 和 `cycle-01.json`。

首次尝试 `fault-20261124-1x` 在构造周期记录时报 `KeyError: 'router_container_id'`（周期上下文键名不一致），**在发出停止命令之前**中止：`actual_engines_stopped=[]`，两台引擎最终 200，隔离 Router 已移除。修复后以新 run ID 重跑并通过；该失败运行保留在 `.verification` 下，不作为验收证据。

**现行预检下的实机周期尚未执行。** 按计划第 4 节，未收到停机指令时不进行新的停机演练；因此多周期和现行只读预检 + 真实停启的组合记为待执行，r2 的实测数字仍作为上一版预检的通过记录。

## 2d. 压测回放失败向压测入口的传递（实现已保留，未实跑）

- `run_retest.py`：任一轮 `replay.py` 子进程非零退出时，立即把该轮的 `group`、`profile`、`policy`、`returncode` 和 `log` 写入本轮 `run-index.jsonl`；后续轮次继续执行，已发生的失败不被后续成功覆盖；全部轮次结束后若有失败，打印失败汇总并返回退出码 1。
- `run_stress.py`：顶层摘要包含 `replay` 段（`pass`、`returncode`、`log`、`run-index.jsonl` 路径）；回放失败时该段 `pass=false`，整组 `pass=false`，进程返回退出码 1；成功时 `pass=true`、退出码 0。
- 本轮没有运行 replay/concurrency/cold-rag 压测，也没有真实制造回放失败。实现路径通过代码审查确认；原先覆盖该路径的替身用例已按计划第 4 节删除，因此当前没有自动化用例重放这一行为。

## 3. 历史代理与 mock 归档

代理和 mock 故障路径已从执行面移除：`benchmark_proxy.py`、`proxy_failover.py`、`smoke_failover.py`、`test_smoke_proxy.py` 已删除，日常 smoke/stress 不再调用代理，`config/`、`scheduler/`、`app/` 没有代理依赖。以下仅是 `runs/` 中的历史结果，不是当前验收路径，也不应再被执行：

- `runs/smoke-proxy-v12/`：代理路径历史通过记录，Router 摘除 5.60 s、重新接纳 6.75 s、首条 `.16` 完整响应 7.34 s，请求 ID `5d8abfe9d2ca401e963c80f74daee66d`，121.54 s。
- `runs/smoke-proxy-v10/`、`runs/smoke-proxy-v6/`：更早的代理验收。
- `runs/smoke-entry-v1/`：旧 mock 故障阶段记录，87.80 s。

这些目录保留原始摘要、请求记录、分析重建和 SHA-256 清单，便于审计历史结论；脚本与用例不再随仓库提供。

## 4. 结果去重与离线重建

删除根目录副本前重新比较了 SHA-256，四个文件均与归档一致：

| 根目录文件（已删除） | SHA-256 | 唯一归档 |
| --- | --- | --- |
| `smoke-analysis.json` | `d42628c426e12176aa6f4989b0bac60960a3c405397b8e5fce6353a8f6172ede` | `runs/smoke-v1/analysis/smoke-analysis.json` |
| `retest-analysis.json` | `a92772d9ef0ebb79f1b24e30929a3cd94db2f730bde05d055c18aa36c411fa05` | `runs/retest-v12/analysis/retest-analysis.json` |
| `fault-analysis.json` | `8146d890b165f352faafaab38d64ae37af888cc327460ac140a3579fb6f6726b` | `runs/retest-v12/analysis/fault-analysis.json` |
| `cold-rag-analysis.json` | `3d83fc566685e0d81b35053cf4640b2ffdf2706e7ef1586d4e09ac90bd8d2ee3` | `runs/cold-rag-v1/analysis/cold-rag-analysis.json` |

分析入口使用 `--input` / `--output`，不再写根目录；登记入口要求 `--run-id`，结果写到 `runs/<run-id>/`。`runs/retest-v12/analysis/request-digests.jsonl` 保存每请求的 `{run, state, ttft_ms}`，使 retest 的合并 p99 等指标无需原始 requests 也能重建。

离线重建检查（`benchmarks/router/verify_archives.py`）在 v1.3.0 上复跑通过（退出码 0）：

| 归档 | 重建结果 |
| --- | --- |
| smoke | 字节完全一致，`d42628c4…6172ede` |
| retest | 字节完全一致，`a92772d9…c411fa05` |
| cold-rag | 字节完全一致（使用原 matrix 目录前缀重写诊断路径），`3d83fc56…8d2ee3` |
| fault | 12 个周期的检测/恢复/通过字段一致；新分析增加了 preflight 和 `.16` 首条成功证据字段，因此不要求整文件字节一致 |

验证输出：`.verification/dual-qwen/archive-verification.json`。

## 5. 保留、移除与路径

日常保留入口：

- `run_smoke.py`（无故障 smoke）
- `run_stress.py`、`run_retest.py`、`run_smoke_concurrency.py`
- `analyze_smoke.py`、`analyze_retest.py`、`analyze_fault.py`
- `register_smoke_evidence.py`、`cold_rag/register_cold_rag_evidence.py`
- `verify_archives.py`

手动保留入口：

- `run_fault_drill.py`（唯一实机停启入口，固定 `.16`；完整运行需要 `--maintenance-window-ack`，`--preflight-only` 只读）

历史保留（仅归档，不再提供脚本）：

- `runs/smoke-proxy-v6/`、`runs/smoke-proxy-v10/`、`runs/smoke-proxy-v12/`、`runs/smoke-entry-v1/`
- `runs/fault-20261124-1x-r2/`（旧预检方法的实机周期）
- `runs/smoke-daily-v1/` … `v5/`、`runs/smoke-v1/`、`runs/retest-v12/`、`runs/cold-rag-v1/`

移除：

- `benchmark_proxy.py`、`proxy_failover.py`、`smoke_failover.py`、`test_smoke_proxy.py`（代理/mock 故障实现与入口）。
- `test_run_smoke.py`、`test_fault_drill.py`、`test_run_stress.py`（不连接服务的替身用例）。
- `test_harness.py`、`test-results.json`（harness mock 检查及其生成结果）。
- 根目录四份重复分析 JSON（仅副本，归档保留）。
- `fault_retest.py`、`run_fault_matrix.py`、`smoke_failure.py`（旧实机停启脚本）。
- 日常 smoke 和 stress 的实机停启与代理故障调用。

结果路径：

- 当前日常 smoke：`benchmarks/router/runs/smoke-daily-v6/`。
- 只读预检：`benchmarks/router/runs/preflight-20261124/`。
- 手动实机演练（历史）：`benchmarks/router/runs/fault-20261124-1x-r2/`。
- 当前 manifest v1.3.1：`677ec27d8bca5edd0e41a8a797474d01e2138cb0f0e1765a89cd50560ccf51de`（32 个条目；删除 7 个代理/替身文件以及 `test_harness.py`/`test-results.json`，重算 `README.md` 与 `run_fault_drill.py` 哈希；数据集、校准、profiles 与 budgets 未变）。
- 当前 `smoke.json`：`912bd6ff84eddbc6992ff3479c9d5673a046280a40ff4e04865f4ba6e60e1786`（只更新 `source_manifest_sha256` 指向 v1.3.1；历史 smoke 证据保留当时记录的旧哈希）。

### 代码与数据检查（本轮复跑）

| 检查 | 结果 |
| --- | --- |
| `py_compile` run_fault_drill / run_smoke / run_stress / run_retest | 退出码 0 |
| `replay.py validate` | 300 cases / 195 sessions，退出码 0 |
| `verify_archives.py` | `pass=true`，退出码 0 |
| manifest 32 条目哈希核对 | 无缺失、无不一致 |
| `smoke.json` → manifest 哈希 | 一致 |

## 6. 未验证事项汇总

1. 现行只读预检只做了 `--preflight-only` 实跑（2b 节）；现行预检 + 真实停启周期的组合尚未执行，多周期仍未执行，SSH 中断、启动失败、恢复未确认等异常路径仍未在真实演练中制造。r2 的单周期通过记录使用旧预检方法，且 19.469 s 距 20.00 s 上限很近，样本偏差未知。
2. 压测未实跑：全量 replay、1–4 路并发和长冷 RAG 本轮均未运行；回放失败传递只有代码路径，没有真实失败注入，也没有替身用例回归。
3. 历史代理路径的实测结论只对应归档，脚本已删除；代理清理失败、SSH 停止检查失败和 attach 恢复失败不再有离线用例覆盖。
4. `runs/smoke-daily-v6/` 的 core `both_workers_routed=false` 与 v5 相同，未额外调查；日常 smoke 的通过条件不要求该字段。
5. real-Agent 会话仍为 0，相关验收保持 pending。
