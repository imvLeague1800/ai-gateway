# .16 Qwen 手动实机故障演练

## 什么时候执行

由你确认维护窗口、指定次数并明确要求 agent 执行。沿用已实测成功停启 Qwen 的 `dgx@10.169.25.16`。如果账号、目标服务或执行环境有变，先核实变化，再安排演练。 [单次实机记录](router-benchmark-organization-results.md)

## 简化后的流程

1. agent 登录 `.16`，用只读查询确认目标是 `/home/dgx/spark-vllm` 项目下的 Qwen 服务，并检查两台引擎直连健康。确认隔离 Router 已就绪且指向两个直连 worker。需要启动隔离 Router 时，在完成基础只读检查后单独执行。仅运行预检时保持只读。
2. 维护窗口未确认、SSH 登录失败、目标不符或状态无法确认时，退出并报告原因，真实停止和启动调用均为零。脚本省去 Compose dry-run、自动授权推断和额外的授权记录。
3. agent 通过独立入口手动触发。单次指定 `--cycles 1`，多次由你指定次数。只停止并恢复 `.16` 的 Qwen 服务，`.15` 的 Qwen 和网关继续运行。触发后由同一个脚本负责恢复，发出停止命令后即使后续检查报错，也要尝试启动 `.16`。恢复未确认时终止后续周期。
4. 每周期记录 Router 摘除 `.16` 后探针全部改派 `.15`，以及 `.16` 恢复后首条经 Router 完成的请求 ID、实际落点和流结束状态。从首次观察到 `.16` 直连健康到该响应结束，须在 20 秒内。引擎加载时间单独记录。

简化后的入口用法，从 benchmark 目录执行：

```bash
python run_fault_drill.py --cycles 1 --maintenance-window-ack --run-id <新ID>
```

## 实现验收

- 复用现有 SSH、停启、探针和恢复逻辑，删去 dry-run、Docker socket/组及 sudo 推断、`--authorization-record` 和相应测试。移除旧 mock 故障实现、用例及执行入口。
- 验收以维护窗口内的真实停启记录为准。保存每周期的停启结果、Router 摘除耗时、改派 `.15` 的完整响应，以及 `.16` 首次直连健康到经 Router 完成首条响应的实际用时。该间隔 **≤20 秒**才通过。保存两台引擎的最终健康状态，结果归档到 benchmark。未收到停机指令时，实机验收记为待执行。
- 日常 smoke 和压测继续与实机演练分开。保留历史结果。 [Benchmark 整理计划](router-benchmark-organization-plan.md)
