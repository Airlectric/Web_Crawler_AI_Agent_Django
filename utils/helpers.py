from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import logging
from typing import Set, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import tldextract
import backoff
import re
import json
import os
import time
import spacy
from scipy.spatial.distance import cosine

logger = logging.getLogger(__name__)


# Load config dynamically
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'data/config.json')
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "REQUEST_TIMEOUT": 15,
            "MAX_WORKERS": 5,
            "MAX_DEPTH": 4,
            "MAX_URLS": 30,
            "TIMEOUT_SECONDS": 3600
        }


def save_config(config):
    config_path = os.path.join(os.path.dirname(__file__), '..', 'data/config.json')
    with open(config_path, 'w') as f:
        json.dump(config, f)


config = load_config()
REQUEST_TIMEOUT = config['REQUEST_TIMEOUT']
MAX_WORKERS = config['MAX_WORKERS']
MAX_DEPTH = config['MAX_DEPTH']
MAX_URLS = config['MAX_URLS']
TIMEOUT_SECONDS = config['TIMEOUT_SECONDS']

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

start_time = time.time()

# Load spaCy model for semantic analysis
nlp = spacy.load("en_core_web_md")

# Pre-computed category embeddings for semantic classification
LAB_KEYWORDS = ["lab", "research", "group", "faculty", "project", "center", "institute", "academic", "publications", "studies", "department", "clinic"]
STARTUP_KEYWORDS = ["startup", "innovation", "venture", "accelerator", "incubator", "entrepreneur", "funding"]
lab_embedding = nlp(" ".join(LAB_KEYWORDS)).vector
startup_embedding = nlp(" ".join(STARTUP_KEYWORDS)).vector

### Utility Functions
def load_university_domains(file_path: str = 'data/universities.txt') -> Set[str]:
    """Load and validate university domains."""
    logging.info(f"Loading university domains from {file_path}")
    domains = set()
    try:
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parsed = tldextract.extract(line)
                if parsed.registered_domain:
                    domains.add(parsed.registered_domain.lower())
                else:
                    logging.warning(f"Invalid domain format: {line}")
        logging.info(f"Loaded {len(domains)} validated university domains")
        return domains
    except FileNotFoundError:
        logging.error(f"University domains file not found: {file_path}")
        return set()

def is_university_domain(url: str, university_domains: Set[str]) -> bool:
    """Check if a URL belongs to a university domain, including subdomains."""
    extracted = tldextract.extract(url)
    comparison_domains = {
        extracted.registered_domain,
        f"{extracted.subdomain}.{extracted.registered_domain}" if extracted.subdomain else None
    }
    return len(comparison_domains & university_domains) > 0

@backoff.on_exception(
    backoff.expo,
    (requests.exceptions.RequestException, requests.exceptions.Timeout),
    max_tries=3,
    jitter=backoff.full_jitter(60))
def fetch_url(url: str) -> Tuple[str, str]:
    """Fetch URL content with robust error handling."""
    try:
        response = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            logging.warning(f"URL not found (404): {url}")
            return (url, None)
        response.raise_for_status()
        return (url, response.text)
    except requests.exceptions.HTTPError as e:
        logging.warning(f"HTTP error {e.response.status_code} for {url}")
        return (url, None)
    except Exception as e:
        logging.warning(f"Failed to fetch {url}: {str(e)}")
        return (url, None)

def extract_links(html: str, base_url: str) -> Set[Tuple[str, str]]:
    """Extract links with their anchor text from HTML."""
    if not html:
        return set()
    soup = BeautifulSoup(html, 'lxml')
    links_with_anchor = set()
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        full_url = urljoin(base_url, href).split('#')[0].rstrip('/')
        anchor_text = a.get_text(strip=True) or ""
        if is_valid_url(full_url):
            links_with_anchor.add((full_url, anchor_text))
    return links_with_anchor

def is_valid_url(url: str) -> bool:
    """Validate URL format and exclude unwanted patterns."""
    parsed = urlparse(url)
    return (
        parsed.scheme in {'http', 'https'}
        and parsed.netloc
        and not re.search(r"(login|signup|auth|\.pdf|\.docx?)$", url, re.I)
    )

### Semantic Classification Function
def categorize_urls_with_semantics(links_with_anchor: Set[Tuple[str, str]], university_domains: Set[str]) -> Tuple[Set[Tuple[str, str]], Set[Tuple[str, str]]]:
    """Categorize URLs using semantic similarity based on anchor text or URL context."""
    lab_urls = set()
    startup_urls = set()

    for url, anchor_text in links_with_anchor:
        # Use anchor text as context; fallback to URL if no anchor text
        context = anchor_text if anchor_text else url
        context_doc = nlp(context)
        context_embedding = context_doc.vector

        # Ensure the embedding is non-zero before computing similarity
        if context_embedding.any():
            lab_similarity = 1 - cosine(context_embedding, lab_embedding)
            startup_similarity = 1 - cosine(context_embedding, startup_embedding)

            # Classify based on similarity threshold and domain
            if is_university_domain(url, university_domains) and lab_similarity > startup_similarity and lab_similarity > 0.5:
                lab_urls.add((url, anchor_text))
            elif startup_similarity > lab_similarity and startup_similarity > 0.5:
                startup_urls.add((url, anchor_text))

    return lab_urls, startup_urls

