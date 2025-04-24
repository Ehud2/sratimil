import datetime
import traceback
import os
import requests
import json
import re # Import re for regex validation
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify
from authlib.integrations.flask_client import OAuth
import firebase_admin
from firebase_admin import credentials, db
import logging # Import logging for better error handling

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- Security Warning: Do NOT hardcode secrets in production ---
# Use environment variables for production.
# For this example, default values are kept for demonstration,
# but replace these with your actual keys/secrets set as environment variables.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'moviesilsuperdupersecretkey')

# Replace these with your actual Google OAuth credentials from environment variables
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID', '367711020009-o70b96v4cv604acg2hqv60k8c5mjmhtr.apps.googleusercontent.com')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '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') # THIS IS A SECRET! MUST BE IN ENV VAR!
app.config['GOOGLE_DISCOVERY_URL'] = (
    'https://accounts.google.com/.well-known/openid-configuration'
)

# Replace with your actual OMDB API key from environment variables
OMDB_API_KEY = os.environ.get('OMDB_API_KEY', '4ea6447b') # THIS IS A SECRET! MUST BE IN ENV VAR!

# Replace with the actual admin email from environment variables
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'ehudverbin@gmail.com') # Consider a more secure admin check than just email

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
try:
    # Check if app is already initialized (prevents errors in debug/reloader mode)
    if not firebase_admin._apps:
        # Check if the service account file exists
        if not os.path.exists(FIREBASE_SERVICE_ACCOUNT_KEY_PATH):
            logging.error(f"Firebase service account key file not found at {FIREBASE_SERVICE_ACCOUNT_KEY_PATH}")
            # You might want to exit or raise an exception here in production
            # For now, just log and continue (will likely fail Firebase ops later)
            cred = None # Set cred to None so initialization fails
        else:
            cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_KEY_PATH)

        if cred:
            firebase_admin.initialize_app(cred, {
                'databaseURL': FIREBASE_DATABASE_URL
            })
            logging.info("Firebase initialized successfully.")
        else:
             logging.error("Firebase initialization failed due to missing credential file.")
    else:
        logging.info("Firebase already initialized.")
except Exception as e:
    logging.error(f"Error initializing Firebase: {e}", exc_info=True)
    # Handle error - maybe abort app startup or provide a fallback


# --- Categories ---
CATEGORIES = [
    "הסרטים הנצפים ביותר השבוע",
    "הסדרות הנצפים ביותר השבוע",
    "היקום הקולנועי של מארוול",
    "מלחמת הכוכבים",
    "הארי פוטר",
    "המסור",
    "הפארק הדרומי",
    "מהירים ועצבניים",
    "אקס-מן",
    "ספיידרמן",
    "ללא"
]

# --- Data Loading Functions (from Firebase) ---

def load_movies_data():
    """Loads all movies from Firebase, adding 'type: movie'."""
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
    """Loads series data for index display from Firebase, adding 'type: series'."""
    try:
        # We only need top-level series info for the index card
        ref = db.reference('/Series')
        series_dict = ref.get()
        series_for_index = {}
        if series_dict:
            for imdb_id, details in series_dict.items():
                 # Only include basic details for the index card
                 # Avoid fetching nested Seasons/Episodes here for performance
                if isinstance(details, dict):
                    series_for_index[imdb_id] = {
                        'imdbID': imdb_id,
                        'title': details.get('title', 'כותרת לא ידועה'),
                        'poster': details.get('poster', 'N/A'),
                        'category': details.get('category', 'ללא'),
                        'type': 'series' # Add type identifier
                    }
                else:
                    logging.warning(f"Skipping non-dict series entry: {imdb_id}")

        logging.info(f"Loaded {len(series_for_index)} series for index from Firebase.")
        return series_for_index if series_for_index is not None else {}
    except Exception as e:
        logging.error(f"Error loading series for index from Firebase: {e}", exc_info=True)
        return {}


