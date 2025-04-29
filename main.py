import datetime
import traceback
import os
import requests
import json
import re
import random
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify
from authlib.integrations.flask_client import OAuth
import firebase_admin
from firebase_admin import credentials, db
import logging
import threading
import time
import sys # Import sys for exit on fatal error

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- Security Warning: Do NOT hardcode secrets in production ---
# Use environment variables for production.
# For this example, default values are kept for demonstration,
# but replace these with your actual keys/secrets set as environment variables.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'moviesilsuperdupersecretkey')

# Replace with your actual TMDB API key from environment variables
TMDB_API_KEY = os.environ.get('TMDB_API_KEY', 'fb7bb23f03b6994dafc674c074d01761') # THIS IS A SECRET! MUST BE IN ENV VAR!

TMDB_BASE_URL = 'https://api.themoviedb.org/3'
TMDB_IMAGE_BASE_URL = 'https://image.tmdb.org/t/p/w500' # Standard size for posters

# Retry Configuration for TMDB API Calls within Flask app
# Using lower retries/delay than the standalone script to keep web requests responsive
TMDB_MAX_RETRIES = 3 # Fewer retries for a live web request
TMDB_BASE_DELAY_SECONDS = 0.5 # Shorter initial delay for web requests (adjust if needed)


# Replace these with your actual Google OAuth credentials from environment variables
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID', '367711020009-o70b96v4cv604acg2hqv60k8c5mjmhtr.apps.googleusercontent.com')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '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') # THIS IS A SECRET! MUST BE IN ENV VAR!
app.config['GOOGLE_DISCOVERY_URL'] = (
    'https://accounts.google.com/.well-known/openid-configuration'
)

# Replace with your actual OMDB API key from environment variables
OMDB_API_KEY = os.environ.get('OMDB_API_KEY', '6e705a15') # THIS IS A SECRET! MUST BE IN ENV VAR!

# List of admin emails
ADMIN_EMAILS = ['ehudverbin@gmail.com', 'guykresco@gmail.com', "kaylidji@gmail.com"]


app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(days=31)

oauth = OAuth(app)

oauth.register(
    'google',
    client_id=app.config.get('GOOGLE_CLIENT_ID'),
    client_secret=app.config.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url=app.config.get('GOOGLE_DISCOVERY_URL'),
    client_kwargs={'scope': 'openid email profile'},
)

# --- Firebase Configuration ---
# You need to download your service account key JSON file from
# Firebase Project Settings -> Service accounts -> Generate new private key.
# Store this file securely and provide the path here or via an environment variable.
# Example path: 'path/to/your/serviceAccountKey.json'
FIREBASE_SERVICE_ACCOUNT_KEY_PATH = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY_PATH', './firebase.json') # Placeholder path

# Your Firebase Realtime Database URL
FIREBASE_DATABASE_URL = os.environ.get('FIREBASE_DATABASE_URL', 'https://supernovarsil-default-rtdb.firebaseio.com/') # Replace if different

# Initialize Firebase Admin SDK
# Use a flag to track initialization status
FIREBASE_INITIALIZED = False
try:
    # Check if app is already initialized (prevents errors in debug/reloader mode)
    if not firebase_admin._apps:
        # Check if the service account file exists
        if not os.path.exists(FIREBASE_SERVICE_ACCOUNT_KEY_PATH):
            logging.error(f"Firebase service account key file not found at {FIREBASE_SERVICE_ACCOUNT_KEY_PATH}")
            # This is a critical error, app should likely not run Firebase ops
            cred = None # Set cred to None so initialization fails
        else:
            cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_KEY_PATH)

        if cred:
            firebase_admin.initialize_app(cred, {
                'databaseURL': FIREBASE_DATABASE_URL
            })
            FIREBASE_INITIALIZED = True
            logging.info("Firebase initialized successfully.")
        else:
             logging.error("Firebase initialization failed due to missing credential file.")
    else:
        logging.info("Firebase already initialized.")
        # Assume already initialized means it was successful previously
        FIREBASE_INITIALIZED = True
except Exception as e:
    logging.error(f"Error initializing Firebase: {e}", exc_info=True)
    FIREBASE_INITIALIZED = False


# --- Categories ---
CATEGORIES = [
    "היקום הקולנועי של מארוול",
    "DC",
    "יקום המפלצות",
    "מלחמת הכוכבים",
    "הארי פוטר",
    "המסור",
    "הפארק הדרומי",
    "מהירים ועצבניים",
    "משימה בלתי אפשרית",
    "אקס-מן",
    "ספיידרמן",
    "ללא"
]

# --- Data Loading Functions (from Firebase) ---

def load_movies_data():
    """Loads all movies from Firebase, adding 'type: movie'."""
    if not FIREBASE_INITIALIZED:
         logging.error("Firebase not initialized. Cannot load movie data.")
         return {}
    try:
        ref = db.reference('/Movies')
        movies = ref.get()
        movies_with_type = {}
        if movies:
            for imdb_id, details in movies.items():
                if isinstance(details, dict):
                    details['type'] = 'movie' # Add type identifier
                    movies_with_type[imdb_id] = details
                else:
                    logging.warning(f"Skipping non-dict movie entry: {imdb_id}")

        logging.info(f"Loaded {len(movies_with_type)} movies from Firebase.")
        return movies_with_type if movies_with_type is not None else {} # Ensure returns dict even if empty
    except Exception as e:
        logging.error(f"Error loading movies from Firebase: {e}", exc_info=True)
        return {}

def load_series_data_for_index():
    """Loads basic series data for index/all_series display from Firebase, adding 'type: series'.
       Excludes nested Season/Episode data for performance."""
    if not FIREBASE_INITIALIZED:
         logging.error("Firebase not initialized. Cannot load basic series data.")
         return {}
    try:
        # We need top-level series info for the index/all_series card
        # Fetch all top-level series data first
        ref = db.reference('/Series')
        series_dict = ref.get()

        series_for_display = {} # Use a different name to avoid confusion with old logic
        if series_dict:
            for imdb_id, details in series_dict.items():
                 # Only include basic details for the card
                 # Explicitly exclude the 'Seasons' key if it exists
                if isinstance(details, dict):
                    basic_details = {
                        'imdbID': imdb_id,
                        'title': details.get('title', 'כותרת לא ידועה'),
                        'poster': details.get('poster', 'N/A'),
                        'HebrewName': details.get('HebrewName'), # Include Hebrew name
                        'HebrewPoster': details.get('HebrewPoster'), # Include Hebrew poster
                        'category': details.get('category', 'ללא'), # Include category
                        'type': 'series', # Add type identifier
                         # Do NOT include 'Seasons' or other large nested structures here
                    }
                    series_for_display[imdb_id] = basic_details
                else:
                    logging.warning(f"Skipping non-dict series entry in /Series: {imdb_id}")

        logging.info(f"Loaded basic details for {len(series_for_display)} series from Firebase for display.")
        return series_for_display if series_for_display is not None else {}
    except Exception as e:
        logging.error(f"Error loading basic series data for display from Firebase: {e}", exc_info=True)
        return {}


def load_series_list_for_add_page():
    """Loads basic series info for the add page dropdown."""
    if not FIREBASE_INITIALIZED:
         logging.error("Firebase not initialized. Cannot load series list for add page.")
         return []
    try:
        ref = db.reference('/Series')
        series_dict = ref.get() # Gets dictionary {imdb_id: series_details}
        available_series_list = []
        if series_dict:
             # Convert to the list format expected by the add.html dropdown
            for imdb_id, details in series_dict.items():
                 if isinstance(details, dict):
                     # Use Hebrew name if available, otherwise English title for dropdown
                     display_title = details.get('HebrewName') or details.get('title', 'Untitled Series')
                     available_series_list.append({
                        "id": imdb_id,
                        "title": display_title # Use the determined display title
                     })
        logging.info(f"Loaded {len(available_series_list)} series for dropdown from Firebase.")
        return available_series_list
    except Exception as e:
        logging.error(f"Error loading series list from Firebase: {e}", exc_info=True)
        # Return dummy data or empty list on error
        return []


def load_full_series_details(imdb_id):
    """Loads all details for a single series, including seasons and episodes from Firebase."""
    if not FIREBASE_INITIALIZED:
         logging.error(f"Firebase not initialized. Cannot load full series details for ID {imdb_id}.")
         return None
    try:
        ref = db.reference(f'/Series/{imdb_id}')
        series_details = ref.get()
        if series_details:
             logging.info(f"Loaded full series details for ID {imdb_id} from Firebase.")
        else:
             logging.info(f"No full details found for series ID {imdb_id} in Firebase.")
        return series_details
    except Exception as e:
        logging.error(f"Error loading full series details for ID {imdb_id}: {e}", exc_info=True)
        return None


def load_movie_details(imdb_id):
    """Loads details for a single movie from Firebase."""
    if not FIREBASE_INITIALIZED:
         logging.error(f"Firebase not initialized. Cannot load movie details for ID {imdb_id}.")
         return None
    try:
        ref = db.reference(f'/Movies/{imdb_id}')
        movie_details = ref.get()
        if movie_details:
             logging.info(f"Loaded details for movie ID {imdb_id} from Firebase.")
        else:
             logging.info(f"No details found for movie ID {imdb_id} in Firebase.")
        return movie_details
    except Exception as e:
        logging.error(f"Error loading movie details for ID {imdb_id}: {e}", exc_info=True)
        return None

# --- New function to load trending IDs from Firebase ---
def load_trending_ids():
    """Loads trending movie and series IMDb IDs from Firebase /Top."""
    if not FIREBASE_INITIALIZED:
         logging.error("Firebase not initialized. Cannot load trending IDs.")
         return [], [] # Return empty lists

    try:
        ref_movies = db.reference('/Top/Movies')
        trending_movies_ids = ref_movies.get() or [] # Default to empty list if None
        if not isinstance(trending_movies_ids, list):
             logging.warning(f"Firebase /Top/Movies is not a list, resetting. Data: {trending_movies_ids}")
             trending_movies_ids = [] # Ensure it's a list

        ref_series = db.reference('/Top/Series')
        trending_series_ids = ref_series.get() or [] # Default to empty list if None
        if not isinstance(trending_series_ids, list):
             logging.warning(f"Firebase /Top/Series is not a list, resetting. Data: {trending_series_ids}")
             trending_series_ids = [] # Ensure it's a list

        logging.info(f"Loaded {len(trending_movies_ids)} trending movie IDs and {len(trending_series_ids)} trending series IDs from Firebase.")
        return trending_movies_ids, trending_series_ids
    except Exception as e:
        logging.error(f"Error loading trending IDs from Firebase: {e}", exc_info=True)
        return [], [] # Return empty lists on error


