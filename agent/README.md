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
python -m agent.cli prompts build         # (re)generate the prompt manifest
python -m agent.cli run --config agent/examples/config.example.json --dry-run
python -m agent.cli run --config agent/examples/config.example.json --stages storage_plan
```

`doctor` is the first thing to run: `dspy.RLM` needs the **Deno** runtime for its
Pyodide sandbox, and a missing Deno otherwise surfaces as an opaque protocol
error deep inside the interpreter.

## Stages

| Stage | Requires | Produces | State |
|---|---|---|---|
| `storage_plan` | – | `storage_plan`, `storage_plan_meta` | **implemented** |
| `divide` | `storage_plan` | `schema_levels` | contract stub |
| `hppgen` | `schema_levels` | `storage_layout_headers` | contract stub |
| `query_codegen` | `schema_levels`, `storage_layout_headers` | `query_sources`, `correctness_report` | contract stub |
| `optimize` | `query_sources`, `correctness_report` | `optimization_report` | contract stub |

Execution order is derived from these `requires`/`produces` declarations, not
hardcoded. Each stub raises `StageNotImplemented`; its class docstring is the
implementation contract (inputs, output shape, loop structure, prompts, caching).
Everything around the stub — config resolution, prompt binding, artifact keys,
cache keying — already works and is covered by tests.

## Layout

```
agent/
├── cli.py            run | doctor | prompts build/list/show | config show/normalize
├── pipeline.py       dependency ordering, resumption, failure handling
├── config/           pydantic models + loader (field-level inheritance, legacy mapping)
├── prompting/        manifest over prompts/*.txt, strict rendering, fingerprints
├── llm/              dspy.LM construction (OpenAI/DeepSeek), content-addressed cache
├── trace/            span stack, JSONL callbacks, optional weave/wandb
├── rlm/              CppWorkspace, compile results, tool callables, CppRLM
└── stages/           Stage ABC, ArtifactStore, the five stages
```

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

Prompt text stays in `prompts/*.txt` — readable and diffable — with
`agent/prompting/manifest.json` carrying the metadata. Rebuild it after editing
a prompt:

```bash
python -m agent.cli prompts build
python -m agent.cli prompts show optim_w_trace --var query_id=7 --var sf=0.25 ...
```

The builder detects placeholders in both `${braced}` and bare `$named` form,
preserves hand-edited `stage`/`role`/`description`/`composes`, and bumps
`version` only when a file's content hash changes. It **fails** on a stray `$`
that is not a valid placeholder rather than letting `substitute` raise mid-run.

Rendering is strict: a missing placeholder is an error naming the prompt, file
and missing names, never a prompt that reaches the model still containing
`${query_id}`. Fragments compose automatically — every `optim_w_*.txt` opens
with `${constraints}`, which is filled from `optim_constraints` without being
passed.

## Caching

Two layers. Ours (`agent/llm/cache.py`) memoizes a **whole module invocation**
keyed on prompt fingerprint + inputs + model signature, stored as readable JSON
under `cache.dir`. DSPy's own LM cache sits underneath and covers what ours
cannot see: the individual completions inside a single RLM call, including its
recursive sub-calls.

Editing a prompt file changes its fingerprint and so invalidates exactly the
entries that depended on it. `cache.refresh` recomputes on every lookup while
still writing, which refreshes a stale answer without discarding the cache.

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

236 tests, none needing an API key: the LLM is DSPy's `DummyLM` and the C++
workspace is a real on-disk tree, so the cpplib integration is exercised for
real. The live RLM smoke test is gated behind `AGENT_LIVE_RLM=1` because it
costs tokens.
