# C246 后置 C8 扩展策略

`P(C=8)-aligned` 不是当前 C246 主比较的一部分。当前主实验固定为
`U0-aligned`、`P(C=2)-aligned` 和 `P(C=4)-aligned`，共 12 个 block。

只有以下条件全部满足，才允许启动 C8：

1. 同一 run 的 `PHASE_RESULT.json` 存在，且 `status=PASS`、`phase=full`。
2. `completed_block_indices` 精确为 `0..11`，所有 block result hash 校验通过。
3. 8002/8003 的模型身份、APC 运行时和 Neo4j 仍通过只读 preflight。

启动命令：

```bash
cd /data/predator/ly/MemBind/paper-eval-v3
tmux new-session -d -s c246-c8-20260819-001 -c "$PWD" \
  "PYTHONPATH=src:../membind-validation/src \
   ../membind-validation/.venv/bin/python scripts/run_c246_baselines.py \
   c246-baseline-<completed-run-id> --phase c8-extension \
   2>&1 | tee logs/c246-c8-20260819-001.log"
```

扩展结果写入：

```text
artifacts/paper_eval/c246_baseline/runs/<run-id>/c8-extension/
```

C8 复用主实验的固定 arrival trace、模型/embedding identity 和全局 LLM
admission，但使用 4 个全新 namespace 和 block-specific cache salt。C8 的
性能、APC telemetry 与 correctness 只能作为后置诊断，不能回写或替换
U0/P(C=2)/P(C=4) 主表。
