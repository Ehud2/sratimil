import datetime
import traceback
import os
import requests
import json
import re
import urllib.parse # Import urllib for URL encoding/decoding
from flask import Flask, render_template, session, redirect, url_for, flash, request, abort, jsonify
from authlib.integrations.flask_client import OAuth
import firebase_admin
from firebase_admin import credentials, db
import logging
from flask_socketio import SocketIO, emit, join_room, leave_room # Import SocketIO components

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

# List of admin emails
ADMIN_EMAILS = ['ehudverbin@gmail.com', 'guykresco@gmail.com']

# Configure SocketIO
# async_mode can be 'eventlet', 'gevent', 'threading'. 'threading' is simplest for basic apps.
# Use message_queue for horizontal scaling if needed.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading') # Allow connections from any origin (adjust as needed)

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
    if not firebase_admin._apps:
        if not os.path.exists(FIREBASE_SERVICE_ACCOUNT_KEY_PATH):
            logging.error(f"Firebase service account key file not found at {FIREBASE_SERVICE_ACCOUNT_KEY_PATH}")
            cred = None
        else:
            cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_KEY_PATH)

        if cred:
            firebase_admin.initialize_app(cred, {
                'databaseURL': FIREBASE_DATABASE_URL
            })
            logging.info("Firebase initialized successfully.")
        else:
             logging.error("Firebase initialization failed due to missing credential file.")
    else:
        logging.info("Firebase already initialized.")
except Exception as e:
    logging.error(f"Error initializing Firebase: {e}", exc_info=True)


# --- SocketIO User Mapping ---
# A simple in-memory mapping of user email to SocketIO session ID (sid)
# This is basic and won't scale across multiple server processes without a message queue.
# Assumes one tab per user or the last connected tab wins.
email_to_sid = {}
sid_to_email = {}

@socketio.on('connect')
def handle_connect():
    user = session.get('user')
    if user and user.get('email'):
        email = user['email']
        sid = request.sid
        logging.info(f"Socket connected: {sid}, User: {email}")
        # Store the mapping. Overwrite if user connects from a new tab/device.
        email_to_sid[email] = sid
        sid_to_email[sid] = email
    else:
        logging.info(f"Socket connected: {request.sid}, User: Anonymous")
        # Anonymous users won't be mapped

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    email = sid_to_email.pop(sid, None) # Remove from sid_to_email
    if email:
        # Check if this sid was the *currently* mapped sid for this email
        if email_to_sid.get(email) == sid:
            # If so, remove the email mapping. Otherwise, it means the user
            # connected from a new tab and already overwrote the mapping.
            del email_to_sid[email]
            logging.info(f"Socket disconnected: {sid}, User: {email}")
        else:
             logging.info(f"Socket disconnected: {sid}, User: {email} (was already replaced by new connection)")
    else:
        logging.info(f"Socket disconnected: {sid}, User: Anonymous")


# --- SocketIO Invitation Events ---
@socketio.on('send_invitation')
def handle_send_invitation(data):
    sender_email = sid_to_email.get(request.sid)
    if not sender_email:
        emit('invitation_error', {'message': 'אתה לא מחובר כדי לשלוח הזמנה.'})
        logging.warning("Attempted to send invitation without logged-in user.")
        return

    receiver_email = data.get('receiver_email')
    movie_imdb_id = data.get('movie_imdb_id')
    movie_title = data.get('movie_title')

    if not receiver_email or not movie_imdb_id or not movie_title:
        emit('invitation_error', {'message': 'חסרים פרטי הזמנה (מקבל, סרט).'})
        logging.warning(f"Missing invitation details from sender {sender_email}: {data}")
        return

    if sender_email == receiver_email:
         emit('invitation_error', {'message': 'אי אפשר להזמין את עצמך.'})
         logging.warning(f"Sender {sender_email} attempted to invite themselves.")
         return

    logging.info(f"User {sender_email} sending invitation for movie {movie_imdb_id} to {receiver_email}")

    # Check if the receiver is currently online and connected via SocketIO
    receiver_sid = email_to_sid.get(receiver_email)

    # Save invitation to Firebase (optional, but good for persistence/history)
    # Use a unique key based on sender, receiver, movie for easy lookup/update
    invitation_key = f"{sender_email.replace('.', '_')}_{receiver_email.replace('.', '_')}_{movie_imdb_id}"
    invitation_ref = db.reference(f'/invitations/{invitation_key}')

    # Check if a pending invitation already exists
    existing_invitation = invitation_ref.get()
    if existing_invitation and existing_invitation.get('status') == 'pending':
         emit('invitation_error', {'message': 'הזמנה למשתמש זה כבר נשלחה עבור הסרט הזה וממתינה לתשובה.'})
         logging.info(f"Duplicate pending invitation detected from {sender_email} to {receiver_email} for {movie_imdb_id}")
         return # Don't send again if pending

    invitation_data = {
        'sender_email': sender_email,
        'receiver_email': receiver_email,
        'movie_imdb_id': movie_imdb_id,
        'movie_title': movie_title,
        'status': 'pending',
        'timestamp': datetime.datetime.utcnow().isoformat()
    }

    try:
        invitation_ref.set(invitation_data)
        logging.info(f"Invitation saved to Firebase: {invitation_key}")

        if receiver_sid:
            logging.info(f"Receiver {receiver_email} is online, emitting new_invitation event to sid {receiver_sid}")
            # Emit event to the specific receiver's socket
            emit('new_invitation', invitation_data, room=receiver_sid)
            emit('invitation_sent_success', {'message': f'הזמנה נשלחה בהצלחה למשתמש {receiver_email}.', 'receiver_online': True})
        else:
            logging.info(f"Receiver {receiver_email} is offline. Invitation saved to Firebase.")
            emit('invitation_sent_success', {'message': f'הזמנה נשמרה עבור {receiver_email}. הוא יראה אותה כשיתחבר.', 'receiver_online': False}) # Message indicates offline
            # Note: For offline users to see invites later, the client on index/other pages
            # would need to check Firebase for pending invites on load. This is not implemented here.


    except Exception as e:
        logging.error(f"Error saving invitation {invitation_key} to Firebase: {e}", exc_info=True)
        emit('invitation_error', {'message': 'אירעה שגיאה בשליחת ההזמנה.'})


