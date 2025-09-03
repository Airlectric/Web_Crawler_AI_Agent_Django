import logging
import schedule
import time
from utils.database import create_db
from utils.helpers import generate_urls
from utils.workflow import State, app
from langchain_core.runnables.config import RunnableConfig
import utils.state  

logger = logging.getLogger(__name__)

def run_workflow(session, quick_scrape=False, initial_url=None):
    """Execute the LangGraph workflow with stop control, quick scrape option, and error handling."""
    logger.info(f"Starting workflow execution (quick_scrape={quick_scrape}, initial_url={initial_url})")
    logger.info(f"Current crawler_running_event.is_set(): {utils.state.crawler_running_event.is_set()}")

    if not utils.state.crawler_running_event.is_set() and not quick_scrape:
        logger.info("Workflow stopped before starting")
        return
    
    if not quick_scrape:
        try:
            logger.info("Calling generate_urls...")
            generate_urls()
        except Exception as e:
            logger.error(f"Error generating URLs: {e}")
            return
        
        if not utils.state.crawler_running_event.is_set():
            logger.info("Workflow stopped after generating URLs")
            return
        
        logger.info("Calling create_db...")
        create_db()
        if not utils.state.crawler_running_event.is_set():
            logger.info("Workflow stopped after initializing database")
            return
    
    logger.info("Initializing workflow state...")
    initial_state = State(urls=[(initial_url, "initial")] if quick_scrape and initial_url else [], session=session)
    
    try:
        logger.info("Invoking LangGraph workflow...")
        config = RunnableConfig(recursion_limit=500)
        final_state = app.invoke({"urls": [(initial_url, "initial")] if quick_scrape and initial_url else [], "session": session}, config=config)
        logger.info(f"Type of final_state: {type(final_state)}")
        logger.info(f"Content of final_state: {final_state}")
        
        if isinstance(final_state, State):
            status = final_state.status
        elif isinstance(final_state, dict):
            status = final_state.get('status', 'Unknown')
        else:
            logger.warning(f"Unexpected type for final_state: {type(final_state)}")
            status = 'Unknown'
        
        if (utils.state.crawler_running_event.is_set() or quick_scrape):
            logger.info(f"Workflow completed with status: {status}")
        else:
            logger.info("Workflow stopped during execution")
    except Exception as e:
        logger.error(f"Error during workflow execution: {e}")

# Schedule the workflow to run daily at 08:00
schedule.every().day.at("08:00").do(run_workflow)

# Optional: Run the scheduler in a loop (uncomment if needed)
# while True:
#     schedule.run_pending()
#     time.sleep(60)  # Check every minute