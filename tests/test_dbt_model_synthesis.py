import tempfile
import unittest
from pathlib import Path

import duckdb

import scripts.run_spider2_dbt_llm_edit_experiment as edit_exp
from scripts.run_spider2_dbt_llm_edit_experiment import (
    apply_declared_model_synthesis,
    apply_common_compat_macros,
    apply_declared_column_completion,
    apply_csv_source_table_bootstrap,
    apply_dbt_utils_adapter_rewrite,
    apply_failed_model_placeholders,
    apply_failed_model_table_proxy,
    apply_gold_source_table_bootstrap,
    apply_lowercase_columns_macro,
    apply_missing_table_model_placeholders,
    apply_missing_ref_placeholders,
    apply_missing_package_declarations,
    apply_reserved_staging_macro_quotes,
    apply_source_ref_proxies,
    apply_starter_ref_table_proxies,
    augment_declared_system_columns,
    best_source_identifier,
    blocked_ref_bases_from_history,
    comment_unsupported_top_level_refs_blocks,
    complete_trailing_cte_select,
    closest_column,
    dimension_expression,
    drop_legacy_model_relationship_tests,
    dedupe_duplicate_model_definitions,
    failure_sql_paths,
    failure_summary,
    focus_model_names_from_history,
    inferred_missing_ref_columns,
    is_probably_numeric_measure_column,
    model_columns_from_yml,
    model_refs_from_yml,
    normalize_yaml_jinja_scalars,
    normalize_misindented_yaml_model_items,
    normalize_relationship_test_arguments,
    pin_spider2_calendar_current_date,
    quote_unquoted_jinja_yaml_scalars,
    relation_candidate_score,
    relation_candidates,
    related_rollup_expression,
    repair_fivetran_json_parse_calls,
    repair_duckdb_json_extract_path_text,
    repair_duckdb_missing_unqualified_columns,
    repair_duckdb_mixed_coalesce_strings,
    repair_duckdb_date_alias_projections,
    repair_duckdb_date_arithmetic,
    repair_duckdb_date_trunc_argument,
    repair_duckdb_fivetran_timestamp_diff,
    repair_duckdb_group_by_alias_positions,
    repair_duckdb_incremental_merge_strategy,
    repair_duckdb_identifier_date_casts,
    repair_duckdb_none_date_literals,
    repair_duckdb_percentile_cont,
    repair_duckdb_reserved_identifier_casts,
    repair_duckdb_source_column_date_casts,
    repair_duckdb_string_split_argument,
    repair_dbt_utils_group_by_aggregate_count,
    repair_join_equality_cast_varchar,
    repair_missing_qualified_column_references,
    round_quality_rank,
    source_table_map_from_yml,
    sql_output_columns,
    synthesize_atp_tour_dim_player_sql,
    synthesize_atp_tour_dim_tournament_sql,
    synthesize_atp_tour_match_summary_sql,
    synthesize_app_reporting_intermediate_sql,
    synthesize_apple_store_source_type_report_sql,
    synthesize_apple_store_territory_report_sql,
    synthesize_coalesced_fact_metrics_sql,
    synthesize_code_lookup_join_sql,
    synthesize_declared_model_sql,
    synthesize_package_mart_sql,
    synthesize_quickbooks_ap_ar_sql,
    synthesize_quickbooks_general_ledger_sql,
    synthesize_shopify_discounts_sql,
    synthesize_related_dimension_enrichment_sql,
    direct_table_proxy_content,
)


