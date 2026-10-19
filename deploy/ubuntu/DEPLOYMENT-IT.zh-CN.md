# 模型网关生产部署说明（Ubuntu / DGX Spark ARM64）

本文用于把已验证的测试网关复制到一台新的生产服务器。默认新建生产数据库和密钥，不迁移测试用户、虚拟 Key 或调用历史。

## 1. 部署结构与准备条件

在选定的 **一台网关服务器** 上，用 Docker Compose 启动四个容器：

| 容器 | 用途 |
|---|---|
| LiteLLM | Web 管理、用户 Key、模型访问权限、使用记录 |
| scheduler | 准入控制、排队、每个 Key 的并发限制、模型端点选择 |
| PostgreSQL | 保存 Key、配置与调用记录 |
| Redis | LiteLLM 使用的协调服务 |

请求路径：用户 → LiteLLM → scheduler → vLLM 模型端点。

**不需要在每台 Spark 上各部署一套网关。** 一套网关可以连接多个模型端点。两台 Spark 共同提供一个模型服务、只暴露一个 API 地址时，在网关中只配置一个端点；GPU 之间如何协作由 vLLM 决定。

目标服务器需要 Ubuntu ARM64、Docker Engine、Docker Compose 插件。网关本身不使用 GPU；若与 vLLM 共用 Spark，需要预留内存和 CPU 资源。

在生产服务器执行：

```bash
uname -m
docker --version
docker compose version
docker compose ls -a
```

架构必须为 `aarch64`。镜像不适用于 x86_64。若目标机已存在 `imvision-gateway` 项目，不要按全新安装覆盖其配置和密码，应先备份并按升级处理。

## 2. 在测试 Spark 上准备交付文件

**源目录：测试 Spark 的 `~/imvision-gateway`。** 使用这份正在运行、已经改成 GLM 的配置，不要直接用早期压缩包中的 Qwen 默认配置代替。

需要交付：

```text
gateway-release/
├── compose.yaml
├── .env.example
├── config/
│   ├── backends.yaml
│   └── litellm.yaml
├── images.tar
└── SHA256SUMS
```

Logo 已在 LiteLLM 镜像内，不需要另外复制 PNG。不要复制测试环境 `.env`、Docker 数据目录或模型权重。本说明只部署网关，vLLM 和模型权重由模型服务单独部署。

在测试 Spark 执行：

```bash
cd ~/imvision-gateway
mkdir -p ~/gateway-release/config
cp compose.yaml .env.example ~/gateway-release/
cp config/backends.yaml config/litellm.yaml ~/gateway-release/config/

docker image save -o ~/gateway-release/images.tar \
  imvision-gateway:20260916-arm64 \
  imvision-scheduler:20260916-arm64 \
  imvision-postgres:16-20260914-arm64 \
  imvision-redis:7-20260914-arm64

cd ~/gateway-release
sha256sum compose.yaml .env.example config/backends.yaml config/litellm.yaml images.tar > SHA256SUMS
tar -czf ~/gateway-release.tar.gz compose.yaml .env.example config images.tar SHA256SUMS
```

以上镜像标签对应当前已验证版本；若实际部署更换过镜像，应同步修改导出列表及 Compose 中的标签。

把 `~/gateway-release.tar.gz` 传给 IT，或使用以下命令传输。替换 `PROD_USER` 和 `PROD_IP`：

```bash
scp ~/gateway-release.tar.gz PROD_USER@PROD_IP:~/
```

同时交付本说明。打包不会停止测试服务。

## 3. 在生产服务器解压并导入镜像

以下目录应作为本次新安装目录，不要覆盖已有生产部署。

```bash
mkdir -p ~/imvision-gateway
cd ~/imvision-gateway
tar -xzf ~/gateway-release.tar.gz
sha256sum -c SHA256SUMS
docker load -i images.tar
```

校验应全部为 `OK`，Docker 应显示成功载入四个镜像。后续配置修改会改变文件校验值，这是正常的；此处校验用于确认传输完整。

确认 `compose.yaml` 中 scheduler 和 LiteLLM 都使用目录挂载：

```yaml
volumes:
  - ./config:/app/config:ro
```

LiteLLM 的启动参数应为：

```yaml
command: ["--config", "/app/config/litellm.yaml", "--port", "4000"]
```

保留完整 Compose 文件，只检查上述内容。不要改回单文件挂载，否则编辑器替换文件后，容器可能仍读取旧文件。

