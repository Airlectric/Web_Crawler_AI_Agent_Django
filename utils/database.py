# utils/database.py
import json
from crawler.models import Entity  # Import the Entity model

def create_db():
    """Initialize the database (handled by Django migrations)."""
    print("Initializing database...")
    # No need for raw SQL here; Django migrations handle table creation.
    # This function can be a no-op or used for custom initialization if needed.
    print("Database initialized successfully (via Django ORM)")

def store_data(url, extracted_data):
    """Store or update data in the database using the Entity model if it meets criteria."""
    if extracted_data and "error" not in extracted_data:
        # Check if all top-level fields are empty or default
        if all(
            value == "" or value == {} or value == [] 
            for value in extracted_data.values()
        ):
            print(f"Skipping storage for {url}: All fields are empty")
            return
        
        # Try to get an existing entity or create a new one
        try:
            entity, created = Entity.objects.get_or_create(url=url)
            
            # Prepare data for comparison and update
            existing_dict = {
                'university': entity.university or '',
                'location': entity.get_json_field('location'),
                'website': entity.website or '',
                'edurank': entity.get_json_field('edurank'),
                'department': entity.get_json_field('department'),
                'publications': entity.get_json_field('publications'),
                'related': entity.related or '',
                'point_of_contact': entity.get_json_field('point_of_contact'),
                'scopes': entity.get_json_field('scopes'),
                'research_abstract': entity.research_abstract or '',
                'lab_equipment': entity.get_json_field('lab_equipment')
            }

            # Compare with extracted data
            if existing_dict == extracted_data:
                print(f"Skipping storage for {url}: Data is identical to existing")
                return
            
            # Update the entity with new data
            entity.university = extracted_data.get('university', '')
            entity.set_json_field('location', extracted_data.get('location', {}))
            entity.website = extracted_data.get('website', '')
            entity.set_json_field('edurank', extracted_data.get('edurank', {}))
            entity.set_json_field('department', extracted_data.get('department', {}))
            entity.set_json_field('publications', extracted_data.get('publications', {}))
            entity.related = extracted_data.get('related', '')
            entity.set_json_field('point_of_contact', extracted_data.get('point_of_contact', {}))
            entity.set_json_field('scopes', extracted_data.get('scopes', []))
            entity.research_abstract = extracted_data.get('research_abstract', '')
            entity.set_json_field('lab_equipment', extracted_data.get('lab_equipment', {}))
            entity.save()
            
            action = "Inserted" if created else "Updated"
            print(f"{action} data for {url}")

        except Exception as e:
            print(f"Error storing data for {url}: {str(e)}")
    else:
        print(f"No data stored for {url}: {extracted_data.get('error', 'Unknown error')}")

def url_exists_in_db(url: str) -> bool:
    """Check if a URL already exists in the database."""
    try:
        return Entity.objects.filter(url=url).exists()
    except Exception as e:
        print(f"Database error: {str(e)}")
        return False