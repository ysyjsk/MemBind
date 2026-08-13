# MemBind Judge Qualification Workplan v1.0

> **状态**：冻结，等待离线实现与单次 live 授权  
> **Protocol ID**：`judge-qualification-v1.0`  
> **范围**：`JUDGE_QUALIFICATION_ONLY`  
> **TDD**：RED -> minimal GREEN -> focused -> impact regression -> dry-run -> bounded live once

```text
WORKPLAN_FREEZE=true
scientific_surface=JUDGE_QUALIFICATION_ONLY
characterization_dependency=NONE
live_run_limit=1
fixture_count=14
```

<!-- 维护注释：本文只管理 Judge 的独立资格验证。不得把它并入 C5/C6，
不得从 Judge 结果反推或修改 characterization 结论；未来若改变 fixture、模型、
rubric、解析器或请求配置，必须新建版本，不得原地改写 v1.0。 -->

## 1. 边界与非目标

本计划只回答：固定 Qwen3 Judge 是否能在公开、合成、人工标签已预先冻结的
LongMemEval rubric fixture 上得到完全一致且可审计的 YES/NO 结果。

本计划：

- 不属于 C5 或 C6，不授权、阻塞或推进 Native Graphiti characterization；
- 不启动或访问 Neo4j、Graphiti、construction pipeline 或 embedding；
- 不读取 C5 输出，不生成 C5/C6 指标，不改变 characterization artifact；
- 不覆盖 `CURRENT_STATE.json`，也不借用其中的 C4/C5 live grant；
- 不覆盖既有 Judge offline manifest；
- 不把 qualification 结果当作 benchmark accuracy 或论文效果结果。

C4 run `c4-8e76fba0288047f9` 的 post-finalize verification failure 必须在 C4
流程内另行处理。本计划不得修改、恢复、合并或引用该 run 为 Judge 证据。

## 2. 固定上游与离线根证据

资格验证必须只使用已实现的：

```text
EvaluatorRegistry
  -> LongMemEvalAdapter
     -> official get_anscheck_prompt
        -> Qwen3JudgeBackend
```

离线 source of truth 为：

```text
membind-validation/artifacts/protocol/judge_upstream_manifest_20260812.json
file_sha256 = ec1062f4adc7e5a852fd38082f0ddc5f7c92c3fc32d3bf2c7cfb5c2117c4c7ce
payload_sha256 = 2d2a1511c37b6aa4cf3b27c3ce9f8eba7b762384e7a23b490e03032da3f5b7a2
```

它固定：LongMemEval commit
`9e0b455f4ef0e2ab8f2e582289761153549043fc`、TiMEM reference commit
`6d279a5f5d40ee229e1995df15c182cb2062c71c`、本地 adapter/backend 文件哈希
和 offline request policy。执行前必须重新验证 manifest seal 与绑定文件哈希。
该 offline manifest 保持只读；live runtime identity 和本计划 artifact 写入新的
qualification run 目录，绝不回填或覆盖它。

## 3. 14 个公开合成 fixture

固定 7 条 official LongMemEval route，每条恰好一个 YES 和一个 NO：

```text
single-session-user          YES / NO
single-session-assistant     YES / NO
multi-session                YES / NO
temporal-reasoning           YES / NO
knowledge-update             YES / NO
single-session-preference    YES / NO
abstention                   YES / NO
```

共 14 项，正负标签各 7 项。fixture 只能使用公开、合成内容，不使用真实用户数据、
held-out confirmation set、C5 输出或私有 benchmark 内容。

在任何真实 Judge response 可见之前，必须 exclusive-create
`fixture_freeze.json`，冻结：

- protocol ID、14 个稳定 `item_id` 和固定 route/order；
- question、reference、hypothesis、abstention 的内容或公开内容哈希；
- `human_label`，每条只能为 `YES` 或 `NO`；
- YES/NO 各 7 条的平衡约束；
- official prompt hash、offline manifest file/payload SHA256；
- fixture payload SHA256、创建命令和 schema version。

冻结后不得按 Qwen 输出改标签、改措辞、删题、换 route 或调顺序。若 fixture
有错，当前 attempt 标记 `incomplete_invalid_non_mergeable` 并 STOP；修订只能
新建 workplan 版本和新 run ID。

## 4. 精确 Qwen 请求配置

