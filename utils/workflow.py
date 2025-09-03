from langgraph.graph import StateGraph, END
from pydantic import BaseModel
from typing import List, Optional, Tuple
from asgiref.sync import sync_to_async
from utils.helpers import load_seed_urls
from utils.scrapers import is_static, scrape_with_bs, scrape_with_selenium
from utils.extractors import extract_info_with_llm
from utils.database import store_data, url_exists_in_db
from utils.online_crawler_model import OnlineLearningCrawler
import random
import logging

logger = logging.getLogger(__name__)
online_learner = OnlineLearningCrawler(model_file='models/online_model.pkl', scaler_file='models/scaler.pkl')

class State(BaseModel):
    urls: List[Tuple[str, str]]
    index: int = 0
    current_url: Optional[str] = None
    is_static: Optional[bool] = None
    scraped_data: Optional[dict] = None 
    extracted_data: Optional[dict] = None
    status: str = "starting"
    errors: List[str] = []
    session: Optional[object] = None
    quick_scrape: bool = False  # Track quick scrape mode

def initialize(state: State) -> State:
    logger.info("Initializing workflow...")
    if state.urls:
        logger.info(f"Using provided URLs: {state.urls}")
    else:
        state.urls = load_seed_urls()
        logger.info(f"Loaded {len(state.urls)} seed URLs")
    state.index = 0
    state.status = "initialized"
    logger.info(f"Workflow initialized with {len(state.urls)} URLs")
    return state

def check_urls(state: State) -> State:
    logger.info(f"Checking URLs (index {state.index}/{len(state.urls)})")
    if state.index >= len(state.urls):
        logger.info("No more URLs to process, setting status to finished")
        state.status = "finished"
        return state
    url, anchor_text = state.urls[state.index]
    logger.debug(f"Processing URL: {url}, anchor_text: {anchor_text}")
    if not state.quick_scrape:  # Skip DB check for quick scrape
        try:
            # Run url_exists_in_db synchronously
            exists = url_exists_in_db(url)
            if exists:
                logger.info(f"Skipping duplicate URL: {url}")
                state.index += 1
                return state
        except Exception as e:
            logger.error(f"Error checking URL {url} in database: {str(e)}")
            state.errors.append(f"DB check failed for {url}: {str(e)}")
            state.status = "error"
            state.index += 1
            return state
    # In quick_scrape mode, always select the URL
    prob = online_learner.predict(url, anchor_text, parent_relevance=1)
    trained_enough = online_learner.total_updates >= 100
    logger.debug(f"URL {url} relevance prob: {prob:.2f}, trained_enough: {trained_enough}")
    state.current_url = url
    state.status = "processing"
    logger.info(f"Selected URL: {url} (prob: {prob:.2f})")
    return state

def detect_type(state: State) -> State:
    logger.info(f"Detecting type for URL: {state.current_url}")
    try:
        state.is_static = is_static(state.current_url)
        logger.debug(f"URL {state.current_url} is_static: {state.is_static}")
    except Exception as e:
        error_msg = f"Detection failed for {state.current_url}: {str(e)}"
        logger.error(error_msg)
        state.errors.append(error_msg)
        state.status = "error"
    return state

def scrape(state: State) -> State:
    logger.info(f"Scraping URL: {state.current_url}, is_static: {state.is_static}")
    try:
        if state.is_static:
            state.scraped_data = scrape_with_bs(state.current_url)
        else:
            state.scraped_data = scrape_with_selenium(state.current_url)
        if not state.scraped_data:
            # Fallback to BeautifulSoup if Selenium fails
            logger.warning(f"Selenium failed for {state.current_url}, trying BeautifulSoup")
            state.scraped_data = scrape_with_bs(state.current_url)
        if not state.scraped_data:
            error_msg = f"No content extracted for {state.current_url}"
            logger.warning(error_msg)
            raise ValueError(error_msg)
        logger.debug(f"Scraped data for {state.current_url}: {state.scraped_data}")
    except Exception as e:
        error_msg = f"Scraping failed for {state.current_url}: {str(e)}"
        logger.error(error_msg)
        state.errors.append(error_msg)
        state.status = "error"
        state.scraped_data = None
    return state

def extract_data(state: State) -> State:
    logger.info(f"Extracting data for URL: {state.current_url}")
    logger.debug(f"Input scraped_data: {state.scraped_data}")
    try:
        if state.scraped_data:
            state.extracted_data = extract_info_with_llm(state.scraped_data)
            logger.debug(f"Extracted data: {state.extracted_data}")
            if state.extracted_data is None:
                error_msg = f"extract_info_with_llm returned None for {state.current_url}"
                logger.warning(error_msg)
                raise ValueError(error_msg)
            if "error" in state.extracted_data:
                error_msg = f"LLM extraction error for {state.current_url}: {state.extracted_data['error']}"
                logger.warning(error_msg)
                raise ValueError(error_msg)
        else:
            error_msg = f"No scraped data available for {state.current_url}"
            logger.warning(error_msg)
            raise ValueError(error_msg)
    except Exception as e:
        error_msg = f"Extraction failed for {state.current_url}: {str(e)}"
        logger.error(error_msg)
        state.errors.append(error_msg)
        state.status = "error"
        state.extracted_data = None
    return state

