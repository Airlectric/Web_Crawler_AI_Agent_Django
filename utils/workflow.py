from langgraph.graph import StateGraph, END
from pydantic import BaseModel
from typing import List, Optional, Tuple
from utils.helpers import load_seed_urls
from utils.scrapers import is_static, scrape_with_bs, scrape_with_selenium
from utils.extractors import extract_info_with_llm
from utils.database import store_data, url_exists_in_db
from utils.online_crawler_model import OnlineLearningCrawler
import random

online_learner = OnlineLearningCrawler(model_file='models/online_model.pkl', scaler_file='models/scaler.pkl')

class State(BaseModel):
    urls: List[Tuple[str, str]]
    index: int = 0
    current_url: Optional[str] = None
    is_static: Optional[bool] = None
    scraped_data: Optional[dict] = None  # Changed to dict to match extract_info_with_llm input
    extracted_data: Optional[dict] = None
    status: str = "starting"
    errors: List[str] = []

def initialize(state: State) -> State:
    """Initialize the workflow by loading URLs."""
    print("Initializing workflow...")
    state.urls = load_seed_urls()
    state.index = 0
    state.status = "initialized"
    print(f"Workflow initialized with {len(state.urls)} URLs")
    return state

def check_urls(state: State) -> State:
    """Check if there are more URLs to process, predict relevance, and skip duplicates or low-relevance URLs."""
    print(f"Checking URLs (index {state.index}/{len(state.urls)})...")
    if state.index >= len(state.urls):
        state.status = "finished"
        return state
    url, anchor_text = state.urls[state.index]
    if url_exists_in_db(url):
        print(f"Skipping duplicate URL: {url}")
        state.index += 1
        return state
    prob = online_learner.predict(url, anchor_text, parent_relevance=1)
    trained_enough = online_learner.total_updates >= 100
    if not trained_enough or prob > 0.5 or random.random() < 0.3:
        state.current_url = url
        state.status = "processing"
        print(f"Processing URL: {url} (prob: {prob:.2f})")
    else:
        print(f"Skipping URL: {url} (low relevance: {prob:.2f})")
        state.index += 1
    return state



def detect_type(state: State) -> State:
    """Detect website type with error handling."""
    try:
        print(f"Detecting type for: {state.current_url}")
        state.is_static = is_static(state.current_url)
    except Exception as e:
        state.errors.append(f"Detection failed: {str(e)}")
        state.status = "error"
    return state

def scrape(state: State) -> State:
    """Scrape content with error handling."""
    try:
        if state.is_static:
            state.scraped_data = scrape_with_bs(state.current_url)
        else:
            state.scraped_data = scrape_with_selenium(state.current_url)
        if not state.scraped_data:
            raise ValueError("No content extracted")
    except Exception as e:
        state.errors.append(f"Scraping failed: {str(e)}")
        state.status = "error"
    return state

def extract_data(state: State) -> State:
    """Data extraction with error propagation."""
    try:
        if state.scraped_data:
            state.extracted_data = extract_info_with_llm(state.scraped_data)
            if "error" in state.extracted_data:
                raise ValueError(state.extracted_data["error"])
    except Exception as e:
        state.errors.append(f"Extraction failed: {str(e)}")
        state.status = "error"
    return state

def store_data_node(state: State) -> State:
    """Store data with transaction handling, skipping if all fields are empty."""
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

        # Check if extracted_data matches the empty template
        if extracted_data_no_id == empty_template_no_id:
            print(f"Skipping storage for {state.current_url}: All fields are empty")
        else:
            try:
                store_data(state.current_url, state.extracted_data)
            except Exception as e:
                state.errors.append(f"Storage failed: {str(e)}")
                state.status = "error"
    else:
        print(f"Skipping storage for {state.current_url}: Invalid data or error - {state.extracted_data}")
    return state

def update_model(state: State) -> State:
    """Update the model based on whether key fields are meaningfully populated."""
    if state.current_url:
        url, anchor_text = state.urls[state.index]
        parent_relevance = state.parent_relevance if hasattr(state, 'parent_relevance') else 0.5

        # Define an empty template to compare against
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
        else:
            # Remove 'id' from comparison
            extracted_data_no_id = {k: v for k, v in state.extracted_data.items() if k != "id"}
            empty_template_no_id = {k: v for k, v in empty_template.items() if k != "id"}

            # Check if extracted_data is all empty
            if extracted_data_no_id == empty_template_no_id:
                label = 0
                populated_fields = 0
            else:
                # Check key fields for meaningful content
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

        online_learner.update_model(state.current_url, anchor_text, parent_relevance=parent_relevance, label=label)
        print(f"Updated model for {url}: label={label}, populated_fields={populated_fields}")

    return state

def increment_index(state: State) -> State:
    """Move to next URL with state cleanup."""
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
    """Conditional routing logic after URL check."""
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