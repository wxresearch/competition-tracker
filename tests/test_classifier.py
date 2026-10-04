from classifier import classify_saved_post


def classify(text: str):
    return classify_saved_post("", text)


def test_competition_post_is_opportunity():
    r = classify("8 competitions for high school students. Deadlines October 2 and October 11.")
    assert r["local_is_opportunity"] == 1
    assert r["local_kind"] == "competition"
    assert r["local_score"] >= 70


def test_scholarship_post_is_opportunity():
    r = classify("5 full ride scholarships you should know about. Apply for these scholarships.")
    assert r["local_is_opportunity"] == 1
    assert r["local_kind"] == "scholarship"


def test_awards_open_now_are_opportunity():
    r = classify("Easy honors open now! Awards that you can apply for right now.")
    assert r["local_is_opportunity"] == 1
    assert r["local_kind"] == "award"


def test_college_advice_is_not_opportunity():
    r = classify("These are the biggest mistakes students make when applying to college. College admissions and Common App tips.")
    assert r["local_is_opportunity"] == 0
    assert r["local_kind"] == "college_advice"


def test_coding_resources_are_not_opportunity():
    r = classify("Five free resources to improve your programming skills. Coding ideas and websites for students.")
    assert r["local_is_opportunity"] == 0
    assert r["local_kind"] == "resource"


def test_deadline_without_clear_type_goes_to_review():
    r = classify("A student deadline is coming soon. Save this so you do not miss it.")
    assert r["local_is_opportunity"] == 0
    assert r["local_kind"] == "needs_review"


def test_owner_name_does_not_create_false_scholarship():
    r = classify_saved_post(
        "College App + Scholarship Advice | Melody",
        "How to make your ordinary life stand out in college essays.\n\nInstagram owner: College App + Scholarship Advice | Melody",
    )
    assert r["local_is_opportunity"] == 0
    assert r["local_kind"] == "college_advice"


def test_hashtag_keyword_dump_does_not_create_false_award():
    r = classify_saved_post(
        "Girls In CS",
        "how to start coding in high school tutorial! save for later!\n"
        "#girlsincs #coding #studentopportunities computerscience college award awardwinning awards\n"
        "Instagram owner: Girls In CS",
    )
    assert r["local_is_opportunity"] == 0
    assert r["local_kind"] == "resource"
