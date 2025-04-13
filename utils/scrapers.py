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

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

OCR_SPACE_API_URL = "https://api.ocr.space/parse/image"
OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY")

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

    # Configuration
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

    # Find and exclude non-content areas
    non_content = soup.select(','.join(NON_CONTENT_SELECTORS))
    for element in non_content:
        element.decompose()

    # Find potential content containers with priority
    content_containers = []
    for selector in CONTENT_SELECTORS:
        content_containers += soup.select(selector)
    
    if not content_containers and soup.body:
        content_containers = [soup.body]

    # Content scoring and selection
    container_scores = []
    for container in content_containers:
        score = 0
        # Score based on container type
        if container.name == 'main':
            score += 3
        elif container.name == 'article':
            score += 2
        # Score based on text density
        text_length = len(container.get_text())
        tag_count = len(container.find_all())
        if tag_count > 0:
            text_density = text_length / tag_count
            score += text_density * 0.1
        container_scores.append((container, score))
    
    # Sort containers by score and take top 3
    container_scores.sort(key=lambda x: x[1], reverse=True)
    main_containers = [cs[0] for cs in container_scores[:3]]

    # Content extraction with structure preservation
    extracted_content = []
    seen_texts = set()
    current_heading = []
    
    for container in main_containers:
        for element in container.find_all(TEXT_ELEMENTS):
            # Clean and normalize text
            text = ' '.join(element.get_text(' ', strip=True).split())
            if not text:
                continue
                
            # Deduplication check
            text_hash = hash(text.lower().strip())
            if text_hash in seen_texts:
                continue
            seen_texts.add(text_hash)
            
            # Context awareness
            if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                level = int(element.name[1])
                current_heading = current_heading[:level-1] + [text]
                extracted_content.append(f"\n{'#'*level} {text}")
            else:
                # Add context heading if available
                if current_heading:
                    extracted_content.append(f"[Context: {' > '.join(current_heading)}]")
                
                # Text quality check (minimum 3 words)
                if len(text.split()) >= 3:
                    extracted_content.append(text)
                    
                # Special handling for different element types
                if element.name == 'li':
                    extracted_content[-1] = f"• {extracted_content[-1]}"
                elif element.name == 'blockquote':
                    extracted_content[-1] = f"> {extracted_content[-1]}"
                elif element.name == 'code':
                    extracted_content[-1] = f"```{extracted_content[-1]}```"
                    
    return '\n'.join(extracted_content)



def process_ocr(url, base_url, file_type='image'):
    """Process an image or PDF with OCR.space API"""
    if not utils.state.crawler_running_event.is_set():
        logger.info("Scraping stopped by user")
        return ""
    
    try:
        # Resolve relative URLs
        full_url = urllib.parse.urljoin(base_url, url)
        # Check file size (skip small images, e.g., <10KB)
        response = requests.head(full_url, headers=HEADERS, timeout=10, allow_redirects=True)
        content_length = int(response.headers.get('Content-Length', 0))
        content_type = response.headers.get('Content-Type', '')
        
        if file_type == 'image' and (content_length < 10240 or 'image' not in content_type):
            logger.info(f"Skipping small or non-image file: {full_url}")
            return ""
        if file_type == 'pdf' and 'pdf' not in content_type:
            logger.info(f"Skipping non-PDF file: {full_url}")
            return ""
        
        # Prepare OCR.space request
        payload = {
            'apikey': OCR_SPACE_API_KEY,
            'url': full_url,
            'language': 'eng',
            'isOverlayRequired': False
        }
        if file_type == 'pdf':
            payload['filetype'] = 'PDF'
        
        response = requests.post(OCR_SPACE_API_URL, data=payload, headers=HEADERS, timeout=15)
        result = response.json()
        
        if result.get('IsErroredOnProcessing', True):
            error_msg = result.get('ErrorMessage', ['Unknown error'])[0]
            logger.error(f"OCR.space error for {full_url}: {error_msg}")
            return ""
        
        text = result['ParsedResults'][0]['ParsedText'].strip()
        if text:
            logger.info(f"OCR extracted text from {full_url}: {text[:50]}...")
            return text
        else:
            logger.info(f"OCR said no text found in {full_url}")
            return ""
            
    except Exception as e:
        logger.error(f"Error processing {file_type} {url}: {e}")
        return ""



