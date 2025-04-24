# main.py
import datetime
import traceback
import os
import requests
import json
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify
from authlib.integrations.flask_client import OAuth
import firebase_admin
from firebase_admin import credentials, db
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

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
    # Check if app is already initialized to avoid re-initialization in debug mode with auto-reloader
    if not firebase_admin._apps:
        cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred, {
            'databaseURL': FIREBASE_DATABASE_URL
        })
        logging.info("Firebase initialized successfully.")
except Exception as e:
    logging.error(f"Error initializing Firebase: {e}")
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
        return movies if movies is not None else {}
    except Exception as e:
        logging.error(f"Error loading movies from Firebase: {e}")
        return {}

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
        return available_series_list
    except Exception as e:
        logging.error(f"Error loading series list from Firebase: {e}")
        # Return dummy data or empty list on error
        # This should ideally not happen if Firebase is reachable
        return [] # Return empty list

def get_movie_details_from_firebase(imdb_id):
    """Loads details for a single movie from Firebase."""
    try:
        ref = db.reference(f'/Movies/{imdb_id}')
        movie_details = ref.get()
        return movie_details if movie_details is not None else None
    except Exception as e:
        logging.error(f"Error loading movie details for {imdb_id} from Firebase: {e}")
        return None


def categorize_movies(movies_data):
    """Categorizes movies loaded from Firebase."""
    categorized_movies = {}
    # Initialize categories excluding "ללא" for display
    for cat in CATEGORIES:
         if cat != "ללא":
             categorized_movies[cat] = []

    if not movies_data:
        return categorized_movies

    # Firebase data is {imdb_id: {details}}
    for imdb_id, movie_details in movies_data.items():
        if not isinstance(movie_details, dict):
            logging.warning(f"Skipping non-dict entry in /Movies: {imdb_id}")
            continue

        # Ensure required fields exist, provide defaults
        # Note: OMDB fetches Title, Plot, Year, imdbRating, Genre, Actors, Poster
        title = movie_details.get('title', 'כותרת לא ידועה')
        poster = movie_details.get('poster', 'N/A') # Use N/A from OMDB as default, handle in template
        video_url = movie_details.get('video_url', '#')
        category = movie_details.get('category', 'ללא') # Default to 'ללא'

        # Add to the correct category list if category is valid (and not 'ללא')
        if category != "ללא" and category in CATEGORIES:
             categorized_movies[category].append({
                "id": imdb_id,
                "title": title,
                "poster": poster, # Pass the poster URL
                "video_url": video_url
                # Basic info for the card is enough here. Full details fetched later.
             })
        # If category is 'ללא' or invalid, it's not added to displayed categories

    # Return the dictionary with valid, non-'ללא' categories
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

# --- OMDB API Functions ---
OMDB_BASE_URL = 'http://www.omdbapi.com/'

def search_omdb_api(search_term, content_type):
    """Searches OMDB API for movies or series."""
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY' or OMDB_API_KEY == '':
        logging.warning("OMDB_API_KEY is not set or is placeholder.")
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
            return data.get('Search', [])
        else:
             logging.info(f"OMDB search found no results for '{search_term}' type '{content_type}': {data.get('Error', 'Unknown error')}")
             return []
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling OMDB search API: {e}")
        return []

def get_omdb_details_api(imdb_id):
    """Gets full details for a specific IMDb ID from OMDB."""
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY' or OMDB_API_KEY == '':
         logging.warning("OMDB_API_KEY is not set or is placeholder.")
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
            return data
        else:
             logging.info(f"OMDB details not found for ID '{imdb_id}': {data.get('Error', 'Unknown error')}")
             return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling OMDB details API for ID {imdb_id}: {e}")
        return None

# --- API Routes for Frontend OMDB Search and Movie Details ---
@app.route('/api/search_omdb')
def api_search_omdb():
    """API endpoint to search OMDB."""
    search_term = request.args.get('s')
    content_type = request.args.get('type') # 'movie' or 'series'
    if not search_term or not content_type:
        return jsonify({"Error": "Missing search term or type"}), 400
    if content_type not in ['movie', 'series']:
         return jsonify({"Error": "Invalid type specified"}), 400

    results = search_omdb_api(search_term, content_type)
    return jsonify(results)

@app.route('/api/get_omdb_details')
def api_get_omdb_details():
    """API endpoint to get full OMDB details by IMDb ID."""
    imdb_id = request.args.get('i')
    if not imdb_id:
        return jsonify({"Error": "Missing IMDb ID"}), 400

    details = get_omdb_details_api(imdb_id)
    if details:
        return jsonify(details)
    else:
        return jsonify({"Error": "Details not found or API error"}), 404

