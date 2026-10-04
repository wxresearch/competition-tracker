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
    assert rows[0]["import_title"] == "essay competitions"


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
    assert rows[0]["import_title"] == "example_account"


def test_ignores_owner_hashtag_brand_partner_metadata():
    data = b'''{
      "saved_saved_media": [
        {
          "title": "essay competitions",
          "string_map_data": {
            "Saved on": {
              "href": "https://www.instagram.com/p/REALPOST/",
              "timestamp": 1719763200
            },
            "Owner": {
              "href": "https://www.instagram.com/example_owner/"
            },
            "Hashtags": {
              "href": "https://www.instagram.com/explore/tags/essay/"
            },
            "Brand partner": {
              "href": "https://www.instagram.com/example_brand/"
            }
          }
        }
      ]
    }'''
    rows = parse_json(data)
    assert len(rows) == 1
    assert rows[0]["instagram_url"] == "https://www.instagram.com/p/REALPOST/"
    assert rows[0]["import_title"] == "essay competitions"


def test_does_not_import_profile_only_string_list_data():
    data = b'''[
      {
        "title": "Owner",
        "string_list_data": [
          {
            "href": "https://www.instagram.com/example_owner/",
            "value": "example_owner"
          }
        ]
      }
    ]'''
    rows = parse_json(data)
    assert rows == []


def test_instagram_string_map_alternate_label():
    data = b'''{
      "saved_saved_media": [
        {
          "title": "my saves",
          "string_map_data": {
            "Media": {
              "href": "https://www.instagram.com/reel/ALT123/",
              "timestamp": 1719763200
            },
            "Owner": {
              "href": "https://www.instagram.com/some_owner/"
            }
          }
        }
      ]
    }'''
    rows = parse_json(data)
    assert len(rows) == 1
    assert rows[0]["instagram_url"] == "https://www.instagram.com/reel/ALT123/"
