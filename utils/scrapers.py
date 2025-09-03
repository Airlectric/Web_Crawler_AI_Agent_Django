import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import time
import re
import logging
import utils.state
import urllib.parse
import os
from dotenv import load_dotenv
from PIL import Image
import io
from urllib.parse import quote
from pdf2image import convert_from_bytes
import pytesseract

load_dotenv()

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# Platform-specific Tesseract path for Windows
if os.name == 'nt':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


from utils.config import load_config

def is_static(url):
    """Enhanced static detection with better heuristics"""
    if not utils.state.crawler_running_event.is_set():
        logger.info("Scraping stopped by user")
        return False
    
    try:
        response = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
        
        static_servers = {'Netlify', 'Vercel', 'GitHub-Pages', 'S3'}
        if any(server in response.headers.get('Server', '') for server in static_servers):
            return True
            
        response = requests.get(url, headers=HEADERS, timeout=10)
        html = response.text
        
        soup = BeautifulSoup(html, 'html.parser')
        
        meta_generator = soup.find('meta', {'name': 'generator'})
        if meta_generator and any(ssg in meta_generator.get('content', '') 
                                for ssg in ['Jekyll', 'Hugo', 'Gatsby', 'Next.js']):
            return True
            
        script_sources = [script.get('src', '') for script in soup.find_all('script')]
        if any('react' in src or 'vue' in src for src in script_sources):
            return False
            
        return len(html) < 100000 and not soup.find(id='root')
        
    except Exception as e:
        logger.error(f"Error checking {url}: {e}")
        return False

def extract_raw_content(soup):
    """Extract structured content with enhanced context awareness"""
    if not utils.state.crawler_running_event.is_set():
        logger.info("Scraping stopped by user")
        return ""
    
    CONTENT_SELECTORS = [
        'main', 'article', 'section',
        'div.content', 'div.main-content', 'div.container',
        'div.post-content', 'div.entry-content', '[role="main"]'
    ]
    
    NON_CONTENT_SELECTORS = [
        'nav', 'header', 'footer', 'aside',
        '.sidebar', '.ad-container', '.navbar',
        '.menu', '.comments', '.social-links'
    ]
    
    TEXT_ELEMENTS = [
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'p', 'li', 'blockquote', 'pre',
        'figcaption', 'dt', 'dd', 'td', 'th',
        'code', 'q', 'cite'
    ]

    non_content = soup.select(','.join(NON_CONTENT_SELECTORS))
    for element in non_content:
        element.decompose()

    content_containers = []
    for selector in CONTENT_SELECTORS:
        content_containers += soup.select(selector)
    
    if not content_containers and soup.body:
        content_containers = [soup.body]

    container_scores = []
    for container in content_containers:
        score = 0
        if container.name == 'main':
            score += 3
        elif container.name == 'article':
            score += 2
        text_length = len(container.get_text())
        tag_count = len(container.find_all())
        if tag_count > 0:
            text_density = text_length / tag_count
            score += text_density * 0.1
        container_scores.append((container, score))
    
    container_scores.sort(key=lambda x: x[1], reverse=True)
    main_containers = [cs[0] for cs in container_scores[:3]]

    extracted_content = []
    seen_texts = set()
    current_heading = []
    
    for container in main_containers:
        for element in container.find_all(TEXT_ELEMENTS):
            text = ' '.join(element.get_text(' ', strip=True).split())
            if not text:
                continue
                
            text_hash = hash(text.lower().strip())
            if text_hash in seen_texts:
                continue
            seen_texts.add(text_hash)
            
            if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                level = int(element.name[1])
                current_heading = current_heading[:level-1] + [text]
                extracted_content.append(f"\n{'#'*level} {text}")
            else:
                if current_heading:
                    extracted_content.append(f"[Context: {' > '.join(current_heading)}]")
                
                if len(text.split()) >= 3:
                    extracted_content.append(text)
                    
                if element.name == 'li':
                    extracted_content[-1] = f"• {extracted_content[-1]}"
                elif element.name == 'blockquote':
                    extracted_content[-1] = f"> {extracted_content[-1]}"
                elif element.name == 'code':
                    extracted_content[-1] = f"```{extracted_content[-1]}```"
                    
    return '\n'.join(extracted_content)

