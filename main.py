import datetime
import traceback
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify
import os
from authlib.integrations.flask_client import OAuth

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'moviesilsuperdupersecretkey')

app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID', '367711020009-o70b96v4cv604acg2hqv60k8c5mjmhtr.apps.googleusercontent.com')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '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')
app.config['GOOGLE_DISCOVERY_URL'] = (
    'https://accounts.google.com/.well-known/openid-configuration'
)

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
    dummy_movies = {
        "tt0133093": {
            "title": "המטריקס",
            "poster_url": "https://m.media-amazon.com/images/M/MV5BNzQzOTk3OTAtNDQ0Zi00ZTVkLWI0MTEtMDllZjNkYzNjNTc4L2ltYWdlXkEyXkFqcGdeQXVyNjU0OTQ0OTY@._V1_SX300.jpg",
            "video_url": "#",
            "category": "הסרטים הנצפים ביותר השבוע"
        },
         "tt0120737": {
            "title": "שר הטבעות: אחוות הטבעת",
            "poster_url": "https://m.media-amazon.com/images/M/MV5BN2EyZjM3NzUtNWUzMi00MTgxLWI0NTctMzY4M2VlOTdjZaeXkEyXkFqcGdeQXVyNDUzOTQ5MjY@._V1_SX300.jpg",
            "video_url": "#",
            "category": "הסרטים הנצפים ביותר השבוע"
        },
         "tt0848228": {
            "title": "הנוקמים",
            "poster_url": "https://m.media-amazon.com/images/M/MV5BNDYxNjQyMjAtNTdlNC00YzM4LTg4OnItMDEzYzE5NzZhZWExXkEyXkFqcGdeQXVyNjU0OTQ0OTY@._V1_SX300.jpg",
            "video_url": "#",
            "category": "היקום הקולנועי של מארוול"
         },
    }
    return dummy_movies


def categorize_movies(movies_data):
    categorized_movies = {}
    for cat in CATEGORIES:
        if cat != "ללא":
            categorized_movies[cat] = []

    if not movies_data:
        return categorized_movies

    for imdb_id, movie_details in movies_data.items():
        category = movie_details.get('category', 'ללא')
        if category != "ללא" and category in CATEGORIES:
             categorized_movies[category].append({
                "id": imdb_id,
                "title": movie_details.get('title', 'Untitled'),
                "poster": movie_details.get('poster_url', 'https://placehold.co/240x360/cccccc/000000?text=No+Poster'),
                "video_url": movie_details.get('video_url', '#')
             })
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


@app.route('/auth/google')
def google_login():
    return oauth.google.authorize_redirect(redirect_uri=url_for('google_callback', _external=True))

@app.route('/auth/google/callback')
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        userinfo = {
            'name': token.get('name'),
            'email': token.get('email'),
            'picture': token.get('picture'),
            'google_id': token.get('sub')
        }

        session['user'] = userinfo
        session.permanent = True
        flash('התחברת בהצלחה עם גוגל!', 'success')
        return redirect(url_for('index'))

    except Exception as e:
        print(f"OAuth callback error: {e}")
        traceback.print_exc()
        flash('התחברות עם גוגל נכשלה. אנא נסה שוב.', 'error')
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

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    print(f"SERVER ERROR: {e}")
    tb_str = traceback.format_exc()
    print(f"SERVER ERROR TRACEBACK:\n{tb_str}")
    return render_template('500.html'), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