# --- New function to load details for trending items ---
def load_trending_data_for_display(trending_movies_ids, trending_series_ids):
    """
    Loads details for trending items by looking them up in the main /Movies and /Series paths.
    Creates a minimal placeholder if not found.
    """
    trending_movies_details = []
    trending_series_details = []

    # Load details for trending movies
    for imdb_id in trending_movies_ids:
        movie_details = load_movie_details(imdb_id) # Use existing function to get full details
        if movie_details:
             # Found in main database, add full details (ensure type is movie)
             if movie_details.get('type') == 'movie' or movie_details.get('type') is None: # Handle old data without type
                 movie_details['type'] = 'movie' # Ensure type is set for rendering
                 trending_movies_details.append(movie_details)
             else:
                  logging.warning(f"Trending movie ID {imdb_id} found in /Movies but has unexpected type '{movie_details.get('type')}'. Skipping.")
                  # Optionally add a placeholder here if desired, or just skip
        else:
            # Not found in main database, create a placeholder
            # Fetch minimal info from OMDB/TMDB just for title/poster if possible?
            # Or just use ID and mark as missing? Let's create a minimal placeholder
            # using just the ID, as we don't want to spam external APIs just for placeholders.
            logging.warning(f"Trending movie ID {imdb_id} not found in /Movies. Creating placeholder.")
            trending_movies_details.append({
                'imdbID': imdb_id,
                'type': 'movie',
                'title': f'ID {imdb_id} - לא זמין', # Generic placeholder title
                'poster': 'N/A', # Mark poster as N/A
                'HebrewName': f'מזהה {imdb_id} - לא זמין',
                'HebrewPoster': 'N/A',
                'unavailable': True # Flag to indicate this is a placeholder for a missing item
            })

    # Load details for trending series
    for imdb_id in trending_series_ids:
        # Note: For series, load_full_series_details is needed if you want seasons/episodes
        # but for the *index card display*, load_series_data_for_index provides the basic info.
        # However, load_full_series_details handles the 'series' type check better. Let's use that.
        series_details = load_full_series_details(imdb_id) # Use existing function

        if series_details:
             # Found in main database, add relevant details for index card (ensure type is series)
             if series_details.get('type') == 'series' or series_details.get('type') is None: # Handle old data without type
                 # Construct basic details needed for the card display
                 basic_series_details = {
                     'imdbID': imdb_id,
                     'type': 'series', # Ensure type is set
                     'title': series_details.get('title', f'ID {imdb_id} - כותרת לא ידועה'),
                     'poster': series_details.get('poster', 'N/A'),
                     'HebrewName': series_details.get('HebrewName'),
                     'HebrewPoster': series_details.get('HebrewPoster'),
                     # We don't need Seasons/Episodes here, just the top-level info
                 }
                 trending_series_details.append(basic_series_details)
             else:
                  logging.warning(f"Trending series ID {imdb_id} found in /Series but has unexpected type '{series_details.get('type')}'. Skipping.")

        else:
            # Not found in main database, create a placeholder
            logging.warning(f"Trending series ID {imdb_id} not found in /Series. Creating placeholder.")
            trending_series_details.append({
                'imdbID': imdb_id,
                'type': 'series',
                'title': f'ID {imdb_id} - לא זמין', # Generic placeholder title
                'poster': 'N/A', # Mark poster as N/A
                'HebrewName': f'מזהה {imdb_id} - לא זמין',
                'HebrewPoster': 'N/A',
                'unavailable': True # Flag to indicate this is a placeholder for a missing item
            })

    logging.info(f"Prepared {len(trending_movies_details)} trending movie details and {len(trending_series_details)} trending series details for display.")
    return trending_movies_details, trending_series_details


def categorize_content(movies_data, series_data):
    """Categorizes movies and series loaded from Firebase for index display."""
    categorized_items = {}
    # Initialize categories excluding "ללא" as it's typically not a display category
    # and also exclude the specific trending category names that will be separate sections
    display_categories = [cat for cat in CATEGORIES if cat != "ללא"]


    for cat in display_categories:
        categorized_items[cat] = []

    all_items = {}
    if movies_data:
        all_items.update(movies_data)
    if series_data:
        all_items.update(series_data)

    if not all_items:
        logging.info("No movies or series data to categorize.")
        # Ensure all display categories exist, even if empty
        return {cat: [] for cat in display_categories}


    # all_items now contains {imdb_id: {details_including_type}}
    for imdb_id, item_details in all_items.items():
        if not isinstance(item_details, dict):
            logging.warning(f"Skipping non-dict entry in all_items: {imdb_id}")
            continue

        # Ensure required fields exist, provide defaults
        # Get both English and Hebrew names/posters
        title = item_details.get('title', 'כותרת לא ידועה')
        poster = item_details.get('poster', 'N/A')
        hebrew_name = item_details.get('HebrewName')
        hebrew_poster = item_details.get('HebrewPoster')

        category = item_details.get('category', 'ללא') # Default to 'ללא'
        item_type = item_details.get('type') # Get the type ('movie' or 'series')

        # Add to the correct category list if category is valid and not "ללא"
        # We need id, title, poster, type, and Hebrew fields for the item cards on index.html
        # Only add if the category is one of the standard ones (not 'ללא')
        if category in display_categories and item_type in ['movie', 'series']:
             categorized_items[category].append({
                "id": imdb_id,
                "title": title, # Include English title
                "poster": poster, # Include English poster
                "HebrewName": hebrew_name, # Include Hebrew name
                "HebrewPoster": hebrew_poster, # Include Hebrew poster
                "type": item_type # Include type here
             })
        elif category == "ללא":
            pass # Don't display 'ללא' category on index
        # Don't add items belonging to the special 'Top Trending' lists here either
        else:
             # If the category is valid but not one of the display_categories (like 'ללא'),
             # it won't be added. Also skips items with invalid type.
             logging.debug(f"Item {imdb_id} ('{title}') with category '{category}' and type '{item_type}' is not added to standard display categories.")
             pass # Skip invalid categories or types

    # Optional: If you want to ensure categories with no items are still shown,
    # you might add checks here. But usually, you only show categories with items.
    # return {cat: items for cat, items in categorized_items.items() if items} # Only return categories with items

    return categorized_items


def get_greeting(user=None, language='he'): # Added language parameter back
    now = datetime.datetime.now()
    current_hour = now.hour
    greeting_text = ""

    greetings_he = {
        (5, 12): "בוקר טוב",
        (12, 18): "צהריים טובים",
        (18, 21): "ערב טוב",
        (21, 24): "לילה טוב",
        (0, 5): "לילה טוב" # Handle 0-4 AM
    }

    greetings_en = {
         (5, 12): "Good Morning",
         (12, 18): "Good Afternoon",
         (18, 21): "Good Evening",
         (21, 24): "Good Night",
         (0, 5): "Good Night"
    }

    greetings = greetings_he if language == 'he' else greetings_en

    for hour_range, text in greetings.items():
        if hour_range[0] <= current_hour < hour_range[1]:
            greeting_text = text
            break
    # Fallback if hour doesn't match any range (shouldn't happen with comprehensive ranges)
    if not greeting_text:
         greeting_text = "שלום" if language == 'he' else "Hello"


    if user and user.get('name'):
        # Split name by space and take the first part (handle multi-word names)
        first_name = user['name'].split(' ')[0]
        # Append the name only if it's a Hebrew greeting or if name is ASCII
        # Avoid issues with Hebrew name display if greeting is English and font doesn't mix well easily
        # Now that the greeting is language dependent, we can append the name based on the language
        if language == 'he':
             return f"{greeting_text} {first_name}"
        else: # For English, maybe just the greeting unless name is purely ASCII
             # Check if name contains only ASCII characters
             if all(ord(c) < 128 for c in first_name):
                  return f"{greeting_text} {first_name}"
             else:
                  # If the name contains non-ASCII (like Hebrew), just return the greeting
                  return greeting_text

    else:
        return f"{greeting_text} {'אורח' if language == 'he' else 'Guest'}"


# --- OMDB API Functions ---
OMDB_BASE_URL = 'http://www.omdbapi.com/'

def search_omdb_api(search_term, content_type):
    """Searches OMDB API for movies or series."""
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY':
        logging.warning("OMDB_API_KEY is not set.")
        return []
    params = {
        'apikey': OMDB_API_KEY,
        's': search_term,
        'type': content_type
    }
    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        if data.get('Response') == 'True':
            logging.info(f"OMDB search for '{search_term}' type '{content_type}' found {len(data.get('Search', []))} results.")
            # OMDB search results list includes Title, Year, imdbID, Type, Poster
            # Filter out anything that isn't the requested type just in case (OMDB search can be fuzzy)
            filtered_results = [item for item in data.get('Search', []) if item.get('Type', '').lower() == content_type]
            return filtered_results
        else:
             logging.info(f"OMDB search found no results for '{search_term}' type '{content_type}': {data.get('Error', 'Unknown error')}")
             return []
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling OMDB search API: {e}", exc_info=True)
        return []

