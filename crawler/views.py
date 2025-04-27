from django.shortcuts import render, redirect
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import json
import os
import re
import logging
import threading
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



logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
LOG_FILE = os.path.join(BASE_DIR, 'crawler.log')

# def index(request):
#     sessions = Session.objects.order_by('-start_time')[:5]  # Get 5 most recent sessions
#     return render(request, 'index.html', {'sessions': sessions})

def index(request):
    # Get the most recent session as the active session
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

        # Check if input is a valid URL
        url_pattern = re.compile(
            r'^(https?:\/\/)?'  # http:// or https://
            r'((([A-Z0-9][A-Z0-9_-]*)(\.[A-Z0-9][A-Z0-9_-]*)+)|'  # domain...
            r'(localhost))'  # or localhost
            r'(:\d+)?'  # optional port
            r'(\/.*)?$', re.IGNORECASE)

        if url_pattern.match(search_input):
            # It's a valid URL
            full_url = search_input if search_input.startswith('http') else 'http://' + search_input
            domain = urlparse(full_url).netloc
        else:
            # Not a URL, search DuckDuckGo using DDGS
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

        # Remove 'www.' prefix from domain if present
        domain = domain.lower()
        if domain.startswith('www.'):
            domain = domain[4:]

        # Save to files (overwrite mode)
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
                
                # If updating potential_directories.txt, extract domains and overwrite universities.txt
                if filename == 'potential_directories.txt':
                    domains = set()  # Use set to avoid duplicates
                    url_pattern = re.compile(
                        r'^(https?:\/\/)?'  # http:// or https://
                        r'((([A-Z0-9][A-Z0-9_-]*)(\.[A-Z0-9][A-Z0-9_-]*)+)|'  # domain...
                        r'(localhost))'  # or localhost
                        r'(:\d+)?'  # optional port
                        r'(\/.*)?$', re.IGNORECASE)

                    # Process each line as a potential URL
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

                    # Overwrite universities.txt with extracted domains
                    universities_file = os.path.join(DATA_DIR, 'universities.txt')
                    with open(universities_file, 'w', encoding='utf-8') as uni_file:
                        for domain in sorted(domains):  # Sort for consistency
                            uni_file.write(domain + '\n')
            except IOError as e:
                logger.error(f"Failed to write to {filename}: {str(e)}")
                return HttpResponseRedirect(request.path)  # Redirect even on error to avoid form resubmission
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
    session = Session.objects.create()  # Create a new session
    crawler_running_event.set()  # Set the event to True
    logger.info("Crawler execution started")
    
    # Start the workflow in a new thread, passing the session
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
        crawler_thread = None  # Reset the thread reference
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

# Wrapper for run_workflow to check stop flag and associate with session
def run_workflow_with_stop(session):
    try:
        logger.info("Starting run_workflow_with_stop")
        run_workflow(session)  # Pass session to workflow
    finally:
        logger.info("Cleaning up: Clearing crawler_running_event")
        crawler_running_event.clear()
        logger.info("Crawler execution completed or stopped")