@socketio.on('accept_invitation')
def handle_accept_invitation(data):
    receiver_email = sid_to_email.get(request.sid)
    if not receiver_email:
        emit('invitation_error', {'message': 'שגיאה בקבלת ההזמנה: אימייל משתמש לא נמצא.'})
        logging.warning("Attempted to accept invitation without logged-in user.")
        return

    sender_email = data.get('sender_email')
    movie_imdb_id = data.get('movie_imdb_id')

    if not sender_email or not movie_imdb_id:
        emit('invitation_error', {'message': 'שגיאה בקבלת ההזמנה: חסרים פרטים.'})
        logging.warning(f"Missing details when accepting invitation by {receiver_email}: {data}")
        return

    logging.info(f"User {receiver_email} accepting invitation from {sender_email} for movie {movie_imdb_id}")

    invitation_key = f"{sender_email.replace('.', '_')}_{receiver_email.replace('.', '_')}_{movie_imdb_id}"
    invitation_ref = db.reference(f'/invitations/{invitation_key}')

    try:
        invitation_data = invitation_ref.get()

        if not invitation_data or invitation_data.get('status') != 'pending':
            emit('invitation_error', {'message': 'הזמנה לא נמצאה או כבר טופלה.'})
            logging.warning(f"Invitation not found or not pending for key {invitation_key} when {receiver_email} tried to accept.")
            return

        # Update status in Firebase
        invitation_ref.update({'status': 'accepted', 'accepted_timestamp': datetime.datetime.utcnow().isoformat()})
        logging.info(f"Invitation {invitation_key} status updated to 'accepted'.")

        # Get sender's current sid
        sender_sid = email_to_sid.get(sender_email)

        # Encode emails for URL
        encoded_sender_email = urllib.parse.quote_plus(sender_email)
        encoded_receiver_email = urllib.parse.quote_plus(receiver_email)
        watch_together_url = url_for('watch_together',
                                     imdb_id=movie_imdb_id,
                                     sender_email=encoded_sender_email,
                                     receiver_email=encoded_receiver_email)

        # Emit event to both sender and receiver to redirect them
        if sender_sid:
            emit('invitation_accepted', {'url': watch_together_url}, room=sender_sid)
            logging.info(f"Emitted invitation_accepted to sender {sender_email} (sid {sender_sid})")
        else:
             logging.warning(f"Sender {sender_email} is offline, cannot emit invitation_accepted for key {invitation_key}.")
             # Maybe flash a message on next login for the sender? Out of scope for this request.

        # Emit to the receiver who just accepted (using their current sid)
        emit('invitation_accepted', {'url': watch_together_url}, room=request.sid)
        logging.info(f"Emitted invitation_accepted to receiver {receiver_email} (sid {request.sid})")

    except Exception as e:
        logging.error(f"Error accepting invitation {invitation_key}: {e}", exc_info=True)
        emit('invitation_error', {'message': 'אירעה שגיאה בקבלת ההזמנה.'})


@socketio.on('decline_invitation')
def handle_decline_invitation(data):
    receiver_email = sid_to_email.get(request.sid)
    if not receiver_email:
        emit('invitation_error', {'message': 'שגיאה בדחיית ההזמנה: אימייל משתמש לא נמצא.'})
        logging.warning("Attempted to decline invitation without logged-in user.")
        return

    sender_email = data.get('sender_email')
    movie_imdb_id = data.get('movie_imdb_id')

    if not sender_email or not movie_imdb_id:
        emit('invitation_error', {'message': 'שגיאה בדחיית ההזמנה: חסרים פרטים.'})
        logging.warning(f"Missing details when declining invitation by {receiver_email}: {data}")
        return

    logging.info(f"User {receiver_email} declining invitation from {sender_email} for movie {movie_imdb_id}")

    invitation_key = f"{sender_email.replace('.', '_')}_{receiver_email.replace('.', '_')}_{movie_imdb_id}"
    invitation_ref = db.reference(f'/invitations/{invitation_key}')

    try:
        invitation_data = invitation_ref.get()

        if not invitation_data or invitation_data.get('status') != 'pending':
            # Could be already accepted/declined or not found. Just log.
            logging.warning(f"Invitation not found or not pending for key {invitation_key} when {receiver_email} tried to decline.")
            # Don't emit an error to the receiver, just let them dismiss the UI
            return

        # Update status in Firebase
        invitation_ref.update({'status': 'declined', 'declined_timestamp': datetime.datetime.utcnow().isoformat()})
        logging.info(f"Invitation {invitation_key} status updated to 'declined'.")

        # Notify the sender that the invitation was declined
        sender_sid = email_to_sid.get(sender_email)
        if sender_sid:
            emit('invitation_declined', {'receiver_email': receiver_email, 'movie_title': invitation_data.get('movie_title', 'הסרט')}, room=sender_sid)
            logging.info(f"Emitted invitation_declined to sender {sender_email} (sid {sender_sid})")
        else:
             logging.warning(f"Sender {sender_email} is offline, cannot notify of declined invitation {invitation_key}.")

    except Exception as e:
        logging.error(f"Error declining invitation {invitation_key}: {e}", exc_info=True)
        # Could emit an error back to the receiver if crucial, but dismissing the UI is usually enough.


# --- SocketIO Playback Control Events (PLACEHOLDERS - Will NOT CONTROL Vidsrc) ---
# These events are sent *between* the sender and receiver via the server,
# but the client-side JS *cannot* apply them to the Vidsrc iframe.
# This is included structure-wise but functionally limited by the video source.