def get_omdb_details_api(imdb_id, season=None, episode=None):
    """
    Gets full details for a specific IMDb ID from OMDB.
    If season and episode are provided, gets details for a specific episode of a series.
    If only season is provided (and imdb_id is series ID), gets details for that season (list of episodes).
    """
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY':
         logging.warning("OMDB_API_KEY is not set.")
         return None
    params = {
        'apikey': OMDB_API_KEY,
        'i': imdb_id, # This is the SERIES IMDb ID when getting episode/season details
    }
    if season is not None:
        params['Season'] = season
    if episode is not None:
        params['Episode'] = episode
    else: # If only season is requested (or neither), request full plot for main details
         params['plot'] = 'full'


    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        if data.get('Response') == 'True':
            # Check if it's a specific episode request and if the response matches
            if season is not None and episode is not None:
                 # OMDB episode response *should* contain 'SeriesID' matching the requested 'i'
                 # and 'Season'/'Episode' matching the request. It also has its own 'imdbID' and 'Title'.
                 if data.get('SeriesID') == imdb_id and data.get('Season') == str(season) and data.get('Episode') == str(episode):
                      logging.info(f"Successfully fetched OMDB episode details for Series ID '{imdb_id}', S{season}E{episode}. Episode IMDb ID: {data.get('imdbID', 'N/A')}")
                      return data
                 else:
                      # This might happen if the season/episode doesn't exist for the series
                      logging.warning(f"OMDB episode details response does not match request for Series ID '{imdb_id}', S{season}E{episode}. Response: {data}")
                      # Return an empty-like response or None to signal not found/match
                      return {'Response': 'False', 'Error': 'Episode data mismatch or not found'} # Return a dict with Response=False
            elif season is not None and episode is None:
                 # This is a request for a specific season's episodes list
                 # OMDB season response contains 'Season', 'Episodes' (a list), 'Response'
                 if data.get('Response') == 'True' and data.get('Season') == str(season):
                      logging.info(f"Successfully fetched OMDB season details for Series ID '{imdb_id}', Season {season}. Found {len(data.get('Episodes', []))} episodes.")
                      return data
                 else:
                     logging.warning(f"OMDB season details response does not match request for Series ID '{imdb_id}', Season {season}. Response: {data}")
                     return {'Response': 'False', 'Error': 'Season data mismatch or not found'} # Return a dict with Response=False
            else:
                # It's a main movie/series details request (season and episode are None)
                logging.info(f"Successfully fetched OMDB main details for ID '{imdb_id}'. Type: {data.get('Type')}")
                return data
        else:
             # Handle OMDB Response 'False' case for any request type
             error_message = data.get('Error', 'Unknown error from OMDB')
             if season is not None and episode is not None:
                  logging.info(f"OMDB episode not found for Series ID '{imdb_id}', S{season}E{episode}: {error_message}")
             elif season is not None:
                 logging.info(f"OMDB season data not found for Series ID '{imdb_id}', Season {season}: {error_message}")
             else:
                  logging.info(f"OMDB main details not found for ID '{imdb_id}': {error_message}")
             return {'Response': 'False', 'Error': error_message} # Return a dict with Response=False
    except requests.exceptions.RequestException as e:
        # Handle network or request errors
        logging.error(f"Error calling OMDB details API for ID {imdb_id} (Season {season}, Episode {episode}): {e}", exc_info=True)
        return {'Response': 'False', 'Error': f'Request Error: {e}'}


# --- API Routes for Frontend OMDB Search (Used by add.html) ---
@app.route('/api/search_omdb')
def api_search_omdb():
    search_term = request.args.get('s')
    content_type = request.args.get('type') # 'movie' or 'series'
    if not search_term or not content_type:
        logging.warning("API search called with missing search term or type.")
        return jsonify({"Error": "Missing search term or type"}), 400
    if content_type not in ['movie', 'series']:
         logging.warning(f"API search called with invalid type: {content_type}")
         return jsonify({"Error": "Invalid type specified"}), 400

    results = search_omdb_api(search_term, content_type)
    # OMDB search results contain Title, Year, imdbID, Type, Poster. We need to return these.
    formatted_results = []
    for res in results:
         formatted_results.append({
             'Title': res.get('Title'),
             'Year': res.get('Year'),
             'imdbID': res.get('imdbID'),
             'Type': res.get('Type'),
             'Poster': res.get('Poster') # Include poster for display in results list if desired
         })

    return jsonify(formatted_results)

@app.route('/api/get_omdb_details')
def api_get_omdb_details():
    imdb_id = request.args.get('i')
    season = request.args.get('season') # Optional: for episode lookup
    episode = request.args.get('episode') # Optional: for episode lookup

    if not imdb_id:
        logging.warning("API get details called with missing IMDb ID.")
        return jsonify({"Error": "Missing IMDb ID"}), 400

    try:
        season_int = int(season) if season else None
        episode_int = int(episode) if episode else None
    except ValueError:
        logging.warning(f"API get details called with non-integer season/episode: season={season}, episode={episode}")
        return jsonify({"Error": "Invalid season or episode number"}), 400

    # Call the unified get_omdb_details_api function
    details = get_omdb_details_api(imdb_id, season_int, episode_int)

    if details and details.get('Response') == 'True':
        # Return the raw details object from OMDB
        return jsonify(details)
    else:
        # Return 404 if details (or specific episode/season) not found or API error
        error_message = details.get('Error', 'Details not found or API error') if isinstance(details, dict) else 'Details not found or API error'
        return jsonify({"Error": error_message}), 404


