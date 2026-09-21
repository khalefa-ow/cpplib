# `agent/` — DSPy workflow for C++ storage planning, codegen and optimization

A staged LLM pipeline that designs an in-memory storage layout for an analytical
C++ query engine, generates the code, fixes it until it compiles and matches
DuckDB gold results, then optimizes it with hints.

The LLM layer is **DSPy 3.3**; the C++ reading and editing is **cpplib**, reached
from the RLM sandbox through host-side tools. Every path comes from config, so
the package can be pointed at any project.

## Quick start

```bash
uv pip install -e ".[dev,agent]"          # adds dspy, duckdb, pyarrow
python -m agent.cli doctor                # check deno, cmake, g++, keys
python -m agent.cli run --config agent/examples/config.example.json --dry-run
python -m agent.cli run --config agent/examples/config.example.json
python -m agent.cli run --config agent/examples/config.example.json --steps 5  # stage by stage
```

All five stages are implemented. A run resumes: a stage whose outputs already
reflect its current inputs is skipped, so a run that dies in `optimize` does not
re-pay for planning and code generation. `--force` re-runs anyway (and still
hits the disk cache), `--stages` restricts the selection and `--start-from`
begins partway through.

`doctor` is the first thing to run: `dspy.RLM` needs the **Deno** runtime for its
Pyodide sandbox, and a missing Deno otherwise surfaces as an opaque protocol
error deep inside the interpreter.

## Stages

| Stage | Requires | Produces |
|---|---|---|
| `storage_plan` | – | `storage_plan`, `storage_plan_meta` |
| `divide` | `storage_plan` | `schema_levels` |
| `hppgen` | `schema_levels` | `storage_layout_headers` |
| `query_codegen` | `schema_levels`, `storage_layout_headers` | `query_sources`, `correctness_report` |
| `optimize` | `query_sources`, `correctness_report` | `optimization_report` |

Execution order is derived from these `requires`/`produces` declarations, not
hardcoded. Each stage's class docstring documents its config keys and output
shape.

**`storage_plan`** reasons about the layout from schema + workload + statistics.
No workspace and no tools: it is designing, not editing.

**`divide`** splits schema and plan into the levels declared in `common.levels`
— `no_hints`, `organization_level`, `all_hints` in the example. This is what
makes the pipeline an ablation: generate the engine once per level and the value
of the storage plan is measurable. The split is validated against the declared
level names before it is written, because a level missing here surfaces two
stages later as a failure in `hppgen`.

**`hppgen`** generates one storage-layout header per active level, then compiles
it and repairs it up to `compile.max_fix_rounds` times. Only a header that
actually compiled is cached, so a re-run retries a broken one instead of serving
it from disk forever.

**`query_codegen`** generates one translation unit per query and loops until it
compiles *and* matches gold. Two budgets, deliberately separate:
`compile.max_fix_rounds` for compile and link errors,
`params.max_correctness_rounds` for result mismatches — a missing include is a
one-line fix while a wrong answer usually means the plan was wrong, and one
shared budget would let trivial compile errors consume the rounds a mismatch
needs. Comparison is full-output with a per-cell tolerance for floats, and
reports the first differing row and column: a model told "row 4, column revenue:
expected 1234.50, got 1234.00" fixes the accumulator, whereas one told "wrong"
rewrites at random.

It generates **one level per run**. Several levels in one tree would collide on
filenames and on the build, so compare levels by running the pipeline once per
level with its own `artifacts_dir` and `gen_project_root`.

**`optimize`** refuses to start unless every query in scope compiled and matched
— a faster wrong answer is not an improvement, and finding that out at the end
costs the whole budget. Each round snapshots the workspace, applies the model's
patch, rebuilds, re-verifies *every* query in scope against gold (the storage
layout is shared, so an edit made for one query is exactly how the others
break), measures a median of `params.repeat_runs` runs, and keeps the change
only if it cleared `params.min_improvement`. Otherwise the snapshot is restored.
All four optimization prompts end with "Make sure the performance improved.
Otherwise, try again or remove your changes"; the snapshot is what turns that
from advice into a guarantee.

### Executing the generated engine

`query_codegen` and `optimize` need a way to run what was generated, and that is
a property of the project being generated, not of this package — so it comes
from `params.run_command`, a shell template:

