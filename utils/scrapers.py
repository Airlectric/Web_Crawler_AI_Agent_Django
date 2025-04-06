import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import time
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

def is_static(url):
    """Enhanced static detection with better heuristics"""
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
        print(f"Error checking {url}: {e}")
        return False

def extract_raw_content(soup):
    """Extract cleaned text from main content sections (headings, paragraphs, list items)"""
    content_sections = []
    for tag in ['main', 'article', 'section', 'div.content', 'div.main-content']:
        content_sections += soup.select(tag)
    
    if not content_sections:
        content_sections = [soup.body]
    
    cleaned_text = []
    for section in content_sections:
        for element in section.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']):
            text = element.get_text(' ', strip=True)
            if len(text) > 20:  
                cleaned_text.append(text)
    
    return '\n'.join(cleaned_text)

def extract_structured_data(soup, url):
    """Extract structured data and raw content from the soup object based on the schema"""
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
        'raw_content': '' 
    }


    title = soup.title.string if soup.title else ''
    data['university'] = title.split('|')[0].strip() if '|' in title else title.strip()
    if not data['university']:
        h1 = soup.find('h1')
        data['university'] = h1.get_text(strip=True) if h1 else ''

    # Location (look for address-like patterns or meta tags)
    address_tags = soup.find_all(['address', 'div', 'p'], class_=re.compile(r'location|address|contact', re.I))
    for tag in address_tags:
        text = tag.get_text(strip=True)
        if 'country' in text.lower() or ',' in text:
            parts = text.split(',')
            if len(parts) >= 2:
                data['location']['city'] = parts[-2].strip()
                data['location']['country'] = parts[-1].strip()
                break

    # Department (look for department-related keywords)
    dept_tags = soup.find_all(['h2', 'h3', 'div'], string=re.compile(r'department|faculty|school', re.I))
    for tag in dept_tags:
        data['department']['name'] = tag.get_text(strip=True)
        link = tag.find_parent('a', href=True)
        if link:
            data['department']['url'] = link['href']
        break

    # Focus and scopes (keywords in content)
    content = ' '.join(p.get_text(strip=True) for p in soup.find_all('p'))
    focus_keywords = ['research', 'focus', 'specialize', 'study']
    for keyword in focus_keywords:
        if keyword in content.lower():
            start = content.lower().find(keyword)
            excerpt = content[start:start + 100]
            data['department']['focus'] = excerpt.strip()
            break
    scopes = re.findall(r'\b(?:AI|machine learning|robotics|biology|physics|chemistry)\b', content, re.I)
    data['scopes'] = list(set(scopes))  # Unique scopes

    # Publications (Google Scholar or publication links)
    for a in soup.find_all('a', href=True):
        href = a['href'].lower()
        if 'scholar.google' in href:
            data['publications']['google_scholar_url'] = href
        elif 'publication' in href or 'research' in href:
            data['publications']['other_url'] = href
        if a.get_text(strip=True).startswith(('Paper:', 'Article:', 'Publication:')):
            data['publications']['contents'].append(a.get_text(strip=True))

    # Point of Contact (email, phone, name)
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

    # Research Abstract (first long paragraph)
    for p in soup.find_all('p'):
        text = p.get_text(strip=True)
        if len(text) > 100:
            data['research_abstract'] = text
            break

    # Lab Equipment (look for equipment or lab sections)
    equip_tags = soup.find_all(['ul', 'div'], string=re.compile(r'equipment|lab|facility', re.I))
    for tag in equip_tags:
        data['lab_equipment']['overview'] = tag.get_text(strip=True)[:200]
        items = tag.find_all('li')
        data['lab_equipment']['list'] = [li.get_text(strip=True) for li in items if li.get_text(strip=True)]
        break

    # Extract raw content for LLM to use
    data['raw_content'] = extract_raw_content(soup)

    return data

def scrape_with_bs(url):
    """Enhanced static scraper with structured data and raw content extraction"""
    print(f"Scraping static website: {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.encoding = response.apparent_encoding 
        soup = BeautifulSoup(response.text, 'html.parser')
        data = extract_structured_data(soup, url)
        print(f"Successfully scraped {url} (Fields extracted: {len(data)}, Raw content length: {len(data['raw_content'])})")
        return data
    except Exception as e:
        print(f"Error scraping {url} with BeautifulSoup: {e}")
        return None

def scrape_with_selenium(url):
    """Enhanced dynamic scraper with structured data and raw content extraction"""
    print(f"Scraping dynamic website: {url}")
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
        print(f"Successfully scraped {url} (Fields extracted: {len(data)}, Raw content length: {len(data['raw_content'])})")
        return data
    except Exception as e:
        print(f"Error scraping {url} with Selenium: {e}")
        return None
    finally:
        driver.quit()