# --- Authentication Routes ---
@app.route('/auth/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    try:
        logging.info(f"Initiating Google login redirect to {redirect_uri}")
        return oauth.google.authorize_redirect(redirect_uri=redirect_uri)
    except Exception as e:
        logging.error("Error during Google login authorization:", exc_info=True)
        flash('שגיאה בתהליך ההתחברות עם גוגל.', 'error')
        return redirect(url_for('index'))

@app.route('/auth/google/callback')
def google_callback():
    try:
        logging.info("Handling Google login callback.")
        token = oauth.google.authorize_access_token()

        # Access user info directly from userinfo_response
        userinfo_response = oauth.google.userinfo(token=token)

        user_data = {
            'name': userinfo_response.get('name'), # Access properties directly
            'email': userinfo_response.get('email'), # Access properties directly
            'picture': userinfo_response.get('picture'), # Access properties directly
            'google_id': userinfo_response.get('sub') # Access properties directly (sub is standard OIDC ID)
        }

        if not user_data.get('google_id'):
            logging.warning("Google login callback failed: No google_id received.")
            flash('התחברות עם גוגל נכשלה: לא הושגו פרטי משתמש.', 'error')
            return redirect(url_for('index'))

        session['user'] = user_data
        session.permanent = True
        logging.info(f"User {user_data.get('email')} logged in successfully.")
        flash('התחברת בהצלחה!', 'success')
        return redirect(url_for('index'))

    except Exception as e:
        logging.error("Error during Google login callback:", exc_info=True)
        flash('התחברות נכשלה. אנא ודא שההרשאות המתאימות אושרו ונסה שוב.', 'error')
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    user_email = session.get('user', {}).get('email', 'anonymous')
    session.pop('user', None)
    # --- Frontend: Clear local storage for 'continueWatching' on logout ---
    # This cannot be done directly from Python. You would need to add
    # JavaScript on the index page or a dedicated logout page that clears localStorage.
    # For this example, we won't add a separate logout page, but keep in mind
    # localStorage persists even after server-side session pop. Clearing
    # localStorage on successful login is another option, or tying localStorage
    # key to user ID, but the request asked for local storage *like session*,
    # implying simple localStorage. A simple way is to clear on the client side
    # when the logout action is confirmed (e.g., on page load after logout).
    # Adding a flash message indicates successful logout, and the JS below
    # checks for the user being None to determine if they are logged out.
    logging.info(f"User {user_email} logged out.")
    flash('התנתקת בהצלחה.', 'info')
    # Redirecting back to index. The index page JS will handle the logged-out state.
    return redirect(url_for('index'))

# --- Language Route ---
@app.route('/set_language/<lang_code>')
def set_language(lang_code):
    """Sets the language preference in the session and redirects."""
    # Validate language code
    valid_languages = ['he', 'en']
    if lang_code not in valid_languages:
        logging.warning(f"Attempted to set invalid language code: {lang_code}")
        flash('שגיאה: שפה לא נתמכת.', 'error')
        return redirect(request.referrer or url_for('index')) # Redirect back or to index

    session['language'] = lang_code
    logging.info(f"Language set to: {lang_code}")
    # Redirect back to the page the user was on, or the index page if referrer is not available
    return redirect(request.referrer or url_for('index'))


# --- Main Routes ---
@app.route('/')
def index():
    user = session.get('user')
    current_language = session.get('language', 'he') # Get language from session, default to 'he'
    greeting = get_greeting(user, current_language) # Pass language to greeting function again

    # Load all content from Firebase for the index page
    # NOTE: Loading ALL movies/series here might be inefficient for very large databases.
    # A better approach for large data would be to only load what's needed for the *initial* view
    # or implement proper pagination server-side. For now, keeping existing loading logic.
    movies_data = load_movies_data() # Includes type: 'movie' and Hebrew fields if exist
    series_data_for_index = load_series_data_for_index() # Includes type: 'series' and Hebrew fields if exist

    # --- Load and Prepare Trending Data ---
    trending_movies_ids, trending_series_ids = load_trending_ids()
    # Load details for trending items, getting from main database or creating placeholders
    trending_movies_details, trending_series_details = load_trending_data_for_display(
        trending_movies_ids, trending_series_ids
    )
    logging.info(f"Displaying {len(trending_movies_details)} trending movies and {len(trending_series_details)} trending series.")


    # Categorize them for standard display sections (EXCLUDING the trending ones now)
    categories = categorize_content(movies_data, series_data_for_index)

    current_year = datetime.datetime.utcnow().year
    # Pass the list of admin emails to the template if needed (though index.html might not use it)
    # If admin status is only checked server-side, passing the list isn't strictly necessary here.
    # However, keeping it consistent with previous logic of passing *something* related to admin.

    # Pass the user object, categorized data, and current language to the template.
    # Also pass the specific trending lists.
    # The 'continue watching' logic is handled client-side using localStorage.
    return render_template('index.html',
                           greeting=greeting,
                           trending_movies=trending_movies_details, # Pass trending movies data
                           trending_series=trending_series_details, # Pass trending series data
                           categories=categories, # All categoried content for display and JS lookup
                           current_year=current_year,
                           user=user,
                           admin_emails=ADMIN_EMAILS, # Pass the list of admin emails
                           current_language=current_language # Pass the current language
                           )

# --- Route for Single Movie Page ---
@app.route('/movie/<imdb_id>')
def movie_details(imdb_id):
    user = session.get('user')
    current_language = session.get('language', 'he') # Get language from session, default to 'he'
    current_year = datetime.datetime.utcnow().year

    # Validate IMDb ID format before querying
    if not imdb_id or not imdb_id.startswith('tt') or len(imdb_id) < 7:
         logging.warning(f"Attempted to access movie page with invalid IMDb ID format: {imdb_id}")
         abort(404) # Or redirect to error page

    # Load movie details from Firebase
    movie = load_movie_details(imdb_id) # Includes Hebrew fields if exist

    # Check if found and if it's a movie type (assuming /Movies only contains movies or add type check)
    # Add explicit type check from loaded data if available
    if not movie or (movie.get('type') not in [None, 'movie'] and movie.get('type') != 'movie'): # Handle potential old data without 'type'
        logging.warning(f"Movie details not found or is not of type 'movie' for ID: {imdb_id}")
        # If not found or not a movie type, show 404 or specific error page
        abort(404)

    # Render the movie details page
    # NOTE: The video player and timestamp logic is expected in movie.html's JS.
    # The video URL is assumed to be stored as movie['video_url'] in Firebase,
    # even though the add form no longer accepts input for it.
    # This route does NOT require a video_url to exist in Firebase.
    return render_template('movie.html',
                           movie=movie, # movie object should contain video_url if needed for playback
                           user=user,
                           current_year=current_year,
                           admin_emails=ADMIN_EMAALS, # Pass the list of admin emails
                           current_language=current_language # Pass the current language
                           )

# --- Route for Single Series Page ---
# Base series page
@app.route('/series/<imdb_id>')
# Series page with specific season and episode number
@app.route('/series/<imdb_id>/<int:season_number>/<int:episode_number>')
def series_details(imdb_id, season_number=None, episode_number=None):
    user = session.get('user')
    current_language = session.get('language', 'he') # Get language from session, default to 'he'
    current_year = datetime.datetime.utcnow().year

    # Validate Series IMDb ID format
    if not imdb_id or not imdb_id.startswith('tt') or len(imdb_id) < 7:
        logging.warning(f"Attempted to access series page with invalid Series IMDb ID format: {imdb_id}")
        abort(404)

    # Validate Season/Episode Numbers if provided (Flask <int:> handles non-integers,
    # but we can add checks for non-positive if needed, though JS also validates >=1)
    if season_number is not None and (season_number < 1):
        logging.warning(f"Attempted to access series page with invalid season number ({season_number}) for series {imdb_id}")
        # Let JS handle it for flexibility.
        pass # Continue loading the page

    if episode_number is not None and (episode_number < 1):
        logging.warning(f"Attempted to access series page with invalid episode number ({episode_number}) for series {imdb_id}")
        # Let JS handle it for flexibility.
        pass # Continue loading the page


    # Load full series details from Firebase (including Seasons/Episodes)
    series = load_full_series_details(imdb_id) # Includes Hebrew fields if exist

    # Check if found and if it's a series type
    # Handle potential old data without 'type' gracefully by checking existence
    if not series or (series.get('type') not in [None, 'series'] and series.get('type') != 'series'):
        logging.warning(f"Series details not found or is not of type 'series' for ID: {imdb_id}")
        abort(404)

    # Pass the full series object and current language to the template.
    # The season_number and episode_number from the URL are *not* explicitly
    # passed as template variables here, because the JavaScript reads them
    # directly from window.location.pathname on page load.
    return render_template('series.html',
                           series=series, # Pass the full series data
                           user=user,
                           current_year=current_year,
                           admin_emails=ADMIN_EMAILS,
                           current_language=current_language # Pass the current language
                           )


@app.route('/add', methods=['GET', 'POST'])
def add_content():
    user = session.get('user')
    # Basic admin check: Check if the user's email is in the list of ADMIN_EMAILS
    if not user or user.get('email') not in ADMIN_EMAILS:
        logging.warning(f"Unauthorized access attempt to /add by {user.get('email') if user else 'anonymous'}")
        abort(403)

    if request.method == 'POST':
        content_type = request.form.get('content_type')
        logging.info(f"Received POST for content type: {content_type}")

        try:
            if content_type == 'movie':
                # Get form data for movie
                imdb_id = request.form.get('movie_imdb_id', '').strip()
                category = request.form.get('movie_category', 'ללא') # Get category from form

                # Validate required fields for movie
                if not imdb_id:
                    flash('שגיאה: שדה חובה (IMDb ID) חסר עבור סרט.', 'error')
                    return redirect(url_for('add_content'))

                imdb_id_pattern = re.compile(r'^tt\d{7,}$')
                if not imdb_id_pattern.match(imdb_id):
                     flash('שגיאה: פורמט IMDb ID לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                     return redirect(url_for('add_content'))


                # Fetch full details from OMDB server-side using the IMDb ID
                omdb_details = get_omdb_details_api(imdb_id)

                # Check if OMDB details were found and if the type is actually a movie
                if not omdb_details or omdb_details.get('Response') == 'False' or omdb_details.get('Type', '').lower() != 'movie': # Case-insensitive check
                     error_msg = omdb_details.get('Error', 'Details not found or API error') if isinstance(omdb_details, dict) else 'Details not found or API error'
                     flash(f'שגיאה: לא נמצאו פרטי סרט תקינים עבור IMDb ID "{imdb_id}" ב-OMDB. {error_msg}', 'error')
                     logging.warning(f"OMDB details not found or type is not 'movie' for ID {imdb_id}. OMDB Response: {omdb_details}")
                     return redirect(url_for('add_content'))

                # Construct base movie data from OMDB details and form data
                movie_data = {
                    'imdbID': omdb_details.get('imdbID', imdb_id), # Use fetched ID if available
                    'title': omdb_details.get('Title', 'Untitled'),
                    'year': omdb_details.get('Year', 'N/A'),
                    'rated': omdb_details.get('Rated', 'N/A'),
                    'released': omdb_details.get('Released', 'N/A'),
                    'runtime': omdb_details.get('Runtime', 'N/A'),
                    'genre': omdb_details.get('Genre', 'N/A'),
                    'director': omdb_details.get('Director', 'N/A'),
                    'writer': omdb_details.get('Writer', 'N/A'),
                    'actors': omdb_details.get('Actors', 'N/A'),
                    'plot': omdb_details.get('Plot', 'N/A'),
                    'language': omdb_details.get('Language', 'N/A'),
                    'country': omdb_details.get('Country', 'N/A'),
                    'awards': omdb_details.get('Awards', 'N/A'),
                    'poster': omdb_details.get('Poster', 'N/A'), # Use 'N/A' instead of default image if not found
                    'ratings': omdb_details.get('Ratings', []), # List of rating objects
                    'metascore': omdb_details.get('Metascore', 'N/A'),
                    'imdbRating': omdb_details.get('imdbRating', 'N/A'),
                    'imdbVotes': omdb_details.get('imdbVotes', 'N/A'),
                    'type': 'movie', # Explicitly set type as 'movie'
                    'dvd': omdb_details.get('DVD', 'N/A'),
                    'boxoffice': omdb_details.get('BoxOffice', 'N/A'),
                    'production': omdb_details.get('Production', 'N/A'),
                    'website': omdb_details.get('Website', 'N/A'),
                    'video_url': '', # Placeholder: Video URL must be added separately
                    'category': category # From form
                }

                # --- Fetch and add Hebrew details from TMDB ---
                logging.info(f"Attempting to fetch Hebrew details for movie {imdb_id} from TMDB...")
                tmdb_id, tmdb_type = get_tmdb_info(imdb_id)

                if tmdb_id and tmdb_type == 'movie':
                     hebrew_name, hebrew_poster_url = get_hebrew_details(tmdb_id, tmdb_type)

                     if hebrew_name:
                         movie_data['HebrewName'] = hebrew_name
                         logging.info(f"Added HebrewName: {hebrew_name} for movie {imdb_id}")

                     if hebrew_poster_url:
                         movie_data['HebrewPoster'] = hebrew_poster_url
                         logging.info(f"Added HebrewPoster: {hebrew_poster_url} for movie {imdb_id}")
                     elif movie_data['poster'] == 'N/A' or movie_data['poster'] is None:
                          # If OMDB poster was missing AND TMDB Hebrew poster was missing,
                          # fallback to a default missing image URL if you have one defined elsewhere.
                          # For now, just log that both are missing.
                          logging.warning(f"Both OMDB and TMDB Hebrew posters are missing for movie {imdb_id}.")

                else:
                    logging.warning(f"Could not find TMDB movie info for IMDb ID {imdb_id} or type mismatch. Skipping Hebrew details.")
                # --- END NEW CODE ---

                # Save movie data to Firebase under /Movies/{imdb_id}
                # Use set here as adding a movie usually means a new entry
                if FIREBASE_INITIALIZED:
                    ref = db.reference(f'/Movies/{imdb_id}')
                    ref.set(movie_data)
                    logging.info(f"Movie '{movie_data['title']}' ({imdb_id}) added to Firebase.")

                    # --- Update Flash Message for Movie ---
                    flash_message = f'סרט "{movie_data.get("title", imdb_id)}" נוסף בהצלחה!'
                    if not (movie_data.get('HebrewName') or movie_data.get('HebrewPoster')):
                         flash_message += ' אזהרה: לא ניתן היה להשיג שם עברי או פוסטר עברי מ-TMDb.'
                         flash(flash_message, 'warning')
                    else:
                         flash(flash_message, 'success')
                else:
                     flash('שגיאה: Firebase אינו מאותחל, לא ניתן לשמור את הסרט.', 'error')
                     logging.error("Firebase not initialized, unable to save movie.")


            elif content_type == 'series':
                 # Get form data for series (main details, series_imdb_id from search selection)
                 series_imdb_id = request.form.get('series_imdb_id', '').strip() # From OMDB search selection
                 category = request.form.get('series_category', 'ללא') # Get category for series

                 # Validate required fields for main series
                 if not series_imdb_id:
                     flash('שגיאה: שדה חובה עבור סדרה (IMDb ID) חסר.', 'error')
                     return redirect(url_for('add_content'))

                 imdb_id_pattern = re.compile(r'^tt\d{7,}$')
                 if not imdb_id_pattern.match(series_imdb_id):
                     flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                     return redirect(url_for('add_content'))


                 # Fetch main series details from OMDB server-side
                 omdb_details = get_omdb_details_api(series_imdb_id)

                 # Check if OMDB details were found and if the type is actually a series
                 if not omdb_details or omdb_details.get('Response') == 'False' or omdb_details.get('Type', '').lower() != 'series':
                      error_msg = omdb_details.get('Error', 'Details not found or API error') if isinstance(omdb_details, dict) else 'Details not found or API error'
                      flash(f'שגיאה: לא נמצאו פרטים לסדרה או שה-ID אינו של סדרה עבור "{series_imdb_id}" ב-OMDB. {error_msg}', 'error')
                      logging.warning(f"OMDB details not found or type is not 'series' for ID {series_imdb_id}. OMDB Response: {omdb_details}")
                      return redirect(url_for('add_content'))

                 # Extract total seasons from OMDB details, default to 1 if missing/invalid
                 try:
                     total_seasons_str = omdb_details.get('totalSeasons', '1')
                     total_seasons = int(total_seasons_str)
                     if total_seasons < 1:
                          logging.warning(f"OMDB returned invalid totalSeasons ({total_seasons_str}) for {series_imdb_id}. Defaulting to 1.")
                          total_seasons = 1
                 except ValueError:
                      logging.warning(f"OMDB returned non-integer totalSeasons ({total_seasons_str}) for {series_imdb_id}. Defaulting to 1.")
                      total_seasons = 1


                 # Construct base series data from OMDB details and form data
                 series_data = {
                    'imdbID': omdb_details.get('imdbID', series_imdb_id),
                    'title': omdb_details.get('Title', 'Untitled Series'),
                    'year': omdb_details.get('Year', 'N/A'), # This might be a range (e.g., 2008–2013)
                    'rated': omdb_details.get('Rated', 'N/A'),
                    'released': omdb_details.get('Released', 'N/A'),
                    'runtime': omdb_details.get('Runtime', 'N/A'), # Runtime per episode/total? OMDB is inconsistent.
                    'genre': omdb_details.get('Genre', 'N/A'),
                    'director': omdb_details.get('Director', 'N/A'),
                    'writer': omdb_details.get('Writer', 'N/A'),
                    'actors': omdb_details.get('Actors', 'N/A'),
                    'plot': omdb_details.get('Plot', 'N/A'),
                    'language': omdb_details.get('Language', 'N/A'),
                    'country': omdb_details.get('Country', 'N/A'),
                    'awards': omdb_details.get('Awards', 'N/A'),
                    'poster': omdb_details.get('Poster', 'N/A'),
                    'ratings': omdb_details.get('Ratings', []),
                    'metascore': omdb_details.get('Metascore', 'N/A'),
                    'imdbRating': omdb_details.get('imdbRating', 'N/A'),
                    'imdbVotes': omdb_details.get('imdbVotes', 'N/A'),
                    'type': 'series',
                    'totalSeasons': total_seasons_str, # Store the string from OMDB or default '1'
                    'category': category
                    # HebrewName and HebrewPoster will be added below
                 }

                 # --- Fetch and add Hebrew details from TMDB FOR THE SERIES ---
                 logging.info(f"Attempting to fetch Hebrew details for series {series_imdb_id} from TMDB...")
                 tmdb_id, tmdb_type = get_tmdb_info(series_imdb_id)

                 # Check for 'tv' type from TMDB find
                 if tmdb_id and tmdb_type == 'tv':
                     hebrew_name, hebrew_poster_url = get_hebrew_details(tmdb_id, tmdb_type)

                     if hebrew_name:
                         series_data['HebrewName'] = hebrew_name
                         logging.info(f"Added HebrewName: {hebrew_name} for series {series_imdb_id}")

                     if hebrew_poster_url:
                         series_data['HebrewPoster'] = hebrew_poster_url
                         logging.info(f"Added HebrewPoster: {hebrew_poster_url} for series {series_imdb_id}")
                     elif series_data['poster'] == 'N/A' or series_data['poster'] is None:
                          # If OMDB poster was missing AND TMDB Hebrew poster was missing
                          logging.warning(f"Both OMDB and TMDB Hebrew posters are missing for series {series_imdb_id}.")

                 else:
                     logging.warning(f"Could not find TMDB TV info for IMDb ID {series_imdb_id} or type mismatch. Skipping Hebrew details for series.")
                 # --- END NEW CODE ---


                 # Build the Seasons/Episodes structure by fetching season data from OMDB
                 seasons_data = {}
                 all_episodes_fetched_successfully = True # Flag to track if all episode fetches worked

                 for season_num in range(1, total_seasons + 1):
                     # Fetch details for the specific season to get the episode list
                     season_details_from_omdb = get_omdb_details_api(series_imdb_id, season=season_num)

                     if season_details_from_omdb and season_details_from_omdb.get('Response') == 'True' and season_details_from_omdb.get('Episodes'):
                          episodes_list_for_season = season_details_from_omdb.get('Episodes', [])
                          episodes_data = {}
                          num_episodes_in_season = len(episodes_list_for_season)
                          logging.info(f"Fetched {num_episodes_in_season} episodes for S{season_num} from OMDB for series {series_imdb_id}.")

                          # OMDB season response gives a list of episodes, each with its 'Episode', 'Title', 'imdbID' etc.
                          # NOTE: Hebrew details are NOT fetched for individual episodes here as requested.
                          for episode_detail in episodes_list_for_season:
                               try:
                                   episode_num_str = episode_detail.get('Episode')
                                   episode_num = int(episode_num_str) if episode_num_str else None

                                   if episode_num is not None and episode_num >= 1:
                                        episode_imdb_id_to_save = episode_detail.get('imdbID', f'tt_placeholder_{series_imdb_id}_s{season_num}e{episode_num}')
                                        episode_title_to_save = episode_detail.get('Title', f'פרק {episode_num}')

                                        episodes_data[str(episode_num)] = { # Use episode number as the key
                                            'episode_imdb_id': episode_imdb_id_to_save,
                                            'title': episode_title_to_save,
                                            'season_number': season_num, # Store season number explicitly
                                            'episode_number': episode_num, # Store episode number explicitly
                                            'video_url': '', # Placeholder: Video URL must be added separately
                                            # Optionally add other episode details from OMDB if available and desired
                                            # e.g., 'Released': episode_detail.get('Released'), 'Plot': episode_detail.get('Plot') # Plot is often short in season response
                                        }
                                        # logging.debug(f"Processing S{season_num}E{episode_num} ({series_imdb_id}): IMDb ID: {episode_imdb_id_to_save}, Title: '{episode_title_to_save}'")
                                   else:
                                       logging.warning(f"Skipping episode data with invalid number or missing data in OMDB Season {season_num} response for {series_imdb_id}: {episode_detail}")
                                       all_episodes_fetched_successfully = False # Mark failure if an episode within a season is malformed


                               except ValueError:
                                    logging.warning(f"Could not parse episode number from OMDB Season {season_num} response for {series_imdb_id}: {episode_detail.get('Episode')}. Skipping episode.")
                                    all_episodes_fetched_successfully = False # Mark failure
                               except Exception as e:
                                   logging.error(f"Unexpected error processing episode data for S{season_num} in {series_imdb_id}: {e}", exc_info=True)
                                   all_episodes_fetched_successfully = False


                          if episodes_data: # Only add season node if episodes were successfully processed for it
                              seasons_data[str(season_num)] = {
                                  'Episodes': episodes_data
                              }
                          else:
                              logging.warning(f"No valid episode data found in OMDB response for Season {season_num} of series {series_imdb_id}. Season might not be added.")
                              all_episodes_fetched_successfully = False

                     else:
                         error_msg = season_details_from_omdb.get('Error', 'Unknown Error') if isinstance(season_details_from_omdb, dict) else 'Unknown Error'
                         logging.warning(f"Failed to fetch OMDB season details or found no episodes for Season {season_num} of series {series_imdb_id}. OMDB Error: {error_msg}. Season will not be added.")
                         all_episodes_fetched_successfully = False # Mark failure

                 # Add the Seasons structure to the main series data
                 if seasons_data:
                      series_data['Seasons'] = seasons_data
                 else:
                     logging.warning(f"No seasons or episodes were successfully added for series {series_imdb_id} based on OMDB data.")
                     # Flash a warning if no episodes were processed at all


                 # Save series data (including Seasons/Episodes and new Hebrew fields) to Firebase
                 # Use update instead of set to potentially preserve manual video_urls if re-adding or partial updates happen
                 if FIREBASE_INITIALIZED:
                     ref = db.reference(f'/Series/{series_imdb_id}')
                     ref.update(series_data)
                     logging.info(f"Series '{series_data.get('title', series_imdb_id)}' ({series_imdb_id}) added/updated in Firebase with {len(seasons_data)} seasons and Hebrew details.")

                     # --- Update Flash Message for Series ---
                     flash_message = f'סדרה "{series_data.get("title", series_imdb_id)}" נוספה/עודכנה בהצלחה!'
                     # Combine warnings
                     warnings = []
                     if not (series_data.get('HebrewName') or series_data.get('HebrewPoster')):
                          warnings.append('לא ניתן היה להשיג שם עברי או פוסטר עברי מ-TMDb')
                     if not all_episodes_fetched_successfully:
                          warnings.append('לא ניתן היה להשיג פרטים עבור כל הפרקים/עונות מ-OMDb')

                     if warnings:
                          flash_message += ' אזהרה: ' + '. '.join(warnings) + '.'
                          flash(flash_message, 'warning')
                     else:
                          flash(flash_message, 'success')
                 else:
                     flash('שגיאה: Firebase אינו מאותחל, לא ניתן לשמור את הסדרה.', 'error')
                     logging.error("Firebase not initialized, unable to save series.")


            elif content_type == 'episode':
                 # Get form data for episode
                 series_imdb_id_select = request.form.get('episode_series_id')
                 manual_series_imdb_id = request.form.get('manual_episode_series_id', '').strip()
                 episode_title_form = request.form.get('episode_title', '').strip() # Get title from form (user override)
                 season_number_str = request.form.get('episode_season', '').strip()
                 episode_number_str = request.form.get('episode_number', '').strip()
                 # video_url = request.form.get('episode_video_url', '').strip() # REMOVED INPUT

                 # Determine the series IMDb ID
                 series_imdb_id = manual_series_imdb_id if series_imdb_id_select == 'manual' else series_imdb_id_select

                 # Validate required fields for episode (excluding episode_imdb_id and video_url)
                 # Title is now optional from form, but we need season, episode, and series ID
                 if not series_imdb_id or not season_number_str or not episode_number_str:
                      missing = []
                      if not series_imdb_id: missing.append('סדרה')
                      if not season_number_str: missing.append('מספר עונה')
                      if not episode_number_str: missing.append('מספר פרק')
                      flash(f'שגיאה: שדות חובה חסרים: {", ".join(missing)}.', 'error')
                      return redirect(url_for('add_content'))

                 # Validate season and episode numbers
                 try:
                     season_number = int(season_number_str)
                     episode_number = int(episode_number_str)
                     if season_number < 1 or episode_number < 1:
                         raise ValueError("Numbers must be positive")
                 except ValueError:
                     flash('שגיאה: מספרי עונה ופרק חייבים להיות מספרים שלמים חיוביים.', 'error')
                     return redirect(url_for('add_content'))

                 # Validate Series IMDb ID format
                 imdb_id_pattern = re.compile(r'^tt\d{7,}$')
                 if not imdb_id_pattern.match(series_imdb_id):
                     flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                     return redirect(url_for('add_content'))

                 # Fetch episode details from OMDB using the Series ID, Season, and Episode numbers
                 # This is mainly for title and episode_imdb_id if available from OMDB
                 episode_details_from_omdb = get_omdb_details_api(series_imdb_id, season=season_number, episode=episode_number)

                 # Determine the episode's IMDb ID and Title to save
                 episode_imdb_id_to_save = None
                 episode_title_to_save = episode_title_form # Use form title if provided

                 if episode_details_from_omdb and episode_details_from_omdb.get('Response') == 'True':
                      episode_imdb_id_to_save = episode_details_from_omdb.get('imdbID')
                      # If form title is empty, use OMDB title
                      if not episode_title_form:
                          episode_title_to_save = episode_details_from_omdb.get('Title', f'פרק {episode_number} (מ-OMDb)')
                      logging.info(f"Fetched OMDB details for episode {series_imdb_id} S{season_number}E{episode_number}. Episode IMDb ID: {episode_imdb_id_to_save}, Title: '{episode_details_from_omdb.get('Title')}'")
                 else:
                     # Check if episode_details_from_omdb is a dictionary with an error message
                     error_msg = episode_details_from_omdb.get('Error', 'Unknown Error') if isinstance(episode_details_from_omdb, dict) else 'Unknown Error'
                     logging.warning(f"Failed to fetch OMDB details for episode {series_imdb_id} S{season_number}E{episode_number}. OMDB Error: {error_msg}. Proceeding with form data and placeholder ID.")
                     # Use placeholder ID if OMDB fails to provide one
                     episode_imdb_id_to_save = f'tt_placeholder_{series_imdb_id}_s{season_number}e{episode_number}'
                     # If OMDB fetch failed and user didn't provide a title, use a generic placeholder title
                     if not episode_title_form:
                         episode_title_to_save = f'פרק {episode_number} (לא נמצא שם ב-OMDb)'


                 # Ensure we have at least a placeholder ID even if OMDB fetch failed AND fallback placeholder logic had an issue (highly unlikely)
                 if not episode_imdb_id_to_save:
                     episode_imdb_id_to_save = f'tt_fallback_{series_imdb_id}_s{season_number}e{episode_number}'
                     logging.error(f"Critical: No episode IMDb ID could be determined for {series_imdb_id} S{season_number}E{episode_number}. Using double-fallback placeholder.")

                 # Construct episode data (without video_url from form, but setting it to empty)
                 # NOTE: Hebrew details are NOT added to individual episodes.
                 episode_data = {
                     'episode_imdb_id': episode_imdb_id_to_save, # Store the fetched/placeholder episode IMDb ID
                     'title': episode_title_to_save, # Use user's title or fetched title
                     'video_url': '', # Placeholder: Video URL must be added separately
                     'episode_number': episode_number,
                     'season_number': season_number
                 }

                 # Save episode data to Firebase under /Series/{series_imdb_id}/Seasons/{season_number}/Episodes/{episode_number}
                 # Use update instead of set to preserve existing video_url if it was added manually before
                 if FIREBASE_INITIALIZED:
                     ref = db.reference(f'/Series/{series_imdb_id}/Seasons/{season_number}/Episodes/{episode_number}')
                     ref.update(episode_data)
                     logging.info(f"Episode S{season_number}E{episode_number} (IMDb ID: {episode_imdb_id_to_save}, Title: '{episode_data['title']}') added/updated in series {series_imdb_id}.")
                     flash(f'פרק "{episode_data["title"]}" (עונה {season_number}, פרק {episode_number}) נוסף/עודכן בהצלחה לסדרה!', 'success')
                 else:
                     flash('שגיאה: Firebase אינו מאותחל, לא ניתן לשמור את הפרק.', 'error')
                     logging.error("Firebase not initialized, unable to save episode.")


            else:
                 flash('סוג תוכן לא ידוע.', 'warning')
                 logging.warning(f"Received unknown content type: {content_type}")


        except Exception as e:
             logging.error(f"Error processing add content POST: {e}", exc_info=True)
             flash('אירעה שגיאה בעת שמירת התוכן.', 'error')

        # Always redirect back to the add page after POST, regardless of success/error
        return redirect(url_for('add_content'))

    # GET request: Render the add content form
    # Load series from Firebase for episode form dropdown on GET request
    available_series = load_series_list_for_add_page()

    return render_template('add.html',
                           user=user,
                           categories=[c for c in CATEGORIES if c != 'ללא'], # Categories excluding 'ללא' for movie/series dropdown
                           available_series=available_series,
                           current_year=datetime.datetime.utcnow().year,
                           admin_emails=ADMIN_EMAILS
                           )


@app.route('/movies')
def all_movies():
    """Displays all movies from Firebase in a grid."""
    user = session.get('user')
    current_language = session.get('language', 'he') # Get language from session, default to 'he'
    current_year = datetime.datetime.utcnow().year

    # Load ALL movies from Firebase
    all_movies_data = load_movies_data() # Includes Hebrew fields if exist

    # Define how many items to show per page initially and on "Load More"
    items_per_page = 15 # You can adjust this number, matching series for consistency

    logging.info(f"Rendering all_movies page with {len(all_movies_data)} movies. Initial {items_per_page} items will be shown.")

    return render_template('movies.html',
                           movies=all_movies_data, # Pass all movies
                           user=user,
                           current_year=current_year,
                           admin_emails=ADMIN_EMAILS,
                           current_language=current_language, # Pass the current language
                           items_per_page=items_per_page # ADDED: Pass the number of items per page
                           )




@app.route('/series') # Define the new route
def all_series():
    """Displays all series (basic details) from Firebase in a grid with pagination."""
    user = session.get('user')
    current_language = session.get('language', 'he') # Get language from session, default to 'he'
    current_year = datetime.datetime.utcnow().year

    # Load ALL basic series data from Firebase (excluding seasons/episodes)
    all_series_data = load_series_data_for_index() # This function is now optimized

    # Define how many items to show per page initially and on "Load More"
    items_per_page = 15 # You can adjust this number

    # Log how many series were loaded (basic details)
    logging.info(f"Rendering all_series page with {len(all_series_data)} series (basic details). Initial {items_per_page} items will be shown.")


    # Render the new template, passing the series data, items_per_page, and current language
    return render_template('SeriesTV.html',
                           series=all_series_data, # Pass all series basic data to the template
                           user=user,
                           current_year=current_year,
                           admin_emails=ADMIN_EMAILS,
                           current_language=current_language, # Pass the current language
                           items_per_page=items_per_page # Pass the number of items per page
                           )


# --- Keep Alive Thread ---
WEBSITE_URL = "https://moviesil.onrender.com/"  # כתובת האתר שלך
INTERVAL_MINUTES = 4  # זמן בין בקשות בשביל לשמור על האתר ער (בדקות)

def keep_website_alive(url, interval_minutes):
    """
    שולח בקשת HTTP לכתובת ה-URL כל פרק זמן מוגדר כדי למנוע כיבוי.
    """
    interval_seconds = interval_minutes * 60
    print(f"[*] החל תהליך רקע לשמירה על האתר {url} פעיל. שליחת בקשה כל {interval_minutes} דקות.")
    while True:
        time.sleep(interval_seconds) # המתן את פרק הזמן המוגדר
        try:
            # שלח בקשת GET פשוטה
            response = requests.get(url)
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # בדוק את קוד הסטטוס של התגובה
            if response.status_code == 200:
                print(f"[{current_time}] בקשת 'Keep-Alive' ל- {url} הצליחה. סטטוס: {response.status_code}")
            else:
                print(f"[{current_time}] בקשת 'Keep-Alive' ל- {url} נכשלה או החזירה סטטוס שאינו 200. סטטוס: {response.status_code}")

        except requests.exceptions.RequestException as e:
            # טיפול בשגיאות שקשורות לבקשה (למשל, בעיות רשת)
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{current_time}] שגיאה בבקשת 'Keep-Alive' ל- {url}: {e}")
        except Exception as e:
            # טיפול בשגיאות אחרות בלתי צפויות
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{current_time}] שגיאה בלתי צפויה בתהליך 'Keep-Alive' ל- {url}: {e}")

