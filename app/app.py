from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash
)

import os
import uuid
import requests
import datetime

from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

import urllib.parse
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import Increment
import cloudinary
import cloudinary.uploader


load_dotenv()

app = Flask(__name__)


# -----------------------------------------------------------
# SECRETS — loaded from environment, never hardcoded
# -----------------------------------------------------------

app.secret_key = os.environ.get("SECRET_KEY")

if not app.secret_key:
    raise RuntimeError(
        "SECRET_KEY is not set. Create a .env file with a SECRET_KEY "
        "value before running the app (see .env.example)."
    )


ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")

ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")

if not ADMIN_USERNAME or not ADMIN_PASSWORD_HASH:
    raise RuntimeError(
        "ADMIN_USERNAME / ADMIN_PASSWORD_HASH are not set. See "
        ".env.example — generate the hash with generate_password_hash()."
    )


# -----------------------------------------------------------
# FIREBASE — Firestore (database)
# -----------------------------------------------------------
#
# Locally: set GOOGLE_APPLICATION_CREDENTIALS in your .env to the path
# of a service-account JSON key downloaded from
# Firebase Console > Project Settings > Service Accounts.
#
# On Cloud Run: leave GOOGLE_APPLICATION_CREDENTIALS unset — Cloud Run
# provides credentials automatically via its service account.

cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

if cred_path:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
else:
    # Cloud Run / any environment with Application Default Credentials
    firebase_admin.initialize_app()

db = firestore.client()


# -----------------------------------------------------------
# CLOUDINARY — free image hosting for uploaded complaint photos
# (Firebase Storage now requires a paid Blaze plan, so images
# live here instead; everything else stays on Firebase.)
# -----------------------------------------------------------

CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL")

if not CLOUDINARY_URL:
    raise RuntimeError(
        "CLOUDINARY_URL is not set. Add it to your .env — you get it "
        "free from your Cloudinary dashboard after signing up."
    )

# Parsed manually (rather than passed straight to cloudinary.config)
# because the cloudinary package only auto-reads CLOUDINARY_URL from
# the environment at import time — which is before load_dotenv() has
# run, so it would otherwise silently pick up nothing.
_parsed_cloudinary_url = urllib.parse.urlparse(CLOUDINARY_URL)

cloudinary.config(
    cloud_name=_parsed_cloudinary_url.hostname,
    api_key=_parsed_cloudinary_url.username,
    api_secret=_parsed_cloudinary_url.password,
    secure=True
)


# -----------------------------------------------------------
# GARBAGE IMAGE CHECK — uses Roboflow's free garbage-detection
# model so citizens can't upload random / unrelated photos.
# -----------------------------------------------------------

ROBOFLOW_API_KEY = os.environ.get("ROBOFLOW_API_KEY")

ROBOFLOW_MODEL_ID = os.environ.get(
    "ROBOFLOW_MODEL_ID",
    "garbage_detection-wvzwv/9"
)

GARBAGE_CONFIDENCE_THRESHOLD = 0.35


def contains_garbage(image_bytes):
    """
    Sends the uploaded image bytes to the Roboflow garbage-detection API.

    Returns True if garbage/litter is detected with confidence above
    the threshold, False otherwise. Fails open (returns True) if the
    API key isn't set or the call fails, so a temporary outage never
    blocks citizens from submitting real reports.
    """

    if not ROBOFLOW_API_KEY:
        return True

    try:
        response = requests.post(
            f"https://detect.roboflow.com/{ROBOFLOW_MODEL_ID}",
            params={
                "api_key": ROBOFLOW_API_KEY,
                "confidence": int(GARBAGE_CONFIDENCE_THRESHOLD * 100),
            },
            files={"file": image_bytes},
            timeout=10,
        )

        response.raise_for_status()

        result = response.json()

        predictions = result.get("predictions", [])

        for prediction in predictions:
            if prediction.get("confidence", 0) >= GARBAGE_CONFIDENCE_THRESHOLD:
                return True

        return False

    except Exception:
        return True


# -----------------------------------------------------------
# UPLOADS — restricted to image types, capped in size
# -----------------------------------------------------------

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB max upload


def allowed_image(filename):

    if "." not in filename:
        return False

    extension = filename.rsplit(".", 1)[1].lower()

    return extension in ALLOWED_EXTENSIONS


def upload_image_to_storage(file_storage, extension):
    """Uploads an image to Cloudinary and returns its public URL."""

    image_filename = uuid.uuid4().hex

    result = cloudinary.uploader.upload(
        file_storage,
        public_id=f"uploads/{image_filename}",
        resource_type="image"
    )

    return result["secure_url"]


