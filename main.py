# main.py
import datetime
import traceback
import os
import requests
import json
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify, send_from_directory
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
        cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred, {
            'databaseURL': FIREBASE_DATABASE_URL
        })
        logging.info("Firebase initialized successfully.")
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
    "אקס-מן",
    "ספיידרמן",
    "ללא"
]

# --- Data Loading Functions (from Firebase) ---

def load_movies_data():
    """Loads all movies from Firebase."""
    try:
        ref = db.reference('/Movies')
        movies = ref.get()
        logging.info(f"Loaded {len(movies) if movies else 0} movies from Firebase.")
        return movies if movies is not None else {} # Ensure returns dict even if empty
    except Exception as e:
        logging.error(f"Error loading movies from Firebase: {e}", exc_info=True)
        return {}

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


def load_series_data():
    """Loads all series from Firebase."""
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


def categorize_movies(movies_data):
    """Categorizes movies loaded from Firebase for index display."""
    categorized_movies = {}
    # Initialize categories excluding "ללא" as it's typically not a display category
    for cat in CATEGORIES:
        if cat != "ללא":
            categorized_movies[cat] = []

    if not movies_data:
        logging.info("No movies data to categorize.")
        return {cat: [] for cat in CATEGORIES if cat != "ללא"} # Return empty dict for display categories

    # Firebase data is {imdb_id: {details}}
    for imdb_id, movie_details in movies_data.items():
        if not isinstance(movie_details, dict):
            logging.warning(f"Skipping non-dict entry in /Movies: {imdb_id}")
            continue

        # Ensure required fields exist, provide defaults for the card display
        title = movie_details.get('title', 'כותרת לא ידועה')
        # Use 'poster' field from Firebase, which should be from OMDB
        poster = movie_details.get('poster', 'N/A') # Use N/A as default for poster from OMDB
        # video_url is NOT needed on the index card anymore, as we navigate to movie page
        category = movie_details.get('category', 'ללא') # Default to 'ללא'

        # Add to the correct category list if category is valid and not "ללא"
        # We need id, title, poster for the item cards in index.html
        if category in CATEGORIES and category != "ללא":
             categorized_movies[category].append({
                "id": imdb_id, # The ID is needed to build the link URL
                "title": title,
                "poster": poster,
             })
        elif category == "ללא":
            pass # Don't display 'ללא' category on index
        else:
             logging.warning(f"Movie {imdb_id} has invalid/unknown category: {category}")
             pass # Skip invalid categories

    # Optional: If you want to ensure categories with no movies are still shown,
    # you might add checks here. But usually, you only show categories with items.
    # return {cat: items for cat, items in categorized_movies.items() if items} # Only return categories with items

    return categorized_movies


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

# --- OMDB API Functions (Used by add.html) ---
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
            return data.get('Search', [])
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
    return jsonify(results)

