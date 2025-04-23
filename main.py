import datetime
import traceback
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'moviesilsuperdupersecretkey'

CATEGORIES = [
    "הסרטים הנצפים ביותר השבוע",
    "הסדרות הנצפות ביותר השבוע",
    "היקום הקולנועי של מארוול",
    "מלחמת הכוכבים",
    "אקס-מן",
    "ספיידרמן",
    "ללא"
]

# Replace Firebase data loading with a static placeholder or empty data
# In a real app, you would load this from a database, file, or API
def load_movies_data():
    # This is a placeholder structure. Replace with your actual movie data loading logic.
    # For now, it's empty or contains dummy data matching the structure.
    dummy_movies = {
        "tt0133093": { # The Matrix
            "title": "המטריקס",
            "poster_url": "https://m.media-amazon.com/images/M/MV5BNzQzOTk3OTAtNDQ0Zi00ZTVkLWI0MTEtMDllZjNkYzNjNTc4L2ltYWdlXkEyXkFqcGdeQXVyNjU0OTQ0OTY@._V1_SX300.jpg",
            "video_url": "#", # Replace with actual video URL
            "category": "הסרטים הנצפים ביותר השבוע"
        },
         "tt0120737": { # The Lord of the Rings: The Fellowship of the Ring
            "title": "שר הטבעות: אחוות הטבעת",
            "poster_url": "https://m.media-amazon.com/images/M/MV5BN2EyZjM3NzUtNWUzMi00MTgxLWI0NTctMzY4M2VlOTdjZaeXkEyXkFqcGdeQXVyNDUzOTQ5MjY@._V1_SX300.jpg",
            "video_url": "#", # Replace with actual video URL
            "category": "הסרטים הנצפים ביותר השבוע"
        },
         "tt0848228": { # The Avengers
            "title": "הנוקמים",
            "poster_url": "https://m.media-amazon.com/images/M/MV5BNDYxNjQyMjAtNTdlNC00YzM4LTg4OnItMDEzYzE5NzZhZWExXkEyXkFqcGdeQXVyNjU0OTQ0OTY@._V1_SX300.jpg",
            "video_url": "#",
            "category": "היקום הקולנועי של מארוול"
         },
        # Add more dummy movies or leave empty
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


def get_greeting():
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
    # Generic greeting as there's no user login
    return f"{greeting_text} אורח"


@app.route('/')
def index():
    greeting = get_greeting()
    movies_data = load_movies_data() # Load placeholder/dummy data
    categories = categorize_movies(movies_data) # Categorize the loaded data
    current_year = datetime.datetime.utcnow().year
    # No current_user or google_client_id needed
    return render_template('index.html',
                           greeting=greeting,
                           categories=categories,
                           current_year=current_year)

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
    app.run(host='0.0.0.0', debug=True)