每个 fixture 只发送一次 Chat Completions 请求，配置严格为：

```text
endpoint_path                   = /v1/chat/completions
model                           = qwen3-32b-fp8
temperature                     = 0
max_tokens                      = 10
n                               = 1
messages                        = [{role: user, content: official_prompt}]
system_message_count            = 0
thinking_control                = client_request
extra_body.chat_template_kwargs.enable_thinking = false
sdk_hidden_retries              = 0
max_attempts                    = 1
explicit_retry_count_allowed    = 0
timeout_seconds                 = 30
```

不得注入 system prompt、few-shot 修补、response_format、reader re-answer、
route-specific decoding 或 fallback Judge。不得因为 NO、INVALID、超时或结果不利
而重试。一次请求的 `retry_count` 必须为 0。

## 5. Runtime identity 与 secret hygiene

live 前必须建立独立 `runtime_identity.json` 并绑定到 qualification freeze。至少记录：

- endpoint 的规范化 SHA256 identity，不记录原始私有 URL；
- `/v1/models` 观察到的 exact served model name；
- vLLM version、模型 revision/fingerprint、dtype/quantization；
- `max_model_len`、RoPE/YaRN 参数和 chat-template SHA256；
- effective `enable_thinking=false` 的证据来源；
- Python、OpenAI SDK、httpx 版本；
- backend public config 与 config hash；
- offline manifest file/payload SHA256。

上述关键字段无法从部署日志、只读 metadata 或实际配置确定时，必须在 live Judge
请求前 STOP 并报告缺失证据，不允许人工猜测。

任何 artifact、日志、异常或命令不得包含 API key、Authorization header、`.env`
内容、URL userinfo、credential 或完整私有 endpoint。密钥只从现有环境配置在内存
读取；公开 synthetic prompt/output 可以持久化，仍须同时保存其 SHA256。异常只保存
sanitized error class，不保存响应 body、headers 或秘密。

## 6. Artifact、checkpoint 与 resume

每次只允许一个新目录：

```text
membind-validation/artifacts/judge_qualification/runs/jq-<16hex>/
  manifest.json
  fixture_freeze.json
  runtime_identity.json
  events.jsonl
  items/000..013/checkpoint.json
  checkpoint.json
  qualification_summary.json        # 仅 PASS 时存在
```

所有 JSON 使用 canonical ASCII JSON、payload SHA256、atomic replace；manifest、
fixture freeze 和 run directory 必须 exclusive-create。`events.jsonl` 每条 append 后
flush+fsync，event sequence 从 0 连续递增。

每项状态机：

```text
planned
  -> dispatch_intent_durable
     -> terminal_success | terminal_invalid | terminal_service_error
```

先 fsync `dispatch_intent_durable`，再发唯一请求，最后 exclusive 写 terminal
checkpoint。terminal checkpoint 至少保存 item/route/human label、prompt hash、
raw/normalized output、official label、audit label、parse status、config hash、
retry count、agreement 和前序 event hash。

Resume 只能在同一 run ID、manifest/freeze/runtime identity 全部 hash 匹配且已完成项
形成连续 terminal prefix 时继续；已 terminal 项永不重发。若最后一项停在
`dispatch_intent_durable` 而没有 terminal 结果，请求是否到达服务端无法证明，整个
attempt 必须 `incomplete_invalid_non_mergeable` 并 STOP，禁止冒险重发。新 run 不得
复用旧 response。

任一 INVALID_OUTPUT、SERVICE_ERROR、artifact/hash/parity 错误、进程中断、身份漂移
或非连续 checkpoint 都立即：持久化失败 -> root 标记
`incomplete_invalid_non_mergeable` -> STOP。失败 attempt 不参与聚合，也不自动启动
第二次 live run。

## 7. TDD 实现顺序

### Q0 - RED

先写失败测试，覆盖：

- 7 routes x YES/NO 的 exact 14-item freeze、标签平衡和 order；
- human label 必须早于任何 response/event 冻结；
- exact Qwen wire request：one user、zero system、thinking false、无 hidden/explicit retry；
- official-compatible label 与 strict audit parser 的一致/INVALID 分离；
- TP/TN/FP/FN、agreement 和 Cohen's kappa 的确定性计算；
- exclusive manifest/checkpoint、fsync event chain、tamper fail-closed；
- completed-prefix resume 不重发、in-flight ambiguity 不重发并失败；
- INVALID、SERVICE_ERROR、identity drift、secret redaction 均终止且不可合并；
- offline manifest 只读绑定，`CURRENT_STATE.json` 不属于 writer surface。

