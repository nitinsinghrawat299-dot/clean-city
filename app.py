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
import secrets
import smtplib
import ssl
from email.mime.text import MIMEText

from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

import urllib.parse
from google.cloud import firestore
from google.cloud.firestore_v1 import Increment
from google.oauth2 import service_account
import cloudinary
import cloudinary.uploader


load_dotenv()

app = Flask(__name__)

# Render (and most PaaS hosts) sit behind a reverse proxy that terminates
# HTTPS — without this, Flask doesn't know the original request was HTTPS,
# so links it builds itself (like password reset links) would come out as
# "http://" instead of "https://".
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Sessions are signed cookies, so they don't depend on server memory —
# but by default Flask treats them as "browser session" cookies, which
# some mobile browsers clear when the app is backgrounded / the tab is
# switched. Making sessions permanent (with a real expiry) fixes the
# "gets logged out when switching tabs" issue.
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=14)


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


# Usernames that citizens are not allowed to register, so nobody can
# impersonate the municipality account (or other reserved/official
# sounding names) in the citizen portal or on the leaderboard.
RESERVED_USERNAMES = {
    ADMIN_USERNAME.strip().lower(),
    "admin",
    "administrator",
    "municipality",
    "municipal",
    "cleancity",
    "clean city",
    "official",
    "government",
    "govt",
    "staff",
    "support",
    "moderator",
    "root",
    "superuser",
}


def is_reserved_username(username):
    return username.strip().lower() in RESERVED_USERNAMES


# -----------------------------------------------------------
# FIRESTORE (database) — connects directly via google-cloud-firestore
# rather than through the firebase_admin wrapper. This sidesteps a
# class of "Invalid database id (default)" errors some deployments hit
# with the firebase_admin abstraction layer, especially under Gunicorn
# with multiple workers.
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
    gcp_credentials = service_account.Credentials.from_service_account_file(
        cred_path
    )
    db = firestore.Client(
        credentials=gcp_credentials,
        project=gcp_credentials.project_id,
        database="(default)"
    )
else:
    # Cloud Run / any environment with Application Default Credentials
    db = firestore.Client(database="(default)")


# -----------------------------------------------------------
# SEQUENTIAL REPORT NUMBERS — Firestore document IDs are random
# strings (e.g. "us7Xk2..."), not useful to show citizens/admins.
# This keeps a single counter document and atomically increments it
# inside a transaction, so every complaint gets a clean, natural
# report number (1, 2, 3, ...) even with concurrent submissions.
# -----------------------------------------------------------

_report_counter_ref = db.collection("counters").document("complaints")


@firestore.transactional
def _increment_report_counter(transaction):

    snapshot = _report_counter_ref.get(transaction=transaction)

    current_value = snapshot.get("value") if snapshot.exists else 0

    next_value = current_value + 1

    transaction.set(_report_counter_ref, {"value": next_value})

    return next_value


def get_next_report_number():
    return _increment_report_counter(db.transaction())


# -----------------------------------------------------------
# EMAIL — used only for "forgot password" reset links.
# Works with Gmail (an App Password, not your normal password)
# or SendGrid's SMTP relay — any standard SMTP provider will do.
# -----------------------------------------------------------

SMTP_SERVER = os.environ.get("SMTP_SERVER")

SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))

SMTP_USERNAME = os.environ.get("SMTP_USERNAME")

SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")

EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USERNAME)

RESET_TOKEN_LIFETIME_MINUTES = 60


def send_email(to_email, subject, body_text):
    """
    Sends a plain-text email via SMTP. Returns True on success.

    If SMTP isn't configured (e.g. still in local dev without a mail
    provider set up), this logs to the console instead of raising —
    so the rest of the app keeps working even before email is wired up.
    """

    if not SMTP_SERVER or not SMTP_USERNAME or not SMTP_PASSWORD:

        print(
            "[email not configured] Would have sent to "
            f"{to_email}:\n{subject}\n{body_text}"
        )

        return False

    message = MIMEText(body_text)

    message["Subject"] = subject

    message["From"] = EMAIL_FROM

    message["To"] = to_email

    try:

        context = ssl.create_default_context()

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:

            server.starttls(context=context)

            server.login(SMTP_USERNAME, SMTP_PASSWORD)

            server.sendmail(EMAIL_FROM, [to_email], message.as_string())

        return True

    except Exception as error:

        print(f"[email send failed] {error}")

        return False


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

    citizen_id = session["citizen_id"]

    user_doc = db.collection("users").document(citizen_id).get()

    active_warning = None

    if user_doc.exists:

        user = user_doc.to_dict()

        if user.get("warning_message") and not user.get(
            "warning_acknowledged", True
        ):

            active_warning = user["warning_message"]

    return render_template("index.html", active_warning=active_warning)