# --- הוסף את הקוד הבא בחלק הראשי של הסקריפט שלך, לפני שהשרת מתחיל לרוץ ---

# יצירת אובייקט Thread
# daemon=True גורם ל-thread להיסגר אוטומטית כשהתוכנית הראשית נסגרת
# The Keep Alive thread should *not* be daemon if you want to ensure it tries to keep the app awake on platforms that stop non-daemon threads.
# However, for simple hosting like Render/Heroku where the main process exiting kills everything, daemon is fine. Let's keep it daemon.
# We will start this thread inside the conditional block for running the app.


# --- TMDB API Functions (for Trailers) ---
def get_trailer_from_imdb(imdb_id, tmdb_api_key):
    """Fetches a YouTube trailer URL for a given IMDb ID using TMDB API."""
    if not tmdb_api_key or tmdb_api_key == 'YOUR_TMDB_API_KEY':
         logging.warning("TMDB_API_KEY is not set. Cannot fetch trailer.")
         return None

    find_url = f"https://api.themoviedb.org/3/find/{imdb_id}"
    params = {'external_source': 'imdb_id'}

    # Use the retry helper for the find request
    find_data = fetch_tmdb_data_with_retry(find_url, params, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)

    if not find_data:
        logging.warning(f"TMDB find failed or returned no data for IMDb ID: {imdb_id} after retries.")
        return None

    # Check if movie results are found
    if not find_data.get('movie_results'):
        logging.info(f"No movie results found on TMDB for IMDb ID: {imdb_id}")
        return None

    # Get the first movie ID found (assuming it's the correct one)
    movie_id = find_data['movie_results'][0].get('id')
    if not movie_id:
         logging.warning(f"TMDB find response for {imdb_id} did not contain movie ID.")
         return None

    videos_url = f"{TMDB_BASE_URL}/movie/{movie_id}/videos"
    params = {} # No specific params needed for videos other than API key (added by helper)

    # Use the retry helper for the videos request
    videos_data = fetch_tmdb_data_with_retry(videos_url, params, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)

    if not videos_data:
        logging.warning(f"TMDB videos fetch failed or returned no data for movie ID {movie_id} (IMDb ID: {imdb_id}) after retries.")
        return None


    # Search for a YouTube trailer
    for video in videos_data.get('results', []):
        if video.get('site') == 'YouTube' and video.get('type') == 'Trailer' and video.get('key'):
            youtube_url = f"https://www.youtube.com/watch?v={video['key']}"
            logging.info(f"Found trailer for {imdb_id}: {youtube_url}")
            return youtube_url

    logging.info(f"No YouTube trailer found on TMDB for movie ID: {movie_id} (IMDb ID: {imdb_id})")
    return None # No suitable trailer found





