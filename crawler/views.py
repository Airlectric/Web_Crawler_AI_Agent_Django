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
from utils.scheduler import run_workflow
from utils.state import crawler_running_event, crawler_thread
from django.shortcuts import redirect
from django.contrib import messages


logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
LOG_FILE = os.path.join(BASE_DIR, 'crawler.log')

def index(request):
    return render(request, 'index.html')


def parameters(request):
    if request.method == 'POST':
        print("POST received:", request.POST)  # Debug
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
    crawler_running_event.set()  # Set the event to True
    logger.info("Crawler execution started")
    
    # Start the workflow in a new thread
    global crawler_thread
    crawler_thread = threading.Thread(target=run_workflow_with_stop, daemon=True)
    crawler_thread.start()
    logger.info("Crawler thread started")
    
    return JsonResponse({'status': 'started'})

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

# Wrapper for run_workflow to check stop flag
def run_workflow_with_stop():
    try:
        logger.info("Starting run_workflow_with_stop")
        run_workflow()
    finally:
        logger.info("Cleaning up: Clearing crawler_running_event")
        crawler_running_event.clear()
        logger.info("Crawler execution completed or stopped")