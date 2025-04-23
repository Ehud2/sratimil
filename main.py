import datetime
import traceback
from flask import Flask, render_template, session, redirect, url_for, flash
from authlib.integrations.flask_client import OAuth
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user

app = Flask(__name__)

app.config['SECRET_KEY'] = 'moviesilsuperdupersecretkey'
app.config['GOOGLE_CLIENT_ID'] = '657393464441-iaq7khpbqlr7iksaf8oua7l431noljd1.apps.googleusercontent.com'
app.config['GOOGLE_CLIENT_SECRET'] = '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'

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
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    oidc_claims_options={
        "iss": {
            "essential": True,
            "value": "https://accounts.google.com"
        }
    }
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'index'

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

dummy_content = {
    "הסרטים הנצפים ביותר השבוע": [
        {"id": 101, "title": "שומר הזמן", "poster": "https://placehold.co/240x360/0D1B2A/E0E1DD?text=Movie+1"},
        {"id": 102, "title": "קוד צללים", "poster": "https://placehold.co/240x360/1B263B/E0E1DD?text=Movie+2"},
        {"id": 103, "title": "ברית הפלדה", "poster": "https://placehold.co/240x360/415A77/E0E1DD?text=Movie+3"},
        {"id": 104, "title": "נהר האזמרגד", "poster": "https://placehold.co/240x360/778DA9/0D1B2A?text=Movie+4"},
        {"id": 105, "title": "הבריחה הגדולה", "poster": "https://placehold.co/240x360/E0E1DD/0D1B2A?text=Movie+5"},
        {"id": 106, "title": "רודפי האור", "poster": "https://placehold.co/240x360/3D405B/F4F1DE?text=Movie+6"},
        {"id": 107, "title": "מעבר לכוכבים", "poster": "https://placehold.co/240x360/81B29A/3D405B?text=Movie+7"},
        {"id": 108, "title": "ציידי המטמון", "poster": "https://placehold.co/240x360/F2CC8F/3D405B?text=Movie+8"},
        {"id": 109, "title": "לב הדרקון", "poster": "https://placehold.co/240x360/E07A5F/F4F1DE?text=Movie+9"},
        {"id": 110, "title": "ממלכת הקרח", "poster": "https://placehold.co/240x360/F4F1DE/3D405B?text=Movie+10"},
        {"id": 111, "title": "המרדף האחרון", "poster": "https://placehold.co/240x360/9A8C98/22223B?text=Movie+11"},
        {"id": 112, "title": "עיר החלומות", "poster": "https://placehold.co/240x360/C9ADA7/22223B?text=Movie+12"},
        {"id": 113, "title": "רוח המדבר", "poster": "https://placehold.co/240x360/F2E9E4/4A4E69?text=Movie+13"},
        {"id": 114, "title": "סודות הים", "poster": "https://placehold.co/240x360/4A4E69/F2E9E4?text=Movie+14"},
        {"id": 115, "title": "הרפתקה בזמן", "poster": "https://placehold.co/240x360/22223B/F2E9E4?text=Movie+15"},
    ],
    "הסדרות הנצפות ביותר השבוע": [
        {"id": 201, "title": "כתר הזהב", "poster": "https://placehold.co/240x360/f72585/ffffff?text=Series+1"},
        {"id": 202, "title": "שושלת הברזל", "poster": "https://placehold.co/240x360/b5179e/ffffff?text=Series+2"},
        {"id": 203, "title": "תעלומת העמק", "poster": "https://placehold.co/240x360/7209b7/ffffff?text=Series+3"},
        {"id": 204, "title": "סוכני העתיד", "poster": "https://placehold.co/240x360/560bad/ffffff?text=Series+4"},
        {"id": 205, "title": "כרוניקות הנווד", "poster": "https://placehold.co/240x360/480ca8/ffffff?text=Series+5"},
        {"id": 206, "title": "משחקי הכספים", "poster": "https://placehold.co/240x360/3a0ca3/ffffff?text=Series+6"},
        {"id": 207, "title": "קו האופק", "poster": "https://placehold.co/240x360/3f37c9/ffffff?text=Series+7"},
        {"id": 208, "title": "האי הנעלם", "poster": "https://placehold.co/240x360/4361ee/ffffff?text=Series+8"},
        {"id": 209, "title": "צופן הגורל", "poster": "https://placehold.co/240x360/4895ef/ffffff?text=Series+9"},
        {"id": 210, "title": "מרדפי לילה", "poster": "https://placehold.co/240x360/4cc9f0/000000?text=Series+10"},
        {"id": 211, "title": "שומרי היער", "poster": "https://placehold.co/240x360/007f5f/ffffff?text=Series+11"},
        {"id": 212, "title": "קשרי דם", "poster": "https://placehold.co/240x360/2b9348/ffffff?text=Series+12"},
        {"id": 213, "title": "תחנת החלל", "poster": "https://placehold.co/240x360/55a630/ffffff?text=Series+13"},
        {"id": 214, "title": "הרשת האפלה", "poster": "https://placehold.co/240x360/80b918/000000?text=Series+14"},
        {"id": 215, "title": "ברית הצללים", "poster": "https://placehold.co/240x360/aacc00/000000?text=Series+15"},
    ],
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
    greeting = get_greeting()
    categories = dummy_content
    current_year = datetime.datetime.utcnow().year
    return render_template('index.html',
                           greeting=greeting,
                           categories=categories,
                           current_year=current_year)

@app.route('/login/google')
def login_google():
    redirect_uri = "https://test-web-nph8.onrender.com/authorize/google"
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize/google')
def authorize_google():
    
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
        tb_str = traceback.format_exc()
        print(f"ERROR: Exception during Google OAuth callback:\n{tb_str}")
        flash('An error occurred during authentication. Please try again.', 'error')
        return redirect(url_for('index'))


@app.route('/logout')
def logout():
    logout_user()
    session.pop('profile_pic', None)
    flash('יצאת בהצלחה מהמערכת.', 'info')
    return redirect(url_for('index'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    print(f"SERVER ERROR: {e}")
    tb_str = traceback.format_exc()
    print(f"SERVER ERROR TRACEBACK:\n{tb_str}")
    return render_template('500.html'), 500
