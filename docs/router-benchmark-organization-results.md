# Router benchmark 整理结果

执行日期为 2026-11-24。现行入口见 [benchmark 使用说明](../benchmarks/router/README.md)，手动演练见 [实机演练说明](router-fault-drill-authorization.md)。

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

压测回放失败不会被吞掉：`run_retest.py` 把失败轮的组号、场景、策略、原始退出码和日志路径写入 `run-index.jsonl` 并最终返回非零；`run_stress.py` 将 `replay` 段和整组标记为失败并返回非零，摘要保留 `replay` 退出码、日志和索引路径。本轮压测全部成功，该失败路径未被实际触发，详见 2d 节。

## 2. 日常 smoke 验收（smoke-daily-v8，当前）

运行命令（从 benchmark 目录，manifest v1.3.3 `c13b57bb…`）：

```bash
python run_smoke.py --policy cache_aware --run-id smoke-daily-v8
```

| 阶段 | 耗时 | 结果 |
| --- | ---: | --- |
| router_ready | 1.09 s | 隔离 Router 就绪，两台引擎直连 health 200 |
| core | 61.66 s | 28/28 success、协议 28、质量 28；`policy_ok=true`；召回 4/4；缓存命中 92,224 / 336,848 tokens（0.2738） |
| queue | 33.69 s | 12 个长请求、取消 2 个（`queue-0`、`queue-11`）、完成 10 个（全部 success）；在途计数 0.29 s 恢复 |
| analysis | 0.26 s | 生成 `smoke-analysis.json` |
| **合计** | **96.72 s** | 低于 10 分钟目标；顶层 `pass=true`，退出码 0 |

本次运行没有 `failover` 键，没有代理健康切换，也没有停止 Qwen；两台引擎直连在结束时均为 200，Router 在途计数归零。`core.both_workers_routed=false` 与 v5–v7 相同，不影响 `policy_ok` 和顶层通过。

证据：

- 顶层：`.verification/dual-qwen/smoke-daily-v8/summary.json`
- 登记：持久归档 `current-acceptance/smoke-daily-v8/`

### 历史 smoke 验收（v7 / v6 / v5 / v4 / v3）

| run ID | manifest | 合计 | 结果 |
| --- | --- | ---: | --- |
| `smoke-daily-v7` | v1.3.2 `67d06787…` | 89.55 s | core 28/28，queue 2 取消/10 完成，退出码 0 |
| `smoke-daily-v6` | v1.3.0 `9590ba02…` | 90.30 s | core 28/28，queue 2 取消/10 完成，退出码 0 |
| `smoke-daily-v5` | v1.2.9 `d1cffb2a…` | 88.42 s | core 28/28，queue 2 取消/10 完成，退出码 0 |
| `smoke-daily-v4` | v1.2.9 中间版本 | 92.07 s | core 28/28，queue 2 取消/10 完成，退出码 0 |
| `smoke-daily-v3` | v1.2.8 | 87.18 s | core 28/28，queue 2 取消/10 完成，退出码 0 |

`smoke-daily-v1`（91.31 s）和 `smoke-daily-v2`（88.08 s）已迁出 Git 树，持久归档在 `X:\damonlu\ai-gateway\router-runs-archive\20261125`（按 run ID 定位，见第 5 节）。

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
- 登记：持久归档 `current-acceptance/preflight-20261124/`

## 2c. 新版只读预检 + 实机停启周期（fault-20261124-newpreflight，通过）

用户于 2026-11-24 要求新入口进入实测阶段；执行 1 个停启周期。只停启 `.16` 的 Qwen 服务，`.15` 与网关持续运行。Router 由前序压测留下、已就绪且 policy 为 `cache_aware`（容器 `939384f98d24`），预检确认后直接复用（`started_by_drill=false`、`reused=true`），周期内保持不变，演练结束也未删除。

```bash
python run_fault_drill.py --cycles 1 --maintenance-window-ack --run-id fault-20261124-newpreflight
```