def store_data_node(state: State) -> State:
    logger.info(f"Attempting to store data for URL: {state.current_url}")
    logger.debug(f"Extracted data: {state.extracted_data}")
    if state.extracted_data and "error" not in state.extracted_data:
        empty_template = {
            "id": 0,
            "university": "",
            "location": {"country": "", "city": ""},
            "website": "",
            "edurank": {"url": "", "score": ""},
            "department": {"name": "", "url": "", "teams": {"urls": [], "members": []}, "focus": ""},
            "publications": {"google_scholar_url": "", "other_url": "", "contents": []},
            "related": "",
            "point_of_contact": {"name": "", "first_name": "", "last_name": "", "title": "", "bio_url": "", "linked_in": "", "google_scholar_url": "", "email": "", "phone_number": ""},
            "scopes": [],
            "research_abstract": "",
            "lab_equipment": {"overview": "", "list": []}
        }
        extracted_data_no_id = {k: v for k, v in state.extracted_data.items() if k != "id"}
        empty_template_no_id = {k: v for k, v in empty_template.items() if k != "id"}
        logger.debug(f"Comparing extracted_data_no_id: {extracted_data_no_id}")
        logger.debug(f"Against empty_template_no_id: {empty_template_no_id}")
        if extracted_data_no_id == empty_template_no_id:
            logger.info(f"Skipping storage for {state.current_url}: All fields are empty")
        else:
            try:
                logger.info(f"Calling store_data with URL: {state.current_url}, session: {state.session}")
                store_data(state.current_url, state.extracted_data, session=state.session)
                logger.info(f"Successfully stored data for {state.current_url}")
            except Exception as e:
                error_msg = f"Storage failed for {state.current_url}: {str(e)}"
                logger.error(error_msg)
                state.errors.append(error_msg)
                state.status = "error"
    else:
        error_msg = f"Skipping storage for {state.current_url}: Invalid data or error - {state.extracted_data}"
        logger.warning(error_msg)
    return state

def update_model(state: State) -> State:
    logger.info(f"Updating model for URL: {state.current_url}")
    if state.current_url:
        url, anchor_text = state.urls[state.index]
        parent_relevance = state.parent_relevance if hasattr(state, 'parent_relevance') else 0.5
        empty_template = {
            "id": 0,
            "university": "",
            "location": {"country": "", "city": ""},
            "website": "",
            "edurank": {"url": "", "score": ""},
            "department": {"name": "", "url": "", "teams": {"urls": [], "members": []}, "focus": ""},
            "publications": {"google_scholar_url": "", "other_url": "", "contents": []},
            "related": "",
            "point_of_contact": {"name": "", "first_name": "", "last_name": "", "title": "", "bio_url": "", "linked_in": "", "google_scholar_url": "", "email": "", "phone_number": ""},
            "scopes": [],
            "research_abstract": "",
            "lab_equipment": {"overview": "", "list": []}
        }
        if state.status == "error" or not state.extracted_data or "error" in state.extracted_data:
            label = 0
            populated_fields = 0
            logger.debug(f"Model update: label=0 due to error or no data")
        else:
            extracted_data_no_id = {k: v for k, v in state.extracted_data.items() if k != "id"}
            empty_template_no_id = {k: v for k, v in empty_template.items() if k != "id"}
            if extracted_data_no_id == empty_template_no_id:
                label = 0
                populated_fields = 0
                logger.debug(f"Model update: label=0, data is empty")
            else:
                def is_populated(field):
                    if field == 'publications':
                        pubs = state.extracted_data.get(field, {})
                        return any([
                            pubs.get('google_scholar_url', "") != "",
                            pubs.get('other_url', "") != "",
                            len(pubs.get('contents', [])) > 0
                        ])
                    elif field == 'scopes':
                        return len(state.extracted_data.get(field, [])) > 0
                    elif field == 'lab_equipment':
                        equip = state.extracted_data.get(field, {})
                        return bool(equip.get('overview', "") != "") or len(equip.get('list', [])) > 0
                    elif field == 'research_abstract':
                        return state.extracted_data.get(field, "") != ""
                    return False
                key_fields = ['publications', 'scopes', 'lab_equipment', 'research_abstract']
                populated_fields = sum(1 for field in key_fields if is_populated(field))
                label = 1 if populated_fields >= 1 else 0
                logger.debug(f"Model update: label={label}, populated_fields={populated_fields}")
        online_learner.update_model(state.current_url, anchor_text, parent_relevance=parent_relevance, label=label)
        logger.info(f"Updated model for {url}: label={label}, populated_fields={populated_fields}")
    return state

def increment_index(state: State) -> State:
    logger.info(f"Incrementing index to {state.index + 1} for URL: {state.current_url}")
    state.index += 1
    state.current_url = None
    state.is_static = None
    state.scraped_data = None
    state.extracted_data = None
    state.status = "processing"
    return state

graph = StateGraph(State)
graph.add_node("initialize", initialize)
graph.add_node("check_urls", check_urls)
graph.add_node("detect_type", detect_type)
graph.add_node("scrape", scrape)
graph.add_node("extract_data", extract_data)
graph.add_node("store_data", store_data_node)
graph.add_node("update_model", update_model)
graph.add_node("increment_index", increment_index)
graph.set_entry_point("initialize")
graph.add_edge("initialize", "check_urls")

def route_after_check(state: State):
    logger.debug(f"Routing after check_urls, status: {state.status}, current_url: {state.current_url}")
    if state.status == "finished":
        return END
    if state.status == "error" or state.current_url is None:
        return "increment_index"
    return "detect_type"

graph.add_conditional_edges(
    "check_urls",
    route_after_check,
    {"detect_type": "detect_type", "increment_index": "increment_index", END: END}
)
graph.add_edge("detect_type", "scrape")
graph.add_edge("scrape", "extract_data")
graph.add_edge("extract_data", "store_data")
graph.add_edge("store_data", "update_model")
graph.add_edge("update_model", "increment_index")
graph.add_edge("increment_index", "check_urls")
app = graph.compile()