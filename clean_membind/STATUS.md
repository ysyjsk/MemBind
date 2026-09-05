# Clean Mainline Status

`clean_membind` implementation: **TDD GREEN** (16 tests).

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
* qwen3.5:latest can return a single Graphiti episode when Ollama's
  `reasoning_effort=none` is injected and `json_object` is used, but the real
  MAB prefix failed at global 1 during the untouched `EdgeDuplicate` schema
  validation. The model returned a schema document missing
  `duplicate_facts`/`contradicted_facts`.

No full-H0 Serial Native run and no formal A/B/C experiment is authorized.
Historical artifacts and the failed qwen3.5 transport experiment are
preserved. No additional model search or prompt repair is performed in this
run; the repository remains explicitly `NATIVE_NOT_READY`.
