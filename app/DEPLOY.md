# Deploying CleanCity to Firebase

Your app now uses **Firestore** (database) and **Firebase Storage** (images)
instead of SQLite, and runs on **Cloud Run** (Firebase Hosting can't execute
Python itself, but it can rewrite traffic to a Cloud Run service, which
gives you one URL that "feels like" Firebase Hosting).

## 1. Create the Firebase project

1. Go to https://console.firebase.google.com → **Add project**.
2. Once created, go to **Build > Firestore Database** → Create database
   (start in production mode).
3. Go to **Build > Storage** → Get started (accept default rules for now).
4. Note your **Project ID** (shown on the project overview page).
5. Your storage bucket is `PROJECT_ID.appspot.com` — put that in
   `FIREBASE_STORAGE_BUCKET`.

## 2. Get a service account key (for local testing only)

**Project Settings (gear icon) > Service accounts > Generate new private key.**
Save the downloaded JSON file somewhere safe (never commit it to git).
Set `GOOGLE_APPLICATION_CREDENTIALS` in your local `.env` to its full path.

## 3. Fill in your `.env`

Copy `.env.example` to `.env` and fill in every value — `SECRET_KEY`,
`ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` (same as before — run
`generate_password_hash.py` again), and the new `FIREBASE_STORAGE_BUCKET`.

## 4. Test locally

```powershell
pip install -r requirements.txt --break-system-packages
python app.py
```

Visit `http://127.0.0.1:5000`, register a citizen account, submit a
report — confirm it shows up in the Firebase Console under Firestore
and Storage.

## 5. Install the tools

You need the Firebase CLI and the Google Cloud CLI:

- Firebase CLI: `npm install -g firebase-tools`
- Google Cloud CLI: https://cloud.google.com/sdk/docs/install

Then log in:
```powershell
firebase login
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

## 6. Deploy the app to Cloud Run

From this folder:
```powershell
gcloud run deploy cleancity `
  --source . `
  --region us-central1 `
  --allow-unauthenticated `
  --set-env-vars SECRET_KEY=your-key,ADMIN_USERNAME=your-admin,ADMIN_PASSWORD_HASH=your-hash,FIREBASE_STORAGE_BUCKET=your-project-id.appspot.com
```

This builds your `Dockerfile` and deploys it — no `GOOGLE_APPLICATION_CREDENTIALS`
needed here, Cloud Run uses its own built-in service account automatically.

Cloud Run will print a `*.run.app` URL — the app is already live there.

## 7. Point Firebase Hosting at it (optional, for a nicer URL)

```powershell
firebase init hosting
```
When prompted, pick your project and say **no** to a single-page app,
then it'll detect and use the `firebase.json` already in this folder.

```powershell
firebase deploy --only hosting
```

Firebase will give you a `your-project.web.app` URL that proxies to
your Cloud Run service.

## 8. Give Cloud Run's service account Firestore/Storage access

Usually granted by default in the same project, but if you get
permission errors, go to **IAM** in Google Cloud Console and make sure
the Cloud Run service account has the **Cloud Datastore User** and
**Storage Object Admin** roles.

---

### What changed from the SQLite version

- `get_db()` / `init_db()` / raw SQL → replaced with Firestore
  collections `users` and `complaints` (no schema/migration needed —
  Firestore is schemaless).
- Uploaded images now go to Firebase Storage and are stored as public
  URLs in the `image` field, instead of local files in `uploads/`.
- `complaint["id"]` is now a Firestore document ID (a string) instead
  of an auto-increment integer — templates already handle this fine.
- Ordering by `id DESC` became ordering by a `created_at` server
  timestamp, since Firestore IDs aren't sequential.
