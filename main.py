import datetime
import traceback
import os
import requests
import json
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify
from authlib.integrations.flask_client import OAuth
import firebase_admin
from firebase_admin import credentials, db
from firebase_admin.exceptions import FirebaseError # Import FirebaseError for better error handling

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'moviesilsuperdupersecretkey')

# Note: Using placeholder values. Replace with your actual credentials in production/environment variables.
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID', '367711020009-o70b96v4cv604acg2hqv60k8c5mjmhtr.apps.googleusercontent.com')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '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')
app.config['GOOGLE_DISCOVERY_URL'] = (
    'https://accounts.google.com/.well-known/openid-configuration'
)

# --- API Keys and Configs ---
# It's better to load these from environment variables
OMDB_API_KEY = os.environ.get('OMDB_API_KEY', '4ea6447b') # OMDb API Key

# Firebase Configuration
FIREBASE_CONFIG_PATH = 'firebase.json' # Path to your firebase.json file
FIREBASE_DATABASE_URL = 'https://moviesweb-3015a-default-rtdb.firebaseio.com/' # Your Firebase DB URL

# Initialize Firebase Admin SDK
try:
    # Check if Firebase app is already initialized
    # This is important for development environments with hot-reloading
    if not firebase_admin._apps:
        # Use the path to the service account file directly
        cred = credentials.Certificate(FIREBASE_CONFIG_PATH)
        firebase_admin.initialize_app(cred, {
            'databaseURL': FIREBASE_DATABASE_URL
        })
        print("Firebase Admin SDK initialized successfully.")
    else:
         print("Firebase Admin SDK already initialized.")

except FileNotFoundError:
    print(f"Error: Firebase config file not found at {FIREBASE_CONFIG_PATH}. Cannot initialize Firebase.")
except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
    # Depending on deployment, you might want to abort startup here
    # For now, we print the error and allow the app to run (though Firebase calls will fail)


# --- User Whitelist for Admin Features ---
ADMIN_EMAIL = 'ehudverbin@gmail.com'
# ----------------------------------------

app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(days=31)

oauth = OAuth(app)

oauth.register(
    'google',
    client_id=app.config.get('GOOGLE_CLIENT_ID'),
    client_secret=app.config.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url=app.config.get('GOOGLE_DISCOVERY_URL'),
    client_kwargs={'scope': 'openid email profile'},
)

CATEGORIES = [
    "הסרטים הנצפים ביותר השבוע",
    "הסדרות הנצפים ביותר השבוע",
    "היקום הקולנועי של מארוול",
    "מלחמת הכוכבים",
    "אקס-מן",
    "ספיידרמן",
    "ללא"
]

def load_movies_data():
    # Load movies from Firebase - Dummy categories applied in categorize_movies
    try:
        # Check if Firebase app is initialized before attempting to get reference
        if not firebase_admin._apps:
             print("Firebase app not initialized. Cannot load movies.")
             return {}

        ref = db.reference('/Movies')
        movies_data = ref.get()
        if movies_data is None:
            return {} # Return empty if no movies found

        # Add a dummy category for demonstration purposes if not saved
        for movie_id, movie_details in movies_data.items():
             if 'category' not in movie_details or movie_details['category'] not in CATEGORIES:
                  # Assign a default category if missing or invalid
                 movie_details['category'] = CATEGORIES[0] if CATEGORIES and CATEGORIES[0] != "ללא" else 'ללא'
        return movies_data
    except FirebaseError as e:
        print(f"Firebase error loading movies: {e}")
        return {} # Return empty data on Firebase error
    except Exception as e:
        print(f"Unexpected error loading movies from Firebase: {e}")
        traceback.print_exc()
        return {} # Return empty data on other errors


def load_series_data():
    # Load series details from Firebase for the dropdown
    try:
        # Check if Firebase app is initialized
        if not firebase_admin._apps:
            print("Firebase app not initialized. Cannot load series list.")
            # Fallback to dummy data if Firebase not initialized
            return [
              {"id": "tt0903747", "title": "שובר שורות (נתוני דמה)"},
              {"id": "tt0944947", "title": "משחקי הכס (נתוני דמה)"},
           ]

        ref = db.reference('/Series')
        all_series_data = ref.get()
        available_series = []
        if all_series_data:
            for series_id, series_details in all_series_data.items():
                # Ensure 'Details' exists and has a title
                if series_details and 'Details' in series_details and series_details['Details'].get('title'):
                    available_series.append({
                        "id": series_id,
                        "title": series_details['Details'].get('title')
                    })
        return available_series
    except FirebaseError as e:
        print(f"Firebase error loading series list: {e}")
        # Fallback to dummy data on Firebase error
        return [
           {"id": "tt0903747", "title": "שובר שורות (נתוני דמה)"},
           {"id": "tt0944947", "title": "משחקי הכס (נתוני דמה)"},
        ]
    except Exception as e:
        print(f"Unexpected error loading series list from Firebase: {e}")
        traceback.print_exc()
         # Fallback to dummy data on other errors
        return [
           {"id": "tt0903747", "title": "שובר שורות (נתוני דמה)"},
           {"id": "tt0944947", "title": "משחקי הכס (נתוני דמה)"},
        ]


