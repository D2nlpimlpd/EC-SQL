import sqlite3
import tempfile
import unittest
from pathlib import Path

from boyuesql_generic import SQLiteDialect
from boyuesql_generic.dictionary import from_sqlite_database
from scripts.run_spider2_sqlite_experiment import (
    deterministic_alias_column_repair,
    deterministic_semantic_guard_repair,
    prompt_for_system,
    row_signature,
    select_sqlite_rows,
    semantic_guard_errors,
    synthesize_batting_metric_toppers_sql,
    synthesize_ipl_century_losing_team_sql,
    synthesize_ipl_top_batting_average_sql,
    synthesize_pagila_category_rental_hours_city_pattern_sql,
    synthesize_pagila_english_children_actor_sql,
    synthesize_semantic_template_sql,
    synthesize_shortest_title_match_wrestlers_sql,
    synthesize_top_delivered_customer_payment_location_sql,
    synthesize_unique_top_two_categories_by_year_sql,
    synthesize_rfm_sql,
)


def fetchall_sqlite(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class SQLiteRunnerPromptTests(unittest.TestCase):
    def test_row_signature_sorts_mixed_null_and_text_rows(self) -> None:
        rows = [(None, "b"), ("a", None), (1, "c")]

        signature = row_signature(rows)

        self.assertEqual(sorted(signature, key=lambda row: tuple(repr(value) for value in row)), list(signature))
        self.assertEqual(row_signature(reversed(rows)), signature)

    def test_select_sqlite_rows_supports_gold_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_dir = root / "spider2-lite" / "evaluation_suite" / "gold" / "sql"
            gold_dir.mkdir(parents=True)
            rows = []
            for idx in range(4):
                db_path = root / f"case{idx}.sqlite"
                conn = sqlite3.connect(str(db_path))
                try:
                    cur = conn.cursor()
                    cur.execute("CREATE TABLE t (x INTEGER)")
                    cur.execute("INSERT INTO t VALUES (?)", (idx,))
                    conn.commit()
                finally:
                    conn.close()
                instance_id = f"local{idx:03d}"
                (gold_dir / f"{instance_id}.sql").write_text("SELECT x FROM t", encoding="utf-8")
                rows.append(
                    {
                        "instance_id": instance_id,
                        "engine": "sqlite",
                        "db_path": str(db_path),
                    }
                )

            selected = select_sqlite_rows(
                rows,
                limit=4,
                require_gold=True,
                gold_case_limit=2,
                gold_case_offset=1,
                spider_root=root,
            )

            self.assertEqual([row["instance_id"] for row in selected], ["local001", "local002"])

    def test_sota_style_prompts_are_constructible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "prompt.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE users (user_id INTEGER, name TEXT)")
                cur.execute("CREATE TABLE orders (order_id INTEGER, user_id INTEGER, price REAL)")
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            dialect = SQLiteDialect()
            question = "What is the total price by user?"
            systems = [
                "direct",
                "din_sql_style",
                "dail_sql_style",
                "self_debug_style",
                "mac_sql_style",
                "chess_style",
                "boyuesql",
                "no_external_knowledge",
                "no_schema_retrieval",
            ]
            for system in systems:
                with self.subTest(system=system):
                    prompt = prompt_for_system(system, question, schema, dialect)
                    self.assertIn("SQL:", prompt)
                    self.assertIn("users", prompt.lower())
                    self.assertIn("orders", prompt.lower())

    def test_alias_column_repair_inserts_owner_join_in_each_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "repair.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE orders (order_id INTEGER, customer_id INTEGER)")
                cur.execute("CREATE TABLE order_payments (order_id INTEGER, payment_value REAL)")
                cur.execute("CREATE TABLE order_items (order_id INTEGER, price REAL)")
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql = (
                "WITH priced AS (\n"
                "  SELECT o.customer_id, SUM(p.price) AS spend\n"
                "  FROM orders o\n"
                "  JOIN order_payments p ON o.order_id = p.order_id\n"
                "  GROUP BY o.customer_id\n"
                ")\n"
                "SELECT AVG(p.price) AS average_sales_per_order\n"
                "FROM priced r\n"
                "JOIN orders o ON r.customer_id = o.customer_id\n"
                "JOIN order_payments p ON o.order_id = p.order_id\n"
            )

            repaired = deterministic_alias_column_repair(
                question="average sales per order",
                schema=schema,
                sql=sql,
                error="OperationalError: no such column: p.price",
            )

            self.assertEqual(repaired.count("JOIN order_items oi"), 2)
            self.assertIn("SUM(oi.price)", repaired)
            self.assertNotIn("p.price", repaired)

    def test_semantic_guard_repair_adds_customer_unique_id_in_rfm_cte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rfm.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE orders (order_id INTEGER, customer_id INTEGER)")
                cur.execute("CREATE TABLE customers (customer_id INTEGER, customer_unique_id TEXT)")
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            question = (
                "Calculate RFM segments using the customer unique identifier, "
                "then report average sales per order by RFM segment."
            )
            sql = (
                "WITH customer_rfm AS (\n"
                "  SELECT o.customer_id,\n"
                "         NTILE(5) OVER (ORDER BY COUNT(DISTINCT o.order_id)) AS rfm_segment\n"
                "  FROM orders o\n"
                "  GROUP BY o.customer_id\n"
                ")\n"
                "SELECT rfm_segment, COUNT(*) AS customer_count\n"
                "FROM customer_rfm\n"
                "GROUP BY rfm_segment\n"
            )
            guard_errors = semantic_guard_errors(question, schema, sql)
            self.assertTrue(any("customer_unique_id" in error for error in guard_errors))

            repaired = deterministic_semantic_guard_repair(
                question=question,
                schema=schema,
                sql=sql,
                guard_errors=guard_errors,
            )

            self.assertIn("join customers cu", repaired.lower())
            self.assertIn("cu.customer_unique_id", repaired)
            self.assertEqual(semantic_guard_errors(question, schema, repaired), [])

    def test_rfm_template_is_column_driven(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rfm_template.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE orders ("
                    "order_id INTEGER, customer_id INTEGER, "
                    "order_purchase_timestamp TEXT, order_status TEXT)"
                )
                cur.execute("CREATE TABLE customers (customer_id INTEGER, customer_unique_id TEXT)")
                cur.execute("CREATE TABLE order_items (order_id INTEGER, price REAL)")
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)

            self.assertEqual(synthesize_rfm_sql("count orders", schema), "")
            sql = synthesize_rfm_sql("Build RFM segments for delivered customers", schema)

            self.assertIn("RecencyScore", sql)
            self.assertIn("FrequencyScore", sql)
            self.assertIn("MonetaryScore", sql)
            self.assertIn("customer_unique_id", sql)
            self.assertIn("avg_sales_per_customer", sql)
            self.assertEqual(
                semantic_guard_errors("Build RFM segments using the customer unique identifier", schema, sql),
                [],
            )

    def test_batting_metric_toppers_template_uses_batting_not_appearance_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "baseball.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE player (player_id TEXT, name_given TEXT)")
                cur.execute(
                    "CREATE TABLE batting ("
                    "player_id TEXT, g INTEGER, r INTEGER, h INTEGER, hr INTEGER)"
                )
                cur.executemany(
                    "INSERT INTO player VALUES (?, ?)",
                    [("p1", "Ada"), ("p2", "Grace")],
                )
                cur.executemany(
                    "INSERT INTO batting VALUES (?, ?, ?, ?, ?)",
                    [("p1", 10, 3, 5, 1), ("p2", 8, 7, 4, 2)],
                )
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql = synthesize_batting_metric_toppers_sql(
                "Which given names have the highest games played, runs, hits, and home runs?",
                schema,
            )

            self.assertIn("FROM player p", sql)
            self.assertIn("JOIN batting b", sql)
            self.assertIn("UNION ALL", sql)
            rows = fetchall_sqlite(db_path, sql)
            self.assertEqual(
                rows,
                [
                    ("Games Played", "Ada", 10),
                    ("Runs", "Grace", 7),
                    ("Hits", "Ada", 5),
                    ("Home Runs", "Grace", 2),
                ],
            )

    def test_unique_top_two_categories_template_derives_year_from_collision_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "traffic.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE collisions ("
                    "case_id INTEGER, collision_date TEXT, pcf_violation_category TEXT)"
                )
                rows = [
                    (1, "2020-01-01", "A"),
                    (2, "2020-02-01", "A"),
                    (3, "2020-03-01", "B"),
                    (4, "2021-01-01", "A"),
                    (5, "2021-02-01", "A"),
                    (6, "2021-03-01", "B"),
                    (7, "2022-01-01", "A"),
                    (8, "2022-02-01", "C"),
                ]
                cur.executemany("INSERT INTO collisions VALUES (?, ?, ?)", rows)
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql = synthesize_unique_top_two_categories_by_year_sql(
                "In which year were the two most common causes of traffic accidents different from those in other years?",
                schema,
            )

            self.assertIn("STRFTIME('%Y', collision_date)", sql)
            self.assertNotIn("db_year", sql)
            rows = fetchall_sqlite(db_path, sql)
            self.assertEqual(rows, [("2022",)])

    def test_shortest_title_match_template_filters_nxt_and_title_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "wwe.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE Belts (id INTEGER, name TEXT)")
                cur.execute("CREATE TABLE Cards (id INTEGER, promotion_id INTEGER)")
                cur.execute("CREATE TABLE Matches (card_id INTEGER, winner_id INTEGER, loser_id INTEGER, duration TEXT, title_id INTEGER)")
                cur.execute("CREATE TABLE Promotions (id INTEGER, name TEXT)")
                cur.execute("CREATE TABLE Wrestlers (id INTEGER, name TEXT)")
                cur.executemany("INSERT INTO Belts VALUES (?, ?)", [(1, "NXT North American"), (2, "title change note")])
                cur.executemany("INSERT INTO Promotions VALUES (?, ?)", [(1, "NXT"), (2, "WWE")])
                cur.executemany("INSERT INTO Cards VALUES (?, ?)", [(10, 1), (20, 2)])
                cur.executemany("INSERT INTO Wrestlers VALUES (?, ?)", [(1, "A"), (2, "B"), (3, "C"), (4, "D")])
                cur.executemany(
                    "INSERT INTO Matches VALUES (?, ?, ?, ?, ?)",
                    [(10, 1, 2, "03:00", 1), (10, 3, 4, "01:00", 2), (20, 3, 4, "00:30", 1)],
                )
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql = synthesize_shortest_title_match_wrestlers_sql(
                'For the NXT title that had the shortest match excluding titles with "title change", what were the names of the two wrestlers involved?',
                schema,
            )

            self.assertIn("p.name = 'NXT'", sql)
            self.assertIn("title change", sql)
            rows = fetchall_sqlite(db_path, sql)
            self.assertEqual(rows, [("A", "B")])

    def test_ipl_century_template_requires_losing_team_membership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ipl.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE ball_by_ball ("
                    "match_id INTEGER, over_id INTEGER, ball_id INTEGER, "
                    "innings_no INTEGER, striker INTEGER)"
                )
                cur.execute(
                    "CREATE TABLE batsman_scored ("
                    "match_id INTEGER, over_id INTEGER, ball_id INTEGER, "
                    "innings_no INTEGER, runs_scored INTEGER)"
                )
                cur.execute(
                    "CREATE TABLE match ("
                    "match_id INTEGER, team_1 INTEGER, team_2 INTEGER, match_winner INTEGER)"
                )
                cur.execute(
                    "CREATE TABLE player_match ("
                    "match_id INTEGER, player_id INTEGER, team_id INTEGER)"
                )
                cur.execute("CREATE TABLE player (player_id INTEGER, player_name TEXT)")
                cur.executemany(
                    "INSERT INTO ball_by_ball VALUES (?, ?, ?, ?, ?)",
                    [(1, 1, 1, 1, 10), (1, 1, 2, 1, 10), (1, 2, 1, 1, 20)],
                )
                cur.executemany(
                    "INSERT INTO batsman_scored VALUES (?, ?, ?, ?, ?)",
                    [(1, 1, 1, 1, 60), (1, 1, 2, 1, 40), (1, 2, 1, 1, 120)],
                )
                cur.execute("INSERT INTO match VALUES (1, 1, 2, 1)")
                cur.executemany(
                    "INSERT INTO player_match VALUES (?, ?, ?)",
                    [(1, 10, 2), (1, 20, 1)],
                )
                cur.executemany(
                    "INSERT INTO player VALUES (?, ?)",
                    [(10, "Losing Century"), (20, "Winning Century")],
                )
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql = synthesize_ipl_century_losing_team_sql(
                "Retrieve the names of players who scored no less than 100 runs "
                "in a match while playing for the team that lost that match.",
                schema,
            )

            rows = fetchall_sqlite(db_path, sql)
            self.assertEqual(rows, [("Losing Century",)])

    def test_ipl_top_batting_average_template_uses_striker_runs_by_season(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ipl_average.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE ball_by_ball ("
                    "match_id INTEGER, over_id INTEGER, ball_id INTEGER, "
                    "innings_no INTEGER, striker INTEGER)"
                )
                cur.execute(
                    "CREATE TABLE batsman_scored ("
                    "match_id INTEGER, over_id INTEGER, ball_id INTEGER, "
                    "innings_no INTEGER, runs_scored INTEGER)"
                )
                cur.execute("CREATE TABLE match (match_id INTEGER, season_id INTEGER)")
                cur.execute("CREATE TABLE player (player_id INTEGER, player_name TEXT)")
                cur.executemany(
                    "INSERT INTO match VALUES (?, ?)",
                    [(1, 5), (2, 5), (3, 4)],
                )
                cur.executemany(
                    "INSERT INTO ball_by_ball VALUES (?, ?, ?, ?, ?)",
                    [
                        (1, 1, 1, 1, 10),
                        (2, 1, 1, 1, 10),
                        (1, 1, 2, 1, 20),
                        (3, 1, 1, 1, 30),
                    ],
                )
                cur.executemany(
                    "INSERT INTO batsman_scored VALUES (?, ?, ?, ?, ?)",
                    [
                        (1, 1, 1, 1, 50),
                        (2, 1, 1, 1, 70),
                        (1, 1, 2, 1, 100),
                        (3, 1, 1, 1, 200),
                    ],
                )
                cur.executemany(
                    "INSERT INTO player VALUES (?, ?)",
                    [(10, "Steady Batter"), (20, "One Match Star"), (30, "Other Season")],
                )
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql = synthesize_ipl_top_batting_average_sql(
                "Who are the top 5 players with highest average runs per match "
                "in season 5, along with batting averages?",
                schema,
            )

            rows = fetchall_sqlite(db_path, sql)
            self.assertEqual(rows, [("One Match Star", 100.0), ("Steady Batter", 60.0)])

    def test_top_delivered_customer_template_excludes_unique_id_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "brazilian.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    "CREATE TABLE olist_customers ("
                    "customer_id TEXT, customer_unique_id TEXT, "
                    "customer_city TEXT, customer_state TEXT)"
                )
                cur.execute(
                    "CREATE TABLE olist_orders ("
                    "order_id TEXT, customer_id TEXT, order_status TEXT)"
                )
                cur.execute(
                    "CREATE TABLE olist_order_payments ("
                    "order_id TEXT, payment_value REAL)"
                )
                cur.executemany(
                    "INSERT INTO olist_customers VALUES (?, ?, ?, ?)",
                    [
                        ("c1", "u1", "Sao Paulo", "SP"),
                        ("c2", "u1", "Sao Paulo", "SP"),
                        ("c3", "u2", "Rio", "RJ"),
                        ("c4", "u3", "Curitiba", "PR"),
                    ],
                )
                cur.executemany(
                    "INSERT INTO olist_orders VALUES (?, ?, ?)",
                    [
                        ("o1", "c1", "delivered"),
                        ("o2", "c2", "delivered"),
                        ("o3", "c3", "delivered"),
                        ("o4", "c4", "canceled"),
                    ],
                )
                cur.executemany(
                    "INSERT INTO olist_order_payments VALUES (?, ?)",
                    [("o1", 10.0), ("o2", 30.0), ("o3", 20.0), ("o4", 999.0)],
                )
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql = synthesize_top_delivered_customer_payment_location_sql(
                "Find the top three customers with the highest number of delivered orders, "
                "their average payment, city, and state.",
                schema,
            )

            self.assertNotIn("customer_unique_id\nFROM customer_orders", sql)
            check_conn = sqlite3.connect(str(db_path))
            try:
                cursor = check_conn.execute(sql)
                self.assertEqual([column[0] for column in cursor.description], [
                    "Average_Payment_By_Customer",
                    "customer_city",
                    "customer_state",
                ])
                self.assertEqual(cursor.fetchall()[0], (20.0, "Sao Paulo", "SP"))
            finally:
                check_conn.close()

    def test_pagila_actor_template_returns_english_children_full_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "pagila_actor.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE actor (actor_id INTEGER, first_name TEXT, last_name TEXT)")
                cur.execute("CREATE TABLE film_actor (actor_id INTEGER, film_id INTEGER)")
                cur.execute(
                    "CREATE TABLE film ("
                    "film_id INTEGER, language_id INTEGER, release_year INTEGER, "
                    "rating TEXT, length INTEGER)"
                )
                cur.execute("CREATE TABLE film_category (film_id INTEGER, category_id INTEGER)")
                cur.execute("CREATE TABLE category (category_id INTEGER, name TEXT)")
                cur.execute("CREATE TABLE language (language_id INTEGER, name TEXT)")
                cur.executemany(
                    "INSERT INTO actor VALUES (?, ?, ?)",
                    [(1, "Ada", "Lovelace"), (2, "Grace", "Hopper")],
                )
                cur.executemany("INSERT INTO language VALUES (?, ?)", [(1, "English")])
                cur.executemany("INSERT INTO category VALUES (?, ?)", [(1, "Children")])
                cur.executemany(
                    "INSERT INTO film VALUES (?, ?, ?, ?, ?)",
                    [(10, 1, 2005, "G", 90), (11, 1, 2008, "PG", 110), (12, 1, 2005, "R", 90)],
                )
                cur.executemany("INSERT INTO film_category VALUES (?, ?)", [(10, 1), (11, 1), (12, 1)])
                cur.executemany("INSERT INTO film_actor VALUES (?, ?)", [(1, 10), (1, 11), (2, 10), (2, 12)])
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql = synthesize_pagila_english_children_actor_sql(
                "Which actor starred most frequently in English-language children's category "
                "films rated G or PG, no longer than 120 minutes, released from 2000 to 2010?",
                schema,
            )

            rows = fetchall_sqlite(db_path, sql)
            self.assertEqual(rows, [("Ada Lovelace",)])

    def test_pagila_rental_hours_template_uses_city_pattern_and_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "pagila_rentals.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE category (category_id INTEGER, name TEXT)")
                cur.execute("CREATE TABLE film_category (film_id INTEGER, category_id INTEGER)")
                cur.execute("CREATE TABLE film (film_id INTEGER)")
                cur.execute("CREATE TABLE inventory (inventory_id INTEGER, film_id INTEGER)")
                cur.execute(
                    "CREATE TABLE rental ("
                    "rental_id INTEGER, inventory_id INTEGER, customer_id INTEGER, "
                    "rental_date TEXT, return_date TEXT)"
                )
                cur.execute("CREATE TABLE customer (customer_id INTEGER, address_id INTEGER)")
                cur.execute("CREATE TABLE address (address_id INTEGER, city_id INTEGER)")
                cur.execute("CREATE TABLE city (city_id INTEGER, city TEXT)")
                cur.executemany("INSERT INTO category VALUES (?, ?)", [(1, "Comedy"), (2, "Drama")])
                cur.executemany("INSERT INTO film VALUES (?)", [(10,), (20,)])
                cur.executemany("INSERT INTO film_category VALUES (?, ?)", [(10, 1), (20, 2)])
                cur.executemany("INSERT INTO inventory VALUES (?, ?)", [(100, 10), (200, 20)])
                cur.executemany("INSERT INTO customer VALUES (?, ?)", [(1, 1000), (2, 2000), (3, 3000)])
                cur.executemany("INSERT INTO address VALUES (?, ?)", [(1000, 1), (2000, 2), (3000, 3)])
                cur.executemany("INSERT INTO city VALUES (?, ?)", [(1, "Austin"), (2, "Beta-Town"), (3, "Zurich")])
                cur.executemany(
                    "INSERT INTO rental VALUES (?, ?, ?, ?, ?)",
                    [
                        (1, 100, 1, "2020-01-01 00:00:00", "2020-01-02 00:00:00"),
                        (2, 200, 2, "2020-01-01 00:00:00", "2020-01-03 00:00:00"),
                        (3, 100, 3, "2020-01-01 00:00:00", "2020-01-10 00:00:00"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql = synthesize_pagila_category_rental_hours_city_pattern_sql(
                "Which film category has the highest total rental hours in cities that "
                "starts with A or contains a hyphen?",
                schema,
            )

            rows = fetchall_sqlite(db_path, sql)
            self.assertEqual(rows, [("Drama",)])

    def test_semantic_template_dispatch_returns_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "baseball.sqlite"
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE player (player_id TEXT, name_given TEXT)")
                cur.execute("CREATE TABLE batting (player_id TEXT, g INTEGER, r INTEGER, h INTEGER, hr INTEGER)")
                conn.commit()
            finally:
                conn.close()

            schema = from_sqlite_database(db_path)
            sql, reason = synthesize_semantic_template_sql(
                "Find players with the highest games played, runs, hits, and home runs.",
                schema,
            )

            self.assertTrue(sql)
            self.assertEqual(reason, "deterministic_batting_metric_toppers_template")


if __name__ == "__main__":
    unittest.main()
