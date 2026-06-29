import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from boyuesql_service import app


class BoyueSQLServiceTests(unittest.TestCase):
    def test_service_health_schema_and_deterministic_query(self) -> None:
        old_env = dict(os.environ)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "service.sqlite"
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

            os.environ.update(
                {
                    "BOYUESQL_DIALECT": "sqlite",
                    "DB_PATH": str(db_path),
                    "BOYUESQL_ENABLE_SQLITE_TEMPLATES": "1",
                    "MAX_ROWS": "20",
                }
            )
            try:
                client = app.test_client()
                health = client.get("/health").get_json()
                self.assertTrue(health["ok"])
                self.assertEqual(health["dialect"], "sqlite")
                self.assertTrue(health["schema_source_exists"])

                schema = client.get("/api/schema").get_json()
                self.assertTrue(schema["ok"])
                self.assertEqual(schema["table_count"], 2)

                response = client.post(
                    "/api/query",
                    json={
                        "question": (
                            "Which given names have the highest games played, "
                            "runs, hits, and home runs?"
                        ),
                        "execute": True,
                    },
                ).get_json()
                self.assertTrue(response["ok"], response)
                self.assertEqual(response["validation_error"], "")
                self.assertEqual(
                    response["result"]["rows"],
                    [
                        ["Games Played", "Ada", 10],
                        ["Runs", "Grace", 7],
                        ["Hits", "Ada", 5],
                        ["Home Runs", "Grace", 2],
                    ],
                )
            finally:
                os.environ.clear()
                os.environ.update(old_env)


if __name__ == "__main__":
    unittest.main()