def categorize_movies(movies_data):
    categorized_movies = {}
    for cat in CATEGORIES:
        if cat != "ללא":
            categorized_movies[cat] = []

    if not movies_data:
        return categorized_movies

    for imdb_id, movie_details in movies_data.items():
        # Use the saved category if it exists and is valid, otherwise use 'ללא'
        category = movie_details.get('category', 'ללא')
        # Only add to category lists if the category is valid and not "ללא"
        if category != "ללא" and category in CATEGORIES:
             categorized_movies[category].append({
                "id": imdb_id,
                "title": movie_details.get('title', 'Untitled'),
                "poster": movie_details.get('poster_url', 'https://placehold.co/240x360/cccccc/000000?text=No+Poster'),
                "video_url": movie_details.get('video_url', '#')
             })
        elif category == "ללא":
            # Movies with 'ללא' category are not added to any category section on the index page
            pass
        else:
             # Handle cases where a saved category might be invalid/old
             print(f"Warning: Movie {imdb_id} has invalid category '{category}'. Skipping categorization for index display.")


    return categorized_movies


# --- OMDb API Functions ---
OMDB_BASE_URL = 'http://www.omdbapi.com/'

def search_omdb_api(search_term, content_type):
    """Searches OMDb by title for movies or series."""
    params = {
        'apikey': OMDB_API_KEY,
        's': search_term,
        'type': content_type # 'movie' or 'series'
    }
    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10) # Added timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        if data.get('Response') == 'True':
            return data.get('Search', []) # Returns a list of dictionaries
        else:
            print(f"OMDb search error for '{search_term}' ({content_type}): {data.get('Error')}")
            return []
    except requests.exceptions.RequestException as e:
        print(f"OMDb search request failed for '{search_term}' ({content_type}): {e}")
        return []

def get_omdb_details_api(imdb_id):
    """Gets full details for a movie or series by IMDb ID."""
    params = {
        'apikey': OMDB_API_KEY,
        'i': imdb_id,
        'plot': 'full' # Request full plot
    }
    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10) # Added timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        if data.get('Response') == 'True':
            return data # Returns a dictionary with details
        else:
             print(f"OMDb details error for '{imdb_id}': {data.get('Error')}")
             return None
    except requests.exceptions.RequestException as e:
        print(f"OMDb details request failed for '{imdb_id}': {e}")
        return None

# --- Flask Routes for API calls from Frontend ---
@app.route('/api/search_omdb')
def api_search_omdb():
    search_term = request.args.get('s')
    content_type = request.args.get('type') # 'movie' or 'series'
    if not search_term or not content_type:
        return jsonify({"Error": "Missing search term or type"}), 400

    results = search_omdb_api(search_term, content_type)
    return jsonify(results) # results is already a list or empty list

@app.route('/api/get_omdb_details')
def api_get_omdb_details():
    imdb_id = request.args.get('i')
    if not imdb_id:
        return jsonify({"Error": "Missing IMDb ID"}), 400

    details = get_omdb_details_api(imdb_id)
    if details:
        return jsonify(details) # details is a dictionary
    else:
        return jsonify({"Error": "Details not found or API error"}), 404


# --- Authentication Routes ---
@app.route('/auth/google')
def google_login():
    return oauth.google.authorize_redirect(redirect_uri=url_for('google_callback', _external=True))

@app.route('/auth/google/callback')
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        # Fetch user info using the access token
        # Note: userinfo call requires the 'profile' and 'email' scopes
        userinfo_response = oauth.google.userinfo(token=token)

        userinfo = {
            'name': userinfo_response.get('name'),
            'email': userinfo_response.get('email'),
            'picture': userinfo_response.get('picture'),
            'google_id': userinfo_response.get('sub')
        }

        if not userinfo.get('google_id'):
            print("Failed to get Google user ID from userinfo response.")
            flash('התחברות עם גוגל נכשלה: לא הושגו פרטי משתמש.', 'error')
            return redirect(url_for('index'))


        session['user'] = userinfo
        session.permanent = True
        flash('התחברת בהצלחה!', 'success') # Updated flash message
        return redirect(url_for('index'))

    except Exception as e:
        print(f"OAuth callback error: {e}")
        traceback.print_exc()
        flash('התחברות נכשלה. אנא ודא שההרשאות המתאימות אושרו ונסה שוב.', 'error') # Updated flash message
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('התנתקת בהצלחה.', 'info')
    return redirect(url_for('index'))

