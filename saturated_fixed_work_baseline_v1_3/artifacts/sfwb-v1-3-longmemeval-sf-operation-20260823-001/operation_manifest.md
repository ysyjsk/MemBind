# LongMemEval-S FactConsolidation Operation Freeze

Status: `OFFLINE_OPERATION_FROZEN`

This is an append-only, gold-only operation manifest. It does not run
Graphiti, an LLM, an embedding service, Neo4j, Reader, or Judge.

## Literature Basis

Primary precedent: **Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions**, ICLR 2026 (2507.05257v4).
The paper's Selective Forgetting / FactConsolidation protocol injects
facts incrementally, treats larger serial numbers as newer, resolves
contradictions in favor of the newest fact, queries after all injection,
and uses SubEM-compatible final-answer scoring.

This lane is explicitly a `LONGMEMEVAL_OPERATIONALIZATION`, not an exact
MemoryAgentBench FactConsolidation reproduction: LongMemEval-S exposes
two official answer-session anchors and a final answer, but no structured
`old_value`/`new_value`. Old/new values therefore remain
`OPAQUE_UNLESS_PROVABLE` and are never inferred from a model result.

## Frozen Source

- dataset: `/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json`
- file SHA-256: `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`
- records: `500`
- knowledge-update: `78`
- `_abs` excluded: `6`
- selected non-abstention operations: `72`
- selection reads B0/B1 results: `false`
- selection reads execution outcomes: `false`

## Reference-Time Policy

Construction reference times are a fixed monotonic source-order mapping
(`2000-01-01T00:00:00Z + source_sequence * 60s`). Raw LongMemEval dates
are retained and hashed for provenance but are not used to encode gold
answers or to schedule construction. The mapping is identical for B0/B1.

## Existing Graph Coverage (Inventory Only)

The raw operation freeze covers `72` histories.
Existing paired v1.3 canonical graph paths cover `4` histories:
`07741c45, 6071bd76, a2f3aa27, b6019101`.
This inventory did not read graph payloads and does not change the frozen
LongMemEval cohort.

## Frozen Cases

