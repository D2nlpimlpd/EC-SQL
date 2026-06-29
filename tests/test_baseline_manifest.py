import unittest

from scripts.baseline_manifest import baseline_metadata, manifest_systems


class BaselineManifestTests(unittest.TestCase):
    def test_style_baseline_is_marked_as_prompt_proxy(self) -> None:
        metadata = baseline_metadata("mac_sql_style", "qwen2.5-coder:7b")
        self.assertEqual(metadata["implementation_type"], "prompt_style_proxy")
        self.assertEqual(metadata["official_reproduction"], "False")
        self.assertEqual(metadata["baseline_reference"], "MAC-SQL")
        self.assertIn("does not run the official MAC-SQL", metadata["baseline_note"])

    def test_sqlcoder_model_note_is_attached(self) -> None:
        metadata = baseline_metadata("direct", "sqlcoder:7b")
        self.assertIn("local_model_baseline", metadata["implementation_type"])
        self.assertEqual(metadata["official_reproduction"], "False")
        self.assertIn("SQLCoder", metadata["baseline_reference"])
        self.assertIn("model baseline", metadata["baseline_note"])

    def test_manifest_lists_expected_local_style_systems(self) -> None:
        systems = set(manifest_systems())
        for system in {"din_sql_style", "dail_sql_style", "mac_sql_style", "chess_style"}:
            self.assertIn(system, systems)


if __name__ == "__main__":
    unittest.main()