@socketio.on('playback_command')
def handle_playback_command(data):
    sender_email = sid_to_email.get(request.sid)
    # Data should include movie_imdb_id, receiver_email, command (e.g., 'play', 'pause', 'seek'), value (e.g., timestamp)
    receiver_email = data.get('receiver_email')
    movie_imdb_id = data.get('movie_imdb_id')
    command = data.get('command')
    value = data.get('value') # e.g., time in seconds for 'seek'

    if not sender_email or not receiver_email or not movie_imdb_id or not command:
         logging.warning(f"Incomplete playback command received from {sender_email}: {data}")
         return

    # Ensure the sender is actually the designated sender for this watch session
    # This requires checking the active watch session state.
    # A simple way for *this specific request* is to assume the sender in the URL
    # of watch-together.html is the controller and they are emitting this.
    # A more robust way would involve managing 'rooms' in SocketIO for each session.
    # For this example, we'll just check if the sender matches the expected sender email in the event data.
    # This is still not fully secure or robust for multiple sessions.

    # Check if the receiver is online
    receiver_sid = email_to_sid.get(receiver_email)

    if receiver_sid:
        logging.info(f"Forwarding playback command '{command}' ({value}) for {movie_imdb_id} from {sender_email} to {receiver_email} (sid {receiver_sid})")
        # Emit the command to the specific receiver's socket
        emit('receive_playback_command', {'command': command, 'value': value}, room=receiver_sid)
    else:
        logging.warning(f"Attempted to forward playback command '{command}' to offline receiver {receiver_email}.")
        # Could emit a message back to the sender saying the receiver is offline.


# --- Categories ---
CATEGORIES = [
    "הסרטים הנצפים ביותר השבוע",
    "הסדרות הנצפים ביותר השבוע",
    "היקום הקולנועי של מארוול",
    "DC",
    "מלחמת הכוכבים",
    "הארי פוטר",
    "המסור",
    "הפארק הדרומי",
    "מהירים ועצבניים",
    "אקס-מן",
    "ספיידרמן",
    "ללא"
]

# --- Data Loading Functions (from Firebase) ---

def load_movies_data():
    """Loads all movies from Firebase, adding 'type: movie'."""
    try:
        ref = db.reference('/Movies')
        movies = ref.get()
        movies_with_type = {}
        if movies:
            for imdb_id, details in movies.items():
                if isinstance(details, dict):
                    details['type'] = 'movie' # Add type identifier
                    movies_with_type[imdb_id] = details
                else:
                    logging.warning(f"Skipping non-dict movie entry: {imdb_id}")

        logging.info(f"Loaded {len(movies_with_type)} movies from Firebase.")
        return movies_with_type if movies_with_type is not None else {} # Ensure returns dict even if empty
    except Exception as e:
        logging.error(f"Error loading movies from Firebase: {e}", exc_info=True)
        return {}

def load_series_data_for_index():
    """Loads series data for index display from Firebase, adding 'type: series'."""
    try:
        # We only need top-level series info for the index card
        ref = db.reference('/Series')
        series_dict = ref.get()
        series_for_index = {}
        if series_dict:
            for imdb_id, details in series_dict.items():
                 # Only include basic details for the index card
                 # Avoid fetching nested Seasons/Episodes here for performance
                if isinstance(details, dict):
                    series_for_index[imdb_id] = {
                        'imdbID': imdb_id,
                        'title': details.get('title', 'כותרת לא ידועה'),
                        'poster': details.get('poster', 'N/A'),
                        'category': details.get('category', 'ללא'),
                        'type': 'series' # Add type identifier
                    }
                else:
                    logging.warning(f"Skipping non-dict series entry: {imdb_id}")

        logging.info(f"Loaded {len(series_for_index)} series for index from Firebase.")
        return series_for_index if series_for_index is not None else {}
    except Exception as e:
        logging.error(f"Error loading series for index from Firebase: {e}", exc_info=True)
        return {}


def load_series_list_for_add_page():
    """Loads basic series info for the add page dropdown."""
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


def load_full_series_details(imdb_id):
    """Loads all details for a single series, including seasons and episodes."""
    try:
        ref = db.reference(f'/Series/{imdb_id}')
        series_details = ref.get()
        if series_details:
             logging.info(f"Loaded full series details for ID {imdb_id} from Firebase.")
        else:
             logging.info(f"No full details found for series ID {imdb_id} in Firebase.")
        return series_details
    except Exception as e:
        logging.error(f"Error loading full series details for ID {imdb_id}: {e}", exc_info=True)
        return None


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
        logging.error(f"Error loading movie details for ID {imdb_id}: {e}", exc_info=True)
        return None


def categorize_content(movies_data, series_data):
    """Categorizes movies and series loaded from Firebase for index display."""
    categorized_items = {}
    # Initialize categories excluding "ללא" as it's typically not a display category
    for cat in CATEGORIES:
        if cat != "ללא":
            categorized_items[cat] = []

    all_items = {}
    if movies_data:
        all_items.update(movies_data)
    if series_data:
        all_items.update(series_data)

    if not all_items:
        logging.info("No movies or series data to categorize.")
        # Ensure all display categories exist, even if empty
        return {cat: [] for cat in CATEGORIES if cat != "ללא"}


    # all_items now contains {imdb_id: {details_including_type}}
    for imdb_id, item_details in all_items.items():
        if not isinstance(item_details, dict):
            logging.warning(f"Skipping non-dict entry in all_items: {imdb_id}")
            continue

        # Ensure required fields exist, provide defaults
        title = item_details.get('title', 'כותרת לא ידועה')
        poster = item_details.get('poster', 'N/A')
        category = item_details.get('category', 'ללא') # Default to 'ללא'
        item_type = item_details.get('type') # Get the type ('movie' or 'series')

        # Add to the correct category list if category is valid and not "ללא"
        # We need id, title, poster, and type for the item cards on index.html
        if category in CATEGORIES and category != "ללא" and item_type in ['movie', 'series']:
             categorized_items[category].append({
                "id": imdb_id,
                "title": title,
                "poster": poster,
                "type": item_type # Include type here
             })
        elif category == "ללא":
            pass # Don't display 'ללא' category on index
        else:
             logging.warning(f"Item {imdb_id} ('{title}') has invalid/unknown category '{category}' or type '{item_type}'. Skipping index display.")
             pass # Skip invalid categories or types

    # Optional: If you want to ensure categories with no items are still shown,
    # you might add checks here. But usually, you only show categories with items.
    # return {cat: items for cat, items in categorized_items.items() if items} # Only return categories with items

    return categorized_items


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
            # OMDB search results list includes Title, Year, imdbID, Type, Poster
            # Filter out anything that isn't the requested type just in case (OMDB search can be fuzzy)
            filtered_results = [item for item in data.get('Search', []) if item.get('Type', '').lower() == content_type]
            return filtered_results
        else:
             logging.info(f"OMDB search found no results for '{search_term}' type '{content_type}': {data.get('Error', 'Unknown error')}")
             return []
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling OMDB search API: {e}", exc_info=True)
        return []

