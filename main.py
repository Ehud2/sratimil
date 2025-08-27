import eventlet
eventlet.monkey_patch()

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
import uuid
from flask_socketio import SocketIO, join_room, leave_room, emit

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'moviesilsuperdupersecretkey')
socketio = SocketIO(app)

GROUPS_FILE = 'groups.json'
groups_lock = threading.Lock()
user_connections = {}

def initialize_groups_file():
    with groups_lock:
        with open(GROUPS_FILE, 'w') as f:
            json.dump({}, f)
    logging.info(f"{GROUPS_FILE} has been cleared and initialized.")

def load_groups():
    with groups_lock:
        try:
            with open(GROUPS_FILE, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

def save_groups(data):
    with groups_lock:
        with open(GROUPS_FILE, 'w') as f:
            json.dump(data, f, indent=4)

TMDB_API_KEY = os.environ.get('TMDB_API_KEY', 'fb7bb23f03b6994dafc674c074d01761')
TMDB_BASE_URL = 'https://api.themoviedb.org/3'
TMDB_IMAGE_BASE_URL = 'https://image.tmdb.org/t/p/w500'
TMDB_MAX_RETRIES = 3
TMDB_BASE_DELAY_SECONDS = 1

app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID', '367711020009-o70b96v4cv604acg2hqv60k8c5mjmhtr.apps.googleusercontent.com')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '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')
app.config['GOOGLE_DISCOVERY_URL'] = 'https://accounts.google.com/.well-known/openid-configuration'
OMDB_API_KEY = os.environ.get('OMDB_API_KEY', '6e705a15')
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
            firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DATABASE_URL})
            logging.info("Firebase initialized successfully.")
        else:
            logging.error("Firebase initialization failed: No valid credentials found.")
    else:
        logging.info("Firebase already initialized.")
except Exception as e:
    logging.error(f"Error initializing Firebase: {e}", exc_info=True)

CATEGORIES = [
    "היקום הקולנועי של מארוול", "DC", "יקום המפלצות", "מלחמת הכוכבים", "הארי פוטר",
    "המסור", "הפארק הדרומי", "מהירים ועצבניים", "משימה בלתי אפשרית", "אקס-מן", "ספיידרמן", "ללא"
]
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
    combined_data = {'movies': movies, 'series': series}
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
                    'imdbID': imdb_id, 'title': details.get('title', 'כותרת לא ידועה'), 'poster': details.get('poster', 'N/A'),
                    'HebrewName': details.get('HebrewName'), 'HebrewPoster': details.get('HebrewPoster'), 'category': details.get('category', 'ללא'),
                    'type': 'series', 'genre': details.get('genre', 'N/A'), 'imdbRating': details.get('imdbRating', 'N/A'), 'year': details.get('year', 'N/A')
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
                 available_series_list.append({"id": imdb_id, "title": display_title})
    return available_series_list

def load_full_series_details(imdb_id):
    return APP_DATA.get('series', {}).get(imdb_id)

def load_movie_details(imdb_id):
    return APP_DATA.get('movies', {}).get(imdb_id)

def get_greeting(user=None, language='he'):
    now = datetime.datetime.now()
    current_hour = now.hour
    greeting_text = ""
    greetings_he = {(5, 12): "בוקר טוב", (12, 18): "צהריים טובים", (18, 21): "ערב טוב", (21, 24): "לילה טוב", (0, 5): "לילה טוב"}
    greetings_en = {(5, 12): "Good Morning", (12, 18): "Good Afternoon", (18, 21): "Good Evening", (21, 24): "Good Night", (0, 5): "Good Night"}
    greetings = greetings_he if language == 'he' else greetings_en
    for hour_range, text in greetings.items():
        if hour_range[0] <= current_hour < hour_range[1]:
            greeting_text = text
            break
    if not greeting_text:
         greeting_text = "שלום" if language == 'he' else "Hello"
    if user and user.get('name'):
        first_name = user['name'].split(' ')[0]
        if language == 'he':
             return f"{greeting_text} {first_name}"
        else:
             if all(ord(c) < 128 for c in first_name):
                  return f"{greeting_text} {first_name}"
             else:
                  return greeting_text
    else:
        return f"{greeting_text} {'אורח' if language == 'he' else 'Guest'}"