```json
"run_command": "./build/engine --query {query_id} --out {output} --sf {sf}"
```

Placeholders: `{query_id}`, `{query_text}`, `{output}`, `{project_root}`,
`{build_dir}`, `{dataset_dir}`, `{sf}`, `{trace}`. The template is split into
argv *before* substitution, so a query containing spaces or quotes stays one
argument. Output is read from the `{output}` file when the template names one
and from stdout otherwise. `optimize` inherits the command recorded in
`correctness_report`, so it need not be repeated.

Without a run command, `query_codegen` reports every query as `unverified` and
the run summary carries `verified=False`. It does not claim correctness it did
not check, and `optimize` then refuses to start.

For timing, set `params.runtime_pattern` to a regex whose first group is the
engine's own reported runtime. Wall clock includes process start and data load,
which are identical every round and so shrink the apparent effect of a real
improvement.

## Layout

```
agent/
├── cli.py            run | doctor | prompts build/list/show | config show/normalize
├── pipeline.py       dependency ordering, resumption, failure handling
├── config/           pydantic models + loader (field-level inheritance, legacy mapping)
├── prompting/        manifest with inline prompt text, strict rendering, fingerprints
├── llm/              dspy.LM construction (OpenAI/DeepSeek), content-addressed cache
├── trace/            span stack, JSONL callbacks, optional weave/wandb
├── rlm/              CppWorkspace, compile results, tool callables, CppRLM
└── stages/           Stage ABC, ArtifactStore, the five stages, and their
                      support layer: parsing, results, gold, execution, fixing
```

The support modules under `stages/` are where the non-obvious correctness lives
and are tested directly in `tests/agent/test_agent_support.py`:

| Module | Responsibility |
|---|---|
| `parsing.py` | Strip fences off model output; scan a workload into queries with stable ids (a `;` inside a string or comment is not a split point) |
| `results.py` | Compare output to gold, locate the first difference, tolerate float drift but not an off-by-one integer |
| `gold.py` | Produce or find reference results (DuckDB / external command / pre-existing) |
| `execution.py` | Run the engine through the configured command; median-of-N timing |
| `fixing.py` | The generate → verify → repair loop, with a budget and a no-progress guard |
| `predict.py` | The one place a signature plus a payload becomes an answer; the seam tests patch |

## Config

One canonical schema (`agent/config/models.py`); see
`agent/examples/config.example.json`. A `common` block holds project paths and
defaults, and each stage overrides what it needs:

```json
{
  "common": { "base_dir": ".", "inputs": {"schema": "...", "queries": "..."},
              "model": {"name": "openai/gpt-5.3-codex"}, "cache": {"dir": "out/.cache"} },
  "stages": { "storage_plan": {"cache": {"refresh": true}} }
}
```

Inheritance is **field-level** and happens on the raw dict before validation, so
a stage that sets only `cache.refresh` keeps `common`'s `cache.dir`. Relative
paths resolve against `common.base_dir`, which itself resolves against the
config file's own directory, so a config stays portable.

The three differently-shaped configs pasted into `agent.md` still load:
`normalize_legacy()` maps them onto this schema (`divide.llm` → `stages.divide.model`,
flat `trace_path`/`use_cache`/`gold_*` into their blocks, `planner_models`/`patcher_models`
onto the stages that use them). `agent.cli config normalize` prints the result.

**Models** are LiteLLM `provider/model` strings, so OpenAI and DeepSeek are a
config edit apart. The provider prefix fills the conventional key env var and
endpoint; using `openai/deepseek-chat` without an `api_base` is rejected, since
LiteLLM would otherwise send the request to `api.openai.com`.

## Prompts

Prompt text lives **inline** in `agent/prompting/manifest.json` — each entry's
`text` field is the full template, alongside its metadata. There is no
separate `prompts/*.txt` directory; the manifest is the only source of truth.

```bash
python -m agent.cli prompts set query_codegen_task --file draft.txt   # add/edit one prompt
python -m agent.cli prompts build                                      # resync derived fields
python -m agent.cli prompts show optim_w_trace --var query_id=7 --var sf=0.25 ...
```

`hppgen_policy` and `query_codegen_task` carry the generation instructions for
the two code stages; `fix_compile_errors` is the repair prompt both of them use,
and it is in their `default_prompt_ids` so that editing it invalidates exactly
their caches.

