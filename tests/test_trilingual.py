import pytest
from unittest.mock import patch, MagicMock
from bs4 import BeautifulSoup

from websitescorecard.checks.trilingual import TrilingualCheck, TrilingualCheckResult

@pytest.fixture
def checker():
    return TrilingualCheck(timeout=1.0)

# 1. Static HTML Checks
def test_check_html_attribute_all_present(checker):
    html = """
    <html>
        <head>
            <link rel="alternate" hreflang="en" href="/en/" />
            <link rel="alternate" hreflang="si" href="/si/" />
            <link rel="alternate" hreflang="ta" href="/ta/" />
        </head>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    passed, missing = checker._check_html_attribute("https://example.com", soup)
    assert passed is True
    assert not missing

def test_check_html_attribute_missing_ta(checker):
    html = """
    <html lang="en">
        <head>
            <link rel="alternate" hreflang="si-LK" href="/si/" />
        </head>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    passed, missing = checker._check_html_attribute("https://example.com", soup)
    assert passed is False
    assert missing == ["ta"]

def test_check_html_attribute_wrong_domain(checker):
    html = """
    <html lang="si">
        <head>
            <!-- Valid subdomain -->
            <link rel="alternate" hreflang="ta" href="https://www.example.com/ta/" />
            <!-- Invalid domain -->
            <link rel="alternate" hreflang="en" href="https://other.com/en/" />
        </head>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    passed, missing = checker._check_html_attribute("https://example.com", soup)
    assert passed is False
    assert missing == ["en"]

# 2. URL Localization Patterns
def test_url_localization_exact_paths(checker):
    html = """
    <a href="/en/">English</a>
    <a href="/si-LK/">Sinhala</a>
    <a href="/tamil/">Tamil</a>
    """
    soup = BeautifulSoup(html, "html.parser")
    passed, missing = checker._check_url_localization_patterns(soup)
    assert passed is True
    assert not missing

def test_url_localization_query_params(checker):
    html = """
    <a href="?lang=en">English</a>
    <a href="?lang=sinhala">Sinhala</a>
    <a href="?lang=ta">Tamil</a>
    """
    soup = BeautifulSoup(html, "html.parser")
    passed, missing = checker._check_url_localization_patterns(soup)
    assert passed is True

def test_url_localization_false_positive(checker):
    # 'sigiriya' should not match 'si'
    html = """
    <a href="/sigiriya/">Sigiriya</a>
    <a href="/en/">English</a>
    """
    soup = BeautifulSoup(html, "html.parser")
    passed, missing = checker._check_url_localization_patterns(soup)
    assert passed is False
    assert set(missing) == {"si", "ta"}

# 3. Unicode Content Detection
def test_unicode_content_detection(checker):
    # Needs >= 50 English chars
    en_text = "a" * 50
    # Needs >= 50 Sinhala chars
    si_text = "\u0D80" * 50
    # Needs >= 50 Tamil chars
    ta_text = "\u0B80" * 50
    
    html = f"""
    <div>
        <p>{en_text}</p>
        <p>{si_text}</p>
        <p>{ta_text}</p>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    passed, missing = checker._check_unicode_content(soup)
    assert passed is True
    assert not missing

def test_unicode_content_noise_ignore(checker):
    # Script tag should be ignored
    en_text = "a" * 200
    html = f"""
    <script>{en_text}</script>
    <p>සිංහල අකුරු පහක්</p>
    """
    soup = BeautifulSoup(html, "html.parser")
    passed, missing = checker._check_unicode_content(soup)
    assert passed is False
    assert "en" in missing

# 4. Analyze Soup Forgiveness Logic
def test_analyze_soup_forgiveness(checker):
    # English page text
    en_text = "a" * 50
    # Links to si and ta, missing en
    html = f"""
    <div>
        <p>{en_text}</p>
        <a href="/si/">Sinhala</a>
        <a href="/ta/">Tamil</a>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    found_langs, widget, criteria = checker._analyze_soup("https://example.com", soup)
    assert found_langs == {"en", "si", "ta"}
    assert "URL_LOCALIZATION" in criteria

def test_analyze_soup_unicode_content_all_three(checker):
    # Page text has en, si, and ta — unicode_content alone should pass the site
    en_text = "a" * 150
    si_text = "\u0D80" * 50
    ta_text = "\u0B80" * 50
    html = f"""
    <div>
        <p>{en_text}</p>
        <p>{si_text}</p>
        <p>{ta_text}</p>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    found_langs, widget, criteria = checker._analyze_soup("https://example.com", soup)
    assert found_langs == {"en", "si", "ta"}
    assert "UNICODE_CONTENT" in criteria