def load_series_list_for_add_page():
    """Loads basic series info for the add page dropdown."""
    try:
        ref = db.reference('/Series')
        series_dict = ref.get() # Gets dictionary {imdb_id: series_details}
        available_series_list = []
        if series_dict:
             # Convert to the list format expected by the add.html dropdown
            for imdb_id, details in series_dict.items():
                 if isinstance(details, dict):
                     available_series_list.append({
                        "id": imdb_id,
                        "title": details.get('title', 'Untitled Series')
                     })
        logging.info(f"Loaded {len(available_series_list)} series for dropdown from Firebase.")
        return available_series_list
    except Exception as e:
        logging.error(f"Error loading series list from Firebase: {e}", exc_info=True)
        # Return dummy data or empty list on error
        return []


def load_full_series_details(imdb_id):
    """Loads all details for a single series, including seasons and episodes."""
    try:
        ref = db.reference(f'/Series/{imdb_id}')
        series_details = ref.get()
        if series_details:
             logging.info(f"Loaded full series details for ID {imdb_id} from Firebase.")
        else:
             logging.info(f"No full details found for series ID {imdb_id} in Firebase.")
        return series_details
    except Exception as e:
        logging.error(f"Error loading full series details for ID {imdb_id} from Firebase: {e}", exc_info=True)
        return None


def load_movie_details(imdb_id):
    """Loads details for a single movie from Firebase."""
    try:
        ref = db.reference(f'/Movies/{imdb_id}')
        movie_details = ref.get()
        if movie_details:
             logging.info(f"Loaded details for movie ID {imdb_id} from Firebase.")
        else:
             logging.info(f"No details found for movie ID {imdb_id} in Firebase.")
        return movie_details
    except Exception as e:
        logging.error(f"Error loading movie details for ID {imdb_id} from Firebase: {e}", exc_info=True)
        return None


def categorize_content(movies_data, series_data):
    """Categorizes movies and series loaded from Firebase for index display."""
    categorized_items = {}
    # Initialize categories excluding "ללא" as it's typically not a display category
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
        # Ensure all display categories exist, even if empty
        return {cat: [] for cat in CATEGORIES if cat != "ללא"}


    # all_items now contains {imdb_id: {details_including_type}}
    for imdb_id, item_details in all_items.items():
        if not isinstance(item_details, dict):
            logging.warning(f"Skipping non-dict entry in all_items: {imdb_id}")
            continue

        # Ensure required fields exist, provide defaults
        title = item_details.get('title', 'כותרת לא ידועה')
        poster = item_details.get('poster', 'N/A')
        category = item_details.get('category', 'ללא') # Default to 'ללא'
        item_type = item_details.get('type') # Get the type ('movie' or 'series')

        # Add to the correct category list if category is valid and not "ללא"
        # We need id, title, poster, and type for the item cards on index.html
        if category in CATEGORIES and category != "ללא" and item_type in ['movie', 'series']:
             categorized_items[category].append({
                "id": imdb_id,
                "title": title,
                "poster": poster,
                "type": item_type # Include type here
             })
        elif category == "ללא":
            pass # Don't display 'ללא' category on index
        else:
             logging.warning(f"Item {imdb_id} ('{title}') has invalid/unknown category '{category}' or type '{item_type}'. Skipping index display.")
             pass # Skip invalid categories or types

    # Optional: If you want to ensure categories with no items are still shown,
    # you might add checks here. But usually, you only show categories with items.
    # return {cat: items for cat, items in categorized_items.items() if items} # Only return categories with items

    return categorized_items


def get_greeting(user=None):
    now = datetime.datetime.now()
    current_hour = now.hour
    greeting_text = ""
    if 5 <= current_hour < 12:
        greeting_text = "בוקר טוב"
    elif 12 <= current_hour < 18:
        greeting_text = "צהריים טובים"
    elif 18 <= current_hour < 21:
        greeting_text = "ערב טוב"
    else:
        greeting_text = "לילה טוב"

    if user and user.get('name'):
        # Split name by space and take the first part (handle multi-word names)
        first_name = user['name'].split(' ')[0]
        return f"{greeting_text} {first_name}"
    else:
        return f"{greeting_text} אורח"

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

