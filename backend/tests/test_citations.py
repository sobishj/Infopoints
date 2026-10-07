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
    assert is_not_found("I could  not find anything about this in the Selected Document Folders.")
    assert is_not_found("i could not find anything about this in the selected document folders")
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


def test_code_blocks_are_never_given_markers():
    # Real Qwen output put a marker inside the code sample.
    answer = ("Invoke the wrapper [1]:\n```javascript\nbResult = cKonfipay.ListPaymentFiles( [2]\n"
              "    jBearerToken, pageNumber, pageSize\n)\n```\nIt returns True on success.")
    sources = ["Invoke the wrapper: bResult = cKonfipay.ListPaymentFiles(jBearerToken, pageNumber, pageSize). "
               "The method returns True on success.", SOURCES[1]]
    v = validate(answer, 2, sources)
    code = v.text.split("```")[1]
    assert "[" not in code.replace("[]", "")
    assert "ListPaymentFiles(\n" in code


def test_long_answer_that_mentions_the_phrase_is_not_treated_as_not_found():
    long = "Approve it in Procurement [1]. " * 20 + NOT_FOUND
    assert not validate(long, 1).not_found


# ------------------------------------------------------------- answers must come from the folders
KONFIPAY = ("RequestToken returns an access token. The wrapper caches the bearer token internally. "
            "If the existing token has not expired, the cached token is returned. Call POST /authentication/token.")


def test_answer_from_general_knowledge_becomes_not_found():
    answer = ("OAuth 2.0 access tokens are usually JWTs signed with RS256 [1]. Refresh tokens let clients obtain "
              "new ones without asking the user to log in again [1].")
    v = validate(answer, 1, [KONFIPAY])
    assert v.not_found and v.text == NOT_FOUND and v.supported < 0.5


def test_hedged_answer_becomes_not_found():
    answer = ("The documents do not mention token refresh. In general, you call the refresh endpoint with "
              "the refresh token [1].")
    assert validate(answer, 1, [KONFIPAY]).not_found


def test_answer_from_the_documents_is_kept():
    answer = ("The wrapper caches the bearer token internally [1]. If the existing token has not expired, "
              "the cached token is returned [1].")
    v = validate(answer, 1, [KONFIPAY])
    assert not v.not_found and v.supported == 1.0


def test_infopoint_sample_lines_do_not_count_against_support():
    answer = ("The wrapper caches the bearer token internally [1].\n\nThe documents show this sample [1]:\n\n"
              "```text\nbResult = cKonfiPay.RequestToken(jAuthRequest)\n```")
    assert not validate(answer, 1, [KONFIPAY]).not_found


def test_answer_about_a_topic_the_documents_never_mention_becomes_not_found():
    # Real Qwen output: well-matched words, but "refresh" appears in no source.
    answer = ("To refresh an expired token, create a Refresh Token JSON object with the bearer token and call "
              "RequestToken [1]. The cached token is returned [1].")
    v = validate(answer, 1, [KONFIPAY], "how do I refresh an expired token using a refresh token")
    assert v.not_found


def test_question_words_missing_from_sources_do_not_block_a_real_answer():
    answer = "The wrapper caches the bearer token internally [1]."
    assert not validate(answer, 1, [KONFIPAY], "what happens to the bearer token, explain").not_found