def categorize_content(movies_data, series_data):
    categorized_items = {}
    for cat in CATEGORIES:
        if cat != "ללא":
            categorized_items[cat] = []
    all_items = {}
    if movies_data: all_items.update(movies_data)
    if series_data: all_items.update(series_data)
    if not all_items:
        logging.info("No movies or series data to categorize.")
        return {cat: [] for cat in CATEGORIES if cat != "ללא"}
    for imdb_id, item_details in all_items.items():
        if not isinstance(item_details, dict):
            logging.warning(f"Skipping non-dict entry in all_items: {imdb_id}")
            continue
        category = item_details.get('category', 'ללא')
        item_type = item_details.get('type')
        if category in CATEGORIES and category != "ללא" and item_type in ['movie', 'series']:
             categorized_items[category].append({
                "id": imdb_id, "title": item_details.get('title', 'כותרת לא ידועה'), "poster": item_details.get('poster', 'N/A'),
                "HebrewName": item_details.get('HebrewName'), "HebrewPoster": item_details.get('HebrewPoster'), "type": item_type
             })
    return categorized_items

OMDB_BASE_URL = 'http://www.omdbapi.com/'
def search_omdb_api(search_term, content_type):
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY': return []
    params = {'apikey': OMDB_API_KEY, 's': search_term, 'type': content_type}
    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('Response') == 'True':
            return [item for item in data.get('Search', []) if item.get('Type', '').lower() == content_type]
        return []
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling OMDB search API: {e}", exc_info=True)
        return []

def get_omdb_details_api(imdb_id, season=None, episode=None):
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY': return None
    params = {'apikey': OMDB_API_KEY, 'i': imdb_id}
    if season is not None: params['Season'] = season
    if episode is not None: params['Episode'] = episode
    else: params['plot'] = 'full'
    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('Response') == 'True': return data
        return {'Response': 'False', 'Error': data.get('Error', 'Unknown error from OMDB')}
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling OMDB details API for ID {imdb_id}: {e}", exc_info=True)
        return {'Response': 'False', 'Error': f'Request Error: {e}'}

@app.route('/api/search_omdb')
def api_search_omdb():
    search_term = request.args.get('s')
    content_type = request.args.get('type')
    if not search_term or not content_type: return jsonify({"Error": "Missing search term or type"}), 400
    if content_type not in ['movie', 'series']: return jsonify({"Error": "Invalid type specified"}), 400
    results = search_omdb_api(search_term, content_type)
    return jsonify([{'Title': r.get('Title'), 'Year': r.get('Year'), 'imdbID': r.get('imdbID'), 'Type': r.get('Type'), 'Poster': r.get('Poster')} for r in results])

@app.route('/api/get_omdb_details')
def api_get_omdb_details():
    imdb_id = request.args.get('i')
    if not imdb_id: return jsonify({"Error": "Missing IMDb ID"}), 400
    try:
        season_int = int(request.args.get('season')) if request.args.get('season') else None
        episode_int = int(request.args.get('episode')) if request.args.get('episode') else None
    except ValueError:
        return jsonify({"Error": "Invalid season or episode number"}), 400
    details = get_omdb_details_api(imdb_id, season_int, episode_int)
    if details and details.get('Response') == 'True': return jsonify(details)
    error_message = details.get('Error', 'Details not found') if isinstance(details, dict) else 'Details not found'
    return jsonify({"Error": error_message}), 404