@app.route('/api/get_trailer/<imdb_id>')
def api_get_trailer(imdb_id):
    """API endpoint to get trailer URL for a given IMDb ID."""
    # Basic validation for IMDb ID format
    imdb_id_pattern = re.compile(r'^tt\d{7,}$')
    if not imdb_id or not imdb_id_pattern.match(imdb_id):
         logging.warning(f"API get trailer called with invalid IMDb ID format: {imdb_id}")
         return jsonify({"error": "Invalid IMDb ID format"}), 400

    logging.info(f"Attempting to fetch trailer for IMDb ID: {imdb_id}")
    # Pass the API key explicitly, although the helper adds it, this makes it clear
    trailer_url = get_trailer_from_imdb(imdb_id, TMDB_API_KEY)

    if trailer_url:
        return jsonify({"trailer_url": trailer_url})
    else:
        return jsonify({"error": "Trailer not found"}), 404






# --- New TMDB API Helper Functions (for Hebrew data and trending) ---
# Using TMDB for finding Hebrew titles/posters and trending lists

def fetch_tmdb_data_with_retry(url, params, max_retries, base_delay):
    """Fetches data from a TMDB URL with retry logic."""
    if not TMDB_API_KEY or TMDB_API_KEY == 'YOUR_TMDB_API_KEY':
         logging.warning("TMDB_API_KEY is not set. Cannot fetch data from TMDB.")
         return None

    # Ensure API key is always included unless already in params (defensive)
    if 'api_key' not in params:
        params['api_key'] = TMDB_API_KEY

    # Default language if not specified
    if 'language' not in params:
         params['language'] = 'en-US' # Default language

    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=15) # Add timeout
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            data = response.json()
            return data # Success!

        except requests.exceptions.RequestException as e:
            logging.warning(f"Attempt {attempt + 1}/{max_retries} failed for {url}: {e}")
            if attempt < max_retries - 1:
                # Exponential backoff with jitter
                # Cap the delay to avoid excessive waiting in a web request context
                # Use a minimum delay of 0.1s for jitter
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), 10) # Cap delay at 10 seconds
                # Ensure minimum delay
                delay = max(delay, 0.1)
                logging.info(f"Retrying in {delay:.2f} seconds...")
                time.sleep(delay)
            else:
                logging.error(f"Max retries ({max_retries}) exceeded for {url}. Skipping TMDB call.")
                return None # Failed after all retries
        except Exception as e:
            logging.error(f"Unexpected error during TMDB fetch attempt {attempt + 1}/{max_retries} for {url}: {e}", exc_info=True)
            return None # Return None for unexpected errors too