### Core Processing Function
def process_directory(url: str, university_domains: Set[str], visited_urls: Set[str], depth: int = 0) -> Tuple[Set[Tuple[str, str]], Set[Tuple[str, str]]]:
    """Recursively crawl directories and categorize URLs."""
    if depth > MAX_DEPTH or url in visited_urls or len(visited_urls) >= MAX_URLS or (time.time() - start_time) > TIMEOUT_SECONDS:
        logging.info(f"Stopping at {url}: Depth {depth}, Visited {len(visited_urls)}, Time elapsed {int(time.time() - start_time)}s")
        return set(), set()

    logging.info(f"Processing directory at depth {depth}: {url} (Visited: {len(visited_urls)})")
    visited_urls.add(url)
    url_lab_urls: Set[Tuple[str, str]] = set()
    url_startup_urls: Set[Tuple[str, str]] = set()

    # Fetch and parse the page
    _, html = fetch_url(url)
    if not html:
        return url_lab_urls, url_startup_urls

    # Extract links with anchor text
    links_with_anchor = extract_links(html, url)

    # Categorize using semantic analysis
    lab_urls, startup_urls = categorize_urls_with_semantics(links_with_anchor, university_domains)
    url_lab_urls.update(lab_urls)
    url_startup_urls.update(startup_urls)

    # Recursively process sublinks
    sublinks = {link[0] for link in links_with_anchor} - visited_urls
    logging.info(f"Found {len(sublinks)} sublinks at depth {depth} for {url}")

    if depth + 1 <= MAX_DEPTH and sublinks:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(process_directory, sub_url, university_domains, visited_urls, depth + 1): sub_url
                for sub_url in sublinks if len(visited_urls) < MAX_URLS and (time.time() - start_time) <= TIMEOUT_SECONDS
            }
            for future in as_completed(futures):
                try:
                    sub_lab_urls, sub_startup_urls = future.result()
                    url_lab_urls.update(sub_lab_urls)
                    url_startup_urls.update(sub_startup_urls)
                except Exception as e:
                    sub_url = futures[future]
                    logging.error(f"Error processing sublink {sub_url}: {str(e)}")

    return url_lab_urls, url_startup_urls

### Main URL Generation Function
def generate_urls():
    """Generate and categorize URLs with recursive crawling and semantic analysis."""
    global start_time
    start_time = time.time()

    try:
        university_domains = load_university_domains()
        if not university_domains:
            logging.error("Aborting due to missing university domains")
            return

        try:
            with open('data/potential_directories.txt', 'r') as f:
                directory_urls = {line.strip() for line in f if line.strip()}
        except FileNotFoundError:
            logging.error("data/potential_directories.txt not found")
            return

        all_lab_urls: Set[Tuple[str, str]] = set()
        all_startup_urls: Set[Tuple[str, str]] = set()
        visited_urls = set()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(process_directory, url, university_domains, visited_urls): url
                for url in directory_urls
            }
            for future in as_completed(futures):
                try:
                    lab_urls, startup_urls = future.result()
                    all_lab_urls.update(lab_urls)
                    all_startup_urls.update(startup_urls)
                except Exception as e:
                    url = futures[future]
                    logging.error(f"Error processing {url}: {str(e)}")

        merged_urls = all_lab_urls | all_startup_urls
        logging.info(f"Discovered {len(merged_urls)} unique URLs")
    

        temp_file = 'urls.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            for url, anchor_text in sorted(merged_urls):
                f.write(f"{url}|{anchor_text}\n")  # Store URL and anchor text
        os.replace(temp_file, 'data/urls.txt')
        logging.info("URL generation completed successfully")

    except Exception as e:
        logging.error(f"Critical error in URL generation: {str(e)}")
        raise

### URL Loading Function
def load_seed_urls(file_path: str = 'data/urls.txt') -> List[Tuple[str, str]]:
    """Load URLs with anchor text from file."""
    urls_with_anchor = []
    seen = set()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('|', 1)
                url = parts[0]
                anchor_text = parts[1] if len(parts) > 1 else ""
                if url and is_valid_url(url) and url not in seen:
                    urls_with_anchor.append((url, anchor_text))
                    seen.add(url)
        logging.info(f"Loaded {len(urls_with_anchor)} validated URLs from {file_path}")
        return urls_with_anchor
    except FileNotFoundError:
        logging.error(f"URL file not found: {file_path}")
        return []