@app.route('/auth/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri=redirect_uri)

@app.route('/auth/google/callback')
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        userinfo = oauth.google.userinfo(token=token)
        session['user'] = {'name': userinfo.get('name'), 'email': userinfo.get('email'), 'picture': userinfo.get('picture'), 'google_id': userinfo.get('sub')}
        session.permanent = True
        flash('התחברת בהצלחה!', 'success')
        return redirect(url_for('index'))
    except Exception as e:
        logging.error("Error during Google login callback:", exc_info=True)
        flash('התחברות נכשלה.', 'error')
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('התנתקת בהצלחה.', 'info')
    return redirect(url_for('index'))

@app.route('/set_language/<lang_code>')
def set_language(lang_code):
    if lang_code in ['he', 'en']: session['language'] = lang_code
    return redirect(request.referrer or url_for('index'))

@app.route('/')
def index():
    user = session.get('user')
    current_language = session.get('language', 'he')
    greeting = get_greeting(user, current_language)
    movies_data = load_movies_data()
    series_data_for_index = load_series_data_for_index()
    return render_template('index.html', greeting=greeting, categories=categorize_content(movies_data, series_data_for_index),
                           current_year=datetime.datetime.utcnow().year, user=user, admin_emails=ADMIN_EMAILS,
                           current_language=current_language, num_movies=len(movies_data), num_series=len(series_data_for_index))

@app.route('/movie/<imdb_id>')
def movie_details(imdb_id):
    if not imdb_id or not imdb_id.startswith('tt'): abort(404)
    movie = load_movie_details(imdb_id)
    if not movie: abort(404)
    return render_template('movie.html', movie=movie, user=session.get('user'), current_year=datetime.datetime.utcnow().year,
                           admin_emails=ADMIN_EMAILS, current_language=session.get('language', 'he'))

@app.route('/series/<imdb_id>')
@app.route('/series/<imdb_id>/<int:season_number>/<int:episode_number>')
def series_details(imdb_id, season_number=None, episode_number=None):
    if not imdb_id or not imdb_id.startswith('tt'): abort(404)
    series = load_full_series_details(imdb_id)
    if not series: abort(404)
    return render_template('series.html', series=series, user=session.get('user'), current_year=datetime.datetime.utcnow().year,
                           admin_emails=ADMIN_EMAILS, current_language=session.get('language', 'he'))

@app.route('/add', methods=['GET', 'POST'])
def add_content():
    user = session.get('user')
    if not user or user.get('email') not in ADMIN_EMAILS: abort(403)
    if request.method == 'POST':
        content_type = request.form.get('content_type')
        try:
            if content_type == 'movie':
                imdb_id = request.form.get('movie_imdb_id', '').strip()
                if not imdb_id:
                    flash('שגיאה: IMDb ID חסר.', 'error')
                    return redirect(url_for('add_content'))
                omdb_details = get_omdb_details_api(imdb_id)
                if not omdb_details or omdb_details.get('Response') == 'False' or omdb_details.get('Type', '').lower() != 'movie':
                    flash(f'שגיאה: לא נמצא סרט עבור IMDb ID "{imdb_id}".', 'error')
                    return redirect(url_for('add_content'))
                movie_data = {k.capitalize(): omdb_details.get(k.capitalize(), 'N/A') for k in ['title', 'year', 'rated', 'released', 'runtime', 'genre', 'director', 'writer', 'actors', 'plot', 'language', 'country', 'awards', 'poster', 'metascore', 'imdbRating', 'imdbVotes', 'dVD', 'boxOffice', 'production', 'website']}
                movie_data.update({'imdbID': omdb_details.get('imdbID', imdb_id), 'Ratings': omdb_details.get('Ratings', []), 'type': 'movie', 'video_url': '', 'category': request.form.get('movie_category', 'ללא')})
                tmdb_id, tmdb_type = get_tmdb_info(imdb_id)
                if tmdb_id and tmdb_type == 'movie':
                    hebrew_name, hebrew_poster_url = get_hebrew_details(tmdb_id, tmdb_type)
                    if hebrew_name: movie_data['HebrewName'] = hebrew_name
                    if hebrew_poster_url: movie_data['HebrewPoster'] = hebrew_poster_url
                db.reference(f'/Movies/{imdb_id}').set(movie_data)
                flash(f'סרט "{movie_data.get("Title", imdb_id)}" נוסף בהצלחה!', 'success')
            elif content_type == 'series':
                series_imdb_id = request.form.get('series_imdb_id', '').strip()
                if not series_imdb_id:
                    flash('שגיאה: IMDb ID חסר.', 'error')
                    return redirect(url_for('add_content'))
                omdb_details = get_omdb_details_api(series_imdb_id)
                if not omdb_details or omdb_details.get('Response') == 'False' or omdb_details.get('Type', '').lower() != 'series':
                    flash(f'שגיאה: לא נמצאה סדרה עבור IMDb ID "{series_imdb_id}".', 'error')
                    return redirect(url_for('add_content'))
                total_seasons = int(omdb_details.get('totalSeasons', '1'))
                series_data = {k.capitalize(): omdb_details.get(k.capitalize(), 'N/A') for k in ['title', 'year', 'rated', 'released', 'runtime', 'genre', 'director', 'writer', 'actors', 'plot', 'language', 'country', 'awards', 'poster', 'metascore', 'imdbRating', 'imdbVotes']}
                series_data.update({'imdbID': omdb_details.get('imdbID', series_imdb_id), 'Ratings': omdb_details.get('Ratings', []), 'type': 'series', 'totalSeasons': str(total_seasons), 'category': request.form.get('series_category', 'ללא')})
                tmdb_id, tmdb_type = get_tmdb_info(series_imdb_id)
                if tmdb_id and tmdb_type == 'tv':
                    hebrew_name, hebrew_poster_url = get_hebrew_details(tmdb_id, tmdb_type)
                    if hebrew_name: series_data['HebrewName'] = hebrew_name
                    if hebrew_poster_url: series_data['HebrewPoster'] = hebrew_poster_url
                seasons_data = {}
                for season_num in range(1, total_seasons + 1):
                    season_details = get_omdb_details_api(series_imdb_id, season=season_num)
                    if season_details and season_details.get('Response') == 'True' and season_details.get('Episodes'):
                        episodes_data = {}
                        for ep_detail in season_details.get('Episodes', []):
                            try:
                                ep_num = int(ep_detail.get('Episode'))
                                episodes_data[str(ep_num)] = {'episode_imdb_id': ep_detail.get('imdbID'), 'title': ep_detail.get('Title'), 'season_number': season_num, 'episode_number': ep_num, 'video_url': ''}
                            except (ValueError, TypeError):
                                continue
                        if episodes_data: seasons_data[str(season_num)] = {'Episodes': episodes_data}
                if seasons_data: series_data['Seasons'] = seasons_data
                db.reference(f'/Series/{series_imdb_id}').update(series_data)
                flash(f'סדרה "{series_data.get("Title", series_imdb_id)}" נוספה/עודכנה בהצלחה!', 'success')
        except Exception as e:
            logging.error(f"Error processing add content POST: {e}", exc_info=True)
            flash('אירעה שגיאה בעת שמירת התוכן.', 'error')
        return redirect(url_for('add_content'))
    return render_template('add.html', user=user, categories=[c for c in CATEGORIES if c != 'ללא'],
                           available_series=load_series_list_for_add_page(), current_year=datetime.datetime.utcnow().year, admin_emails=ADMIN_EMAILS)

@app.route('/movies')
def all_movies():
    return render_template('movies.html', movies=load_movies_data(), user=session.get('user'),
                           current_year=datetime.datetime.utcnow().year, admin_emails=ADMIN_EMAILS,
                           current_language=session.get('language', 'he'), items_per_page=15)

@app.route('/api/refresh_data', methods=['POST'])
def api_refresh_data():
    user = session.get('user')
    if not user or user.get('email') not in ADMIN_EMAILS: abort(403)
    success = refresh_data_from_firebase()
    if success: return jsonify({"message": "Data cache updated successfully from Firebase."}), 200
    return jsonify({"error": "Failed to update data cache."}), 500

@app.route('/series')
def all_series():
    return render_template('SeriesTV.html', series=load_series_data_for_index(), user=session.get('user'),
                           current_year=datetime.datetime.utcnow().year, admin_emails=ADMIN_EMAILS,
                           current_language=session.get('language', 'he'), items_per_page=15)

WEBSITE_URL = "https://sratims.online/"
INTERVAL_MINUTES = 4
def keep_website_alive(url, interval_minutes):
    interval_seconds = interval_minutes * 60
    print(f"[*] Starting keep-alive background process for {url}. Sending request every {interval_minutes} minutes.")
    while True:
        time.sleep(interval_seconds)
        try:
            response = requests.get(url)
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if response.status_code == 200:
                print(f"[{current_time}] Keep-alive request to {url} successful. Status: {response.status_code}")
            else:
                print(f"[{current_time}] Keep-alive request to {url} failed. Status: {response.status_code}")
        except requests.exceptions.RequestException as e:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{current_time}] Error in keep-alive request to {url}: {e}")