def get_omdb_details_api(imdb_id):
    """Gets full details for a specific IMDb ID from OMDB."""
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY':
         logging.warning("OMDB_API_KEY is not set.")
         return None
    params = {
        'apikey': OMDB_API_KEY,
        'i': imdb_id,
        'plot': 'full' # Request full plot
    }
    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        if data.get('Response') == 'True':
            logging.info(f"Successfully fetched OMDB details for ID '{imdb_id}'.")
            return data
        else:
             logging.info(f"OMDB details not found for ID '{imdb_id}': {data.get('Error', 'Unknown error')}")
             return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling OMDB details API for ID {imdb_id}: {e}", exc_info=True)
        return None

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
    if not imdb_id:
        logging.warning("API get details called with missing IMDb ID.")
        return jsonify({"Error": "Missing IMDb ID"}), 400

    details = get_omdb_details_api(imdb_id)
    if details:
        # Return the raw details object from OMDB
        return jsonify(details)
    else:
        return jsonify({"Error": "Details not found or API error"}), 404


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

# --- Main Routes ---
@app.route('/')
def index():
    user = session.get('user')
    greeting = get_greeting(user)

    # Load all content from Firebase for the index page
    movies_data = load_movies_data() # Includes type: 'movie'
    series_data_for_index = load_series_data_for_index() # Includes type: 'series'

    # Categorize them for standard display sections
    categories = categorize_content(movies_data, series_data_for_index)

    current_year = datetime.datetime.utcnow().year
    admin_email = ADMIN_EMAIL # Ensure this is passed

    # Pass the user object and categorized data to the template.
    # The 'continue watching' logic is handled client-side using localStorage.
    return render_template('index.html',
                           greeting=greeting,
                           categories=categories, # All categoried content for display and JS lookup
                           current_year=current_year,
                           user=user,
                           admin_email=admin_email # Pass the admin email
                           )

# --- Route for Single Movie Page ---
@app.route('/movie/<imdb_id>')
def movie_details(imdb_id):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    admin_email = ADMIN_EMAIL # Pass admin email to movie page for nav link

    # Validate IMDb ID format before querying
    if not imdb_id or not imdb_id.startswith('tt') or len(imdb_id) < 7:
         logging.warning(f"Attempted to access movie page with invalid IMDb ID format: {imdb_id}")
         abort(404) # Or redirect to error page

    # Load movie details from Firebase
    movie = load_movie_details(imdb_id)

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
                           admin_email=admin_email # Pass admin email for nav link
                           )

# --- Route for Single Series Page (NEW) ---
# Base series page
@app.route('/series/<imdb_id>')
# Series page with specific season and episode number
@app.route('/series/<imdb_id>/<int:season_number>/<int:episode_number>') # <-- CHANGED LINE
def series_details(imdb_id, season_number=None, episode_number=None): # <-- Keep parameters with defaults
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    admin_email = ADMIN_EMAIL

    # Validate Series IMDb ID format
    if not imdb_id or not imdb_id.startswith('tt') or len(imdb_id) < 7:
        logging.warning(f"Attempted to access series page with invalid Series IMDb ID format: {imdb_id}")
        abort(404)

    # Validate Season/Episode Numbers if provided (Flask <int:> handles non-integers,
    # but we can add checks for non-positive if needed, though JS also validates >=1)
    if season_number is not None and (season_number < 1):
        logging.warning(f"Attempted to access series page with invalid season number ({season_number}) for series {imdb_id}")
        # Option: abort(404) or proceed and let JS handle finding nothing for that number
        # Let's let JS handle it for flexibility.
        pass # Continue loading the page

    if episode_number is not None and (episode_number < 1):
        logging.warning(f"Attempted to access series page with invalid episode number ({episode_number}) for series {imdb_id}")
        # Option: abort(404) or proceed
        pass # Continue loading the page


    # Load full series details from Firebase (including Seasons/Episodes)
    series = load_full_series_details(imdb_id)

    # Check if found and if it's a series type
    # Handle potential old data without 'type' gracefully by checking existence
    if not series or (series.get('type') not in [None, 'series'] and series.get('type') != 'series'):
        logging.warning(f"Series details not found or is not of type 'series' for ID: {imdb_id}")
        abort(404)

    # Pass the full series object to the template.
    # The season_number and episode_number from the URL are *not* explicitly
    # passed as template variables here, because the JavaScript reads them
    # directly from window.location.pathname on page load.
    return render_template('series.html',
                           series=series, # Pass the full series data
                           user=user,
                           current_year=current_year,
                           admin_email=admin_email
                           )


