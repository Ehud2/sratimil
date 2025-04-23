import datetime
# import os # No longer needed for environment variables
from flask import Flask, render_template, session, redirect, url_for, flash
from authlib.integrations.flask_client import OAuth
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Configuration (HARDCODED SECRETS - INSECURE!) ---
# WARNING: Hardcoding secrets is insecure! Use environment variables in real applications.
app.config['SECRET_KEY'] = 'moviesilsuperdupersecretkey' # <-- REPLACE with a strong, random key
app.config['GOOGLE_CLIENT_ID'] = '657393464441-iaq7khpbqlr7iksaf8oua7l431noljd1.apps.googleusercontent.com'
app.config['GOOGLE_CLIENT_SECRET'] = 'YOUR_GOOGLE_CLIENT_SECRET_HERE' # <-- REPLACE with your actual Client Secret

# Basic check if secrets seem to be placeholders (improve as needed)
if 'YOUR_GOOGLE_CLIENT_SECRET_HERE' in app.config['GOOGLE_CLIENT_SECRET'] or \
   'a_very_strong_random_secret_key_here' in app.config['SECRET_KEY']:
    print("WARNING: Default placeholder secrets detected in main.py. Replace them!")
    # You might want to raise an error in a real scenario if placeholders are used
    # raise ValueError("Placeholder secrets detected. Please replace them in main.py")


# --- Authlib Initialization ---
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    access_token_url='https://accounts.google.com/o/oauth2/token',
    access_token_params=None,
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
    client_kwargs={'scope': 'openid email profile'},
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration'
)

# --- Flask-Login Initialization ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'index'

# --- Simple User Model (In-Memory - Replace with Database) ---
users = {}

class User(UserMixin):
    def __init__(self, id, name, email, picture):
        self.id = id
        self.name = name
        self.email = email
        self.picture = picture

@login_manager.user_loader
def load_user(user_id):
    return users.get(user_id)

# --- Dummy Content ---
dummy_content = {
    # ... (your dummy_content dictionary remains unchanged) ...
    "הסרטים הנצפים ביותר השבוע": [
        {"id": 101, "title": "שומר הזמן", "poster": "https://placehold.co/240x360/0D1B2A/E0E1DD?text=Movie+1"},
        # ... other movies
    ],
    "הסדרות הנצפות ביותר השבוע": [
        {"id": 201, "title": "כתר הזהב", "poster": "https://placehold.co/240x360/f72585/ffffff?text=Series+1"},
         # ... other series
    ],
    # --- שאר הקטגוריות ללא שינוי ---
    "היקום הקולנועי של מארוול": [
        {"id": 301, "title": "איירון מן", "poster": "https://placehold.co/240x360/B71C1C/ffffff?text=Iron+Man"},
        # ... other marvel items
    ],
    "מלחמת הכוכבים": [
        {"id": 401, "title": "תקווה חדשה", "poster": "https://placehold.co/240x360/FBC02D/000000?text=A+New+Hope"},
        # ... other star wars items
    ],
    "אקס-מן": [
        {"id": 501, "title": "אקס-מן", "poster": "https://placehold.co/240x360/FF6F00/ffffff?text=X-Men"},
        # ... other x-men items
    ],
    "ספיידרמן": [
        {"id": 601, "title": "ספיידרמן", "poster": "https://placehold.co/240x360/D32F2F/ffffff?text=Spider-Man"},
        # ... other spiderman items
    ]
}


def get_greeting():
    """מחזיר ברכה בהתאם לשעה ביום"""
    name_part = f" {current_user.name}" if current_user.is_authenticated and hasattr(current_user, 'name') else ""

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
    return f"{greeting_text}{name_part}"


@app.route('/')
def index():
    """הנתיב הראשי, מרנדר את דף הבית"""
    greeting = get_greeting()
    categories = dummy_content
    current_year = datetime.datetime.utcnow().year
    return render_template('index.html',
                           greeting=greeting,
                           categories=categories,
                           current_year=current_year)

# --- Authentication Routes ---

@app.route('/login/google')
def login_google():
    """Redirects to Google's authorization page."""
    # Use https for the redirect_uri when deployed
    redirect_uri = url_for('authorize_google', _external=True, _scheme='https')
    print(f"DEBUG: Redirect URI for Google: {redirect_uri}") # Keep for debugging
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize/google')
def authorize_google():
    """Callback route for Google OAuth."""
    try:
        token = google.authorize_access_token()
        if not token:
            flash('Access denied by Google or invalid token.', 'error')
            return redirect(url_for('index'))

        resp = google.get('userinfo')
        resp.raise_for_status()
        user_info = resp.json()

        google_user_id = user_info['sub']
        user_email = user_info.get('email')
        user_name = user_info.get('name')
        user_picture = user_info.get('picture')

        user = users.get(google_user_id)
        if user is None:
            user = User(id=google_user_id, name=user_name, email=user_email, picture=user_picture)
            users[google_user_id] = user

        login_user(user, remember=True)
        session['profile_pic'] = user_picture

        flash(f'ברוך הבא, {user_name}!', 'success')
        return redirect(url_for('index'))

    except Exception as e:
        print(f"ERROR: Exception during Google OAuth callback: {e}") # Log error
        flash('An error occurred during authentication. Please try again.', 'error')
        return redirect(url_for('index'))


@app.route('/logout')
def logout():
    """Logs the user out."""
    logout_user()
    session.pop('profile_pic', None)
    flash('יצאת בהצלחה מהמערכת.', 'info')
    return redirect(url_for('index'))

# --- Error Handling (Optional but Recommended) ---
@app.errorhandler(404)
def page_not_found(e):
    # Create a templates/404.html file
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    # Log the error e in a real app
    print(f"SERVER ERROR: {e}")
    # Create a templates/500.html file
    return render_template('500.html'), 500

# --- No if __name__ == '__main__' block needed for Gunicorn ---