keep_alive_thread = threading.Thread(target=keep_website_alive, args=(WEBSITE_URL, INTERVAL_MINUTES), daemon=True)
keep_alive_thread.start()

def get_trailer_from_imdb(imdb_id, tmdb_api_key):
    if not tmdb_api_key or tmdb_api_key == 'YOUR_TMDB_API_KEY': return None
    find_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={tmdb_api_key}&external_source=imdb_id"
    try:
        find_response = requests.get(find_url, timeout=10)
        find_response.raise_for_status()
        find_data = find_response.json()
        if not find_data.get('movie_results'): return None
        movie_id = find_data['movie_results'][0].get('id')
        if not movie_id: return None
        videos_url = f"https://api.themoviedb.org/3/movie/{movie_id}/videos?api_key={tmdb_api_key}"
        videos_response = requests.get(videos_url, timeout=10)
        videos_response.raise_for_status()
        videos_data = videos_response.json()
        for video in videos_data.get('results', []):
            if video.get('site') == 'YouTube' and video.get('type') == 'Trailer' and video.get('key'):
                return f"https://www.youtube.com/watch?v={video['key']}"
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling TMDB API for IMDb ID {imdb_id}: {e}", exc_info=True)
        return None

@app.route('/api/get_trailer/<imdb_id>')
def api_get_trailer(imdb_id):
    if not re.compile(r'^tt\d{7,}$').match(imdb_id):
        return jsonify({"error": "Invalid IMDb ID format"}), 400
    trailer_url = get_trailer_from_imdb(imdb_id, TMDB_API_KEY)
    if trailer_url: return jsonify({"trailer_url": trailer_url})
    return jsonify({"error": "Trailer not found"}), 404

