import eventlet
eventlet.monkey_patch()

import datetime
import traceback
import os
import requests
import json
import re # Import re for regex validation
import random # Import random for jitter in retry
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify
from authlib.integrations.flask_client import OAuth
import firebase_admin
from firebase_admin import credentials, db
import logging # Import logging for better error handling
import threading
import time
import string
import uuid

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
TMDB_BASE_DELAY_SECONDS = 1 # Shorter initial delay for web requests


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


FIREBASE_DATABASE_URL = os.environ.get('FIREBASE_DATABASE_URL', 'https://sratimsonline-default-rtdb.firebaseio.com/')

try:
    if not firebase_admin._apps:
        cred = None
        firebase_credentials_json = os.environ.get('FIREBASE_CREDENTIALS_JSON')
        
        if firebase_credentials_json:
            logging.info("Found Firebase credentials in environment variable. Initializing from JSON string.")
            cred_dict = json.loads(firebase_credentials_json)
            cred = credentials.Certificate(cred_dict)
        else:
            logging.info("FIREBASE_CREDENTIALS_JSON env var not found. Falling back to local file path.")
            FIREBASE_SERVICE_ACCOUNT_KEY_PATH = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY_PATH', './firebase.json')
            if not os.path.exists(FIREBASE_SERVICE_ACCOUNT_KEY_PATH):
                logging.error(f"Firebase service account key file not found at {FIREBASE_SERVICE_ACCOUNT_KEY_PATH}")
            else:
                cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_KEY_PATH)

        if cred:
            firebase_admin.initialize_app(cred, {
                'databaseURL': FIREBASE_DATABASE_URL
            })
            logging.info("Firebase initialized successfully.")
        else:
            logging.error("Firebase initialization failed: No valid credentials found.")
    else:
        logging.info("Firebase already initialized.")
except Exception as e:
    logging.error(f"Error initializing Firebase: {e}", exc_info=True)


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

ACTIVE_USERS = {}
ACTIVITY_TIMEOUT_SECONDS = 300



@app.before_request
def track_user_activity():
    if '_id' not in session:
        session['_id'] = str(uuid.uuid4())
    
    session_id = session['_id']
    ACTIVE_USERS[session_id] = time.time()



DATA_FILE = 'data.json'
APP_DATA = {'movies': {}, 'series': {}}

def _fetch_movies_from_firebase():
    try:
        ref = db.reference('/Movies')
        movies = ref.get()
        movies_with_type = {}
        if movies:
            for imdb_id, details in movies.items():
                if isinstance(details, dict):
                    details['type'] = 'movie'
                    movies_with_type[imdb_id] = details
                else:
                    logging.warning(f"Skipping non-dict movie entry during Firebase fetch: {imdb_id}")
        return movies_with_type if movies_with_type is not None else {}
    except Exception as e:
        logging.error(f"Error fetching movies directly from Firebase: {e}", exc_info=True)
        return {}

def _fetch_series_from_firebase():
    try:
        ref = db.reference('/Series')
        series_data = ref.get()
        series_with_type = {}
        if series_data:
            for imdb_id, details in series_data.items():
                 if isinstance(details, dict):
                    details['type'] = 'series'
                    series_with_type[imdb_id] = details
                 else:
                    logging.warning(f"Skipping non-dict series entry during Firebase fetch: {imdb_id}")
        return series_with_type if series_with_type is not None else {}
    except Exception as e:
        logging.error(f"Error fetching series directly from Firebase: {e}", exc_info=True)
        return {}

def refresh_data_from_firebase():
    global APP_DATA
    logging.info("Starting data refresh from Firebase...")
    movies = _fetch_movies_from_firebase()
    series = _fetch_series_from_firebase()

    combined_data = {
        'movies': movies,
        'series': series
    }

    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(combined_data, f, ensure_ascii=False, indent=4)
        logging.info(f"Successfully saved combined data to {DATA_FILE}.")
        APP_DATA = combined_data
        return True
    except Exception as e:
        logging.error(f"Failed to write data to {DATA_FILE}: {e}", exc_info=True)
        return False