@app.route('/add', methods=['GET', 'POST'])
def add_content():
    user = session.get('user')
    # Basic admin check (consider a more robust method for production)
    if not user or user.get('email') != ADMIN_EMAIL:
        logging.warning(f"Unauthorized access attempt to /add by {user.get('email') if user else 'anonymous'}")
        abort(403)

    # Load series from Firebase for episode form dropdown on GET request
    available_series = load_series_list_for_add_page()

    if request.method == 'POST':
        content_type = request.form.get('content_type')
        logging.info(f"Received POST for content type: {content_type}")

        try:
            if content_type == 'movie':
                # Get form data for movie
                imdb_id = request.form.get('movie_imdb_id', '').strip()
                # REMOVED: video_url = request.form.get('movie_video_url', '').strip() # Input removed
                category = request.form.get('movie_category', 'ללא') # Get category from form

                # Validate required fields for movie
                # video_url is no longer required from form
                if not imdb_id:
                    flash('שגיאה: שדה חובה (IMDb ID) חסר עבור סרט.', 'error')
                    return redirect(url_for('add_content'))

                if not imdb_id.startswith('tt') or len(imdb_id) < 7:
                     flash('שגיאה: פורמט IMDb ID לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                     return redirect(url_for('add_content'))

                # Fetch full details from OMDB server-side using the IMDb ID
                omdb_details = get_omdb_details_api(imdb_id)

                # Check if OMDB details were found and if the type is actually a movie
                if not omdb_details or omdb_details.get('Type', '').lower() != 'movie': # Case-insensitive check
                     flash(f'שגיאה: לא נמצאו פרטי סרט תקינים עבור IMDb ID "{imdb_id}" ב-OMDB.', 'error')
                     logging.warning(f"OMDB details not found or type is not 'movie' for ID {imdb_id}. OMDB Response: {omdb_details}")
                     return redirect(url_for('add_content'))

                # Construct movie data from OMDB details and form data
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
                    # REMOVED: 'video_url': video_url, # From form (input removed)
                    'category': category # From form
                }

                # Save movie data to Firebase under /Movies/{imdb_id}
                # This assumes the video URL will be added by a different mechanism
                # or is not needed in this specific data structure.
                ref = db.reference(f'/Movies/{imdb_id}')
                ref.set(movie_data)
                logging.info(f"Movie '{movie_data['title']}' ({imdb_id}) added to Firebase (without video URL from form).")
                flash(f'סרט "{movie_data["title"]}" נוסף בהצלחה!', 'success')

            elif content_type == 'series':
                # Get form data for series
                imdb_id = request.form.get('series_imdb_id', '').strip()
                category = request.form.get('series_category', 'ללא') # Get category for series

                 # Validate required fields for series
                if not imdb_id:
                    flash('שגיאה: שדה חובה (IMDb ID) חסר עבור סדרה.', 'error')
                    return redirect(url_for('add_content'))

                if not imdb_id.startswith('tt') or len(imdb_id) < 7:
                     flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                     return redirect(url_for('add_content'))

                # Fetch full details from OMDB server-side using the IMDb ID
                omdb_details = get_omdb_details_api(imdb_id)

                # Also explicitly check the type from OMDB response
                if not omdb_details or omdb_details.get('Type', '').lower() != 'series': # Case-insensitive check
                     flash(f'שגיאה: לא נמצאו פרטים לסדרה או שה-ID אינו של סדרה עבור "{imdb_id}" ב-OMDB.', 'error')
                     logging.warning(f"OMDB details not found or type is not 'series' for ID {imdb_id}. OMDB Response: {omdb_details}")
                     return redirect(url_for('add_content'))

                 # Construct series data from OMDB details and form data
                series_data = {
                    'imdbID': omdb_details.get('imdbID', imdb_id),
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
                    'poster': omdb_details.get('Poster', 'N/A'), # Use 'N/A' instead of default image if not found
                    'ratings': omdb_details.get('Ratings', []),
                    'metascore': omdb_details.get('Metascore', 'N/A'),
                    'imdbRating': omdb_details.get('imdbRating', 'N/A'),
                    'imdbVotes': omdb_details.get('imdbVotes', 'N/A'),
                    'type': 'series', # Explicitly set type as 'series'
                    'totalSeasons': omdb_details.get('totalSeasons', 'N/A'), # Specific to series
                    'category': category # From form
                 }


                # Save series data to Firebase under /Series/{imdb_id}
                # Use update instead of set to preserve existing Seasons/Episodes
                ref = db.reference(f'/Series/{imdb_id}')
                ref.update(series_data)
                logging.info(f"Series '{series_data['title']}' ({imdb_id}) added/updated in Firebase.")
                flash(f'סדרה "{series_data["title"]}" נוספה/עודכנה בהצלחה!', 'success')


            elif content_type == 'episode':
                 # Get form data for episode
                 series_imdb_id_select = request.form.get('episode_series_id')
                 manual_series_imdb_id = request.form.get('manual_episode_series_id', '').strip()
                 episode_imdb_id = request.form.get('episode_imdb_id', '').strip() # Get the episode IMDb ID
                 episode_title = request.form.get('episode_title', '').strip()
                 season_number_str = request.form.get('episode_season', '').strip()
                 episode_number_str = request.form.get('episode_number', '').strip()
                 # REMOVED: video_url = request.form.get('episode_video_url', '').strip() # Input removed

                 # Determine the series IMDb ID
                 series_imdb_id = manual_series_imdb_id if series_imdb_id_select == 'manual' else series_imdb_id_select

                 # Validate required fields for episode (excluding video_url)
                 if not series_imdb_id or not episode_imdb_id or not episode_title or not season_number_str or not episode_number_str:
                      # This check is also done client-side, but good to have server-side too
                      missing = []
                      if not series_imdb_id: missing.append('סדרה')
                      if not episode_imdb_id: missing.append('IMDb ID של הפרק')
                      if not episode_title: missing.append('כותרת פרק')
                      if not season_number_str: missing.append('מספר עונה')
                      if not episode_number_str: missing.append('מספר פרק')
                      # Removed the video URL missing field check
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

                 # Validate IMDb ID formats
                 imdb_id_pattern = re.compile(r'^tt\d{7,}$') # Use regex for pattern validation
                 if not imdb_id_pattern.match(series_imdb_id):
                     flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                     return redirect(url_for('add_content'))
                 if not imdb_id_pattern.match(episode_imdb_id):
                      flash('שגיאה: פורמט IMDb ID של הפרק לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                      return redirect(url_for('add_content'))


                 # Check if the parent series exists (Optional but good practice)
                 # series_ref = db.reference(f'/Series/{series_imdb_id}')
                 # series_exists = series_ref.get() is not None
                 # if not series_exists:
                 #      logging.warning(f'Parent series with IMDb ID "{series_imdb_id}" not found for episode. Episode will be added anyway.')
                 #      flash(f'אזהרה: הסדרה עם IMDb ID "{series_imdb_id}" אינה קיימת במסד הנתונים. הפרק יתווסף, אך ייתכן שתצטרך להוסיף את פרטי הסדרה בנפרד.', 'warning')


                 # Construct episode data (without video_url from form)
                 episode_data = {
                     'episode_imdb_id': episode_imdb_id, # Store the episode IMDb ID
                     'title': episode_title,
                     # REMOVED: 'video_url': video_url, # Not from form anymore
                     'episode_number': episode_number, # Store episode number explicitly
                     'season_number': season_number # Store season number explicitly (optional, but good for structure)
                     # Note: video_url MUST exist in Firebase for playback, but this form no longer adds it.
                     # 'duration': 'N/A'
                 }

                 # Save episode data to Firebase under /Series/{series_imdb_id}/Seasons/{season}/Episodes/{episode_number}
                 # We use episode_number as the key within the Episodes node for the season,
                 # as it fits the common structure for episode lists (1, 2, 3...).
                 # The episode_imdb_id is stored *within* the episode's data.
                 ref = db.reference(f'/Series/{series_imdb_id}/Seasons/{season_number}/Episodes/{episode_number}')
                 # Use update instead of set here, in case a video_url was added manually or elsewhere previously
                 # Using set would overwrite any existing video_url. Using update preserves it.
                 ref.update(episode_data)
                 logging.info(f"Episode S{season_number}E{episode_number} (IMDb ID: {episode_imdb_id}, Title: '{episode_title}') added/updated in series {series_imdb_id}.")
                 flash(f'פרק "{episode_title}" (עונה {season_number}, פרק {episode_number}) נוסף/עודכן בהצלחה לסדרה!', 'success')


            else:
                 flash('סוג תוכן לא ידוע.', 'warning')
                 logging.warning(f"Received unknown content type: {content_type}")


        except Exception as e:
             logging.error(f"Error processing add content POST: {e}", exc_info=True)
             flash('אירעה שגיאה בעת שמירת התוכן.', 'error')

        return redirect(url_for('add_content')) # Redirect back to the add page after POST

    # GET request: Render the add content form
    # Load series from Firebase for episode form dropdown on GET request
    available_series = load_series_list_for_add_page()

    return render_template('add.html',
                           user=user,
                           categories=[c for c in CATEGORIES if c != 'ללא'], # Categories excluding 'ללא' for movie/series dropdown
                           available_series=available_series,
                           current_year=datetime.datetime.utcnow().year
                           )



@app.route('/movies')
def all_movies():
    """Displays all movies from Firebase in a grid."""
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    admin_email = ADMIN_EMAIL

    # Load ALL movies from Firebase
    # load_movies_data() already returns a dictionary {imdbID: details}
    # with 'type: movie' added, which is what we need.
    all_movies_data = load_movies_data()

    # No categorization needed for this page, just pass the dictionary
    # The template will iterate through this dictionary.

    logging.info(f"Rendering all_movies page with {len(all_movies_data)} movies.")

    return render_template('movies.html',
                           movies=all_movies_data, # Pass all movies
                           user=user,
                           current_year=current_year,
                           admin_email=admin_email
                           )




@app.route('/series') # Define the new route
def all_series():
    """Displays all series from Firebase in a grid."""
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    admin_email = ADMIN_EMAIL

    # Use the existing function that loads basic series data for display
    # load_series_data_for_index() already returns {imdbID: basic_details}
    # with 'type: series' added, which is perfect for the grid.
    all_series_data = load_series_data_for_index()

    # Log how many series were loaded
    logging.info(f"Rendering all_series page with {len(all_series_data)} series.")

    # Render the new template, passing the series data
    return render_template('SeriesTV.html',
                           series=all_series_data, # Pass series data to the template
                           user=user,
                           current_year=current_year,
                           admin_email=admin_email
                           )


# --- Error Handlers ---
@app.errorhandler(403)
def forbidden(e):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    logging.warning(f"403 Forbidden: {request.path} - {e}")
    return render_template('403.html', user=user, current_year=current_year), 403

@app.errorhandler(404)
def page_not_found(e):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    logging.warning(f"404 Not Found: {request.path} - {e}")
    return render_template('404.html', user=user, current_year=current_year), 404

@app.errorhandler(500)
def internal_server_error(e):
    tb_str = traceback.format_exc()
    logging.error(f"Internal Server Error: {request.path} - {e}\n{tb_str}", exc_info=True)
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    return render_template('500.html', user=user, current_year=current_year), 500


if __name__ == '__main__':
    # Ensure Firebase is initialized before running the app
    # The try/except block with _apps check handles reloader
    # Also check if initialization actually succeeded before running
    if firebase_admin._apps:
        port = int(os.environ.get('PORT', 5000))
        # debug=True should only be used in development
        app.run(host='0.0.0.0', port=port, debug=True)
    else:
        logging.error("Application not started because Firebase initialization failed.")
        # You might want to sys.exit(1) here in a real application
