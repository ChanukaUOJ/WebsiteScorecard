import logging
import threading
import re
import requests
import urllib3
from urllib.parse import urljoin, urlparse
from websitescorecard.url_utils import parse_url
from websitescorecard.checks.base import CheckResult
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from dataclasses import dataclass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class TrilingualCheckResult(CheckResult):
    details: str | None = None
    deeplink: str | None = None

LANGUAGE_KEY = ['en','si','ta']

# each entry is values to detect and inject, order should be preserved
LANG_STORAGE_FORMATS: list[tuple[list[str], list[str]]] = [
    (['en', 'si', 'ta'],                            ['en', 'si', 'ta']),
    (['en-US', 'en-GB', 'si-LK', 'ta-LK', 'ta-IN'], ['en-US', 'si-LK', 'ta-LK']),
    (['en-us', 'en-gb', 'si-lk', 'ta-lk', 'ta-in'], ['en-us', 'si-lk', 'ta-lk']),
    (['English', 'Sinhala', 'Tamil'],               ['English', 'Sinhala', 'Tamil']),
    (['english', 'sinhala', 'tamil'],               ['english', 'sinhala', 'tamil']),
]

# html key and tags
HTML_KEY_HREFLANG = 'hreflang'
HTML_KEY_LINK = 'link'
HTML_KEY_ALTERNATE = 'alternate'
HTML_GOOGLE_TRANSLATE_ELEMENT = 'google_translate_element'
HTML_GOOG_TE_COMBO = 'goog-te-combo'
HTML_GOOGLE_TRANSLATE_ELEMENT_JS = 'translate.google.com/translate_a/element.js'

_playwright_lock = threading.Lock()