## 4. 配置生产地址和密码

```bash
cp -n .env.example .env
chmod 600 .env
nano .env
```

填写以下五个不同的秘密值：

| 字段 | 用途 |
|---|---|
| `LITELLM_MASTER_KEY` | LiteLLM 管理 API 的主密钥，必须以 `sk-` 开头 |
| `LITELLM_UI_PASSWORD` | Web 管理员登录密码 |
| `POSTGRES_PASSWORD` | 数据库密码 |
| `REDIS_PASSWORD` | Redis 密码 |
| `SCHEDULER_INTERNAL_API_KEY` | LiteLLM 调用 scheduler 的内部凭证 |

每次执行以下命令生成一个不同的随机值：

```bash
openssl rand -hex 24
```

主密钥在生成值前加 `sk-`，其余字段可直接使用生成值。使用十六进制数据库密码可避免连接字符串中的特殊字符问题。内部凭证由 Compose 自动传给两个应用容器，不提供给普通用户。

同时设置：

```dotenv
LITELLM_UI_USERNAME=admin
GATEWAY_BIND_ADDRESS=生产网关服务器的公司内网IP
GATEWAY_PORT=4000
```

这里填写的是 **网关服务器自身 IP**，不是任意一台模型服务器的 IP。使用 `ip -4 -br addr` 确认地址。建议使用固定地址或 DHCP 保留地址，并确保启动时该地址已配置。

初始排队参数可保留：每个 Key 最多 1 个活动请求、2 个等待请求；其他参数在 `.env` 中可调。保持：排队超时 < scheduler 总超时 < LiteLLM 超时。

注意：这些密码用于新数据库初始化。已有数据库不能只修改 `.env` 中的数据库密码来完成改密。

## 5. 配置模型和端点

### 5.1 确认模型服务

网关服务器必须能访问生产 vLLM 的 `/health`、`/v1/models`、`/tokenize` 和 `/v1/chat/completions`。将下面地址替换为真实端点：

```bash
curl --noproxy '*' --max-time 10 http://MODEL_IP:8000/v1/models
```

使用返回结果中的准确模型 `id`。若 vLLM 开启认证，需要带相应凭证，并配置下一节的 `api_key_env`。

### 5.2 修改 `config/backends.yaml`

以下为 GLM 单端点示例。将 `MODEL_IP` 替换为实际地址；上下文能力和模型名称应以生产 vLLM 为准。

```yaml
models:
  glm-5.3-flash:
    backend_pool: glm-pool
    upstream_model: glm-5.3-flash
    max_context_tokens: 32768
    max_output_tokens: 4096
    default_output_tokens: 1024
    long_request_threshold_tokens: 8192

backend_pools:
  glm-pool:
    backends:
      - id: glm-endpoint-1
        url: http://MODEL_IP:8000
        enabled: true
        weight: 1.0
        max_context_tokens: 262144
        max_concurrent_requests: 2
        max_long_requests: 1
        health_path: /health
```

- `url` 不加 `/v1`。两台 Spark 共同暴露一个地址时只写一条，不要为没有 API 的工作节点再添加条目。
- 模型级上下文限制是用户请求的“输入 + 最大输出”上限；端点级限制描述 vLLM 的实际能力，不能凭空调高。
- 并发 2、长请求并发 1 是初始策略，需要结合真实模型测试调整，不代表已测定的硬件吞吐能力。
- 同模型的独立副本可继续加入同一 `backends` 列表；`id` 必须唯一。池内模型、分词器及聊天模板应一致。
- 不同模型使用独立模型配置和后端池。
- 若开启后端认证，可在端点下设置 `api_key_env: SPARK_15_API_KEY`，并在 `.env` 填写该变量。它只是凭证变量名；如使用新的变量名，还须在 Compose 的 scheduler `environment` 中传入。

### 5.3 修改 `config/litellm.yaml`

公开模型名需要与 scheduler 的 `models` 下的名称一致。示例：

```yaml
model_list:
  - model_name: glm-5.3-flash
    litellm_params:
      model: openai/glm-5.3-flash
      api_base: http://scheduler:8080/v1
      api_key: os.environ/SCHEDULER_INTERNAL_API_KEY
      allowed_openai_params: [user]
    model_info:
      id: admission-glm
```