def get_tmdb_info(imdb_id):
    """Finds TMDB ID and media type (movie/tv) for a given IMDb ID."""
    url = f"{TMDB_BASE_URL}/find/{imdb_id}"
    params = {'external_source': 'imdb_id'}
    # We don't add language=he here because /find doesn't support it well for initial match

    data = fetch_tmdb_data_with_retry(url, params, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)

    if data:
        if data.get('movie_results'):
            # Check if results list is not empty and has at least one item
            if data['movie_results'] and data['movie_results'][0].get('id'):
                 tmdb_id = data['movie_results'][0].get('id')
                 logging.debug(f"Found TMDB movie ID {tmdb_id} for IMDb ID {imdb_id}")
                 return tmdb_id, 'movie'
            else:
                logging.warning(f"TMDB find response for {imdb_id} movie results is empty or missing ID.")
        elif data.get('tv_results'):
            # Check if results list is not empty and has at least one item
            if data['tv_results'] and data['tv_results'][0].get('id'):
                 tmdb_id = data['tv_results'][0].get('id')
                 logging.debug(f"Found TMDB TV ID {tmdb_id} for IMDb ID {imdb_id}")
                 return tmdb_id, 'tv'
            else:
                logging.warning(f"TMDB find response for {imdb_id} TV results is empty or missing ID.")
        else:
            logging.info(f"No movie or TV results found on TMDB for IMDb ID: {imdb_id}")
    else:
         logging.warning(f"Failed to get TMDB info for IMDb ID {imdb_id} after retries.")

    return None, None # Not found or failed

def get_hebrew_details(tmdb_id, media_type):
    """Gets Hebrew title and poster path for a TMDB ID and media type."""
    if not tmdb_id or media_type not in ['movie', 'tv']:
        logging.error(f"Invalid TMDB ID ({tmdb_id}) or media type ({media_type}) passed to get_hebrew_details.")
        return None, None # Invalid input

    endpoint = f"movie/{tmdb_id}" if media_type == 'movie' else f"tv/{tmdb_id}"
    url = f"{TMDB_BASE_URL}/{endpoint}"
    params = {'language': 'he-IL'} # Use he-IL for Hebrew details

    data = fetch_tmdb_data_with_retry(url, params, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)

    hebrew_name = None
    hebrew_poster_url = None

    if data:
        # TMDB returns 'title' for movies and 'name' for TV shows
        hebrew_name = data.get('title') if media_type == 'movie' else data.get('name')
        poster_path = data.get('poster_path')

        if not hebrew_name:
            logging.info(f"No Hebrew title found on TMDB for {media_type} ID {tmdb_id}.")
        # Poster path can be None even if data is successful

        # Construct full poster URL if path exists
        if poster_path:
             hebrew_poster_url = f"{TMDB_IMAGE_BASE_URL}{poster_path}"

        logging.debug(f"Fetched Hebrew details for TMDB ID {tmdb_id}: Name='{hebrew_name}', Poster URL='{hebrew_poster_url}'")

    else:
        logging.warning(f"Failed to get Hebrew details for TMDB {media_type} ID {tmdb_id} after retries.")

    return hebrew_name, hebrew_poster_url

# --- New functions for getting trending lists from TMDB ---
def get_trending_movies_tmdb():
    """Fetches trending movie TMDB IDs for the week."""
    url = f'{TMDB_BASE_URL}/trending/movie/week'
    params = {} # Language is not critical here, just getting the list

    data = fetch_tmdb_data_with_retry(url, params, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)

    trending_ids = []
    if data and data.get('results'):
        today = datetime.date.today()
        for item in data['results']:
            # Check release date - only include movies released on or before today
            release_date_str = item.get('release_date')
            if release_date_str:
                 try:
                     release_date = datetime.strptime(release_date_str, "%Y-%m-%d").date()
                     if release_date > today:
                          logging.debug(f"Skipping trending movie {item.get('title')} ({item.get('id')}) - not yet released.")
                          continue # Skip movies not yet released
                 except ValueError:
                      logging.warning(f"Invalid release date format for trending movie {item.get('id')}: {release_date_str}")
                      # Decide whether to include or skip items with bad dates - let's include but log
            else:
                 logging.debug(f"No release date for trending movie {item.get('id')}. Including.") # Include if no date provided

            tmdb_id = item.get('id')
            if tmdb_id:
                 trending_ids.append(tmdb_id)
            if len(trending_ids) >= 15: # Limit to top 15
                 break

    logging.info(f"Fetched {len(trending_ids)} trending movie TMDB IDs from TMDB.")
    return trending_ids # Returns list of TMDB IDs