保留 RED log，证明测试针对缺失 contract 失败，而非 import/syntax 错误。

### Q1 - Minimal GREEN

只实现使上述 RED 变绿的 fixture builder、qualification artifact store、runner、
resume verifier 和 analyzer。复用现有 Registry、LongMemEval adapter、Qwen backend、
canonical hashing 与 parser；不得复制第二套 rubric/backend。

### Q2 - Focused 与 impact regression

依次执行并持久化：

1. Judge qualification focused tests；
2. 现有 evaluator/registry/backend/provenance tests；
3. qualification 影响面 offline regression。

任何非绿先修复并重新从 focused 开始。不得为通过测试修改 C4/C5 或
`CURRENT_STATE.json`。

### Q3 - Dry-run

使用 fake backend/`httpx.MockTransport` 跑完整 14 项，证明：

```text
real_external_requests = 0
planned = 14
terminal = 14
eligible = 14
resume/checkpoint verifier = PASS
```

dry-run 必须覆盖全 PASS、INVALID、SERVICE_ERROR、tamper 和 ambiguous in-flight
分支。保存 focused/impact/dry-run log 及 SHA256。dry-run 不是 live 结果。

### Q4 - Freeze 与人工授权

只有 Q0-Q3 全绿后，exclusive-create live manifest，绑定代码、测试、logs、fixture、
offline manifest 和 runtime identity 的 SHA256。人工明确授权仅一个
`jq-<16hex>` run ID。授权不写入或修改 `CURRENT_STATE.json`。

### Q5 - Bounded live once

按冻结顺序运行 14 项，每项立即 checkpoint。初始阶段可查看每项 terminal 状态；
确认稳定后只按 checkpoint 间隔监听。不得另发 canary、重复 Judge、补测题或第二轮。
终态后离线运行 verifier/analyzer，然后进入 PASS 或 STOP。

## 8. 唯一 PASS 条件

以下条件必须同时精确成立：

```text
planned_item_count  = 14
terminal_item_count = 14
eligible_item_count = 14
agreement_count     = 14
invalid_count       = 0
service_error_count = 0
retry_count_total   = 0

confusion_matrix:
  TP = 7
  TN = 7
  FP = 0
  FN = 0

observed_agreement = 1.0
cohens_kappa       = 1.0
```

`eligible` 仅指 `status=SUCCESS` 且 strict audit label 为明确 YES/NO。INVALID_OUTPUT
和 SERVICE_ERROR 不得自动算作错误答案或 agreement denominator；但任一出现都会令
本 qualification FAIL。正类固定为 YES，kappa 使用 14 个预冻结 human labels 与
14 个 eligible Qwen labels 计算，不做 rounding 后判定。

PASS 时才写 `qualification_summary.json`，其 `mergeable` 仅表示 Judge qualification
artifact 自身完整，不表示可合并进 C5/C6 科研结果。若任何条件不满足，根状态必须
为 `incomplete_invalid_non_mergeable` 并立即 STOP；不得调 prompt、换 model、挑样本
或自动重跑。

## 9. 终止条件与后续边界

本计划在以下任一状态终止：

```text
JUDGE_QUALIFICATION_PASS
JUDGE_QUALIFICATION_INCOMPLETE_INVALID_NON_MERGEABLE
```

终止报告只给出 run ID、冻结身份哈希、14 项计数、confusion matrix、kappa、失败项
位置/error class（如有）和 artifact 路径/SHA256，不泄露秘密。

PASS 只允许未来计划把该 Qwen 配置列为“已通过这 14 个公开合成 fixture 的 Judge
backend”。它不证明泛化、真实 benchmark accuracy、跨模型等价或 characterization
结论。任何后续 C5/C6、真实 benchmark evaluation、扩大 fixture、重复性验证或模型
比较都必须由各自独立计划授权；本计划不得自动进入这些阶段。

<!-- 维护注释：实现过程中若发现 contract bug，只能修复与 Q0-Q5 直接相关的
Judge qualification 文件和测试。C4 verification wiring failure 保持独立 issue；
不要为了“顺手修复”扩大本 workplan 的 writer surface。 -->
