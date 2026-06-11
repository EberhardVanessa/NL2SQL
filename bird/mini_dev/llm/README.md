# LLM Mini-Dev Pipeline

This folder contains two shell entrypoints for the mini-dev LLM workflow. Run them from this directory so the relative paths resolve correctly.

## `run_gpt.sh`

Runs `src/gpt_request.py` over the configured evaluation split.

Main uses:

- Generate SQL predictions for BIRD or Spider.
- Run schema linking before SQL generation.
- Run schema-linking only mode and write linker-evaluation JSONL output.
- Run error correction mechanism

Important settings near the top of the script:

- `dataset`: selects `bird` or `spider` and configures paths, dialect, mode, and knowledge usage.
- `engine`: model name passed to the LLM client.
- `num_threads`: number of worker threads passed as `--num_processes`.
- `max_fix_attempts`: number of local SQL validation/repair attempts.
- `langgraph_max_fix_attempts`: repair attempts delegated to the LangGraph provider.
- `schema_link_mode`: `none`, `table_only`, or `full`.
- `schema_link_only`: when `true`, skips SQL generation and only writes schema-linking outputs.
- `schema_link_representations`: comma-separated schema formats used by the linker, for example `ddl,m_schema,mac_schema`.
- `limit`: optional cap on the number of examples processed.
- `data_output_path`: output directory for prediction, debug, and linker-eval files.

Provider configuration is controlled by environment variables inside the script:

- `LLM_PROVIDER=local`: use local Ollama/OpenAI-compatible endpoint.
- `LLM_PROVIDER=remote`: call the REST endpoint in `LLM_REST_URL`.
- `LLM_PROVIDER=langgraph`: call the LangGraph schema-agent endpoint in `LANGGRAPH_URL`.

Example:

```sh
cd bird/mini_dev/llm
bash run_gpt.sh
```

Outputs include:

- `predict_<mode>_<engine>_<dialect>.json`: SQL predictions.
- `predict_<mode>_<engine>_<dialect>_debug.json`: per-question debug records.
- `*_linker_eval_*.jsonl`: schema-linker evaluation rows, especially useful when `schema_link_only=true`.

## `run_linker_eval.sh`

Runs `src/linker_eval.py` on a linker-evaluation JSONL file produced by `run_gpt.sh`.

Main uses:

- Compare predicted schema links against gold SQL.
- Produce a diagnostics CSV for inspecting linker quality.

Important settings:

- `input_jsonl`: linker-evaluation JSONL to evaluate. If empty, the script builds a default path from `mode`, `engine`, and `data_output_path`.
- `output_csv`: diagnostics output path. If empty, the script builds a default path.
- `dialect`: SQL dialect used by the evaluator, for example `postgres` or `sqlite`.

Example:

```sh
cd bird/mini_dev/llm
bash run_linker_eval.sh
```

The script writes a CSV containing linker diagnostics for the configured input JSONL.

## Notes

- The scripts use `py -u`, so they assume the Windows Python launcher is available.
- Install optional validation dependencies before running SQL validation paths: `sqlglot`, `psycopg2-binary`, `openai`, `requests`, and `tqdm`.
- `run_gpt.sh` currently includes PostgreSQL connection variables for BIRD validation: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`.