def load_data_from_json():
    global APP_DATA
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            APP_DATA = json.load(f)
        logging.info(f"Successfully loaded data from {DATA_FILE} into memory.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(f"Error loading {DATA_FILE}: {e}. Will attempt to rebuild from Firebase.", exc_info=True)
        refresh_data_from_firebase()

def load_movies_data():
    return APP_DATA.get('movies', {})

def load_series_data_for_index():
    series_dict = APP_DATA.get('series', {})
    series_for_display = {}
    if series_dict:
        for imdb_id, details in series_dict.items():
            if isinstance(details, dict):
                basic_details = {
                    'imdbID': imdb_id,
                    'title': details.get('title', 'כותרת לא ידועה'),
                    'poster': details.get('poster', 'N/A'),
                    'HebrewName': details.get('HebrewName'),
                    'HebrewPoster': details.get('HebrewPoster'),
                    'category': details.get('category', 'ללא'),
                    'type': 'series',
                    'genre': details.get('genre', 'N/A'),
                    'imdbRating': details.get('imdbRating', 'N/A'),
                    'year': details.get('year', 'N/A')
                }
                series_for_display[imdb_id] = basic_details
    return series_for_display

def load_series_list_for_add_page():
    series_dict = APP_DATA.get('series', {})
    available_series_list = []
    if series_dict:
        for imdb_id, details in series_dict.items():
             if isinstance(details, dict):
                 display_title = details.get('HebrewName') or details.get('title', 'Untitled Series')
                 available_series_list.append({
                    "id": imdb_id,
                    "title": display_title
                 })
    return available_series_list

def load_full_series_details(imdb_id):
    return APP_DATA.get('series', {}).get(imdb_id)

def load_movie_details(imdb_id):
    return APP_DATA.get('movies', {}).get(imdb_id)


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




def categorize_content(movies_data, series_data):
    categorized_items = {}
    for cat in CATEGORIES:
        if cat != "ללא":
            categorized_items[cat] = []

    all_items = {}
    if movies_data:
        all_items.update(movies_data)
    if series_data:
        all_items.update(series_data)

    if not all_items:
        logging.info("No movies or series data to categorize.")
        return {cat: [] for cat in CATEGORIES if cat != "ללא"}

    for imdb_id, item_details in all_items.items():
        if not isinstance(item_details, dict):
            logging.warning(f"Skipping non-dict entry in all_items: {imdb_id}")
            continue

        title = item_details.get('title', 'כותרת לא ידועה')
        poster = item_details.get('poster', 'N/A')
        hebrew_name = item_details.get('HebrewName')
        hebrew_poster = item_details.get('HebrewPoster')
        category = item_details.get('category', 'ללא')
        item_type = item_details.get('type')

        if category in CATEGORIES and category != "ללא" and item_type in ['movie', 'series']:
             categorized_items[category].append({
                "id": imdb_id,
                "title": title,
                "poster": poster,
                "HebrewName": hebrew_name,
                "HebrewPoster": hebrew_poster,
                "type": item_type
             })
        elif category == "ללא":
            pass
        else:
             logging.warning(f"Item {imdb_id} ('{title}') has invalid/unknown category '{category}' or type '{item_type}'. Skipping index display.")
             pass

    return categorized_items



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
    current_language = session.get('language', 'he')
    greeting = get_greeting(user, current_language)

    movies_data = load_movies_data()
    series_data_for_index = load_series_data_for_index()

    num_movies = len(movies_data)
    num_series = len(series_data_for_index)

    categories = categorize_content(movies_data, series_data_for_index)

    current_year = datetime.datetime.utcnow().year

    all_content = []
    for category_items in categories.values():
        all_content.extend(category_items)

    random_hero_item = None
    if all_content:
        random_hero_item = random.choice(all_content)

    return render_template('index.html',
                           greeting=greeting,
                           categories=categories,
                           current_year=current_year,
                           user=user,
                           admin_emails=ADMIN_EMAILS,
                           current_language=current_language,
                           hero_item=random_hero_item,
                           num_movies=num_movies,
                           num_series=num_series,
                           active_users=len(ACTIVE_USERS)
                           )


@app.route('/api/active_users')
def active_users_count():
    return jsonify({"active_users": len(ACTIVE_USERS)})


def cleanup_inactive_users():
    while True:
        time.sleep(60)
        current_time = time.time()
        inactive_sessions = [
            sid for sid, last_seen in list(ACTIVE_USERS.items())
            if current_time - last_seen > ACTIVITY_TIMEOUT_SECONDS
        ]
        for sid in inactive_sessions:
            ACTIVE_USERS.pop(sid, None)

cleanup_thread = threading.Thread(target=cleanup_inactive_users, daemon=True)
cleanup_thread.start()

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
                           admin_emails=ADMIN_EMAILS, # Pass the list of admin emails
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
                 ref = db.reference(f'/Series/{series_imdb_id}/Seasons/{season_number}/Episodes/{episode_number}')
                 ref.update(episode_data)
                 logging.info(f"Episode S{season_number}E{episode_number} (IMDb ID: {episode_imdb_id_to_save}, Title: '{episode_data['title']}') added/updated in series {series_imdb_id}.")
                 flash(f'פרק "{episode_data["title"]}" (עונה {season_number}, פרק {episode_number}) נוסף/עודכן בהצלחה לסדרה!', 'success')


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





@app.route('/api/refresh_data', methods=['POST'])
def api_refresh_data():
    user = session.get('user')
    if not user or user.get('email') not in ADMIN_EMAILS:
        logging.warning(f"Unauthorized access attempt to /api/refresh_data by {user.get('email') if user else 'anonymous'}")
        abort(403)

    logging.info(f"Data refresh initiated by admin: {user.get('email')}")
    success = refresh_data_from_firebase()
    if success:
        return jsonify({"message": "Data cache updated successfully from Firebase."}), 200
    else:
        return jsonify({"error": "Failed to update data cache."}), 500


@app.route('/series')
def all_series():
    user = session.get('user')
    current_language = session.get('language', 'he')
    current_year = datetime.datetime.utcnow().year

    all_series_data = load_series_data_for_index()

    items_per_page = 15

    logging.info(f"Rendering all_series page with {len(all_series_data)} series (basic details). Initial {items_per_page} items will be shown.")

    return render_template('SeriesTV.html',
                           series=all_series_data,
                           user=user,
                           current_year=current_year,
                           admin_emails=ADMIN_EMAILS,
                           current_language=current_language,
                           items_per_page=items_per_page
                           )



WEBSITE_URL = "https://freemoviesil.onrender.com/"  # כתובת האתר שלך
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
keep_alive_thread = threading.Thread(target=keep_website_alive, args=(WEBSITE_URL, INTERVAL_MINUTES), daemon=True)

# התחלת ה-thread
keep_alive_thread.start()






# --- TMDB API Functions (for Trailers) ---
def get_trailer_from_imdb(imdb_id, tmdb_api_key):
    """Fetches a YouTube trailer URL for a given IMDb ID using TMDB API."""
    if not tmdb_api_key or tmdb_api_key == 'YOUR_TMDB_API_KEY':
         logging.warning("TMDB_API_KEY is not set.")
         return None

    find_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={tmdb_api_key}&external_source=imdb_id"
    try:
        find_response = requests.get(find_url, timeout=10)
        find_response.raise_for_status() # Raise HTTPError for bad responses
        find_data = find_response.json()

        # Check if movie results are found
        if not find_data.get('movie_results'):
            logging.info(f"No movie results found on TMDB for IMDb ID: {imdb_id}")
            return None

        # Get the first movie ID found (assuming it's the correct one)
        movie_id = find_data['movie_results'][0].get('id')
        if not movie_id:
             logging.warning(f"TMDB find response for {imdb_id} did not contain movie ID.")
             return None

        videos_url = f"https://api.themoviedb.org/3/movie/{movie_id}/videos?api_key={tmdb_api_key}"
        videos_response = requests.get(videos_url, timeout=10)
        videos_response.raise_for_status() # Raise HTTPError for bad responses
        videos_data = videos_response.json()

        # Search for a YouTube trailer
        for video in videos_data.get('results', []):
            if video.get('site') == 'YouTube' and video.get('type') == 'Trailer' and video.get('key'):
                youtube_url = f"https://www.youtube.com/watch?v={video['key']}"
                logging.info(f"Found trailer for {imdb_id}: {youtube_url}")
                return youtube_url

        logging.info(f"No YouTube trailer found on TMDB for movie ID: {movie_id} (IMDb ID: {imdb_id})")
        return None # No suitable trailer found

    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling TMDB API for IMDb ID {imdb_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logging.error(f"Unexpected error processing TMDB data for IMDb ID {imdb_id}: {e}", exc_info=True)
        return None





@app.route('/api/get_trailer/<imdb_id>')
def api_get_trailer(imdb_id):
    """API endpoint to get trailer URL for a given IMDb ID."""
    # Basic validation for IMDb ID format
    imdb_id_pattern = re.compile(r'^tt\d{7,}$')
    if not imdb_id or not imdb_id_pattern.match(imdb_id):
         logging.warning(f"API get trailer called with invalid IMDb ID format: {imdb_id}")
         return jsonify({"error": "Invalid IMDb ID format"}), 400

    logging.info(f"Attempting to fetch trailer for IMDb ID: {imdb_id}")
    trailer_url = get_trailer_from_imdb(imdb_id, TMDB_API_KEY)

    if trailer_url:
        return jsonify({"trailer_url": trailer_url})
    else:
        return jsonify({"error": "Trailer not found"}), 404






# --- New TMDB API Helper Functions (for Hebrew data) ---
# Using TMDB for finding Hebrew titles/posters as OMDB lacks good localization

def fetch_tmdb_data_with_retry(url, params, max_retries, base_delay):
    """Fetches data from a TMDB URL with retry logic."""
    if not TMDB_API_KEY or TMDB_API_KEY == 'YOUR_TMDB_API_KEY':
         logging.warning("TMDB_API_KEY is not set. Cannot fetch Hebrew data from TMDB.")
         return None

    params['api_key'] = TMDB_API_KEY # Ensure API key is always included
    params['language'] = params.get('language', 'en-US') # Default language if not specified

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
                # Cap the delay to avoid excessive waiting in a web request
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), 10) # Cap delay at 10 seconds
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
            tmdb_id = data['movie_results'][0].get('id')
            if tmdb_id:
                 logging.debug(f"Found TMDB movie ID {tmdb_id} for IMDb ID {imdb_id}")
                 return tmdb_id, 'movie'
        elif data.get('tv_results'):
            tmdb_id = data['tv_results'][0].get('id')
            if tmdb_id:
                 logging.debug(f"Found TMDB TV ID {tmdb_id} for IMDb ID {imdb_id}")
                 return tmdb_id, 'tv'
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
        if not poster_path:
             logging.info(f"No Hebrew poster path found on TMDB for {media_type} ID {tmdb_id}.")

        # Construct full poster URL if path exists
        if poster_path:
             hebrew_poster_url = f"{TMDB_IMAGE_BASE_URL}{poster_path}"

        logging.debug(f"Fetched Hebrew details for TMDB ID {tmdb_id}: Name='{hebrew_name}', Poster URL='{hebrew_poster_url}'")

    else:
        logging.warning(f"Failed to get Hebrew details for TMDB {media_type} ID {tmdb_id} after retries.")

    return hebrew_name, hebrew_poster_url






