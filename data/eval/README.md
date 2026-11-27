# CareerCrew evaluation dataset contract

`cases.jsonl` is the checked-in, immutable-by-convention baseline dataset for
the offline regression evaluation.

- Dataset version: `2026-09-10.v1`
- Format: one UTF-8 JSON object per line.
- Identity: `case_id` is canonical. `id` remains an accepted read-time alias
  for the existing dataset and is normalized to `case_id`; duplicate IDs fail
  loading.
- Required fields: `case_id` (or legacy `id`), `kind`, `question`, and
  `expected`. Supported kinds are `route`, `retrieval`, `citation`, `tool`,
  `memory`, and `consult`.
- Privacy: keep cases synthetic or redacted. Reports retain only IDs, kinds,
  timings, tokens, and safe error codes; never add prompts, answers, resumes,
  user IDs, or provider credentials.

## Commands

> 2026-11-27：`scripts/eval_runner.py` 已随 scripts 精简移除（git 历史可找回），
> CI 的 eval-sanity 门禁同步移除。以下命令仅作历史参考。

```bash
# Offline non-regression gate (required on PRs)
python scripts/eval_runner.py --offline --compare data/eval/baseline.json --fail-on-regression

# Intentional, human-reviewed offline baseline update
python scripts/eval_runner.py --offline --update-baseline

```

真实模型评测（`--real`/`--runtime`/`--require-real`/`--model-probe`）需要受保护
评测租户的外部 attestation 与可证明的独占写入租约，2026-09-16 决策后已从仓库移除；
runner 只保留离线 fixture 回归，不再接受这些参数。

Do not edit this dataset to make a failing run pass. Dataset or baseline changes
require human review, a new documented version, and an intentional baseline
update. Offline fixtures describe the recorded observation for this exact
dataset.

Promoted bad-case JSONL supplied through `--bad-cases` also accepts legacy
`id`. Its `rubric` must contain a non-empty `must_include` and/or
`must_not_contain` list. When `rubric` is absent, a non-empty string/list
`expected` is normalized to `must_include`; an empty rubric is rejected rather
than treated as a passing case.