def get_omdb_details_api(imdb_id, season=None, episode=None):
    """
    Gets full details for a specific IMDb ID from OMDB.
    If season and episode are provided, gets details for a specific episode of a series.
    If only season is provided (and imdb_id is series ID), gets details for that season (list of episodes).
    """
    if not OMDB_API_KEY or OMDB_API_KEY == 'YOUR_OMDB_API_KEY':
         logging.warning("OMDB_API_KEY is not set.")
         return None
    params = {
        'apikey': OMDB_API_KEY,
        'i': imdb_id, # This is the SERIES IMDb ID when getting episode/season details
    }
    if season is not None:
        params['Season'] = season
    if episode is not None:
        params['Episode'] = episode
    else: # If only season is requested (or neither), request full plot for main details
         params['plot'] = 'full'


    try:
        response = requests.get(OMDB_BASE_URL, params=params, timeout=10)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        if data.get('Response') == 'True':
            # Check if it's a specific episode request and if the response matches
            if season is not None and episode is not None:
                 # OMDB episode response *should* contain 'SeriesID' matching the requested 'i'
                 # and 'Season'/'Episode' matching the request. It also has its own 'imdbID' and 'Title'.
                 if data.get('SeriesID') == imdb_id and data.get('Season') == str(season) and data.get('Episode') == str(episode):
                      logging.info(f"Successfully fetched OMDB episode details for Series ID '{imdb_id}', S{season}E{episode}. Episode IMDb ID: {data.get('imdbID', 'N/A')}")
                      return data
                 else:
                      # This might happen if the season/episode doesn't exist for the series
                      logging.warning(f"OMDB episode details response does not match request for Series ID '{imdb_id}', S{season}E{episode}. Response: {data}")
                      # Return an empty-like response or None to signal not found/match
                      return {'Response': 'False', 'Error': 'Episode data mismatch or not found'} # Return a dict with Response=False
            elif season is not None and episode is None:
                 # This is a request for a specific season's episodes list
                 # OMDB season response contains 'Season', 'Episodes' (a list), 'Response'
                 if data.get('Response') == 'True' and data.get('Season') == str(season):
                      logging.info(f"Successfully fetched OMDB season details for Series ID '{imdb_id}', Season {season}. Found {len(data.get('Episodes', []))} episodes.")
                      return data
                 else:
                     logging.warning(f"OMDB season details response does not match request for Series ID '{imdb_id}', Season {season}. Response: {data}")
                     return {'Response': 'False', 'Error': 'Season data mismatch or not found'} # Return a dict with Response=False
            else:
                # It's a main movie/series details request (season and episode are None)
                logging.info(f"Successfully fetched OMDB main details for ID '{imdb_id}'. Type: {data.get('Type')}")
                return data
        else:
             # Handle OMDB Response 'False' case for any request type
             error_message = data.get('Error', 'Unknown error from OMDB')
             if season is not None and episode is not None:
                  logging.info(f"OMDB episode not found for Series ID '{imdb_id}', S{season}E{episode}: {error_message}")
             elif season is not None:
                 logging.info(f"OMDB season data not found for Series ID '{imdb_id}', Season {season}: {error_message}")
             else:
                  logging.info(f"OMDB main details not found for ID '{imdb_id}': {error_message}")
             return {'Response': 'False', 'Error': error_message} # Return a dict with Response=False
    except requests.exceptions.RequestException as e:
        # Handle network or request errors
        logging.error(f"Error calling OMDB details API for ID {imdb_id} (Season {season}, Episode {episode}): {e}", exc_info=True)
        return {'Response': 'False', 'Error': f'Request Error: {e}'}


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
    # OMDB search results contain Title, Year, imdbID, Type, Poster. We need to return these.
    formatted_results = []
    for res in results:
         formatted_results.append({
             'Title': res.get('Title'),
             'Year': res.get('Year'),
             'imdbID': res.get('imdbID'),
             'Type': res.get('Type'),
             'Poster': res.get('Poster') # Include poster for display in results list if desired
         })

    return jsonify(formatted_results)