# New API endpoint to fetch movie details from Firebase for the modal
@app.route('/api/movie_firebase_details/<string:imdb_id>')
def api_movie_firebase_details(imdb_id):
    """API endpoint to get movie details from Firebase by IMDb ID."""
    if not imdb_id:
        return jsonify({"Error": "Missing IMDb ID"}), 400

    movie_details = get_movie_details_from_firebase(imdb_id)

    if movie_details:
        return jsonify(movie_details)
    else:
        logging.warning(f"Movie details not found in Firebase for ID: {imdb_id}")
        return jsonify({"Error": "Movie details not found in database"}), 404


# --- Authentication Routes ---
@app.route('/auth/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    try:
        return oauth.google.authorize_redirect(redirect_uri=redirect_uri)
    except Exception as e:
        traceback.print_exc()
        flash('שגיאה בתהליך ההתחברות עם גוגל.', 'error')
        return redirect(url_for('index'))

@app.route('/auth/google/callback')
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        # Use the token to get user info
        userinfo_response = oauth.google.userinfo(token=token)
        userinfo = userinfo_response.json() # Get the JSON data

        user_data = {
            'name': userinfo.get('name'),
            'email': userinfo.get('email'),
            'picture': userinfo.get('picture'),
            'google_id': userinfo.get('sub')
        }

        if not user_data.get('google_id'):
            flash('התחברות עם גוגל נכשלה: לא הושגו פרטי משתמש.', 'error')
            return redirect(url_for('index'))

        session['user'] = user_data
        session.permanent = True
        flash('התחברת בהצלחה!', 'success')
        return redirect(url_for('index'))

    except Exception as e:
        traceback.print_exc()
        flash('התחברות נכשלה. אנא ודא שההרשאות המתאימות אושרו ונסה שוב.', 'error')
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('התנתקת בהצלחה.', 'info')
    return redirect(url_for('index'))

# --- Main Routes ---
@app.route('/')
def index():
    user = session.get('user')
    greeting = get_greeting(user)

    # Load data from Firebase
    movies_data = load_movies_data()
    categories = categorize_movies(movies_data)
    current_year = datetime.datetime.utcnow().year
    # Pass ADMIN_EMAIL to the template
    admin_email = ADMIN_EMAIL

    return render_template('index.html',
                           greeting=greeting,
                           categories=categories,
                           current_year=current_year,
                           user=user,
                           admin_email=admin_email # Pass the admin email
                           )

@app.route('/add', methods=['GET', 'POST'])
def add_content():
    user = session.get('user')
    # Basic admin check (consider a more robust method for production)
    if not user or user.get('email') != ADMIN_EMAIL:
        logging.warning(f"Unauthorized access attempt to /add by {user.get('email') if user else 'anonymous'}")
        abort(403)

    available_series = load_series_data() # Load series from Firebase for episode form

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

                if not omdb_details:
                     flash(f'שגיאה: לא נמצאו פרטים עבור IMDb ID "{imdb_id}" ב-OMDB. סרט לא נוסף.', 'error')
                     # Cannot add movie without OMDB details in this structure
                     return redirect(url_for('add_content'))

                # Construct movie data from OMDB details and form data
                # Include all relevant OMDB fields + local fields
                movie_data = {
                    'imdbID': omdb_details.get('imdbID', imdb_id),
                    'title': omdb_details.get('Title', 'Untitled'),
                    'year': omdb_details.get('Year', 'N/A'),
                    'rated': omdb_details.get('Rated', 'N/A'),
                    'released': omdb_details.get('Released', 'N/A'),
                    'runtime': omdb_details.get('Runtime', 'N/A'),
                    'genre': omdb_details.get('Genre', 'N/A'),
                    'director': omdb_details.get('Director', 'N/A'),
                    'writer': omdb_details.get('Writer', 'N/A'),
                    'actors': omdb_details.get('Actors', 'N/A'),
                    'plot': omdb_details.get('Plot', 'N/A'), # This is the description
                    'language': omdb_details.get('Language', 'N/A'),
                    'country': omdb_details.get('Country', 'N/A'),
                    'awards': omdb_details.get('Awards', 'N/A'),
                    'poster': omdb_details.get('Poster', 'N/A'),
                    'ratings': omdb_details.get('Ratings', []), # List of rating objects
                    'metascore': omdb_details.get('Metascore', 'N/A'),
                    'imdbRating': omdb_details.get('imdbRating', 'N/A'), # This is the rating
                    'imdbVotes': omdb_details.get('imdbVotes', 'N/A'),
                    'type': omdb_details.get('Type', 'movie'), # Should be 'movie'
                    'dvd': omdb_details.get('DVD', 'N/A'),
                    'boxoffice': omdb_details.get('BoxOffice', 'N/A'),
                    'production': omdb_details.get('Production', 'N/A'),
                    'website': omdb_details.get('Website', 'N/A'),
                    # Local fields
                    'video_url': video_url, # From form
                    'category': category # From form
                }

                # Save movie data to Firebase under /Movies/{imdb_id}
                ref = db.reference(f'/Movies/{imdb_id}')
                ref.set(movie_data)
                logging.info(f"Movie '{movie_data.get('title', imdb_id)}' ({imdb_id}) added to Firebase.")
                flash(f'סרט "{movie_data.get("title", imdb_id)}" נוסף בהצלחה!', 'success')

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

                if not omdb_details or omdb_details.get('Type') != 'series':
                     flash(f'שגיאה: לא נמצאו פרטים לסדרה או שה-ID אינו של סדרה עבור "{imdb_id}" ב-OMDB. סדרה לא נוספה.', 'error')
                     return redirect(url_for('add_content'))

                 # Construct series data from OMDB details and form data
                 # Include all relevant OMDB fields + local fields (category)
                series_data = {
                    'imdbID': omdb_details.get('imdbID', imdb_id),
                    'title': omdb_details.get('Title', 'Untitled Series'),
                    'year': omdb_details.get('Year', 'N/A'),
                    'rated': omdb_details.get('Rated', 'N/A'),
                    'released': omdb_details.get('Released', 'N/A'),
                    'runtime': omdb_details.get('Runtime', 'N/A'),
                    'genre': omdb_details.get('Genre', 'N/A'),
                    'director': omdb_details.get('Director', 'N/A'),
                    'writer': omdb_details.get('Writer', 'N/A'),
                    'actors': omdb_details.get('Actors', 'N/A'),
                    'plot': omdb_details.get('Plot', 'N/A'), # Description for the series
                    'language': omdb_details.get('Language', 'N/A'),
                    'country': omdb_details.get('Country', 'N/A'),
                    'awards': omdb_details.get('Awards', 'N/A'),
                    'poster': omdb_details.get('Poster', 'N/A'),
                    'ratings': omdb_details.get('Ratings', []),
                    'metascore': omdb_details.get('Metascore', 'N/A'),
                    'imdbRating': omdb_details.get('imdbRating', 'N/A'), # Rating for the series
                    'imdbVotes': omdb_details.get('imdbVotes', 'N/A'),
                    'type': omdb_details.get('Type', 'series'),
                    'totalSeasons': omdb_details.get('totalSeasons', 'N/A'), # Specific to series
                    # Local fields
                    'category': category # From form
                 }


                # Save series data to Firebase under /Series/{imdb_id}
                ref = db.reference(f'/Series/{imdb_id}')
                # Set operation will overwrite existing series details but won't affect existing Seasons/Episodes sub-paths
                ref.set(series_data)
                logging.info(f"Series '{series_data.get('title', imdb_id)}' ({imdb_id}) added/updated in Firebase.")
                flash(f'סדרה "{series_data.get("title", imdb_id)}" נוספה/עודכנה בהצלחה!', 'success')


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

                 # Note: We are NOT fetching episode details from OMDB here as it's not directly supported
                 # We save the manual title and video URL.

                 # Construct episode data
                 episode_data = {
                     'title': episode_title,
                     'video_url': video_url,
                     # Duration is complex to extract from URL, omitting for now
                     # 'duration': 'N/A'
                 }

                 # Save episode data to Firebase under /Series/{series_imdb_id}/Seasons/{season}/Episodes/{episode}
                 # Using .child().child() is equivalent to f-string path
                 ref = db.reference(f'/Series/{series_imdb_id}/Seasons/{season_number}/Episodes/{episode_number}')
                 ref.set(episode_data)
                 logging.info(f"Episode S{season_number}E{episode_number} ('{episode_title}') added to series {series_imdb_id}.")
                 flash(f'פרק "{episode_title}" (עונה {season_number}, פרק {episode_number}) נוסף בהצלחה לסדרה!', 'success')


            else:
                 flash('סוג תוכן לא ידוע.', 'warning')

        except Exception as e:
             logging.error(f"Error adding content to Firebase: {e}")
             traceback.print_exc()
             flash('אירעה שגיאה בעת שמירת התוכן.', 'error')

        return redirect(url_for('add_content')) # Redirect back to the add page after POST

    # GET request: Render the add content form
    available_series = load_series_data() # Ensure this is re-loaded for the GET request template

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
    return render_template('403.html', user=user, current_year=current_year), 403

@app.errorhandler(404)
def page_not_found(e):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    return render_template('404.html', user=user, current_year=current_year), 404

@app.errorhandler(500)
def internal_server_error(e):
    tb_str = traceback.format_exc()
    logging.error(f"Internal Server Error: {tb_str}")
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    return render_template('500.html', user=user, current_year=current_year), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # debug=True should only be used in development
    # Use debug=False and a production WSGI server like Gunicorn in production
    app.run(host='0.0.0.0', port=port, debug=True)