def get_recommendations(watched_ids, limit=15):
    all_movies = load_movies_data()
    all_series = load_series_data_for_index()
    all_content = {**all_movies, **all_series}

    if not watched_ids or not all_content:
        return []

    watched_items_details = []
    candidate_items = {}
    for imdb_id, details in all_content.items():
        if imdb_id in watched_ids:
            if isinstance(details, dict):
                watched_items_details.append(details)
        else:
            if isinstance(details, dict):
                candidate_items[imdb_id] = details

    if not watched_items_details or not candidate_items:
        return []

    profile = {
        'categories': {},
        'genres': {},
        'ratings': []
    }

    for item in watched_items_details:
        cat = item.get('category', 'ללא')
        if cat != 'ללא':
            profile['categories'][cat] = profile['categories'].get(cat, 0) + 1

        genres_str = item.get('genre', '')
        if genres_str and genres_str != 'N/A':
            for genre in [g.strip() for g in genres_str.split(',')]:
                profile['genres'][genre] = profile['genres'].get(genre, 0) + 1

        rating_str = item.get('imdbRating', 'N/A')
        if rating_str and rating_str != 'N/A':
            try:
                profile['ratings'].append(float(rating_str))
            except (ValueError, TypeError):
                pass

    if not profile['ratings']:
        avg_rating = 7.0
    else:
        avg_rating = sum(profile['ratings']) / len(profile['ratings'])

    scored_candidates = []
    for imdb_id, item in candidate_items.items():
        score = 0
        
        cat = item.get('category', 'ללא')
        if cat in profile['categories']:
            score += profile['categories'][cat] * 3

        genres_str = item.get('genre', '')
        if genres_str and genres_str != 'N/A':
            item_genres = {g.strip() for g in genres_str.split(',')}
            for genre in item_genres:
                if genre in profile['genres']:
                    score += profile['genres'][genre] * 2

        rating_str = item.get('imdbRating', 'N/A')
        if rating_str and rating_str != 'N/A':
            try:
                item_rating = float(rating_str)
                rating_diff = abs(item_rating - avg_rating)
                rating_score = max(0, 1 - (rating_diff / 5))
                score += rating_score * 1.5
            except (ValueError, TypeError):
                pass
        
        if score > 0:
            item_details_for_card = {
                "id": imdb_id,
                "title": item.get('title', 'כותרת לא ידועה'),
                "poster": item.get('poster', 'N/A'),
                "HebrewName": item.get('HebrewName'),
                "HebrewPoster": item.get('HebrewPoster'),
                "type": item.get('type'),
                "score": score
            }
            scored_candidates.append(item_details_for_card)

    scored_candidates.sort(key=lambda x: x['score'], reverse=True)
    return scored_candidates[:limit]


