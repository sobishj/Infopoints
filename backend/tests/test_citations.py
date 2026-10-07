from app.rag.citations import is_not_found, validate
from app.rag.prompt import NOT_FOUND


def test_valid_markers_are_kept_in_order_of_use():
    v = validate("Open Procurement [2]. Then approve [1][2].", 3)
    assert v.cited == [2, 1]
    assert v.invalid == []
    assert v.text == "Open Procurement [2]. Then approve [1][2]."


def test_markers_outside_the_provided_sources_are_removed():
    v = validate("Step one [1]. Step two [7]. Step three [0].", 2)
    assert v.cited == [1]
    assert sorted(v.invalid) == [0, 7]
    assert "[7]" not in v.text and "[0]" not in v.text
    assert v.text == "Step one [1]. Step two. Step three."


def test_comma_lists_and_source_prefix_are_normalised():
    v = validate("Configured in Settings [1, 3] and [Source 2].", 3)
    assert v.cited == [1, 3, 2]
    assert v.text == "Configured in Settings [1][3] and [2]."


def test_not_found_detection_tolerates_quotes_and_case():
    assert is_not_found(NOT_FOUND)
    assert is_not_found("I couldn’t find this in the project documents.")
    assert is_not_found("i couldn't find this in the project documents")
    assert not is_not_found("Go to Procurement → Pending Orders [1].")


SOURCES = [
    "Approving purchase orders. To approve an order, go to Procurement > Pending Orders, select the order and click "
    "Approve. Orders above 10,000 EUR also need approval from the finance manager.",
    "Vendors. New vendors are created under Procurement > Vendors > New Vendor. A tax number is required.",
]


def test_wrong_marker_is_moved_to_the_matching_source():
    # Real Qwen 1.5B output: the second fact is on source 1, but the model cited [2].
    answer = ("To approve a purchase order, go to Procurement > Pending Orders, select the order, and click Approve. [1] "
              "Orders above 10,000 EUR also need approval from the finance manager. [2]")
    v = validate(answer, 2, SOURCES)
    assert v.cited == [1]
    assert v.text.endswith("finance manager [1].")
    assert v.regrounded == 1


def test_uncited_sentences_get_the_clearly_matching_source():
    answer = "To create a new vendor, navigate to Procurement > Vendors > New Vendor. A tax number is required for this process."
    v = validate(answer, 2, SOURCES)
    assert v.cited == [2]
    assert v.text == ("To create a new vendor, navigate to Procurement > Vendors > New Vendor [2]. "
                      "A tax number is required for this process [2].")


def test_sentences_matching_no_source_are_left_uncited():
    v = validate("The cafeteria opens at noon on Fridays.", 2, SOURCES)
    assert v.cited == [] and v.regrounded == 0


def test_correct_markers_and_list_formatting_are_preserved():
    answer = "1. Go to Procurement > Pending Orders [1].\n2. Select the order and click Approve [1]."
    v = validate(answer, 2, SOURCES)
    assert v.text == answer and v.regrounded == 0


def test_long_answer_that_mentions_the_phrase_is_not_treated_as_not_found():
    long = "Approve it in Procurement [1]. " * 20 + NOT_FOUND
    assert not validate(long, 1).not_found