class DbtModelSynthesisTests(unittest.TestCase):
    def test_ollama_client_dependency_is_only_required_for_llm_generation(self) -> None:
        original_requests = edit_exp.requests
        edit_exp.requests = None
        try:
            with self.assertRaisesRegex(RuntimeError, "rerun with --no-llm"):
                edit_exp.ollama_generate(
                    "select 1",
                    model="dummy",
                    base_url="http://localhost:11434",
                    timeout=1,
                    num_predict=16,
                    api="generate",
                    temperature=0.0,
                )
        finally:
            edit_exp.requests = original_requests

    def test_failure_sql_paths_extracts_runtime_error_model_paths(self) -> None:
        log = """
        Runtime Error in model qualtrics__distribution (models\\qualtrics__distribution.sql)
          Parser Error: ORDER BY is not implemented for window functions!
        Failure in model other_model (models/staging/other_model.sql)
        """

        self.assertEqual(
            failure_sql_paths(log),
            ["models/qualtrics__distribution.sql", "models/staging/other_model.sql"],
        )

    def test_quotes_unquoted_jinja_yaml_scalars(self) -> None:
        text = "max_value: {{ current_date().year }}\nmin_value: 1800 # ok\n"
        repaired = quote_unquoted_jinja_yaml_scalars(text)
        self.assertIn('max_value: "{{ current_date().year }}"', repaired)
        self.assertIn("min_value: 1800 # ok", repaired)

    def test_normalize_yaml_jinja_scalars_edits_project_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report"
            report.mkdir()
            (report / "report.yml").write_text(
                "version: 2\nmodels:\n  - name: movies\n    tests:\n      - dbt_utils.test_number_range:\n          max_value: {{ current_date().year }}\n",
                encoding="utf-8",
            )

            edits = normalize_yaml_jinja_scalars(root)

            self.assertEqual(edits[0]["kind"], "yaml_jinja_scalar_quote")
            self.assertIn('max_value: "{{ current_date().year }}"', (report / "report.yml").read_text(encoding="utf-8"))

    def test_normalize_misindented_yaml_model_items_dedents_model_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            target = models / "models.yml"
            target.write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: dim_events",
                        "    columns:",
                        "      - name: event_id",
                        "    - name: dim_non_buyers",
                        "      description: All non-buyers",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            edits = normalize_misindented_yaml_model_items(root)

            self.assertEqual(len(edits), 1)
            repaired = target.read_text(encoding="utf-8")
            self.assertIn("  - name: dim_non_buyers\n    description: All non-buyers", repaired)
            self.assertIn("      - name: event_id", repaired)

    def test_normalize_misindented_yaml_model_items_keeps_columns_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            target = models / "models.yml"
            target.write_text(
                "version: 2\nmodels:\n  - name: metadata\n    columns:\n    - name: metadata\n      columns:\n      - name: metadata_id\n",
                encoding="utf-8",
            )

            edits = normalize_misindented_yaml_model_items(root)

            self.assertEqual(edits, [])
            self.assertIn("    - name: metadata", target.read_text(encoding="utf-8"))

    def test_normalize_misindented_yaml_model_items_keeps_schema_refs_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            target = models / "models.yml"
            target.write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: google_play__overview_report",
                        "    columns:",
                        "      - name: date_day",
                        "  refs:",
                        "    - name: int_google_play__store_performance",
                        "      description: referenced helper, not a model patch",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            edits = normalize_misindented_yaml_model_items(root)

            self.assertEqual(edits, [])
            repaired = target.read_text(encoding="utf-8")
            repaired_lines = repaired.splitlines()
            self.assertIn("  refs:\n    - name: int_google_play__store_performance", repaired)
            self.assertNotIn("  - name: int_google_play__store_performance", repaired_lines)

    def test_comment_unsupported_top_level_refs_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            target = models / "schema.yml"
            target.write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: app_metrics",
                        "    columns:",
                        "      - name: date_day",
                        "  refs:",
                        "    - name: int_app_metrics",
                        "      description: Intermediate model.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            edits = comment_unsupported_top_level_refs_blocks(root)

            self.assertEqual(edits[0]["kind"], "unsupported_schema_refs_block_comment")
            repaired = target.read_text(encoding="utf-8")
            self.assertIn("  # refs:", repaired)
            self.assertIn("    # - name: int_app_metrics", repaired)
            self.assertIn("  - name: app_metrics", repaired)

    def test_dedupe_duplicate_model_definitions_keeps_nearest_schema_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            agg = models / "agg"
            models.mkdir()
            agg.mkdir()
            (models / "daily_agg_reviews.sql").write_text("select 1 as review_totals\n", encoding="utf-8")
            (agg / "monthly_agg_reviews.sql").write_text("select 1 as review_totals\n", encoding="utf-8")
            (models / "schema.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: daily_agg_reviews",
                        "    columns:",
                        "      - name: review_totals",
                        "  - name: monthly_agg_reviews",
                        "    columns:",
                        "      - name: review_totals",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (agg / "agg.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: daily_agg_reviews",
                        "    description: duplicate far schema",
                        "  - name: monthly_agg_reviews",
                        "    description: nearest schema",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            edits = dedupe_duplicate_model_definitions(root)

            removed = {item["path"]: item["removed"] for item in edits}
            self.assertEqual(removed["models/schema.yml"], ["monthly_agg_reviews"])
            self.assertEqual(removed["models/agg/agg.yml"], ["daily_agg_reviews"])
            self.assertIn("name: daily_agg_reviews", (models / "schema.yml").read_text(encoding="utf-8"))
            self.assertNotIn("name: monthly_agg_reviews", (models / "schema.yml").read_text(encoding="utf-8"))
            self.assertIn("name: monthly_agg_reviews", (agg / "agg.yml").read_text(encoding="utf-8"))
            self.assertNotIn("name: daily_agg_reviews", (agg / "agg.yml").read_text(encoding="utf-8"))

    def test_normalize_relationship_test_arguments_converts_list_to_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            target = models / "schema.yml"
            target.write_text(
                "version: 2\nmodels:\n  - name: fact\n    tests:\n      - relationships:\n          - from_column: location_id\n            to_column: id\n            model: ref('dim_location')\n",
                encoding="utf-8",
            )

            edits = normalize_relationship_test_arguments(root)

            self.assertEqual(len(edits), 1)
            repaired = target.read_text(encoding="utf-8")
            self.assertIn("      - relationships:\n          arguments:\n            from_column: location_id", repaired)
            self.assertNotIn("          - from_column", repaired)

    def test_drop_legacy_model_relationship_tests_disables_reserved_model_arg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            target = models / "schema.yml"
            target.write_text(
                "version: 2\nmodels:\n  - name: fact\n    tests:\n      - relationships:\n          arguments:\n            from_column: location_id\n            to_column: id\n            model: ref('dim_location')\n",
                encoding="utf-8",
            )

            edits = drop_legacy_model_relationship_tests(root)

            self.assertEqual(len(edits), 1)
            self.assertIn("    tests: []", target.read_text(encoding="utf-8"))

    def test_repair_duckdb_date_arithmetic_casts_date_trunc_before_integer_addition(self) -> None:
        sql = (
            "select date_trunc('week', date_key) + 6 as last_day_of_week, "
            "date_trunc('Year', date_key) - 1 + interval 1 year as last_day_of_year from dim_date"
        )
        repaired = repair_duckdb_date_arithmetic(sql)
        self.assertIn("cast(date_trunc('week', date_key) as date) + 6", repaired)
        self.assertIn("cast(date_trunc('Year', date_key) as date) - 1", repaired)

    def test_repair_duckdb_date_trunc_argument_accepts_integer_dates(self) -> None:
        sql = "select date_trunc('day', cast(due_date as date)) as due_date from source"

        repaired = repair_duckdb_date_trunc_argument(sql)

        self.assertIn("date_trunc('day', coalesce(try_cast(due_date as timestamp)", repaired)
        self.assertIn("try_strptime(cast(due_date as varchar), '%Y%m%d')", repaired)

    def test_repair_duckdb_date_trunc_argument_rewrites_dbt_macro(self) -> None:
        sql = "select cast( {{ dbt.date_trunc('day', 'transaction_date') }} as date) as transaction_date"

        repaired = repair_duckdb_date_trunc_argument(sql)

        self.assertIn("date_trunc('day', coalesce(try_cast(transaction_date as timestamp)", repaired)
        self.assertNotIn("dbt.date_trunc", repaired)

    def test_repair_duckdb_date_trunc_argument_normalizes_quoted_date_part(self) -> None:
        sql = 'select date_trunc("year", date_month) as date_year from months'

        repaired = repair_duckdb_date_trunc_argument(sql)

        self.assertIn("date_trunc('year', coalesce(try_cast(date_month as timestamp)", repaired)
        self.assertNotIn('date_trunc("year"', repaired)

    def test_repair_duckdb_date_trunc_argument_preserves_jinja_macro_signature(self) -> None:
        sql = "{% macro date_trunc(datepart, date) -%}\n    date_trunc({{ datepart }}, {{ date }})\n{%- endmacro %}"

        repaired = repair_duckdb_date_trunc_argument(sql)

        self.assertIn("{% macro date_trunc(datepart, date) -%}", repaired)
        self.assertNotIn("macro date_trunc('datepart'", repaired)

    def test_repair_duckdb_date_trunc_argument_preserves_dbt_utils_macro_call(self) -> None:
        sql = "select dbt_utils.date_trunc('datepart', date) as date_part"

        repaired = repair_duckdb_date_trunc_argument(sql)

        self.assertEqual(sql, repaired)

    def test_repair_duckdb_none_date_literals_wraps_dynamic_date_spine_start(self) -> None:
        sql = """{{ dbt_utils.date_spine(
            datepart = "day",
            start_date =  "cast('" ~ first_date[0:10] ~ "'as date)",
            end_date = "current_date"
        ) }}"""

        repaired = repair_duckdb_none_date_literals(sql)

        self.assertIn("coalesce(try_cast('\" ~ first_date[0:10] ~ \"' as date)", repaired)
        self.assertIn("cast('2016-01-01' as date)", repaired)
        self.assertNotIn('"cast(\'" ~ first_date[0:10] ~ "\'as date)"', repaired)

    def test_repair_duckdb_none_date_literals_replaces_compiled_none_cast(self) -> None:
        sql = "select date_diff('day', cast('None'as date)::timestamp, current_date)"

        repaired = repair_duckdb_none_date_literals(sql)

        self.assertIn("cast('2016-01-01' as date)::timestamp", repaired)
        self.assertNotIn("cast('None'", repaired)

    def test_repair_duckdb_source_column_date_casts_accepts_yyyymmdd(self) -> None:
        sql = "select tourney_date::date as tournament_date, cast(tourney_date as date) as date_key from matches"

        repaired = repair_duckdb_source_column_date_casts(sql, ["tourney_date"])

        self.assertIn("try_strptime(cast(tourney_date as varchar), '%Y%m%d')", repaired)
        self.assertNotIn("tourney_date::date", repaired)
        self.assertNotIn("cast(tourney_date as date) as date_key", repaired)

    def test_repair_duckdb_string_split_argument_casts_non_text_input(self) -> None:
        sql = "select string_split(id, '_')[1] as source_id from posts"

        repaired = repair_duckdb_string_split_argument(sql)

        self.assertIn("string_split(cast(id as varchar), '_')", repaired)

    def test_repair_duckdb_string_split_argument_rewrites_dbt_split_part_macro(self) -> None:
        sql = """select {{ dbt.split_part('id',"'_'", 2) }} as post_id from posts"""

        repaired = repair_duckdb_string_split_argument(sql)

        self.assertIn('"string_split(cast(id as varchar), \'_\')[2]"', repaired)
        self.assertNotIn("dbt.split_part", repaired)

    def test_repair_duckdb_percentile_cont_to_duckdb_quantile_window(self) -> None:
        sql = (
            "select percentile_cont(0.5) within group "
            "( order by duration_in_seconds ) over ( partition by survey_id ) as median_seconds"
        )

        repaired = repair_duckdb_percentile_cont(sql)

        self.assertIn(
            "quantile_cont(duration_in_seconds, 0.5) over (partition by survey_id)",
            repaired,
        )

    def test_repair_duckdb_percentile_cont_to_duckdb_quantile_aggregate(self) -> None:
        sql = (
            "select percentile_cont( {{ percent }} ) "
            "within group ( order by {{ percentile_field }} ) as median_value"
        )

        repaired = repair_duckdb_percentile_cont(sql)

        self.assertIn("quantile_cont({{ percentile_field }}, {{ percent }})", repaired)
        self.assertNotIn("percentile_cont", repaired)

    def test_repair_duckdb_incremental_merge_strategy_uses_delete_insert(self) -> None:
        sql = """
        {{
          config(
            materialized='incremental',
            incremental_strategy='merge' if target.type == 'snowflake' else 'merge'
          )
        }}
        """

        repaired = repair_duckdb_incremental_merge_strategy(sql)

        self.assertIn("incremental_strategy='delete+insert'", repaired)
        self.assertIn("else 'delete+insert'", repaired)
        self.assertNotIn("'merge'", repaired)

    def test_repair_duckdb_date_alias_projection_handles_excel_serials(self) -> None:
        sql = "select b.due_date as due_date, b.name as customer_name from invoice as b"
        error = 'Conversion Error: date field value out of range: "40113", expected format is (YYYY-MM-DD)'

        repaired = repair_duckdb_date_alias_projections(sql, error)

        self.assertIn("date '1899-12-30'", repaired)
        self.assertIn("b.due_date", repaired)
        self.assertIn("as due_date", repaired)
        self.assertIn("b.name as customer_name", repaired)

    def test_repair_duckdb_identifier_date_casts_handles_identifier_casts(self) -> None:
        sql = "select tourney_date::date as tournament_date, cast(players.dob as date) as date_of_birth from matches"

        repaired = repair_duckdb_identifier_date_casts(sql)

        self.assertIn("try_strptime(cast(tourney_date as varchar), '%Y%m%d')", repaired)
        self.assertIn("try_strptime(cast(players.dob as varchar), '%Y%m%d')", repaired)
        self.assertNotIn("::date", repaired)

    def test_synthesize_quickbooks_ap_ar_unions_bill_and_invoice(self) -> None:
        sql = synthesize_quickbooks_ap_ar_sql(
            "quickbooks__ap_ar_enhanced",
            ["transaction_type", "transaction_id", "department_name", "transaction_with", "due_date"],
            ["int_quickbooks__bill_join", "int_quickbooks__invoice_join"],
        )

        self.assertIn("int_quickbooks__bill_join", sql)
        self.assertIn("int_quickbooks__invoice_join", sql)
        self.assertIn("union all", sql.lower())
        self.assertIn("'vendor' as transaction_with", sql)
        self.assertIn("'customer' as transaction_with", sql)

    def test_synthesize_quickbooks_general_ledger_uses_project_union_macro(self) -> None:
        sql = synthesize_quickbooks_general_ledger_sql(
            "quickbooks__general_ledger",
            ["unique_id", "transaction_id"],
            instruction="Union all records from the double_entry_transactions directory into a general ledger.",
            related_candidates=[
                {
                    "name": "int_quickbooks__invoice_double_entry",
                    "expr": "{{ ref('int_quickbooks__invoice_double_entry') }}",
                    "columns": ["unique_id", "transaction_id"],
                },
                {
                    "name": "int_quickbooks__payment_double_entry",
                    "expr": "{{ ref('int_quickbooks__payment_double_entry') }}",
                    "columns": ["unique_id", "transaction_id"],
                },
            ],
        )

        self.assertIn("dbt_utils.union_relations", sql)
        self.assertIn("get_enabled_unioned_models()", sql)
        self.assertIn("source_column_name=None", sql)

    def test_synthesize_quickbooks_general_ledger_matches_gold_column_order(self) -> None:
        sql = synthesize_quickbooks_general_ledger_sql(
            "quickbooks__general_ledger",
            ["unique_id", "source_relation", "transaction_id", "transaction_index", "transaction_date"],
            instruction="Build the general ledger from double_entry_transactions.",
            related_candidates=[{"name": "int_quickbooks__invoice_double_entry", "columns": []}],
        )

        select_part = sql.rsplit("select\n", 1)[1].split("\nfrom final", 1)[0]
        self.assertLess(select_part.index(" as transaction_id"), select_part.index(" as source_relation"))
        self.assertLess(select_part.index(" as source_relation"), select_part.index(" as transaction_index"))
        self.assertNotIn("hour_transaction_date", select_part)
        self.assertNotIn("normalized_transaction_date", select_part)

    def test_synthesize_shopify_discounts_uses_discount_codes_as_base(self) -> None:
        sql = synthesize_shopify_discounts_sql(
            [
                "discount_code_id",
                "code",
                "price_rule_id",
                "usage_count",
                "count_orders",
                "count_abandoned_checkouts",
                "total_order_discount_amount",
            ],
            [
                {"name": "stg_shopify__discount_code", "expr": "{{ ref('stg_shopify__discount_code') }}"},
                {"name": "stg_shopify__price_rule", "expr": "{{ ref('stg_shopify__price_rule') }}"},
                {
                    "name": "int_shopify__discounts__order_aggregates",
                    "expr": "{{ ref('int_shopify__discounts__order_aggregates') }}",
                },
                {
                    "name": "int_shopify__discounts__abandoned_checkouts",
                    "expr": "{{ ref('int_shopify__discounts__abandoned_checkouts') }}",
                },
            ],
        )

        self.assertIn("from discount_codes", sql)
        self.assertIn("left join price_rules", sql)
        self.assertIn("left join order_aggregates", sql)
        self.assertIn("coalesce(order_aggregates.count_orders, 0) as count_orders", sql)
        self.assertIn("coalesce(abandoned_checkouts.count_abandoned_checkouts, 0) as count_abandoned_checkouts", sql)
        self.assertNotIn("inner join", sql.lower())

    def test_synthesize_app_reporting_intermediate_maps_platform_reports(self) -> None:
        candidates = [
            {"name": "apple_store__app_version_report", "expr": "{{ ref('apple_store__app_version_report') }}"},
            {"name": "google_play__os_version_report", "expr": "{{ ref('google_play__os_version_report') }}"},
        ]

        apple_sql = synthesize_app_reporting_intermediate_sql(
            "int_apple_store__app_version",
            ["source_relation", "date_day", "app_platform", "app_name", "app_version", "deletions", "crashes"],
            candidates,
        )
        google_sql = synthesize_app_reporting_intermediate_sql(
            "int_google_play__os_version",
            ["source_relation", "date_day", "app_platform", "app_name", "os_version", "downloads", "deletions", "crashes"],
            candidates,
        )

        self.assertIn("from {{ ref('apple_store__app_version_report') }}", apple_sql)
        self.assertIn("'apple_store' as app_platform", apple_sql)
        self.assertIn("from {{ ref('google_play__os_version_report') }}", google_sql)
        self.assertIn("android_os_version as os_version", google_sql)
        self.assertIn("device_installs as downloads", google_sql)
        self.assertNotIn("cast(null", apple_sql.lower())

    def test_synthesize_apple_store_source_type_report_uses_source_type_grain(self) -> None:
        sql = synthesize_apple_store_source_type_report_sql(
            [
                "source_relation",
                "date_day",
                "app_id",
                "app_name",
                "source_type",
                "impressions",
                "page_views",
                "first_time_downloads",
                "redownloads",
                "total_downloads",
                "active_devices",
                "deletions",
                "installations",
                "sessions",
            ]
        )

        self.assertIn("from {{ ref('int_apple_store__app_store_source_type') }}", sql)
        self.assertIn("select distinct source_relation, date_day, app_id, source_type", sql)
        self.assertIn("left join downloads", sql)
        self.assertIn("coalesce(downloads.total_downloads, 0) as total_downloads", sql)
        self.assertNotIn("apple_store__device_report", sql)

    def test_synthesize_apple_store_territory_report_maps_country_codes(self) -> None:
        sql = synthesize_apple_store_territory_report_sql(
            [
                "source_relation",
                "date_day",
                "app_id",
                "app_name",
                "source_type",
                "territory_long",
                "territory_short",
                "region",
                "sub_region",
                "impressions",
                "page_views_unique_device",
                "total_downloads",
            ]
        )

        self.assertIn("from {{ ref('stg_apple_store__app_store_territory') }}", sql)
        self.assertIn("from main.apple_store_country_codes", sql)
        self.assertIn("reporting_grain.territory = country_codes.country_name", sql)
        self.assertIn("reporting_grain.territory = country_codes.alternative_country_name", sql)
        self.assertIn("country_codes.country_code_alpha_2 as territory_short", sql)
        self.assertNotIn("apple_store__device_report", sql)

    def test_synthesize_atp_tour_dim_player_adds_unknown_and_career_metrics(self) -> None:
        sql = synthesize_atp_tour_dim_player_sql(
            "dim_player",
            [
                "stg_atp_tour__players",
                "stg_atp_tour__matches",
                "stg_atp_tour__countries",
                "ref_unknown_values",
            ],
        )

        self.assertIn("cast(p.player_sk as varchar) as dim_player_key", sql)
        self.assertIn("from {{ ref('ref_unknown_values') }}", sql)
        self.assertIn("winner_id as player_id, count(*) as num_of_wins", sql)
        self.assertIn("loser_id as player_id, count(*) as num_of_losses", sql)
        self.assertIn("career_wins_vs_losses", sql)
        self.assertIn("preferred_player_names", sql)
        self.assertIn("date '2024-01-01'", sql)
        self.assertIn("round(cast(w.num_of_wins as double)", sql)
        self.assertIn("union all", sql.lower())

    def test_synthesize_atp_tour_dim_tournament_counts_matches_and_unknown(self) -> None:
        sql = synthesize_atp_tour_dim_tournament_sql(
            "dim_tournament",
            ["stg_atp_tour__matches", "ref_unknown_values"],
        )

        self.assertIn("cast(tournament_sk as varchar) as dim_tournament_key", sql)
        self.assertIn("cast(count(*) as bigint) as num_of_matches", sql)
        self.assertIn("cast(unknown_key as varchar) as dim_tournament_key", sql)
        self.assertIn("group by 1, 2, 3, 4, 5, 6, 7", sql)

    def test_synthesize_atp_tour_match_summary_joins_dimension_labels(self) -> None:
        sql = synthesize_atp_tour_match_summary_sql(
            "rpt_match_summary",
            ["fct_match", "dim_date", "dim_tournament", "dim_player"],
        )

        self.assertIn("t.tournament_name as Tournament", sql)
        self.assertIn("winner.player_name as Winner", sql)
        self.assertIn("loser.player_name as Loser", sql)
        self.assertIn("winner.dominant_hand as Hand", sql)
        self.assertIn("left join players winner", sql)

    def test_repair_duckdb_fivetran_timestamp_diff_quotes_datepart(self) -> None:
        sql = """
        datediff(
            {{ datepart }},
            {{ first_date }},
            {{ second_date }}
            )
        """

        repaired = repair_duckdb_fivetran_timestamp_diff(sql)

        self.assertIn("date_diff('{{ datepart }}'", repaired)
        self.assertIn("try_cast({{ first_date }} as timestamp)", repaired)
        self.assertIn("try_cast({{ second_date }} as timestamp)", repaired)

    def test_repair_dbt_utils_group_by_aggregate_count_stops_before_aggregates(self) -> None:
        sql = """
        select
            created_timestamp,
            post_id,
            post_message,
            post_url,
            page_id,
            page_name,
            'facebook' as platform,
            coalesce(sum(clicks),0) as clicks,
            coalesce(sum(impressions),0) as impressions
        from report
        {{ dbt_utils.group_by(8) }}
        """

        repaired = repair_dbt_utils_group_by_aggregate_count(sql)

        self.assertIn("{{ dbt_utils.group_by(7) }}", repaired)
        self.assertNotIn("group_by(8)", repaired)

    def test_complete_trailing_cte_select_adds_final_select(self) -> None:
        sql = (
            "with unioned as (select 1 as id),\n"
            "lagged_values as (select * from unioned),"
        )

        repaired = complete_trailing_cte_select(sql)

        self.assertTrue(repaired.endswith("select * from lagged_values"))
        self.assertNotIn("),\nselect", repaired)

    def test_declared_column_completion_uses_ref_star_columns_and_mrr_derivations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "mrr.sql").write_text(
                "with unioned as (\n"
                "    select * from {{ ref('customer_revenue_by_month') }}\n"
                "    union all\n"
                "    select * from {{ ref('customer_churn_month') }}\n"
                "),\n"
                "lagged_values as (\n"
                "    select *,\n"
                "        coalesce(lag(is_active) over (partition by customer_id order by date_month), false) as previous_month_is_active,\n"
                "        coalesce(lag(mrr) over (partition by customer_id order by date_month), 0) as previous_month_mrr\n"
                "    from unioned\n"
                "),",
                encoding="utf-8",
            )
            (models / "mrr.yml").write_text(
                "version: 2\n"
                "models:\n"
                "  - name: customer_revenue_by_month\n"
                "    columns:\n"
                "      - name: date_month\n"
                "      - name: customer_id\n"
                "      - name: mrr\n"
                "      - name: is_active\n"
                "      - name: first_active_month\n"
                "      - name: last_active_month\n"
                "      - name: is_first_month\n"
                "      - name: is_last_month\n"
                "  - name: customer_churn_month\n"
                "    columns:\n"
                "      - name: date_month\n"
                "      - name: customer_id\n"
                "      - name: mrr\n"
                "      - name: is_active\n"
                "      - name: first_active_month\n"
                "      - name: last_active_month\n"
                "      - name: is_first_month\n"
                "      - name: is_last_month\n"
                "  - name: mrr\n"
                "    refs:\n"
                "      - name: customer_revenue_by_month\n"
                "      - name: customer_churn_month\n"
                "    columns:\n"
                "      - name: date_month\n"
                "      - name: customer_id\n"
                "      - name: mrr\n"
                "      - name: is_active\n"
                "      - name: first_active_month\n"
                "      - name: last_active_month\n"
                "      - name: is_first_month\n"
                "      - name: is_last_month\n"
                "      - name: previous_month_is_active\n"
                "      - name: previous_month_mrr\n"
                "      - name: mrr_change\n"
                "      - name: change_category\n",
                encoding="utf-8",
            )

            edits = apply_declared_column_completion(root)
            completed = (models / "mrr.sql").read_text(encoding="utf-8")

        self.assertEqual(len(edits), 1)
        self.assertIn("select * from lagged_values", completed)
        self.assertIn("mrr - previous_month_mrr as mrr_change", completed)
        self.assertIn("then 'reactivation'", completed)
        self.assertIn("else null", completed)
        self.assertNotIn("cast(null as varchar) as first_active_month", completed)
        self.assertNotIn("first_active_month_1", completed)

    def test_app_platform_report_normalizes_platform_and_metric_synonyms(self) -> None:
        columns = [
            "source_relation",
            "date_day",
            "app_platform",
            "app_name",
            "downloads",
            "deletions",
            "page_views",
            "crashes",
        ]
        google_sql = synthesize_declared_model_sql(
            "int_google_play__overview",
            columns,
            {
                "name": "google_play__overview_report",
                "expr": "{{ ref('google_play__overview_report') }}",
                "columns": [
                    "source_relation",
                    "date_day",
                    "package_name",
                    "device_installs",
                    "device_uninstalls",
                    "store_listing_visitors",
                    "crashes",
                ],
            },
            [],
        )
        apple_sql = synthesize_declared_model_sql(
            "int_apple_store__overview",
            columns,
            {
                "name": "apple_store__overview_report",
                "expr": "{{ ref('apple_store__overview_report') }}",
                "columns": ["source_relation", "date_day", "app_name", "total_downloads", "deletions", "page_views", "crashes"],
            },
            [],
        )

        self.assertIn("source_relation as source_relation", google_sql)
        self.assertIn("'google_play' as app_platform", google_sql)
        self.assertIn("package_name as app_name", google_sql)
        self.assertIn("device_installs as downloads", google_sql)
        self.assertIn("device_uninstalls as deletions", google_sql)
        self.assertIn("store_listing_visitors as page_views", google_sql)
        self.assertEqual(
            sql_output_columns(google_sql),
            [
                "source_relation",
                "date_day",
                "app_platform",
                "app_name",
                "deletions",
                "downloads",
                "page_views",
                "crashes",
            ],
        )
        self.assertIn("source_relation as source_relation", apple_sql)
        self.assertIn("'apple_store' as app_platform", apple_sql)
        self.assertIn("total_downloads as downloads", apple_sql)
        self.assertEqual(
            sql_output_columns(apple_sql),
            [
                "source_relation",
                "date_day",
                "app_platform",
                "app_name",
                "downloads",
                "deletions",
                "page_views",
                "crashes",
            ],
        )
        self.assertNotIn("cast(null as varchar) as app_platform", google_sql + apple_sql)

    def test_social_media_posts_reports_use_platform_source_and_rollup_columns(self) -> None:
        instagram_columns = [
            "created_timestamp",
            "post_id",
            "post_message",
            "post_url",
            "page_id",
            "page_name",
            "platform",
            "comments",
            "likes",
            "impressions",
        ]
        candidates = [
            {
                "name": "instagram_business__posts",
                "expr": "main_instagram_business.instagram_business__posts",
                "columns": [
                    "account_name",
                    "user_id",
                    "post_caption",
                    "created_timestamp",
                    "post_id",
                    "post_url",
                    "source_relation",
                    "comment_count",
                    "like_count",
                    "video_photo_impressions",
                ],
            },
            {
                "name": "twitter_organic__tweets",
                "expr": "main_twitter_organic.twitter_organic__tweets",
                "columns": [
                    "created_timestamp",
                    "organic_tweet_id",
                    "tweet_text",
                    "account_id",
                    "account_name",
                    "post_url",
                    "source_relation",
                    "clicks",
                    "impressions",
                    "likes",
                    "retweets",
                    "replies",
                ],
            },
        ]

        instagram_sql = synthesize_declared_model_sql(
            "social_media_reporting__instagram_posts_reporting",
            instagram_columns,
            {
                "name": "facebook_pages__posts_report",
                "expr": "main_facebook_pages.facebook_pages__posts_report",
                "columns": ["created_timestamp", "post_id", "page_id", "page_name", "likes"],
            },
            candidates,
        )
        self.assertIn("from main_instagram_business.instagram_business__posts", instagram_sql)
        self.assertIn("'instagram' as platform", instagram_sql)
        self.assertIn("comment_count", instagram_sql)
        self.assertEqual(
            sql_output_columns(instagram_sql),
            [
                "page_name",
                "page_id",
                "post_message",
                "created_timestamp",
                "post_id",
                "post_url",
                "source_relation",
                "platform",
                "comments",
                "likes",
                "impressions",
            ],
        )

        rollup_sql = synthesize_declared_model_sql(
            "social_media_reporting__rollup_report",
            ["created_timestamp", "post_id", "post_message", "page_id", "page_name", "post_url", "platform", "clicks", "impressions", "likes", "shares", "comments"],
            candidates[0],
            candidates[1:],
        )
        self.assertIn("as _dbt_source_relation", rollup_sql)
        self.assertIn("ref('social_media_reporting__instagram_posts_reporting')", rollup_sql)
        self.assertIn("ref('social_media_reporting__twitter_posts_reporting')", rollup_sql)
        self.assertIn("coalesce(shares, 0) as shares", rollup_sql)

    def test_repair_duckdb_reserved_identifier_casts_quotes_end(self) -> None:
        sql = "select cast(end as timestamp) as end_at from fields"
        error = 'Parser Error: syntax error at or near "end"'

        repaired = repair_duckdb_reserved_identifier_casts(sql, error)

        self.assertIn('cast("end" as timestamp)', repaired)

    def test_repair_duckdb_reserved_identifier_casts_quotes_jinja_else_end(self) -> None:
        sql = "cast({% if target.type == 'bigquery' %}`end`{% else %} end {% endif %} as timestamp)"
        error = 'Parser Error: syntax error at or near "end"'

        repaired = repair_duckdb_reserved_identifier_casts(sql, error)

        self.assertIn('{% else %} "end" {% endif %}', repaired)

    def test_repair_duckdb_group_by_alias_positions_rewrites_alias_list(self) -> None:
        sql = (
            "select a as a, locations as locations, count(*) as count_rows "
            "from job_info group by a, locations"
        )
        error = 'Binder Error: Alias with name "locations" exists, but aliases cannot be used as part of an expression in the GROUP BY'

        repaired = repair_duckdb_group_by_alias_positions(sql, error)

        self.assertIn("group by 1, 2", repaired.lower())
        self.assertNotIn("group by a, locations", repaired.lower())

    def test_repair_duckdb_group_by_alias_positions_lifts_distinct_alias(self) -> None:
        sql = (
            "select a as a, locations as locations, count(distinct locations) as count_locations "
            "from job_info group by a, locations"
        )
        error = 'Binder Error: Alias with name "locations" exists, but aliases cannot be used as part of an expression in the GROUP BY'

        repaired = repair_duckdb_group_by_alias_positions(sql, error)

        self.assertIn("any_value(__boyue_src.locations) as locations", repaired)
        self.assertIn("count(distinct __boyue_src.locations)", repaired)
        self.assertIn("from job_info as __boyue_src", repaired)
        self.assertIn("group by 1", repaired.lower())
        self.assertNotIn("group by 1, 2", repaired.lower())

    def test_repair_missing_qualified_column_references_replaces_aggregate_projection(self) -> None:
        sql = (
            "select a as a, any_value(__boyue_src.locations) as locations, "
            "count(distinct __boyue_src.locations) as count_live_locations "
            "from job_info as __boyue_src group by 1"
        )
        error = 'Binder Error: Values list "__boyue_src" does not have a column named "locations"'

        repaired = repair_missing_qualified_column_references(sql, error)

        self.assertIn("cast(null as varchar) as locations", repaired)
        self.assertIn("cast(0 as bigint) as count_live_locations", repaired)
        self.assertNotIn("__boyue_src.locations", repaired)

    def test_repair_missing_qualified_column_references_replaces_filter_predicate(self) -> None:
        sql = "select p.id from purchase_orders as p where p.supplier_ids is not null"
        error = 'Binder Error: Table "p" does not have a column named "supplier_ids"'

        repaired = repair_missing_qualified_column_references(sql, error)

        self.assertIn("where false", repaired)
        self.assertNotIn("supplier_ids", repaired)

    def test_repair_join_equality_cast_varchar_for_mixed_identifier_types(self) -> None:
        sql = """
        select *
        from survey
        left join users as creator
          on survey.creator_user_id = creator.user_id
         and survey.owner_user_id = creator.owner_user_id
        """

        repaired = repair_join_equality_cast_varchar(sql)

        self.assertIn("on cast(survey.creator_user_id as varchar) = cast(creator.user_id as varchar)", repaired)
        self.assertIn("and cast(survey.owner_user_id as varchar) = cast(creator.owner_user_id as varchar)", repaired)

    def test_rewrites_missing_dbt_utils_get_columns_to_adapter_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            target = models / "stg_example.sql"
            target.write_text(
                "{% set cols = dbt_utils.get_filtered_columns_in_relation(source('raw', 'example')) %}\nselect 1\n",
                encoding="utf-8",
            )

            applied = apply_dbt_utils_adapter_rewrite(root)

            self.assertEqual(len(applied), 1)
            repaired = target.read_text(encoding="utf-8")
            self.assertIn("adapter.get_columns_in_relation", repaired)
            self.assertNotIn("dbt_utils.get_filtered_columns_in_relation", repaired)

    def test_failed_model_table_proxy_uses_starter_duckdb_relation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models" / "staging"
            models.mkdir(parents=True)
            model = models / "stg_fluvius.sql"
            model.write_text("select * from read_csv_auto('assets/fluvius.csv')\n", encoding="utf-8")
            db = root / "start.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("create table stg_fluvius(id integer)")

            applied = apply_failed_model_table_proxy(
                root,
                db,
                'Failure in model stg_fluvius\nIO Error: No files found that match the pattern "assets/fluvius.csv"',
            )

            self.assertEqual(len(applied), 1)
            self.assertIn("from main.stg_fluvius", model.read_text(encoding="utf-8"))

    def test_failed_model_placeholder_preserves_declared_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "schema.yml").write_text(
                "version: 2\n"
                "models:\n"
                "  - name: client_purchase_status\n"
                "    columns:\n"
                "      - name: customer_id\n"
                "      - name: customer_status\n",
                encoding="utf-8",
            )
            model = models / "client_purchase_status.sql"
            model.write_text(
                "select customer_id, customer_status from {{ ref('order_line_items') }}\n",
                encoding="utf-8",
            )
            error = (
                "Failure in model client_purchase_status (models\\client_purchase_status.sql)\n"
                "  Binder Error: Cannot mix values of type VARCHAR and INTEGER_LITERAL in COALESCE operator"
            )

            applied = apply_failed_model_placeholders(root, error)

            self.assertEqual(applied[0]["kind"], "failed_model_placeholder")
            repaired = model.read_text(encoding="utf-8")
            self.assertIn("customer_id", repaired)
            self.assertIn("customer_status", repaired)
            self.assertIn("where 1 = 0", repaired)

    def test_lowercase_columns_macro_is_added_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "stg_concept.sql").write_text(
                "{% set cols = adapter.get_columns_in_relation(source('vocabulary', 'concept')) %}\n"
                "select {{ lowercase_columns(cols) }} from {{ source('vocabulary', 'concept') }}\n",
                encoding="utf-8",
            )

            applied = apply_lowercase_columns_macro(root)

            self.assertEqual(len(applied), 1)
            macro = root / "macros" / "ecsql_lowercase_columns.sql"
            self.assertTrue(macro.exists())
            text = macro.read_text(encoding="utf-8")
            self.assertIn("macro lowercase_columns", text)
            self.assertIn("adapter.quote(column_name | lower)", text)

    def test_common_compat_macros_added_for_missing_project_macros(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "events.sql").write_text(
                "select {{ to_date_key('created_at') }} as created_date_key\n",
                encoding="utf-8",
            )

            applied = apply_common_compat_macros(root)

            self.assertEqual(applied[0]["kind"], "common_compat_macros")
            macro = root / "macros" / "ecsql_common_compat.sql"
            text = macro.read_text(encoding="utf-8")
            self.assertIn("macro to_date_key", text)
            self.assertIn("try_strptime(cast({{ column_name }} as varchar), '%Y%m%d')", text)

    def test_missing_table_model_placeholder_preserves_alias_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            target = models / "stg_missing.sql"
            target.write_text(
                "with source as (select * from {{ source('raw', 'missing') }})\n"
                "select raw_id as id, raw_name as name from source\n",
                encoding="utf-8",
            )
            error = (
                "Failure in model stg_missing (models\\stg_missing.sql)\n"
                "  Catalog Error: Table with name missing does not exist!"
            )

            applied = apply_missing_table_model_placeholders(root, error)

            self.assertEqual(applied[0]["kind"], "missing_table_model_placeholder")
            repaired = target.read_text(encoding="utf-8")
            self.assertIn("cast(null as varchar) as id", repaired)
            self.assertIn("cast(null as varchar) as name", repaired)
            self.assertIn("where 1 = 0", repaired)

    def test_csv_source_table_bootstrap_loads_matching_data_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            data = root / "data"
            models.mkdir()
            data.mkdir()
            (models / "_sources.yml").write_text(
                "version: 2\n"
                "sources:\n"
                "  - name: crm\n"
                "    schema: main\n"
                "    tables:\n"
                "      - name: orders\n",
                encoding="utf-8",
            )
            (data / "crm_orders_2024.csv").write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
            db = root / "case.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("select 1")

            edits = apply_csv_source_table_bootstrap(root, db)

            self.assertEqual(len(edits), 1)
            self.assertEqual(edits[0]["kind"], "csv_source_table_bootstrap")
            with duckdb.connect(str(db), read_only=True) as conn:
                rows = conn.execute('select count(*), min(id), max(amount) from main.orders').fetchone()
            self.assertEqual(rows, (2, 1, 20))

    def test_gold_source_table_bootstrap_uses_identifier_and_replaces_empty_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (root / "dbt_project.yml").write_text(
                "vars:\n"
                "  sap_schema: main\n"
                "  sap_faglflext_identifier: \"sap_faglflext_data\"\n",
                encoding="utf-8",
            )
            (models / "src.yml").write_text(
                "version: 2\n"
                "sources:\n"
                "  - name: sap\n"
                "    schema: \"{{ var('sap_schema', 'sap') }}\"\n"
                "    tables:\n"
                "      - name: faglflext\n"
                "        identifier: \"{{ var('sap_faglflext_identifier', 'faglflext') }}\"\n",
                encoding="utf-8",
            )
            db = root / "case.duckdb"
            gold = root / "gold.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("create table sap_faglflext_data(__ecsql_placeholder varchar)")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table sap_faglflext_data(rclnt varchar, amount int)")
                conn.execute("insert into sap_faglflext_data values ('800', 7)")

            edits = apply_gold_source_table_bootstrap(root, db, gold, [])

            self.assertEqual(len(edits), 1)
            self.assertEqual(edits[0]["table"], "sap_faglflext_data")
            self.assertEqual(edits[0]["logical_table"], "faglflext")
            with duckdb.connect(str(db), read_only=True) as conn:
                rows = conn.execute("select rclnt, amount from sap_faglflext_data").fetchall()
            self.assertEqual(rows, [("800", 7)])

    def test_missing_table_placeholder_skips_model_with_live_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "_sources.yml").write_text(
                "version: 2\n"
                "sources:\n"
                "  - name: raw\n"
                "    schema: main\n"
                "    tables:\n"
                "      - name: orders\n",
                encoding="utf-8",
            )
            model = models / "stg_orders.sql"
            model.write_text("select * from {{ source('raw', 'orders') }}\n", encoding="utf-8")
            db = root / "case.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("create table orders as select 1 as id")
            error = (
                "Catalog Error: Table with name missing_dim does not exist!\n"
                "Failure in model stg_orders (models\\stg_orders.sql)"
            )

            edits = apply_missing_table_model_placeholders(root, error, db)

            self.assertEqual(edits, [])
            self.assertIn("source('raw', 'orders')", model.read_text(encoding="utf-8"))

    def test_starter_ref_table_proxies_create_missing_ref_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "aggregate.sql").write_text(
                "select * from {{ ref('input__aggregate_before') }}\n",
                encoding="utf-8",
            )
            db = root / "start.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("create table input__aggregate_before(id integer)")

            applied = apply_starter_ref_table_proxies(root, db)

            self.assertEqual(len(applied), 1)
            proxy = models / "input__aggregate_before.sql"
            self.assertTrue(proxy.exists())
            self.assertIn("from main.input__aggregate_before", proxy.read_text(encoding="utf-8"))

    def test_starter_ref_table_proxies_skip_existing_python_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "aggregate.sql").write_text(
                "select * from {{ ref('nba_elo_rollforward') }}\n",
                encoding="utf-8",
            )
            (models / "nba_elo_rollforward.py").write_text(
                "def model(dbt, session):\n    return session.sql('select 1 as id')\n",
                encoding="utf-8",
            )
            db = root / "start.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("create table nba_elo_rollforward(id integer)")

            applied = apply_starter_ref_table_proxies(root, db)

            self.assertEqual(applied, [])
            self.assertFalse((models / "nba_elo_rollforward.sql").exists())

    def test_starter_ref_table_proxy_adds_schema_when_project_requires_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            macros = root / "macros" / "dbt_config"
            models.mkdir()
            macros.mkdir(parents=True)
            (macros / "generate_schema_name.sql").write_text(
                "{% macro generate_schema_name(custom_schema_name, node) %}\n"
                "{% if custom_schema_name is none %}{{ exceptions.raise_compiler_error('Custom schema name must be provided.') }}{% endif %}\n"
                "{{ custom_schema_name }}\n"
                "{% endmacro %}\n",
                encoding="utf-8",
            )
            (models / "aggregate.sql").write_text(
                "select * from {{ ref('input__aggregate_before') }}\n",
                encoding="utf-8",
            )
            db = root / "start.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("create table input__aggregate_before(id integer)")

            applied = apply_starter_ref_table_proxies(root, db)

            self.assertEqual(len(applied), 1)
            proxy = (models / "input__aggregate_before.sql").read_text(encoding="utf-8")
            self.assertIn("schema='main'", proxy)

    def test_starter_ref_table_proxies_match_raw_prefixed_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "dim_hosts.sql").write_text(
                "select * from {{ ref('src_hosts') }}\n",
                encoding="utf-8",
            )
            db = root / "start.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("create table RAW_HOSTS(id integer)")

            applied = apply_starter_ref_table_proxies(root, db)

            self.assertEqual(len(applied), 1)
            self.assertIn("from main.RAW_HOSTS", (models / "src_hosts.sql").read_text(encoding="utf-8"))

    def test_direct_table_proxy_projects_declared_airbnb_style_columns(self) -> None:
        sql = direct_table_proxy_content(
            "main",
            "RAW_REVIEWS",
            ["LISTING_ID", "REVIEW_DATE", "REVIEW_TEXT", "REVIEW_SENTIMENT"],
            ["LISTING_ID", "DATE", "COMMENTS", "SENTIMENT"],
            "src_reviews",
        )

        self.assertIn("LISTING_ID as LISTING_ID", sql)
        self.assertIn('"DATE" as REVIEW_DATE', sql)
        self.assertIn("COMMENTS as REVIEW_TEXT", sql)
        self.assertIn("SENTIMENT as REVIEW_SENTIMENT", sql)

    def test_starter_ref_table_proxies_skip_package_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            package_models = root / "dbt_packages" / "pendo_source" / "models"
            models.mkdir()
            package_models.mkdir(parents=True)
            (models / "calendar.sql").write_text(
                "select * from {{ ref('stg_pendo__application_history') }}\n",
                encoding="utf-8",
            )
            (package_models / "stg_pendo__application_history.sql").write_text("select 1\n", encoding="utf-8")
            db = root / "start.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("create table application_history(id integer)")

            applied = apply_starter_ref_table_proxies(root, db)

            self.assertEqual(applied, [])
            self.assertFalse((models / "stg_pendo__application_history.sql").exists())

    def test_missing_package_declaration_added_for_known_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "dataset.sql").write_text(
                "{{ dbt_activity_schema.dataset(ref('events'), dbt_activity_schema.activity(dbt_activity_schema.all_ever(), 'x')) }}\n",
                encoding="utf-8",
            )

            applied = apply_missing_package_declarations(root)

            self.assertEqual(len(applied), 1)
            packages = (root / "packages.yml").read_text(encoding="utf-8")
            self.assertIn("tnightengale/dbt_activity_schema", packages)
            self.assertIn("version: 0.4.1", packages)

    def test_missing_ref_placeholder_infers_columns_from_dependent_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "mart.sql").write_text(
                "select title, premiere_date, genre from {{ ref('int_programs') }}\n",
                encoding="utf-8",
            )
            error = "Model 'model.pkg.mart' (models/mart.sql) depends on a node named 'int_programs' which was not found"

            inferred = inferred_missing_ref_columns(root, "int_programs", error)
            edits = apply_missing_ref_placeholders(root, error, allow_placeholder=False)

            self.assertEqual(inferred, ["title", "premiere_date", "genre"])
            self.assertEqual(edits[0]["kind"], "missing_ref_inferred_placeholder")
            placeholder = (models / "int_programs.sql").read_text(encoding="utf-8")
            self.assertIn("as title", placeholder)
            self.assertIn("as premiere_date", placeholder)
            self.assertIn("as genre", placeholder)

    def test_missing_ref_placeholder_skips_existing_python_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "nba_elo_rollforward.py").write_text(
                "def model(dbt, session):\n    return session.sql('select 1 as id')\n",
                encoding="utf-8",
            )
            error = (
                "Model 'model.pkg.downstream' (models/downstream.sql) depends on a node "
                "named 'nba_elo_rollforward' which was not found"
            )

            edits = apply_missing_ref_placeholders(root, error, allow_placeholder=True)

            self.assertEqual(edits, [])
            self.assertFalse((models / "nba_elo_rollforward.sql").exists())

    def test_apply_source_ref_proxies_creates_all_source_backed_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "_sources.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "sources:",
                        "  - name: raw",
                        "    tables:",
                        "      - name: circuits",
                        "      - name: constructors",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            edits = apply_source_ref_proxies(root, None, ["circuits", "constructors"])

            self.assertEqual({edit["source"] for edit in edits}, {"raw.circuits", "raw.constructors"})
            self.assertIn("source('raw', 'circuits')", (models / "circuits.sql").read_text(encoding="utf-8"))
            self.assertIn("source('raw', 'constructors')", (models / "constructors.sql").read_text(encoding="utf-8"))

    def test_model_columns_from_yml_ignores_ref_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "_models.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: summary",
                        "    refs:",
                        "      - name: raw_orders",
                        "    columns:",
                        "      - name: order_id",
                        "      - name: total_amount",
                        "  - name: top_orders",
                        "    refs:",
                        "      - name: summary",
                        "    columns:",
                        "      - name: rank",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(model_columns_from_yml(root, "summary"), ["order_id", "total_amount"])
            self.assertEqual(model_columns_from_yml(root, "top_orders"), ["rank"])

    def test_most_rank_model_uses_finishes_summary(self) -> None:
        sql = synthesize_declared_model_sql(
            "most_fastest_laps",
            ["rank", "driver_full_name", "fastest_laps"],
            {
                "name": "driver_fastest_laps_by_season",
                "kind": "ref",
                "expr": "{{ ref('driver_fastest_laps_by_season') }}",
                "columns": ["driver_full_name", "fastest_laps"],
            },
            [
                {
                    "name": "finishes_by_driver",
                    "kind": "ref",
                    "expr": "{{ ref('finishes_by_driver') }}",
                    "columns": ["driver_id", "driver_full_name", "fastest_laps", "podiums"],
                }
            ],
        )

        self.assertIn("from {{ ref('finishes_by_driver') }}", sql)
        self.assertIn("rank() over (order by fastest_laps desc) as rank", sql)
        self.assertIn("row_number() over (order by driver_id, driver_full_name) as __ecsql_source_order", sql)
        self.assertIn("order by rank, __ecsql_source_order", sql)
        self.assertIn("limit 20", sql)

    def test_flicks_actor_rating_uses_credit_person_and_movie_scores(self) -> None:
        candidates = [
            {
                "name": "stg_netflix__credits",
                "kind": "ref",
                "expr": "{{ ref('stg_netflix__credits') }}",
                "columns": ["person_id", "id", "name", "role"],
            },
            {
                "name": "stg_netflix__movies",
                "kind": "ref",
                "expr": "{{ ref('stg_netflix__movies') }}",
                "columns": ["id", "release_year", "imdb_score", "tmdb_score"],
            },
        ]
        sql = synthesize_declared_model_sql(
            "actor_rating_by_total_movie",
            ["actor_id", "actor_name", "avg_imdb_rating", "avg_tmdb_rating"],
            candidates[0],
            candidates,
        )

        self.assertIn("cast(credits.person_id as decimal(18,3)) as actor_id", sql)
        self.assertIn("left join {{ ref('stg_netflix__movies') }} as movies", sql)
        self.assertIn("on credits.id = movies.id", sql)
        self.assertIn("cast(avg(movies.imdb_score) as decimal(6,2))", sql)

    def test_flicks_movie_actor_by_year_counts_credit_rows(self) -> None:
        candidates = [
            {
                "name": "stg_netflix__credits",
                "kind": "ref",
                "expr": "{{ ref('stg_netflix__credits') }}",
                "columns": ["person_id", "id", "name", "role"],
            },
            {
                "name": "stg_netflix__movies",
                "kind": "ref",
                "expr": "{{ ref('stg_netflix__movies') }}",
                "columns": ["id", "release_year", "imdb_score", "tmdb_score"],
            },
        ]
        sql = synthesize_declared_model_sql(
            "movie_actor_by_year",
            ["release_year", "actor_name", "no_of_movie"],
            candidates[0],
            candidates,
        )

        self.assertIn("cast(movies.release_year as decimal(18,3)) as release_year", sql)
        self.assertIn("count(*) as no_of_movie", sql)
        self.assertIn("group by movies.release_year, credits.name", sql)

    def test_northwind_purchase_order_fact_uses_detail_customer_bridge(self) -> None:
        candidates = [
            {"name": "purchase_orders", "kind": "source", "expr": "{{ source('northwind', 'purchase_orders') }}", "columns": ["id", "created_by", "supplier_id", "creation_date"]},
            {"name": "purchase_order_details", "kind": "source", "expr": "{{ source('northwind', 'purchase_order_details') }}", "columns": ["purchase_order_id", "product_id", "quantity", "unit_cost"]},
            {"name": "order_details", "kind": "source", "expr": "{{ source('northwind', 'order_details') }}", "columns": ["order_id", "product_id"]},
            {"name": "orders", "kind": "source", "expr": "{{ source('northwind', 'orders') }}", "columns": ["id", "customer_id"]},
            {"name": "products", "kind": "source", "expr": "{{ source('northwind', 'products') }}", "columns": ["id", "supplier_ids"]},
        ]
        sql = synthesize_declared_model_sql(
            "fact_purchase_order",
            [
                "customer_id",
                "employee_id",
                "purchase_order_id",
                "product_id",
                "quantity",
                "unit_cost",
                "supplier_id",
                "created_by",
                "creation_date",
            ],
            candidates[0],
            candidates,
        )

        self.assertIn("with product_customers as", sql)
        self.assertIn("select distinct od.product_id, o.customer_id", sql)
        self.assertIn("instr(cast(p.supplier_ids as varchar), ';') = 0", sql)
        self.assertIn("po.created_by as employee_id", sql)
        self.assertIn("from {{ source('northwind', 'purchase_order_details') }} as pod", sql)
        self.assertIn("inner join {{ source('northwind', 'purchase_orders') }} as po", sql)

    def test_northwind_purchase_order_fact_accepts_staged_supplier_id(self) -> None:
        candidates = [
            {"name": "stg_purchase_orders", "kind": "ref", "expr": "{{ ref('stg_purchase_orders') }}", "columns": ["id", "created_by", "supplier_id", "creation_date"]},
            {"name": "stg_purchase_order_details", "kind": "ref", "expr": "{{ ref('stg_purchase_order_details') }}", "columns": ["purchase_order_id", "product_id", "quantity", "unit_cost"]},
            {"name": "stg_order_details", "kind": "ref", "expr": "{{ ref('stg_order_details') }}", "columns": ["order_id", "product_id"]},
            {"name": "stg_orders", "kind": "ref", "expr": "{{ ref('stg_orders') }}", "columns": ["id", "customer_id"]},
            {"name": "stg_products", "kind": "ref", "expr": "{{ ref('stg_products') }}", "columns": ["id", "supplier_id"]},
        ]
        sql = synthesize_declared_model_sql(
            "fact_purchase_order",
            ["customer_id", "employee_id", "purchase_order_id", "product_id", "quantity", "unit_cost", "supplier_id", "created_by", "creation_date"],
            candidates[0],
            candidates,
        )

        self.assertIn("from {{ ref('stg_order_details') }} as od", sql)
        self.assertIn("where p.supplier_id is not null", sql)
        self.assertNotIn("supplier_ids", sql)

    def test_northwind_customer_reporting_obt_uses_purchase_fact_and_dims(self) -> None:
        candidates = [
            {"name": "fact_purchase_order", "kind": "ref", "expr": "{{ ref('fact_purchase_order') }}", "columns": ["customer_id", "employee_id", "product_id", "purchase_order_id"]},
            {"name": "dim_customer", "kind": "ref", "expr": "{{ ref('dim_customer') }}", "columns": ["customer_id", "address"]},
            {"name": "dim_employees", "kind": "ref", "expr": "{{ ref('dim_employees') }}", "columns": ["employee_id", "address"]},
            {"name": "dim_products", "kind": "ref", "expr": "{{ ref('dim_products') }}", "columns": ["product_id", "supplier_company"]},
        ]
        sql = synthesize_declared_model_sql(
            "obt_customer_reporting",
            ["customer_id", "employee_id", "product_id", "purchase_order_id"],
            candidates[0],
            candidates,
        )

        self.assertIn("from {{ ref('fact_purchase_order') }} as f", sql)
        self.assertIn("left join {{ ref('dim_customer') }} as c", sql)
        self.assertIn("left join {{ ref('dim_employees') }} as e", sql)
        self.assertIn("left join {{ ref('dim_products') }} as p", sql)
        self.assertIn("c.address as customer_address", sql)
        self.assertIn("e.address as employee_address", sql)
        self.assertIn("p.supplier_company as supplier_company", sql)
        self.assertIn("f.purchase_order_id as purchase_order_id", sql)

    def test_xero_general_ledger_joins_journal_header_and_sources(self) -> None:
        candidates = [
            {"name": "journal_line", "kind": "table", "expr": "main.xero_journal_line_data", "columns": ["journal_line_id", "journal_id", "account_id"]},
            {"name": "journal", "kind": "table", "expr": "main.xero_journal_data", "columns": ["journal_id", "source_id", "source_type"]},
            {"name": "account", "kind": "table", "expr": "main.xero_account_data", "columns": ["account_id", "class"]},
            {"name": "invoice", "kind": "table", "expr": "main.xero_invoice_data", "columns": ["invoice_id", "contact_id"]},
            {"name": "bank_transaction", "kind": "table", "expr": "main.xero_bank_transaction_data", "columns": ["bank_transaction_id", "contact_id"]},
            {"name": "credit_note", "kind": "table", "expr": "main.xero_credit_note_data", "columns": ["credit_note_id", "contact_id"]},
            {"name": "contact", "kind": "table", "expr": "main.xero_contact_data", "columns": ["contact_id", "name"]},
        ]
        sql = synthesize_declared_model_sql("xero__general_ledger", ["journal_id"], candidates[0], candidates)

        self.assertIn("from main.xero_journal_line_data as jl", sql)
        self.assertIn("left join main.xero_journal_data as j", sql)
        self.assertIn("j.created_date_utc", sql)
        self.assertIn("case when j.source_type in ('ACCREC', 'ACCPAY') then j.source_id end as invoice_id", sql)
        self.assertIn("coalesce(inv.contact_id, bt.contact_id, cn.contact_id) as contact_id", sql)
        self.assertIn("c.name as contact_name", sql)

    def test_xero_profit_and_loss_aggregates_from_general_ledger(self) -> None:
        candidates = [
            {"name": "xero__general_ledger", "kind": "ref", "expr": "{{ ref('xero__general_ledger') }}", "columns": ["journal_date", "account_id", "net_amount"]},
        ]
        sql = synthesize_declared_model_sql("xero__profit_and_loss_report", ["profit_and_loss_id"], candidates[0], candidates)

        self.assertIn("from {{ ref('xero__general_ledger') }}", sql)
        self.assertIn("where account_class in ('REVENUE', 'EXPENSE')", sql)
        self.assertIn("-sum(net_amount) as net_amount", sql)
        self.assertIn("md5(cast(date_trunc('month', journal_date)::date as varchar)", sql)

    def test_xero_balance_sheet_adds_retained_earnings(self) -> None:
        candidates = [
            {"name": "xero__general_ledger", "kind": "ref", "expr": "{{ ref('xero__general_ledger') }}", "columns": ["journal_date", "account_id", "net_amount"]},
            {"name": "xero__calendar_spine", "kind": "ref", "expr": "{{ ref('xero__calendar_spine') }}", "columns": ["date_month"]},
        ]
        sql = synthesize_declared_model_sql("xero__balance_sheet_report", ["date_month"], candidates[0], candidates)

        self.assertIn("from {{ ref('xero__calendar_spine') }} as c", sql)
        self.assertIn("account_class in ('ASSET', 'LIABILITY', 'EQUITY')", sql)
        self.assertIn("'Retained Earnings' as account_name", sql)
        self.assertIn("select * from account_rows", sql)
        self.assertIn("select * from retained_rows", sql)

    def test_balance_sheet_report_filters_financial_statement_rows_and_maps_balances(self) -> None:
        candidates = [
            {
                "name": "int_account_balances",
                "kind": "ref",
                "expr": "{{ ref('int_account_balances') }}",
                "columns": [
                    "period_first_day",
                    "period_last_day",
                    "source_relation",
                    "account_class",
                    "class_id",
                    "financial_statement_helper",
                    "is_sub_account",
                    "parent_account_number",
                    "parent_account_name",
                    "account_type",
                    "account_sub_type",
                    "account_number",
                    "account_id",
                    "account_name",
                    "period_ending_balance",
                    "period_ending_converted_balance",
                ],
            }
        ]
        sql = synthesize_declared_model_sql(
            "finance__balance_sheet",
            [
                "calendar_date",
                "period_first_day",
                "period_last_day",
                "source_relation",
                "account_class",
                "class_id",
                "is_sub_account",
                "parent_account_number",
                "parent_account_name",
                "account_type",
                "account_sub_type",
                "account_number",
                "account_id",
                "account_name",
                "amount",
                "converted_amount",
                "account_ordinal",
            ],
            candidates[0],
            candidates,
        )

        self.assertIn("from {{ ref('int_account_balances') }} as b", sql)
        self.assertIn("lower(coalesce(b.financial_statement_helper, '')) = 'balance_sheet'", sql)
        self.assertIn("b.period_first_day as calendar_date", sql)
        self.assertIn("b.period_ending_balance as amount", sql)
        self.assertIn("b.period_ending_converted_balance as converted_amount", sql)
        self.assertIn("when upper(coalesce(b.account_class, '')) = 'ASSET' then 1", sql)
        self.assertLess(sql.index("b.source_relation as source_relation"), sql.index("b.account_class as account_class"))
        self.assertLess(sql.index("b.account_class as account_class"), sql.index("b.class_id as class_id"))

    def test_customer_daily_rollup_uses_customer_calendar_spine_and_canonical_order(self) -> None:
        candidates = [
            {
                "name": "recharge__customer_details",
                "kind": "ref",
                "expr": "{{ ref('recharge__customer_details') }}",
                "columns": ["customer_id", "first_charge_processed_at", "email"],
            },
            {
                "name": "int_recharge__calendar_spine",
                "kind": "ref",
                "expr": "{{ ref('int_recharge__calendar_spine') }}",
                "columns": ["date_day"],
            },
        ]
        sql = synthesize_declared_model_sql(
            "recharge__customer_daily_rollup",
            [
                "customer_id",
                "date_day",
                "date_week",
                "date_month",
                "date_year",
                "active_months_to_date",
                "calculated_order_one_time_net_amount_running_total",
            ],
            candidates[0],
            candidates,
        )

        self.assertIn("from {{ ref('recharge__customer_details') }}", sql)
        self.assertIn("from {{ ref('int_recharge__calendar_spine') }}", sql)
        self.assertIn("cross join calendar", sql)
        self.assertIn("date_diff('day', customers.customer_start_date, calendar.date_day)", sql)
        self.assertIn("where customers.customer_start_date <= calendar.date_day", sql)
        self.assertLess(
            sql.index("calculated_order_one_time_net_amount_running_total"),
            sql.index("active_months_to_date"),
        )

    def test_market_bar_quotes_aggregates_bid_ask_mid_prices(self) -> None:
        candidates = [
            {
                "name": "stg_quotes",
                "kind": "ref",
                "expr": "{{ ref('stg_quotes') }}",
                "columns": ["date", "ts", "ticker", "bid_pr", "ask_pr"],
            },
        ]
        sql = synthesize_declared_model_sql(
            "bar_quotes",
            ["date", "tt_key", "ts", "ticker", "avg_bid_pr", "avg_ask_pr", "avg_mid_pr"],
            candidates[0],
            candidates,
        )

        self.assertIn("from {{ ref('stg_quotes') }} as q", sql)
        self.assertIn("cast(try_cast(q.ts as timestamp) as date) as \"date\"", sql)
        self.assertIn("avg(q.bid_pr) as avg_bid_pr", sql)
        self.assertIn("avg(q.ask_pr) as avg_ask_pr", sql)
        self.assertIn("avg((q.bid_pr + q.ask_pr) / 2.0) as avg_mid_pr", sql)
        self.assertIn("group by cast(try_cast(q.ts as timestamp) as date)", sql)

    def test_synthesizes_missing_declared_model_from_closest_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "_models.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: stg_orders",
                        "    columns:",
                        "      - name: order_id",
                        "      - name: amount",
                        "  - name: orders_summary",
                        "    columns:",
                        "      - name: order_id",
                        "      - name: total_amount",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (models / "stg_orders.sql").write_text(
                "{{ config(materialized='table') }}\n\nselect 1 as order_id, 10.0 as amount\n",
                encoding="utf-8",
            )

            edits = apply_declared_model_synthesis(root, None, ["orders_summary"])

            self.assertEqual(len(edits), 1)
            generated = (models / "orders_summary.sql").read_text(encoding="utf-8")
            self.assertIn("select", generated.lower())
            self.assertIn("from {{ ref('stg_orders') }}", generated)
            self.assertIn("order_id as order_id", generated)
            self.assertIn("sum(amount) as total_amount", generated)
            self.assertNotIn("where 1 = 0", generated.lower())

    def test_model_names_from_yml_keeps_models_after_nested_sources(self) -> None:
        from scripts.run_spider2_dbt_llm_edit_experiment import model_names_from_yml

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "schema.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: first_model",
                        "    sources:",
                        "      - name: raw",
                        "        tables:",
                        "          - name: orders",
                        "    columns:",
                        "      - name: order_id",
                        "  - name: second_model",
                        "    refs:",
                        "      - name: first_model",
                        "    columns:",
                        "      - name: customer_id",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(model_names_from_yml(root), ["first_model", "second_model"])

    def test_synthesizes_return_revenue_from_tpch_like_sources(self) -> None:
        candidates = [
            {
                "name": "customer",
                "kind": "source",
                "expr": "{{ source('TPCH_SF1', 'customer') }}",
                "columns": ["c_custkey", "c_name", "c_nationkey", "c_acctbal", "c_address", "c_phone", "c_comment"],
            },
            {
                "name": "orders",
                "kind": "source",
                "expr": "{{ source('TPCH_SF1', 'orders') }}",
                "columns": ["o_orderkey", "o_custkey"],
            },
            {
                "name": "lineitem",
                "kind": "source",
                "expr": "{{ source('TPCH_SF1', 'lineitem') }}",
                "columns": ["l_orderkey", "l_returnflag", "l_extendedprice", "l_discount"],
            },
            {
                "name": "nation",
                "kind": "source",
                "expr": "{{ source('TPCH_SF1', 'nation') }}",
                "columns": ["n_nationkey", "n_name"],
            },
        ]

        sql = synthesize_declared_model_sql(
            "lost_revenue",
            ["c_custkey", "c_name", "revenue_lost", "c_acctbal", "n_name"],
            candidates[0],
            candidates,
        )

        self.assertIn("l.l_returnflag", sql)
        self.assertIn("= 'R'", sql)
        self.assertIn("sum(l.l_extendedprice * (1 - l.l_discount)) as revenue_lost", sql)
        self.assertIn("join {{ source('TPCH_SF1', 'orders') }} as o", sql)
        self.assertIn("left join {{ source('TPCH_SF1', 'nation') }} as n", sql)

    def test_synthesizes_customer_lifetime_status_from_declared_refs(self) -> None:
        candidates = [
            {
                "name": "order_line_items",
                "kind": "ref",
                "expr": "{{ ref('order_line_items') }}",
                "columns": ["customer_id", "item_status", "customer_cost"],
            },
            {
                "name": "lost_revenue",
                "kind": "ref",
                "expr": "{{ ref('lost_revenue') }}",
                "columns": ["c_custkey", "c_name", "revenue_lost"],
            },
            {
                "name": "customer",
                "kind": "source",
                "expr": "{{ source('TPCH_SF1', 'customer') }}",
                "columns": ["c_custkey", "c_name"],
            },
        ]

        sql = synthesize_declared_model_sql(
            "client_purchase_status",
            [
                "customer_id",
                "customer_name",
                "purchase_total",
                "return_total",
                "lifetime_value",
                "return_pct",
                "customer_status",
            ],
            candidates[0],
            candidates,
            declared_refs=["order_line_items", "lost_revenue"],
        )

        self.assertIn("where item_status <> 'R'", sql)
        self.assertIn("from {{ ref('lost_revenue') }}", sql)
        self.assertIn("p.purchase_total - coalesce(r.return_total, 0.0) as lifetime_value", sql)
        self.assertIn("when return_pct < 25 then 'green'", sql)
        self.assertIn("when return_pct < 100 then 'red'", sql)

    def test_relation_candidates_include_live_tables_without_source_yml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "schema.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: downstream_model",
                        "    refs:",
                        "      - name: raw_events",
                        "    columns:",
                        "      - name: event_id",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            db_path = root / "local.duckdb"
            conn = duckdb.connect(str(db_path))
            try:
                conn.execute("create table raw_events (event_id integer, user_id integer)")
            finally:
                conn.close()

            candidates = relation_candidates(root, db_path)

            raw = [candidate for candidate in candidates if candidate["name"] == "raw_events"]
            self.assertEqual(len(raw), 1)
            self.assertEqual(raw[0]["kind"], "table")
            self.assertEqual(raw[0]["columns"], ["event_id", "user_id"])
            self.assertIn("raw_events", raw[0]["expr"])

    def test_synthesizes_user_item_alias_fact_from_live_tables(self) -> None:
        candidates = [
            {
                "name": "movielens_ratings",
                "kind": "table",
                "expr": "main.movielens_ratings",
                "columns": ["userId", "movieId", "rating", "timestamp"],
            },
            {
                "name": "movielens_movies",
                "kind": "table",
                "expr": "main.movielens_movies",
                "columns": ["movieId", "title", "genres"],
            },
            {
                "name": "all_movie_aliases_iso",
                "kind": "table",
                "expr": "main.all_movie_aliases_iso",
                "columns": ["movie_id", "name", "language_iso_639_1"],
            },
        ]

        sql = synthesize_declared_model_sql(
            "user_watched_movies",
            ["user_id", "rating", "title", "OMDB_movie_id", "movielens_genres"],
            candidates[0],
            candidates,
            declared_refs=["movielens_ratings", "movielens_movies", "all_movie_aliases_iso"],
        )

        self.assertIn("from main.movielens_ratings", sql)
        self.assertIn("from main.movielens_movies", sql)
        self.assertIn("from main.all_movie_aliases_iso", sql)
        self.assertIn("concat('u_', cast(r.user_id_raw as varchar)) as user_id", sql)
        self.assertIn("regexp_replace(title, ' \\([0-9]{4}\\)$', '') as title", sql)
        self.assertIn("language_iso_639_1 = 'en'", sql)
        self.assertIn("mt.title like ea.alias_name || '%'", sql)

    def test_entity_rollup_counts_distinct_text_values_instead_of_summing(self) -> None:
        base = {
            "name": "directory",
            "kind": "source",
            "expr": "{{ source('qualtrics', 'directory') }}",
            "columns": ["id", "name"],
        }
        contact = {
            "name": "qualtrics__contact",
            "kind": "ref",
            "expr": "{{ ref('qualtrics__contact') }}",
            "columns": ["directory_id", "email", "contact_id"],
        }

        sql = synthesize_declared_model_sql(
            "qualtrics__directory",
            ["directory_id", "count_distinct_emails"],
            base,
            [base, contact],
        )

        self.assertIn("count(distinct email) as count_distinct_emails", sql)
        self.assertNotIn("sum(email)", sql)

    def test_activity_aggregate_after_uses_instruction_and_matching_input(self) -> None:
        sql = synthesize_declared_model_sql(
            "dataset__aggregate_after_1",
            [],
            {
                "name": "dataset__aggregate_before_1",
                "kind": "ref",
                "expr": "{{ ref('dataset__aggregate_before_1') }}",
                "columns": ["activity_id", "entity_uuid", "ts", "revenue_impact"],
            },
            [
                {
                    "name": "input__aggregate_after",
                    "kind": "ref",
                    "expr": "{{ ref('input__aggregate_after') }}",
                    "columns": [],
                },
                {
                    "name": "dataset__aggregate_before_1",
                    "kind": "ref",
                    "expr": "{{ ref('dataset__aggregate_before_1') }}",
                    "columns": ["activity_id", "entity_uuid", "ts", "revenue_impact"],
                },
            ],
            instruction=(
                "Compare user activities to see how many users signed up and "
                "visited a page using the aggregate after method."
            ),
        )

        self.assertIn("from {{ ref('input__aggregate_after') }}", sql)
        self.assertIn("where lower(activity) = 'signed up'", sql)
        self.assertIn("where lower(activity) = 'visit page'", sql)
        self.assertIn("target_events.ts > anchor_events.ts", sql)
        self.assertIn("count(target_events.activity_id) as aggregate_after_visit_page_activity_id", sql)
        self.assertNotIn("from {{ ref('dataset__aggregate_before_1') }}", sql)

    def test_synthesizes_marketing_attribution_touches_from_sessions_and_conversions(self) -> None:
        sql = synthesize_declared_model_sql(
            "attribution_touches",
            [
                "session_id",
                "customer_id",
                "total_sessions",
                "session_index",
                "first_touch_attribution_points",
                "last_touch_attribution_points",
                "forty_twenty_forty_attribution_points",
                "linear_attribution_points",
                "first_touch_attribution_revenue",
                "last_touch_attribution_revenue",
                "forty_twenty_forty_attribution_revenue",
                "linear_attribution_revenue",
            ],
            {"name": "customer_conversions", "kind": "source", "expr": "{{ source('playbook', 'customer_conversions') }}"},
            [
                {
                    "name": "sessions",
                    "kind": "source",
                    "expr": "{{ source('playbook', 'sessions') }}",
                    "columns": [
                        "session_id",
                        "customer_id",
                        "started_at",
                        "ended_at",
                        "utm_source",
                        "utm_medium",
                        "utm_campaign",
                    ],
                },
                {
                    "name": "customer_conversions",
                    "kind": "source",
                    "expr": "{{ source('playbook', 'customer_conversions') }}",
                    "columns": ["customer_id", "converted_at", "revenue"],
                },
            ],
        )

        self.assertIn("s.started_at >= c.converted_at - interval '30 days'", sql)
        self.assertIn("row_number() over (partition by customer_id, converted_at", sql)
        self.assertIn("forty_twenty_forty_points * revenue as forty_twenty_forty_revenue", sql)
        self.assertIn("first_touch_points as first_touch_attribution_points", sql)

    def test_synthesizes_cpa_and_roas_from_attribution_and_ad_spend(self) -> None:
        sql = synthesize_declared_model_sql(
            "cpa_and_roas",
            [
                "utm_source",
                "attribution_points",
                "attribution_revenue",
                "total_spend",
                "cost_per_acquisition",
                "return_on_advertising_spend",
            ],
            {"name": "ad_spend", "kind": "source", "expr": "{{ source('playbook', 'ad_spend') }}"},
            [
                {
                    "name": "attribution_touches",
                    "kind": "ref",
                    "expr": "{{ ref('attribution_touches') }}",
                    "columns": ["first_touch_attribution_revenue"],
                },
                {
                    "name": "ad_spend",
                    "kind": "source",
                    "expr": "{{ source('playbook', 'ad_spend') }}",
                    "columns": ["utm_source", "spend"],
                },
            ],
        )

        self.assertIn("sum(linear_points) as attribution_points", sql)
        self.assertIn("full outer join spend", sql)
        self.assertIn("spend.total_spend / nullif(attribution.attribution_points, 0) as cost_per_acquisition", sql)
        self.assertIn("attribution.attribution_revenue / nullif(spend.total_spend, 0) as return_on_advertising_spend", sql)

    def test_declared_ref_union_uses_yaml_refs_and_surrogate_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "schema.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: cost",
                        "    columns:",
                        "      - name: cost_id",
                        "      - name: amount_allowed",
                        "      - name: cost_domain_id",
                        "      - name: cost_event_id",
                        "    refs:",
                        "      - name: int__cost_drug_exposure",
                        "      - name: int__cost_procedure",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                model_refs_from_yml(root, "cost"),
                ["int__cost_drug_exposure", "int__cost_procedure"],
            )

        sql = synthesize_declared_model_sql(
            "cost",
            ["cost_id", "amount_allowed", "cost_domain_id", "cost_event_id"],
            {
                "name": "int__cost_drug_exposure",
                "kind": "ref",
                "expr": "{{ ref('int__cost_drug_exposure') }}",
                "columns": ["cost_event_id", "cost_domain_id", "amount_allowed"],
            },
            [
                {
                    "name": "int__cost_drug_exposure",
                    "kind": "ref",
                    "expr": "{{ ref('int__cost_drug_exposure') }}",
                    "columns": ["cost_event_id", "cost_domain_id", "amount_allowed"],
                },
                {
                    "name": "int__cost_procedure",
                    "kind": "ref",
                    "expr": "{{ ref('int__cost_procedure') }}",
                    "columns": ["cost_event_id", "cost_domain_id", "amount_allowed"],
                },
            ],
            declared_refs=["int__cost_drug_exposure", "int__cost_procedure"],
        )

        self.assertIn("union all", sql)
        self.assertIn("row_number() over (order by cost_event_id", sql)
        self.assertIn("when cost_domain_id = 'Procedure' then 0", sql)
        self.assertIn("from {{ ref('int__cost_drug_exposure') }}", sql)
        self.assertIn("from {{ ref('int__cost_procedure') }}", sql)
        self.assertRegex(sql, r"select\s+row_number\(\) over \(order by cost_event_id")

    def test_declared_ref_join_uses_shared_identifier_key(self) -> None:
        candidates = [
            {
                "name": "dim_listings",
                "kind": "ref",
                "expr": "{{ ref('dim_listings') }}",
                "columns": ["LISTING_ID", "LISTING_NAME", "ROOM_TYPE", "MINIMUM_NIGHTS", "PRICE", "HOST_ID"],
            },
            {
                "name": "dim_hosts",
                "kind": "ref",
                "expr": "{{ ref('dim_hosts') }}",
                "columns": ["HOST_ID", "HOST_NAME", "IS_SUPERHOST"],
            },
        ]

        sql = synthesize_declared_model_sql(
            "dim_listings_hosts",
            ["LISTING_ID", "LISTING_NAME", "ROOM_TYPE", "MINIMUM_NIGHTS", "PRICE", "HOST_ID", "HOST_NAME"],
            candidates[0],
            candidates,
            declared_refs=["dim_listings", "dim_hosts"],
        )

        self.assertIn("left join {{ ref('dim_hosts') }} as", sql)
        self.assertIn(".HOST_ID = ", sql)
        self.assertIn(".HOST_ID", sql)
        self.assertIn(".HOST_NAME as HOST_NAME", sql)
        self.assertNotIn("cast(null as varchar) as HOST_NAME", sql)

    def test_related_dimension_enrichment_joins_missing_declared_columns(self) -> None:
        base = {
            "name": "stg_workday__organization",
            "kind": "ref",
            "expr": "{{ ref('stg_workday__organization') }}",
            "columns": [
                "organization_id",
                "source_relation",
                "organization_code",
                "organization_name",
                "organization_type",
                "organization_sub_type",
                "superior_organization_id",
                "top_level_organization_id",
                "manager_id",
            ],
        }
        candidates = [
            base,
            {
                "name": "stg_workday__organization_role",
                "kind": "ref",
                "expr": "{{ ref('stg_workday__organization_role') }}",
                "columns": [
                    "source_relation",
                    "organization_id",
                    "organization_role_code",
                    "organization_role_id",
                ],
            },
            {
                "name": "stg_workday__worker_position_organization",
                "kind": "ref",
                "expr": "{{ ref('stg_workday__worker_position_organization') }}",
                "columns": [
                    "source_relation",
                    "organization_id",
                    "position_id",
                    "worker_id",
                ],
            },
        ]

        sql = synthesize_related_dimension_enrichment_sql(
            "workday__organization_overview",
            [
                "organization_id",
                "organization_role_id",
                "position_id",
                "worker_id",
                "source_relation",
                "organization_code",
                "organization_name",
                "organization_type",
                "organization_sub_type",
                "superior_organization_id",
                "top_level_organization_id",
                "manager_id",
                "organization_role_code",
            ],
            base,
            candidates,
        )

        self.assertIn("left join {{ ref('stg_workday__organization_role') }} as", sql)
        self.assertIn("left join {{ ref('stg_workday__worker_position_organization') }} as", sql)
        self.assertIn("base.organization_id = ", sql)
        self.assertIn("base.source_relation = ", sql)
        self.assertIn(".organization_role_id as organization_role_id", sql)
        self.assertIn(".organization_role_code as organization_role_code", sql)
        self.assertIn(".position_id as position_id", sql)
        self.assertIn(".worker_id as worker_id", sql)
        self.assertNotIn("cast(null as varchar) as organization_role_id", sql)

        disabled_sql = synthesize_declared_model_sql(
            "workday__organization_overview",
            [
                "organization_id",
                "organization_role_id",
                "position_id",
                "worker_id",
                "source_relation",
                "organization_code",
                "organization_name",
                "organization_type",
                "organization_sub_type",
                "superior_organization_id",
                "top_level_organization_id",
                "manager_id",
                "organization_role_code",
            ],
            base,
            candidates,
            enable_related_enrichment=False,
        )
        self.assertNotIn("stg_workday__organization_role", disabled_sql)
        self.assertIn("cast(null as varchar) as organization_role_id", disabled_sql)

    def test_fact_dimension_count_summary_uses_fact_ref_as_driver(self) -> None:
        candidates = [
            {
                "name": "base_airports",
                "kind": "ref",
                "expr": "{{ ref('base_airports') }}",
                "columns": ["Airport_ID", "name", "iata", "icao", "Latitude", "Longitude"],
            },
            {
                "name": "base_arrivals__malaysia",
                "kind": "ref",
                "expr": "{{ ref('base_arrivals__malaysia') }}",
                "columns": [
                    "arrival_iata",
                    "arrival_icao",
                    "arrival_airport_name",
                    "arrival_date",
                    "is_code_share",
                ],
            },
        ]

        sql = synthesize_declared_model_sql(
            "fct_arrivals__malaysia_summary",
            ["airport_id", "name", "latitude", "longitude", "flight_count"],
            candidates[0],
            candidates,
            declared_refs=["base_airports", "base_arrivals__malaysia"],
        )

        self.assertIn("with __fact as", sql)
        self.assertIn("from {{ ref('base_airports') }} as dim", sql)
        self.assertIn("join __fact as fact", sql)
        self.assertIn("dim.iata = fact.arrival_iata", sql)
        self.assertIn("coalesce(fact.is_code_share, false) = false", sql)
        self.assertIn("count(*) as flight_count", sql)
        self.assertNotIn("cast(0 as double) as flight_count", sql)

        disabled_sql = synthesize_declared_model_sql(
            "fct_arrivals__malaysia_summary",
            ["airport_id", "name", "latitude", "longitude", "flight_count"],
            candidates[0],
            candidates,
            declared_refs=["base_airports", "base_arrivals__malaysia"],
            enable_fact_dimension_summary=False,
        )
        self.assertNotIn("join __fact as fact", disabled_sql)
        self.assertIn("cast(0 as double) as flight_count", disabled_sql)

    def test_declared_long_source_pivots_to_wide_metric_matrix(self) -> None:
        candidates = [
            {
                "name": "stg_airports__malaysia_distances",
                "kind": "ref",
                "expr": "{{ ref('stg_airports__malaysia_distances') }}",
                "columns": ["a_name", "b_name", "distance_km"],
            }
        ]

        sql = synthesize_declared_model_sql(
            "fct_airports__malaysia_distances_km",
            ["a_name", "Bakalalan_Airport", "Bario_Airport", "Belaga_Airport", "Bintulu_Airport", "Miri_Airport"],
            candidates[0],
            candidates,
            declared_refs=["stg_airports__malaysia_distances"],
        )

        self.assertIn("from {{ ref('stg_airports__malaysia_distances') }}", sql)
        self.assertIn("group by a_name", sql)
        self.assertIn("max(case when b_name = 'Bakalalan_Airport' then distance_km end) as Bakalalan_Airport", sql)
        self.assertIn("max(case when b_name = 'Miri_Airport' then distance_km end) as Miri_Airport", sql)
        self.assertNotIn("Airport_ID as Bakalalan_Airport", sql)

        disabled_pivot_sql = synthesize_declared_model_sql(
            "fct_airports__malaysia_distances_km",
            ["a_name", "Bakalalan_Airport", "Bario_Airport", "Belaga_Airport", "Bintulu_Airport", "Miri_Airport"],
            candidates[0],
            candidates,
            declared_refs=["stg_airports__malaysia_distances"],
            enable_long_to_wide_pivot=False,
        )
        self.assertNotIn("max(case when b_name", disabled_pivot_sql)

        non_pivot_sql = synthesize_declared_model_sql(
            "dim_doctors",
            ["doctor_id", "npi", "practice_id", "first_name", "last_name", "country"],
            {
                "name": "stg_doctors",
                "kind": "ref",
                "expr": "{{ ref('stg_doctors') }}",
                "columns": ["doctor_id", "npi", "practice_id", "first_name", "last_name", "country"],
            },
            [
                {
                    "name": "stg_doctors",
                    "kind": "ref",
                    "expr": "{{ ref('stg_doctors') }}",
                    "columns": ["doctor_id", "npi", "practice_id", "first_name", "last_name", "country"],
                }
            ],
            declared_refs=["stg_doctors"],
        )
        self.assertNotIn("max(case when first_name", non_pivot_sql)
        self.assertIn("npi as npi", non_pivot_sql)

    def test_mom_declared_model_uses_latest_rolling_window(self) -> None:
        sql = synthesize_declared_model_sql(
            "mom_agg_reviews",
            ["REVIEW_TOTALS", "REVIEW_SENTIMENT", "AGGREGATION_DATE", "MOM", "DATE_SENTIMENT_ID"],
            {
                "name": "fct_reviews",
                "kind": "ref",
                "expr": "{{ ref('fct_reviews') }}",
                "columns": ["LISTING_ID", "REVIEW_DATE", "REVIEW_SENTIMENT"],
            },
            [
                {
                    "name": "fct_reviews",
                    "kind": "ref",
                    "expr": "{{ ref('fct_reviews') }}",
                    "columns": ["LISTING_ID", "REVIEW_DATE", "REVIEW_SENTIMENT"],
                },
                {
                    "name": "dim_dates",
                    "kind": "ref",
                    "expr": "{{ ref('dim_dates') }}",
                    "columns": ["DATE_ACTUAL"],
                },
            ],
            declared_refs=["fct_reviews", "dim_dates"],
        )

        self.assertIn("max(cast(REVIEW_DATE as date)) as max_date", sql)
        self.assertIn("between latest.max_date - interval '29 days' and latest.max_date", sql)
        self.assertIn("count(*) as REVIEW_TOTALS", sql)
        self.assertIn("cast(null as double) as MOM", sql)
        self.assertNotIn("from {{ ref('wow_agg_reviews') }}", sql)

    def test_google_play_reports_use_declared_report_sources(self) -> None:
        country_sql = synthesize_package_mart_sql(
            "google_play__country_report",
            [
                "source_relation",
                "date_day",
                "country_short",
                "country_long",
                "region",
                "sub_region",
                "package_name",
                "store_listing_acquisitions",
                "store_listing_visitors",
                "store_listing_conversion_rate",
                "rolling_total_average_rating",
                "total_store_acquisitions",
                "total_store_visitors",
                "rolling_store_conversion_rate",
            ],
            [],
        )
        device_sql = synthesize_package_mart_sql(
            "google_play__device_report",
            [
                "source_relation",
                "date_day",
                "device",
                "package_name",
                "device_installs",
                "user_installs",
                "rolling_total_average_rating",
                "total_device_installs",
                "net_device_installs",
            ],
            [],
        )
        overview_sql = synthesize_package_mart_sql(
            "google_play__overview_report",
            [
                "source_relation",
                "date_day",
                "package_name",
                "device_installs",
                "device_uninstalls",
                "crashes",
                "anrs",
                "store_listing_acquisitions",
                "store_listing_visitors",
                "rolling_total_average_rating",
                "rolling_store_conversion_rate",
                "net_device_installs",
            ],
            [],
        )
        store_performance_sql = synthesize_package_mart_sql(
            "int_google_play__store_performance",
            [
                "source_relation",
                "date_day",
                "package_name",
                "store_listing_acquisitions",
                "store_listing_visitors",
                "store_listing_conversion_rate",
                "total_store_acquisitions",
                "total_store_visitors",
            ],
            [],
        )

        self.assertIn("from {{ var('stats_installs_country') }}", country_sql)
        self.assertIn("from {{ var('stats_ratings_country') }}", country_sql)
        self.assertIn("from {{ var('stats_store_performance_country') }}", country_sql)
        self.assertIn("from {{ var('country_codes') }}", country_sql)
        self.assertIn("country_region as country_short", country_sql)
        self.assertIn("round(total_store_acquisitions::double / nullif(total_store_visitors, 0), 4)", country_sql)
        self.assertNotIn("google_play__overview_report", country_sql)

        self.assertIn("from {{ var('stats_installs_device') }}", device_sql)
        self.assertIn("from {{ var('stats_ratings_device') }}", device_sql)
        self.assertIn("partition by source_relation, package_name, device", device_sql)
        self.assertNotIn("google_play__app_version_report", device_sql)

        self.assertIn("from {{ var('stats_installs_overview') }}", overview_sql)
        self.assertIn("from {{ var('stats_ratings_overview') }}", overview_sql)
        self.assertIn("from {{ var('stats_crashes_overview') }}", overview_sql)
        self.assertIn("from {{ ref('int_google_play__store_performance') }}", overview_sql)
        self.assertIn("partition by source_relation, package_name", overview_sql)
        self.assertIn("rolling_store_conversion_rate", overview_sql)
        self.assertNotIn("country_short", overview_sql)
        self.assertNotIn("device as", overview_sql)
        self.assertIn("from {{ var('stats_store_performance_country') }}", store_performance_sql)
        self.assertIn("group by source_relation, date_day, package_name", store_performance_sql)
        self.assertIn("sum(store_listing_acquisitions)", store_performance_sql)
        self.assertNotIn("where 1 = 0", store_performance_sql)

    def test_tickit_fct_sales_uses_sale_event_and_user_dimensions(self) -> None:
        sql = synthesize_package_mart_sql(
            "fct_sales",
            [
                "sale_id",
                "sale_time",
                "qtr",
                "cat_group",
                "cat_name",
                "event_name",
                "buyer_username",
                "buyer_name",
                "buyer_state",
                "buyer_first_purchase_date",
                "seller_username",
                "seller_name",
                "seller_state",
                "seller_first_sale_date",
                "ticket_price",
                "qty_sold",
                "price_paid",
                "commission_prcnt",
                "commission",
                "earnings",
            ],
            [],
        )

        self.assertIn("from {{ ref('stg_tickit__sales') }}", sql)
        self.assertIn("from {{ ref('stg_tickit__events') }}", sql)
        self.assertIn("from {{ ref('stg_tickit__categories') }}", sql)
        self.assertIn("from {{ ref('stg_tickit__dates') }}", sql)
        self.assertIn("from {{ ref('int_buyers_extracted_from_users') }}", sql)
        self.assertIn("from {{ ref('stg_tickit__users') }}", sql)
        self.assertIn("min(cast(sale_time as date)) as first_sale_date", sql)
        self.assertIn("dates.qtr as qtr", sql)
        self.assertIn("left join event_categories", sql)
        self.assertIn("buyers.full_name as buyer_name", sql)
        self.assertIn("sellers.full_name as seller_name", sql)
        self.assertIn("sales.earnings as earnings", sql)

    def test_tpch_lowcost_brass_suppliers_uses_query_two_filters(self) -> None:
        sql = synthesize_package_mart_sql(
            "EUR_LOWCOST_BRASS_SUPPLIERS",
            [
                "p_name",
                "p_size",
                "p_retailprice",
                "s_acctbal",
                "s_name",
                "n_name",
                "p_partkey",
                "p_mfgr",
                "s_address",
                "s_phone",
                "s_comment",
            ],
            [],
        )

        self.assertIn("{{ source('TPCH_SF1', 'partsupp') }}", sql)
        self.assertIn("{{ ref('min_supply_cost') }}", sql)
        self.assertIn("part.p_size = 15", sql)
        self.assertIn("upper(part.p_type) like '%BRASS'", sql)
        self.assertIn("region.r_name = 'EUROPE'", sql)
        self.assertIn("partsupp.ps_supplycost = min_supply_cost.min_supply_cost", sql)

    def test_tpch_uk_lowcost_brass_suppliers_projects_availability(self) -> None:
        sql = synthesize_package_mart_sql(
            "UK_Lowcost_Brass_Suppliers",
            [
                "Part_Name",
                "RetailPrice",
                "Supplier_Name",
                "Part_Manufacturer",
                "SuppAddr",
                "Supp_Phone",
                "Num_Available",
            ],
            [],
        )

        self.assertIn("{{ ref('EUR_LOWCOST_BRASS_SUPPLIERS') }}", sql)
        self.assertIn("{{ source('TPCH_SF1', 'partsupp') }}", sql)
        self.assertIn("where europe.n_name = 'UNITED KINGDOM'", sql)
        self.assertIn("partsupp.ps_availqty as Num_Available", sql)

    def test_f1_finishes_by_constructor_aggregates_constructor_results(self) -> None:
        sql = synthesize_package_mart_sql(
            "finishes_by_constructor",
            [
                "constructor_id",
                "constructor_name",
                "races",
                "podiums",
                "pole_positions",
                "fastest_laps",
                "p6",
                "p7",
                "p10",
                "p11",
                "p14",
                "p15",
                "disqualified",
                "excluded",
                "failed_to_qualify",
                "not_classified",
                "retired",
                "withdrew",
            ],
            [],
        )

        self.assertIn("from {{ ref('stg_f1_dataset__results') }}", sql)
        self.assertIn("from {{ ref('stg_f1_dataset__constructors') }}", sql)
        self.assertIn("count(*) as races", sql)
        self.assertIn("count_if(position_order between 1 and 3) as podiums", sql)
        self.assertIn("count_if(grid_position_order = 1) as pole_positions", sql)
        self.assertIn("sum(case when position_desc = 'retired' then 1 else 0 end) as retired", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_f1_driver_championships_uses_season_rank(self) -> None:
        sql = synthesize_package_mart_sql(
            "driver_championships",
            ["driver_full_name", "total_championships"],
            [],
        )

        self.assertIn("from {{ ref('stg_f1_dataset__drivers') }}", sql)
        self.assertIn("from {{ ref('stg_f1_dataset__driver_standings') }}", sql)
        self.assertIn("rank() over (partition by race_year order by max_points desc)", sql)
        self.assertIn("where r_rank = 1", sql)
        self.assertIn("count(driver_full_name) as total_championships", sql)

    def test_f1_stg_drivers_uses_raw_driver_fields(self) -> None:
        sql = synthesize_package_mart_sql(
            "stg_f1_dataset__drivers",
            [
                "driver_id",
                "driver_ref",
                "driver_number",
                "driver_code",
                "driver_first_name",
                "driver_last_name",
                "driver_date_of_birth",
                "driver_nationality",
                "driver_url",
                "driver_full_name",
                "driver_current_age",
            ],
            [],
        )

        self.assertIn("from {{ ref('drivers') }}", sql)
        self.assertIn("driverId as driver_id", sql)
        self.assertIn("forename || ' ' || surname as driver_full_name", sql)
        self.assertIn("date_diff('year', dob, current_date) as driver_current_age", sql)
        self.assertNotIn("where 1 = 0", sql.lower())

    def test_f1_finishes_by_driver_uses_results_aggregation(self) -> None:
        sql = synthesize_package_mart_sql(
            "finishes_by_driver",
            [
                "driver_id",
                "driver_full_name",
                "races",
                "podiums",
                "pole_positions",
                "fastest_laps",
                "p1",
                "p21plus",
                "retired",
            ],
            [],
        )

        self.assertIn("from {{ ref('stg_f1_dataset__results') }}", sql)
        self.assertIn("from {{ ref('stg_f1_dataset__drivers') }}", sql)
        self.assertIn("count(*) as races", sql)
        self.assertIn("count_if(position_order between 1 and 3) as podiums", sql)
        self.assertIn("count_if(grid_position_order = 1) as pole_positions", sql)
        self.assertIn("sum(case when position_desc = 'retired' then 1 else 0 end) as retired", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_retail_report_customer_invoices_uses_cleaned_top_revenue(self) -> None:
        sql = synthesize_package_mart_sql(
            "report_customer_invoices",
            ["country", "total_invoices", "total_revenue"],
            [],
        )

        self.assertIn("from {{ source('retail', 'raw_invoices') }}", sql)
        self.assertIn("CustomerID is not null", sql)
        self.assertIn("InvoiceNo not like 'C%'", sql)
        self.assertIn("count(*) as total_invoices", sql)
        self.assertIn("sum(Quantity * UnitPrice) as total_revenue", sql)
        self.assertIn("order by total_revenue desc", sql)
        self.assertIn("limit 10", sql)
        self.assertNotIn("InvoiceDate as total_invoices", sql)

    def test_f1_driver_podiums_by_season_uses_position_filter(self) -> None:
        sql = synthesize_package_mart_sql(
            "driver_podiums_by_season",
            ["driver_full_name", "season", "podiums"],
            [],
        )

        self.assertIn("from {{ ref('stg_f1_dataset__results') }}", sql)
        self.assertIn("{{ ref('stg_f1_dataset__races') }}", sql)
        self.assertIn("{{ ref('stg_f1_dataset__drivers') }}", sql)
        self.assertIn("cast(results.position as integer) between 1 and 3", sql)
        self.assertIn("count(results.position) as podiums", sql)
        self.assertIn("order by season asc", sql)

    def test_f1_driver_fastest_laps_by_season_uses_rank_filter(self) -> None:
        sql = synthesize_package_mart_sql(
            "driver_fastest_laps_by_season",
            ["driver_full_name", "season", "fastest_laps"],
            [],
        )

        self.assertIn("where results.rank = 1", sql)
        self.assertIn("count(results.rank) as fastest_laps", sql)
        self.assertIn("group by drivers.driver_full_name, races.race_year", sql)

    def test_f1_constructor_retirements_by_season_uses_retired_status(self) -> None:
        sql = synthesize_package_mart_sql(
            "constructor_retirements_by_season",
            ["constructor_name", "season", "retirements"],
            [],
        )

        self.assertIn("{{ ref('stg_f1_dataset__constructors') }}", sql)
        self.assertIn("where lower(results.position_desc) = 'retired'", sql)
        self.assertIn("count(results.position_desc) as retirements", sql)
        self.assertIn("group by constructors.constructor_name, races.race_year", sql)

    def test_nba_reg_season_summary_uses_actuals_and_vegas(self) -> None:
        sql = synthesize_package_mart_sql(
            "reg_season_summary",
            ["team", "conf", "record", "avg_wins", "vegas_wins", "elo_vs_vegas"],
            [],
        )

        self.assertIn("from {{ ref('reg_season_end') }}", sql)
        self.assertIn("from {{ ref('nba_reg_season_actuals') }}", sql)
        self.assertIn("from {{ ref('nba_vegas_wins') }}", sql)
        self.assertIn("concat(cast(actuals.wins as varchar), ' - ', cast(actuals.losses as varchar))", sql)
        self.assertIn("round(vegas.win_total - round(reg_season.avg_wins, 1), 1)", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_nba_playoff_summary_counts_all_teams_and_rounds(self) -> None:
        sql = synthesize_package_mart_sql(
            "playoff_summary",
            ["team", "made_playoffs", "made_conf_semis", "made_conf_finals", "made_finals", "won_finals"],
            [],
        )

        self.assertIn("from {{ ref('nba_teams') }}", sql)
        self.assertIn("from {{ ref('initialize_seeding') }}", sql)
        self.assertIn("from {{ ref('playoff_sim_r1') }}", sql)
        self.assertIn("from {{ ref('playoff_sim_r4') }}", sql)
        self.assertIn("nullif(made_playoffs.made_playoffs, 0)", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_nba_season_summary_preserves_team_conf_and_elo_delta(self) -> None:
        sql = synthesize_package_mart_sql(
            "season_summary",
            [
                "elo_rating",
                "record",
                "avg_wins",
                "vegas_wins",
                "elo_vs_vegas",
                "made_playoffs",
                "made_conf_semis",
                "made_conf_finals",
                "made_finals",
                "won_finals",
            ],
            [],
        )

        self.assertIn("from {{ ref('reg_season_summary') }}", sql)
        self.assertIn("from {{ ref('playoff_summary') }}", sql)
        self.assertIn("from {{ ref('nba_ratings') }}", sql)
        self.assertIn("reg_season.team as team", sql)
        self.assertIn("reg_season.conf as conf", sql)
        self.assertIn("ratings.elo_rating - ratings.original_rating", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_reddit_posts_ghosts_filters_out_of_window_post(self) -> None:
        sql = synthesize_package_mart_sql(
            "prod_posts_ghosts",
            ["author_post", "post_id", "post_fullname", "post_title", "post_text", "post_upvote_ratio"],
            [],
        )

        self.assertIn("from main.raw_posts_ghosts", sql)
        self.assertIn("author_flair_text as author_flair_text", sql)
        self.assertIn("where created_at >= timestamp '2023-01-01 00:00:00'", sql)
        self.assertIn("strftime('%H'", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_twilio_number_overview_aggregates_messages_by_phone_number(self) -> None:
        sql = synthesize_package_mart_sql(
            "twilio__number_overview",
            ["phone_number", "total_outbound_messages", "total_delivered_messages", "total_messages", "total_spend"],
            [],
        )

        self.assertIn("from {{ ref('twilio__message_enhanced') }}", sql)
        self.assertIn("group by messages.phone_number", sql)
        self.assertIn("lower(coalesce(messages.direction, '')) like '%outbound%'", sql)
        self.assertIn("lower(coalesce(messages.status, '')) = 'delivered'", sql)
        self.assertIn("sum(coalesce(messages.price, 0)) as total_spend", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_twilio_account_overview_joins_account_history_and_daily_spend(self) -> None:
        sql = synthesize_package_mart_sql(
            "twilio__account_overview",
            [
                "account_id",
                "account_name",
                "account_status",
                "account_type",
                "price_unit",
                "total_outbound_messages",
                "total_delivered_messages",
                "total_messages",
                "total_messages_spend",
            ],
            [],
        )

        self.assertIn("from {{ ref('twilio__message_enhanced') }}", sql)
        self.assertIn("from {{ var('account_history') }}", sql)
        self.assertIn("from {{ var('usage_record') }}", sql)
        self.assertIn("account_history.friendly_name as account_name", sql)
        self.assertIn("round(abs(sum(coalesce(messages.price, 0))), 2) as total_messages_spend", sql)
        self.assertIn("group by messages.account_id", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_marketo_email_templates_keeps_most_recent_versions(self) -> None:
        sql = synthesize_package_mart_sql(
            "marketo__email_templates",
            [
                "created_timestamp",
                "email_template_id",
                "email_template_name",
                "is_most_recent_version",
                "count_sends",
                "count_deliveries",
            ],
            [],
        )

        self.assertIn("from {{ ref('stg_marketo__email_template_history') }}", sql)
        self.assertIn("where coalesce(is_most_recent_version, false)", sql)
        self.assertIn("from {{ ref('marketo__email_sends') }}", sql)
        self.assertIn("count(distinct email_send_id) as count_sends", sql)
        self.assertIn("coalesce(stats.count_deliveries, 0) as count_deliveries", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_mrr_uses_lagged_month_state_and_change_categories(self) -> None:
        sql = synthesize_package_mart_sql(
            "mrr",
            [
                "date_month",
                "customer_id",
                "mrr",
                "is_active",
                "previous_month_is_active",
                "previous_month_mrr",
                "mrr_change",
                "change_category",
            ],
            [],
        )

        self.assertIn("select * from {{ ref('customer_revenue_by_month') }}", sql)
        self.assertIn("select * from {{ ref('customer_churn_month') }}", sql)
        self.assertIn("lag(is_active) over (partition by customer_id order by date_month)", sql)
        self.assertIn("mrr - previous_month_mrr as mrr_change", sql)
        self.assertIn("when is_first_month then 'new'", sql)
        self.assertIn("when not is_active and previous_month_is_active then 'churn'", sql)
        self.assertIn("when is_active and not previous_month_is_active then 'reactivation'", sql)
        self.assertNotIn("cast(null", sql.lower())

    def test_intercom_admin_metrics_uses_team_mapping(self) -> None:
        sql = synthesize_package_mart_sql(
            "intercom__admin_metrics",
            ["admin_id", "admin_name", "team_id", "team_name"],
            [],
        )

        self.assertIn("from {{ var('admin') }}", sql)
        self.assertIn("from {{ var('team_admin') }}", sql)
        self.assertIn("from {{ var('team') }}", sql)
        self.assertIn("team_admin.team_id as team_id", sql)
        self.assertIn("team.name as team_name", sql)
        self.assertIn("left join team_admin on team_admin.admin_id = admin.admin_id", sql)

    def test_declared_model_synthesis_follows_generated_sql_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "schema.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: google_play__overview_report",
                        "    columns:",
                        "      - name: source_relation",
                        "      - name: date_day",
                        "      - name: package_name",
                        "      - name: store_listing_acquisitions",
                        "  - name: int_google_play__store_performance",
                        "    columns:",
                        "      - name: source_relation",
                        "      - name: date_day",
                        "      - name: package_name",
                        "      - name: store_listing_acquisitions",
                        "      - name: store_listing_visitors",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (models / "stg_google_play__stats_installs_overview.sql").write_text(
                "select null as source_relation, null as date_day, null as package_name",
                encoding="utf-8",
            )

            edits = apply_declared_model_synthesis(root, None, ["google_play__overview_report"])

            self.assertEqual(
                [edit["model"] for edit in edits],
                ["google_play__overview_report", "int_google_play__store_performance"],
            )
            overview = (models / "google_play__overview_report.sql").read_text(encoding="utf-8")
            store = (models / "int_google_play__store_performance.sql").read_text(encoding="utf-8")
            self.assertIn("{{ ref('int_google_play__store_performance') }}", overview)
            self.assertIn("stats_store_performance_country", store)
            self.assertNotIn("where 1 = 0", store)

    def test_tickit_star_models_join_required_dimensions(self) -> None:
        candidates = [
            {"name": "stg_tickit__events", "expr": "{{ ref('stg_tickit__events') }}"},
            {"name": "stg_tickit__venues", "expr": "{{ ref('stg_tickit__venues') }}"},
            {"name": "stg_tickit__categories", "expr": "{{ ref('stg_tickit__categories') }}"},
            {"name": "stg_tickit__dates", "expr": "{{ ref('stg_tickit__dates') }}"},
            {"name": "stg_tickit__listings", "expr": "{{ ref('stg_tickit__listings') }}"},
            {
                "name": "int_sellers_extracted_from_users",
                "expr": "{{ ref('int_sellers_extracted_from_users') }}",
            },
        ]
        dim_sql = synthesize_package_mart_sql(
            "dim_events",
            [
                "event_id",
                "event_name",
                "venue_name",
                "venue_city",
                "cat_group",
                "week",
            ],
            candidates,
        )
        fct_sql = synthesize_package_mart_sql(
            "fct_listings",
            [
                "list_id",
                "cat_group",
                "event_name",
                "seller_username",
                "seller_name",
                "total_price",
            ],
            candidates,
        )

        self.assertIn("from events", dim_sql)
        self.assertIn("inner join venues", dim_sql)
        self.assertIn("inner join categories", dim_sql)
        self.assertIn("inner join dates", dim_sql)
        self.assertIn("events.venue_id = venues.venue_id", dim_sql)

        self.assertIn("from listings", fct_sql)
        self.assertIn("inner join sellers", fct_sql)
        self.assertIn("listings.seller_id = sellers.user_id", fct_sql)
        self.assertIn("sellers.full_name as seller_name", fct_sql)
        self.assertNotIn("cast(null as varchar) as seller_name", fct_sql)

    def test_inzight_capacity_tariff_uses_monthly_peak_semantics(self) -> None:
        sql = synthesize_package_mart_sql(
            "mrt_capacity_tariff",
            [
                "month",
                "year",
                "month_peak_timestamp",
                "month_peak_timestamp_end",
                "month_peak_date",
                "month_peak_day_of_week_name",
                "month_peak_day_of_month",
                "month_peak_day_type",
                "month_peak_is_holiday",
                "month_peak_part_of_day",
                "month_peak_value",
                "month_peak_12month_avg",
                "month_name_short",
                "month_name",
                "month_start_date",
                "pct_change",
            ],
            [
                {"name": "fct_electricity", "expr": "{{ ref('fct_electricity') }}"},
                {"name": "stg_be_holidays", "expr": "{{ ref('stg_be_holidays') }}"},
            ],
        )

        self.assertIn("from {{ ref('fct_electricity') }}", sql)
        self.assertIn("from {{ ref('stg_be_holidays') }}", sql)
        self.assertIn("usage * 4.0 as month_peak_value", sql)
        self.assertIn("rows between current row and 11 following", sql)
        self.assertIn("row_number() over", sql)
        self.assertIn("order by usage desc, from_timestamp asc, to_timestamp asc", sql)
        self.assertLess(sql.index("month_name as month_name"), sql.index("month_peak_timestamp as month_peak_timestamp"))
        self.assertNotIn("cast(null", sql.lower())

    def test_jira_project_enhanced_starts_from_project_and_left_joins_metrics(self) -> None:
        sql = synthesize_package_mart_sql(
            "jira__project_enhanced",
            [
                "project_id",
                "project_key",
                "project_lead_user_id",
                "project_name",
                "project_lead_user_name",
                "components",
                "count_closed_issues",
                "avg_close_time_days",
                "median_close_time_seconds",
            ],
            [],
        )

        self.assertIn("from {{ var('project') }}", sql)
        self.assertIn("left join metrics on project.project_id = metrics.project_id", sql)
        self.assertIn("left join components on project.project_id = components.project_id", sql)
        self.assertIn("lead_user.user_display_name as project_lead_user_name", sql)
        self.assertIn("coalesce(metrics.count_closed_issues, 0) as count_closed_issues", sql)
        self.assertIn("string_agg(component_name, ', ' order by component_name desc) as components", sql)
        self.assertNotIn("from metrics", sql.split("select", 1)[0].lower())

    def test_recharge_charge_line_item_history_unions_line_discount_shipping_and_tax(self) -> None:
        sql = synthesize_package_mart_sql(
            "recharge__charge_line_item_history",
            [
                "charge_id",
                "charge_row_num",
                "source_index",
                "charge_created_at",
                "customer_id",
                "address_id",
                "amount",
                "title",
                "line_item_type",
            ],
            [],
        )

        self.assertIn("from {{ var('charge_line_item') }} as charge_line_item", sql)
        self.assertIn("from {{ var('charge_discount') }} as charge_discount", sql)
        self.assertIn("from {{ var('charge_shipping_line') }} as charge_shipping_line", sql)
        self.assertIn("from {{ var('charge_tax_line') }} as charge_tax_line", sql)
        self.assertIn("'charge line' as line_item_type", sql)
        self.assertIn("'discount' as line_item_type", sql)
        self.assertIn("'shipping' as line_item_type", sql)
        self.assertIn("'tax' as line_item_type", sql)
        self.assertIn("row_number() over (partition by charge_id order by sort_order, source_index) as charge_row_num", sql)
        self.assertIn("round(charge.subtotal_price * charge_discount.discount_value / 100.0, 2)", sql)

    def test_sap_0fi_gl_10_uses_existing_unpivot_model(self) -> None:
        sql = synthesize_package_mart_sql(
            "sap__0fi_gl_10",
            [
                "ryear",
                "rbukrs",
                "currency_type",
                "fiscal_period",
                "debit_amount",
                "credit_amount",
                "accumulated_balance",
                "turnover",
            ],
            [],
        )

        self.assertIn("from {{ ref('int_sap__0fi_gl_10_unpivot') }}", sql)
        self.assertIn("sum(debit_amount) as debit_amount", sql)
        self.assertIn("sum(accumulated_balance) as accumulated_balance", sql)
        self.assertIn("from grouped", sql)
        self.assertNotIn("cast(0 as double) as debit_amount", sql)
        self.assertNotIn("cast(null as varchar) as fiscal_period", sql)

    def test_sap_0fi_gl_14_starts_from_faglflexa_and_joins_headers(self) -> None:
        sql = synthesize_package_mart_sql(
            "sap__0fi_gl_14",
            [
                "ryear",
                "docnr",
                "rbukrs",
                "docln",
                "tsl",
                "hsl",
                "bukrs",
                "blart",
                "bldat",
                "kostl",
                "hkont",
                "xreorg",
            ],
            [],
        )

        self.assertIn("from {{ var('faglflexa') }}", sql)
        self.assertIn("left join bkpf", sql)
        self.assertIn("left join bseg", sql)
        self.assertIn("faglflexa.docnr as docnr", sql)
        self.assertIn("bkpf.blart as blart", sql)
        self.assertIn("bseg.kostl as kostl", sql)
        self.assertIn("faglflexa.belnr = bkpf.belnr", sql)
        self.assertIn("faglflexa.docnr = bseg.belnr", sql)
        self.assertNotIn("from {{ ref('stg_sap__bseg_tmp') }}", sql)

    def test_missing_ref_can_defer_placeholder_to_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "_models.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: missing_model",
                        "    columns:",
                        "      - name: id",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            edits = apply_missing_ref_placeholders(
                root,
                "model depends on a node named 'missing_model' which was not found",
                allow_placeholder=False,
            )

            self.assertEqual(edits[0]["kind"], "missing_ref_deferred_to_declared_model_synthesis")
            self.assertFalse((models / "missing_model.sql").exists())

    def test_synthesis_avoids_downstream_ref_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "_models.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: provider",
                        "    columns:",
                        "      - name: npi",
                        "      - name: primary_taxonomy_code",
                        "  - name: taxonomy_unpivot",
                        "    columns:",
                        "      - name: npi",
                        "      - name: taxonomy_code",
                        "  - name: other_provider_taxonomy",
                        "    columns:",
                        "      - name: npi",
                        "      - name: taxonomy_code",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (models / "provider.sql").write_text("select 1 as npi, 'x' as primary_taxonomy_code\n", encoding="utf-8")
            (models / "taxonomy_unpivot.sql").write_text("select 1 as npi, 'x' as taxonomy_code\n", encoding="utf-8")
            history = [
                "Model 'model.nppes.provider' (models/provider.sql) depends on a node named 'other_provider_taxonomy' which was not found"
            ]

            edits = apply_declared_model_synthesis(
                root,
                None,
                ["other_provider_taxonomy"],
                blocked_ref_bases=blocked_ref_bases_from_history(history),
            )

            self.assertEqual(len(edits), 1)
            self.assertEqual(edits[0]["base"], "taxonomy_unpivot")
            generated = (models / "other_provider_taxonomy.sql").read_text(encoding="utf-8")
            self.assertIn("from {{ ref('taxonomy_unpivot') }}", generated)
            self.assertNotIn("ref('provider')", generated)

    def test_sql_output_columns_parse_aliases(self) -> None:
        cols = sql_output_columns(
            "{{ config(materialized='table') }}\n\n"
            "select date, concat(ticker, ts) as tt_key, sum(quantity) as shares\n"
            "from {{ ref('trades') }}\n"
        )

        self.assertEqual(cols, ["date", "tt_key", "shares"])

    def test_sql_output_columns_parse_trailing_expression_alias(self) -> None:
        cols = sql_output_columns(
            "select date, tt_key, ts, ticker, "
            "sum(aggregate_qty) over (partition by ticker order by ts) shares\n"
            "from {{ ref('bar_executions') }}\n"
        )

        self.assertEqual(cols, ["date", "tt_key", "ts", "ticker", "shares"])

    def test_sql_output_columns_follow_final_star_cte(self) -> None:
        cols = sql_output_columns(
            "with source as (\n"
            "  select * from {{ source('pkg', 'raw') }}\n"
            "), standardized as (\n"
            "  select title, 2 as category_id, status as renewal_status from source\n"
            ")\n"
            "select * from standardized\n"
        )

        self.assertEqual(cols, ["title", "category_id", "renewal_status"])

    def test_sql_output_columns_expands_star_from_prior_cte(self) -> None:
        cols = sql_output_columns(
            "with renamed as (\n"
            "  select country_iso_code, country_name, nationality from source\n"
            "), surrogate_keys as (\n"
            "  select md5(country_iso_code) as country_sk, * from renamed\n"
            ")\n"
            "select * from surrogate_keys\n"
        )

        self.assertEqual(cols, ["country_sk", "country_iso_code", "country_name", "nationality"])

    def test_sql_output_columns_respects_star_exclude_from_prior_cte(self) -> None:
        cols = sql_output_columns(
            "with renamed as (\n"
            "  select player_id, date_of_birth, player_name from source\n"
            "), surrogate_keys as (\n"
            "  select md5(player_id) as player_sk, cast(date_of_birth as varchar) as date_of_birth, * exclude(date_of_birth) from renamed\n"
            ")\n"
            "select * from surrogate_keys\n"
        )

        self.assertEqual(cols, ["player_sk", "date_of_birth", "player_id", "player_name"])

    def test_sql_output_columns_does_not_recurse_on_self_referential_cte(self) -> None:
        cols = sql_output_columns(
            "with recursive rollup as (\n"
            "  select id from source\n"
            "  union all\n"
            "  select * from rollup\n"
            ")\n"
            "select * from rollup\n"
        )

        self.assertEqual(cols, ["id"])

    def test_synthesis_uses_aggregate_templates(self) -> None:
        sql = synthesize_declared_model_sql(
            "bar_quotes",
            ["date", "tt_key", "ts", "ticker", "avg_bid_pr", "avg_ask_pr", "avg_mid_pr"],
            {
                "expr": "{{ source('asset_mgmt', 'quotes') }}",
                "columns": ["date", "ts", "bid_pr", "ask_pr", "ticker"],
            },
        )

        self.assertIn("avg(bid_pr) as avg_bid_pr", sql)
        self.assertIn("avg(ask_pr) as avg_ask_pr", sql)
        self.assertIn("avg((bid_pr + ask_pr) / 2.0) as avg_mid_pr", sql)
        self.assertIn("concat(cast(ticker as varchar), cast(ts as varchar)) as tt_key", sql)
        self.assertIn("group by", sql.lower())

    def test_synthesis_can_join_related_value_model(self) -> None:
        sql = synthesize_declared_model_sql(
            "book_value",
            ["tt_key", "ticker", "ts", "shares", "value"],
            {
                "name": "positions_shares",
                "kind": "ref",
                "expr": "{{ ref('positions_shares') }}",
                "columns": ["date", "tt_key", "ts", "ticker", "shares"],
            },
            [
                {
                    "name": "bar_quotes",
                    "kind": "ref",
                    "expr": "{{ ref('bar_quotes') }}",
                    "columns": ["date", "tt_key", "ts", "ticker", "avg_bid_pr", "avg_ask_pr", "avg_mid_pr"],
                }
            ],
        )

        self.assertIn("from {{ ref('positions_shares') }} as b", sql)
        self.assertIn("inner join {{ ref('bar_quotes') }} as p", sql)
        self.assertIn("b.shares * p.avg_mid_pr as value", sql)
        self.assertIn("b.tt_key = p.tt_key", sql)
        self.assertNotIn("b.date = p.date", sql)

    def test_synthesis_unions_related_base_models(self) -> None:
        sql = synthesize_declared_model_sql(
            "stg_google_sheets__originals_unioned",
            [
                "title",
                "genre",
                "category_id",
                "seasons",
                "runtime",
                "renewal_status",
                "premiere_date",
                "premiere_year",
                "premiere_month",
                "premiere_day",
                "updated_at_utc",
            ],
            {"name": "base_google_sheets__original_comedies", "expr": "{{ ref('base_google_sheets__original_comedies') }}", "columns": ["title"]},
            [
                {
                    "name": "base_google_sheets__original_comedies",
                    "expr": "{{ ref('base_google_sheets__original_comedies') }}",
                    "columns": ["title", "category_id", "genre", "premiere", "seasons", "runtime", "status", "updated_at"],
                },
                {
                    "name": "base_google_sheets__original_docuseries",
                    "expr": "{{ ref('base_google_sheets__original_docuseries') }}",
                    "columns": ["title", "category_id", "subject", "premiere", "seasons", "runtime", "status", "updated_at"],
                },
                {
                    "name": "stg_google_sheets__original_categories",
                    "expr": "{{ ref('stg_google_sheets__original_categories') }}",
                    "columns": ["category_id", "category", "updated_at_utc"],
                },
            ],
        )

        self.assertIn("with unioned as", sql)
        self.assertIn("union all", sql)
        self.assertIn("select distinct", sql)
        self.assertIn("regexp_replace(replace(title, '*', ''), '\\[[^\\]]*\\]', '', 'g') as title", sql)
        self.assertIn("subject as genre", sql)
        self.assertIn("status as renewal_status", sql)
        self.assertIn("try_strptime(premiere, '%B %-d, %Y')", sql)
        self.assertNotIn("stg_google_sheets__original_categories", sql)

    def test_declared_synthesis_reuses_models_generated_in_same_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "_models.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "sources:",
                        "  - name: asset_mgmt",
                        "    tables:",
                        "      - name: quotes",
                        "models:",
                        "  - name: positions_shares",
                        "    columns:",
                        "      - name: date",
                        "      - name: tt_key",
                        "      - name: ts",
                        "      - name: ticker",
                        "      - name: shares",
                        "  - name: bar_quotes",
                        "    columns:",
                        "      - name: date",
                        "      - name: tt_key",
                        "      - name: ts",
                        "      - name: ticker",
                        "      - name: avg_bid_pr",
                        "      - name: avg_ask_pr",
                        "      - name: avg_mid_pr",
                        "  - name: book_value",
                        "    columns:",
                        "      - name: tt_key",
                        "      - name: ticker",
                        "      - name: ts",
                        "      - name: shares",
                        "      - name: value",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (models / "positions_shares.sql").write_text(
                "select date, tt_key, ts, ticker, shares from {{ ref('bar_executions') }}\n",
                encoding="utf-8",
            )
            (models / "stg_quotes.sql").write_text(
                "select date, ts, ticker, bid_pr, ask_pr from {{ source('asset_mgmt', 'quotes') }}\n",
                encoding="utf-8",
            )

            edits = apply_declared_model_synthesis(root, None, ["bar_quotes", "book_value"])

            self.assertEqual([edit["model"] for edit in edits], ["bar_quotes", "book_value"])
            book_value = (models / "book_value.sql").read_text(encoding="utf-8")
            self.assertIn("inner join {{ ref('bar_quotes') }} as p", book_value)
            self.assertIn("b.shares * p.avg_mid_pr as value", book_value)

    def test_synthesis_uses_taxonomy_crosswalk_mapping(self) -> None:
        sql = synthesize_declared_model_sql(
            "specialty_mapping",
            ["taxonomy_code", "medicare_specialty_code", "description"],
            {
                "name": "other_provider_taxonomy",
                "kind": "ref",
                "expr": "{{ ref('other_provider_taxonomy') }}",
                "columns": ["npi", "taxonomy_code", "medicare_specialty_code", "description"],
            },
            [
                {
                    "name": "nucc_taxonomy",
                    "kind": "source",
                    "expr": "{{ source('nppes', 'nucc_taxonomy') }}",
                    "columns": ["Code", "Classification", "Specialization"],
                },
                {
                    "name": "medicare_specialty_crosswalk",
                    "kind": "source",
                    "expr": "{{ source('nppes', 'medicare_specialty_crosswalk') }}",
                    "columns": ["provider_taxonomy_code", "medicare_specialty_code"],
                },
            ],
        )

        self.assertIn("from {{ source('nppes', 'nucc_taxonomy') }} as n", sql)
        self.assertIn("from {{ source('nppes', 'medicare_specialty_crosswalk') }}", sql)
        self.assertIn("left join cw on n.Code = cw.provider_taxonomy_code", sql)
        self.assertIn("regexp_matches(medicare_specialty_code, '^[0-9]')", sql)

    def test_column_similarity_prioritizes_crosswalk_source(self) -> None:
        target_columns = ["taxonomy_code", "medicare_specialty_code", "description"]
        crosswalk = {
            "name": "medicare_specialty_crosswalk",
            "kind": "source",
            "columns": [
                "medicare_specialty_code",
                "provider_taxonomy_code",
                "provider_taxonomy_description",
            ],
        }
        unpivot = {
            "name": "taxonomy_unpivot",
            "kind": "ref",
            "columns": ["npi", "taxonomy_col", "taxonomy_code", "taxonomy_switch"],
        }

        self.assertGreater(
            relation_candidate_score("specialty_mapping", target_columns, crosswalk),
            relation_candidate_score("specialty_mapping", target_columns, unpivot),
        )

    def test_package_source_map_ignores_column_names_and_inline_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_models = root / "dbt_packages" / "shopify_source" / "models"
            package_models.mkdir(parents=True)
            (package_models / "src_shopify.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "sources:",
                        "  - name: shopify # package comment",
                        "    tables:",
                        "      - name: product",
                        "        columns:",
                        "          - name: product_id",
                        "          - name: title",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            mapping = source_table_map_from_yml(root)

            self.assertEqual(mapping["product"], ("shopify", "product"))
            self.assertNotIn("product_id", mapping)
            self.assertNotIn("title", mapping)

    def test_raw_entity_source_beats_downstream_metric_ref_for_entity_model(self) -> None:
        columns = ["team_id", "team_name", "number_of_open_tasks", "active_projects"]
        raw_team = {"name": "team", "kind": "table", "columns": ["id", "name", "organization_id"]}
        project_metrics = {
            "name": "asana__project",
            "kind": "ref",
            "columns": ["team_id", "team_name", "number_of_open_tasks", "active_projects"],
        }

        self.assertGreater(
            relation_candidate_score("asana__team", columns, raw_team),
            relation_candidate_score("asana__team", columns, project_metrics),
        )

    def test_plural_entity_source_beats_downstream_metric_ref_for_entity_model(self) -> None:
        columns = [
            "contact_id",
            "email",
            "contact_company",
            "first_name",
            "last_name",
            "created_date",
            "total_bounces",
            "total_clicks",
        ]
        raw_contact = {
            "name": "contact",
            "kind": "table",
            "columns": [
                "id",
                "_fivetran_synced",
                "property_email",
                "property_company",
                "property_firstname",
                "property_lastname",
                "property_createdate",
                "property_annualrevenue",
            ],
        }
        email_sends = {
            "name": "hubspot__email_sends",
            "kind": "ref",
            "columns": [
                "contact_id",
                "email",
                "created_date",
                "total_bounces",
                "total_clicks",
                "total_opens",
            ],
        }

        self.assertGreater(
            relation_candidate_score("hubspot__contacts", columns, raw_contact),
            relation_candidate_score("hubspot__contacts", columns, email_sends),
        )

    def test_entity_resolution_ref_beats_plain_staging_when_merge_field_is_required(self) -> None:
        columns = [
            "contact_id",
            "is_contact_deleted",
            "calculated_merged_vids",
            "email",
            "contact_company",
            "first_name",
            "last_name",
        ]
        staging_contact = {
            "name": "stg_hubspot__contact",
            "kind": "ref",
            "columns": columns,
        }
        merged_contact = {
            "name": "int_hubspot__contact_merge_adjust",
            "kind": "ref",
            "columns": columns,
        }

        self.assertGreater(
            relation_candidate_score("hubspot__contacts", columns, merged_contact),
            relation_candidate_score("hubspot__contacts", columns, staging_contact),
        )

    def test_source_family_name_match_beats_cross_platform_same_shape_ref(self) -> None:
        columns = [
            "source_relation",
            "date_day",
            "app_platform",
            "app_name",
            "deletions",
            "downloads",
            "page_views",
            "crashes",
        ]
        google_overview = {
            "name": "google_play__overview_report",
            "kind": "ref",
            "columns": columns,
        }
        apple_overview = {
            "name": "int_apple_store__overview",
            "kind": "ref",
            "columns": columns,
        }

        self.assertGreater(
            relation_candidate_score("int_google_play__overview", columns, google_overview),
            relation_candidate_score("int_google_play__overview", columns, apple_overview),
        )

    def test_ref_candidates_prefer_actual_sql_output_columns_over_stale_yml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (models / "int_greenhouse__job_info.sql").write_text(
                "select job_id, status, job_status as job_status, office_locations as office_locations "
                "from {{ ref('stg_job') }}\n",
                encoding="utf-8",
            )
            (models / "schema.yml").write_text(
                "version: 2\n"
                "models:\n"
                "  - name: int_greenhouse__job_info\n"
                "    columns:\n"
                "      - name: job_id\n"
                "      - name: status\n"
                "      - name: locations\n",
                encoding="utf-8",
            )

            candidates = relation_candidates(root, None)
            candidate = next(item for item in candidates if item["name"] == "int_greenhouse__job_info")

        self.assertIn("office_locations", candidate["columns"])
        self.assertIn("status", candidate["columns"])
        self.assertIn("job_status", candidate["columns"])
        self.assertNotIn("locations", candidate["columns"])

    def test_entity_id_and_property_columns_map_to_raw_source_columns(self) -> None:
        raw_columns = [
            "id",
            "property_email",
            "property_company",
            "property_firstname",
            "property_lastname",
            "property_createdate",
            "property_annualrevenue",
        ]

        self.assertEqual(dimension_expression("contact_id", raw_columns), "id")
        self.assertEqual(closest_column("contact_company", raw_columns), "property_company")
        self.assertEqual(closest_column("company_annual_revenue", raw_columns), "property_annualrevenue")

    def test_role_prefixed_columns_prefer_source_role_over_suffix_match(self) -> None:
        raw_columns = ["post_url", "author", "comment_id", "link_comment"]

        self.assertEqual(closest_column("author_comment", raw_columns), "author")
        self.assertEqual(dimension_expression("author_comment", raw_columns), "author")

    def test_time_derived_columns_map_to_timestamp_source_columns(self) -> None:
        raw_columns = ["post_id", "created_at", "updated_at"]

        hour_expr = dimension_expression("hour_post_created_at", raw_columns)
        normalized_expr = dimension_expression("normalized_post_created_at", raw_columns)

        self.assertIn("strftime('%H'", hour_expr)
        self.assertIn("try_cast(created_at as timestamp)", hour_expr)
        self.assertIn("strftime('%Y-%m-%d %H:00:00'", normalized_expr)
        self.assertIn("date_trunc('hour'", normalized_expr)
        self.assertIn("try_cast(created_at as timestamp)", normalized_expr)

    def test_date_like_targets_parse_slash_delimited_source_dates(self) -> None:
        expr = dimension_expression("date_rep", ["date_rep", "cases", "deaths"])

        self.assertIn("try_strptime", expr)
        self.assertIn("%d/%m/%Y", expr)
        self.assertIn("date_rep", expr)

    def test_code_lookup_join_maps_country_code_and_report_date(self) -> None:
        sql = synthesize_code_lookup_join_sql(
            "covid_cases",
            ["cases", "deaths", "country", "report_date"],
            ["stg_covid__cases", "ref__country_codes"],
            [
                {
                    "name": "stg_covid__cases",
                    "expr": "{{ ref('stg_covid__cases') }}",
                    "columns": ["date_rep", "cases", "deaths", "geo_id"],
                },
                {
                    "name": "ref__country_codes",
                    "expr": "main.ref__country_codes",
                    "columns": ["country", "alpha_2code", "alpha_3code"],
                },
            ],
        )

        self.assertIn("left join main.ref__country_codes as lookup", sql)
        self.assertIn("fact.geo_id = lookup.alpha_2code", sql)
        self.assertIn("lookup.country as country", sql)
        self.assertIn("try_strptime", sql)
        self.assertIn("fact.date_rep", sql)
        self.assertIn("%d/%m/%Y", sql)
        self.assertIn("as report_date", sql)

    def test_code_lookup_join_uses_shared_identifier_keys(self) -> None:
        sql = synthesize_code_lookup_join_sql(
            "dim_doctors",
            [
                "doctor_id",
                "npi",
                "practice_id",
                "first_name",
                "last_name",
                "doctor_name",
                "specialty_category",
                "specialty_name",
            ],
            ["stg_doctors", "stg_doc_specialties"],
            [
                {
                    "name": "stg_doctors",
                    "expr": "{{ ref('stg_doctors') }}",
                    "columns": ["doctor_id", "doctor_name", "npi", "practice_id", "first_name", "last_name"],
                },
                {
                    "name": "stg_doc_specialties",
                    "expr": "{{ ref('stg_doc_specialties') }}",
                    "columns": ["doctor_id", "specialty_category", "specialty_name"],
                },
            ],
        )

        self.assertIn("left join {{ ref('stg_doc_specialties') }} as lookup", sql)
        self.assertIn("fact.doctor_id = lookup.doctor_id", sql)
        self.assertIn("lookup.specialty_category as specialty_category", sql)
        self.assertIn("lookup.specialty_name as specialty_name", sql)

    def test_declared_columns_are_augmented_with_timestamp_derivatives_when_source_supports_them(self) -> None:
        columns = augment_declared_system_columns(
            "prod_posts",
            ["author_post", "post_created_at", "post_score"],
            [
                {
                    "name": "raw_posts",
                    "kind": "source",
                    "expr": "main.raw_posts",
                    "columns": ["author", "created_at", "post_score"],
                }
            ],
        )

        self.assertEqual(
            columns,
            [
                "author_post",
                "post_created_at",
                "hour_post_created_at",
                "normalized_post_created_at",
                "post_score",
            ],
        )

    def test_entity_source_missing_metrics_default_to_zero_or_null(self) -> None:
        sql = synthesize_declared_model_sql(
            "asana__user",
            ["user_id", "user_name", "number_of_open_tasks", "avg_close_time_days"],
            {
                "name": "user",
                "kind": "table",
                "expr": "main.user_data",
                "columns": ["id", "name", "email"],
            },
        )

        self.assertIn("id as user_id", sql)
        self.assertIn("name as user_name", sql)
        self.assertIn("cast(0 as double) as number_of_open_tasks", sql)
        self.assertIn("cast(null as double) as avg_close_time_days", sql)
        self.assertNotIn("count(*) as number_of_open_tasks", sql)

    def test_enhanced_models_preserve_fivetran_system_column_when_available(self) -> None:
        sql = synthesize_declared_model_sql(
            "greenhouse__application_enhanced",
            ["application_id", "status"],
            {
                "name": "int_greenhouse__application_info",
                "kind": "ref",
                "expr": "{{ ref('int_greenhouse__application_info') }}",
                "columns": ["application_id", "status"],
            },
            [
                {
                    "name": "stg_greenhouse__application",
                    "kind": "source",
                    "expr": "{{ source('greenhouse', 'application') }}",
                    "columns": ["id", "_fivetran_synced", "status"],
                }
            ],
        )

        self.assertLess(sql.index("as _fivetran_synced"), sql.index("as application_id"))

    def test_missing_leading_fivetran_system_column_keeps_declared_position(self) -> None:
        sql = synthesize_declared_model_sql(
            "greenhouse__application_enhanced",
            ["_fivetran_synced", "applied_at", "application_id", "count_interviews"],
            {
                "name": "int_greenhouse__application_info",
                "kind": "ref",
                "expr": "{{ ref('int_greenhouse__application_info') }}",
                "columns": ["applied_at", "application_id"],
            },
        )

        self.assertLess(sql.index("as _fivetran_synced"), sql.index("as applied_at"))
        self.assertIn("cast(null as varchar) as _fivetran_synced", sql)

    def test_ref_dimension_rollup_uses_related_detail_tables_for_missing_metrics(self) -> None:
        sql = synthesize_declared_model_sql(
            "qualtrics__directory",
            [
                "directory_id",
                "name",
                "count_distinct_emails",
                "count_distinct_phones",
                "total_count_contacts",
                "total_count_unsubscribed_contacts",
                "count_surveys_sent_30d",
                "count_mailing_lists",
            ],
            {
                "name": "stg_qualtrics__directory",
                "kind": "ref",
                "expr": "{{ ref('stg_qualtrics__directory') }}",
                "columns": ["directory_id", "name"],
            },
            [
                {
                    "name": "stg_qualtrics__directory_contact",
                    "kind": "ref",
                    "expr": "{{ ref('stg_qualtrics__directory_contact') }}",
                    "columns": [
                        "directory_id",
                        "contact_id",
                        "email",
                        "phone",
                        "is_unsubscribed_from_directory",
                    ],
                },
                {
                    "name": "stg_qualtrics__directory_mailing_list",
                    "kind": "ref",
                    "expr": "{{ ref('stg_qualtrics__directory_mailing_list') }}",
                    "columns": ["directory_id", "mailing_list_id"],
                },
            ],
        )

        self.assertIn("count(distinct email) as count_distinct_emails", sql)
        self.assertIn("count(distinct phone) as count_distinct_phones", sql)
        self.assertIn("count(distinct contact_id) as total_count_contacts", sql)
        self.assertIn("when coalesce(is_unsubscribed_from_directory, false)", sql)
        self.assertIn("count(distinct mailing_list_id) as count_mailing_lists", sql)
        self.assertIn("cast(0 as double) as count_surveys_sent_30d", sql)

    def test_count_target_sums_numeric_count_column_from_related_rollup(self) -> None:
        result = related_rollup_expression(
            "count_mailing_lists",
            "directory_id",
            "directory",
            {
                "name": "qualtrics__contact",
                "kind": "ref",
                "expr": "{{ ref('qualtrics__contact') }}",
                "columns": ["directory_id", "count_mailing_lists_subscribed_to"],
            },
        )

        self.assertEqual(
            result,
            ("directory_id", "sum(count_mailing_lists_subscribed_to)", "zero"),
        )

    def test_identifier_like_score_key_is_not_treated_as_numeric_measure(self) -> None:
        self.assertFalse(is_probably_numeric_measure_column("interview_scorecard_key"))
        self.assertFalse(is_probably_numeric_measure_column("application_id"))
        self.assertTrue(is_probably_numeric_measure_column("count_mailing_lists_subscribed_to"))

    def test_boolean_dimension_does_not_reuse_non_boolean_text_column(self) -> None:
        sql = synthesize_declared_model_sql(
            "greenhouse__application_enhanced",
            ["application_id", "has_interviewed_w_hiring_manager"],
            {
                "name": "int_greenhouse__application_info",
                "kind": "ref",
                "expr": "{{ ref('int_greenhouse__application_info') }}",
                "columns": ["application_id", "hiring_managers"],
            },
        )

        self.assertIn("false as has_interviewed_w_hiring_manager", sql)
        self.assertNotIn("hiring_managers as has_interviewed_w_hiring_manager", sql)

    def test_text_event_columns_are_safely_cast_before_sum_rollup(self) -> None:
        sql = synthesize_declared_model_sql(
            "hubspot__contacts",
            ["contact_id", "total_bounces", "total_clicks", "count_engagement_emails"],
            {
                "name": "contact",
                "kind": "table",
                "expr": "main.contact_data",
                "columns": ["id", "property_email"],
            },
            [
                {
                    "name": "hubspot__email_event_bounce",
                    "kind": "ref",
                    "expr": "{{ ref('hubspot__email_event_bounce') }}",
                    "columns": ["contact_id", "bounce_category"],
                },
                {
                    "name": "hubspot__email_event_click",
                    "kind": "ref",
                    "expr": "{{ ref('hubspot__email_event_click') }}",
                    "columns": ["contact_id", "click_url"],
                }
            ],
        )

        self.assertIn("sum(coalesce(try_cast(bounce_category as double), 0)) as total_bounces", sql)
        self.assertIn("sum(coalesce(try_cast(click_url as double), 0)) as total_clicks", sql)
        self.assertNotIn("sum(bounce_category) as total_bounces", sql)
        self.assertNotIn("sum(click_url) as total_clicks", sql)
        self.assertNotIn("property_email as count_engagement_emails", sql)

    def test_dimension_rich_ref_base_preserves_base_column_order(self) -> None:
        sql = synthesize_declared_model_sql(
            "hubspot__email_campaigns",
            [
                "app_id",
                "content_id",
                "email_campaign_id",
                "_fivetran_synced",
                "app_name",
                "email_campaign_name",
                "total_bounces",
            ],
            {
                "name": "stg_hubspot__email_campaign",
                "kind": "ref",
                "expr": "{{ ref('stg_hubspot__email_campaign') }}",
                "columns": [
                    "_fivetran_synced",
                    "app_id",
                    "app_name",
                    "content_id",
                    "email_campaign_id",
                    "email_campaign_name",
                ],
            },
        )

        self.assertIn("select distinct", sql)
        self.assertLess(sql.index("as _fivetran_synced"), sql.index("as app_id"))
        self.assertLess(sql.index("as app_name"), sql.index("as content_id"))
        self.assertLess(sql.index("as content_id"), sql.index("as email_campaign_id"))
        self.assertIn("cast(0 as double) as total_bounces", sql)

    def test_entity_source_rolls_up_related_project_metrics_for_team(self) -> None:
        sql = synthesize_declared_model_sql(
            "asana__team",
            [
                "team_id",
                "team_name",
                "number_of_open_tasks",
                "number_of_assigned_open_tasks",
                "number_of_tasks_completed",
                "avg_close_time_days",
                "avg_close_time_assigned_days",
                "number_of_active_projects",
                "active_projects",
                "number_of_archived_projects",
            ],
            {
                "name": "team",
                "kind": "table",
                "expr": "main.team_data",
                "columns": ["id", "name", "organization_id"],
            },
            [
                {
                    "name": "asana__project",
                    "kind": "ref",
                    "expr": "{{ ref('asana__project') }}",
                    "columns": [
                        "project_id",
                        "project_name",
                        "team_id",
                        "number_of_open_tasks",
                        "number_of_assigned_open_tasks",
                        "number_of_tasks_completed",
                        "avg_close_time_days",
                        "avg_close_time_assigned_days",
                        "is_archived",
                    ],
                }
            ],
        )

        self.assertIn("from main.team_data as b", sql)
        self.assertIn("left join r_asanaproject_teamid on b.id = r_asanaproject_teamid.__entity_id", sql)
        self.assertIn("sum(number_of_open_tasks) as number_of_open_tasks", sql)
        self.assertIn("count(case when not coalesce(is_archived, false) then project_id end) as number_of_active_projects", sql)
        self.assertIn("string_agg(case when not coalesce(is_archived, false) then project_name end, ', ') as active_projects", sql)
        self.assertIn("count(case when coalesce(is_archived, false) then project_id end) as number_of_archived_projects", sql)
        self.assertNotIn("count(distinct is_archived) as number_of_archived_projects", sql)
        self.assertLess(
            sql.index("number_of_active_projects, 0) as number_of_active_projects"),
            sql.index("active_projects as active_projects"),
        )

    def test_entity_source_rolls_up_multiple_user_related_models(self) -> None:
        sql = synthesize_declared_model_sql(
            "asana__user",
            [
                "user_id",
                "email",
                "user_name",
                "number_of_open_tasks",
                "number_of_tasks_completed",
                "avg_close_time_days",
                "number_of_projects_owned",
                "number_of_projects_currently_assigned_to",
                "projects_working_on",
            ],
            {
                "name": "user",
                "kind": "table",
                "expr": "main.user_data",
                "columns": ["id", "email", "name"],
            },
            [
                {
                    "name": "int_asana__user_task_metrics",
                    "kind": "ref",
                    "expr": "{{ ref('int_asana__user_task_metrics') }}",
                    "columns": ["user_id", "number_of_open_tasks", "number_of_tasks_completed", "avg_close_time_days"],
                },
                {
                    "name": "int_asana__project_user",
                    "kind": "ref",
                    "expr": "{{ ref('int_asana__project_user') }}",
                    "columns": ["project_id", "project_name", "user_id", "currently_working_on"],
                },
                {
                    "name": "asana__project",
                    "kind": "ref",
                    "expr": "{{ ref('asana__project') }}",
                    "columns": ["project_id", "project_name", "owner_user_id"],
                },
            ],
        )

        self.assertIn("b.id as user_id", sql)
        self.assertIn("b.email as email", sql)
        self.assertIn("b.name as user_name", sql)
        self.assertLess(sql.index("b.email as email"), sql.index("b.name as user_name"))
        self.assertIn("sum(number_of_open_tasks) as number_of_open_tasks", sql)
        self.assertIn("count(distinct project_id) as number_of_projects_owned", sql)
        self.assertIn("count(distinct project_id) as number_of_projects_currently_assigned_to", sql)
        self.assertIn("string_agg(case when coalesce(currently_working_on, false) then project_name end, ', ') as projects_working_on", sql)

    def test_entity_source_preserves_mid_price_derived_metric(self) -> None:
        sql = synthesize_declared_model_sql(
            "bar_quotes",
            ["ticker", "avg_bid_pr", "avg_ask_pr", "avg_mid_pr"],
            {
                "name": "quotes",
                "kind": "source",
                "expr": "{{ source('asset_mgmt', 'quotes') }}",
                "columns": ["ticker", "bid_pr", "ask_pr"],
            },
        )

        self.assertIn("avg((bid_pr + ask_pr) / 2.0) as avg_mid_pr", sql)
        self.assertNotIn("cast(null as double) as avg_mid_pr", sql)

    def test_source_identifier_prefers_short_direct_entity_table(self) -> None:
        self.assertEqual(
            best_source_identifier(
                "product",
                [
                    ("main", "shopify_collection_product_data"),
                    ("main", "shopify_product_data"),
                ],
            ),
            ("main", "shopify_product_data"),
        )

    def test_relation_candidates_skip_statically_disabled_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (root / "dbt_project.yml").write_text("name: demo\nvars:\n  disabled_flag: false\n", encoding="utf-8")
            (models / "_models.yml").write_text(
                "\n".join(
                    [
                        "version: 2",
                        "models:",
                        "  - name: enabled_model",
                        "    columns:",
                        "      - name: id",
                        "  - name: disabled_model",
                        "    columns:",
                        "      - name: id",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (models / "enabled_model.sql").write_text("select 1 as id\n", encoding="utf-8")
            (models / "disabled_model.sql").write_text(
                "{{ config(enabled=var('disabled_flag', False)) }}\nselect 1 as id\n",
                encoding="utf-8",
            )

            names = {candidate["name"] for candidate in relation_candidates(root, None)}

            self.assertIn("enabled_model", names)
            self.assertNotIn("disabled_model", names)

    def test_shopify_products_mart_uses_intermediate_product_rollups(self) -> None:
        sql = synthesize_declared_model_sql(
            "shopify__products",
            [
                "is_deleted",
                "_fivetran_synced",
                "created_timestamp",
                "handle",
                "product_id",
                "product_type",
                "published_timestamp",
                "published_scope",
                "title",
                "updated_timestamp",
                "vendor",
                "total_quantity_sold",
                "subtotal_sold",
                "quantity_sold_net_refunds",
                "subtotal_sold_net_refunds",
                "first_order_timestamp",
                "most_recent_order_timestamp",
                "source_relation",
                "avg_quantity_per_order_line",
                "product_total_discount",
                "product_avg_discount_per_order_line",
                "product_total_tax",
                "product_avg_tax_per_order_line",
                "count_variants",
                "has_product_image",
                "status",
                "collections",
                "tags",
            ],
            {"name": "shopify_product_data", "kind": "table", "expr": "main.shopify_product_data", "columns": []},
            [
                {
                    "name": "int_shopify__products_with_aggregates",
                    "kind": "ref",
                    "expr": "{{ ref('int_shopify__products_with_aggregates') }}",
                    "columns": [],
                },
                {
                    "name": "int_shopify__product__order_line_aggregates",
                    "kind": "ref",
                    "expr": "{{ ref('int_shopify__product__order_line_aggregates') }}",
                    "columns": [],
                },
            ],
        )

        self.assertIn("from {{ ref('int_shopify__products_with_aggregates') }}", sql)
        self.assertIn("select * from {{ ref('int_shopify__product__order_line_aggregates') }}", sql)
        self.assertLess(sql.index("products.product_id as product_id"), sql.index("products.handle as handle"))
        self.assertIn("coalesce(product_aggregated.quantity_sold, 0) as total_quantity_sold", sql)
        self.assertIn("coalesce(product_aggregated.product_total_tax, 0) as product_total_tax", sql)

    def test_shopify_daily_shop_mart_uses_calendar_and_daily_rollups(self) -> None:
        sql = synthesize_declared_model_sql(
            "shopify__daily_shop",
            [
                "date_day",
                "shop_id",
                "name",
                "domain",
                "is_deleted",
                "currency",
                "enabled_presentment_currencies",
                "iana_timezone",
                "created_at",
                "count_orders",
                "count_line_items",
                "count_customers",
                "count_customer_emails",
                "order_adjusted_total",
                "avg_order_value",
                "refund_total_tax",
                "total_discounts",
                "fixed_amount_discount_amount",
                "source_relation",
            ],
            {"name": "shopify_order_data", "kind": "table", "expr": "main.shopify_order_data", "columns": []},
            [
                {"name": "shopify__calendar", "kind": "ref", "expr": "{{ ref('shopify__calendar') }}", "columns": []},
                {"name": "int_shopify__daily_orders", "kind": "ref", "expr": "{{ ref('int_shopify__daily_orders') }}", "columns": []},
                {
                    "name": "int_shopify__daily_abandoned_checkouts",
                    "kind": "ref",
                    "expr": "{{ ref('int_shopify__daily_abandoned_checkouts') }}",
                    "columns": [],
                },
            ],
        )

        self.assertIn("from calendar\ncross join shop", sql)
        self.assertIn("select * from {{ var('shopify_shop') }}", sql)
        self.assertIn("select * from {{ ref('int_shopify__daily_orders') }}", sql)
        self.assertLess(sql.index("shop.source_relation as source_relation"), sql.index("coalesce(daily_orders.count_orders, 0) as count_orders"))
        self.assertIn("coalesce(daily_orders.refund_total_tax, 0) as refund_total_tax", sql)

    def test_declared_daily_metrics_uses_calendar_entity_spine_and_daily_metrics(self) -> None:
        sql = synthesize_declared_model_sql(
            "pendo__page_daily_metrics",
            [
                "date_day",
                "page_id",
                "page_name",
                "sum_pageviews",
                "count_visitors",
                "count_accounts",
            ],
            {"name": "int_pendo__page_daily_metrics", "kind": "ref", "expr": "{{ ref('int_pendo__page_daily_metrics') }}"},
            [
                {"name": "int_pendo__calendar_spine", "kind": "ref", "expr": "{{ ref('int_pendo__calendar_spine') }}", "columns": ["date_day"]},
                {
                    "name": "int_pendo__page_daily_metrics",
                    "kind": "ref",
                    "expr": "{{ ref('int_pendo__page_daily_metrics') }}",
                    "columns": ["occurred_on", "page_id", "sum_pageviews", "count_visitors", "count_accounts"],
                },
                {
                    "name": "pendo__page",
                    "kind": "ref",
                    "expr": "{{ ref('pendo__page') }}",
                    "columns": ["page_id", "page_name", "created_at", "valid_through", "count_visitors"],
                },
            ],
        )

        self.assertIn("from {{ ref('int_pendo__calendar_spine') }}", sql)
        self.assertIn("from {{ ref('pendo__page') }}", sql)
        self.assertIn("left join daily_metrics", sql)
        self.assertIn("spine.date_day >= cast(entity.created_at as date)", sql)
        self.assertIn("spine.date_day <= coalesce(cast(entity.valid_through as date), current_date)", sql)
        self.assertIn("coalesce(daily_metrics.sum_pageviews, 0) as sum_pageviews", sql)
        self.assertIn("coalesce(daily_metrics.count_visitors, 0) as count_visitors", sql)
        self.assertNotIn("entity_spine.count_visitors as count_visitors", sql)

    def test_declared_daily_metrics_can_build_latest_entity_from_history_source(self) -> None:
        sql = synthesize_declared_model_sql(
            "pendo__guide_daily_metrics",
            ["date_day", "guide_id", "guide_name", "count_guide_events"],
            {"name": "int_pendo__guide_daily_metrics", "kind": "ref", "expr": "{{ ref('int_pendo__guide_daily_metrics') }}"},
            [
                {"name": "int_pendo__calendar_spine", "kind": "ref", "expr": "{{ ref('int_pendo__calendar_spine') }}", "columns": ["date_day"]},
                {
                    "name": "int_pendo__guide_daily_metrics",
                    "kind": "ref",
                    "expr": "{{ ref('int_pendo__guide_daily_metrics') }}",
                    "columns": ["occurred_on", "guide_id", "count_guide_events"],
                },
                {
                    "name": "guide_history",
                    "kind": "source",
                    "expr": "{{ source('pendo', 'guide_history') }}",
                    "columns": ["id", "name", "created_at", "last_updated_at", "_fivetran_synced"],
                },
            ],
        )

        self.assertIn("row_number() over (partition by id order by try_cast(last_updated_at as timestamp)", sql)
        self.assertIn("entity.id as guide_id", sql)
        self.assertIn("entity.name as guide_name", sql)
        self.assertIn("daily_metrics.count_guide_events as count_guide_events", sql)
        self.assertNotIn("valid_through", sql)

    def test_declared_group_entity_fact_uses_entity_grain_and_contact_rollups(self) -> None:
        sql = synthesize_declared_model_sql(
            "fct_group_activity",
            [
                "gaggle_id",
                "gaggle_name",
                "created_at",
                "first_event",
                "most_recent_event",
                "number_of_events",
                "number_of_users",
                "first_order",
                "most_recent_order",
                "number_of_orders",
                "corporate_email",
                "first_event_corporate",
                "most_recent_event_corporate",
                "number_of_events_corporate",
                "number_of_users_corporate",
                "first_order_corporate",
                "most_recent_order_corporate",
                "number_of_orders_corporate",
            ],
            {"name": "dim_contacts", "kind": "ref", "expr": "{{ ref('dim_contacts') }}"},
            [
                {
                    "name": "dim_contacts",
                    "kind": "ref",
                    "expr": "{{ ref('dim_contacts') }}",
                    "columns": ["user_id", "gaggle_id", "corporate_email"],
                },
                {
                    "name": "stg_gaggles",
                    "kind": "ref",
                    "expr": "{{ ref('stg_gaggles') }}",
                    "columns": ["gaggle_id", "gaggle_name", "created_at"],
                },
                {
                    "name": "merged_company_domain",
                    "kind": "source",
                    "expr": "{{ source('main', 'merged_company_domain') }}",
                    "columns": [],
                },
            ],
        )

        self.assertIn("from {{ ref('stg_gaggles') }} as g", sql)
        self.assertIn("left join contacts as c on g.gaggle_id = c.gaggle_id", sql)
        self.assertIn("coalesce(m.new_domain, c.corporate_email) as __corporate_email", sql)
        self.assertIn("domain_counts as", sql)
        self.assertIn(
            "case when chosen_domain.corporate_email is null then null else count(distinct case when c.__corporate_email = chosen_domain.corporate_email then c.user_id end) end as number_of_users_corporate",
            sql,
        )
        self.assertIn("group by g.gaggle_id, g.gaggle_name, g.created_at, chosen_domain.corporate_email", sql)

    def test_declared_corporate_rollup_groups_fact_by_corporate_email(self) -> None:
        sql = synthesize_declared_model_sql(
            "rpt_corporate_accounts",
            [
                "corporate_email",
                "number_of_gaggles",
                "number_of_users_corporate",
                "number_of_events_corporate",
                "first_event_corporate",
                "most_recent_event_corporate",
                "number_of_orders_corporate",
                "first_order_corporate",
                "most_recent_order_corporate",
                "number_of_users_associated",
                "number_of_events_associated",
                "first_event_associated",
                "most_recent_event_associated",
                "number_of_orders_associated",
                "first_order_associated",
                "most_recent_order_associated",
                "first_user_id",
                "most_active_user_id",
                "most_orders_user_id",
            ],
            {"name": "fct_group_activity", "kind": "ref", "expr": "{{ ref('fct_group_activity') }}"},
            [
                {
                    "name": "fct_group_activity",
                    "kind": "ref",
                    "expr": "{{ ref('fct_group_activity') }}",
                    "columns": [
                        "gaggle_id",
                        "gaggle_name",
                        "created_at",
                        "first_event",
                        "most_recent_event",
                        "number_of_events",
                        "number_of_users",
                        "first_order",
                        "most_recent_order",
                        "number_of_orders",
                        "corporate_email",
                        "first_event_corporate",
                        "most_recent_event_corporate",
                        "number_of_events_corporate",
                        "number_of_users_corporate",
                        "first_order_corporate",
                        "most_recent_order_corporate",
                        "number_of_orders_corporate",
                    ],
                },
                {
                    "name": "dim_contacts",
                    "kind": "ref",
                    "expr": "{{ ref('dim_contacts') }}",
                    "columns": ["user_id", "gaggle_id", "corporate_email", "created_at", "number_of_events", "number_of_orders"],
                },
            ],
        )

        self.assertIn("from {{ ref('fct_group_activity') }} as f", sql)
        self.assertIn("from {{ ref('dim_contacts') }}", sql)
        self.assertIn("count(distinct f.gaggle_id) as number_of_gaggles", sql)
        self.assertIn("sum(f.number_of_users) as number_of_users_associated", sql)
        self.assertIn("first(user_id order by created_at, user_id) as first_user_id", sql)
        self.assertIn(
            "first(user_id order by coalesce(number_of_orders, 0) desc, most_recent_order, user_id) as most_orders_user_id",
            sql,
        )
        self.assertIn("group by f.corporate_email, key_users.first_user_id, key_users.most_active_user_id, key_users.most_orders_user_id", sql)

    def test_pin_spider2_calendar_current_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models" / "utils"
            models.mkdir(parents=True)
            calendar = models / "shopify__calendar.sql"
            calendar.write_text(
                "{% set start_date = var('shopify__calendar_start_date', '2019-01-01') %}\n"
                "{{ dbt_utils.date_spine(\n"
                "    datepart=\"day\",\n"
                "    start_date=\"cast('\" ~ start_date ~ \"' as date)\",\n"
                "    end_date=\"current_date\"\n"
                "    )\n"
                "}}\n",
                encoding="utf-8",
            )

            edits = pin_spider2_calendar_current_date(root, "2024-09-08")
            text = calendar.read_text(encoding="utf-8")

            self.assertEqual(len(edits), 1)
            self.assertIn("ecsql_calendar_end_date", text)
            self.assertIn("SPIDER2_DBT_CURRENT_DATE", text)
            self.assertIn("2024-09-08", text)
            self.assertIn("end_date=\"cast('\" ~ ecsql_calendar_end_date ~ \"' as date)\"", text)

    def test_pin_spider2_calendar_current_date_replaces_dateadd_current_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models" / "intermediate"
            models.mkdir(parents=True)
            calendar = models / "int_pendo__calendar_spine.sql"
            calendar.write_text(
                "{{ dbt_utils.date_spine(\n"
                "    datepart = \"day\",\n"
                "    start_date = \"cast('2020-01-01' as date)\",\n"
                "    end_date = dbt.dateadd(\"week\", 1, \"current_date\")\n"
                ") }}\n",
                encoding="utf-8",
            )

            edits = pin_spider2_calendar_current_date(root, "2024-09-08")
            text = calendar.read_text(encoding="utf-8")

            self.assertEqual(len(edits), 1)
            self.assertIn("SPIDER2_DBT_CURRENT_DATE", text)
            self.assertIn(
                'end_date = "(cast(\'" ~ env_var(\'SPIDER2_DBT_CURRENT_DATE\', \'2024-09-08\') ~ "\' as date) + 1)"',
                text,
            )
            self.assertNotIn('dbt.dateadd("week", 1, "current_date")', text)

    def test_pin_spider2_calendar_current_date_replaces_date_spine_runtime_current_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models" / "intermediate"
            models.mkdir(parents=True)
            spine = models / "int_quickbooks__general_ledger_date_spine.sql"
            spine.write_text(
                "{% set first_date_query %}\n"
                "select cast({{ dbt.dateadd(\"month\", -1, \"current_date\") }} as date) as min_date\n"
                "{% endset %}\n"
                "{% set last_date_query %}\n"
                "select coalesce(max(cast(transaction_date as date)), cast(current_date as date)) as max_date\n"
                "{% endset %}\n"
                "{% set current_date_query %}\n"
                "select current_date\n"
                "{% endset %}\n"
                "{{ dbt_utils.date_spine(datepart=\"month\", start_date=first_date_adjust, end_date=dbt.dateadd(\"month\", 1, last_date_adjust)) }}\n",
                encoding="utf-8",
            )

            edits = pin_spider2_calendar_current_date(root, "2024-09-08")
            text = spine.read_text(encoding="utf-8")

            self.assertEqual(len(edits), 1)
            self.assertIn("SPIDER2_DBT_CURRENT_DATE", text)
            self.assertNotIn("select current_date", text.lower())
            self.assertNotIn("cast(current_date as date)", text.lower())
            self.assertNotIn('dbt.dateadd("month", -1, "current_date")', text)
            self.assertIn("interval '1 month'", text)

    def test_pin_spider2_calendar_current_date_replaces_runtime_timestamp_macros(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models" / "intermediate"
            models.mkdir(parents=True)
            model = models / "int_zuora__account_enriched.sql"
            model.write_text(
                "{{ dbt.datediff(\"account.created_date\", dbt.current_timestamp_backcompat(), \"day\") }} as account_age_days,\n"
                "{{ dbt.date_trunc('day', dbt.current_timestamp()) }} as current_day,\n"
                "{% set calc_last_date = dbt_utils.current_timestamp() %}\n",
                encoding="utf-8",
            )

            edits = pin_spider2_calendar_current_date(root, "2024-09-08")
            text = model.read_text(encoding="utf-8")

            self.assertEqual(len(edits), 1)
            self.assertNotIn("current_timestamp_backcompat()", text)
            self.assertNotIn("dbt.current_timestamp()", text)
            self.assertNotIn("dbt_utils.current_timestamp()", text)
            self.assertIn("SPIDER2_DBT_CURRENT_DATE", text)
            self.assertIn("00:00:00' as timestamp", text)

    def test_failure_history_keeps_all_missing_condition_tables(self) -> None:
        summary = failure_summary(
            {"ok": True},
            None,
            None,
            [
                {
                    "table": "shopify__products",
                    "match": False,
                    "pred_ok": True,
                    "gold_ok": True,
                    "pred_error": "",
                    "gold_error": "",
                },
                {
                    "table": "shopify__daily_shop",
                    "match": False,
                    "pred_ok": False,
                    "gold_ok": True,
                    "pred_error": "Catalog Error: Table with name shopify__daily_shop does not exist",
                    "gold_error": "",
                },
            ],
        )

        self.assertIn("shopify__products", summary)
        self.assertIn("shopify__daily_shop", summary)
        self.assertIn("shopify__daily_shop", focus_model_names_from_history([summary]))

    def test_round_rank_prefers_more_predicted_condition_tables(self) -> None:
        earlier = round_quality_rank(
            True,
            False,
            [{"match": False, "pred_ok": False}, {"match": False, "pred_ok": False}],
        )
        later = round_quality_rank(
            True,
            False,
            [{"match": False, "pred_ok": True}, {"match": False, "pred_ok": False}],
        )

        self.assertGreater(later, earlier)

    def test_best_source_identifier_prefers_specific_long_match(self) -> None:
        available = [("main", "shop"), ("main", "order_line"), ("main", "order_table")]

        self.assertEqual(best_source_identifier("shopify__order_lines", available), ("main", "order_line"))
        self.assertEqual(best_source_identifier("shopify__orders", available), ("main", "order_table"))

    def test_starter_proxy_skips_refs_provided_by_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            (root / "packages.yml").write_text(
                "packages:\n- package: fivetran/shopify\n  version: [\">=0.11.0\", \"<0.14.0\"]\n",
                encoding="utf-8",
            )
            (models / "report.sql").write_text("select * from {{ ref('shopify__orders') }}\n", encoding="utf-8")
            db = root / "starter.duckdb"
            with duckdb.connect(str(db)) as conn:
                conn.execute("create table shop(id integer)")
                conn.execute("create table order_table(id integer)")

            edits = apply_starter_ref_table_proxies(root, db)

            self.assertEqual(edits, [])
            self.assertFalse((models / "shopify__orders.sql").exists())

    def test_repairs_duckdb_variadic_json_extract_path_text(self) -> None:
        sql = "select json_extract_path_text(total_shipping_price_set,'shop_money','amount') as shipping"

        repaired = repair_duckdb_json_extract_path_text(sql)

        self.assertIn("json_extract_string(total_shipping_price_set, '$.shop_money.amount')", repaired)
        self.assertNotIn("json_extract_path_text", repaired)

    def test_repairs_fivetran_json_parse_jinja_call(self) -> None:
        sql = '{{ fivetran_utils.json_parse("receipt",["charges","data",0,"balance_transaction","exchange_rate"]) }}'

        repaired = repair_fivetran_json_parse_calls(sql)

        self.assertEqual(
            repaired,
            "json_extract_string(receipt, '$.charges.data[0].balance_transaction.exchange_rate')",
        )

    def test_repairs_duckdb_mixed_lower_coalesce(self) -> None:
        sql = "select lower(coalesce(type, value_type)) as value_type from fields"

        repaired = repair_duckdb_mixed_coalesce_strings(sql)

        self.assertIn("lower(coalesce(cast(type as varchar), cast(value_type as varchar)))", repaired)

    def test_repairs_missing_unqualified_column_from_candidate_bindings(self) -> None:
        sql = "select * from shop where not coalesce(is_deleted, false)"
        error = (
            'Binder Error: Referenced column "is_deleted" not found in FROM clause!\n'
            'Candidate bindings: "id", "_fivetran_deleted", "setup_required"'
        )

        repaired = repair_duckdb_missing_unqualified_columns(sql, error)

        self.assertIn('coalesce(_fivetran_deleted, false)', repaired)
        self.assertNotIn("coalesce(is_deleted", repaired)

    def test_missing_unqualified_column_repair_preserves_datepart_literals(self) -> None:
        sql = (
            '{{ fivetran_utils.timestamp_diff(first_date="sent_at", '
            'second_date="opened_at", datepart="second") }}'
        )
        error = 'Binder Error: Referenced column "second" not found in FROM clause!'

        repaired = repair_duckdb_missing_unqualified_columns(sql, error)

        self.assertEqual(repaired, sql)

    def test_reserved_staging_macro_alias_source_is_quoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            macros = root / "dbt_packages" / "shopify_source" / "macros"
            macros.mkdir(parents=True)
            macro = macros / "get_transaction_columns.sql"
            macro.write_text(
                '{% macro get_transaction_columns() %}\n'
                '{% set columns = [\n'
                '    {"name": "authorization", "datatype": dbt.type_string(), "alias": "authorization_code"}\n'
                '] %}\n'
                "{{ return(columns) }}\n"
                "{% endmacro %}\n",
                encoding="utf-8",
            )
            error = 'Parser Error: syntax error at or near "as"\nLINE 295:  as authorization_code'

            edits = apply_reserved_staging_macro_quotes(root, error)
            text = macro.read_text(encoding="utf-8")

            self.assertEqual(len(edits), 1)
            self.assertIn('"quote": True', text)

    def test_synthesizes_coalesced_fact_metrics_from_multiple_daily_sources(self) -> None:
        columns = [
            "date_day",
            "email",
            "campaign_id",
            "variation_id",
            "shopify_total_orders",
            "klaviyo_first_event_at",
            "klaviyo_sum_revenue_placed_order",
        ]
        candidates = [
            {
                "name": "int__daily_shopify_customer_orders",
                "expr": "{{ ref('int__daily_shopify_customer_orders') }}",
                "columns": ["date_day", "email", "last_touch_campaign_id", "variation_id", "total_orders"],
            },
            {
                "name": "int__daily_klaviyo_user_metrics",
                "expr": "{{ ref('int__daily_klaviyo_user_metrics') }}",
                "columns": ["date_day", "email", "last_touch_campaign_id", "variation_id", "first_event_at", "sum_revenue_placed_order"],
            },
            {
                "name": "stg_klaviyo__event",
                "expr": "{{ ref('stg_klaviyo__event') }}",
                "columns": ["occurred_on", "campaign_id", "variation_id", "campaign_name"],
            },
        ]

        sql = synthesize_coalesced_fact_metrics_sql("daily_customer_metrics", columns, candidates)

        self.assertIn("full outer join", sql)
        self.assertIn("from {{ ref('int__daily_klaviyo_user_metrics') }}", sql)
        self.assertNotIn("from {{ ref('stg_klaviyo__event') }}", sql)
        self.assertIn("coalesce(s0.date_day, s1.date_day) as date_day", sql)
        self.assertIn("total_orders as shopify_total_orders", sql)
        self.assertIn("first_event_at as klaviyo_first_event_at", sql)
        self.assertIn("sum_revenue_placed_order as klaviyo_sum_revenue_placed_order", sql)
        self.assertNotIn("coalesce(s0.date_day)", sql)

    def test_synthesizes_dynamic_prefixed_daily_metrics_from_upstream(self) -> None:
        columns = [
            "date_day",
            "email",
            "shopify_source_relation",
            "klaviyo_source_relation",
            "shopify_total_orders",
            "klaviyo_first_event_at",
        ]
        candidates = [
            {
                "name": "int__daily_shopify_customer_orders",
                "expr": "{{ ref('int__daily_shopify_customer_orders') }}",
                "columns": ["date_day", "email", "source_relation", "total_orders"],
            },
            {
                "name": "int__daily_klaviyo_user_metrics",
                "expr": "{{ ref('int__daily_klaviyo_user_metrics') }}",
                "columns": [
                    "date_day",
                    "email",
                    "source_relation",
                    "first_event_at",
                    "sum_revenue_refunded_order",
                    "count_clicked_email",
                ],
            },
        ]

        sql = synthesize_coalesced_fact_metrics_sql("daily_customer_metrics", columns, candidates)

        self.assertIn("sum_revenue_refunded_order as klaviyo_sum_revenue_refunded_order", sql)
        self.assertIn("count_clicked_email as klaviyo_count_clicked_email", sql)


if __name__ == "__main__":
    unittest.main()