@app.route('/api/recommendations', methods=['POST'])
def api_recommendations():
    if not session.get('user'):
        return jsonify({"error": "User not authenticated"}), 401

    data = request.get_json()
    if not data or 'watched_ids' not in data:
        return jsonify({"error": "Missing watched_ids"}), 400

    watched_ids = data['watched_ids']
    if not isinstance(watched_ids, list):
        return jsonify({"error": "watched_ids must be a list"}), 400

    recommendations = get_recommendations(watched_ids)
    return jsonify(recommendations)







GROUPS_FILE = 'groups.json'
GROUPS_DATA = {}

def initialize_groups_file():
    global GROUPS_DATA
    try:
        with open(GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)
        GROUPS_DATA = {}
        logging.info(f"Initialized and cleared {GROUPS_FILE}.")
    except Exception as e:
        logging.error(f"Could not initialize groups file {GROUPS_FILE}: {e}", exc_info=True)
        if not os.path.exists(GROUPS_FILE):
             with open(GROUPS_FILE, 'w', encoding='utf-8') as f:
                json.dump({}, f)

def save_groups_data():
    global GROUPS_DATA
    try:
        with open(GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(GROUPS_DATA, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Error saving groups data to {GROUPS_FILE}: {e}", exc_info=True)

def generate_group_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def find_user_group(email):
    for group_id, group_data in GROUPS_DATA.items():
        if email in group_data.get('participants', {}):
            return group_id
    return None

@app.route('/stream/create/<imdb_id>')
def create_stream_group(imdb_id):
    user = session.get('user')
    if not user:
        flash('עליך להתחבר כדי ליצור קבוצת צפייה.', 'warning')
        return redirect(url_for('google_login'))

    user_email = user.get('email')
    if find_user_group(user_email):
        flash('אתה כבר חבר בקבוצת צפייה. עזוב את הקבוצה הנוכחית כדי ליצור אחת חדשה.', 'warning')
        return redirect(url_for('index'))

    movie = load_movie_details(imdb_id)
    if not movie:
        abort(404)

    group_id = generate_group_id()
    while group_id in GROUPS_DATA:
        group_id = generate_group_id()

    GROUPS_DATA[group_id] = {
        'imdb_id': imdb_id,
        'host_email': user_email,
        'participants': {
            user_email: {
                'name': user.get('name'),
                'picture': user.get('picture')
            }
        }
    }
    save_groups_data()
    logging.info(f"User {user_email} created group {group_id} for movie {imdb_id}.")
    return redirect(url_for('view_stream', group_id=group_id))

@app.route('/stream/join/<group_id>')
def join_stream_group(group_id):
    user = session.get('user')
    if not user:
        flash('עליך להתחבר כדי להצטרף לקבוצת צפייה.', 'warning')
        return redirect(url_for('index'))

    if group_id not in GROUPS_DATA:
        flash('קבוצת הצפייה שאתה מנסה להצטרף אליה אינה קיימת.', 'error')
        return redirect(url_for('index'))

    user_email = user.get('email')
    if find_user_group(user_email):
        flash('אתה כבר חבר בקבוצת צפייה.', 'warning')
        return redirect(url_for('index'))

    GROUPS_DATA[group_id]['participants'][user_email] = {
        'name': user.get('name'),
        'picture': user.get('picture')
    }
    save_groups_data()
    logging.info(f"User {user_email} joined group {group_id}.")
    return redirect(url_for('view_stream', group_id=group_id))

@app.route('/stream/view/<group_id>')
def view_stream(group_id):
    user = session.get('user')
    if not user:
        flash('עליך להתחבר כדי לצפות.', 'warning')
        return redirect(url_for('index'))

    if group_id not in GROUPS_DATA:
        flash('קבוצת הצפייה אינה קיימת או שהסתיימה.', 'error')
        return redirect(url_for('index'))

    group_data = GROUPS_DATA[group_id]
    user_email = user.get('email')
    if user_email not in group_data.get('participants', {}):
        flash('אינך חבר בקבוצת צפייה זו.', 'error')
        return redirect(url_for('index'))

    movie = load_movie_details(group_data['imdb_id'])
    if not movie:
        flash('הסרט המשויך לקבוצה זו לא נמצא.', 'error')
        del GROUPS_DATA[group_id]
        save_groups_data()
        return redirect(url_for('index'))

    return render_template('stream.html',
                           user=user,
                           movie=movie,
                           group_id=group_id,
                           group_data=group_data,
                           current_year=datetime.datetime.utcnow().year,
                           admin_emails=ADMIN_EMAILS)


@app.route('/api/stream/<group_id>/status')
def stream_status(group_id):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if group_id not in GROUPS_DATA:
        return jsonify({"error": "Group not found"}), 404

    group = GROUPS_DATA[group_id]
    user_email = user.get('email')

    if user_email not in group.get('participants', {}):
        return jsonify({"error": "Not a member of this group"}), 403

    return jsonify(group)

@app.route('/api/stream/<group_id>/leave', methods=['POST'])
def leave_stream(group_id):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if group_id not in GROUPS_DATA:
        return jsonify({}), 200

    user_email = user.get('email')
    group = GROUPS_DATA[group_id]

    if user_email in group['participants']:
        del group['participants'][user_email]

        if not group['participants']:
            del GROUPS_DATA[group_id]
            logging.info(f"Group {group_id} dissolved as last participant left.")
        elif user_email == group['host_email']:
            new_host_email = next(iter(group['participants']))
            group['host_email'] = new_host_email
            logging.info(f"Host left group {group_id}. New host is {new_host_email}.")

        save_groups_data()
        return jsonify({"success": True}), 200

    return jsonify({"error": "User not in group"}), 400

@app.route('/api/stream/<group_id>/kick', methods=['POST'])
def kick_from_stream(group_id):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if group_id not in GROUPS_DATA:
        return jsonify({"error": "Group not found"}), 404

    requester_email = user.get('email')
    group = GROUPS_DATA[group_id]

    if requester_email != group['host_email']:
        return jsonify({"error": "Only the host can kick participants."}), 403

    data = request.get_json()
    email_to_kick = data.get('email')

    if not email_to_kick or email_to_kick not in group['participants']:
        return jsonify({"error": "Participant not found"}), 400

    if email_to_kick == requester_email:
        return jsonify({"error": "Host cannot kick themselves"}), 400

    del group['participants'][email_to_kick]
    save_groups_data()
    logging.info(f"Host {requester_email} kicked {email_to_kick} from group {group_id}.")
    return jsonify({"success": True}), 200

@app.route('/api/stream/<group_id>/make_host', methods=['POST'])
def make_stream_host(group_id):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if group_id not in GROUPS_DATA:
        return jsonify({"error": "Group not found"}), 404

    requester_email = user.get('email')
    group = GROUPS_DATA[group_id]

    if requester_email != group['host_email']:
        return jsonify({"error": "Only the host can transfer ownership."}), 403

    data = request.get_json()
    new_host_email = data.get('email')

    if not new_host_email or new_host_email not in group['participants']:
        return jsonify({"error": "Participant not found"}), 400

    if new_host_email == requester_email:
        return jsonify({"error": "User is already the host"}), 400

    group['host_email'] = new_host_email
    save_groups_data()
    logging.info(f"Host {requester_email} made {new_host_email} the new host of group {group_id}.")
    return jsonify({"success": True}), 200





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




def initialize_data_cache():
    if os.path.exists(DATA_FILE):
        logging.info(f"Loading initial data from {DATA_FILE}...")
        load_data_from_json()
    else:
        logging.info(f"{DATA_FILE} not found. Creating it by fetching initial data from Firebase...")
        time.sleep(2)
        refresh_data_from_firebase()

initialize_data_cache()


if __name__ == '__main__':
    # Ensure Firebase is initialized before running the app
    # The try/except block with _apps check handles reloader
    # Also check if initialization actually succeeded before running
    if firebase_admin._apps:
        # Check if Firebase creds were successfully loaded
        # This check firebase_admin._apps['[DEFAULT]'].options.get('credential') is more robust
        # than just checking if firebase_admin._apps is not empty, as initialization might
        # have failed without raising an immediate exception if cred was None.
        try:
            # Attempt to access the default app's options. This will raise an exception if not initialized correctly.
             default_app_creds = firebase_admin._apps['[DEFAULT]'].options.get('credential')
             if default_app_creds is not None:
                logging.info("Firebase default app credential check passed.")
                port = int(os.environ.get('PORT', 5000))
                # debug=True should only be used in development
                app.run(host='0.0.0.0', port=port, debug=True)
             else:
                 logging.error("Application not started: Firebase default app credential is None.")
        except KeyError:
            # If firebase_admin._apps['[DEFAULT]'] doesn't exist, it wasn't initialized correctly.
            logging.error("Application not started: Firebase default app was not initialized.")
        except Exception as e:
             logging.error(f"Application not started: Unexpected error during Firebase check: {e}", exc_info=True)

    else:
        logging.error("Application not started because Firebase initialization failed.")
        # You might want to sys.exit(1) here in a real application













