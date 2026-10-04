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


def test_current_meta_label_values_format():
    obj = [
        {
            "timestamp": 1791130455,
            "media": [],
            "label_values": [
                {
                    "label": "URL",
                    "value": "https://www.instagram.com/reel/DeDYyRRIdFI/",
                    "href": "https://www.instagram.com/reel/DeDYyRRIdFI/",
                },
                {
                    "label": "Caption",
                    "value": "Easiest scholarships anyone can win. Voice of Democracy deadline October 31.",
                },
                {"label": "Title", "value": ""},
                {
                    "dict": [
                        {
                            "dict": [
                                {"label": "URL", "value": ""},
                                {"label": "Name", "value": "Ryan Choice"},
                                {"label": "Username", "value": "ryanchoice_"},
                            ],
                            "title": "",
                        }
                    ],
                    "title": "Owner",
                },
                {
                    "dict": [{"dict": [{"label": "Name", "value": "scholarships"}], "title": ""}],
                    "title": "Hashtags",
                },
            ],
            "fbid": "123",
        }
    ]
    import json

    rows = parse_json(json.dumps(obj).encode())
    assert len(rows) == 1
    assert rows[0]["instagram_url"] == "https://www.instagram.com/reel/DeDYyRRIdFI/"
    assert rows[0]["import_title"] == "Ryan Choice"
    assert "Voice of Democracy" in rows[0]["raw_text"]
    assert "@ryanchoice_" in rows[0]["raw_text"]


def test_label_values_does_not_create_owner_or_hashtag_records():
    obj = [
        {
            "label_values": [
                {"label": "URL", "href": "https://www.instagram.com/p/REALPOST/"},
                {"label": "Caption", "value": "An essay competition."},
                {
                    "title": "Owner",
                    "dict": [
                        {
                            "dict": [
                                {"label": "URL", "value": "https://example.com"},
                                {"label": "Username", "value": "owner_account"},
                            ]
                        }
                    ],
                },
                {
                    "title": "Hashtags",
                    "dict": [{"dict": [{"label": "Name", "value": "competition"}]}],
                },
            ]
        }
    ]
    import json

    rows = parse_json(json.dumps(obj).encode())
    assert len(rows) == 1
    assert rows[0]["instagram_url"].endswith("/REALPOST/")


def test_instagram_string_map_data():
    data = b'''{
      "saved_saved_media": [
        {
          "title": "essay competitions",
          "string_map_data": {
            "Saved on": {
              "href": "https://www.instagram.com/p/ABC123/",
              "timestamp": 1719763200
            }
          }
        }
      ]
    }'''
    rows = parse_json(data)
    assert len(rows) == 1
    assert rows[0]["instagram_url"] == "https://www.instagram.com/p/ABC123/"


def test_instagram_string_list_data():
    data = b'''[
      {
        "title": "",
        "string_list_data": [
          {
            "href": "https://www.instagram.com/reel/XYZ789/",
            "value": "example_account",
            "timestamp": 1719763200
          }
        ]
      }
    ]'''
    rows = parse_json(data)
    assert len(rows) == 1
    assert rows[0]["instagram_url"] == "https://www.instagram.com/reel/XYZ789/"


def test_mojibake_repair_in_meta_caption():
    obj = [
        {
            "label_values": [
                {"label": "URL", "href": "https://www.instagram.com/p/ABC/"},
                {"label": "Caption", "value": "It\u00e2\u0080\u0099s free \u00f0\u009f\u008e\u0093"},
            ]
        }
    ]
    import json

    rows = parse_json(json.dumps(obj).encode())
    assert "It’s free" in rows[0]["raw_text"]
    assert "🎓" in rows[0]["raw_text"]


def test_meta_import_preserves_timestamp_and_owner_fields():
    obj = [
        {
            "timestamp": 1790893224,
            "label_values": [
                {"label": "URL", "href": "https://www.instagram.com/p/ABC123/"},
                {"label": "Caption", "value": "Oct 2: Example Competition"},
                {
                    "title": "Owner",
                    "dict": [
                        {
                            "dict": [
                                {"label": "URL", "value": "https://example.com"},
                                {"label": "Name", "value": "Example Owner"},
                                {"label": "Username", "value": "example_owner"},
                            ]
                        }
                    ],
                },
            ],
        }
    ]
    import json

    rows = parse_json(json.dumps(obj).encode())
    assert rows[0]["source_timestamp"] == 1790893224
    assert rows[0]["owner_name"] == "Example Owner"
    assert rows[0]["owner_username"] == "example_owner"
    assert rows[0]["owner_url"] == "https://example.com"