@app.route('/api/get_omdb_details')
def api_get_omdb_details():
    imdb_id = request.args.get('i')
    season = request.args.get('season') # Optional: for episode lookup
    episode = request.args.get('episode') # Optional: for episode lookup

    if not imdb_id:
        logging.warning("API get details called with missing IMDb ID.")
        return jsonify({"Error": "Missing IMDb ID"}), 400

    try:
        season_int = int(season) if season else None
        episode_int = int(episode) if episode else None
    except ValueError:
        logging.warning(f"API get details called with non-integer season/episode: season={season}, episode={episode}")
        return jsonify({"Error": "Invalid season or episode number"}), 400

    # Call the unified get_omdb_details_api function
    details = get_omdb_details_api(imdb_id, season_int, episode_int)

    if details and details.get('Response') == 'True':
        # Return the raw details object from OMDB
        return jsonify(details)
    else:
        # Return 404 if details (or specific episode/season) not found or API error
        error_message = details.get('Error', 'Details not found or API error') if isinstance(details, dict) else 'Details not found or API error'
        return jsonify({"Error": error_message}), 404


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

        # Access user info directly from userinfo_response
        userinfo_response = oauth.google.userinfo(token=token)

        user_data = {
            'name': userinfo_response.get('name'), # Access properties directly
            'email': userinfo_response.get('email'), # Access properties directly
            'picture': userinfo_response.get('picture'), # Access properties directly
            'google_id': userinfo_response.get('sub') # Access properties directly (sub is standard OIDC ID)
        }

        if not user_data.get('google_id'):
            logging.warning("Google login callback failed: No google_id received.")
            flash('התחברות עם גוגל נכשלה: לא הושגו פרטי משתמש.', 'error')
            return redirect(url_for('index'))

        session['user'] = user_data
        session.permanent = True
        logging.info(f"User {user_data.get('email')} logged in successfully.")
        flash('התחברת בהצלחה!', 'success')

        # --- Check for pending invitations for this user on successful login ---
        # This is a basic check. A more robust system would handle this via SocketIO
        # or check on page load for *any* page. This check is minimal.
        try:
            pending_invites_ref = db.reference('/invitations').order_by_child('receiver_email').equal_to(user_data['email']).get()
            if pending_invites_ref:
                # Filter for status 'pending' and potentially recent invites
                 pending_invites = {key: inv for key, inv in pending_invites_ref.items() if inv.get('status') == 'pending'}
                 if pending_invites:
                     # Store pending invites in session or use a flash message
                     # Using session for demo, could be complex with multiple invites
                     first_invite_key = list(pending_invites.keys())[0]
                     first_invite = pending_invites[first_invite_key]
                     flash(f"יש לך הזמנה לצפייה מ{first_invite.get('sender_email')} לסרט '{first_invite.get('movie_title')}'. אנא עבור לדף הסרט כדי לקבל/לדחות.", 'info')
                     logging.info(f"User {user_data['email']} has {len(pending_invites)} pending invites.")
                 else:
                     logging.info(f"User {user_data['email']} has no pending invites.")
            else:
                 logging.info(f"No invites found for user {user_data['email']} in Firebase.")
        except Exception as e:
            logging.error(f"Error checking for pending invites for user {user_data['email']}: {e}", exc_info=True)
            flash('אירעה שגיאה בבדיקת הזמנות ממתינות.', 'warning')
        # --- End Check for pending invitations ---


        return redirect(url_for('index'))

    except Exception as e:
        logging.error("Error during Google login callback:", exc_info=True)
        flash('התחברות נכשלה. אנא ודא שההרשאות המתאימות אושרו ונסה שוב.', 'error')
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    user_email = session.get('user', {}).get('email', 'anonymous')
    session.pop('user', None)
    # When a user logs out, remove their SocketIO mapping if it exists
    sid = email_to_sid.pop(user_email, None)
    if sid and sid_to_email.get(sid) == user_email:
         del sid_to_email[sid]
         logging.info(f"Removed SocketIO mapping for logged out user {user_email} (sid {sid})")


    logging.info(f"User {user_email} logged out.")
    flash('התנתקת בהצלחה.', 'info')
    return redirect(url_for('index'))

# --- Main Routes ---
@app.route('/')
def index():
    user = session.get('user')
    greeting = get_greeting(user)

    # Load all content from Firebase for the index page
    movies_data = load_movies_data() # Includes type: 'movie'
    series_data_for_index = load_series_data_for_index() # Includes type: 'series'

    # Categorize them for standard display sections
    categories = categorize_content(movies_data, series_data_for_index)

    current_year = datetime.datetime.utcnow().year

    return render_template('index.html',
                           greeting=greeting,
                           categories=categories, # All categoried content for display and JS lookup
                           current_year=current_year,
                           user=user,
                           admin_emails=ADMIN_EMAILS # Pass the list of admin emails
                           )

# --- Route for Single Movie Page ---
@app.route('/movie/<imdb_id>')
def movie_details(imdb_id):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year

    # Validate IMDb ID format before querying
    if not imdb_id or not imdb_id.startswith('tt') or len(imdb_id) < 7:
         logging.warning(f"Attempted to access movie page with invalid IMDb ID format: {imdb_id}")
         abort(404)

    # Load movie details from Firebase
    movie = load_movie_details(imdb_id)

    if not movie or (movie.get('type') not in [None, 'movie'] and movie.get('type') != 'movie'):
        logging.warning(f"Movie details not found or is not of type 'movie' for ID: {imdb_id}")
        abort(404)

    # Check for pending invitations specifically for *this* movie for the logged-in user
    pending_invite = None
    if user and user.get('email'):
        try:
            invitation_key_prefix = f"{user['email'].replace('.', '_')}_" # Receiver prefix
            invitations_ref = db.reference('/invitations')
            # Firebase doesn't allow querying by partial key, need to fetch and filter
            all_invites = invitations_ref.get()
            if all_invites:
                 # Find invites where this user is the receiver, status is pending, and it's for this movie
                 for key, invite in all_invites.items():
                      if invite.get('receiver_email') == user['email'] and \
                         invite.get('status') == 'pending' and \
                         invite.get('movie_imdb_id') == imdb_id:
                            pending_invite = invite # Found a pending invite for this movie
                            logging.info(f"Found pending invitation for user {user['email']} for movie {imdb_id}")
                            break # Found one, no need to search further
        except Exception as e:
            logging.error(f"Error checking for pending invites for movie {imdb_id} for user {user['email']}: {e}", exc_info=True)
            pending_invite = None # Ensure it's None on error


    return render_template('movie.html',
                           movie=movie, # movie object should contain video_url if needed for playback
                           user=user,
                           current_year=current_year,
                           admin_emails=ADMIN_EMAILS, # Pass the list of admin emails
                           pending_invite=pending_invite # Pass pending invite data
                           )

# --- Route for Single Series Page ---
# Base series page
@app.route('/series/<imdb_id>')
# Series page with specific season and episode number
@app.route('/series/<imdb_id>/<int:season_number>/<int:episode_number>')
def series_details(imdb_id, season_number=None, episode_number=None):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year

    # Validate Series IMDb ID format
    if not imdb_id or not imdb_id.startswith('tt') or len(imdb_id) < 7:
        logging.warning(f"Attempted to access series page with invalid Series IMDb ID format: {imdb_id}")
        abort(404)

    # Validate Season/Episode Numbers if provided (Flask <int:> handles non-integers,
    # but we can add checks for non-positive if needed, though JS also validates >=1)
    if season_number is not None and (season_number < 1):
        logging.warning(f"Attempted to access series page with invalid season number ({season_number}) for series {imdb_id}")
        pass # Continue loading the page

    if episode_number is not None and (episode_number < 1):
        logging.warning(f"Attempted to access series page with invalid episode number ({episode_number}) for series {imdb_id}")
        pass # Continue loading the page

    # Load full series details from Firebase (including Seasons/Episodes)
    series = load_full_series_details(imdb_id)

    # Check if found and if it's a series type
    if not series or (series.get('type') not in [None, 'series'] and series.get('type') != 'series'):
        logging.warning(f"Series details not found or is not of type 'series' for ID: {imdb_id}")
        abort(404)

    # Pass the full series object to the template.
    return render_template('series.html',
                           series=series, # Pass the full series data
                           user=user,
                           current_year=current_year,
                           admin_emails=ADMIN_EMAILS # Pass the list of admin emails
                           )


