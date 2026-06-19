"""Tests for CSV read/write utilities."""

from pathlib import Path

from websitescorecard.csv_io import read_csv, write_csv


def test_round_trip_preserves_columns(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("name,website\nAcme,https://acme.com\n", encoding="utf-8")

    columns, rows = read_csv(input_path)
    assert columns == ["name", "website"]
    assert rows == [{"name": "Acme", "website": "https://acme.com"}]

    output_path = tmp_path / "output.csv"
    enriched_columns = columns + ["ssl_status", "ssl_error"]
    enriched_rows = [{**rows[0], "ssl_status": "valid", "ssl_error": ""}]
    write_csv(output_path, enriched_columns, enriched_rows)

    out_columns, out_rows = read_csv(output_path)
    assert out_columns == ["name", "website", "ssl_status", "ssl_error"]
    assert out_rows[0]["ssl_status"] == "valid"
