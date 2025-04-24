# main.py
import datetime
import traceback
import os
import requests
import json
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify
from authlib.integrations.flask_client import OAuth

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'moviesilsuperdupersecretkey')

app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID', '367711020009-o70b96v4cv604acg2hqv60k8c5mjmhtr.apps.googleusercontent.com')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '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')
app.config['GOOGLE_DISCOVERY_URL'] = (
    'https://accounts.google.com/.well-known/openid-configuration'
)

OMDB_API_KEY = os.environ.get('OMDB_API_KEY', '4ea6447b')

ADMIN_EMAIL = 'ehudverbin@gmail.com'

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
    return {}

def load_series_data():
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
        if not isinstance(movie_details, dict):
            continue

        category = movie_details.get('category', 'ללא')
        if category != "ללא" and category in CATEGORIES:
             categorized_movies[category].append({
                "id": imdb_id,
                "title": movie_details.get('title', 'Untitled'),
                "poster": movie_details.get('poster_url', 'https://placehold.co/240x360/cccccc/000000?text=No+Poster'),
                "video_url": movie_details.get('video_url', '#')
             })
        elif category == "ללא":
            pass
        else:
             pass

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
        return f"{greeting_text} {user['name']}"
    else:
        return f"{greeting_text} אורח"

OMDB_BASE_URL = 'http://www.omdbapi.com/'

def search_omdb_api(search_term, content_type):
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY':
        return []
    params = {
        'apikey': OMDB_API_KEY,
        's': search_term,
        'type': content_type
    }
    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('Response') == 'True':
            return data.get('Search', [])
        else:
            return []
    except requests.exceptions.RequestException as e:
        return []

def get_omdb_details_api(imdb_id):
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY':
         return None
    params = {
        'apikey': OMDB_API_KEY,
        'i': imdb_id,
        'plot': 'full'
    }
    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('Response') == 'True':
            return data
        else:
             return None
    except requests.exceptions.RequestException as e:
        return None

@app.route('/api/search_omdb')
def api_search_omdb():
    search_term = request.args.get('s')
    content_type = request.args.get('type')
    if not search_term or not content_type:
        return jsonify({"Error": "Missing search term or type"}), 400

    results = search_omdb_api(search_term, content_type)
    return jsonify(results)

@app.route('/api/get_omdb_details')
def api_get_omdb_details():
    imdb_id = request.args.get('i')
    if not imdb_id:
        return jsonify({"Error": "Missing IMDb ID"}), 400

    details = get_omdb_details_api(imdb_id)
    if details:
        return jsonify(details)
    else:
        return jsonify({"Error": "Details not found or API error"}), 404

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
        userinfo_response = oauth.google.userinfo(token=token)

        userinfo = {
            'name': userinfo_response.get('name'),
            'email': userinfo_response.get('email'),
            'picture': userinfo_response.get('picture'),
            'google_id': userinfo_response.get('sub')
        }

        if not userinfo.get('google_id'):
            flash('התחברות עם גוגל נכשלה: לא הושגו פרטי משתמש.', 'error')
            return redirect(url_for('index'))

        session['user'] = userinfo
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

@app.route('/')
def index():
    user = session.get('user')
    greeting = get_greeting(user)

    movies_data = load_movies_data()
    categories = categorize_movies(movies_data)
    current_year = datetime.datetime.utcnow().year

    return render_template('index.html',
                           greeting=greeting,
                           categories=categories,
                           current_year=current_year,
                           user=user
                           )

@app.route('/add', methods=['GET', 'POST'])
def add_content():
    user = session.get('user')
    if not user or user.get('email') != ADMIN_EMAIL:
        abort(403)

    available_series = load_series_data()

    if request.method == 'POST':
        content_type = request.form.get('content_type')

        if content_type == 'movie':
            imdb_id = request.form.get('movie_imdb_id', '').strip()
            title = request.form.get('movie_title', '').strip()
            video_url = request.form.get('movie_video_url', '').strip()
            poster_url = request.form.get('movie_poster_url', '').strip()
            category = request.form.get('movie_category', 'ללא')

            if not imdb_id or not title or not video_url:
                flash('שגיאה: שדות חובה (IMDb ID, כותרת, וידאו) חסרים עבור סרט.', 'error')
                return redirect(url_for('add_content'))

            if not imdb_id.startswith('tt') or len(imdb_id) < 7:
                 flash('שגיאה: פורמט IMDb ID לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו מספרים.', 'error')
                 return redirect(url_for('add_content'))

        elif content_type == 'series':
            imdb_id = request.form.get('series_imdb_id', '').strip()
            title = request.form.get('series_title', '').strip()
            poster_url = request.form.get('series_poster_url', '').strip()

            if not imdb_id or not title:
                flash('שגיאה: שדות חובה (IMDb ID, כותרת) חסרים עבור סדרה.', 'error')
                return redirect(url_for('add_content'))

            if not imdb_id.startswith('tt') or len(imdb_id) < 7:
                 flash('שגיאה: פורמל IMDb ID של הסדרה לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו מספרים.', 'error')
                 return redirect(url_for('add_content'))

        elif content_type == 'episode':
             series_imdb_id_select = request.form.get('episode_series_id')
             manual_series_imdb_id = request.form.get('manual_episode_series_id', '').strip()
             episode_title = request.form.get('episode_title', '').strip()
             episode_number_str = request.form.get('episode_number', '').strip()
             season_number_str = request.form.get('episode_season', '').strip()
             video_url = request.form.get('episode_video_url', '').strip()

             series_imdb_id = manual_series_imdb_id if series_imdb_id_select == 'manual' else series_imdb_id_select

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

             if series_imdb_id_select == 'manual' and (not series_imdb_id.startswith('tt') or len(series_imdb_id) < 7):
                  flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין (הזנה ידנית). ודא שהוא מתחיל ב-"tt" ואחריו מספרים.', 'error')
                  return redirect(url_for('add_content'))

        else:
             flash('סוג תוכן לא ידוע.', 'warning')

        return redirect(url_for('add_content'))

    available_series = load_series_data()

    return render_template('add.html',
                           user=user,
                           categories=CATEGORIES,
                           available_series=available_series,
                           current_year=datetime.datetime.utcnow().year
                           )

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
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    return render_template('500.html', user=user, current_year=current_year), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
