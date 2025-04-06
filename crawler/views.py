# crawler/views.py
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
import json
import os
import logging
import threading
from .models import Entity
from utils.helpers import generate_urls, load_seed_urls, load_config, save_config
from utils.database import create_db
from utils.workflow import State, app
from utils.scheduler import run_workflow, crawler_running 

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
LOG_FILE = os.path.join(BASE_DIR, 'crawler.log')

# Global flag to control crawler execution
crawler_running = False
crawler_thread = None

def index(request):
    return render(request, 'index.html')

def parameters(request):
    if request.method == 'POST':
        config = {
            "REQUEST_TIMEOUT": int(request.POST.get('REQUEST_TIMEOUT')),
            "MAX_WORKERS": int(request.POST.get('MAX_WORKERS')),
            "MAX_DEPTH": int(request.POST.get('MAX_DEPTH')),
            "MAX_URLS": int(request.POST.get('MAX_URLS')),
            "TIMEOUT_SECONDS": int(request.POST.get('TIMEOUT_SECONDS'))
        }
        save_config(config)
        return redirect('parameters')
    config = load_config()
    return render(request, 'parameters.html', {'config': config})

def files(request):
    if request.method == 'POST':
        filename = request.POST.get('filename')
        content = request.POST.get('content')
        if filename != 'urls.txt':
            with open(os.path.join(DATA_DIR, filename), 'w', encoding='utf-8') as f:
                f.write(content)
        return redirect('files')
    
    file_content = {}
    for filename in ['universities.txt', 'potential_directories.txt', 'urls.txt']:
        file_path = os.path.join(DATA_DIR, filename)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content[filename] = f.read()
        except FileNotFoundError:
            file_content[filename] = ''
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                file_content[filename] = f.read()
            logger.warning(f"{filename} contained undecodable characters, replaced with placeholders")
    return render(request, 'files.html', {'file_content': file_content})

def database(request):
    entities = Entity.objects.all()
    return render(request, 'database.html', {'entities': entities})

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
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'})

def run_crawler(request):
    global crawler_running, crawler_thread
    if crawler_running:
        return JsonResponse({'status': 'already_running', 'message': 'Crawler is already running'})
    
    logger.info("Starting crawler...")
    logger.info("Crawler execution started")
    crawler_running = True
    crawler_thread = threading.Thread(target=run_workflow_with_stop, daemon=True)
    crawler_thread.start()
    return JsonResponse({'status': 'started'})

def stop_crawler(request):
    global crawler_running, crawler_thread
    if not crawler_running:
        return JsonResponse({'status': 'not_running', 'message': 'No crawler is running'})
    
    crawler_running = False
    if crawler_thread:
        crawler_thread.join(timeout=5)  # Wait up to 5 seconds for thread to stop
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

# Wrapper for run_workflow to check stop flag
def run_workflow_with_stop():
    global crawler_running
    try:
        run_workflow()
    finally:
        crawler_running = False
        logger.info("Crawler execution completed")