仅修改需要的模型条目，保留文件中的 `router_settings`、`litellm_settings`、`general_settings`。尤其保留 `overwrite_user_with_key_hash: true` 和 `allowed_openai_params: [user]`，以传递可信的 Key 身份。

**LiteLLM 的地址应指向 scheduler，不要直接指向 vLLM，否则会绕过准入和排队控制。** 不同模型增加对应条目；只为同一模型增加副本时，通常不需要修改 LiteLLM。

## 6. 启动和验收

```bash
cd ~/imvision-gateway
docker compose config --quiet
```

无报错后启动：

```bash
docker compose up -d
```

首次启动等待数据库初始化，约一分钟后检查：

```bash
docker compose ps
docker compose exec scheduler python -m app.admin status
```

四个容器应为 `healthy`，要使用的后端也应显示 `healthy: true`。scheduler 容器健康只代表进程正常，不等于模型端点可用。

在生产网关服务器上验证，替换 `GATEWAY_IP`：

```bash
curl --noproxy '*' --max-time 10 -i http://GATEWAY_IP:4000/health/liveliness
```

预期 `HTTP/1.1 200 OK` 和 `"I'm alive!"`。

从用户 Windows 电脑验证：

```powershell
Test-NetConnection GATEWAY_IP -Port 4000
```

预期 `TcpTestSucceeded: True`。IT 需允许指定公司用户网段（包括实际使用的 Wi-Fi 网段）访问网关 TCP 4000。无需向用户开放 PostgreSQL 5432、Redis 6379 或 scheduler 8080。

浏览器打开 `http://GATEWAY_IP:4000/ui`，使用 `admin` 和 `LITELLM_UI_PASSWORD` 登录。创建新的生产虚拟 Key，并指定允许访问的模型。

用户客户端配置：

| 项目 | 值 |
|---|---|
| OpenAI 兼容 API Base URL | `http://GATEWAY_IP:4000/v1` |
| 模型名 | 例如 `glm-5.3-flash` |
| API Key | LiteLLM 中新建的用户虚拟 Key |

发送一次真实请求，确认回复正常，并检查 LiteLLM 的 Logs/Usage 和 scheduler 日志：

```bash
docker compose logs -f --since 5m scheduler
```

`scheduler_request_admitted` 中的 `backend_id` 表示实际选中的端点，`queue_wait_seconds` 表示准入等待时间。它不能证明端点内部具体哪块 GPU 执行了请求。

## 7. 后续配置和运维

| 操作 | 应用命令 |
|---|---|
| 改端点、模型池、上下文或端点并发限制 | `docker compose exec scheduler python -m app.admin reload` |
| 改 LiteLLM 模型配置 | `docker compose restart litellm` |
| 改 `.env` 或 `compose.yaml` | `docker compose up -d` |
| 查看全部状态，包括已退出的容器 | `docker compose ps -a` |
| 查看 LiteLLM 错误 | `docker compose logs --tail 80 litellm` |
| 停止服务并保留数据 | `docker compose stop` |

只运行一个 scheduler 容器、一个 worker。当前队列和活动计数在进程内存中，不能简单增加 scheduler 副本来扩容。重新创建或重启 scheduler 会中断其正在处理或等待的请求；配置 reload 不需要重启。

Key 和调用历史保存在 Docker 数据卷中，不在 `~/imvision-gateway` 文件夹或镜像里。**不要使用 `docker compose down -v`，除非明确要删除数据。** 若需要迁移测试 Key/历史，应另做 PostgreSQL 备份恢复并处理相关密钥，不属于本文的新建部署流程。

生产开放前，配置数据库备份和日志保留，按公司要求在入口部署 HTTPS，并限制用户绕过网关直接访问 vLLM。以上是当前单机网关部署方案，不提供网关高可用。

### 两个已遇到问题的处理

1. **容器读取旧配置：** 确认两应用使用 `./config:/app/config:ro` 目录挂载，并且 LiteLLM 启动参数指向 `/app/config/litellm.yaml`。切换挂载后执行 `docker compose up -d --no-deps --force-recreate scheduler litellm`，之后正常 reload/restart 即可。
2. **绑定地址失败 `cannot assign requested address`：** 用 `ip -4 -br addr` 确认网关 IP 当前存在；地址恢复后执行 `docker compose up -d litellm`。不要只凭 `lsof` 是否有输出判断 Docker 发布的端口；以容器状态和本机 HTTP 测试为准。