@app.route('/api/get_omdb_details')
def api_get_omdb_details():
    imdb_id = request.args.get('i')
    if not imdb_id:
        logging.warning("API get details called with missing IMDb ID.")
        return jsonify({"Error": "Missing IMDb ID"}), 400

    details = get_omdb_details_api(imdb_id)
    if details:
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
        userinfo_response = oauth.google.userinfo(token=token)
        userinfo = userinfo_response.json()
        logging.info(f"Received user info from Google: {userinfo.get('email')}")

        user_data = {
            'name': userinfo.get('name'),
            'email': userinfo.get('email'),
            'picture': userinfo.get('picture'),
            'google_id': userinfo.get('sub')
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
    logging.info(f"User {user_email} logged out.")
    flash('התנתקת בהצלחה.', 'info')
    return redirect(url_for('index'))


# --- Favicon Route ---
@app.route('/favicon.ico')
def favicon():
    # Assuming your favicon is named favicon.ico and is in the static folder
    # If not, replace 'favicon.ico' with your filename or None if you don't have one
    try:
        return send_from_directory(os.path.join(app.root_path, 'static'),
                                   'favicon.ico', mimetype='image/vnd.microsoft.icon')
    except FileNotFoundError:
        # If favicon.ico doesn't exist, return a 404 or 204 No Content
        abort(404) # Or return Response(status=204)

# --- Main Routes ---
@app.route('/')
def index():
    user = session.get('user')
    greeting = get_greeting(user)

    # Load only the necessary data for index cards
    movies_data = load_movies_data() # Still load all movies to categorize them
    categories = categorize_movies(movies_data) # This returns the list structure for index display

    current_year = datetime.datetime.utcnow().year
    admin_email = ADMIN_EMAIL # Ensure this is passed

    # Pass only the categorized data needed for rendering the index page
    return render_template('index.html',
                           greeting=greeting,
                           categories=categories,
                           current_year=current_year,
                           user=user,
                           admin_email=admin_email # Pass the admin email
                           )

# --- New Route for Single Movie Page ---
@app.route('/movie/<imdb_id>')
def movie_details(imdb_id):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year

    # Validate IMDb ID format before querying
    if not imdb_id or not imdb_id.startswith('tt') or len(imdb_id) < 7:
         logging.warning(f"Attempted to access movie page with invalid IMDb ID format: {imdb_id}")
         abort(404) # Or redirect to error page

    # Load movie details from Firebase
    movie = load_movie_details(imdb_id)

    # Important: Check if movie exists AND is actually of type 'movie'
    if not movie or movie.get('type') != 'movie':
        logging.warning(f"Movie details not found or is not of type 'movie' for ID: {imdb_id}")
        # If not found or not a movie type, show 404 or specific error page
        abort(404)

    # Render the movie details page
    # Ensure admin_email is passed if you want the admin link on this page
    return render_template('movie.html',
                           movie=movie,
                           user=user,
                           current_year=current_year,
                           admin_email=ADMIN_EMAIL # Pass admin email for nav link
                           )


@app.route('/add', methods=['GET', 'POST'])
def add_content():
    user = session.get('user')
    # Basic admin check (consider a more robust method for production)
    if not user or user.get('email') != ADMIN_EMAIL:
        logging.warning(f"Unauthorized access attempt to /add by {user.get('email') if user else 'anonymous'}")
        abort(403)

    # Load series from Firebase for episode form dropdown on GET request
    available_series = load_series_data()

    if request.method == 'POST':
        content_type = request.form.get('content_type')
        logging.info(f"Received POST for content type: {content_type}")

        try:
            if content_type == 'movie':
                # Get form data for movie
                imdb_id = request.form.get('movie_imdb_id', '').strip()
                video_url = request.form.get('movie_video_url', '').strip()
                category = request.form.get('movie_category', 'ללא') # Get category from form

                # Validate required fields for movie
                if not imdb_id or not video_url:
                    flash('שגיאה: שדות חובה (IMDb ID, וידאו) חסרים עבור סרט.', 'error')
                    return redirect(url_for('add_content'))

                if not imdb_id.startswith('tt') or len(imdb_id) < 7:
                     flash('שגיאה: פורמט IMDb ID לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו מספרים.', 'error')
                     return redirect(url_for('add_content'))

                # Fetch full details from OMDB server-side using the IMDb ID
                omdb_details = get_omdb_details_api(imdb_id)

                # Check if OMDB details were found and if the type is actually a movie
                if not omdb_details or omdb_details.get('Type') != 'movie':
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
                    'type': omdb_details.get('Type', 'movie'), # Should be 'movie'
                    'dvd': omdb_details.get('DVD', 'N/A'),
                    'boxoffice': omdb_details.get('BoxOffice', 'N/A'),
                    'production': omdb_details.get('Production', 'N/A'),
                    'website': omdb_details.get('Website', 'N/A'),
                    'video_url': video_url, # From form
                    'category': category # From form
                }

                # Save movie data to Firebase under /Movies/{imdb_id}
                ref = db.reference(f'/Movies/{imdb_id}')
                ref.set(movie_data)
                logging.info(f"Movie '{movie_data['title']}' ({imdb_id}) added to Firebase.")
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
                     flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו מספרים.', 'error')
                     return redirect(url_for('add_content'))

                # Fetch full details from OMDB server-side using the IMDb ID
                omdb_details = get_omdb_details_api(imdb_id)

                # Also explicitly check the type from OMDB response
                if not omdb_details or omdb_details.get('Type') != 'series':
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
                    'type': omdb_details.get('Type', 'series'), # Should be 'series'
                    'totalSeasons': omdb_details.get('totalSeasons', 'N/A'), # Specific to series
                    'category': category # From form
                 }


                # Save series data to Firebase under /Series/{imdb_id}
                ref = db.reference(f'/Series/{imdb_id}')
                # Set operation will overwrite existing series details but won't affect existing Seasons/Episodes
                ref.set(series_data)
                logging.info(f"Series '{series_data['title']}' ({imdb_id}) added/updated in Firebase.")
                flash(f'סדרה "{series_data["title"]}" נוספה/עודכנה בהצלחה!', 'success')


            elif content_type == 'episode':
                 # Get form data for episode
                 series_imdb_id_select = request.form.get('episode_series_id')
                 manual_series_imdb_id = request.form.get('manual_episode_series_id', '').strip()
                 episode_title = request.form.get('episode_title', '').strip()
                 season_number_str = request.form.get('episode_season', '').strip()
                 episode_number_str = request.form.get('episode_number', '').strip()
                 video_url = request.form.get('episode_video_url', '').strip()

                 # Determine the series IMDb ID
                 series_imdb_id = manual_series_imdb_id if series_imdb_id_select == 'manual' else series_imdb_id_select

                 # Validate required fields for episode
                 if not series_imdb_id or not episode_title or not season_number_str or not episode_number_str or not video_url:
                      flash('שגיאה: שדות חובה (סדרה, כותרת פרק, מספר עונה, מספר פרק, וידאו) חסרים עבור פרק.', 'error')
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

                 # Validate series IMDb ID format if manually entered
                 if series_imdb_id_select == 'manual' and (not series_imdb_id.startswith('tt') or len(series_imdb_id) < 7):
                      flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין (הזנה ידנית). ודא שהוא מתחיל ב-"tt" ואחריו מספרים.', 'error')
                      return redirect(url_for('add_content'))

                 # Check if the parent series exists (Optional but good practice)
                 series_ref = db.reference(f'/Series/{series_imdb_id}')
                 series_exists = series_ref.get() is not None

                 if not series_exists:
                      # Don't block adding episode, just warn the user
                      logging.warning(f'Parent series with IMDb ID "{series_imdb_id}" not found for episode. Episode will be added anyway.')
                      flash(f'אזהרה: הסדרה עם IMDb ID "{series_imdb_id}" אינה קיימת במסד הנתונים. הפרק יתווסף, אך ייתכן שתצטרך להוסיף את פרטי הסדרה בנפרד.', 'warning')


                 # Construct episode data
                 episode_data = {
                     'title': episode_title,
                     'video_url': video_url,
                     # Duration is complex to extract from URL, omitting for now
                     # 'duration': 'N/A'
                 }

                 # Save episode data to Firebase under /Series/{series_imdb_id}/Seasons/{season}/Episodes/{episode}
                 ref = db.reference(f'/Series/{series_imdb_id}/Seasons/{season_number}/Episodes/{episode_number}')
                 # Use set for the specific episode node
                 ref.set(episode_data)
                 logging.info(f"Episode S{season_number}E{episode_number} ('{episode_title}') added to series {series_imdb_id}.")
                 flash(f'פרק "{episode_title}" (עונה {season_number}, פרק {episode_number}) נוסף בהצלחה לסדרה!', 'success')


            else:
                 flash('סוג תוכן לא ידוע.', 'warning')
                 logging.warning(f"Received unknown content type: {content_type}")


        except Exception as e:
             logging.error(f"Error processing add content POST: {e}", exc_info=True)
             flash('אירעה שגיאה בעת שמירת התוכן.', 'error')

        return redirect(url_for('add_content')) # Redirect back to the add page after POST

    # GET request: Render the add content form
    # Load series from Firebase for episode form dropdown on GET request
    available_series = load_series_data()

    return render_template('add.html',
                           user=user,
                           categories=CATEGORIES,
                           available_series=available_series,
                           current_year=datetime.datetime.utcnow().year
                           )

# --- Error Handlers ---
@app.errorhandler(403)
def forbidden(e):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    logging.warning(f"403 Forbidden: {request.path} - {e}")
    # Pass admin_email to error pages if they use the nav bar
    return render_template('403.html', user=user, current_year=current_year, admin_email=ADMIN_EMAIL), 403

@app.errorhandler(404)
def page_not_found(e):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    logging.warning(f"404 Not Found: {request.path} - {e}")
     # Pass admin_email to error pages if they use the nav bar
    return render_template('404.html', user=user, current_year=current_year, admin_email=ADMIN_EMAIL), 404

@app.errorhandler(500)
def internal_server_error(e):
    tb_str = traceback.format_exc()
    logging.error(f"Internal Server Error: {request.path} - {e}\n{tb_str}", exc_info=True)
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
     # Pass admin_email to error pages if they use the nav bar
    return render_template('500.html', user=user, current_year=current_year, admin_email=ADMIN_EMAIL), 500

if __name__ == '__main__':
    # Ensure Firebase is initialized before running the app
    # The try/except block with _apps check handles reloader
    port = int(os.environ.get('PORT', 5000))
    # debug=True should only be used in development
    app.run(host='0.0.0.0', port=port, debug=True)
