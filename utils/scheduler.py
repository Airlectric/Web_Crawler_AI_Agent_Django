import logging
import schedule
import time
from utils.database import create_db
from utils.helpers import generate_urls
from utils.workflow import State, app
from langchain_core.runnables.config import RunnableConfig

logger = logging.getLogger(__name__)

# Global flag for stopping the crawler (from new version)
crawler_running = True  # Default to True, controlled by views.py

def run_workflow():
    """Execute the LangGraph workflow with stop control and error handling."""
    global crawler_running
    logger.info("Starting workflow execution...")
    if not crawler_running:
        logger.info("Workflow stopped before starting")
        return
    
    try:
        generate_urls()
    except Exception as e:
        logger.error(f"Error generating URLs: {e}")
        return
    
    if not crawler_running:
        logger.info("Workflow stopped after generating URLs")
        return
    
    create_db()
    if not crawler_running:
        logger.info("Workflow stopped after initializing database")
        return
    
    initial_state = State(urls=[])
    
    try:
        config = RunnableConfig(recursion_limit=500)  # From old version
        final_state = app.invoke(initial_state, config=config)
        logger.info(f"Type of final_state: {type(final_state)}")
        logger.info(f"Content of final_state: {final_state}")
        
        if isinstance(final_state, State):
            status = final_state.status
        elif isinstance(final_state, dict):
            status = final_state.get('status', 'Unknown')
        else:
            logger.warning(f"Unexpected type for final_state: {type(final_state)}")
            status = 'Unknown'
        
        if crawler_running:
            logger.info(f"Workflow completed with status: {status}")
        else:
            logger.info("Workflow stopped during execution")
    except Exception as e:
        logger.error(f"Error during workflow execution: {e}")

# Schedule the workflow to run daily at 08:00 (from old version)
schedule.every().day.at("08:00").do(run_workflow)

# Optional: Run the scheduler in a loop (uncomment if needed)
# while True:
#     schedule.run_pending()
#     time.sleep(60)  # Check every minute