# 5. Internal Links
def test_get_internal_links_strips_www(checker):
    html = """
    <a href="/relative">Relative</a>
    <a href="https://www.example.gov.lk/en">English</a>
    <a href="https://other.com">Other</a>
    <a href="https://example.gov.lk/si">Sinhala</a>
    <a href="/doc.pdf">PDF</a>
    """
    soup = BeautifulSoup(html, "html.parser")
    # Base url without www
    links = checker._get_internal_links("https://example.gov.lk", soup)
    
    # Should include relative, www, and explicit non-www. Exclude other.com and PDF.
    assert "https://example.gov.lk/relative" in links
    assert "https://www.example.gov.lk/en" in links
    assert "https://example.gov.lk/si" in links
    assert len(links) == 3
    # Check prioritization: Sinhala should be first (it matched '/si')
    assert links[0] == "https://example.gov.lk/si"

# 6. Google Translate
def test_check_google_translate(checker):
    html = '<div id="google_translate_element"></div>'
    assert checker._check_google_translate(BeautifulSoup(html, "html.parser")) == "google_translate"
    
    html = '<div class="gtranslate_wrapper"></div>'
    assert checker._check_google_translate(BeautifulSoup(html, "html.parser")) == "gtranslate"
    
    assert checker._check_google_translate(BeautifulSoup("<div></div>", "html.parser")) is None

@patch("websitescorecard.checks.trilingual.sync_playwright")
def test_verify_google_translate_languages_pass(mock_playwright, checker):
    mock_browser = MagicMock()
    mock_page = MagicMock()
    # p.chromium.launch() is now used as a context manager: `with p.chromium.launch() as browser`
    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value.__enter__.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page
    
    # Mock evaluate to return found options
    mock_page.evaluate.return_value = ["en", "english", "si", "sinhala", "ta", "tamil"]
    
    passed, missing = checker._verify_google_translate_languages("https://example.com")
    assert passed is True
    assert not missing

# 7. Browser fallbacks
@patch("websitescorecard.checks.trilingual.sync_playwright")
def test_browser_storage_keys_pass(mock_playwright, checker):
    mock_browser = MagicMock()
    mock_page = MagicMock()
    # p.chromium.launch() is now used as a context manager: `with p.chromium.launch() as browser`
    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value.__enter__.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page
    
    # Return one storage item matching our format
    mock_page.evaluate.side_effect = [
        [{"key": "lang", "value": "en-US"}],  # storage extraction
        None, None, None  # injections
    ]
    
    # Return html containing unicode characters for each injection reload
    en_html = f"<html><p>{'a'*50}</p></html>"
    si_html = f"<html><p>{chr(0x0D80)*50}</p></html>"
    ta_html = f"<html><p>{chr(0x0B80)*50}</p></html>"
    
    mock_page.content.side_effect = [en_html, en_html, si_html, ta_html]
    
    passed, missing, method = checker._check_browser_storage_keys("https://example.com")
    assert passed is True
    assert method == "BROWSER_STORAGE"

# 8. Full Workflow
@patch("websitescorecard.checks.trilingual.requests.get")
@patch("websitescorecard.checks.trilingual.TrilingualCheck._verify_language_buttons_via_browser")
@patch("websitescorecard.checks.trilingual.TrilingualCheck._check_browser_storage_keys")
def test_run_total_failure(mock_storage, mock_buttons, mock_requests, checker):
    # Mock requests for homepage and fallback https
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body>Just a basic site without languages</body></html>"
    mock_response.url = "https://example.com"
    mock_requests.return_value = mock_response
    
    mock_storage.return_value = (False, ["si", "ta"], "ERROR")
    mock_buttons.return_value = ["si", "ta"]
    
    result = checker.run("example.com")
    assert result.status == "NON_TRILINGUAL"
    assert result.error == "missing languages: si, ta"

@patch("websitescorecard.checks.trilingual.requests.get")
def test_run_unreachable(mock_requests, checker):
    mock_requests.side_effect = Exception("Connection refused")
    result = checker.run("example.com")
    assert result.status == "UNREACHABLE"

@patch("websitescorecard.checks.trilingual.requests.get")
def test_run_homepage_success(mock_requests, checker):
    html = """
    <html>
        <head>
            <link rel="alternate" hreflang="en" href="/en/" />
            <link rel="alternate" hreflang="si" href="/si/" />
            <link rel="alternate" hreflang="ta" href="/ta/" />
        </head>
    </html>
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = html
    mock_response.url = "https://example.com"
    mock_requests.return_value = mock_response
    
    result = checker.run("example.com")
    assert result.status == "TRILINGUAL"
    assert result.details == "HTML_ATTRIBUTE"