@app.route('/add', methods=['GET', 'POST'])
def add_content():
    user = session.get('user')
    # Basic admin check: Check if the user's email is in the list of ADMIN_EMAILS
    if not user or user.get('email') not in ADMIN_EMAILS:
        logging.warning(f"Unauthorized access attempt to /add by {user.get('email') if user else 'anonymous'}")
        abort(403)

    if request.method == 'POST':
        content_type = request.form.get('content_type')
        logging.info(f"Received POST for content type: {content_type}")

        try:
            if content_type == 'movie':
                imdb_id = request.form.get('movie_imdb_id', '').strip()
                category = request.form.get('movie_category', 'ללא')

                if not imdb_id:
                    flash('שגיאה: שדה חובה (IMDb ID) חסר עבור סרט.', 'error')
                    return redirect(url_for('add_content'))

                imdb_id_pattern = re.compile(r'^tt\d{7,}$')
                if not imdb_id_pattern.match(imdb_id):
                     flash('שגיאה: פורמט IMDb ID לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                     return redirect(url_for('add_content'))

                omdb_details = get_omdb_details_api(imdb_id)

                if not omdb_details or omdb_details.get('Response') == 'False' or omdb_details.get('Type', '').lower() != 'movie':
                     error_msg = omdb_details.get('Error', 'Details not found or API error') if isinstance(omdb_details, dict) else 'Details not found or API error'
                     flash(f'שגיאה: לא נמצאו פרטי סרט תקינים עבור IMDb ID "{imdb_id}" ב-OMDB. {error_msg}', 'error')
                     logging.warning(f"OMDB details not found or type is not 'movie' for ID {imdb_id}. OMDB Response: {omdb_details}")
                     return redirect(url_for('add_content'))

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
                    'plot': omdb_details.get('Plot', 'N/A'),
                    'language': omdb_details.get('Language', 'N/A'),
                    'country': omdb_details.get('Country', 'N/A'),
                    'awards': omdb_details.get('Awards', 'N/A'),
                    'poster': omdb_details.get('Poster', 'N/A'),
                    'ratings': omdb_details.get('Ratings', []),
                    'metascore': omdb_details.get('Metascore', 'N/A'),
                    'imdbRating': omdb_details.get('imdbRating', 'N/A'),
                    'imdbVotes': omdb_details.get('imdbVotes', 'N/A'),
                    'type': 'movie',
                    'dvd': omdb_details.get('DVD', 'N/A'),
                    'boxoffice': omdb_details.get('BoxOffice', 'N/A'),
                    'production': omdb_details.get('Production', 'N/A'),
                    'website': omdb_details.get('Website', 'N/A'),
                    'video_url': '', # Still empty placeholder, not from form
                    'category': category
                }

                ref = db.reference(f'/Movies/{imdb_id}')
                ref.set(movie_data)
                logging.info(f"Movie '{movie_data['title']}' ({imdb_id}) added to Firebase.")
                flash(f'סרט "{movie_data["title"]}" נוסף בהצלחה!', 'success')

            elif content_type == 'series':
                 series_imdb_id = request.form.get('series_imdb_id', '').strip()
                 category = request.form.get('series_category', 'ללא')

                 if not series_imdb_id:
                     flash('שגיאה: שדה חובה עבור סדרה (IMDb ID) חסר.', 'error')
                     return redirect(url_for('add_content'))

                 imdb_id_pattern = re.compile(r'^tt\d{7,}$')
                 if not imdb_id_pattern.match(series_imdb_id):
                     flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                     return redirect(url_for('add_content'))


                 omdb_details = get_omdb_details_api(series_imdb_id)

                 if not omdb_details or omdb_details.get('Response') == 'False' or omdb_details.get('Type', '').lower() != 'series':
                      error_msg = omdb_details.get('Error', 'Details not found or API error') if isinstance(omdb_details, dict) else 'Details not found or API error'
                      flash(f'שגיאה: לא נמצאו פרטים לסדרה או שה-ID אינו של סדרה עבור "{series_imdb_id}" ב-OMDB. {error_msg}', 'error')
                      logging.warning(f"OMDB details not found or type is not 'series' for ID {series_imdb_id}. OMDB Response: {omdb_details}")
                      return redirect(url_for('add_content'))

                 try:
                     total_seasons_str = omdb_details.get('totalSeasons', '1')
                     total_seasons = int(total_seasons_str)
                     if total_seasons < 1:
                          logging.warning(f"OMDB returned invalid totalSeasons ({total_seasons_str}) for {series_imdb_id}. Defaulting to 1.")
                          total_seasons = 1
                 except ValueError:
                      logging.warning(f"OMDB returned non-integer totalSeasons ({total_seasons_str}) for {series_imdb_id}. Defaulting to 1.")
                      total_seasons = 1


                 series_data = {
                    'imdbID': omdb_details.get('imdbID', series_imdb_id),
                    'title': omdb_details.get('Title', 'Untitled Series'),
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
                    'poster': omdb_details.get('Poster', 'N/A'),
                    'ratings': omdb_details.get('Ratings', []),
                    'metascore': omdb_details.get('Metascore', 'N/A'),
                    'imdbRating': omdb_details.get('imdbRating', 'N/A'),
                    'imdbVotes': omdb_details.get('imdbVotes', 'N/A'),
                    'type': 'series',
                    'totalSeasons': total_seasons_str,
                    'category': category
                 }

                 seasons_data = {}
                 all_episodes_fetched_successfully = True

                 for season_num in range(1, total_seasons + 1):
                     season_details_from_omdb = get_omdb_details_api(series_imdb_id, season=season_num)

                     if season_details_from_omdb and season_details_from_omdb.get('Response') == 'True' and season_details_from_omdb.get('Episodes'):
                          episodes_list_for_season = season_details_from_omdb.get('Episodes', [])
                          episodes_data = {}
                          num_episodes_in_season = len(episodes_list_for_season)
                          logging.info(f"Fetched {num_episodes_in_season} episodes for S{season_num} from OMDB for series {series_imdb_id}.")

                          for episode_detail in episodes_list_for_season:
                               try:
                                   episode_num_str = episode_detail.get('Episode')
                                   episode_num = int(episode_num_str) if episode_num_str else None

                                   if episode_num is not None and episode_num >= 1:
                                        episode_imdb_id_to_save = episode_detail.get('imdbID', f'tt_placeholder_{series_imdb_id}_s{season_num}e{episode_num}')
                                        episode_title_to_save = episode_detail.get('Title', f'פרק {episode_num}')

                                        episodes_data[str(episode_num)] = {
                                            'episode_imdb_id': episode_imdb_id_to_save,
                                            'title': episode_title_to_save,
                                            'season_number': season_num,
                                            'episode_number': episode_num,
                                            'video_url': '', # Still empty placeholder
                                        }
                                   else:
                                       logging.warning(f"Skipping episode data with invalid number or missing data in OMDB Season {season_num} response for {series_imdb_id}: {episode_detail}")
                                       all_episodes_fetched_successfully = False


                               except ValueError:
                                    logging.warning(f"Could not parse episode number from OMDB Season {season_num} response for {series_imdb_id}: {episode_detail.get('Episode')}. Skipping episode.")
                                    all_episodes_fetched_successfully = False
                               except Exception as e:
                                   logging.error(f"Unexpected error processing episode data for S{season_num} in {series_imdb_id}: {e}", exc_info=True)
                                   all_episodes_fetched_successfully = False


                          if episodes_data:
                              seasons_data[str(season_num)] = {
                                  'Episodes': episodes_data
                              }
                          else:
                              logging.warning(f"No valid episode data found in OMDB response for Season {season_num} of series {series_imdb_id}. Season might not be added.")
                              all_episodes_fetched_successfully = False

                     else:
                         error_msg = season_details_from_omdb.get('Error', 'Unknown Error') if isinstance(season_details_from_omdb, dict) else 'Unknown Error'
                         logging.warning(f"Failed to fetch OMDB season details or found no episodes for Season {season_num} of series {series_imdb_id}. OMDB Error: {error_msg}. Season will not be added.")
                         all_episodes_fetched_successfully = False

                 if seasons_data:
                      series_data['Seasons'] = seasons_data
                 else:
                     logging.warning(f"No seasons or episodes were successfully added for series {series_imdb_id} based on OMDB data.")

                 ref = db.reference(f'/Series/{series_imdb_id}')
                 ref.update(series_data)
                 logging.info(f"Series '{series_data.get('title', series_imdb_id)}' ({series_imdb_id}) added/updated in Firebase with {len(seasons_data)} seasons.")

                 flash_message = f'סדרה "{series_data.get("title", series_imdb_id)}" נוספה/עודכנה בהצלחה!'
                 if not all_episodes_fetched_successfully:
                      flash_message += ' אזהרה: לא ניתן היה להשיג פרטים עבור כל הפרקים/עונות מ-OMDb. בדוק את הנתונים שנוספו.'
                      flash(flash_message, 'warning')
                 else:
                      flash(flash_message, 'success')

            elif content_type == 'episode':
                 series_imdb_id_select = request.form.get('episode_series_id')
                 manual_series_imdb_id = request.form.get('manual_episode_series_id', '').strip()
                 episode_title_form = request.form.get('episode_title', '').strip()
                 season_number_str = request.form.get('episode_season', '').strip()
                 episode_number_str = request.form.get('episode_number', '').strip()

                 series_imdb_id = manual_series_imdb_id if series_imdb_id_select == 'manual' else series_imdb_id_select

                 if not series_imdb_id or not season_number_str or not episode_number_str:
                      missing = []
                      if not series_imdb_id: missing.append('סדרה')
                      if not season_number_str: missing.append('מספר עונה')
                      if not episode_number_str: missing.append('מספר פרק')
                      flash(f'שגיאה: שדות חובה חסרים: {", ".join(missing)}.', 'error')
                      return redirect(url_for('add_content'))

                 try:
                     season_number = int(season_number_str)
                     episode_number = int(episode_number_str)
                     if season_number < 1 or episode_number < 1:
                         raise ValueError("Numbers must be positive")
                 except ValueError:
                     flash('שגיאה: מספרי עונה ופרק חייבים להיות מספרים שלמים חיוביים.', 'error')
                     return redirect(url_for('add_content'))

                 imdb_id_pattern = re.compile(r'^tt\d{7,}$')
                 if not imdb_id_pattern.match(series_imdb_id):
                     flash('שגיאה: פורמט IMDb ID של הסדרה לא תקין. ודא שהוא מתחיל ב-"tt" ואחריו 7 ספרות או יותר.', 'error')
                     return redirect(url_for('add_content'))

                 episode_details_from_omdb = get_omdb_details_api(series_imdb_id, season=season_number, episode=episode_number)

                 episode_imdb_id_to_save = None
                 episode_title_to_save = episode_title_form

                 if episode_details_from_omdb and episode_details_from_omdb.get('Response') == 'True':
                      episode_imdb_id_to_save = episode_details_from_omdb.get('imdbID')
                      if not episode_title_form:
                          episode_title_to_save = episode_details_from_omdb.get('Title', f'פרק {episode_number} (מ-OMDb)')
                      logging.info(f"Fetched OMDB details for episode {series_imdb_id} S{season_number}E{episode_number}. Episode IMDb ID: {episode_imdb_id_to_save}, Title: '{episode_details_from_omdb.get('Title')}'")
                 else:
                     error_msg = episode_details_from_omdb.get('Error', 'Unknown Error') if isinstance(episode_details_from_omdb, dict) else 'Unknown Error'
                     logging.warning(f"Failed to fetch OMDB details for episode {series_imdb_id} S{season_number}E{episode_number}. OMDB Error: {error_msg}. Proceeding with form data and placeholder ID.")
                     episode_imdb_id_to_save = f'tt_placeholder_{series_imdb_id}_s{season_number}e{episode_number}'
                     if not episode_title_form:
                         episode_title_to_save = f'פרק {episode_number} (לא נמצא שם ב-OMDb)'

                 if not episode_imdb_id_to_save:
                     episode_imdb_id_to_save = f'tt_fallback_{series_imdb_id}_s{season_number}e{episode_number}'
                     logging.error(f"Critical: No episode IMDb ID could be determined for {series_imdb_id} S{season_number}E{episode_number}. Using double-fallback placeholder.")

                 episode_data = {
                     'episode_imdb_id': episode_imdb_id_to_save,
                     'title': episode_title_to_save,
                     'video_url': '',
                     'episode_number': episode_number,
                     'season_number': season_number
                 }

                 ref = db.reference(f'/Series/{series_imdb_id}/Seasons/{season_number}/Episodes/{episode_number}')
                 ref.update(episode_data)
                 logging.info(f"Episode S{season_number}E{episode_number} (IMDb ID: {episode_imdb_id_to_save}, Title: '{episode_data['title']}') added/updated in series {series_imdb_id}.")
                 flash(f'פרק "{episode_data["title"]}" (עונה {season_number}, פרק {episode_number}) נוסף/עודכן בהצלחה לסדרה!', 'success')


            else:
                 flash('סוג תוכן לא ידוע.', 'warning')
                 logging.warning(f"Received unknown content type: {content_type}")


        except Exception as e:
             logging.error(f"Error processing add content POST: {e}", exc_info=True)
             flash('אירעה שגיאה בעת שמירת התוכן.', 'error')

        return redirect(url_for('add_content'))

    available_series = load_series_list_for_add_page()

    return render_template('add.html',
                           user=user,
                           categories=[c for c in CATEGORIES if c != 'ללא'],
                           available_series=available_series,
                           current_year=datetime.datetime.utcnow().year,
                           admin_emails=ADMIN_EMAILS
                           )


