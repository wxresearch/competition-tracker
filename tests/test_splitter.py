from splitter import looks_like_list_source, split_source_post


def test_splits_eight_dated_competitions():
    text = """8 competitions for high school students, all with October deadlines.

Deadlines, earliest first:
Oct 2: Harvard Moot Court Legal Essay Contest
Oct 11: Princeton University Mathematics Competition
Oct 14: Harvard-Crimson Global Case Competition
Oct 17: Harvard Student Agencies Case Competition
Oct 25: SXSW EDU Student Impact Challenge
Oct 25: Immerse Essay Competition
Oct 28: New York Times Tiny Memoir Contest
Oct 29: Conrad Challenge
"""
    result = split_source_post(text, source_timestamp=1790893224)
    names = [x["name"] for x in result["items"]]

    assert result["is_source_list"] is True
    assert len(names) == 8
    assert "Harvard Moot Court Legal Essay Contest" in names
    assert "Conrad Challenge" in names

    conrad = next(x for x in result["items"] if x["name"] == "Conrad Challenge")
    assert conrad["deadline"] == "2026-10-29"


def test_splits_colon_separated_scholarship_names():
    text = (
        "October scholarships are here.\n"
        "Scholarships to apply for: National Space Club Keynote Scholarship, "
        "The Legacy Lab Foundation Scholarship, Chick-fil-A Remarkable Futures, "
        "Work Ethic Scholarship, and more"
    )
    result = split_source_post(text)
    names = [x["name"] for x in result["items"]]
    assert "National Space Club Keynote Scholarship" in names
    assert "The Legacy Lab Foundation Scholarship" in names
    assert "Chick-fil-A Remarkable Futures" in names
    assert "Work Ethic Scholarship" in names


def test_generic_roundup_is_source_without_fake_child():
    text = (
        "In this blog, we have reviewed science challenges for high school students. "
        "Participating in science contests and competitions can be useful."
    )
    result = split_source_post(text)
    assert result["is_source_list"] is True
    assert result["items"] == []


def test_single_named_scholarship_becomes_child_item():
    text = (
        "Have you heard about the Most Valuable Student scholarship? "
        "We will award 500 scholarships ranging from $4,000 to $30,000 over four years."
    )
    result = split_source_post(text)
    names = [x["name"] for x in result["items"]]
    assert any("Most Valuable Student" in name for name in names)


def test_opening_dates_are_not_mislabeled_as_deadlines():
    text = (
        "Five high school competitions opening this September. "
        "HSA Case Competition and the Profile in Courage Essay both open 1 September, "
        "alongside NCWIT's Aspirations in Computing Award. "
        "Lumiere Scholars Essay Award follows on 14 September, "
        "and the Diamond Challenge opens 16 September."
    )
    result = split_source_post(text, source_timestamp=1790305553)
    diamond = next(x for x in result["items"] if "Diamond Challenge" in x["name"])
    assert diamond["deadline"] is None
    assert diamond["source_timing"] == "Opens 16 September"