class TrilingualCheck:
    name = "trilingual"
    column = "trilingual_status"
    error_column = "trilingual_error"
    extra_columns = {
        "details": "trilingual_details",
        "deeplink": "trilingual_deeplink"
    }

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._local = threading.local()

    def _check_html_attribute(self, url: str, soup: BeautifulSoup) -> tuple[bool, list[str]]:
        base_domain = urlparse(url).netloc
        if base_domain.startswith('www.'):
            base_domain = base_domain[4:]

        hreflangs = []
        for link in soup.find_all(HTML_KEY_LINK, rel=HTML_KEY_ALTERNATE):
            hreflang = link.get(HTML_KEY_HREFLANG)
            href = link.get('href')
            if not hreflang:
                continue
            if href:
                absolute_url = urljoin(url, href)
                href_domain = urlparse(absolute_url).netloc
                if href_domain.startswith('www.'):
                    href_domain = href_domain[4:]
                
                # Check if href domain is the same or a subdomain
                if base_domain and not href_domain.endswith(base_domain):
                    continue
            hreflangs.append(hreflang)

        found_langs = {lang.split('-')[0].lower() for lang in hreflangs if lang}
        
        # Also check the base <html> tag's lang attribute (e.g. <html lang="en">)
        if soup.html and soup.html.get('lang'):
            found_langs.add(soup.html.get('lang').split('-')[0].lower())
            
        missing = [lang for lang in LANGUAGE_KEY if lang not in found_langs]
        return (len(missing) == 0, missing)

    def _check_google_translate(self, soup: BeautifulSoup) -> str | None:
        """Detect if a Google Translate / GTranslate widget is present.
        Returns the widget type string if found, or None."""
        # Native Google Translate elements
        if soup.find(id=HTML_GOOGLE_TRANSLATE_ELEMENT):
            return 'google_translate'
        if soup.find(class_=HTML_GOOG_TE_COMBO):
            return 'google_translate'

        # GTranslate plugin
        if soup.find(class_="gtranslate_wrapper"):
            return 'gtranslate'

        for script in soup.find_all('script', src=True):
            src = script.get('src', '').lower()
            if HTML_GOOGLE_TRANSLATE_ELEMENT_JS in src:
                return 'google_translate'
            if 'gtranslate.net' in src:
                return 'gtranslate'
                
        return None

    def _verify_google_translate_languages(
        self, url: str
    ) -> tuple[bool, list[str]]:
        """Use Playwright to check whether the Google Translate / GTranslate
        widget on *url* actually offers English, Sinhala and Tamil.

        Returns (all_found, missing_languages).
        """
        # Language codes used inside Google Translate / GTranslate <select> options
        LANG_OPTION_MAP: dict[str, list[str]] = {
            'en': ['en', 'english'],
            'si': ['si', 'sinhala', 'sinhalese'],
            'ta': ['ta', 'tamil'],
        }

        found_langs: set[str] = set()
        try:
            with _playwright_lock:
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page()
                    try:
                        page.goto(url, wait_until='load', timeout=self.timeout * 1000)
                        page.wait_for_timeout(3000)
                    except PlaywrightTimeoutError:
                        pass

                    # Collect all <option> values and text from <select> elements
                    # that are part of the translate widget.
                    option_values: list[str] = page.evaluate("""() => {
                        const results = [];
                        // Google Translate native combo
                        const goog = document.querySelector('.goog-te-combo, select.gt_selector, select.notranslate');
                        if (goog) {
                            for (const opt of goog.options) {
                                results.push(opt.value.toLowerCase());
                                results.push(opt.textContent.trim().toLowerCase());
                            }
                        }
                        // GTranslate wrappers often use a <select> with
                        // class 'gt_selector' or inside '.gtranslate_wrapper'
                        document.querySelectorAll('.gtranslate_wrapper select, select[onchange*="doGTranslate"]').forEach(sel => {
                            for (const opt of sel.options) {
                                results.push(opt.value.toLowerCase());
                                results.push(opt.textContent.trim().toLowerCase());
                            }
                        });
                        return results;
                    }""")

                    # Also look for GTranslate link/button based widgets (flag / anchor lists)
                    if not option_values:
                        option_values = page.evaluate("""() => {
                            const results = [];
                            document.querySelectorAll('.gtranslate_wrapper a[data-gt-lang], a.gt-current-lang, a.glink').forEach(a => {
                                const lang = a.getAttribute('data-gt-lang') || a.getAttribute('href') || '';
                                results.push(lang.toLowerCase());
                                results.push(a.textContent.trim().toLowerCase());
                            });
                            return results;
                        }""")

                    browser.close()

            # Match collected values against required languages (word-boundary matching
            # to avoid false positives like 'en' in 'sentence')
            for lang, aliases in LANG_OPTION_MAP.items():
                alias_pattern = '|'.join(re.escape(a) for a in aliases)
                if any(re.search(rf'\b(?:{alias_pattern})\b', val) for val in option_values):
                    found_langs.add(lang)

            missing = [l for l in LANGUAGE_KEY if l not in found_langs]
            return (len(missing) == 0, missing)
        except Exception as exc:
            logger.debug("Google Translate language verification failed: %s", exc)
            # If we can't verify, assume the widget is present but we don't know the languages
            return (False, list(LANGUAGE_KEY))

    def _check_url_localization_patterns(self, soup: BeautifulSoup) -> tuple[bool, list[str]]:
        hrefs = [a.get('href') or '' for a in soup.find_all('a', href=True)]

        # Exact patterns for short and long forms, plus optional locale codes (e.g. -US, -lk)
        locale_suffix = r'(?:-[a-zA-Z]+)?'
        lang_regex_map = {
            'en': rf'en(?:glish)?{locale_suffix}',
            'si': rf'si(?:nhala)?{locale_suffix}',
            'ta': rf'ta(?:mil)?{locale_suffix}'
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

    def _check_unicode_content(self, soup: BeautifulSoup) -> tuple[bool, list[str]]:
        NOISE_TAGS = frozenset(['script', 'style', 'head', 'meta', 'noscript', 'nav', 'footer', 'header'])

        # Extract text only from meaningful content tags, skipping those nested inside noise tags
        content_tags = soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'span', 'div', 'article', 'section', 'main'])
        filtered_tags = [tag for tag in content_tags if not any(p.name in NOISE_TAGS for p in tag.parents)]
        text = ' '.join(tag.get_text(separator=' ', strip=True) for tag in filtered_tags)

        found_langs: set[str] = set()
        MIN_CHARS = 50

        if len(re.findall(r'[a-zA-Z]', text)) >= MIN_CHARS:
            found_langs.add('en')

        if len(re.findall(r'[\u0D80-\u0DFF]', text)) >= MIN_CHARS:
            found_langs.add('si')

        if len(re.findall(r'[\u0B80-\u0BFF]', text)) >= MIN_CHARS:
            found_langs.add('ta')

        missing = [lang for lang in LANGUAGE_KEY if lang not in found_langs]
        return (len(missing) == 0, missing)

    def _verify_language_buttons_via_browser(self, url: str, langs_to_check: list[str]) -> list[str]:
        # Returns the list of languages STILL missing after verification
        missing = list(langs_to_check)
        found_langs: set[str] = set()
        try:
            with _playwright_lock:
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    
                    targets = {}
                    if 'en' in langs_to_check:
                        targets['en'] = r'\benglish\b|^en$'
                    if 'si' in langs_to_check:
                        targets['si'] = r'\bsinhala\b|සිංහල|^si$'
                    if 'ta' in langs_to_check:
                        targets['ta'] = r'\btamil\b|தமிழ்|^ta$'
                        
                    for lang, pattern in targets.items():
                        # Use a fresh context (and page) per language to avoid navigation bleed-over
                        context = browser.new_context()
                        page = context.new_page()
                        try:
                            page.goto(url, wait_until="load", timeout=30000)
                            page.wait_for_timeout(2000)
                        except PlaywrightTimeoutError:
                            self._local.had_timeout = True
                            context.close()
                            continue
                            
                        # Try to find a clickable element matching the pattern
                        try:
                            element_handle = page.evaluate_handle(f"""() => {{
                                const regex = /{pattern}/i;
                                const elements = Array.from(document.querySelectorAll('a, button, input[type="submit"], input[type="button"]'));
                                for (let el of elements) {{
                                    if (el.innerText && regex.test(el.innerText)) return el;
                                    if (el.value && regex.test(el.value)) return el;
                                }}
                                return null;
                            }}""")
                        except Exception:
                            context.close()
                            continue
                        
                        if element_handle.as_element():
                            # Click the element
                            try:
                                element_handle.as_element().click(timeout=5000)
                            except Exception:
                                pass
                            
                            # Always wait for load state to be stable before reading content
                            try:
                                page.wait_for_load_state("load", timeout=12000)
                            except Exception:
                                pass
                            page.wait_for_timeout(1000)
                                    
                            # Safely extract the HTML
                            try:
                                html = page.content()
                            except Exception:
                                page.wait_for_timeout(3000)
                                try:
                                    html = page.content()
                                except Exception:
                                    context.close()
                                    continue
                                    
                            soup = BeautifulSoup(html, 'html.parser')
                            _, missing_uni = self._check_unicode_content(soup)
                            if lang not in missing_uni:
                                found_langs.add(lang)
                                
                        context.close()
                    
                    browser.close()
            return [lang for lang in langs_to_check if lang not in found_langs]
        except Exception as exc:
            logger.debug("Language button verification failed: %s", exc)
            self._local.had_timeout = True
            return missing

    def _check_browser_storage_keys(self, url) -> tuple[bool, list[str], str]:
        try:
            with _playwright_lock:
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page()
                    try:
                        # Wait for the load event, then give JavaScript 3 seconds to build the DOM.
                        page.goto(url, wait_until="load", timeout=self.timeout * 1000)
                        page.wait_for_timeout(3000)
                    except PlaywrightTimeoutError:
                        self._local.had_timeout = True

                    # Extract localStorage directly
                    storage = page.evaluate("""()=>{
                        const s = [];
                        for (let i = 0; i < localStorage.length; i++) {
                            s.push({
                                key: localStorage.key(i),
                                value: localStorage.getItem(localStorage.key(i))
                            });
                        }
                        return s;
                    }""")
                    
                    # Search for any key that holds a language value and detect its format
                    target_key = None
                    injection_set = []
                    
                    for item in storage:
                        val = str(item.get('value', ''))
                        for detect_vals, inject_vals in LANG_STORAGE_FORMATS:
                            if val in detect_vals:
                                target_key = item.get('key')
                                injection_set = inject_vals
                                break
                        if target_key:
                            break
                            
                    # Capture original page content before any storage injection
                    original_html = page.content()

                    if target_key:
                        # Inject all 3 values and verify unicode
                        missing_injection = []
                        langs = LANGUAGE_KEY
                        
                        for i in range(3):
                            base_lang = langs[i]
                            inject_val = injection_set[i]
                            
                            page.evaluate("([key, val]) => window.localStorage.setItem(key, val)", [target_key, inject_val])
                            try:
                                page.reload(wait_until="load", timeout=self.timeout * 1000)
                                page.wait_for_timeout(3000)
                            except PlaywrightTimeoutError:
                                pass
                                
                            current_html = page.content()
                            current_soup = BeautifulSoup(current_html, 'html.parser')
                            _, missing_unicode = self._check_unicode_content(current_soup)
                            
                            if base_lang in missing_unicode:
                                missing_injection.append(base_lang)
                                
                        if len(missing_injection) == 0:
                            browser.close()
                            return (True, missing_injection, "BROWSER_STORAGE")

                    html = original_html
                    browser.close()

            soup = BeautifulSoup(html, 'html.parser')
            passed_url_loc, missing_url_localization = self._check_url_localization_patterns(soup)
            passed_uni, missing_unicode = self._check_unicode_content(soup)

            # If it found exactly 2 localization links, and the 3rd language is the language of the page itself,
            # we consider it fully trilingual (since you don't need a switcher to the language you're reading).
            found_by_url = set(LANGUAGE_KEY) - set(missing_url_localization)
            if len(found_by_url) == 2:
                missing_1 = (set(LANGUAGE_KEY) - found_by_url).pop()
                if missing_1 not in missing_unicode: # meaning it IS in current_page_langs
                    found_by_url.add(missing_1)
                    
            if len(found_by_url) == 3:
                passed_url_loc = True

            passed_criteria: list[str] = []
            if passed_url_loc:
                passed_criteria.append("URL_LOCALIZATION")
            if passed_uni:
                passed_criteria.append("UNICODE_CONTENT")

            # Only declare languages found when at least one check confirmed all three;
            # partial coverage across checks does not count.
            if passed_criteria:
                missing = []
            else:
                missing = list(set(missing_url_localization) | set(missing_unicode))
            criteria_str = ", ".join(passed_criteria) if passed_criteria else "JS_RENDERED"
            return (len(missing) == 0, missing, f"{criteria_str}")
        except Exception as exc:
            logger.debug("Browser storage check failed: %s", exc)
            self._local.had_timeout = True
            return (False, list(LANGUAGE_KEY), "ERROR")
    
    def _analyze_soup(self, url: str, soup: BeautifulSoup) -> tuple[set[str], str | None, list[str]]:
        passed_attribute, missing_attribute = self._check_html_attribute(url, soup)
        google_widget = self._check_google_translate(soup)
        passed_url_loc, missing_url_loc = self._check_url_localization_patterns(soup)
        
        passed_uni, missing_uni = self._check_unicode_content(soup)
        current_page_langs = set(LANGUAGE_KEY) - set(missing_uni)
        
        found_by_attr = set(LANGUAGE_KEY) - set(missing_attribute)
        found_by_url = set(LANGUAGE_KEY) - set(missing_url_loc)
        
        # If it found exactly 2 switchers, and the 3rd language is the language of the page itself, it passes.
        if len(found_by_attr) == 2:
            missing_1 = (set(LANGUAGE_KEY) - found_by_attr).pop()
            if missing_1 in current_page_langs:
                found_by_attr.add(missing_1)
                
        if len(found_by_url) == 2:
            missing_1 = (set(LANGUAGE_KEY) - found_by_url).pop()
            if missing_1 in current_page_langs:
                found_by_url.add(missing_1)
                
        passed_attribute = len(found_by_attr) == 3
        passed_url_loc = len(found_by_url) == 3

        passed_criteria: list[str] = []
        if passed_attribute:
            passed_criteria.append("HTML_ATTRIBUTE")
        if passed_url_loc:
            passed_criteria.append("URL_LOCALIZATION")
        if passed_uni:
            passed_criteria.append("UNICODE_CONTENT")

        # Only count languages as found when at least one check confirmed all three;
        # partial coverage across checks does not count as trilingual.
        if passed_criteria:
            found_langs = set(LANGUAGE_KEY)
        else:
            found_langs = set()
            
        return found_langs, google_widget, passed_criteria

    def _get_internal_links(self, base_url: str, soup: BeautifulSoup) -> list[str]:
        lang_links = set()
        other_links = set()
        try:
            base_domain = urlparse(base_url).netloc
            if base_domain.startswith('www.'):
                base_domain = base_domain[4:]
                
            for a in soup.find_all('a', href=True):
                href = a.get('href', '').strip()
                text = a.get_text().strip().lower()
                if not href or href.startswith(('mailto:', 'tel:', 'javascript:', '#')):
                    continue
                
                absolute_url = urljoin(base_url, href)
                parsed_url = urlparse(absolute_url)
                
                href_domain = parsed_url.netloc
                if href_domain.startswith('www.'):
                    href_domain = href_domain[4:]
                
                if href_domain != base_domain:
                    continue
                    
                if parsed_url.path.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png', '.zip', '.doc', '.docx', '.xls', '.xlsx')):
                    continue
                    
                clean_url = absolute_url.split('#')[0]
                
                href_lower = href.lower()
                if re.search(r'(?:/si(?:[/?#]|$)|/ta(?:[/?#]|$)|[?&]lang=(?:si|ta)(?:&|$)|/sinhala|/tamil)', href_lower) or \
                   any(x in text for x in ['sinhala', 'tamil', 'සිංහල', 'தமிழ்']):
                    lang_links.add(clean_url)
                else:
                    other_links.add(clean_url)
        except Exception as exc:
            logger.debug("Error extracting internal links: %s", exc)
        return list(lang_links) + list(other_links)

    def run(self, url: str) -> CheckResult:
        try:
            parsed = parse_url(url)
        except ValueError as exc:
            return TrilingualCheckResult(status="UNREACHABLE", error=str(exc))

        self._local.had_timeout = False

        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            
            # Try http without www first
            try:
                response = requests.get(f'http://{parsed.hostname}', timeout=self.timeout, headers=headers)
                needs_fallback = (response.status_code >= 400)
            except Exception:
                needs_fallback = True
                
            # Fallback to https://www if needed
            if needs_fallback:
                response = requests.get(f'https://www.{parsed.hostname}', timeout=self.timeout, headers=headers, verify=False)
                
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')

            found_langs, google_widget, passed_criteria = self._analyze_soup(response.url, soup)

            if google_widget:
                gt_passed, gt_missing = self._verify_google_translate_languages(response.url)
                if gt_passed:
                    return TrilingualCheckResult(status="TRILINGUAL", error=None, details="GOOGLE_TRANSLATE_API")
                else:
                    logger.debug("Google Translate widget found but missing languages: %s", gt_missing)

            if len(found_langs) == 3:
                return TrilingualCheckResult(status="TRILINGUAL", error=None, details=", ".join(passed_criteria))

            # Try deeplink crawling
            internal_links = self._get_internal_links(response.url, soup)
            # Filter out the homepage itself
            internal_links = [l for l in internal_links if l != response.url and l != response.url.rstrip('/')]
            
            sample_links = internal_links[:5] # The first links are prioritized language links

            checked_links = []
            for link in sample_links:
                checked_links.append(link)
                try:
                    dl_response = requests.get(link, timeout=self.timeout, headers=headers, verify=False)
                    dl_soup = BeautifulSoup(dl_response.text, 'html.parser')
                    dl_found, dl_google_widget, dl_criteria = self._analyze_soup(link, dl_soup)
                    
                    if dl_google_widget:
                        dl_gt_passed, dl_gt_missing = self._verify_google_translate_languages(link)
                        if dl_gt_passed:
                            return TrilingualCheckResult(status="TRILINGUAL", error=None, details="GOOGLE_TRANSLATE_API", deeplink=", ".join(checked_links))
                        else:
                            logger.debug("Deeplink Google Translate widget missing languages: %s", dl_gt_missing)
                    
                    found_langs.update(dl_found)
                    passed_criteria.extend(dl_criteria)
                    
                    if len(found_langs) == 3:
                        unique_criteria = sorted(list(set(passed_criteria)))
                        return TrilingualCheckResult(status="TRILINGUAL", error=None, details=f"DEEPLINK_CRAWL: {', '.join(unique_criteria)}", deeplink=", ".join(checked_links))
                except Exception as exc:
                    if 'timeout' in str(exc).lower() or 'timed out' in str(exc).lower():
                        self._local.had_timeout = True
                    continue

            # Fallback to Playwright on homepage
            try:
                _, missing_browser, browser_method = self._check_browser_storage_keys(response.url)
            except Exception:
                self._local.had_timeout = True
                missing_browser = list(LANGUAGE_KEY)
                browser_method = "ERROR"
            missing_set = set(LANGUAGE_KEY) - found_langs
            missing_set = missing_set & set(missing_browser)

            if len(missing_set) == 0:
                return TrilingualCheckResult(status="TRILINGUAL", error=None, details=browser_method)
                
            # Final Fallback: Functional Verification via click
            try:
                missing_click = self._verify_language_buttons_via_browser(response.url, list(missing_set))
            except Exception:
                self._local.had_timeout = True
                missing_click = list(missing_set)
            missing_set = missing_set & set(missing_click)

            # Forgiveness: If we verified 2 languages, and the only missing one is the language we are already reading, it's trilingual.
            passed_uni, missing_uni = self._check_unicode_content(soup)
            current_page_langs = set(LANGUAGE_KEY) - set(missing_uni)
            
            if len(missing_set) == 1:
                missing_1 = list(missing_set)[0]
                if missing_1 in current_page_langs:
                    missing_set.remove(missing_1)

            if len(missing_set) == 0:
                return TrilingualCheckResult(status="TRILINGUAL", error=None, details="VERIFIED_SWITCHER_CLICK")
            else:
                # Make the error message more intuitive by not claiming the page's native language is 'missing'.
                # However, if it's a mixed-content page that lacks all switchers, we still report all switchers as missing.
                display_missing = missing_set - current_page_langs
                if len(display_missing) == 0:
                    display_missing = missing_set

                if getattr(self._local, 'had_timeout', False):
                     return TrilingualCheckResult(status="TIMEOUT", error=f"Some checks timed out. Potentially missing: {', '.join(sorted(display_missing))}")
                return TrilingualCheckResult(status="NON_TRILINGUAL", error=f"missing languages: {', '.join(sorted(display_missing))}")

        except Exception as exc:
            logger.debug("Trilingual check failed for %s: %s", url, exc)
            return TrilingualCheckResult(status="UNREACHABLE", error="Can't Access")
        