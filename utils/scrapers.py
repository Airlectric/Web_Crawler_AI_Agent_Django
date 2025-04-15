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
        'ocr_content': []
    }

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

    data['raw_content'] = extract_raw_content(soup)

    images = soup.find_all('img', src=True)
    pdfs = soup.find_all('a', href=re.compile(r'\.pdf$', re.I))
    
    for img in images:
        img_src = img['src']
        if img_src:
            ocr_text = process_image_ocr(img_src, url)
            if ocr_text:
                data['ocr_content'].append({
                    'source': img_src,
                    'type': 'image',
                    'text': ocr_text
                })
    
    for pdf in pdfs:
        pdf_href = pdf['href']
        if pdf_href:
            ocr_text = process_pdf_ocr(pdf_href, url)
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