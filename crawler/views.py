import asyncio
from django.shortcuts import render, redirect
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q
import json
import os
import re
import logging
import threading
import time
from asgiref.sync import sync_to_async
from .models import Entity, Session
from utils.helpers import generate_urls, load_seed_urls
from utils.config import load_config, save_config
from utils.database import create_db
from utils.workflow import State, app
from utils.scheduler import run_workflow
from utils.state import crawler_running_event, crawler_thread
from django.contrib import messages
from urllib.parse import urlparse
from duckduckgo_search import DDGS
from groq import Groq


logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
LOG_FILE = os.path.join(BASE_DIR, 'crawler.log')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')

def test_sse(request):
    return render(request, 'test_sse.html')

def index(request):
    active_session = Session.objects.order_by('-start_time').first()
    sessions = Session.objects.all().order_by('-start_time')
    return render(request, 'index.html', {
        'active_session': active_session,
        'sessions': sessions,
        'current_time': timezone.now()
    })

def parameters(request):
    if request.method == 'POST':
        print("POST received:", request.POST) 
        config = {
            "REQUEST_TIMEOUT": int(request.POST.get('REQUEST_TIMEOUT', 15000)),
            "MAX_WORKERS": int(request.POST.get('MAX_WORKERS', 4)),
            "MAX_DEPTH": int(request.POST.get('MAX_DEPTH', 3)),
            "MAX_URLS": int(request.POST.get('MAX_URLS', 100)),
            "TIMEOUT_SECONDS": int(request.POST.get('TIMEOUT_SECONDS', 30)),
            "ENABLE_OCR": 'ENABLE_OCR' in request.POST,  
            "OCR_LANGUAGE": request.POST.get('OCR_LANGUAGE', 'eng')
        }
        save_config(config)
        return redirect('parameters')
    config = load_config()
    return render(request, 'parameters.html', {'config': config})

def search_view(request):
    if request.method == 'POST':
        search_input = request.POST.get('search_input')
        if not search_input:
            return JsonResponse({'error': 'No input provided'}, status=400)

        url_pattern = re.compile(
            r'^(https?:\/\/)?'  # http:// or https://
            r'((([A-Z0-9][A-Z0-9_-]*)(\.[A-Z0-9][A-Z0-9_-]*)+)|'  # domain...
            r'(localhost))'  # or localhost
            r'(:\d+)?'  # optional port
            r'(\/.*)?$', re.IGNORECASE)

        if url_pattern.match(search_input):
            full_url = search_input if search_input.startswith('http') else 'http://' + search_input
            domain = urlparse(full_url).netloc
        else:
            try:
                with DDGS() as ddgs:
                    results = ddgs.text(search_input, max_results=1)
                if results:
                    full_url = results[0]['href']
                    domain = urlparse(full_url).netloc
                else:
                    return JsonResponse({'error': 'No results found'}, status=404)
            except Exception as e:
                return JsonResponse({'error': f'Search failed: {str(e)}'}, status=500)

        domain = domain.lower()
        if domain.startswith('www.'):
            domain = domain[4:]

        universities_file = os.path.join(DATA_DIR, 'universities.txt')
        directories_file = os.path.join(DATA_DIR, 'potential_directories.txt')

        try:
            with open(universities_file, 'w') as uni_file:
                uni_file.write(domain + '\n')
            with open(directories_file, 'w') as dir_file:
                dir_file.write(full_url + '\n')
        except IOError as e:
            return JsonResponse({'error': f'Failed to save to files: {str(e)}'}, status=500)

        return JsonResponse({'success': True, 'domain': domain, 'url': full_url})
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)