@app.route('/movies')
def all_movies():
    """Displays all movies from Firebase in a grid."""
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    all_movies_data = load_movies_data()
    logging.info(f"Rendering all_movies page with {len(all_movies_data)} movies.")
    return render_template('movies.html',
                           movies=all_movies_data,
                           user=user,
                           current_year=current_year,
                           admin_emails=ADMIN_EMAILS
                           )


@app.route('/series')
def all_series():
    """Displays all series from Firebase in a grid."""
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    all_series_data = load_series_data_for_index()
    logging.info(f"Rendering all_series page with {len(all_series_data)} series.")
    return render_template('SeriesTV.html',
                           series=all_series_data,
                           user=user,
                           current_year=current_year,
                           admin_emails=ADMIN_EMAILS
                           )

# --- New Watch Together Route ---
@app.route('/watch-together/<imdb_id>/<sender_email_encoded>/<receiver_email_encoded>')
def watch_together(imdb_id, sender_email_encoded, receiver_email_encoded):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year

    # Ensure user is logged in
    if not user or not user.get('email'):
         flash('יש להתחבר כדי לצפות יחד בסרט.', 'warning')
         return redirect(url_for('google_login')) # Or redirect to index/login page

    # Decode emails
    try:
        sender_email = urllib.parse.unquote_plus(sender_email_encoded)
        receiver_email = urllib.parse.unquote_plus(receiver_email_encoded)
    except Exception as e:
         logging.error(f"Failed to decode emails in watch-together URL: {e}", exc_info=True)
         abort(400) # Bad request if emails are not decodeable

    # Check if the logged-in user is either the sender or the receiver for this session
    logged_in_email = user['email']
    if logged_in_email != sender_email and logged_in_email != receiver_email:
        logging.warning(f"Unauthorized access attempt to watch-together session for {sender_email}/{receiver_email} by user {logged_in_email}")
        flash('אין לך הרשאה לצפות בסרט זה יחד עם משתמשים אלו.', 'error')
        return redirect(url_for('index')) # Or a specific error page

    # Load movie details (same as movie_details route)
    if not imdb_id or not imdb_id.startswith('tt') or len(imdb_id) < 7:
         logging.warning(f"Attempted to access watch-together page with invalid IMDb ID format: {imdb_id}")
         abort(404)

    movie = load_movie_details(imdb_id)

    if not movie or (movie.get('type') not in [None, 'movie'] and movie.get('type') != 'movie'):
        logging.warning(f"Movie details not found or is not of type 'movie' for ID: {imdb_id} for watch-together session.")
        abort(404)

    # Determine if the current user is the controller (the sender)
    is_controller = (logged_in_email == sender_email)

    # Render the new watch-together template
    return render_template('watch-together.html',
                           movie=movie,
                           user=user, # The logged-in user
                           sender_email=sender_email,
                           receiver_email=receiver_email,
                           is_controller=is_controller, # Boolean flag for JS
                           current_year=current_year,
                           admin_emails=ADMIN_EMAILS
                           )