# =========================================================
# CITIZEN REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form.get("username", "").strip()

        email = request.form.get("email", "").strip().lower()

        password = request.form.get("password", "")

        confirm_password = request.form.get("confirm_password", "")

        if len(username) < 3:

            flash("Username must be at least 3 characters.")

            return redirect(url_for("register"))

        if is_reserved_username(username):

            flash("That username is reserved. Please choose another one.")

            return redirect(url_for("register"))

        if "@" not in email or "." not in email.split("@")[-1]:

            flash("Please enter a valid email address.")

            return redirect(url_for("register"))

        if len(password) < 4:

            flash("Password must be at least 4 characters.")

            return redirect(url_for("register"))

        if password != confirm_password:

            flash("Passwords do not match.")

            return redirect(url_for("register"))

        users_ref = db.collection("users")

        existing_username = list(
            users_ref.where("username", "==", username).limit(1).stream()
        )

        if existing_username:

            flash("That username already exists.")

            return redirect(url_for("register"))

        existing_email = list(
            users_ref.where("email", "==", email).limit(1).stream()
        )

        if existing_email:

            flash("An account with that email already exists.")

            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        users_ref.add({
            "username": username,
            "email": email,
            "password": hashed_password,
            "points": 0,
            "reset_token": None,
            "reset_token_expires": None,
            "warning_message": None,
            "warning_issued_at": None,
            "warning_acknowledged": True,
            "created_at": firestore.SERVER_TIMESTAMP
        })

        flash("Account created! Just hit login below.")

        # Carry the just-entered credentials over to the login page so
        # the user only has to press "Login" — nothing to retype. This
        # is stashed in the session for exactly one request and popped
        # as soon as the login page reads it (see citizen_login below).
        session["prefill_username"] = username

        session["prefill_password"] = password

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

            session.permanent = True

            session["citizen_id"] = user_doc.id

            session["citizen_username"] = user_doc.to_dict()["username"]

            return redirect(url_for("home"))

        flash("Incorrect username or password.")

    # Pop (not just read) any prefill values left by a fresh registration
    # so they're only ever used once, right after signing up.
    prefill_username = session.pop("prefill_username", "")

    prefill_password = session.pop("prefill_password", "")

    return render_template(
        "citizen_login.html",
        prefill_username=prefill_username,
        prefill_password=prefill_password
    )


# =========================================================
# CITIZEN LOGOUT
# =========================================================

@app.route("/citizen-logout")
def citizen_logout():

    session.pop("citizen_id", None)

    session.pop("citizen_username", None)

    return redirect(url_for("citizen_login"))


# =========================================================
# FORGOT PASSWORD — request a reset link by email
# =========================================================

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()

        users_ref = db.collection("users")

        matches = list(
            users_ref.where("email", "==", email).limit(1).stream()
        )

        if matches:

            user_doc = matches[0]

            token = secrets.token_urlsafe(32)

            expires_at = (
                datetime.datetime.utcnow()
                + datetime.timedelta(minutes=RESET_TOKEN_LIFETIME_MINUTES)
            )

            user_doc.reference.update({
                "reset_token": token,
                "reset_token_expires": expires_at.isoformat()
            })

            reset_link = url_for(
                "reset_password",
                token=token,
                _external=True
            )

            send_email(
                to_email=email,
                subject="Reset your CleanCity password",
                body_text=(
                    "We received a request to reset your CleanCity "
                    "password.\n\n"
                    f"Click this link to choose a new password:\n{reset_link}\n\n"
                    f"This link expires in {RESET_TOKEN_LIFETIME_MINUTES} "
                    "minutes.\n\n"
                    "If you didn't request this, you can safely ignore "
                    "this email — your password will stay the same."
                )
            )

        # Same message whether or not the email was found, so we don't
        # reveal which emails have accounts registered.
        flash(
            "If that email is registered, we've sent a password reset "
            "link. Check your inbox (and spam folder)."
        )

        return redirect(url_for("citizen_login"))

    return render_template("forgot_password.html")


# =========================================================
# RESET PASSWORD — via the emailed token link
# =========================================================

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):

    users_ref = db.collection("users")

    matches = list(
        users_ref.where("reset_token", "==", token).limit(1).stream()
    )

    user_doc = matches[0] if matches else None

    token_valid = False

    if user_doc:

        expires_raw = user_doc.to_dict().get("reset_token_expires")

        if expires_raw:

            expires_at = datetime.datetime.fromisoformat(expires_raw)

            if datetime.datetime.utcnow() <= expires_at:

                token_valid = True

    if not token_valid:

        flash(
            "That reset link is invalid or has expired. Please request "
            "a new one."
        )

        return redirect(url_for("forgot_password"))

    if request.method == "POST":

        password = request.form.get("password", "")

        confirm_password = request.form.get("confirm_password", "")

        if len(password) < 4:

            flash("Password must be at least 4 characters.")

            return redirect(url_for("reset_password", token=token))

        if password != confirm_password:

            flash("Passwords do not match.")

            return redirect(url_for("reset_password", token=token))

        user_doc.reference.update({
            "password": generate_password_hash(password),
            "reset_token": None,
            "reset_token_expires": None
        })

        flash("Your password has been reset. You can log in now.")

        return redirect(url_for("citizen_login"))

    return render_template("reset_password.html", token=token)


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
# DELETE MY ACCOUNT (citizen)
# =========================================================