# --- Main Index Route ---
@app.route('/')
def index():
    user = session.get('user')
    greeting = get_greeting(user)

    movies_data = load_movies_data() # Load data from Firebase
    categories = categorize_movies(movies_data)
    current_year = datetime.datetime.utcnow().year

    return render_template('index.html',
                           greeting=greeting,
                           categories=categories,
                           current_year=current_year,
                           user=user
                           )

# --- Add Content Route ---
@app.route('/add', methods=['GET', 'POST'])
def add_content():
    user = session.get('user')
    # Check if user is logged in and is the authorized admin email
    if not user or user.get('email') != ADMIN_EMAIL:
        abort(403) # Forbidden

    # Load available series from Firebase for the dropdown
    available_series = load_series_data()

    if request.method == 'POST':
        content_type = request.form.get('content_type')

        # Check if Firebase app is initialized before attempting to save
        if not firebase_admin._apps:
             flash('שגיאה: מערכת מסד הנתונים אינה זמינה. נסה שוב מאוחר יותר.', 'error')
             print("Firebase app not initialized. Cannot save content.")
             return redirect(url_for('add_content'))

        ref = db.reference('/') # Root reference

        try:
            if content_type == 'movie':
                # Get data from form (prioritize OMDb selected hidden fields)
                # Fallback to manual inputs if OMDb fields are empty
                imdb_id = request.form.get('selected_movie_imdb_id') or request.form.get('movie_imdb_id')
                title = request.form.get('selected_movie_title') or request.form.get('movie_title')
                poster_url = request.form.get('movie_poster_url') # This is populated by JS from OMDb
                video_url = request.form.get('movie_video_url')
                category = request.form.get('movie_category', 'ללא')


                # Simple validation
                if not imdb_id or not title or not video_url:
                    flash('שגיאה: שדות חובה (IMDb ID, כותרת, וידאו) חסרים עבור סרט.', 'error')
                    return redirect(url_for('add_content'))

                # Check IMDb ID format (basic)
                if not imdb_id.startswith('tt') or len(imdb_id) < 7: # IMDB IDs are at least 7 digits after tt
                     flash('שגיאה: פורמט IMDb ID לא תקין.', 'error')
                     return redirect(url_for('add_content'))


                movie_data = {
                    'imdb_id': imdb_id, # Use the determined IMDb ID
                    'title': title,     # Use the determined title
                    'video_url': video_url,
                    'poster_url': poster_url or 'https://placehold.co/240x360/cccccc/000000?text=No+Poster', # Use OMDb poster or default
                    'category': category
                }

                # Save to Firebase under /Movies/{imdb_id}
                movies_ref = ref.child('Movies').child(imdb_id)
                movies_ref.set(movie_data) # Use set to overwrite or create

                flash(f'הסרט "{title}" ({imdb_id}) נוסף/עודכן בהצלחה!', 'success') # Added /עודכן

            elif content_type == 'series':
                # Get data from form (prioritize OMDb selected hidden fields)
                 # Fallback to manual inputs if OMDb fields are empty
                imdb_id = request.form.get('selected_series_imdb_id') or request.form.get('series_imdb_id')
                title = request.form.get('selected_series_title') or request.form.get('series_title')
                poster_url = request.form.get('series_poster_url') # This is populated by JS from OMDb
                 # Add fields for description, genres, etc. if needed for series details

                # Simple validation
                if not imdb_id or not title:
                    flash('שגיאה: שדות חובה (IMDb ID, כותרת) חסרים עבור סדרה.', 'error')
                    return redirect(url_for('add_content'))

                # Check IMDb ID format (basic)
                if not imdb_id.startswith('tt') or len(imdb_id) < 7:
                     flash('שגיאה: פורמט IMDb ID לא תקין.', 'error')
                     return redirect(url_for('add_content'))

                series_details_data = {
                    'imdb_id': imdb_id, # Use the determined IMDb ID
                    'title': title,     # Use the determined title
                    'poster_url': poster_url or 'https://placehold.co/240x360/cccccc/000000?text=No+Poster', # Use OMDb poster or default
                    # Add other series details here later
                }

                # Save series details to Firebase under /Series/{imdb_id}/Details
                series_details_ref = ref.child('Series').child(imdb_id).child('Details')
                series_details_ref.set(series_details_data)

                flash(f'הסדרה "{title}" ({imdb_id}) נוספה/עודכנה בהצלחה!', 'success') # Added 'עודכנה' as set overwrites

            elif content_type == 'episode':
                 # Get data from form
                 series_imdb_id_select = request.form.get('episode_series_id')
                 manual_series_imdb_id = request.form.get('manual_episode_series_id')
                 episode_title = request.form.get('episode_title')
                 episode_number_str = request.form.get('episode_number') # Get as string first
                 season_number_str = request.form.get('episode_season')   # Get as string first
                 video_url = request.form.get('episode_video_url')

                 # Determine the series ID
                 series_imdb_id = manual_series_imdb_id.strip() if series_imdb_id_select == 'manual' else series_imdb_id_select

                 # Validate numbers and required fields
                 if not series_imdb_id or not episode_title or not episode_number_str or not season_number_str or not video_url:
                      flash('שגיאה: שדות חובה (סדרה, כותרת פרק, מספר פרק, עונה, וידאו) חסרים עבור פרק.', 'error')
                      return redirect(url_for('add_content'))

                 try:
                      season_number = int(season_number_str)
                      episode_number = int(episode_number_str)
                      if season_number < 1 or episode_number < 1:
                          raise ValueError("Numbers must be positive")
                 except ValueError:
                      flash('שגיאה: מספרי עונה ופרק חייבים להיות מספרים שלמים חיוביים.', 'error')
                      return redirect(url_for('add_content'))


                 # Check IMDb ID format (for manual input)
                 if series_imdb_id_select == 'manual' and (not series_imdb_id.startswith('tt') or len(series_imdb_id) < 7):
                      flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין (הזנה ידנית).', 'error')
                      return redirect(url_for('add_content'))

                 # Check if the selected/manual series_imdb_id exists in Firebase (Optional but good practice)
                 # This would require fetching /Series/{series_imdb_id}/Details first.
                 # For simplicity, we'll skip this check for now, Firebase will just create the path if it doesn't exist.

                 # Create a key for the episode (e.g., S01E01)
                 episode_key = f"S{season_number:02d}E{episode_number:02d}" # Ensure season/episode are numbers with leading zeros


                 episode_data = {
                     'title': episode_title,
                     # 'series_imdb_id': series_imdb_id, # Can add this here if desired, but path already contains it
                     'season_number': season_number,
                     'episode_number': episode_number,
                     'video_url': video_url,
                     # Add episode IMDb ID, plot summary etc. if available/needed
                     'added_at': datetime.datetime.utcnow().isoformat(), # Optional: add timestamp
                 }

                 # Save to Firebase under /Series/{series_imdb_id}/Episodes/{episode_key}
                 episodes_ref = ref.child('Series').child(series_imdb_id).child('Episodes').child(episode_key)
                 episodes_ref.set(episode_data) # Use set for specific season/episode, push() would generate random key

                 flash(f'הפרק "{episode_title}" (עונה {season_number} פרק {episode_number}) נוסף/עודכן בהצלחה לסדרה "{series_imdb_id}"!', 'success') # Added /עודכן

            else:
                 flash('סוג תוכן לא ידוע.', 'warning')

        except FirebaseError as e:
            print(f"Firebase error saving content: {e}")
            traceback.print_exc()
            flash(f'שגיאה במסד הנתונים בעת שמירת התוכן: {e}', 'error') # Provide Firebase error details
        except Exception as e:
            print(f"Unexpected error saving content: {e}")
            traceback.print_exc()
            flash('שגיאה פנימית בעת שמירת התוכן. נסה שוב.', 'error')


        return redirect(url_for('add_content')) # Redirect after POST

    # For GET request, render the add page
    return render_template('add.html',
                           user=user,
                           categories=CATEGORIES,
                           available_series=available_series
                           )


# Error handlers
@app.errorhandler(403)
def forbidden(e):
    user = session.get('user')
    return render_template('403.html', user=user), 403

@app.errorhandler(404)
def page_not_found(e):
    user = session.get('user')
    return render_template('404.html', user=user), 404

@app.errorhandler(500)
def internal_server_error(e):
    print(f"SERVER ERROR: {e}")
    tb_str = traceback.format_exc()
    print(f"SERVER ERROR TRACEBACK:\n{tb_str}")
    user = session.get('user')
    return render_template('500.html', user=user), 500


if __name__ == '__main__':

    port = int(os.environ.get('PORT', 5000))
    # When deploying, debug=False is highly recommended
    app.run(host='0.0.0.0', port=port, debug=True)