def fetch_tmdb_data_with_retry(url, params, max_retries, base_delay):
    if not TMDB_API_KEY or TMDB_API_KEY == 'YOUR_TMDB_API_KEY': return None
    params['api_key'] = TMDB_API_KEY
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(min(base_delay * (2 ** attempt) + random.uniform(0, 1), 10))
            else: return None
    return None

def get_tmdb_info(imdb_id):
    url = f"{TMDB_BASE_URL}/find/{imdb_id}"
    data = fetch_tmdb_data_with_retry(url, {'external_source': 'imdb_id'}, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)
    if data:
        if data.get('movie_results'): return data['movie_results'][0].get('id'), 'movie'
        elif data.get('tv_results'): return data['tv_results'][0].get('id'), 'tv'
    return None, None

def get_hebrew_details(tmdb_id, media_type):
    if not tmdb_id or media_type not in ['movie', 'tv']: return None, None
    endpoint = f"movie/{tmdb_id}" if media_type == 'movie' else f"tv/{tmdb_id}"
    url = f"{TMDB_BASE_URL}/{endpoint}"
    data = fetch_tmdb_data_with_retry(url, {'language': 'he-IL'}, TMDB_MAX_RETRIES, TMDB_BASE_DELAY_SECONDS)
    hebrew_name, hebrew_poster_url = None, None
    if data:
        hebrew_name = data.get('title') if media_type == 'movie' else data.get('name')
        poster_path = data.get('poster_path')
        if poster_path: hebrew_poster_url = f"{TMDB_IMAGE_BASE_URL}{poster_path}"
    return hebrew_name, hebrew_poster_url

def get_recommendations(watched_ids, limit=15):
    all_content = {**load_movies_data(), **load_series_data_for_index()}
    if not watched_ids or not all_content: return []
    watched_items_details = [d for i, d in all_content.items() if i in watched_ids and isinstance(d, dict)]
    candidate_items = {i: d for i, d in all_content.items() if i not in watched_ids and isinstance(d, dict)}
    if not watched_items_details or not candidate_items: return []
    profile = {'categories': {}, 'genres': {}, 'ratings': []}
    for item in watched_items_details:
        cat = item.get('category', 'ללא')
        if cat != 'ללא': profile['categories'][cat] = profile['categories'].get(cat, 0) + 1
        for g in [g.strip() for g in item.get('genre', '').split(',') if g.strip()]: profile['genres'][g] = profile['genres'].get(g, 0) + 1
        try: profile['ratings'].append(float(item.get('imdbRating')))
        except (ValueError, TypeError): pass
    avg_rating = sum(profile['ratings']) / len(profile['ratings']) if profile['ratings'] else 7.0
    scored_candidates = []
    for imdb_id, item in candidate_items.items():
        score = 0
        if item.get('category', 'ללא') in profile['categories']: score += profile['categories'][item.get('category')] * 3
        for g in [g.strip() for g in item.get('genre', '').split(',') if g.strip()]:
            if g in profile['genres']: score += profile['genres'][g] * 2
        try:
            score += max(0, 1 - (abs(float(item.get('imdbRating')) - avg_rating) / 5)) * 1.5
        except (ValueError, TypeError): pass
        if score > 0:
            scored_candidates.append({k: item.get(k) for k in ["id", "title", "poster", "HebrewName", "HebrewPoster", "type"]} | {"score": score, "id": imdb_id})
    return sorted(scored_candidates, key=lambda x: x['score'], reverse=True)[:limit]