# =========================================================
# BADGES
# =========================================================

def get_badge(points):

    if points >= 1000:
        return "👑", "CleanCity Champion"

    if points >= 500:
        return "🌍", "City Guardian"

    if points >= 250:
        return "🏆", "CleanCity Hero"

    if points >= 100:
        return "🌿", "Clean Citizen"

    if points >= 50:
        return "🧹", "Clean Starter"

    if points >= 10:
        return "🌱", "First Step"

    return "✨", "New Citizen"


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    if not session.get("citizen_id"):

        return redirect(url_for("citizen_login"))

    return render_template("index.html")


# =========================================================
# CITIZEN REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form.get("username", "").strip()

        password = request.form.get("password", "")

        if len(username) < 3:

            flash("Username must be at least 3 characters.")

            return redirect(url_for("register"))

        if len(password) < 4:

            flash("Password must be at least 4 characters.")

            return redirect(url_for("register"))

        users_ref = db.collection("users")

        existing = list(
            users_ref.where("username", "==", username).limit(1).stream()
        )

        if existing:

            flash("That username already exists.")

            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        users_ref.add({
            "username": username,
            "password": hashed_password,
            "points": 0,
            "created_at": firestore.SERVER_TIMESTAMP
        })

        flash("Account created! Please log in.")

        return redirect(url_for("citizen_login"))

    return render_template("register.html")


# =========================================================
# CITIZEN LOGIN
# =========================================================

@app.route("/citizen-login", methods=["GET", "POST"])
def citizen_login():

    if request.method == "POST":

        username = request.form.get("username", "").strip()

        password = request.form.get("password", "")

        users_ref = db.collection("users")

        matches = list(
            users_ref.where("username", "==", username).limit(1).stream()
        )

        user_doc = matches[0] if matches else None

        if user_doc and check_password_hash(
            user_doc.to_dict()["password"],
            password
        ):

            session["citizen_id"] = user_doc.id

            session["citizen_username"] = user_doc.to_dict()["username"]

            return redirect(url_for("home"))

        flash("Incorrect username or password.")

    return render_template("citizen_login.html")


# =========================================================
# CITIZEN LOGOUT
# =========================================================

@app.route("/citizen-logout")
def citizen_logout():

    session.pop("citizen_id", None)

    session.pop("citizen_username", None)

    return redirect(url_for("citizen_login"))


# =========================================================
# CITIZEN PROFILE
# =========================================================

@app.route("/profile")
def profile():

    if not session.get("citizen_id"):

        return redirect(url_for("citizen_login"))

    citizen_id = session["citizen_id"]

    user_doc = db.collection("users").document(citizen_id).get()

    if not user_doc.exists:

        session.pop("citizen_id", None)

        return redirect(url_for("citizen_login"))

    user = user_doc.to_dict()

    complaint_docs = list(
        db.collection("complaints")
        .where("citizen_id", "==", citizen_id)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .stream()
    )

    complaints = []

    resolved = 0

    for doc in complaint_docs:

        data = doc.to_dict()

        data["id"] = doc.id

        complaints.append(data)

        if data.get("status") == "Resolved":

            resolved += 1

    total = len(complaints)

    badge_icon, badge_name = get_badge(user.get("points", 0))

    return render_template(
        "profile.html",
        user=user,
        complaints=complaints,
        total=total,
        resolved=resolved,
        badge_icon=badge_icon,
        badge_name=badge_name
    )


# =========================================================
# LEADERBOARD
# =========================================================

@app.route("/leaderboard")
def leaderboard():

    users_docs = (
        db.collection("users")
        .order_by("points", direction=firestore.Query.DESCENDING)
        .limit(20)
        .stream()
    )

    leaderboard_data = []

    for index, doc in enumerate(users_docs, start=1):

        user = doc.to_dict()

        icon, badge = get_badge(user.get("points", 0))

        leaderboard_data.append({
            "rank": index,
            "username": user.get("username"),
            "points": user.get("points", 0),
            "icon": icon,
            "badge": badge
        })

    return render_template(
        "leaderboard.html",
        users=leaderboard_data
    )


# =========================================================
# SUBMIT COMPLAINT
# =========================================================

