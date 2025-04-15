import os
import json

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'data/config.json')
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found at {config_path}")
    
    with open(config_path, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")

def save_config(config):
    config_path = os.path.join(os.path.dirname(__file__), '..', 'data/config.json')
    
    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=4)  
    except TypeError as e:
        raise ValueError(f"Config contains non-serializable data: {e}")
    except FileNotFoundError:
        raise FileNotFoundError(f"Directory does not exist for config path: {config_path}")
    except PermissionError:
        raise PermissionError(f"Permission denied when writing to config file at: {config_path}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error while saving config: {e}")