def extract_structured_data(soup, url):
    """Extract structured data, raw content, and OCR content from the soup object"""
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
        'ocr_content': []  # New field for OCR-extracted text
    }

    # Existing extraction logic
    title = soup.title.string if soup.title else ''
    data['university'] = title.split('|')[0].strip() if '|' in title else title.strip()
    if not data['university']:
        h1 = soup.find('h1')
        data['university'] = h1.get_text(strip=True) if h1 else ''

    address_tags = soup.find_all(['address', 'div', 'p'], class_=re.compile(r'location|address|contact', re.I))
    for tag in address_tags:
        text = tag.get_text(strip=True)
        if 'country' in text.lower() or ',' in text:
            parts = text.split(',')
            if len(parts) >= 2:
                data['location']['city'] = parts[-2].strip()
                data['location']['country'] = parts[-1].strip()
                break

    dept_tags = soup.find_all(['h2', 'h3', 'div'], string=re.compile(r'department|faculty|school', re.I))
    for tag in dept_tags:
        data['department']['name'] = tag.get_text(strip=True)
        link = tag.find_parent('a', href=True)
        if link:
            data['department']['url'] = link['href']
        break

    content = ' '.join(p.get_text(strip=True) for p in soup.find_all('p'))
    focus_keywords = ['research', 'focus', 'specialize', 'study']
    for keyword in focus_keywords:
        if keyword in content.lower():
            start = content.lower().find(keyword)
            excerpt = content[start:start + 100]
            data['department']['focus'] = excerpt.strip()
            break
    scopes = re.findall(r'\b(?:AI|machine learning|robotics|biology|physics|chemistry)\b', content, re.I)
    data['scopes'] = list(set(scopes))

    for a in soup.find_all('a', href=True):
        href = a['href'].lower()
        if 'scholar.google' in href:
            data['publications']['google_scholar_url'] = href
        elif 'publication' in href or 'research' in href:
            data['publications']['other_url'] = href
        if a.get_text(strip=True).startswith(('Paper:', 'Article:', 'Publication:')):
            data['publications']['contents'].append(a.get_text(strip=True))

    email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    phone_pattern = re.compile(r'\+?\d[\d -]{8,}\d')
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

    for p in soup.find_all('p'):
        text = p.get_text(strip=True)
        if len(text) > 100:
            data['research_abstract'] = text
            break

    equip_tags = soup.find_all(['ul', 'div'], string=re.compile(r'equipment|lab|facility', re.I))
    for tag in equip_tags:
        data['lab_equipment']['overview'] = tag.get_text(strip=True)[:200]
        items = tag.find_all('li')
        data['lab_equipment']['list'] = [li.get_text(strip=True) for li in items if li.get_text(strip=True)]
        break

    # Extract raw content
    data['raw_content'] = extract_raw_content(soup)

    # Extract OCR content from images and PDFs
    images = soup.find_all('img', src=True)
    pdfs = soup.find_all('a', href=re.compile(r'\.pdf$', re.I))
    
    for img in images:
        img_src = img['src']
        if img_src:
            ocr_text = process_ocr(img_src, url, file_type='image')
            if ocr_text:
                data['ocr_content'].append({
                    'source': img_src,
                    'type': 'image',
                    'text': ocr_text
                })
    
    for pdf in pdfs:
        pdf_href = pdf['href']
        if pdf_href:
            ocr_text = process_ocr(pdf_href, url, file_type='pdf')
            if ocr_text:
                data['ocr_content'].append({
                    'source': pdf_href,
                    'type': 'pdf',
                    'text': ocr_text
                })

    return data

def scrape_with_bs(url):
    """Enhanced static scraper with structured data, raw content, and OCR extraction"""
    if not utils.state.crawler_running_event.is_set():
        logger.info("Scraping stopped by user")
        return None
    
    logger.info(f"Scraping static website: {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.encoding = response.apparent_encoding 
        soup = BeautifulSoup(response.text, 'html.parser')
        data = extract_structured_data(soup, url)
        logger.info(f"Successfully scraped {url} (Fields extracted: {len(data)}, Raw content length: {len(data['raw_content'])}, OCR items: {len(data['ocr_content'])})")
        return data
    except Exception as e:
        logger.error(f"Error scraping {url} with BeautifulSoup: {e}")
        return None

def scrape_with_selenium(url):
    """Enhanced dynamic scraper with structured data, raw content, and OCR extraction"""
    if not utils.state.crawler_running_event.is_set():
        logger.info("Scraping stopped by user")
        return None
    
    logger.info(f"Scraping dynamic website: {url}")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    
    driver = webdriver.Chrome(options=options)
    try:
        driver.get(url)
        
        WebDriverWait(driver, 15).until(
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
        logger.error(f"Error scraping {url} with Selenium: {e}")
        return None
    finally:
        driver.quit()