@app.route('/api/recommendations', methods=['POST'])
def api_recommendations():
    if not session.get('user'): return jsonify({"error": "User not authenticated"}), 401
    data = request.get_json()
    if not data or 'watched_ids' not in data: return jsonify({"error": "Missing watched_ids"}), 400
    watched_ids = data['watched_ids']
    if not isinstance(watched_ids, list): return jsonify({"error": "watched_ids must be a list"}), 400
    return jsonify(get_recommendations(watched_ids))

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html', user=session.get('user'), current_year=datetime.datetime.utcnow().year, current_language=session.get('language', 'he')), 403

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html', user=session.get('user'), current_year=datetime.datetime.utcnow().year, current_language=session.get('language', 'he')), 404

@app.errorhandler(500)
def internal_server_error(e):
    logging.error(f"Internal Server Error: {request.path} - {e}\n{traceback.format_exc()}", exc_info=True)
    return render_template('500.html', user=session.get('user'), current_year=datetime.datetime.utcnow().year, current_language=session.get('language', 'he')), 500

def initialize_app_data():
    initialize_groups_file()
    if os.path.exists(DATA_FILE): load_data_from_json()
    else: refresh_data_from_firebase()
initialize_app_data()

def get_user_current_group(user_id):
    groups = load_groups()
    for group_id, group_data in groups.items():
        if user_id in group_data.get('participants', {}):
            return group_id
    return None

@app.route('/stream/create/<imdb_id>')
def create_stream(imdb_id):
    user = session.get('user')
    if not user:
        flash('עליך להתחבר כדי ליצור קבוצת צפייה.', 'warning')
        return redirect(url_for('google_login'))
    user_id = user['google_id']
    if get_user_current_group(user_id):
        flash('אתה כבר חבר בקבוצת צפייה.', 'error')
        return redirect(request.referrer or url_for('index'))
    movie = load_movie_details(imdb_id)
    if not movie: abort(404)
    group_id = uuid.uuid4().hex
    groups = load_groups()
    groups[group_id] = {
        'movie_id': imdb_id, 'host_id': user_id,
        'participants': {user_id: {'name': user['name'], 'picture': user['picture']}},
        'chat_history': []
    }
    save_groups(groups)
    return redirect(url_for('watch_stream', group_id=group_id))

@app.route('/stream/join/<group_id>')
def join_stream(group_id):
    user = session.get('user')
    if not user:
        flash('עליך להתחבר כדי להצטרף.', 'warning')
        return redirect(url_for('google_login'))
    user_id = user['google_id']
    groups = load_groups()
    if group_id not in groups:
        flash('קבוצה זו אינה קיימת.', 'error')
        return redirect(url_for('index'))
    current_group = get_user_current_group(user_id)
    if current_group:
        if current_group == group_id: return redirect(url_for('watch_stream', group_id=group_id))
        flash('אתה כבר חבר בקבוצה אחרת.', 'error')
        return redirect(url_for('index'))
    
    user_info = {'name': user['name'], 'picture': user['picture']}
    groups[group_id]['participants'][user_id] = user_info
    save_groups(groups)
    
    socketio.emit('user_joined', {'user_id': user_id, 'user_info': user_info}, room=group_id)
    return redirect(url_for('watch_stream', group_id=group_id))

@app.route('/stream/watch/<group_id>')
def watch_stream(group_id):
    user = session.get('user')
    if not user:
        flash('עליך להתחבר כדי לצפות.', 'warning')
        return redirect(url_for('index'))
    groups = load_groups()
    if group_id not in groups or user['google_id'] not in groups[group_id].get('participants', {}):
        flash('אינך חבר בקבוצה זו או שהיא נסגרה.', 'error')
        return redirect(url_for('index'))
    movie = load_movie_details(groups[group_id]['movie_id'])
    if not movie:
        flash('הסרט המשויך לקבוצה זו לא נמצא.', 'error')
        return redirect(url_for('index'))
    return render_template('stream.html', user=user, group_id=group_id, group_data=groups[group_id], movie=movie)

