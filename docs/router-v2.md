> **状态：退役。** 本文描述的是 Auto / Auto-v2 实验原型，不是第一版产品代码。相关 `auto`、`auto-v2`、`router_v2`、试验 Compose、Dashboard 扩展和专属测试入口已从当前产品树移除；配置与脚本仅保留在历史备份中。当前产品入口请以根目录 README 和第一版 Scheduler 为准。

# Router v2 测试与上线计划

现有 Scheduler 继续持有队列和后端容量，LiteLLM 负责认证与模型入口。单个 Scheduler worker 内的 v1、v2 和显式模型路由共用同一份并发和 KV 账本。选择继续扩展现有 Scheduler，复用 redis-py 访问 Redis。vLLM Production Stack 需要额外的 Kubernetes 部署，Dynamo 会引入新的推理编排层，Gemma/Qwen 验证阶段保留现有部署结构。

| 顺序 | 变更 | 验收 |
|---|---|---|
| 1 | 用隔离的 Gemma、Qwen 端点校准模板、tokenizer、上下文和可用 KV | 冷首轮与重复前缀续轮对照，记录首 token 和真实命中数 |
| 2 | 开启 `auto-v2`，按目标模型 tokenizer、模型能力和配置档位选择。带 session_id 的续轮优先原副本 | 同一用户的不同会话独立，缺 ID 逐次路由，Redis 到期后重新选池 |
| 3 | 对全路径执行并发和请求级 KV 准入，验证原副本忙、故障、超限和取消 | 流量混跑不超预算，异常请求的额度可核查和回收 |
| 4 | 验证会话汇总与导出，对照 TTFT、命中、排队和失败 | 稳定 baseline 后，再预演生产策略比较、切流及排水 |
| 5 | 接入实测过的生产模型配置，再为少量新会话启用 | 实机故障演练和服务目标达标后，调档位和副本数 |

## 测试入口（已退役）

1. 在已移除的 `config/backends.router-v2-test.yaml` 备份中填入 Gemma、Qwen 的可达服务根地址、实际 served-model-name、端点兼容能力、上下文和实测 `safe_kv_tokens`。示例使用 4096 个 prompt token 分流，是为缩短测试周期。两个引擎需要提供 `/health`、`/tokenize` 和 `/v1/chat/completions`。
2. 使用独立测试环境文件。基于项目的环境变量为测试项目设置独立凭据和空闲的 `GATEWAY_PORT`，并设置 `ROUTER_SESSION_REDIS_URL=redis://:URL编码后的测试Redis密码@redis:6379/0`。后端兼容 API Key 时在对应配置添加 `api_key_env`，overlay 已为 `GEMMA_TEST_API_KEY` 和 `QWEN_TEST_API_KEY` 提供入口。
3. 启动测试项目。使用测试 LiteLLM Key 调用 `auto-v2`。同一聊天传递稳定的 `session_id`，可选 `epoch_id`、`quality_route` 和每次用户操作独立的 `operation_id`。LiteLLM 以认证后的 Key hash 覆盖 `user`。无 Redis 时，有会话 ID 的 v2 请求返回 503。
4. 用 Scheduler 内部凭据访问 `GET /admin/router-v2/session?principal_id=...&session_id=...` 下载一份 JSONC 兼容的 JSON。会话汇总存在测试项目自己的 SQLite 卷。计数覆盖已派发尝试，包含失败和取消。队列拒绝发生在派发前。流式响应报告首个实质增量的 TTFT，非流式 TTFT、引擎未提供的缓存 Token 记空。缓存覆盖率使用已知缓存数据的输入 Token / 总输入 Token。

```powershell
docker compose --env-file .env.router-test -p ai-gateway-router-test -f compose.yaml -f compose.router-v2-test.yaml up --build -d
```

## TTL 与故障处理

`session_ttl_seconds` 默认 1800 秒，控制 Redis 路由绑定的空闲寿命。vLLM 前缀缓存按实际 Token 块复用。请求结束后的未引用块可以继续命中，资源紧张时可以更早淘汰，引擎重启也会影响命中。路由始终按完整输入加输出预算计费。推荐用重复前缀在空载和混合并发下分别测试 1、5、15、30 分钟的命中率，再按实际续轮间隔调 TTL。

