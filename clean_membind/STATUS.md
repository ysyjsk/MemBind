# Clean Mainline Status

`clean_membind` implementation: **TDD GREEN** (14 tests).

Native validation: **NOT READY**.

* Official Graphiti/Ollama one-episode reproduction passed for qwen2.5:14b and
  deepseek-r1:7b.
* qwen2.5:14b failed real MAB state at global 18 with malformed JSON under
  `json_schema`; the 16K diagnostic also ran for 51 minutes without a durable
  response.
* qwen2.5:14b under `json_object` returned the schema document and failed
  Pydantic validation.
* deepseek-r1:7b failed real MAB state at global 7 with an unterminated JSON
  string under `json_schema`.

No full-H0 Serial Native run and no formal A/B/C experiment is authorized.
Historical artifacts are preserved. The next engineering action is to bring a
new, genuinely constrained local backend or provider that can pass the same
long-state check, then freeze its identity and execute the conditional plan.