@socketio.on('join_group_room')
def handle_join_group_room(data):
    user = session.get('user')
    if not user: return
    group_id = data.get('group_id')
    if group_id:
        user_id = user['google_id']
        user_connections.setdefault(user_id, set()).add(request.sid)
        join_room(group_id)
        logging.info(f"Socket connection: User {user.get('email')} joined room {group_id} with sid {request.sid}. Total connections: {len(user_connections[user_id])}")

@socketio.on('send_chat_message')
def handle_chat_message(data):
    user = session.get('user')
    if not user: return
    group_id = data.get('group_id')
    message_text = data.get('message')
    if group_id and message_text:
        groups = load_groups()
        if group_id in groups and user['google_id'] in groups[group_id]['participants']:
            message_data = {
                'user_name': user['name'],
                'user_picture': user['picture'],
                'message': message_text,
                'timestamp': datetime.datetime.utcnow().isoformat()
            }
            groups[group_id].setdefault('chat_history', []).append(message_data)
            save_groups(groups)
            emit('new_chat_message', message_data, room=group_id)

@socketio.on('kick_participant')
def handle_kick_participant(data):
    user = session.get('user')
    if not user: return
    group_id = data.get('group_id')
    target_user_id = data.get('target_user_id')
    groups = load_groups()
    if group_id in groups and groups[group_id]['host_id'] == user['google_id']:
        if target_user_id in groups[group_id]['participants']:
            del groups[group_id]['participants'][target_user_id]
            save_groups(groups)
            emit('update_group_data', {'group_data': groups[group_id]}, room=group_id)
            emit('you_were_kicked', {'kicked_user_id': target_user_id}, room=group_id)

@socketio.on('promote_to_host')
def handle_promote_to_host(data):
    user = session.get('user')
    if not user: return
    group_id = data.get('group_id')
    target_user_id = data.get('target_user_id')
    groups = load_groups()
    if group_id in groups and groups[group_id]['host_id'] == user['google_id']:
        if target_user_id in groups[group_id]['participants']:
            groups[group_id]['host_id'] = target_user_id
            save_groups(groups)
            emit('update_group_data', {'group_data': groups[group_id]}, room=group_id)

@socketio.on('disconnect')
def handle_disconnect():
    user = session.get('user')
    if not user: return
    user_id = user['google_id']
    sid = request.sid
    if user_id in user_connections and sid in user_connections[user_id]:
        user_connections[user_id].remove(sid)
        logging.info(f"Socket disconnected for user {user['email']} with sid {sid}. Remaining connections: {len(user_connections[user_id])}")
        if not user_connections[user_id]:
            del user_connections[user_id]
            group_id = get_user_current_group(user_id)
            if group_id:
                groups = load_groups()
                if group_id in groups and user_id in groups[group_id]['participants']:
                    group = groups[group_id]
                    logging.info(f"User {user_id} has no active connections left. Removing from group {group_id}.")
                    del group['participants'][user_id]
                    if not group['participants']:
                        del groups[group_id]
                        logging.info(f"Group {group_id} is empty and has been deleted.")
                    elif group['host_id'] == user_id:
                        if group['participants']:
                            new_host_id = next(iter(group['participants']))
                            group['host_id'] = new_host_id
                            logging.info(f"Host {user['email']} left group {group_id}. New host is {new_host_id}.")
                        else:
                            del groups[group_id]
                            logging.info(f"Host was the last participant. Group {group_id} deleted.")
                    save_groups(groups)
                    if group_id in groups:
                        emit('update_group_data', {'group_data': groups[group_id]}, room=group_id)

if __name__ == '__main__':
    if firebase_admin._apps:
        try:
             default_app_creds = firebase_admin._apps['[DEFAULT]'].options.get('credential')
             if default_app_creds is not None:
                port = int(os.environ.get('PORT', 5000))
                socketio.run(app, host='0.0.0.0', port=port, debug=False)
             else:
                 logging.error("Application not started: Firebase default app credential is None.")
        except KeyError:
            logging.error("Application not started: Firebase default app was not initialized.")
        except Exception as e:
             logging.error(f"Application not started: Unexpected error during Firebase check: {e}", exc_info=True)
    else:
        logging.error("Application not started because Firebase initialization failed.")