@app.route("/submit", methods=["POST"])
def submit():

    if not session.get("citizen_id"):

        return redirect(url_for("citizen_login"))

    citizen_id = session["citizen_id"]

    username = session.get("citizen_username", "Citizen")

    description = request.form.get("description", "").strip()

    location = request.form.get("location", "").strip()

    coordinates = request.form.get("coordinates", "").strip()

    address = request.form.get("address", "").strip()

    if len(description) < 5:

        flash("Please add a short description (at least 5 characters).")

        return redirect(url_for("home"))

    if len(description) > 1000:

        flash("Description is too long (max 1000 characters).")

        return redirect(url_for("home"))

    image = request.files.get("image")

    if not image or not image.filename:

        flash("Please upload a garbage photo.")

        return redirect(url_for("home"))

    if not allowed_image(image.filename):

        flash("Please upload an image file (png, jpg, jpeg, gif, or webp).")

        return redirect(url_for("home"))

    original_name = secure_filename(image.filename)

    extension = original_name.rsplit(".", 1)[1].lower()

    image_bytes = image.read()

    if not contains_garbage(image_bytes):

        flash(
            "We couldn't spot any garbage/litter in that photo. "
            "Please upload a clear photo of the actual garbage."
        )

        return redirect(url_for("home"))

    # Reset stream position, then upload (contains_garbage already
    # consumed the bytes for the API check above).
    image.stream.seek(0)

    image_url = upload_image_to_storage(image, extension)

    db.collection("complaints").add({
        "name": username,
        "description": description,
        "location": location,
        "image": image_url,
        "status": "Reported",
        "coordinates": coordinates,
        "address": address,
        "citizen_id": citizen_id,
        "points_awarded": 0,
        "denial_reason": "",
        "created_at": firestore.SERVER_TIMESTAMP
    })

    flash(
        "🎉 Report received! Thank you for helping keep the city clean."
    )

    return redirect(url_for("profile"))


# =========================================================
# MUNICIPALITY LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username")

        password = request.form.get("password")

        if (
            username == ADMIN_USERNAME
            and check_password_hash(ADMIN_PASSWORD_HASH, password or "")
        ):

            session["admin_logged_in"] = True

            return redirect(url_for("admin"))

        flash("Wrong municipality username or password.")

    return render_template("login.html")


# =========================================================
# MUNICIPALITY LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.pop("admin_logged_in", None)

    return redirect(url_for("login"))


# =========================================================
# MUNICIPALITY DASHBOARD
# =========================================================

@app.route("/admin")
def admin():

    if not session.get("admin_logged_in"):

        return redirect(url_for("login"))

    complaint_docs = list(
        db.collection("complaints")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .stream()
    )

    # Batch-fetch usernames for all citizen_ids referenced, instead of
    # one query per complaint (mirrors the old SQL LEFT JOIN).
    citizen_ids = {
        doc.to_dict().get("citizen_id")
        for doc in complaint_docs
        if doc.to_dict().get("citizen_id")
    }

    usernames_by_id = {}

    for cid in citizen_ids:

        user_doc = db.collection("users").document(cid).get()

        if user_doc.exists:

            usernames_by_id[cid] = user_doc.to_dict().get("username")

    complaints = []

    for doc in complaint_docs:

        data = doc.to_dict()

        data["id"] = doc.id

        data["citizen_username"] = usernames_by_id.get(data.get("citizen_id"))

        complaints.append(data)

    return render_template(
        "admin.html",
        complaints=complaints
    )


# =========================================================
# UPDATE COMPLAINT STATUS
# =========================================================

@app.route("/update/<complaint_id>", methods=["POST"])
def update_status(complaint_id):

    if not session.get("admin_logged_in"):

        return redirect(url_for("login"))

    new_status = request.form.get("status")

    denial_reason = request.form.get("reason", "").strip()

    if new_status == "Denied" and len(denial_reason) < 3:

        flash("Please give a reason (at least 3 characters) when denying a complaint.")

        return redirect(url_for("admin"))

    complaint_ref = db.collection("complaints").document(complaint_id)

    complaint_doc = complaint_ref.get()

    if not complaint_doc.exists:

        return "Complaint not found"

    complaint = complaint_doc.to_dict()

    old_status = complaint.get("status")

    # -----------------------------------------------------
    # RESOLVED = GIVE POINTS
    # -----------------------------------------------------

    if (
        new_status == "Resolved"
        and old_status != "Resolved"
        and complaint.get("points_awarded", 0) == 0
        and complaint.get("citizen_id")
    ):

        points = 10

        db.collection("users").document(complaint["citizen_id"]).update({
            "points": Increment(points)
        })

        complaint_ref.update({
            "status": "Resolved",
            "points_awarded": points
        })

    elif new_status == "Denied":

        complaint_ref.update({
            "status": "Denied",
            "denial_reason": denial_reason
        })

    else:

        complaint_ref.update({
            "status": new_status
        })

    return redirect(url_for("admin"))


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    app.run(host="0.0.0.0", port=port, debug=debug_mode)