预检（只读，无 Compose dry-run、无授权推断、无授权记录）：`dgx@10.169.25.16` 登录与 `id -un`=`dgx`；Compose 项目 `/home/dgx/spark-vllm`、服务 `qwen`、状态 `running`（容器 `535c2a8243d4…`）；两台引擎直连 `{"15": 200, "16": 200}`；`.16` 模型 `qwen3.6-35b-a3b-nvfp4`；Router 就绪、policy `cache_aware`。

| 项目 | 实测 | 判定 |
| --- | ---: | --- |
| 停止前 `.16` 经 Router 完整响应 | 20/20 探针由 `.16` 返回，首条 0.75 s | 通过 |
| 停止命令 / 直连失败 | 11.15 s / 2.84 s | — |
| Router 摘除（自停止命令起算） | 6.93 s | ≤20 s |
| 摘除后探针 | 20/20 由 `.15` 完整返回 | 通过 |
| 在途 4 条长请求 | 2 条 HTTP 200 后断流，2 条 `truncated` | 按实际状态记录 |
| 启动→首次直连 200 | 223.45 s（引擎加载） | — |
| 直连 200→Router 重新接纳 | 5.59 s | — |
| **直连 200→首条完整 `.16` 响应结束** | **6.235 s**（12:36:32.424Z → 12:36:38.659Z） | **≤20.00 s 通过** |
| 首条恢复响应 | 请求 `2bcca0eb8a8f437c9632a89837152cc5`，worker `http://10.169.25.16:8000`，`done=true`，`finish_reason=stop` | 通过 |
| 同一 Router 容器 | `939384f98d24` 保持不变（复用，未重建） | 通过 |
| 结束状态 | 两台直连 200，`.16` 经 Router 接单 | 通过 |

演练返回码 0，`pass=true`、`cycles_completed=1`、`actual_engines_stopped=[".16"]`，总耗时 263.38 s。原始证据：`.verification/dual-qwen/fault-20261124-newpreflight/summary.json`、`cycle-01.json`；登记副本：持久归档 `current-acceptance/fault-20261124-newpreflight/`。

### 历史：旧预检下的实机周期（fault-20261124-1x-r2，通过）