def get_trending_tv_tmdb():
    """Fetches trending TV show TMDB IDs for the week."""
    url = f'{TMDB_BASE_URL}/trending/tv/week'
    params = {} # Language is not critical here

    data = fetch_tmdb_data_with_retry(url, params, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)

    trending_ids = []
    if data and data.get('results'):
        today = datetime.date.today()
        for item in data['results']:
            # Check first air date - only include shows aired on or before today
            first_air_date_str = item.get('first_air_date')
            if first_air_date_str:
                 try:
                     first_air_date = datetime.strptime(first_air_date_str, "%Y-%m-%d").date()
                     if first_air_date > today:
                           logging.debug(f"Skipping trending TV show {item.get('name')} ({item.get('id')}) - not yet aired.")
                           continue # Skip shows not yet aired
                 except ValueError:
                      logging.warning(f"Invalid first air date format for trending TV show {item.get('id')}: {first_air_date_str}")
                      # Decide whether to include or skip items with bad dates - let's include but log
            else:
                 logging.debug(f"No first air date for trending TV show {item.get('id')}. Including.") # Include if no date provided


            tmdb_id = item.get('id')
            if tmdb_id:
                 trending_ids.append(tmdb_id)
            if len(trending_ids) >= 15: # Limit to top 15
                 break

    logging.info(f"Fetched {len(trending_ids)} trending TV TMDB IDs from TMDB.")
    return trending_ids # Returns list of TMDB IDs

# --- New function to get IMDb ID from TMDB ID ---
def get_imdb_id_from_tmdb(tmdb_id, media_type):
    """Gets the IMDb ID for a given TMDB ID and media type (movie/tv)."""
    if not tmdb_id or media_type not in ['movie', 'tv']:
         logging.error(f"Invalid TMDB ID ({tmdb_id}) or media type ({media_type}) passed to get_imdb_id_from_tmdb.")
         return None

    if media_type == 'movie':
        # For movies, the main details endpoint includes imdb_id
        url = f"{TMDB_BASE_URL}/movie/{tmdb_id}"
        params = {} # No specific params needed
        data = fetch_tmdb_data_with_retry(url, params, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)
        if data:
            imdb_id = data.get('imdb_id')
            if imdb_id:
                 logging.debug(f"Got IMDb ID {imdb_id} for TMDB movie {tmdb_id}.")
                 return imdb_id
            else:
                logging.warning(f"TMDB movie details for ID {tmdb_id} did not contain imdb_id.")
                return None # imdb_id not found in response
        else:
            logging.warning(f"Failed to get TMDB movie details for ID {tmdb_id} after retries.")
            return None # Fetch failed


    elif media_type == 'tv':
        # For TV, need the external_ids endpoint
        url = f"{TMDB_BASE_URL}/tv/{tmdb_id}/external_ids"
        params = {} # No specific params needed
        data = fetch_tmdb_data_with_retry(url, params, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)
        if data:
             imdb_id = data.get('imdb_id')
             if imdb_id:
                  logging.debug(f"Got IMDb ID {imdb_id} for TMDB TV {tmdb_id}.")
                  return imdb_id
             else:
                  logging.warning(f"TMDB TV external IDs for ID {tmdb_id} did not contain imdb_id.")
                  return None # imdb_id not found in response
        else:
            logging.warning(f"Failed to get TMDB TV external IDs for ID {tmdb_id} after retries.")
            return None # Fetch failed

    return None # Should not reach here

# --- New function to update trending data in Firebase ---
def update_trending_data_in_firebase():
    """
    Fetches trending data from TMDB, maps to IMDb IDs, clears existing /Top
    and saves the new lists to Firebase. Designed for background thread.
    """
    if not FIREBASE_INITIALIZED:
         logging.error("Firebase not initialized. Cannot update trending data.")
         return

    logging.info("Starting trending data update from TMDB...")
    start_time = time.time()

    trending_movies_tmdb_ids = get_trending_movies_tmdb()
    trending_series_tmdb_ids = get_trending_tv_tmdb()

    # Convert TMDB IDs to IMDb IDs
    trending_movies_imdb_ids = []
    for tmdb_id in trending_movies_tmdb_ids:
        imdb_id = get_imdb_id_from_tmdb(tmdb_id, 'movie')
        if imdb_id and imdb_id.startswith('tt'): # Basic validation
             trending_movies_imdb_ids.append(imdb_id)
        else:
             logging.warning(f"Could not get valid IMDb ID for trending TMDB movie ID {tmdb_id}. Skipping.")

    trending_series_imdb_ids = []
    for tmdb_id in trending_series_tmdb_ids:
        imdb_id = get_imdb_id_from_tmdb(tmdb_id, 'tv')
        if imdb_id and imdb_id.startswith('tt'): # Basic validation
             trending_series_imdb_ids.append(imdb_id)
        else:
             logging.warning(f"Could not get valid IMDb ID for trending TMDB TV ID {tmdb_id}. Skipping.")


    logging.info(f"Mapped {len(trending_movies_imdb_ids)} trending movie IMDb IDs and {len(trending_series_imdb_ids)} trending series IMDb IDs.")

    # Save to Firebase
    try:
        ref_top = db.reference('/Top')
        # Clear existing data first as requested
        ref_top.set({})
        logging.info("Cleared existing /Top data in Firebase.")

        # Save new lists
        if trending_movies_imdb_ids:
             db.reference('/Top/Movies').set(trending_movies_imdb_ids)
             logging.info(f"Saved {len(trending_movies_imdb_ids)} trending movie IMDb IDs to Firebase /Top/Movies.")
        else:
             logging.warning("No trending movie IMDb IDs to save.")

        if trending_series_imdb_ids:
             db.reference('/Top/Series').set(trending_series_imdb_ids)
             logging.info(f"Saved {len(trending_series_imdb_ids)} trending series IMDb IDs to Firebase /Top/Series.")
        else:
             logging.warning("No trending series IMDb IDs to save.")

        end_time = time.time()
        duration = end_time - start_time
        logging.info(f"Trending data update complete. Took {duration:.2f} seconds.")

    except Exception as e:
        logging.error(f"Error saving trending data to Firebase: {e}", exc_info=True)
        # Consider alerting on critical failure

# --- New function for daily update thread ---
def daily_trending_update_thread(interval_hours=24):
    """Background thread function to update trending data daily."""
    # Wait a bit on startup before the first update attempt, to allow app resources to settle
    # Also avoids potential race condition if app starts before network is fully ready
    initial_delay_seconds = 60 # Wait 1 minute initially
    logging.info(f"Trending update thread starting. Initial delay of {initial_delay_seconds} seconds.")
    time.sleep(initial_delay_seconds)

    # Perform the first update on startup after delay
    update_trending_data_in_firebase()

    # Calculate interval in seconds
    interval_seconds = interval_hours * 3600

    logging.info(f"Daily trending update thread running. Next update in {interval_hours} hours ({interval_seconds} seconds).")
    while True:
        # Sleep for the interval, check Firebase status before attempting update
        time.sleep(interval_seconds)
        if FIREBASE_INITIALIZED:
            logging.info("Performing scheduled daily trending data update.")
            update_trending_data_in_firebase()
            logging.info(f"Scheduled daily trending update complete. Next update in {interval_hours} hours.")
        else:
            logging.warning("Firebase not initialized. Skipping scheduled daily trending update.")

# Start the daily update thread if Firebase is initialized
# This will be called within the __main__ block
trending_update_thread = threading.Thread(target=daily_trending_update_thread, daemon=True)


# --- Error Handlers ---
@app.errorhandler(403)
def forbidden(e):
    user = session.get('user')
    current_language = session.get('language', 'he') # Get language for error page
    current_year = datetime.datetime.utcnow().year
    logging.warning(f"403 Forbidden: {request.path} - {e}")
    return render_template('403.html', user=user, current_year=current_year, current_language=current_language), 403

@app.errorhandler(404)
def page_not_found(e):
    user = session.get('user')
    current_language = session.get('language', 'he') # Get language for error page
    current_year = datetime.datetime.utcnow().year
    logging.warning(f"404 Not Found: {request.path} - {e}")
    return render_template('404.html', user=user, current_year=current_year, current_language=current_language), 404

@app.errorhandler(500)
def internal_server_error(e):
    tb_str = traceback.format_exc()
    logging.error(f"Internal Server Error: {request.path} - {e}\n{tb_str}", exc_info=True)
    user = session.get('user')
    current_language = session.get('language', 'he') # Get language for error page
    current_year = datetime.datetime.utcnow().year
    return render_template('500.html', user=user, current_year=current_year, current_language=current_language), 500


if __name__ == '__main__':
    # Ensure Firebase is initialized before running the app and starting threads
    # The try/except block with _apps check handles reloader
    # Also check if initialization actually succeeded before running
    if firebase_admin._apps and FIREBASE_INITIALIZED:
        # Check if Firebase creds were successfully loaded
        # This check firebase_admin._apps['[DEFAULT]'].options.get('credential') is more robust
        # than just checking if firebase_admin._apps is not empty, as initialization might
        # have failed without raising an immediate exception if cred was None.
        try:
            # Attempt to access the default app's options. This will raise an exception if not initialized correctly.
             default_app_creds = firebase_admin._apps['[DEFAULT]'].options.get('credential')
             if default_app_creds is not None:
                logging.info("Firebase default app credential check passed. Starting app.")

                # Start the Keep Alive thread
                keep_alive_thread = threading.Thread(target=keep_website_alive, args=(WEBSITE_URL, INTERVAL_MINUTES), daemon=True)
                keep_alive_thread.start()
                logging.info("Keep alive thread started.")

                # Start the Daily Trending Update thread
                # It will perform the first update after a short delay, then run daily
                trending_update_thread.start()
                logging.info("Daily trending update thread started.")

                # Run the Flask application
                port = int(os.environ.get('PORT', 5000))
                # debug=True should only be used in development
                app.run(host='0.0.0.0', port=port, debug=True)
             else:
                 logging.error("Application not started: Firebase default app credential is None.")
                 sys.exit(1) # Exit if Firebase couldn't initialize credentials correctly
        except KeyError:
            # If firebase_admin._apps['[DEFAULT]'] doesn't exist, it wasn't initialized correctly.
            logging.error("Application not started: Firebase default app was not initialized.")
            sys.exit(1) # Exit if Firebase initialization failed
        except Exception as e:
             logging.error(f"Application not started: Unexpected error during Firebase check: {e}", exc_info=True)
             sys.exit(1) # Exit on other unexpected errors

    else:
        logging.error("Application not started because Firebase initialization failed.")
        sys.exit(1) # Exit if Firebase initialization failed upfront