# --- Error Handlers ---
@app.errorhandler(403)
def forbidden(e):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    logging.warning(f"403 Forbidden: {request.path} - {e}")
    return render_template('403.html', user=user, current_year=current_year), 403

@app.errorhandler(404)
def page_not_found(e):
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    logging.warning(f"404 Not Found: {request.path} - {e}")
    return render_template('404.html', user=user, current_year=current_year), 404

@app.errorhandler(500)
def internal_server_error(e):
    tb_str = traceback.format_exc()
    logging.error(f"Internal Server Error: {request.path} - {e}\n{tb_str}", exc_info=True)
    user = session.get('user')
    current_year = datetime.datetime.utcnow().year
    return render_template('500.html', user=user, current_year=current_year), 500


if __name__ == '__main__':
    if firebase_admin._apps:
        try:
             default_app_creds = firebase_admin._apps['[DEFAULT]'].options.get('credential')
             if default_app_creds is not None:
                logging.info("Firebase default app credential check passed.")
                port = int(os.environ.get('PORT', 5000))
                # Use socketio.run instead of app.run
                # debug=True should only be used in development
                socketio.run(app, host='0.0.0.0', port=port, debug=True)
             else:
                 logging.error("Application not started: Firebase default app credential is None.")
        except KeyError:
            logging.error("Application not started: Firebase default app was not initialized.")
        except Exception as e:
             logging.error(f"Application not started: Unexpected error during Firebase check: {e}", exc_info=True)

    else:
        logging.error("Application not started because Firebase initialization failed.")