`prompts build` detects placeholders in both `${braced}` and bare `$named`
form, preserves hand-edited `stage`/`role`/`description`/`composes`, and bumps
`version` only when an entry's `text` changed since the last build. It
**fails** on a stray `$` that is not a valid placeholder rather than letting
`substitute` raise mid-run. `agent.prompting.import_prompts_dir()` (or
`prompts build --prompts-dir <dir>`) remains for bulk-importing an external
directory of `.txt` files as inline entries.

Rendering is strict: a missing placeholder is an error naming the prompt and
the missing names, never a prompt that reaches the model still containing
`${query_id}`. Fragments compose automatically — every `optim_w_*` prompt
opens with `${constraints}`, which is filled from `optim_constraints` without
being passed.

## Caching

Two layers. Ours (`agent/llm/cache.py`) memoizes a **whole module invocation**
keyed on prompt fingerprint + inputs + model signature, stored as readable JSON
under `cache.dir`. DSPy's own LM cache sits underneath and covers what ours
cannot see: the individual completions inside a single RLM call, including its
recursive sub-calls.

Editing a prompt's text (and rebuilding the manifest) changes its fingerprint
and so invalidates exactly the cache entries that depended on it.
`cache.refresh` recomputes on every lookup while still writing, which
refreshes a stale answer without discarding the cache.

## Tracing

`trace.path` gets one JSON object per line, with a real parent/child span tree.
DSPy's `call_id` correlates a start with its end but says nothing about
containment, so a context-var span stack supplies parentage. Every
`BaseCallback` hook is implemented, including the RLM-specific ones that make a
run legible: `on_interpreter_execute_*` (the code the model wrote and what came
back) and `on_interpreter_tool_call_*` (every cpplib call it made).

Credential-shaped keys are redacted and long fields truncated. The redaction
pattern is anchored so that `prompt_tokens`/`completion_tokens` survive — a
loose match on `token` would destroy the usage counts the trace exists to record.

`enable_weave` / `enable_wandb` are optional (`.[agent-trace]`); a missing or
broken backend logs a warning and the run continues.

## The C++ layer

`dspy.RLM`'s sandbox has no filesystem, network or subprocess access, so cpplib
is reached through host-side callables in `rlm/cpp_tools.py`. Because those run
on the host, `CppWorkspace.safe_path` is a security boundary, not a convenience
check, and no general shell tool is ever exposed. Every tool returns a string and
never raises — an exception crossing the bridge aborts the RLM iteration, while
an error message lets the model correct itself.

`CppWorkspace` works around several cpplib defects, each with a regression test
in `tests/agent/test_agent_workspace.py`:

| cpplib behaviour | Workaround |
|---|---|
| `FileModifier`'s queued edits clobber each other (each mutator re-reads from disk) | Never queue more than one edit; bypass `FileModifier` for whole-file and patch writes |
| No index invalidation after a write | Mutations mark the index dirty; `CPPCodebase` is rebuilt lazily on next read |
| `_find_function_start` is `content.find(f"{name}(")`, matching call sites and comments | `replace_function` splices by the AST-derived line range |
| Eager full-tree index with no exclusions (parses `build/`) | Index a source subdirectory and filter listings |
| `check_syntax` omits `-std=` and returns `None` for its bool | Invoke the compiler directly; `CompileResult.ok` is always a bool and `compiler_missing` is its own flag |
| `validate(clean=True)` shells out `rm -rf` | `clean_build` is opt-in and the path is validated before deletion |

## Tests

```bash
pytest tests/agent -v
```

352 tests, none needing an API key. An autouse fixture deletes every provider
key from the environment, so a stage that reached a real model by mistake would
fail rather than quietly bill you.

Everything except the provider round trip is real: g++ compiles the headers and
queries the stages generate, the generated engine is executed, its output is
compared against a gold file on disk, and `optimize` measures a genuine change
in the engine's runtime before deciding to keep or revert a round. The model is
either DSPy's `DummyLM` — so signature construction, the adapter, the callbacks,
caching and artifact writing all run for real — or a queued fake `invoke` where
a test needs to control what each successive call returns.

The live RLM smoke test is gated behind `AGENT_LIVE_RLM=1` because it costs
tokens.
