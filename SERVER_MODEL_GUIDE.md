# EC-SQL Server Model Guide

This guide records the default A100 80GB server model matrix and the
HuggingFace snapshots downloaded by the one-click setup path.

## Default Runnable Ollama Matrix

The automated Spider2 server benchmark currently runs through the local
Ollama-compatible inference path.  The default models are:

```bash
export EC_SQL_MODELS=qwen3-vl:8b
export BASELINE_MODELS=qwen2.5-coder:7b,sqlcoder:7b,qwen3:32b
```

Each model writes its own JSON artifact under
`artifacts/server_runs/<RUN_ID>/`, for example
`spider2_sqlite_sota_baselines_qwen3_32b.json`.  Resume mode uses
`SKIP_EXISTING=1`, so completed artifacts are preserved rather than
overwritten.

## HuggingFace Snapshot Downloads

`bash scripts/one_click_linux.sh setup` creates the Python environment,
downloads Spider2, builds the manifest, pulls Ollama models, and downloads
the configured HuggingFace snapshots.  The same model download step can be
rerun with:

```bash
bash scripts/one_click_linux.sh models
```

Default HF snapshots:

```bash
export HF_HOME=/data/huggingface
export HF_BASELINE_MODELS=Qwen/Qwen3-32B,Qwen/Qwen2.5-Coder-32B-Instruct,deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct,bigcode/starcoder2-15b-instruct-v0.1,NumbersStation/nsql-6B
```

Optional heavy Text2SQL snapshot:

```bash
export HF_EXTRA_MODELS=defog/sqlcoder-70b-alpha
```

Use `HF_SKIP_DOWNLOAD=1` to skip HF downloads, or
`HF_DOWNLOAD_WARN_ONLY=1` to continue setup if a model snapshot fails.
Private or gated repositories may require `HF_TOKEN`.

## Recommended A100 80GB Baselines

- `Qwen/Qwen3-32B`: strong general reasoning and coding baseline aligned
  with the Ollama `qwen3:32b` comparison.
- `Qwen/Qwen2.5-Coder-32B-Instruct`: strong open code model with long
  context, useful for SQL generation and DBT repair prompts.
- `deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct`: efficient MoE code model
  with long context and good code-oriented reasoning.
- `bigcode/starcoder2-15b-instruct-v0.1`: permissive, transparent code
  baseline for reproducible code-generation comparisons.
- `NumbersStation/nsql-6B`: dedicated Text2SQL baseline; small enough to
  run quickly and useful as a domain-specific contrast.
- `defog/sqlcoder-70b-alpha`: dedicated Text2SQL model.  The full F16
  checkpoint is large, so prefer quantized serving, tensor parallelism, or
  a converted Ollama/vLLM deployment before adding it to the automatic
  matrix.

## Full Server Run

```bash
bash scripts/one_click_linux.sh setup
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh paper-launch
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh status
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh validate
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh evidence
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh bundle
```

The goal is not complete until the server result bundle is returned and
finalized locally with `scripts/finalize_server_result.py`.