原副本满载先短等，再让同池副本接单。选池时发现整组不可用或当前请求超限，可尝试满足要求的其他池。可通过 `new_session_kv_fraction` 设置分流软目标。新会话超过目标时优先尝试下一可用池。没有替代时仍以硬上限排队。一次自动重试只覆盖连接建立失败。一轮成功完成后写入绑定并重置空闲 TTL，流中断返回给客户端；处理中幂等键由同一 worker 去重。

当配置了 `safe_kv_tokens` 且在上游未正常结束时发生取消、超时或传输异常，保留并发槽及预算。上游正常结束 HTTP 响应即释放预算，缺少 SSE `[DONE]` 仍计为流错误。通过内部 `GET /admin/backends` 查看 `uncertain_requests`。用引擎日志确认对应请求已经终止，或将引擎排水后停止，再调用内部 `POST /admin/confirmed-stopped/{request_id}` 释放。Scheduler 重启前应排水，并确认引擎没有遗留任务。进程内账本采用单 worker，切换部署时按后端隔离流量。

生产灰度前还需要实机验证端到端取消、加入可获取的引擎启动代次、自动核对待确认请求，以及配置生产 shadow、切流和回滚。连续拥堵的自动迁移成本估算和 Agent 压缩联动留给后续验证。

## 测试网关的 Actual Backend 与批量导出

`deploy/router-logs/` 及对应 Dashboard 扩展已随实验入口移除，不再提供启动命令。

```powershell
docker compose --env-file .verification/router-v2/.env -p ai-gateway-router-logs -f .verification/router-v2/compose.logging.yaml -f deploy/router-logs/compose.override.yaml up -d
```

Logs 列表的 Actual Backend 紧跟 Status，显示实际模型与副本 ID。打开日志详情后，Actual Backend 卡片显示端点 IP 和端口。

使用 `litellm_proxy/auto-v2` 时，在同一个 Harness `agent_session` 内发起首问和续问。Harness 自动将 `session.session_id` 用于调度粘滞和网关日志归组。到 **Logs → Session ID** 筛选该 ID，再打开会话查看各轮请求。测试网关将 Claude Code 的 Anthropic Messages 转为 Chat Completions，并将认证后的 Key hash 传给 Scheduler。用 Claude Code 测试时，通过 `ClaudeCodeOptions.env` 将 `CLAUDE_CODE_MAX_OUTPUT_TOKENS` 设为 `4096`，与测试路由的输出上限一致。Messages 转换后的日志可按会话展开，副本可从 Scheduler 会话汇总查到。

管理员可点击 Export Route JSON，导出所选时间范围内全部 auto-v2 请求。该按钮采用时间范围，其他列表筛选条件不限制导出。JSON 包含请求 ID、会话 ID、用户、Key 别名、请求模型、实际模型、副本、端点、请求状态、回复存在标记、Token、耗时和 TTFT。

`status` 表示请求执行结果，值为 success、failure 或 unknown。`has_reply` 表示保存的响应包含可见文本或工具调用。`answer_status` 为 has_reply、no_reply、request_failed 或 unknown。成功标记表示 API 调用结果，回答的事实正确性仍需业务评估。派发前拒绝的请求，其后端字段为空。

Scheduler 将实际 served model 和端点加入响应头，日志扩展从已保存的响应头提取。历史记录若只保存了副本 ID，则通过当前后端配置补全模型和端点，`backend_source=current_config`。新记录带完整响应头时为 response_headers。导出采用明确字段清单，不包含聊天正文、工具定义、回复正文或 API Key。

## Usage 中的 Backend Models

测试网关 Usage → Model Activity 的 Backend Models 按实际后端模型汇总 auto-v2 请求。展开 Replicas 可查看副本 ID、端点、成功／失败／未知请求数、输入与输出 Token、平均请求耗时及平均 TTFT。日期范围、模型搜索和用户筛选作用于当前视图。

数据来自 GET /router-v2/usage/backend-models，时间参数使用带时区的 ISO 8601。管理员可查看全量或指定用户，普通用户按认证身份限定为本人。无实际派发后端的失败请求归入 Unknown backend，参与总请求数。TTFT 只统计有成功流式输出观测的请求，其余显示 Unknown。平均值基于请求样本计算。