def process_image_ocr(image_url, base_url):
    """Process an image with Tesseract OCR"""
    if not utils.state.crawler_running_event.is_set():
        logger.info("Scraping stopped by user")
        return ""
    
    config = load_config()
    enable_ocr = config.get('ENABLE_OCR', True)
    ocr_language = config.get('OCR_LANGUAGE', 'eng')
    
    if not enable_ocr:
        logger.info("OCR disabled in configuration")
        return ""
    
    try:
        full_url = urllib.parse.urljoin(base_url, image_url)
        response = requests.get(full_url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            logger.warning(f"Failed to download image {full_url}: Status {response.status_code}")
            return ""
        
        if 'image' not in response.headers.get('Content-Type', '') or len(response.content) < 10240:
            logger.info(f"Skipping small or non-image file: {full_url}")
            return ""
        
        img = Image.open(io.BytesIO(response.content))
        text = pytesseract.image_to_string(img, lang=ocr_language).strip()
        
        if text:
            logger.info(f"OCR extracted text from {full_url}: {text[:50]}...")
            return text
        else:
            logger.info(f"No text found in image {full_url}")
            return ""
            
    except Exception as e:
        logger.error(f"Error processing image {image_url}: {e}")
        return ""

def process_pdf_ocr(pdf_url, base_url):
    """Process a PDF with Tesseract OCR"""
    if not utils.state.crawler_running_event.is_set():
        logger.info("Scraping stopped by user")
        return ""
    
    config = load_config()
    enable_ocr = config.get('ENABLE_OCR', True)
    ocr_language = config.get('OCR_LANGUAGE', 'eng')
    
    if not enable_ocr:
        logger.info("OCR disabled in configuration")
        return ""
    
    try:
        full_url = urllib.parse.urljoin(base_url, pdf_url)
        response = requests.get(full_url, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            logger.warning(f"Failed to download PDF {full_url}: Status {response.status_code}")
            return ""
        
        if 'pdf' not in response.headers.get('Content-Type', ''):
            logger.info(f"Skipping non-PDF file: {full_url}")
            return ""
        
        images = convert_from_bytes(response.content, fmt='png', dpi=300, first_page=1, last_page=5)
        text_parts = []
        
        for i, img in enumerate(images):
            text = pytesseract.image_to_string(img, lang=ocr_language).strip()
            if text:
                text_parts.append(text)
                logger.info(f"OCR extracted text from PDF page {i+1} of {full_url}: {text[:50]}...")
        
        combined_text = '\n'.join(text_parts)
        if combined_text:
            logger.info(f"OCR completed for {full_url}: {len(combined_text)} characters extracted")
        else:
            logger.info(f"No text found in PDF {full_url}")
        
        return combined_text
        
    except Exception as e:
        logger.error(f"Error processing PDF {pdf_url}: {e}")
        return ""

def search_edurank(university_name):
    """Search EduRank.org for the university's score and URL."""
    logger.info(f"Searching EduRank for university: {university_name}")
    try:
        if not university_name:
            logger.warning("No university name provided for EduRank search")
            return None, None
        
        # Try direct university page URL
        slug = university_name.lower().replace(' ', '-').replace('&', 'and')
        direct_url = f"https://edurank.org/uni/{slug}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        logger.info(f"Trying direct EduRank URL: {direct_url}")
        response = requests.get(direct_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            score_tag = soup.find(string=re.compile(r'Ranked #\d+|#\d+', re.I))
            score = re.search(r'#(\d+)', score_tag).group(1) if score_tag else None
            if score:
                logger.info(f"EduRank found: URL={direct_url}, Score={score}")
                return direct_url, score
            logger.info(f"EduRank page found but no score extracted: {direct_url}")
            return direct_url, None
        
        # Fallback to Google search if direct URL fails
        logger.warning(f"Direct EduRank URL failed (Status {response.status_code}): {direct_url}")
        google_query = f"site:edurank.org {university_name}"
        google_url = f"https://www.google.com/search?q={quote(google_query)}"
        logger.info(f"Falling back to Google search: {google_url}")
        response = requests.get(google_url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            logger.warning(f"Google search failed for {university_name}: Status {response.status_code}")
            return None, None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        for link in soup.find_all('a', href=True):
            href = link['href']
            if 'edurank.org/uni/' in href:
                edurank_url = href.split('&')[0]  # Clean URL from Google redirect
                logger.info(f"Found EduRank URL via Google: {edurank_url}")
                response = requests.get(edurank_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    score_tag = soup.find(string=re.compile(r'Ranked #\d+|#\d+', re.I))
                    score = re.search(r'#(\d+)', score_tag).group(1) if score_tag else None
                    logger.info(f"EduRank found: URL={edurank_url}, Score={score}")
                    return edurank_url, score
                logger.warning(f"EduRank URL failed: {edurank_url}")
                return edurank_url, None
        
        logger.info(f"No EduRank results found for {university_name}")
        return None, None
    except Exception as e:
        logger.error(f"Error searching EduRank for {university_name}: {e}")
        return None, None

def search_google_scholar(university_name, department_focus):
    """Search Google Scholar for a research paper related to the university and department focus."""
    logger.info(f"Searching Google Scholar for university: {university_name}, focus: {department_focus}")
    try:
        if not university_name:
            logger.warning("No university name provided for Google Scholar search")
            return None
        query = f"from:{university_name}"
        if department_focus:
            query += f" {department_focus}"
        encoded_query = quote(query)
        search_url = f"https://scholar.google.com/scholar?q={encoded_query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(search_url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.warning(f"Google Scholar search failed for {university_name}: Status {response.status_code}")
            return None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        article = soup.find('div', class_='gs_r gs_or gs_scl')
        if article:
            link = article.find('a', href=True)
            if link and link['href']:
                logger.info(f"Google Scholar found paper: {link['href']}")
                return link['href']
        logger.info(f"No Google Scholar results found for {university_name}")
        return None
    except Exception as e:
        logger.error(f"Error searching Google Scholar for {university_name}: {e}")
        return None

def find_publication_sections(soup):
    """Find sections likely containing publication information."""
    publication_sections = []
    headers = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'], string=re.compile(r'publications|research papers|journal articles', re.I))
    for header in headers:
        section = header.find_parent(['section', 'div'])
        if section:
            publication_sections.append(section)
    divs = soup.find_all('div', class_=re.compile(r'publications|research', re.I))
    publication_sections.extend(divs)
    return publication_sections

def extract_publication_links(section):
    """Extract publication links from a given section."""
    links = []
    for a in section.find_all('a', href=True):
        href = a['href'].lower()
        text = a.get_text(strip=True)
        if 'doi.org' in href or 'scholar.google' in href or 'pubmed' in href or 'ieee' in href or 'acm' in href:
            links.append((href, text))
        elif re.search(r'paper|article|publication|journal', text.lower(), re.I) and not re.search(r'home|about|contact|news', href, re.I):
            links.append((href, text))
    return links

def extract_structured_data(soup, url):
    """Extract structured data, raw content, and OCR content from the soup object."""
    data = {
        'university': '',
        'location': {'country': '', 'city': ''},
        'website': url,
        'edurank': {'url': '', 'score': ''},
        'department': {'name': '', 'url': '', 'teams': {'urls': [], 'members': []}, 'focus': ''},
        'publications': {'google_scholar_url': '', 'other_url': '', 'contents': []},
        'related': '',
        'point_of_contact': {
            'name': '', 'first_name': '', 'last_name': '', 'title': '',
            'bio_url': '', 'linked_in': '', 'google_scholar_url': '', 'email': '', 'phone_number': ''
        },
        'scopes': [],
        'research_abstract': '',
        'lab_equipment': {'overview': '', 'list': []},
        'raw_content': '',
        'ocr_content': []
    }

    # University Name Extraction
    try:
        meta_title = soup.find('meta', attrs={'name': 'title'})
        if meta_title and meta_title.get('content'):
            data['university'] = meta_title['content'].strip()
        if not data['university']:
            title = soup.title.string if soup.title else ''
            if '|' in title:
                data['university'] = title.split('|')[0].strip()
            elif ' - ' in title:
                data['university'] = title.split(' - ')[-1].strip()
            else:
                data['university'] = title.strip()
        if not data['university'] or 'research' in data['university'].lower():
            h1 = soup.find('h1')
            if h1 and 'university' in h1.get_text(strip=True).lower():
                data['university'] = h1.get_text(strip=True)
            else:
                data['university'] = ''
    except Exception as e:
        logger.error(f"Error in university extraction for {url}: {e}")
        data['university'] = ''

    # EduRank Extraction
    try:
        edurank_url, edurank_score = search_edurank(data['university'])
        if edurank_url:
            data['edurank']['url'] = edurank_url
        if edurank_score:
            data['edurank']['score'] = edurank_score
    except Exception as e:
        logger.error(f"Error in EduRank extraction for {url}: {e}")

    # Location Extraction with Schema.org Support
    try:
        schema_tags = soup.find_all('script', type='application/ld+json')
        for tag in schema_tags:
            try:
                schema_data = json.loads(tag.string)
                if 'address' in schema_data:
                    address = schema_data['address']
                    data['location']['city'] = address.get('addressLocality', '')
                    data['location']['country'] = address.get('addressCountry', '')
                    break
            except json.JSONDecodeError:
                continue
        if not data['location']['city']:
            address_tags = soup.find_all(['address', 'div', 'p'], class_=re.compile(r'location|address|contact', re.I))
            for tag in address_tags:
                text = tag.get_text(strip=True)
                if 'country' in text.lower() or ',' in text:
                    parts = text.split(',')
                    if len(parts) >= 2:
                        data['location']['city'] = parts[-2].strip()
                        data['location']['country'] = parts[-1].strip()
                    break
            if not data['location']['country']:
                data['location']['city'] = ''
                data['location']['country'] = ''
    except Exception as e:
        logger.error(f"Error in location extraction for {url}: {e}")

    # Department Extraction
    try:
        dept_tags = soup.find_all(['h2', 'h3', 'div'], string=re.compile(r'department|faculty|school|centre', re.I))
        for tag in dept_tags:
            data['department']['name'] = tag.get_text(strip=True)
            link = tag.find_parent('a', href=True) or tag.find('a', href=True)
            if link and link['href']:
                data['department']['url'] = link['href']
            break
        if not data['department']['name']:
            data['department']['name'] = 'Clean Energy Research Centre'
    except Exception as e:
        logger.error(f"Error in department extraction for {url}: {e}")

    # Department Focus and Scopes
    try:
        content = ' '.join(p.get_text(strip=True) for p in soup.find_all('p'))
        focus_keywords = ['research', 'focus', 'specialize', 'study']
        for keyword in focus_keywords:
            if keyword in content.lower():
                start = content.lower().find(keyword)
                excerpt = content[start:start + 100]
                data['department']['focus'] = excerpt.strip()
                break
        scopes = re.findall(r'\b(?:AI|machine learning|robotics|biology|physics|chemistry|solar|renewable energy|clean energy)\b', content, re.I)
        data['scopes'] = list(set(scopes))
    except Exception as e:
        logger.error(f"Error in department focus and scopes extraction for {url}: {e}")

    # Enhanced Publications Extraction
    try:
        publication_sections = find_publication_sections(soup)
        found_publications = False
        for section in publication_sections:
            pub_links = extract_publication_links(section)
            for link, text in pub_links:
                if 'scholar.google' in link:
                    data['publications']['google_scholar_url'] = link
                else:
                    data['publications']['other_url'] = link
                data['publications']['contents'].append(text)
                found_publications = True
        
        if not found_publications:
            for a in soup.find_all('a', href=True):
                href = a['href'].lower()
                text = a.get_text(strip=True)
                if 'doi.org' in href or 'scholar.google' in href or 'pubmed' in href or 'ieee' in href or 'acm' in href:
                    if 'scholar.google' in href:
                        data['publications']['google_scholar_url'] = href
                    else:
                        data['publications']['other_url'] = href
                    data['publications']['contents'].append(text)
                    found_publications = True
                elif re.search(r'\b(paper|article|publication|journal)\b', text.lower(), re.I) and not re.search(r'home|about|contact|news', href, re.I):
                    data['publications']['other_url'] = href
                    data['publications']['contents'].append(text)
                    found_publications = True
        
        if not found_publications:
            scholar_url = search_google_scholar(data['university'], data['department']['focus'])
            if scholar_url:
                data['publications']['other_url'] = scholar_url
                data['publications']['contents'].append(f"Research paper from {data['university']} related to {data['department']['focus']}")
                logger.info(f"Added Google Scholar paper: {scholar_url}")
    except Exception as e:
        logger.error(f"Error in publications extraction for {url}: {e}")

    # Point of Contact Extraction
    try:
        email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
        phone_pattern = re.compile(r'\+?\d[\d -]{8,}\d')
        contact_links = soup.find_all('a', string=re.compile(r'contact|staff|directory', re.I))
        for tag in soup.find_all(['p', 'div', 'span']):
            text = tag.get_text(strip=True)
            if email := email_pattern.search(text):
                data['point_of_contact']['email'] = email.group(0)
            if phone := phone_pattern.search(text):
                data['point_of_contact']['phone_number'] = phone.group(0)
            if 'dr.' in text.lower() or 'prof.' in text.lower():
                data['point_of_contact']['name'] = text.split(',')[0].strip()
                name_parts = data['point_of_contact']['name'].split()
                if len(name_parts) >= 2:
                    data['point_of_contact']['first_name'] = name_parts[1]
                    data['point_of_contact']['last_name'] = name_parts[-1]
        for link in contact_links:
            if 'linkedin.com' in link['href'].lower():
                data['point_of_contact']['linked_in'] = link['href']
    except Exception as e:
        logger.error(f"Error in point of contact extraction for {url}: {e}")

    # Research Abstract Extraction
    try:
        abstract_sections = soup.find_all(['div', 'section'], class_=re.compile(r'about|research|overview', re.I))
        for section in abstract_sections:
            paragraphs = section.find_all('p')
            for p in paragraphs:
                text = p.get_text(strip=True)
                if len(text) > 100:
                    data['research_abstract'] = text
                    break
            if data['research_abstract']:
                break
        if not data['research_abstract']:
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                if len(text) > 100:
                    data['research_abstract'] = text
                    break
    except Exception as e:
        logger.error(f"Error in research abstract extraction for {url}: {e}")

    # Lab Equipment Extraction
    try:
        equip_tags = soup.find_all(['ul', 'div', 'table'], string=re.compile(r'equipment|lab|facility|instrument|apparatus', re.I))
        for tag in equip_tags:
            data['lab_equipment']['overview'] = tag.get_text(strip=True)[:200]
            if tag.name == 'ul':
                items = tag.find_all('li')
                data['lab_equipment']['list'] = [li.get_text(strip=True) for li in items if li.get_text(strip=True)]
            elif tag.name == 'table':
                rows = tag.find_all('tr')
                data['lab_equipment']['list'] = [row.get_text(strip=True) for row in rows]
            break
    except Exception as e:
        logger.error(f"Error in lab equipment extraction for {url}: {e}")

    # Raw Content Extraction
    try:
        data['raw_content'] = extract_raw_content(soup)
    except Exception as e:
        logger.error(f"Error in raw content extraction for {url}: {e}")

    # OCR Content Extraction
    try:
        images = soup.find_all('img', src=True)
        pdfs = soup.find_all('a', href=re.compile(r'\.pdf$', re.I))
        for img in images:
            img_src = img['src']
            if img_src:
                ocr_text = process_image_ocr(img_src, url)
                if ocr_text:
                    data['ocr_content'].append({'source': img_src, 'type': 'image', 'text': ocr_text})
        for pdf in pdfs:
            pdf_href = pdf['href']
            if pdf_href:
                ocr_text = process_pdf_ocr(pdf_href, url)
                if ocr_text:
                    data['ocr_content'].append({'source': pdf_href, 'type': 'pdf', 'text': ocr_text})
    except Exception as e:
        logger.error(f"Error in OCR extraction for {url}: {e}")

    return data

def scrape_with_bs(url):
    """Enhanced static scraper with structured data, raw content, and OCR extraction"""
    if not utils.state.crawler_running_event.is_set():
        logger.info("Scraping stopped by user")
        return None
    
    config = load_config()
    timeout = config.get('REQUEST_TIMEOUT', 15000) / 1000  # Convert ms to seconds
    logger.info(f"Scraping static website: {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        response.encoding = response.apparent_encoding 
        soup = BeautifulSoup(response.text, 'html.parser')
        data = extract_structured_data(soup, url)
        logger.info(f"Successfully scraped {url} (Fields extracted: {len(data)}, Raw content length: {len(data['raw_content'])}, OCR items: {len(data['ocr_content'])})")
        return data
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        return None

def scrape_with_selenium(url):
    """Enhanced dynamic scraper with structured data, raw content, and OCR extraction"""
    if not utils.state.crawler_running_event.is_set():
        logger.info("Scraping stopped by user")
        return None
    
    logger.info(f"Scraping dynamic website: {url}")
    config = load_config()
    timeout = config.get('REQUEST_TIMEOUT', 15000) / 1000  # Convert ms to seconds
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    
    driver = webdriver.Chrome(options=options)
    try:
        driver.get(url)
        
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, '//body//*[text()]'))
        )
        
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        data = extract_structured_data(soup, url)
        logger.info(f"Successfully scraped {url} (Fields extracted: {len(data)}, Raw content length: {len(data['raw_content'])}, OCR items: {len(data['ocr_content'])})")
        return data
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        return None
    finally:
        driver.quit()