@app.route("/delete-account", methods=["POST"])
def delete_account():

    if not session.get("citizen_id"):

        return redirect(url_for("citizen_login"))

    citizen_id = session["citizen_id"]

    password = request.form.get("password", "")

    user_doc = db.collection("users").document(citizen_id).get()

    if not user_doc.exists:

        session.pop("citizen_id", None)

        session.pop("citizen_username", None)

        return redirect(url_for("citizen_login"))

    user = user_doc.to_dict()

    # Require the password again as a safety check before permanently
    # deleting the account — a confirm dialog alone is easy to click
    # through by accident.
    if not check_password_hash(user.get("password", ""), password):

        flash("Incorrect password. Your account was NOT deleted.")

        return redirect(url_for("profile"))

    db.collection("users").document(citizen_id).delete()

    session.pop("citizen_id", None)

    session.pop("citizen_username", None)

    flash("Your account has been deleted. We're sad to see you go! 🌱")

    return redirect(url_for("citizen_login"))


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

    image_url = upload_image_to_storage(image, extension)

    report_number = get_next_report_number()

    db.collection("complaints").add({
        "report_number": report_number,
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

            session.permanent = True

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
# MANAGE CITIZENS (admin) — view + delete citizen accounts
# =========================================================

@app.route("/admin/users")
def admin_users():

    if not session.get("admin_logged_in"):

        return redirect(url_for("login"))

    user_docs = (
        db.collection("users")
        .order_by("username")
        .stream()
    )

    users = []

    for doc in user_docs:

        data = doc.to_dict()

        data["id"] = doc.id

        badge_icon, badge_name = get_badge(data.get("points", 0))

        data["badge_icon"] = badge_icon

        data["badge_name"] = badge_name

        users.append(data)

    return render_template("admin_users.html", users=users)


@app.route("/admin/delete-user/<user_id>", methods=["POST"])
def admin_delete_user(user_id):

    if not session.get("admin_logged_in"):

        return redirect(url_for("login"))

    user_ref = db.collection("users").document(user_id)

    if not user_ref.get().exists:

        flash("That account no longer exists.")

        return redirect(url_for("admin_users"))

    user_ref.delete()

    flash("🗑️ Citizen account deleted.")

    return redirect(url_for("admin_users"))


@app.route("/admin/warn-user/<user_id>", methods=["POST"])
def admin_warn_user(user_id):

    if not session.get("admin_logged_in"):

        return redirect(url_for("login"))

    warning_message = request.form.get("warning_message", "").strip()

    if not warning_message:

        flash("Please write a warning message before sending.")

        return redirect(url_for("admin_users"))

    user_ref = db.collection("users").document(user_id)

    user_doc = user_ref.get()

    if not user_doc.exists:

        flash("That account no longer exists.")

        return redirect(url_for("admin_users"))

    user = user_doc.to_dict()

    user_ref.update({
        "warning_message": warning_message,
        "warning_issued_at": firestore.SERVER_TIMESTAMP,
        "warning_acknowledged": False
    })

    email = user.get("email")

    if email:

        send_email(
            to_email=email,
            subject="⚠️ Warning from CleanCity Municipality",
            body_text=(
                f"Hi {user.get('username', 'Citizen')},\n\n"
                "The CleanCity municipality team has issued a warning "
                "on your account:\n\n"
                f'"{warning_message}"\n\n'
                "Please log in and acknowledge this warning. If the "
                "issue isn't resolved, your account may be deleted.\n\n"
                "— CleanCity Municipality"
            )
        )

    flash(f"⚠️ Warning sent to {user.get('username', 'the citizen')}.")

    return redirect(url_for("admin_users"))


# =========================================================
# ACKNOWLEDGE WARNING (citizen)
# =========================================================

@app.route("/acknowledge-warning", methods=["POST"])
def acknowledge_warning():

    if not session.get("citizen_id"):

        return redirect(url_for("citizen_login"))

    citizen_id = session["citizen_id"]

    db.collection("users").document(citizen_id).update({
        "warning_acknowledged": True
    })

    flash("Thanks for acknowledging the warning.")

    return redirect(request.referrer or url_for("home"))


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
# DELETE COMPLAINT (admin only)
# =========================================================

@app.route("/delete/<complaint_id>", methods=["POST"])
def delete_complaint(complaint_id):

    if not session.get("admin_logged_in"):

        return redirect(url_for("login"))

    complaint_ref = db.collection("complaints").document(complaint_id)

    if not complaint_ref.get().exists:

        flash("That report no longer exists.")

        return redirect(url_for("admin"))

    complaint_ref.delete()

    flash("🗑️ Report deleted.")

    return redirect(url_for("admin"))


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    app.run(host="0.0.0.0", port=port, debug=debug_mode)
