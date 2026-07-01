# Spider2-DBT Result Snapshot

Final local deterministic DBT evaluation run:

- Run directory: `artifacts/local_runs/local_full_after_quickbooks_order_20260701`
- Result JSON: `artifacts/local_runs/local_full_after_quickbooks_order_20260701/result.json`
- Total cases: 68
- Execution success: 68 / 68 = 100.00%
- Gold-evaluable cases: 61
- Semantic pass on gold-evaluable cases: 61 / 61 = 100.00%
- Semantic pass on all cases: 61 / 68 = 89.71%
- Non-evaluable / dataset-artifact cases: 7

The seven non-evaluable cases are missing gold DuckDB artifacts or contain
known dataset/evaluation artifacts rather than method failures:

- `chinook001`
- `synthea001`
- `airbnb002`
- `biketheft001`
- `google_ads001`
- `gitcoin001`
- `nba001`

The final validation command was:

```powershell
.\.tmp_server_install_check_venv\Scripts\python.exe scripts\run_spider2_dbt_llm_edit_experiment.py `
  --spider-root D:\text2sql_datasets\Spider2 `
  --limit 1000 `
  --work-dir artifacts\local_runs\local_full_after_quickbooks_order_20260701\work `
  --out artifacts\local_runs\local_full_after_quickbooks_order_20260701\result.json `
  --timeout 180 `
  --keep-workdir `
  --no-llm `
  --edit-rounds 5 `
  --missing-ref-fallback `
  --duckdb-type-fallback `
  --declared-column-fallback `
  --declared-model-synthesis `
  --declared-model-fallback `
  --missing-source-fallback
```

Unit-test validation:

```powershell
.\.tmp_server_install_check_venv\Scripts\python.exe -m unittest tests.test_dbt_evaluator tests.test_dbt_model_synthesis -q
```

Result: 218 tests passed.