该轮使用已被现行规范删除的旧预检（Compose dry-run + 对运行中容器的一次真实 `docker start`），只作历史记录：停止命令 11.12 s、直连失败 2.85 s、Router 摘除 8.93 s、启动→首次直连 197.49 s、首条完整 `.16` 响应 19.469 s、请求 ID `50a06a4b32d74562a81afb64873e756a`、同一容器 `eacbab0d9872`、结束两台 200、退出码 0。该目录已迁出 Git，持久归档在 `X:\damonlu\ai-gateway\router-runs-archive\20261125\fault-20261124-1x-r2\`。更早的首次尝试 `fault-20261124-1x` 在发出停止命令前因上下文键名错误中止，未动服务，仅保留在 `.verification`，不作为验收证据。

## 2d. 压测实测（stress-20261124-live，通过）

```bash
python run_stress.py --modes replay concurrency cold-rag --run-id stress-20261124-live
```

| 阶段 | 耗时 | 退出码 | 子运行 |
| --- | ---: | ---: | --- |
| replay | 3655.68 s | 0 | 18/18 轮 rc=0（3 组 × 3 场景 × 2 策略，80 或 300 请求/轮，全部 success） |
| concurrency | 1268.04 s | 0 | 12/12 轮 rc=0（agent/rag_cold/rag_warm × 1–4 路，直连 `.15`） |
| cold-rag | 929.02 s | 0 | 6/6 轮 rc=0（3 组 × long limit 1/2，经临时 Scheduler 请求 `.15`，每轮 120 s + 15 s warmup） |
| **合计** | **5852.74 s** | **0** | 顶层 `pass=true`；`replay` 段 `pass=true`、`returncode=0`，日志与 `run-index.jsonl` 路径已记录 |

本轮所有回放轮次均成功，因此另做了一次真实失败注入来验证失败传递（见下）。证据：`.verification/dual-qwen/stress-20261124-live/summary.json` 及三段 `run-index.jsonl`，登记副本在持久归档 `current-acceptance/stress-20261124-live/`。

### 回放失败注入（stress-failinject-20261124）

临时把 `budgets.json` 移走，使每个 `replay.py` 子进程在加载阶段非零退出（`finally` 中恢复；恢复后哈希 `014e457e…` 与 manifest 一致）。命令 `python run_stress.py --modes replay --run-id stress-failinject-20261124`：

- `run_retest.py` 18/18 轮 rc=1；`run-index.jsonl` 保留每轮的组号、场景、策略、原始退出码和日志路径，失败汇总打印后退出 1。
- `run_stress.py` 的 `replay` 段 `pass=false`、`returncode=1`，整组 `pass=false`，退出码 1，44.2 s；摘要保留 `replay` 日志与索引路径。

证据：持久归档 `current-acceptance/stress-failinject-20261124/`。

结果完整性：`run_retest.py` 要求子进程 rc=0 且日志末行能解析出 `directory` 存在、`requests` 为正整数的结果；rc=0 但结果缺失或解析失败时，记录 `result_error` 和失败原因（轮次、日志路径）并最终退出非零。`run_stress.py` 校验 `run-index.jsonl` 的 18 条轮次全部有效后才让 `replay` 段通过。`--dry-run` 只打印命令，固定 `preview=true`、`pass=false`、退出 1。该完整性逻辑已用本次 live 与 failinject 的真实索引核对；尚未做“rc=0 但结果无效”的真实注入。

## 2e. 异常路径实测：注入启动失败（fault-20261124-startfail）

为验证“发出停止命令后启动失败时仍尝试恢复、记录原始错误、恢复未确认即终止”的路径：停止状态确认后，由远端 watcher 移走 `/home/dgx/spark-vllm/compose.yaml`，使恢复阶段的 `docker compose start` 无法执行；演练退出后立即恢复文件并手动启动引擎。

```bash
python run_fault_drill.py --cycles 1 --maintenance-window-ack --run-id fault-20261124-startfail --health-timeout 60
```

- 预检通过并复用就绪 Router（`cache_aware`，容器 `b255f3ba1986`）。
- 停止：rc=0（11.27 s），状态确认 `exited`。
- 启动：rc=1，stderr `no configuration file provided: not found`，`start_ok=false`。
- 恢复：60 s 内无直连健康，`recovery_error` 记录超时，`first_direct_health=null`，`recovery_confirmed=false`，`router_accepts_16=false`。
- 演练：`pass=false`、`aborted_reason` 已写、`final_health={"15":200,"16":0}`，退出码 1，总 92.23 s，未开始后续周期。
- 人工恢复：恢复同一 compose 文件（2513 字节）、`docker compose start qwen` rc=0；`.16` 在 195 s 后直连 200，Router 约 3 s 内重新接纳；随后 `--preflight-only`（`preflight-20261124-d`）退出 0、两台 200。

该测试是受控故障注入，不代表正常运行验收；实际停机窗口约 5 分钟。证据：持久归档 `current-acceptance/fault-20261124-startfail/`、`current-acceptance/preflight-20261124/`（C/D）。

## 3. 历史代理与 mock 归档

代理和 mock 故障路径已从执行面移除：`benchmark_proxy.py`、`proxy_failover.py`、`smoke_failover.py`、`test_smoke_proxy.py` 已删除，日常 smoke/stress 不再调用代理，`config/`、`scheduler/`、`app/` 没有代理依赖。以下仅是持久归档中的历史结果，不是当前验收路径，也不应再被执行：

- `smoke-proxy-v12`：代理路径历史通过记录，Router 摘除 5.60 s、重新接纳 6.75 s、首条 `.16` 完整响应 7.34 s，请求 ID `5d8abfe9d2ca401e963c80f74daee66d`，121.54 s。
- `smoke-proxy-v10`、`smoke-proxy-v6`：更早的代理验收。
- `smoke-entry-v1`：旧 mock 故障阶段记录，87.80 s。

以上目录已迁出 Git；按 run ID 在持久归档 `X:\damonlu\ai-gateway\router-runs-archive\20261125`（`INDEX.json`）中定位。

这些目录保留原始摘要、请求记录、分析重建和 SHA-256 清单，便于审计历史结论；脚本与用例不再随仓库提供。

## 4. 结果去重与离线重建

删除根目录副本前重新比较了 SHA-256，四个文件均与归档一致：

| 根目录文件（已删除） | SHA-256 | 唯一归档 |
| --- | --- | --- |
| `smoke-analysis.json` | `d42628c426e12176aa6f4989b0bac60960a3c405397b8e5fce6353a8f6172ede` | `current-acceptance/smoke-v1/analysis/smoke-analysis.json` |
| `retest-analysis.json` | `a92772d9ef0ebb79f1b24e30929a3cd94db2f730bde05d055c18aa36c411fa05` | `current-acceptance/retest-v12/analysis/retest-analysis.json` |
| `fault-analysis.json` | `8146d890b165f352faafaab38d64ae37af888cc327460ac140a3579fb6f6726b` | `current-acceptance/retest-v12/analysis/fault-analysis.json` |
| `cold-rag-analysis.json` | `3d83fc566685e0d81b35053cf4640b2ffdf2706e7ef1586d4e09ac90bd8d2ee3` | `current-acceptance/cold-rag-v1/analysis/cold-rag-analysis.json` |

分析入口使用 `--input` / `--output`，不再写根目录；登记入口要求 `--run-id`，新结果先写到本地 `runs/<run-id>/` 暂存，随后复制并校验到持久归档，不再提交到 Git。`retest-v12/analysis/request-digests.jsonl` 保存每请求的 `{run, state, ttft_ms}`，使 retest 的合并 p99 等指标无需原始 requests 也能重建；使用前需从 `current-acceptance/retest-v12/` 恢复到 `runs/retest-v12/`。

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

Git 内运行证据（2026-11-25 起）：**无**。`benchmarks/router/runs/` 只保留 `README.md`，不再保存任何 run 目录。

持久归档：

- `X:\damonlu\ai-gateway\router-runs-archive\20261125\current-acceptance`（共享 `\\10.10.18.151\damonlu`）：本轮当前验收及离线复算输入——`smoke-daily-v7`、`smoke-daily-v8`、`stress-20261124-live`、`stress-failinject-20261124`、`preflight-20261124`、`fault-20261124-newpreflight`、`fault-20261124-startfail`、`smoke-v1`、`retest-v12`、`cold-rag-v1`。
- `X:\damonlu\ai-gateway\router-runs-archive\20261125`：更早的 11 个 run——`smoke-daily-v1` … `v6`、`smoke-entry-v1`、`smoke-proxy-v6/v10/v12`、`fault-20261124-1x-r2`。
- 两处均有 `INDEX.json`，按 run ID 记录文件数、字节数和逐文件原始字节 SHA-256；与本地迁移源比对，10/10 和 11/11 全部一致后才从 Git 删除。本地副本保留在 `.verification/dual-qwen/runs-archive/`（旧 11 个）和 `.verification/dual-qwen/runs-archive/current-acceptance/`（当前 10 个）。
- 离线复算需先把 `smoke-v1`、`retest-v12`、`cold-rag-v1` 从 `current-acceptance` 恢复到 `runs/`，`verify_archives.py` 才可运行；Git 不再提供这三个输入。

移除：

- `benchmark_proxy.py`、`proxy_failover.py`、`smoke_failover.py`、`test_smoke_proxy.py`（代理/mock 故障实现与入口）。
- `test_run_smoke.py`、`test_fault_drill.py`、`test_run_stress.py`（不连接服务的替身用例）。
- `test_harness.py`、`test-results.json`（harness mock 检查及其生成结果）。
- `hot_forced.py`、`cold_rag/config_probe.py`（未被当前入口调用的旧诊断脚本；历史结果归档保留）。
- 根目录四份重复分析 JSON（仅副本，归档保留）。
- `fault_retest.py`、`run_fault_matrix.py`、`smoke_failure.py`（旧实机停启脚本）。
- 日常 smoke 和 stress 的实机停启与代理故障调用。

结果路径：

- 当前日常 smoke：`current-acceptance/smoke-daily-v8/`；`current-acceptance/smoke-daily-v7/` 同处归档。
- 只读预检：`current-acceptance/preflight-20261124/`。
- 压测：`current-acceptance/stress-20261124-live/`。
- 压测失败注入：`current-acceptance/stress-failinject-20261124/`。
- 手动实机演练（现行预检）：`current-acceptance/fault-20261124-newpreflight/`；异常路径注入：`current-acceptance/fault-20261124-startfail/`。
- 持久归档根：`X:\damonlu\ai-gateway\router-runs-archive\20261125`（`current-acceptance/INDEX.json` 和根 `INDEX.json`）；本地副本：`.verification/dual-qwen/runs-archive/`。
- 当前 manifest v1.3.5（删除 run 证据后，LF 归一化指纹）：`a679697ff979c931737cca2de573a8495f191938f8cddf7595e23ebeba950901`（32 个条目；数据集、校准、profiles 与 budgets 未变）。
- 当前 `smoke.json`（LF 归一化指纹）：`dc64d7191a55421ec766180574be1ac14d15201397e48e22d2ef96138e5dd8fe`（`source_manifest_sha256`、`cases_sha256`、`sessions_sha256` 均按 LF 字节）。

### 代码与数据检查（本轮复跑）

下表是 2026-11-25 之前已完成的实测/离线检查。第 6 节静态验收本轮没有复跑这些命令，也未连接 Spark。

| 检查 | 结果 |
| --- | --- |
| `py_compile` run_fault_drill / run_smoke / run_stress / run_retest | 退出码 0 |
| `replay.py validate` | 300 cases / 195 sessions，退出码 0 |
| `verify_archives.py` | `pass=true`，退出码 0 |
| manifest 32 条目（LF 归一化）哈希核对 | 无缺失、无不一致 |
| `smoke.json` → manifest 哈希 | 一致（LF 归一化） |
| smoke `--no-restart` 策略不符 | 拒绝，退出 2 |
| smoke `--baseline` 实跑 | 173.74 s，`baseline_core` 通过，Router 恢复 `cache_aware`，退出 0 |
| 干净检出（文本 CRLF / 数据 LF） | `replay.py validate` 与 smoke 哈希链通过，manifest 32 条无 mismatch |
| 旧 run 持久归档校验 | 11 个 run、逐文件原始字节 SHA-256、文件数和字节数全部一致 |
| 回放完整性（live 索引 18/18；failinject 索引 18 条无效） | 分别判通过 / 失败 |
| `run_stress.py --dry-run` 预览 | `preview=true`、`pass=false`、退出 1 |

## 6. 未验证事项汇总

1. 现行只读预检 + 真实停启周期的组合已执行 1 次并通过（`fault-20261124-newpreflight`：摘除 6.93 s、恢复间隔 6.235 s）；多周期仍未执行。启动命令失败、恢复未确认、演练中止等路径已通过 `fault-20261124-startfail` 注入实测；SSH 中断和 Router 重新接纳超时仍未在真实演练中制造。历史 r2 使用旧预检方法，其 19.469 s 不再代表现行入口。
2. 压测已实跑：replay 18/18、concurrency 12/12、cold-rag 6/6 全部成功；回放失败传递已用 `stress-failinject-20261124` 真实注入验证（18/18 rc=1、`replay.pass=false`、顶层退出 1）。未覆盖的是“部分轮失败、部分成功”的混合场景，以及“子进程 rc=0 但结果缺失/不可解析”的真实注入——这两类目前只有代码路径，完整性判断已用现有 live/failinject 索引核对。
3. 历史代理路径的实测结论只对应归档，脚本已删除；代理清理失败、SSH 停止检查失败和 attach 恢复失败不再有离线用例覆盖。
4. `current-acceptance/smoke-daily-v7/` 与 `v8/` 的 core `both_workers_routed=false` 与 v5/v6 相同，未额外调查；日常 smoke 的通过条件不要求该字段。
5. real-Agent 会话仍为 0，相关验收保持 pending。

## 7. 第 6 节静态验收（2026-11-25）

执行范围：只做静态代码审查、Git 树/文件清单和哈希核对。本轮没有运行单测、smoke、压测、实机停启或离线复算，也没有连接 Spark；第 2–5 节的实跑证据均产生于本轮之前，不能当作本轮新代码的运行验证。

| 剩余问题 | 状态 | 代码位置 / 证据 |
| --- | --- | --- |
| `.16` 停机验证拖延恢复 | 已完成（静态） | `run_fault_drill.py`：`--evidence-timeout`（默认 60 s）、`probe_rows(..., deadline=...)`、`collect_inflight(..., deadline=...)`、`record["evidence_complete"]` 和 `cycle_ok` 纳入；超时保留已采集 rows、标记周期不完整并立即进入 `recover_cycle`；停止后异常路径仍在 `finally` 尝试恢复；时限与 20 s 接单指标分开记录 |
| 重复模式/旧目录/缺失结果造成压测误报 | 已完成（静态） | `run_stress.py`：`EXPECTED_ROUNDS`（18/12/6）、`stage_completeness()`、启动前拒绝重复 `--modes` 和已存在输出目录、所有选中阶段校验 `run-index.jsonl` 与每轮 `summary.json`；`run_retest.py`、`run_smoke_concurrency.py`、`cold_rag/run_cold_rag_matrix.py` 拒绝已有输出目录；rc=0 但结果无效时写 `result_error` 和失败原因并整体非零；`--dry-run` 固定非通过 |
| 长冷 RAG 误用其他 Scheduler | 已完成（静态） | `cold_rag/run_cold_rag_matrix.py`：`port_in_use(18080)` 启动前拒绝占用；`wait_ready(process)` 检查本轮进程存活；端口冲突/提前退出/就绪超时写入 `error`、`scheduler_log`、`client_log` 并失败；`finally` 只停止本轮 Popen |
| 旧证据只在本机 Git 忽略目录 | 已完成 | 11 个 run 整轮复制到持久归档 `X:\damonlu\ai-gateway\router-runs-archive\20261125`（共享 `\\10.10.18.151\damonlu`）；逐文件原始字节 SHA-256、文件数和字节数与本地迁移源比较，11/11 一致；持久和本地 `INDEX.json` 都记录位置与校验；本地副本保留 |
| 旧引用未更新、保留项复核 | 已完成（静态） | 更新 `benchmarks/router/README.md`、`runs/README.md`、本报告和 `docs/qwen-router-smoke-test-results.md`；历史证据按 run ID + 持久归档 `INDEX.json` 定位；当前验收 10 个和旧 11 个 run 的持久副本均逐文件校验；Git 树已无 run 目录，`runs/` 只保留 README；离线复算输入需先从 `current-acceptance` 恢复，本轮未执行复算 |

| Git 中运行证据清理 | 已完成 | 当前验收 10 个 run（含 `smoke-v1`、`retest-v12`、`cold-rag-v1`）和旧 11 个 run 全部复制到持久归档并逐文件校验；`git rm` 后 `benchmarks/router/runs/` 只剩 `README.md`；未运行离线复算 |

静态清单更新：manifest 升到 v1.3.5（32 条目，`a679697f…`），`smoke.json` 为 `dc64d719…`；只做 LF 归一化哈希核对和 `py_compile`，未启动服务。

### 本轮明确未验证

- 上述新代码路径没有在本轮实际运行：重复/旧目录拒绝、并发与长冷 RAG 的轮次/摘要校验、cold-RAG 端口占用/进程早退、演练证据总时限。它们只经过静态审查和语法检查。
- 已有实测数据来自旧代码版本：`smoke-daily-v8`（v1.3.3）、`stress-20261124-live`、两次注入和实机演练；不能据此宣称本轮改动已通过实跑。
- 持久归档只在本机验证可访问，未做恢复演练。
- 多周期演练、SSH 中断、Router 重新接纳超时仍未实测。
- `verify_archives.py` 本轮未复跑；此前通过记录仍保留。