def files(request):
    if request.method == 'POST':
        filename = request.POST.get('filename')
        content = request.POST.get('content')
        if filename != 'urls.txt':
            file_path = os.path.join(DATA_DIR, filename)
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                if filename == 'potential_directories.txt':
                    domains = set()
                    url_pattern = re.compile(
                        r'^(https?:\/\/)?'  # http:// or https://
                        r'((([A-Z0-9][A-Z0-9_-]*)(\.[A-Z0-9][A-Z0-9_-]*)+)|'  # domain...
                        r'(localhost))'  # or localhost
                        r'(:\d+)?'  # optional port
                        r'(\/.*)?$', re.IGNORECASE)

                    for line in content.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        if url_pattern.match(line):
                            full_url = line if line.startswith('http') else 'http://' + line
                            domain = urlparse(full_url).netloc.lower()
                            if domain.startswith('www.'):
                                domain = domain[4:]
                            if domain:
                                domains.add(domain)
                        else:
                            logger.warning(f"Invalid URL in potential_directories.txt: {line}")

                    universities_file = os.path.join(DATA_DIR, 'universities.txt')
                    with open(universities_file, 'w', encoding='utf-8') as uni_file:
                        for domain in sorted(domains):
                            uni_file.write(domain + '\n')
            except IOError as e:
                logger.error(f"Failed to write to {filename}: {str(e)}")
                return HttpResponseRedirect(request.path)
        return redirect('files')

    file_content = {}
    for filename in ['universities.txt', 'potential_directories.txt', 'urls.txt']:
        file_path = os.path.join(DATA_DIR, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content[filename] = f.read()
        except FileNotFoundError:
            file_content[filename] = ''
            logger.warning(f"File not found: {filename}")
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                file_content[filename] = f.read()
            logger.warning(f"{filename} contained undecodable characters, replaced with placeholders")
    return render(request, 'files.html', {'file_content': file_content})

def database(request):
    entities = Entity.objects.all()
    return render(request, 'database.html', {'entities': entities})

def session_output(request, session_id):
    try:
        session = Session.objects.get(id=session_id)
        session_entities = session.entities.all()
        return render(request, 'session_output.html', {
            'session_entities': session_entities,
            'session_id': session_id
        })
    except Session.DoesNotExist:
        messages.error(request, f'Session {session_id} not found')
        return redirect('index')

def delete_session_selected(request, session_id):
    if request.method == 'POST':
        ids = request.POST.getlist('ids')
        if not ids:
            messages.error(request, 'No items selected')
            return redirect('session_output', session_id=session_id)
        count, _ = Entity.objects.filter(id__in=ids, session_id=session_id).delete()
        messages.success(request, f'Deleted {count} selected items from session {session_id}')
        return redirect('session_output', session_id=session_id)
    messages.error(request, 'Invalid request method')
    return redirect('session_output', session_id=session_id)

@csrf_exempt
def edit_row(request, id):
    entity = Entity.objects.get(id=id)
    if request.method == 'POST':
        entity.university = request.POST.get('university', '')
        entity.location = json.dumps(json.loads(request.POST.get('location', '{}')))
        entity.website = request.POST.get('website', '')
        entity.edurank = json.dumps(json.loads(request.POST.get('edurank', '{}')))
        entity.department = json.dumps(json.loads(request.POST.get('department', '{}')))
        entity.publications = json.dumps(json.loads(request.POST.get('publications', '{}')))
        entity.related = request.POST.get('related', '')
        entity.point_of_contact = json.dumps(json.loads(request.POST.get('point_of_contact', '{}')))
        entity.scopes = json.dumps(json.loads(request.POST.get('scopes', '[]')))
        entity.research_abstract = request.POST.get('research_abstract', '')
        entity.lab_equipment = json.dumps(json.loads(request.POST.get('lab_equipment', '{}')))
        entity.save()
        return redirect('database')
    return render(request, 'edit_row.html', {'entity': entity})

@csrf_exempt
def delete_row(request, id):
    if request.method == 'POST':
        Entity.objects.get(id=id).delete()
        messages.success(request, f'Successfully deleted row {id}')
        return redirect('database')  
    messages.error(request, 'Invalid request method')
    return redirect('database')

@csrf_exempt
def delete_all(request):
    if request.method == 'POST':
        count, _ = Entity.objects.all().delete()
        messages.success(request, f'Deleted all {count} entries')
        return redirect('database')
    messages.error(request, 'POST required')
    return redirect('database')

@csrf_exempt
def delete_selected(request):
    if request.method == 'POST':
        try:
            payload = json.loads(request.body)
            ids = payload.get('ids', [])
        except (ValueError, TypeError):
            ids = request.POST.getlist('ids')
        
        if not ids:
            messages.error(request, 'No items selected')
            return redirect('database')
        
        count, _ = Entity.objects.filter(id__in=ids).delete()
        messages.success(request, f'Deleted {count} selected items')
        return redirect('database')
    
    messages.error(request, 'Invalid request method')
    return redirect('database')

def run_crawler(request):
    if crawler_running_event.is_set():
        logger.info("Crawler already running, rejecting new request")
        return JsonResponse({'status': 'already_running', 'message': 'Crawler is already running'})
    
    logger.info("Starting crawler...")
    session = Session.objects.create()
    crawler_running_event.set()
    logger.info("Crawler execution started")
    
    global crawler_thread
    crawler_thread = threading.Thread(target=run_workflow_with_stop, args=(session,), daemon=True)
    crawler_thread.start()
    logger.info("Crawler thread started")
    
    return JsonResponse({'status': 'started', 'session_id': session.id})

def get_crawler_state(request):
    is_running = crawler_running_event.is_set()
    return JsonResponse({'is_running': is_running})

def stop_crawler(request):
    logger.info(f"Before stop: crawler_running_event.is_set() = {crawler_running_event.is_set()}")
    if not crawler_running_event.is_set():
        logger.info("No crawler running to stop")
        return JsonResponse({'status': 'not_running', 'message': 'No crawler is running'})
    
    logger.info("Stopping crawler...")
    crawler_running_event.clear()
    logger.info(f"After clear: crawler_running_event.is_set() = {crawler_running_event.is_set()}")

    global crawler_thread
    if crawler_thread:
        crawler_thread.join(timeout=5)
        if crawler_thread.is_alive():
            logger.warning("Crawler thread did not stop within 5 seconds")
        crawler_thread = None
    logger.info("Crawler stopped by user")
    return JsonResponse({'status': 'stopped'})

def get_logs(request):
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            logs = f.readlines()
    except UnicodeDecodeError:
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            logs = f.readlines()
            logger.warning("crawler.log contained non-UTF-8 characters; replaced with placeholders")
    except FileNotFoundError:
        logs = ['No logs available yet.']
    return JsonResponse({'logs': logs})


def run_workflow_with_stop(session, quick_scrape=False, initial_url=None):
    """Execute workflow with stop control."""
    try:
        logger.info(f"Starting run_workflow_with_stop (quick_scrape={quick_scrape}, initial_url={initial_url})")
        run_workflow(session, quick_scrape=quick_scrape, initial_url=initial_url)
    finally:
        if not quick_scrape:
            logger.info("Cleaning up: Clearing crawler_running_event")
            crawler_running_event.clear()
        logger.info("Crawler execution completed or stopped")


async def handle_streaming(quick_data):
    """Handle SSE streaming for quick scrape results only."""
    logger.info(f"Streaming {len(quick_data)} quick scrape results")
    if quick_data:
        for data in quick_data:
            yield f"data: {json.dumps({'status': 'quick_data', 'data': data, 'full_crawl_ongoing': True})}\n\n"
    else:
        yield f"data: {json.dumps({'status': 'quick_data', 'message': 'No quick data available', 'full_crawl_ongoing': True})}\n\n"
    yield f"data: {json.dumps({'status': 'complete', 'message': 'Quick scrape completed', 'full_crawl_ongoing': True})}\n\n"



@csrf_exempt
async def ai_prompt(request):
    """Handle AI-driven prompt processing with quick scrape and background full scrape."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    try:
        payload = json.loads(request.body)
        prompt = payload.get('prompt')
        if not prompt:
            return JsonResponse({'error': 'No prompt provided'}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON payload'}, status=400)

    logger.info(f"Processing AI prompt: {prompt}")

    # Step 1: Parse prompt to extract university name using Groq AI
    client = Groq(api_key=GROQ_API_KEY)
    parse_prompt = f"""
    Extract the university name from the following prompt. Return a JSON object with a 'university' field.
    If no university is mentioned, set 'university' to null.
    Prompt: {prompt}
    Example: {{"university": "Kwame Nkrumah University of Science and Technology"}}
    """
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": parse_prompt}],
            model="llama3-70b-8192"
        )
        completion_text = chat_completion.choices[0].message.content
        logger.debug(f"Raw Groq API response: {completion_text}")

        try:
            prompt_info = json.loads(completion_text)
            university = prompt_info.get('university')
        except json.JSONDecodeError:
            json_matches = re.findall(r'\{.*?\}', completion_text, re.DOTALL)
            if not json_matches:
                raise ValueError("No valid JSON object found in response")
            for json_str in json_matches:
                try:
                    prompt_info = json.loads(json_str)
                    university = prompt_info.get('university')
                    break
                except json.JSONDecodeError:
                    continue
            else:
                raise ValueError("No valid JSON object could be parsed")

        if not university:
            logger.error("No university identified in prompt")
            return JsonResponse({'error': 'No university identified in prompt'}, status=400)

        university_map = {
            "university of knust": "Kwame Nkrumah University of Science and Technology",
            "knust": "Kwame Nkrumah University of Science and Technology",
            "university of ghana": "University of Ghana",
            "legon": "University of Ghana",
            "university of development studies": "University for Development Studies",
            "uds": "University for Development Studies"
        }
        normalized_university = university_map.get(university.lower(), university)
        logger.info(f"Identified university: {university} (normalized: {normalized_university})")
        university = normalized_university
    except Exception as e:
        logger.error(f"Groq API error parsing prompt: {str(e)}")
        return JsonResponse({'error': f'Failed to parse prompt: {str(e)}'}, status=500)

    # Step 2: Check database for relevant information
    keywords = [kw for kw in prompt.lower().split() if kw not in university.lower().split()]
    query = Q(university__icontains=university)
    for keyword in keywords:
        query &= (Q(department__icontains=keyword) | Q(research_abstract__icontains=keyword))

    async def get_entities():
        return await sync_to_async(list)(Entity.objects.filter(query).distinct())

    entities = await get_entities()
    if entities and all(entity.university.lower().find(university.lower()) != -1 for entity in entities):
        logger.info(f"Found {len(entities)} relevant entities for {university}")
        response_data = [
            {
                'university': entity.university,
                'location': json.loads(entity.location),
                'website': entity.website,
                'edurank': json.loads(entity.edurank),
                'department': json.loads(entity.department),
                'publications': json.loads(entity.publications),
                'scopes': json.loads(entity.scopes),
                'research_abstract': entity.research_abstract
            } for entity in entities[:5]
        ]
        return JsonResponse({'status': 'success', 'data': response_data, 'full_crawl_ongoing': False})

    # Step 3: Search for specific URL using the full prompt
    logger.info(f"No relevant data for {university}, searching for URL with prompt: {prompt}")
    search_query = prompt
    try:
        with DDGS() as ddgs:
            results = ddgs.text(search_query, max_results=3)
        if not results:
            logger.warning(f"No URL found for prompt: {prompt}")
            fallback_query = f"{university} official website"
            logger.info(f"Falling back to search: {fallback_query}")
            with DDGS() as ddgs:
                results = ddgs.text(fallback_query, max_results=1)
            if not results:
                logger.error(f"No URL found for {university}")
                return JsonResponse({'error': f'No URL found for {university}'}, status=404)

        university_domain = university.lower().replace(' ', '').replace('university', '')
        full_url = None
        for result in results:
            url = result['href']
            domain = urlparse(url).netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            if university_domain in domain or domain.endswith('.edu.gh'):
                full_url = url
                break

        if not full_url:
            logger.error(f"No URL from {university} domain found in results")
            return JsonResponse({'error': f'No relevant URL found for {university}'}, status=404)

        domain = urlparse(full_url).netloc.lower()
        if domain.startswith('www.'):
            domain = domain[4:]
        logger.info(f"Selected URL: {full_url}, domain: {domain}")
    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {str(e)}")
        return JsonResponse({'error': f'Search failed: {str(e)}'}, status=500)

    # Save to files
    universities_file = os.path.join(DATA_DIR, 'universities.txt')
    directories_file = os.path.join(DATA_DIR, 'potential_directories.txt')
    try:
        with open(universities_file, 'a') as uni_file:
            uni_file.write(domain + '\n')
        with open(directories_file, 'a') as dir_file:
            dir_file.write(full_url + '\n')
    except IOError as e:
        logger.error(f"Failed to save to files: {str(e)}")
        return JsonResponse({'error': f'Failed to save to files: {str(e)}'}, status=500)

    # Step 4: Perform quick scrape of the DuckDuckGo URL
    logger.info(f"Performing quick scrape for URL: {full_url}")
    quick_session = await sync_to_async(Session.objects.create)()
    crawler_running_event.set()
    logger.info(f"Quick scrape started, session ID: {quick_session.id}")
    await sync_to_async(run_workflow_with_stop)(quick_session, quick_scrape=True, initial_url=full_url)
    await asyncio.sleep(1)  # Ensure database commits
    logger.info("Quick scrape completed")
    crawler_running_event.clear()
    logger.info(f"After quick scrape: crawler_running_event.is_set() = {crawler_running_event.is_set()}")

    # Step 5: Check database for quick scrape results
    async def get_quick_entities():
        return await sync_to_async(list)(Entity.objects.filter(session_id=quick_session.id))

    quick_entities = await get_quick_entities()
    quick_data = [
        {
            'university': entity.university,
            'location': json.loads(entity.location),
            'website': entity.website,
            'edurank': json.loads(entity.edurank),
            'department': json.loads(entity.department),
            'publications': json.loads(entity.publications),
            'scopes': json.loads(entity.scopes),
            'research_abstract': entity.research_abstract
        } for entity in quick_entities
    ]
    logger.info(f"Retrieved {len(quick_entities)} quick scrape entities for session {quick_session.id}")

    # Step 6: Start full crawler in background (outside response)
    def start_full_crawler():
        if crawler_running_event.is_set():
            logger.info("Crawler already running, skipping new full crawl")
            return
        logger.info(f"Starting full crawler for {university}")
        full_session = Session.objects.create()
        crawler_running_event.set()
        try:
            run_workflow_with_stop(full_session, quick_scrape=False, initial_url=None)
        except Exception as e:
            logger.error(f"Full crawler failed: {str(e)}")
        finally:
            crawler_running_event.clear()
            logger.info(f"Full crawler session {full_session.id} completed")

    threading.Thread(target=start_full_crawler, daemon=True).start()

    # Step 7: Handle response based on client capabilities
    accept_header = request.headers.get('Accept', '')
    is_sse_supported = 'text/event-stream' in accept_header
    logger.debug(f"Accept header: {accept_header}, SSE supported: {is_sse_supported}")

    if is_sse_supported:
        logger.info("Client supports SSE, streaming quick scrape results")
        response = StreamingHttpResponse(
            handle_streaming(quick_data),
            content_type='text/event-stream'
        )
        response['X-Accel-Buffering'] = 'no'  # Disable buffering in Nginx (if used)
        response['Cache-Control'] = 'no-cache'
        return response
    else:
        logger.info("Client does not support SSE, returning quick data in JSON")
        if quick_data:
            return JsonResponse({'status': 'success', 'data': quick_data[:5], 'full_crawl_ongoing': True})
        logger.warning("No quick data available")
        return JsonResponse({
            'status': 'timeout',
            'message': 'No quick data available, full crawl in progress',
            'full_crawl_ongoing': True
        }, status=200)

def test_sse(request):
    """Render the SSE test page."""
    return render(request, 'test_sse.html')