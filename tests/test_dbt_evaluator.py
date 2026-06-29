import os
import tempfile
import unittest
from pathlib import Path

import duckdb

import scripts.run_spider2_dbt_experiment as dbt_exp
from scripts.run_spider2_dbt_experiment import (
    compare_condition_table,
    dbt_package_cache_key,
    existing_dbt_packages,
    expected_dbt_package_dirs,
    restore_dbt_packages_from_cache,
    row_sets_match,
    run_dbt_deps,
    save_dbt_packages_to_cache,
    summarize,
)


class DbtEvaluatorTests(unittest.TestCase):
    def test_run_dbt_deps_skips_when_packages_are_already_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "packages.yml").write_text("packages:\n  - package: dbt-labs/dbt_utils\n", encoding="utf-8")
            package = root / "dbt_packages" / "dbt_utils"
            package.mkdir(parents=True)
            (package / "dbt_project.yml").write_text("name: dbt_utils\n", encoding="utf-8")

            self.assertTrue(existing_dbt_packages(root))
            result = run_dbt_deps(root, ["definitely-not-a-real-dbt"], timeout=1, skip=False)

            self.assertTrue(result["ok"])
            self.assertTrue(result["skipped"])
            self.assertEqual(result["command"], [])

    def test_existing_dbt_packages_rejects_incomplete_package_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dbt_packages" / "dbt_external_tables").mkdir(parents=True)

            self.assertFalse(existing_dbt_packages(root))

    def test_existing_dbt_packages_requires_declared_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "packages.yml").write_text(
                "packages:\n"
                "  - package: dbt-labs/dbt_utils\n"
                "  - package: tnightengale/dbt_activity_schema\n",
                encoding="utf-8",
            )
            package = root / "dbt_packages" / "dbt_utils"
            package.mkdir(parents=True)
            (package / "dbt_project.yml").write_text("name: dbt_utils\n", encoding="utf-8")

            self.assertEqual(expected_dbt_package_dirs(root), {"dbt_utils", "dbt_activity_schema"})
            self.assertFalse(existing_dbt_packages(root))

    def test_run_dbt_deps_does_not_skip_when_declared_package_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            cache = Path(tmp) / "cache"
            root.mkdir()
            (root / "packages.yml").write_text(
                "packages:\n"
                "  - package: dbt-labs/dbt_utils\n"
                "  - package: tnightengale/dbt_activity_schema\n",
                encoding="utf-8",
            )
            package = root / "dbt_packages" / "dbt_utils"
            package.mkdir(parents=True)
            (package / "dbt_project.yml").write_text("name: dbt_utils\n", encoding="utf-8")
            old_cache = os.environ.get("SPIDER2_DBT_PACKAGE_CACHE")
            os.environ["SPIDER2_DBT_PACKAGE_CACHE"] = str(cache)
            try:
                result = run_dbt_deps(root, ["definitely-not-a-real-dbt"], timeout=1, skip=False)
            finally:
                if old_cache is None:
                    os.environ.pop("SPIDER2_DBT_PACKAGE_CACHE", None)
                else:
                    os.environ["SPIDER2_DBT_PACKAGE_CACHE"] = old_cache

            self.assertFalse(result["ok"])
            self.assertFalse(result["skipped"])
            self.assertNotEqual(result["command"], [])

    def test_dbt_package_cache_restores_complete_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            cache = Path(tmp) / "cache"
            root.mkdir()
            (root / "packages.yml").write_text("packages:\n  - package: dbt-labs/dbt_utils\n", encoding="utf-8")
            package = root / "dbt_packages" / "dbt_utils"
            package.mkdir(parents=True)
            (package / "dbt_project.yml").write_text("name: dbt_utils\n", encoding="utf-8")
            old_cache = os.environ.get("SPIDER2_DBT_PACKAGE_CACHE")
            os.environ["SPIDER2_DBT_PACKAGE_CACHE"] = str(cache)
            try:
                save_dbt_packages_to_cache(root)
                key = dbt_package_cache_key(root)
                self.assertTrue((cache / key / "dbt_utils" / "dbt_project.yml").exists())

                for child in sorted((root / "dbt_packages").rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                (root / "dbt_packages").rmdir()

                self.assertTrue(restore_dbt_packages_from_cache(root))
                self.assertTrue((root / "dbt_packages" / "dbt_utils" / "dbt_project.yml").exists())
            finally:
                if old_cache is None:
                    os.environ.pop("SPIDER2_DBT_PACKAGE_CACHE", None)
                else:
                    os.environ["SPIDER2_DBT_PACKAGE_CACHE"] = old_cache

    def test_compare_condition_table_allows_tiny_float_tail_difference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            with duckdb.connect(str(pred)) as conn:
                conn.execute("create table result(ticker varchar, shares bigint, value double)")
                conn.execute("insert into result values ('GME', 888237000, 2037689697749.9998)")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table result(ticker varchar, shares bigint, value double)")
                conn.execute("insert into result values ('GME', 888237000, 2037689697749.999)")

            result = compare_condition_table(pred, gold, "result", [], True, True)

            self.assertTrue(result["match"])
            self.assertFalse(result["exact_rows_match"])
            self.assertTrue(result["tolerant_rows_match"])

    def test_compare_condition_table_rejects_non_numeric_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            with duckdb.connect(str(pred)) as conn:
                conn.execute("create table result(ticker varchar, shares bigint)")
                conn.execute("insert into result values ('GME', 1)")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table result(ticker varchar, shares bigint)")
                conn.execute("insert into result values ('AMC', 1)")

            result = compare_condition_table(pred, gold, "result", [], True, True)

            self.assertFalse(result["match"])
            self.assertFalse(result["tolerant_rows_match"])

    def test_compare_condition_table_skips_tolerant_rows_when_columns_differ(self) -> None:
        original = dbt_exp.row_sets_match
        dbt_exp.row_sets_match = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not be called"))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                pred = Path(tmp) / "pred.duckdb"
                gold = Path(tmp) / "gold.duckdb"
                with duckdb.connect(str(pred)) as conn:
                    conn.execute("create table result(id int, extra varchar)")
                    conn.execute("insert into result values (1, 'a')")
                with duckdb.connect(str(gold)) as conn:
                    conn.execute("create table result(id int)")
                    conn.execute("insert into result values (1)")

                result = compare_condition_table(pred, gold, "result", [], True, True)
        finally:
            dbt_exp.row_sets_match = original

        self.assertFalse(result["match"])
        self.assertFalse(result["tolerant_rows_match"])
        self.assertEqual(result["pred_columns"], ["ID", "EXTRA"])
        self.assertEqual(result["gold_columns"], ["ID"])

    def test_row_sets_match_bounds_large_tolerant_remainder(self) -> None:
        original_limit = dbt_exp.MAX_TOLERANT_ROW_COMPARE_ROWS
        dbt_exp.MAX_TOLERANT_ROW_COMPARE_ROWS = 2
        try:
            self.assertTrue(row_sets_match([(1, "a"), (2, "b")], [(1, "a"), (2, "b")], True, ["ID", "LABEL"]))
            self.assertTrue(row_sets_match([(1, 6.32)], [(1, 6.33)], True, ["ID", "RATING"]))
            self.assertFalse(
                row_sets_match(
                    [(idx, f"left-{idx}") for idx in range(5)],
                    [(idx, f"right-{idx}") for idx in range(5)],
                    True,
                    ["ID", "LABEL"],
                )
            )
        finally:
            dbt_exp.MAX_TOLERANT_ROW_COMPARE_ROWS = original_limit

    def test_compare_condition_table_uses_fingerprint_for_large_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            create_sql = "create table result as select i as id, 'v' || cast(i as varchar) as label from range(21001) t(i)"
            with duckdb.connect(str(pred)) as conn:
                conn.execute(create_sql)
            with duckdb.connect(str(gold)) as conn:
                conn.execute(create_sql)

            result = compare_condition_table(pred, gold, "result", [], True, True)

            self.assertTrue(result["match"])
            self.assertTrue(result["large_result"])
            self.assertEqual(result["pred_row_count"], 21001)
            self.assertEqual(result["pred_fingerprint"], result["gold_fingerprint"])

    def test_large_result_fingerprint_normalizes_float_tail_differences(self) -> None:
        original_limit = dbt_exp.MAX_SIGNATURE_FETCH_ROWS
        dbt_exp.MAX_SIGNATURE_FETCH_ROWS = 2
        try:
            with tempfile.TemporaryDirectory() as tmp:
                pred = Path(tmp) / "pred.duckdb"
                gold = Path(tmp) / "gold.duckdb"
                with duckdb.connect(str(pred)) as conn:
                    conn.execute("create table result(post_id varchar, upvote_ratio double)")
                    conn.executemany("insert into result values (?, ?)", [("p1", 0.53), ("p2", 0.88), ("p3", 0.91)])
                with duckdb.connect(str(gold)) as conn:
                    conn.execute("create table result(post_id varchar, upvote_ratio float)")
                    conn.executemany(
                        "insert into result values (?, ?)",
                        [("p1", 0.5299999713897705), ("p2", 0.8799999952316284), ("p3", 0.9100000262260437)],
                    )

                result = compare_condition_table(pred, gold, "result", [], True, True)
        finally:
            dbt_exp.MAX_SIGNATURE_FETCH_ROWS = original_limit

        self.assertTrue(result["match"])
        self.assertTrue(result["large_result"])
        self.assertEqual(result["pred_fingerprint"], result["gold_fingerprint"])

    def test_compare_condition_table_allows_one_cent_rounded_metric_difference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            with duckdb.connect(str(pred)) as conn:
                conn.execute("create table result(actor_id decimal(18,3), rating decimal(6,2))")
                conn.execute("insert into result values (123.000, 6.32)")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table result(actor_id decimal(18,3), rating decimal(6,2))")
                conn.execute("insert into result values (123.000, 6.33)")

            result = compare_condition_table(pred, gold, "result", [], True, True)

            self.assertTrue(result["match"])
            self.assertFalse(result["exact_rows_match"])
            self.assertTrue(result["tolerant_rows_match"])

    def test_compare_condition_table_allows_near_integral_metric_tail_difference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            with duckdb.connect(str(pred)) as conn:
                conn.execute("create table result(ticker varchar, shares bigint, value double)")
                conn.execute("insert into result values ('AMC', 342342000, 563523460500.0)")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table result(ticker varchar, shares bigint, value double)")
                conn.execute("insert into result values ('AMC', 342342000, 563523460500.0002)")

            result = compare_condition_table(pred, gold, "result", [], True, True)

            self.assertTrue(result["match"])
            self.assertFalse(result["exact_rows_match"])
            self.assertTrue(result["tolerant_rows_match"])

    def test_compare_condition_table_treats_exact_rows_as_tolerant_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            with duckdb.connect(str(pred)) as conn:
                conn.execute("create table result(provider_id varchar, code varchar)")
                conn.execute("insert into result values ('100', 'A'), ('100', 'A')")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table result(provider_id varchar, code varchar)")
                conn.execute("insert into result values ('100', 'A'), ('100', 'A')")

            result = compare_condition_table(pred, gold, "result", [], True, True)

            self.assertTrue(result["match"])
            self.assertTrue(result["exact_rows_match"])
            self.assertTrue(result["tolerant_rows_match"])

    def test_compare_condition_table_normalizes_timestamp_string_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            with duckdb.connect(str(pred)) as conn:
                conn.execute("create table result(created_at timestamp, name varchar)")
                conn.execute("insert into result values (timestamp '2016-01-15 16:00:35', 'A')")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table result(created_at varchar, name varchar)")
                conn.execute("insert into result values ('2016-01-15 16:00:35.000000', 'A')")

            result = compare_condition_table(pred, gold, "result", [], True, True)

            self.assertTrue(result["match"])
            self.assertTrue(result["exact_rows_match"])
            self.assertTrue(result["tolerant_rows_match"])

    def test_compare_condition_table_maps_gold_indexes_to_predicted_column_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            with duckdb.connect(str(pred)) as conn:
                conn.execute("create table result(extra varchar, amount integer, transaction_id varchar)")
                conn.execute("insert into result values ('ignored', 42, 'T1')")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table result(transaction_id varchar, amount integer)")
                conn.execute("insert into result values ('T1', 42)")

            result = compare_condition_table(pred, gold, "result", [0, 1], True, True)

            self.assertTrue(result["match"])
            self.assertEqual(result["pred_columns"], ["TRANSACTION_ID", "AMOUNT"])
            self.assertEqual(result["gold_columns"], ["TRANSACTION_ID", "AMOUNT"])

    def test_compare_condition_table_keeps_integral_numeric_ids_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            with duckdb.connect(str(pred)) as conn:
                conn.execute("create table result(actor_id decimal(18,3), rating decimal(6,2))")
                conn.execute("insert into result values (123.000, 6.32)")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table result(actor_id decimal(18,3), rating decimal(6,2))")
                conn.execute("insert into result values (123.010, 6.32)")

            result = compare_condition_table(pred, gold, "result", [], True, True)

            self.assertFalse(result["match"])
            self.assertFalse(result["tolerant_rows_match"])

    def test_compare_condition_table_allows_ranked_boundary_tie_swap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            ddl = "create table leaderboard(rank integer, driver_full_name varchar, fastest_laps integer)"
            with duckdb.connect(str(pred)) as conn:
                conn.execute(ddl)
                conn.executemany(
                    "insert into leaderboard values (?, ?, ?)",
                    [(1, "A", 10), (2, "B", 5), (2, "C", 5)],
                )
            with duckdb.connect(str(gold)) as conn:
                conn.execute(ddl)
                conn.executemany(
                    "insert into leaderboard values (?, ?, ?)",
                    [(1, "A", 10), (2, "B", 5), (2, "D", 5)],
                )

            result = compare_condition_table(pred, gold, "leaderboard", [], True, True)

            self.assertTrue(result["match"])
            self.assertFalse(result["exact_rows_match"])
            self.assertTrue(result["tolerant_rows_match"])

    def test_compare_condition_table_rejects_non_boundary_ranked_tie_swap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            ddl = "create table leaderboard(rank integer, driver_full_name varchar, fastest_laps integer)"
            with duckdb.connect(str(pred)) as conn:
                conn.execute(ddl)
                conn.executemany(
                    "insert into leaderboard values (?, ?, ?)",
                    [(1, "A", 10), (2, "C", 5), (3, "Z", 1)],
                )
            with duckdb.connect(str(gold)) as conn:
                conn.execute(ddl)
                conn.executemany(
                    "insert into leaderboard values (?, ?, ?)",
                    [(1, "A", 10), (2, "D", 5), (3, "Z", 1)],
                )

            result = compare_condition_table(pred, gold, "leaderboard", [], True, True)

            self.assertFalse(result["match"])
            self.assertFalse(result["tolerant_rows_match"])

    def test_compare_condition_table_rejects_boundary_ranked_metric_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            ddl = "create table leaderboard(rank integer, driver_full_name varchar, fastest_laps integer)"
            with duckdb.connect(str(pred)) as conn:
                conn.execute(ddl)
                conn.executemany(
                    "insert into leaderboard values (?, ?, ?)",
                    [(1, "A", 10), (2, "B", 5), (2, "C", 6)],
                )
            with duckdb.connect(str(gold)) as conn:
                conn.execute(ddl)
                conn.executemany(
                    "insert into leaderboard values (?, ?, ?)",
                    [(1, "A", 10), (2, "B", 5), (2, "D", 5)],
                )

            result = compare_condition_table(pred, gold, "leaderboard", [], True, True)

            self.assertFalse(result["match"])
            self.assertFalse(result["tolerant_rows_match"])

    def test_compare_condition_table_allows_key_user_metric_tie(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            result_ddl = (
                "create table rpt_corporate_accounts("
                "corporate_email varchar, number_of_gaggles integer, "
                "most_active_user_id integer, most_orders_user_id integer)"
            )
            contacts_ddl = (
                "create table dim_contacts("
                "corporate_email varchar, user_id integer, number_of_events integer, number_of_orders integer)"
            )
            with duckdb.connect(str(pred)) as conn:
                conn.execute(result_ddl)
                conn.execute(contacts_ddl)
                conn.execute("insert into rpt_corporate_accounts values ('acme.com', 2, 101, 202)")
                conn.executemany(
                    "insert into dim_contacts values (?, ?, ?, ?)",
                    [
                        ("acme.com", 101, 10, 1),
                        ("acme.com", 102, 10, 2),
                        ("acme.com", 201, 4, 8),
                        ("acme.com", 202, 3, 8),
                    ],
                )
            with duckdb.connect(str(gold)) as conn:
                conn.execute(result_ddl)
                conn.execute("insert into rpt_corporate_accounts values ('acme.com', 2, 102, 201)")

            result = compare_condition_table(pred, gold, "rpt_corporate_accounts", [], True, True)

            self.assertTrue(result["match"])
            self.assertFalse(result["exact_rows_match"])
            self.assertTrue(result["tolerant_rows_match"])

    def test_compare_condition_table_rejects_key_user_non_max_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            result_ddl = (
                "create table rpt_corporate_accounts("
                "corporate_email varchar, number_of_gaggles integer, "
                "most_active_user_id integer, most_orders_user_id integer)"
            )
            contacts_ddl = (
                "create table dim_contacts("
                "corporate_email varchar, user_id integer, number_of_events integer, number_of_orders integer)"
            )
            with duckdb.connect(str(pred)) as conn:
                conn.execute(result_ddl)
                conn.execute(contacts_ddl)
                conn.execute("insert into rpt_corporate_accounts values ('acme.com', 2, 103, 202)")
                conn.executemany(
                    "insert into dim_contacts values (?, ?, ?, ?)",
                    [
                        ("acme.com", 101, 10, 1),
                        ("acme.com", 102, 10, 2),
                        ("acme.com", 103, 4, 8),
                        ("acme.com", 202, 3, 8),
                    ],
                )
            with duckdb.connect(str(gold)) as conn:
                conn.execute(result_ddl)
                conn.execute("insert into rpt_corporate_accounts values ('acme.com', 2, 102, 202)")

            result = compare_condition_table(pred, gold, "rpt_corporate_accounts", [], True, True)

            self.assertFalse(result["match"])
            self.assertFalse(result["tolerant_rows_match"])

    def test_compare_condition_table_marks_missing_gold_table_as_artifact_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            with duckdb.connect(str(pred)) as conn:
                conn.execute("create table dim_customer(customer_id varchar)")
                conn.execute("insert into dim_customer values ('C001')")
            with duckdb.connect(str(gold)) as conn:
                conn.execute("create table unrelated(id integer)")

            result = compare_condition_table(pred, gold, "dim_customer", [], True, True)

            self.assertFalse(result["match"])
            self.assertTrue(result["pred_ok"])
            self.assertFalse(result["gold_ok"])
            self.assertTrue(result["gold_artifact_issue"])
            self.assertIn("dim_customer", result["gold_error"])

    def test_compare_condition_table_detects_omop_cost_surrogate_key_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.duckdb"
            gold = Path(tmp) / "gold.duckdb"
            ddl = (
                "create table cost("
                "cost_id integer, cost_event_id integer, cost_domain_id varchar, "
                "cost_type_concept_id integer, currency_concept_id integer, "
                "total_charge decimal(18,3), total_cost decimal(18,3), "
                "total_paid decimal(18,3), paid_by_payer decimal(18,3), "
                "paid_by_patient decimal(18,3), payer_plan_period_id integer)"
            )
            with duckdb.connect(str(pred)) as conn:
                conn.execute(ddl)
                conn.executemany(
                    "insert into cost values (?, ?, 'Drug', 32814, 44818668, ?, ?, ?, 0.000, ?, 7)",
                    [(idx, idx + 1000, idx * 1.0, idx * 1.0, idx * 0.5, idx * 1.0) for idx in range(1, 101)],
                )
            with duckdb.connect(str(gold)) as conn:
                conn.execute(ddl)
                conn.executemany(
                    "insert into cost values (?, ?, 'Drug', 32814, 44818668, ?, ?, ?, 0.000, ?, 7007)",
                    [(idx + 500, idx + 2000, idx * 1.0, idx * 1.0, idx * 0.5, idx * 1.0) for idx in range(1, 101)],
                )

            result = compare_condition_table(pred, gold, "cost", list(range(11)), True, True)

            self.assertFalse(result["match"])
            self.assertTrue(result["gold_ok"])
            self.assertTrue(result["gold_artifact_issue"])
            self.assertTrue(result["surrogate_key_artifact"])
            self.assertEqual(result["payload_overlap_rate"], 100.0)
            self.assertIn("surrogate-key artifact", result["gold_error"])

    def test_summarize_reports_raw_and_gold_evaluable_semantic_rates(self) -> None:
        rows = [
            {
                "run_ok": True,
                "semantic_pass": True,
                "gold_evaluable": True,
                "gold_artifact_issue": False,
                "table_results": [{"match": True, "gold_ok": True}],
            },
            {
                "run_ok": True,
                "semantic_pass": False,
                "gold_evaluable": False,
                "gold_artifact_issue": True,
                "table_results": [{"match": False, "gold_ok": False, "gold_artifact_issue": True}],
            },
        ]

        result = summarize(rows)

        self.assertEqual(result["cases"], 2)
        self.assertEqual(result["semantic_pass"], 1)
        self.assertEqual(result["semantic_pass_rate"], 50.0)
        self.assertEqual(result["gold_evaluable"], 1)
        self.assertEqual(result["gold_artifact_issues"], 1)
        self.assertEqual(result["semantic_pass_evaluable"], 1)
        self.assertEqual(result["semantic_pass_rate_evaluable"], 100.0)


if __name__ == "__main__":
    unittest.main()
