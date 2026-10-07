"""Section headings on PDF passages, and the guard against invented code in answers."""
import os

from app.rag.code_guard import guard_code
from tests.conftest import make_pdf
from worker.chunker import chunk_segments
from worker.extract.pdf import extract_pdf
from worker.extract.sections import SectionTracker, split_by_headings

# Text layout as PyMuPDF extracts it from the KonfiPay reference (Word export).
PAGE_9 = "9 \nHTTP 500 - Internal Server Error \n1. Create a single ErrorItem. \n2. Populate the timestamp. \n3 Authentication \nThe KonfiPay Wrapper provides authentication."
PAGE_10 = "10 \nThe wrapper abstracts the authentication process.\n3.1 \nRequestToken \nRequestToken( \n    insAuthRequestJSON, \n    outBearerToken \n) : Boolean"
PAGE_11 = ('11 \nSample API Call \nStep 1 - Create Authentication Request JSON \njAuthRequest is JSON \n'
           'jAuthRequest.apiKey = "1234567890abcdef" \njAuthRequest.clientName = "KonfiPayDemo" \n'
           'Step 2 - Invoke the Wrapper \nbResult = cKonfiPay.RequestToken(jAuthRequest, stParams, jBearerToken, outErrorItem)')


def test_headings_split_pages_and_carry_over():
    t = SectionTracker()
    p9 = split_by_headings(PAGE_9, t)
    assert [s for s, _ in p9] == [None, "3 Authentication"]
    assert "1. Create a single ErrorItem." in p9[0][1]  # numbered list items are not headings
    p10 = split_by_headings(PAGE_10, t)
    assert [s for s, _ in p10] == ["3 Authentication", "3 Authentication › 3.1 RequestToken"]
    p11 = split_by_headings(PAGE_11, t)
    assert [s for s, _ in p11] == ["3 Authentication › 3.1 RequestToken"]  # carried onto the next page


def test_heading_numbers_must_move_forward():
    t = SectionTracker()
    split_by_headings("4 Bank Accounts\nText", t)
    split_by_headings("2 days later the payment arrives\n3 Retries are possible", t)
    assert t.current == "4 Bank Accounts"


def test_toc_lines_are_not_headings():
    t = SectionTracker()
    split_by_headings("9.3 \nACKNOWLEDGEDOCUMENT ........................ 138", t)
    assert t.current is None


def test_pdf_passages_get_section_in_header(tmp_path):
    path = str(tmp_path / "api.pdf")
    make_pdf(path, ["1 Overview\nIntro text for the wrapper.", "2 Authentication\nCall RequestToken to get a token."])
    drafts = chunk_segments(extract_pdf(path, ocr_images=False).segments, "api.pdf")
    auth = [d for d in drafts if "RequestToken" in d.text][0]
    assert auth.segment.heading == "2 Authentication"
    assert auth.header == "[Source: api.pdf | Page 2 | 2 Authentication]"


def test_question_is_matched_to_the_right_section(db, admin):
    """Every page talks about the token; only the section title says which page is about getting one."""
    from sqlalchemy import text as sql

    from app import folders as svc
    from app.rag.retrieve import _section_hits
    from tests.conftest import DOCS, fake_vector, run_jobs
    body = "The bearer token is passed in every call. Errors are returned as an array."
    os.makedirs(os.path.join(DOCS, "api"))
    titles = ["Payments", "Authentication", "Users", "Accounts", "Documents", "Transactions"]
    make_pdf(os.path.join(DOCS, "api", "ref.pdf"), [f"{i} {t}\n{body}" for i, t in enumerate(titles, 1)])
    svc.create_folder(db, admin.id, "API", "/docs/api")
    run_jobs()
    projects = [r[0] for r in db.execute(sql("SELECT id FROM projects")).all()]
    hits = _section_hits(db, fake_vector("authentication"), projects, 8)
    page = db.execute(sql("SELECT page FROM chunks WHERE id = :i"), {"i": hits[0][0]}).scalar()
    assert page == 2
    # No section stands out (the question matches none of the titles): no boost at all.
    assert _section_hits(db, fake_vector("bearer token errors"), projects, 8) == []


# ---------------------------------------------------------------- code guard
INVENTED = """Here is an example [1]:

```javascript
function getAccessToken() {
    const response = await fetch('https://api.example.com/your-endpoint', {
        headers: { 'Authorization': `Bearer ${token}` }
    });
    return response.json();
}
```"""


def test_invented_code_is_replaced_by_the_documents_sample():
    sources = [PAGE_10, PAGE_11]
    text, n = guard_code(INVENTED, sources, "what is access token sample code")
    assert n == 1
    assert "fetch(" not in text and "api.example.com" not in text
    assert 'jAuthRequest.apiKey = "1234567890abcdef"' in text
    assert "The documents show this sample [2]" in text


def test_code_copied_from_the_sources_is_kept():
    answer = "Call it like this [2]:\n\n```\njAuthRequest is JSON\njAuthRequest.apiKey = \"1234567890abcdef\"\n" \
             "bResult = cKonfiPay.RequestToken(jAuthRequest, stParams, jBearerToken, outErrorItem)\n```"
    text, n = guard_code(answer, [PAGE_10, PAGE_11], "sample code")
    assert n == 0 and text == answer


