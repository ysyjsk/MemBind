import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graphiti_native import parse_datetime  # noqa: E402


class DateTimeParserTests(TestCase):
    def test_parses_longmemeval_timestamp_as_utc(self):
        parsed = parse_datetime("2023/05/02 (Tue) 08:58")

        self.assertEqual(parsed, datetime(2023, 5, 2, 8, 58, tzinfo=timezone.utc))
        self.assertIs(parsed.tzinfo, timezone.utc)

    def test_preserves_iso_support_and_normalizes_offsets_to_utc(self):
        self.assertEqual(
            parse_datetime("2023-05-02T08:58:00Z"),
            datetime(2023, 5, 2, 8, 58, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_datetime("2023-05-02T16:58:00+08:00"),
            datetime(2023, 5, 2, 8, 58, tzinfo=timezone.utc),
        )

    def test_rejects_mismatched_weekday(self):
        with self.assertRaisesRegex(ValueError, "weekday"):
            parse_datetime("2023/05/02 (Mon) 08:58")

    def test_rejects_invalid_or_trailing_content(self):
        for value in (
            "2023/02/30 (Thu) 08:58",
            "2023/05/02 (Tue) 08:58 trailing",
            "not-a-date",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_datetime(value)
