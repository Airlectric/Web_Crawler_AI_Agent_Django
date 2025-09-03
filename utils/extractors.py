import os
import logging
import json
from dotenv import load_dotenv
from google import genai
from groq import Groq

# Load environment variables
load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)

# API keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

def extract_info_with_llm(data: dict):
    """Use Groq API as primary and Google Gemini as backup to extract structured data."""
    print("Extracting information with LLM...")

    if not GROQ_API_KEY:
        print("Error: GROQ_API_KEY is not set")
        return {"error": "GROQ_API_KEY is not set"}
    if not GOOGLE_API_KEY:
        print("Warning: GOOGLE_API_KEY is not set; Google Gemini backup will not work")

    # Prepare pre-extracted and raw content for the prompt
    pre_extracted = ""
    for key, value in data.items():
        if key != 'raw_content':
            if isinstance(value, dict):
                value_str = ', '.join([f"{k}: {v}" for k, v in value.items() if v])
                pre_extracted += f"- {key}: {value_str}\n"
            elif isinstance(value, list):
                pre_extracted += f"- {key}: {', '.join(value)}\n"
            else:
                pre_extracted += f"- {key}: {value}\n"

    raw_content = data.get('raw_content', '')
    ocr_content = data.get('ocr_content', [])

    logger.info(f"Pre-extracted content: {pre_extracted}")
    logger.info(f"Raw content: {raw_content}")
    logger.info(f"OCR content: {ocr_content}")
    

    prompt = f"""
**TOP PRIORITY**:  
If **neither** the pre‑extracted data nor the raw content clearly pertains to a science or engineering university research lab, its research activities, or a relevant startup—especially one matching UNLOKINNO’s focus on Global South\
climate‑tech labs or green‑tech startups—you **must** return a JSON object with **all fields blank or empty**. This preserves data integrity and prevents irrelevant entries.

You are an expert data extractor. You are given three inputs for a single webpage:  
1. **`pre_extracted`** — data already pulled by upstream processes  
2. **`raw_content`** — the full HTML/text of the page 
3. **`ocr_content`** — content from images on the webpage 

Your output must be **one** JSON object that **exactly** follows the schema below.

### Task:
Create a JSON object with the following fields:
- **"university"**: Use the pre-extracted value if it identifies a university; otherwise, infer from raw content if a university is mentioned (e.g., "at MIT" or "University of X") or strongly implied (e.g., "MIT research" suggests MIT).
- **"location"**: Object with "country" and "city". Use pre-extracted values if available; otherwise, infer from raw content (e.g., "located in New York, USA") or deduce from the university (e.g., MIT → Cambridge, USA).
- **"website"**: Use the pre-extracted URL; if missing, infer a likely main website from raw content (e.g., URLs ending in .edu or .org) or deduce from the university (e.g., MIT → "mit.edu").
- **"edurank"**: Object with "url" (EduRank URL) and "score". Fill if EduRank is mentioned; otherwise, leave empty unless context strongly suggests a ranking source.
- **"department"**: Object with "name", "url", "teams" (object with "urls" and "members" arrays), and "focus". Use pre-extracted values or infer from raw content if a department, teams, or focus area (e.g., "AI Lab" or "machine learning") is suggested.
- **"publications"**: Object with "google_scholar_url", "other_url", and "contents" (array of publication details). Use pre-extracted values or extract from raw content if URLs or publication titles are present or implied.
- **"related"**: Include related entities (e.g., collaborating institutions) if mentioned or reasonably inferred from context.
- **"point_of_contact"**: Object with "name", "first_name", "last_name", "title", "bio_url", "linked_in", "google_scholar_url", "email", and "phone_number". Use pre-extracted values or infer from raw content if a person’s details (e.g., "Dr. John Doe, jdoe@university.edu") are present or suggested.
- **"scopes"**: Array of research scopes (e.g., "AI", "robotics"). Use pre-extracted values or identify from raw content based on mentioned or implied research areas.
- **"research_abstract"**: Provide a concise summary (5-6 sentences) of research activities based on raw content, even if briefly mentioned, or infer from context if research is implied.
- **"lab_equipment"**: Object with "overview" (short description) and "list" (array of equipment). List equipment mentioned in raw content (e.g., "microscopes") or infer plausible equipment based on research context (e.g., "AI research" might suggest "computing clusters").

### Instructions:
1. **Strict Schema**: Output must be valid JSON matching the exact field names and types—no extra or missing fields.  
2. **UNLOKINNO Focus**: Only labs in the offering climatetech/new‑materials services or green‑tech startups. Discard generic or unrelated pages.  
3. **Evidence‑Based**: Fill a field **only** when there is explicit evidence in `pre_extracted` or `raw_content`. Otherwise set to `""`, `null`, `[]`, or `{{}}`.  
4. **Minimal Inference**: Infer missing values **only** when context is strong (e.g. a known university’s location). Do **not** fabricate details.  
5. **Precedence**: Always prefer `pre_extracted` data for accuracy; supplement from `raw_content` only as needed.  
6. **Single Object**: Return exactly one JSON object per page—never arrays or multiple objects.

### Schema (all fields must be present, with correct types):
{{
    "id": 0,  
    "university": "",
    "location": {{
        "country": "",
        "city": ""
    }},
    "website": "",
    "edurank": {{
        "url": "",
        "score": ""
    }},
    "department": {{
        "name": "",
        "url": "",
        "teams": {{
            "urls": [],
            "members": []
        }},
        "focus": ""
    }},
    "publications": {{
        "google_scholar_url": "",
        "other_url": "",
        "contents": []
    }},
    "related": "",
    "point_of_contact": {{
        "name": "",
        "first_name": "",
        "last_name": "",
        "title": "",
        "bio_url": "",
        "linked_in": "",
        "google_scholar_url": "",
        "email": "",
        "phone_number": ""
    }},
    "scopes": [],
    "research_abstract": "",
    "lab_equipment": {{
        "overview": "",
        "list": []
    }}
}}

### Detailed Example:
Below is an example output if the webpage were clearly about Stanford University's Robotics Department. Use this strictly as a format reference ONLY; do not infer extra details.
{{
    "id": 1,
    "university": "Stanford University",
    "location": {{
        "country": "USA",
        "city": "Stanford"
    }},
    "website": "https://www.stanford.edu",
    "edurank": {{
        "url": "https://www.edurank.org/institution/stanford-university",
        "score": "98.5"
    }},
    "department": {{
        "name": "Robotics Department",
        "url": "https://robotics.stanford.edu",
        "teams": {{
            "urls": ["https://robotics.stanford.edu/team1", "https://robotics.stanford.edu/team2"],
            "members": ["Dr. Alice Smith", "Dr. Bob Johnson"]
        }},
        "focus": "Autonomous systems and machine learning in robotics"
    }},
    "publications": {{
        "google_scholar_url": "https://scholar.google.com/citations?user=abcdefg",
        "other_url": "https://www.stanford.edu/research/publications",
        "contents": ["Paper on autonomous navigation", "Research on robot perception"]
    }},
    "related": "Collaborations with MIT and Carnegie Mellon University",
    "point_of_contact": {{
        "name": "Dr. Emily Davis",
        "first_name": "Emily",
        "last_name": "Davis",
        "title": "Head of Robotics Department",
        "bio_url": "https://robotics.stanford.edu/emily-davis",
        "linked_in": "https://www.linkedin.com/in/emilydavis",
        "google_scholar_url": "https://scholar.google.com/citations?user=hijklmn",
        "email": "emily.davis@stanford.edu",
        "phone_number": "+1-650-555-1234"
    }},
    "scopes": ["Robotics", "Autonomous Systems", "Machine Learning"],
    "research_abstract": "The Robotics Department at Stanford University leads research in autonomous systems and robotics.",
    "lab_equipment": {{
        "overview": "Equipped with advanced robotics labs including autonomous vehicles and simulation systems.",
        "list": ["Autonomous vehicles", "Robotic arms", "Simulation systems"]
    }}
}}

### Pre-extracted Data:
{pre_extracted}

### Raw Content from the Webpage:
{raw_content}

### OCR content from images on Webpage:
{ocr_content}

### Output:
Return a valid JSON object that strictly follows the schema above.  
**REMINDER**: If the information in both the pre-extracted data and raw content is not closely related to science and engineering university research labs, research, or potential startups, you MUST return a JSON object with all fields blank or empty.
"""

     # Step 1: Try Groq (LLAMA-3) API
    try:
        client = Groq(api_key=GROQ_API_KEY)
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama3-70b-8192", #"deepseek-r1-distill-llama-70b"
        )
        completion_text = chat_completion.choices[0].message.content
        print("Groq API Response:", completion_text)

        start, end = completion_text.find('{'), completion_text.rfind('}')
        if start == -1 or end == -1:
            print("No JSON object found in Groq response")
            raise ValueError("No JSON found in Groq output")

        json_str = completion_text[start:end + 1]
        extracted_data = json.loads(json_str)
        print("Successfully extracted data with Groq LLM")
        return extracted_data

    except Exception as e:
        print(f"Error with Groq API: {e}")

    # Step 2: Fallback to Google Gemini if Groq fails
    if GOOGLE_API_KEY:
        print("Falling back to Google Gemini 2.0 Flash...")
        try:
            client = genai.Client(api_key=GOOGLE_API_KEY)
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[{"parts": [{"text": prompt}]}]
            )
            completion_text = response.text
            print("Google Gemini Response:", completion_text)

            start, end = completion_text.find('{'), completion_text.rfind('}')
            if start == -1 or end == -1:
                print("No JSON object found in Google Gemini response")
                return {"error": "No JSON object found in Google Gemini response", "response": completion_text}

            json_str = completion_text[start:end + 1]
            extracted_data = json.loads(json_str)
            print("Successfully extracted data with Google Gemini LLM")
            return extracted_data

        except Exception as e:
            print(f"Error with Google Gemini: {e}")
            return {"error": f"Google Gemini Error: {str(e)}"}
    else:
        print("No Google API key available for fallback")
        return {"error": "Groq failed and no Google API key provided"}