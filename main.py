import datetime
import os
from flask import Flask, render_template, session, redirect, url_for, flash # Added session, redirect, url_for, flash
from authlib.integrations.flask_client import OAuth # Added OAuth
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user # Added Flask-Login components

# --- Environment Variable Loading (Optional for local dev) ---
# Load .env file if it exists (useful for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass # python-dotenv not installed or not needed

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Configuration ---
# Load SECRET_KEY from environment variable - CRITICAL for sessions
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-fallback-secret-key-for-dev')
if app.config['SECRET_KEY'] == 'default-fallback-secret-key-for-dev' and app.env == 'production':
    raise ValueError("SECRET_KEY must be set in production environment!")

# Load Google OAuth Credentials from environment variables
app.config['GOOGLE_CLIENT_ID'] = "657393464441-iaq7khpbqlr7iksaf8oua7l431noljd1.apps.googleusercontent.com"
app.config['GOOGLE_CLIENT_SECRET'] = "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"

if not app.config['GOOGLE_CLIENT_ID'] or not app.config['GOOGLE_CLIENT_SECRET']:
    raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set!")

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
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',  # Use this for OpenID Connect userinfo
    client_kwargs={'scope': 'openid email profile'}, # Request basic user info
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration' # Optional: For auto-discovery
)

# --- Flask-Login Initialization ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'index' # Redirect to index if login is required but user is not logged in

# --- Simple User Model (Replace with Database in Real App) ---
# NOTE: This is a basic in-memory store for demonstration.
# In a real application, use a database (SQLAlchemy, etc.) to store users.
users = {}

class User(UserMixin):
    def __init__(self, id, name, email, picture):
        self.id = id
        self.name = name
        self.email = email
        self.picture = picture

@login_manager.user_loader
def load_user(user_id):
    """Flask-Login user loader callback."""
    return users.get(user_id) # Get user from our in-memory store

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
        {"id": 302, "title": "הנוקמים", "poster": "https://placehold.co/240x360/1A237E/ffffff?text=Avengers"},
        {"id": 303, "title": "שומרי הגלקסיה", "poster": "https://placehold.co/240x360/880E4F/ffffff?text=Guardians"},
        {"id": 304, "title": "הפנתר השחור", "poster": "https://placehold.co/240x360/1B5E20/ffffff?text=Black+Panther"},
    ],
    "מלחמת הכוכבים": [
        {"id": 401, "title": "תקווה חדשה", "poster": "https://placehold.co/240x360/FBC02D/000000?text=A+New+Hope"},
        {"id": 402, "title": "האימפריה מכה שנית", "poster": "https://placehold.co/240x360/0D47A1/ffffff?text=Empire+Strikes"},
        {"id": 403, "title": "שובו של הג'דיי", "poster": "https://placehold.co/240x360/2E7D32/ffffff?text=Return+of+Jedi"},
        {"id": 404, "title": "המנדלוריאן", "poster": "https://placehold.co/240x360/4E342E/ffffff?text=Mandalorian"},
    ],
    "אקס-מן": [
        {"id": 501, "title": "אקס-מן", "poster": "https://placehold.co/240x360/FF6F00/ffffff?text=X-Men"},
        {"id": 502, "title": "אקס-מן 2", "poster": "https://placehold.co/240x360/BF360C/ffffff?text=X2"},
        {"id": 503, "title": "לוגאן", "poster": "https://placehold.co/240x360/37474F/ffffff?text=Logan"},
    ],
    "ספיידרמן": [
        {"id": 601, "title": "ספיידרמן", "poster": "https://placehold.co/240x360/D32F2F/ffffff?text=Spider-Man"},
        {"id": 602, "title": "ספיידרמן: השיבה הביתה", "poster": "https://placehold.co/240x360/1976D2/ffffff?text=Homecoming"},
        {"id": 603, "title": "ספיידרמן: ברחבי ממדי העכביש", "poster": "https://placehold.co/240x360/512DA8/ffffff?text=Spider-Verse"},
    ]
}


def get_greeting():
    """מחזיר ברכה בהתאם לשעה ביום"""
    # User-specific greeting if logged in?
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
    return f"{greeting_text}{name_part}" # Add name if available


@app.route('/')
def index():
    """הנתיב הראשי, מרנדר את דף הבית"""
    greeting = get_greeting()
    categories = dummy_content
    current_year = datetime.datetime.utcnow().year
    # current_user is automatically available in templates if Flask-Login is set up
    return render_template('index.html',
                           greeting=greeting,
                           categories=categories,
                           current_year=current_year)

# --- Authentication Routes ---

@app.route('/login/google')
def login_google():
    """Redirects to Google's authorization page."""
    # Construct the redirect URI dynamically
    # For production, ensure FLASK_APP_URL is set or detect scheme/host
    # For Render, it should handle HTTPS automatically
    redirect_uri = url_for('authorize_google', _external=True, _scheme='https') # Force https for redirect_uri
    print(f"Redirect URI for Google: {redirect_uri}") # Debugging: Print the redirect URI
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize/google')
def authorize_google():
    """Callback route for Google OAuth."""
    try:
        token = google.authorize_access_token()
        if not token:
            flash('Access denied by Google or invalid token.', 'error')
            return redirect(url_for('index'))

        # Fetch user info using the token
        resp = google.get('userinfo')
        resp.raise_for_status() # Raise an exception for bad status codes
        user_info = resp.json()

        # Get user details from Google profile
        google_user_id = user_info['sub'] # 'sub' is the standard unique ID in OpenID Connect
        user_email = user_info.get('email')
        user_name = user_info.get('name')
        user_picture = user_info.get('picture')

        # Find or create the user in our "database" (in-memory dict)
        user = users.get(google_user_id)
        if user is None:
            user = User(id=google_user_id, name=user_name, email=user_email, picture=user_picture)
            users[google_user_id] = user # Add new user to our store

        # Log the user in using Flask-Login
        login_user(user, remember=True) # remember=True keeps user logged in across sessions

        # Optional: Store additional info in session if needed frequently
        # session['google_token'] = token # Maybe store token if you need to make further API calls
        session['profile_pic'] = user_picture # Store picture for easy access if current_user isn't available everywhere

        flash(f'ברוך הבא, {user_name}!', 'success')
        return redirect(url_for('index'))

    except Exception as e:
        # Log the error properly in a real app
        print(f"Error during Google OAuth callback: {e}")
        flash('An error occurred during authentication. Please try again.', 'error')
        return redirect(url_for('index'))


@app.route('/logout')
def logout():
    """Logs the user out."""
    # Optional: Revoke Google token if you stored it and want to ensure full logout
    # token = session.get('google_token')
    # if token:
    #    google.post('https://accounts.google.com/o/oauth2/revoke', params={'token': token['access_token']})

    logout_user() # Clears the user session using Flask-Login
    session.pop('profile_pic', None) # Remove specific session data
    # session.clear() # Or clear the entire session if preferred
    flash('יצאת בהצלחה מהמערכת.', 'info')
    return redirect(url_for('index'))

# --- Error Handling (Optional but Recommended) ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404 # Assumes you have a 404.html template

@app.errorhandler(500)
def internal_server_error(e):
    # Log the error e
    return render_template('500.html'), 500 # Assumes you have a 500.html template