def test_code_question_answered_with_a_fragment_gets_the_documents_sample():
    # Real Qwen output for "what is access token sample code": only the token structure, no sample.
    answer = '```json\n{\n    "accessToken": "eyJhbGciOiJIUzI1NiIs..."\n}\n```'
    sources = [PAGE_10, PAGE_11 + '\n{ "accessToken": "eyJhbGciOiJIUzI1NiIs..." }']
    text, n = guard_code(answer, sources, "what is access token sample code")
    assert n == 1
    assert text.startswith(answer)  # the model's (grounded) part stays
    assert "The documents show this sample [2]" in text and "Step 1 - Create Authentication Request JSON" in text


def test_worked_example_is_preferred_over_the_method_definition():
    definition = ("3.1 RequestToken\nRequestToken(\n    insAuthRequestJSON,\n    outBearerToken\n) : Boolean\n"
                  "inKonfiPayParams.sURL = \"https://api.konfipay.com/\"\ninKonfiPayParams.sApiKey = \"x\"")
    text, _ = guard_code("No code.", [definition, PAGE_11], "access token sample code")
    assert "[2]" in text and "Step 1 - Create Authentication Request JSON" in text


def test_sample_continues_onto_the_next_page():
    page_12 = "12 \nStep 5 - Check the Result \nIF bResult THEN \n    Info(jBearerToken.accessToken) \nEND"
    text, _ = guard_code("No code.", [PAGE_11, page_12], "access token sample code", next_of={0: 1})
    assert "The documents show this sample [1][2]" in text
    assert "Step 2 - Invoke the Wrapper" in text and "Step 5 - Check the Result" in text
    assert "\n12 \n" not in text  # the page number between the two pages is dropped


def test_code_changed_by_the_model_is_replaced_by_the_document_text():
    # One altered line (": Boolean" added, a parameter renamed) is enough: code must be copied word for word.
    answer = "```\nbResult = cKonfiPay.RequestToken(jAuthRequest, stParams, jToken, outErrorItem) : Boolean\n```"
    text, n = guard_code(answer, [PAGE_10, PAGE_11], "sample code")
    assert n == 1 and "jToken," not in text and "The documents show this sample" in text


PAGE_225 = ("225 \n12.4 \nChangeMailAddressUser \nChangeMailAddressUser( \n inBearerToken, \n inRID, \n"
            " inEmailUpdateRequest, \n outUserResponse, \n outErrorItem \n) : Boolean \nReturn Value")
PAGE_227 = ('227 \nSample API Call \nStep 1 - Create Bearer Token \njBearerToken is JSON \n'
            'jBearerToken.accessToken = "eyJhbGciOiJIUzI1NiIs..." \nStep 4 - Invoke the Wrapper \nbResult is boolean \n'
            'bResult = cKonfipay.ChangeMailAddressUser( \n jBearerToken, \n inRID, \n inEmailUpdateRequest, \n'
            ' outUserResponse, \n outErrorItem \n) \nSuccessful Response \nWhen the operation succeeds: \n'
            '• The method returns True.')


def test_sample_is_shown_once_with_its_definition_and_ends_where_the_sample_ends():
    # Real Qwen output: three altered code blocks for one method.
    bad = "```\nbResult = cKonfipay.ChangeMailAddressUser(jBearerToken, inRID)\n```"
    text, n = guard_code(f"Step 1:\n{bad}\nStep 2:\n{bad}\nFull example:\n{bad}", [PAGE_225, PAGE_227],
                         "change a user's email address sample code")
    assert n == 3
    assert text.count("The documents show this sample") == 1 and text.count("See the sample from the documents") == 2
    assert "The documents define the method as [1]" in text and ") : Boolean" in text
    assert "outErrorItem\n)" in text and "Successful Response" not in text and "returns True" not in text


def test_grounding_keeps_the_markers_on_infopoints_own_lines():
    from app.rag.citations import validate
    page_228 = "228 \nProcessing Flow \n5.Call ChangeMailAddressUser(). \nThe documents define the method name."
    text, _ = guard_code("No code.", [page_228, PAGE_225, PAGE_227], "change email sample code")
    v = validate(text, 3, [page_228, PAGE_225, PAGE_227])
    assert "The documents define the method as [2]" in v.text


def test_non_code_question_gets_no_sample():
    text, n = guard_code("Tokens expire after an hour [1].", [PAGE_11], "how long is a token valid")
    assert n == 0


def test_invented_code_without_any_code_in_sources():
    text, n = guard_code(INVENTED, ["Tokens expire after one hour."], "token sample")
    assert n == 1 and "don't include a code sample" in text


def test_page_numbers_and_bare_headings_are_not_indexed():
    from worker.chunker import has_content
    assert not has_content("6") and not has_content("2 Common Methods", "2 Common Methods")
    assert has_content("Q3 Revenue: 4.2M")
    assert has_content("The KonfiPay Wrapper API provides a simplified interface.")


def test_definitional_questions_are_recognised():
    from app.rag.retrieve import is_definitional
    assert is_definitional("What is konfipay?") and is_definitional("Tell me about the wrapper")
    assert is_definitional("what does this documentation cover")
    assert not is_definitional("what is access token sample code")
    assert not is_definitional("how do I get an access token")