| # | question_id | old anchor (segment) | new anchor (segment) | source count | raw date order | official current answer |
|---:|---|---|---|---:|---|---|
| 1 | `6a1eabeb` | `answer_a25d4a91_1` (19) | `answer_a25d4a91_2` (39) | 40 | `MONOTONIC` | 25 minutes and 50 seconds (or 25:50) |
| 2 | `6aeb4375` | `answer_3f9693b7_1` (35) | `answer_3f9693b7_2` (46) | 47 | `MONOTONIC` | four |
| 3 | `830ce83f` | `answer_0b1a0942_1` (31) | `answer_0b1a0942_2` (44) | 47 | `MONOTONIC` | the suburbs |
| 4 | `852ce960` | `answer_3a6f1e82_1` (2) | `answer_3a6f1e82_2` (36) | 39 | `MONOTONIC` | $400,000 |
| 5 | `945e3d21` | `answer_6a4f8626_1` (3) | `answer_6a4f8626_2` (19) | 48 | `MONOTONIC` | Three times a week. |
| 6 | `d7c942c3` | `answer_eecb10d9_1` (1) | `answer_eecb10d9_2` (36) | 49 | `MONOTONIC` | Yes. |
| 7 | `71315a70` | `answer_c44b9df4_1` (3) | `answer_c44b9df4_2` (29) | 53 | `MONOTONIC` | 10-12 hours |
| 8 | `89941a93` | `answer_e1403127_1` (6) | `answer_e1403127_2` (29) | 51 | `MONOTONIC` | 4 |
| 9 | `ce6d2d27` | `answer_73540165_1` (1) | `answer_73540165_2` (17) | 51 | `MONOTONIC` | Friday |
| 10 | `9ea5eabc` | `answer_02e66dec_1` (6) | `answer_02e66dec_2` (45) | 53 | `MONOTONIC` | Paris |
| 11 | `07741c44` | `answer_7e9ad7b4_1` (7) | `answer_7e9ad7b4_2` (30) | 50 | `MONOTONIC` | under my bed |
| 12 | `a1eacc2a` | `answer_0eb23770_1` (16) | `answer_0eb23770_2` (30) | 44 | `MONOTONIC` | seven |
| 13 | `184da446` | `answer_e2f4f947_1` (1) | `answer_e2f4f947_2` (40) | 42 | `MONOTONIC` | 220 |
| 14 | `031748ae` | `answer_8748f791_1` (0) | `answer_8748f791_2` (6) | 46 | `MONOTONIC` | When you just started your new role as Senior Software Engineer, you led 4 engineers. Now, you lead 5 engineers |
| 15 | `4d6b87c8` | `answer_766ab8da_1` (17) | `answer_766ab8da_2` (47) | 52 | `MONOTONIC` | 25 |
| 16 | `0f05491a` | `answer_d6d2eba8_1` (4) | `answer_d6d2eba8_2` (43) | 45 | `MONOTONIC` | 120 |
| 17 | `08e075c7` | `answer_cdbe2250_1` (4) | `answer_cdbe2250_2` (40) | 45 | `MONOTONIC` | 9 months |
| 18 | `f9e8c073` | `answer_b191df5b_1` (19) | `answer_b191df5b_2` (39) | 53 | `MONOTONIC` | five |
| 19 | `41698283` | `answer_c7ddc051_1` (4) | `answer_c7ddc051_2` (44) | 45 | `MONOTONIC` | a 70-200mm zoom lens |
| 20 | `2698e78f` | `answer_9282283d_1` (24) | `answer_9282283d_2` (38) | 48 | `MONOTONIC` | every week |
| 21 | `b6019101` | `answer_67074b4b_1` (41) | `answer_67074b4b_2` (45) | 49 | `MONOTONIC` | 5 |
| 22 | `45dc21b6` | `answer_07664d43_1` (15) | `answer_07664d43_2` (44) | 49 | `MONOTONIC` | 3 |
| 23 | `5a4f22c0` | `answer_b0f3dfff_1` (7) | `answer_b0f3dfff_2` (40) | 49 | `MONOTONIC` | TechCorp |
| 24 | `6071bd76` | `answer_4dac77cb_1` (1) | `answer_4dac77cb_2` (19) | 46 | `MONOTONIC` | You switched to less water (5 ounces) per tablespoon of coffee. |
| 25 | `e493bb7c` | `answer_1a374afa_1` (8) | `answer_1a374afa_2` (30) | 51 | `MONOTONIC` | in my bedroom |
| 26 | `618f13b2` | `answer_caf5b52e_1` (17) | `answer_caf5b52e_2` (32) | 49 | `NON_MONOTONIC_OR_EQUAL` | six |
| 27 | `72e3ee87` | `answer_d7de9a6a_1` (19) | `answer_d7de9a6a_2` (45) | 51 | `MONOTONIC` | 50 |
| 28 | `c4ea545c` | `answer_d3bf812b_1` (7) | `answer_d3bf812b_2` (29) | 46 | `MONOTONIC` | Yes |
| 29 | `01493427` | `answer_a7b44747_1` (0) | `answer_a7b44747_2` (27) | 47 | `MONOTONIC` | 25 |
| 30 | `6a27ffc2` | `answer_77f32504_1` (19) | `answer_77f32504_2` (43) | 51 | `MONOTONIC` | 30 |
| 31 | `2133c1b5` | `answer_52382508_1` (23) | `answer_52382508_2` (34) | 51 | `MONOTONIC` | 3 months |
| 32 | `18bc8abd` | `answer_fff743f5_1` (17) | `answer_fff743f5_2` (43) | 44 | `MONOTONIC` | Kansas City Masterpiece |
| 33 | `db467c8c` | `answer_611b6e83_1` (9) | `answer_611b6e83_2` (38) | 48 | `MONOTONIC` | nine months |
| 34 | `7a87bd0c` | `answer_d08a934d_1` (0) | `answer_d08a934d_2` (36) | 48 | `MONOTONIC` | 4 weeks |
| 35 | `e61a7584` | `answer_f25c32f5_1` (16) | `answer_f25c32f5_2` (31) | 50 | `MONOTONIC` | 9 months |
| 36 | `1cea1afa` | `answer_79c395a9_1` (2) | `answer_79c395a9_2` (47) | 49 | `MONOTONIC` | 600 |
| 37 | `ed4ddc30` | `answer_babbaccb_1` (1) | `answer_babbaccb_2` (40) | 46 | `MONOTONIC` | 20 |
| 38 | `8fb83627` | `answer_966cecbb_1` (24) | `answer_966cecbb_2` (42) | 44 | `MONOTONIC` | Five |
| 39 | `b01defab` | `answer_8c0712af_1` (2) | `answer_8c0712af_2` (46) | 50 | `MONOTONIC` | Yes |
| 40 | `22d2cb42` | `answer_bcce0b73_1` (10) | `answer_bcce0b73_2` (24) | 48 | `MONOTONIC` | The music shop on Main St. |
| 41 | `0e4e4c46` | `answer_f2f998c7_1` (8) | `answer_f2f998c7_2` (48) | 49 | `MONOTONIC` | 132 points |
| 42 | `4b24c848` | `answer_2cec623b_1` (7) | `answer_2cec623b_2` (46) | 51 | `MONOTONIC` | five |
| 43 | `7e974930` | `answer_c9f5693c_1` (14) | `answer_c9f5693c_2` (44) | 47 | `MONOTONIC` | $420 |
| 44 | `603deb26` | `answer_8afdebac_1` (1) | `answer_8afdebac_2` (46) | 48 | `MONOTONIC` | 10 |
| 45 | `59524333` | `answer_b28f2c7a_1` (6) | `answer_b28f2c7a_2` (40) | 44 | `MONOTONIC` | 6:00 pm |
| 46 | `5831f84d` | `answer_8d63a897_1` (10) | `answer_8d63a897_2` (28) | 40 | `MONOTONIC` | 15 |
| 47 | `eace081b` | `answer_8a791264_1` (4) | `answer_8a791264_2` (39) | 47 | `MONOTONIC` | Oahu |
| 48 | `affe2881` | `answer_90de9b4d_1` (16) | `answer_90de9b4d_2` (24) | 44 | `MONOTONIC` | 32 |
| 49 | `50635ada` | `answer_dcd74827_1` (19) | `answer_dcd74827_2` (42) | 48 | `MONOTONIC` | Premier Silver |
| 50 | `e66b632c` | `answer_ac0140ce_1` (4) | `answer_ac0140ce_2` (45) | 47 | `MONOTONIC` | 27 minutes and 45 seconds |
| 51 | `0ddfec37` | `answer_a22b654d_1` (18) | `answer_a22b654d_2` (32) | 50 | `MONOTONIC` | 15 |
| 52 | `f685340e` | `answer_25df025b_1` (1) | `answer_25df025b_2` (38) | 41 | `MONOTONIC` | Previously, you play tennis with your friends at the local park every week (on Sunday). Currently, you play tennis every other week (on Sunday). |
| 53 | `cc5ded98` | `answer_a5b68517_1` (1) | `answer_a5b68517_2` (14) | 53 | `MONOTONIC` | about two hours |
| 54 | `dfde3500` | `answer_35d6c0be_1` (13) | `answer_35d6c0be_2` (23) | 47 | `MONOTONIC` | Wednesday |
| 55 | `69fee5aa` | `answer_d6028d6e_1` (12) | `answer_d6028d6e_2` (39) | 49 | `MONOTONIC` | 38 |
| 56 | `7401057b` | `answer_94650bfa_1` (10) | `answer_94650bfa_2` (28) | 47 | `MONOTONIC` | Two |
| 57 | `cf22b7bf` | `answer_ae3a122b_1` (28) | `answer_ae3a122b_2` (53) | 55 | `MONOTONIC` | 10 pounds |
| 58 | `a2f3aa27` | `answer_5126c02d_1` (12) | `answer_5126c02d_2` (39) | 44 | `MONOTONIC` | 1300 |
| 59 | `c7dc5443` | `answer_0cdbca92_1` (7) | `answer_0cdbca92_2` (43) | 46 | `MONOTONIC` | 5-2 |
| 60 | `06db6396` | `answer_da72b1b4_1` (3) | `answer_da72b1b4_2` (31) | 51 | `MONOTONIC` | 5 |
| 61 | `3ba21379` | `answer_cd345582_1` (7) | `answer_cd345582_2` (8) | 44 | `MONOTONIC` | Ford F-150 pickup truck |
| 62 | `9bbe84a2` | `answer_c6a0c6c2_1` (5) | `answer_c6a0c6c2_2` (14) | 48 | `MONOTONIC` | level 100 |
| 63 | `10e09553` | `answer_67be2c38_1` (4) | `answer_67be2c38_2` (36) | 45 | `MONOTONIC` | 7 |
| 64 | `dad224aa` | `answer_4a97ae40_1` (2) | `answer_4a97ae40_2` (47) | 48 | `MONOTONIC` | 7:30 am |
| 65 | `ba61f0b9` | `answer_f377cda7_1` (13) | `answer_f377cda7_2` (40) | 45 | `MONOTONIC` | 6 |
| 66 | `42ec0761` | `answer_e3892371_1` (44) | `answer_e3892371_2` (46) | 48 | `MONOTONIC` | Yes |
| 67 | `5c40ec5b` | `answer_1cb52d0a_1` (16) | `answer_1cb52d0a_2` (50) | 51 | `MONOTONIC` | We've met up twice. |
| 68 | `c6853660` | `answer_626e93c4_1` (2) | `answer_626e93c4_2` (13) | 46 | `MONOTONIC` | You increased the limit (from one cup to two cups) |
| 69 | `26bdc477` | `answer_f762ad8d_1` (21) | `answer_f762ad8d_2` (40) | 48 | `MONOTONIC` | five |
| 70 | `0977f2af` | `answer_3bf5b73b_1` (20) | `answer_3bf5b73b_2` (39) | 44 | `MONOTONIC` | Instant Pot |
| 71 | `89941a94` | `answer_e1403127_1` (30) | `answer_e1403127_2` (44) | 46 | `MONOTONIC` | Yes. (You have a road bike too.) |
| 72 | `07741c45` | `answer_7e9ad7b4_1` (2) | `answer_7e9ad7b4_2` (31) | 49 | `MONOTONIC` | in a shoe rack in my closet |

## Later QA Boundary

A later read-only lane may query each completed graph after all source
episodes are durable. Its primary evidence surface must be graph facts
and temporal fields only; it must not include source-local sessions or
the full gold conversation. Retrieval success alone is not Selective
Forgetting: the semantic predicate must require the official current
state and reject a stale value retained as current. Cases whose old value
cannot be proven from official gold remain stale-exclusion `NOT_PROVABLE`.

No B1 or V5 run is authorized by this artifact.
