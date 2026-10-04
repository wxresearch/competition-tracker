from importer import parse_csv, parse_json


def test_csv_aliases():
    data = b"name,description,url\nContest A,Essay competition,https://instagram.com/p/123/\n"
    rows = parse_csv(data)
    assert rows[0]["import_title"] == "Contest A"
    assert rows[0]["raw_text"] == "Essay competition"
    assert rows[0]["instagram_url"].startswith("https://instagram.com")


def test_normal_url_becomes_official_url():
    data = b"title,url\nContest B,https://example.org/rules\n"
    rows = parse_csv(data)
    assert rows[0]["instagram_url"] == ""
    assert rows[0]["imported_official_url"] == "https://example.org/rules"


def test_json_list():
    data = b'[{"title":"Contest C","caption":"Prize: $500"}]'
    rows = parse_json(data)
    assert rows[0]["import_title"] == "Contest C"
    assert "$500" in rows[0]["raw_text"]
