import re
import requests
import urllib3
from websitescorecard.url_utils import parse_url
from websitescorecard.checks.base import CheckResult
from bs4 import BeautifulSoup

LANGUAGE_KEY = ['en','si','ta']

# html key and tags
HTML_KEY_HREFLANG = 'hreflang'
HTML_KEY_LINK = 'link'
HTML_KEY_ALTERNATE = 'alternate'
HTML_GOOGLE_TRANSLATE_ELEMENT = 'google_translate_element'
HTML_GOOG_TE_COMBO = 'goog-te-combo'
HTML_GOOGLE_TRANSLATE_ELEMENT_JS = 'translate.google.com/translate_a/element.js'


class TrilingualCheck:
    name = "trilingual"
    column = "trilingual_status"
    error_column = "trilingual_error"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def check_html_attribute(self, soup: BeautifulSoup) -> tuple[bool, list[str]]:
        hreflangs = [link.get(HTML_KEY_HREFLANG) for link in soup.find_all(HTML_KEY_LINK, rel=HTML_KEY_ALTERNATE)]
        found_langs = {lang.split('-')[0].lower() for lang in hreflangs if lang}
        
        # Also check the base <html> tag's lang attribute (e.g. <html lang="en">)
        if soup.html and soup.html.get('lang'):
            found_langs.add(soup.html.get('lang').split('-')[0].lower())
            
        missing = [lang for lang in LANGUAGE_KEY if lang not in found_langs]
        return (len(missing) == 0, missing)

    def check_google_translate(self, soup: BeautifulSoup) -> bool:
        # Native Google Translate elements
        if soup.find(id=HTML_GOOGLE_TRANSLATE_ELEMENT):
            return True
        if soup.find(class_=HTML_GOOG_TE_COMBO):
            return True

        # GTranslate plugin (used by archaeology.gov.lk and others)
        if soup.find(class_="gtranslate_wrapper"):
            return True

        for script in soup.find_all('script', src=True):
            src = script.get('src', '').lower()
            if HTML_GOOGLE_TRANSLATE_ELEMENT_JS in src or 'gtranslate.net' in src:
                return True
                
        return False

    def check_url_localization_patterns(self, soup: BeautifulSoup) -> tuple[bool, list[str]]:
        hrefs = [a.get('href') or '' for a in soup.find_all('a', href=True)]

        # Exact patterns for short and long forms, plus optional locale codes (e.g. -US, -lk)
        locale_suffix = r'(?:-[a-zA-Z]+)?'
        lang_regex_map = {
            'en': rf'en(?:glish|g)?{locale_suffix}',
            'si': rf'si(?:nhala|n)?{locale_suffix}',
            'ta': rf'ta(?:mil|m)?{locale_suffix}'
        }

        found_langs: set[str] = set()
        for lang in LANGUAGE_KEY:
            # Use the specific regex for this language to avoid false positives like "sigiriya" matching "si"
            pattern = lang_regex_map.get(lang, lang)
            
            # matches path segments: /en/ /english/ /sinhala/
            path_pattern = re.compile(rf'(?:^|/)({pattern})(?:/|$|\?|#)', re.IGNORECASE)
            # matches query params: ?lang=en  &lang=english
            query_pattern = re.compile(rf'[?&]lang=({pattern})(?:&|$)', re.IGNORECASE)
            
            if any(path_pattern.search(href) or query_pattern.search(href) for href in hrefs):
                found_langs.add(lang)

        missing = [lang for lang in LANGUAGE_KEY if lang not in found_langs]
        return (len(missing) == 0, missing)

    def check_unicode_content(self, soup: BeautifulSoup) -> tuple[bool, list[str]]:
        text = soup.get_text()
        found_langs: set[str] = set()
        
        # Check English (basic Latin alphabet)
        if re.search(r'[a-zA-Z]', text):
            found_langs.add('en')
            
        # Check Sinhala unicode block
        if re.search(r'[\u0D80-\u0DFF]', text):
            found_langs.add('si')
            
        # Check Tamil unicode block
        if re.search(r'[\u0B80-\u0BFF]', text):
            found_langs.add('ta')
            
        missing = [lang for lang in LANGUAGE_KEY if lang not in found_langs]
        return (len(missing) == 0, missing)

    def run(self, url: str) -> CheckResult:
        try:
            parsed = parse_url(url)
        except ValueError as exc:
            return CheckResult(status="unreachable", error=str(exc))

        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            
            # Try http without www first
            try:
                response = requests.get(f'http://{parsed.hostname}', timeout=self.timeout, headers=headers)
                needs_fallback = (response.status_code == 404)
            except Exception:
                needs_fallback = True
                
            # Fallback to https://www if needed
            if needs_fallback:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                response = requests.get(f'https://www.{parsed.hostname}', timeout=self.timeout, headers=headers, verify=False)
                
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')

            # HTML lang attribute and header check
            passed_attribute, missing_attribute = self.check_html_attribute(soup)

            # check google translator use
            passed_google_translator_use = self.check_google_translate(soup)

            # URL Localization patterns
            passed_url_localization, missing_url_localization = self.check_url_localization_patterns(soup)

            # Direct body text contents (Unicode)
            passed_unicode, missing_unicode = self.check_unicode_content(soup)

            # Languages missing from ALL checks are truly missing
            missing_set = set(missing_attribute) & set(missing_url_localization) & set(missing_unicode)

            # If no languages are missing (combined), or it uses Google Translate, it passes!
            if len(missing_set) == 0 or passed_google_translator_use:
                return CheckResult(status="trilingual", error=None)
            else:
                return CheckResult(status="Non-trilingual", error=f"missing languages: {', '.join(sorted(missing_set))}")

        except Exception as exc:
            return CheckResult(status="unreachable", error="Can't Access")
        