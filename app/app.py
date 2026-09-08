import os, uuid, json, datetime
import firebase_admin
from firebase_admin import credentials, firestore
import cloudinary, cloudinary.uploader
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from translations import TRANSLATIONS

load_dotenv(); BASE=os.path.dirname(os.path.abspath(__file__))
app=Flask(__name__); app.secret_key=os.getenv('SECRET_KEY','clean-city-local-secret-change-me')
app.jinja_env.globals['category_label']=lambda ck,sk: category_label(ck,sk)
app.config['PERMANENT_SESSION_LIFETIME']=datetime.timedelta(days=14)
ALLOWED={'png','jpg','jpeg','gif','webp'}
ALLOWED_VIDEO={'mp4','mov','webm','mkv','3gp'}
ADMIN_USERNAME=os.getenv('ADMIN_USERNAME','admin'); ADMIN_PASSWORD_HASH=os.getenv('ADMIN_PASSWORD_HASH','')

# ---- Language / translation ----
def get_lang(): return session.get('lang','en')
def t(key): return TRANSLATIONS.get(get_lang(),TRANSLATIONS['en']).get(key,TRANSLATIONS['en'].get(key,key))
app.jinja_env.globals['t']=t
app.jinja_env.globals['get_lang']=get_lang
app.jinja_env.globals['cat_t']=lambda ck: t('cat_'+ck)
app.jinja_env.globals['sub_t']=lambda sk: t('sub_'+sk)
app.jinja_env.globals['status_t']=lambda s: t('status_'+(s or '').lower().replace(' ','_'))
@app.route('/set-language/<lang>')
def set_language(lang):
 if lang not in ('en','hi'): lang='en'
 session['lang']=lang
 if session.get('citizen_id'):
  db.collection('users').document(session['citizen_id']).update({'language':lang})
 dest=request.referrer or url_for('home')
 return redirect(dest)

# ---- Firebase / Firestore ----
# Credentials can come from any of, in order of priority:
#  1. FIREBASE_SERVICE_ACCOUNT_JSON  - env var containing the raw JSON key
#  2. FIREBASE_SERVICE_ACCOUNT_PATH  - explicit path to a mounted key file
#  3. GOOGLE_APPLICATION_CREDENTIALS - the standard Google env var, if you
#     pointed it at your Render Secret File's path
#  4. /etc/secrets/*.json            - Render's default Secret File mount
#     location, auto-detected so you don't have to hardcode the filename
_cred_json=os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
_cred_path=os.getenv('FIREBASE_SERVICE_ACCOUNT_PATH') or os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
if not _cred_path and os.path.isdir('/etc/secrets'):
 _found=[f for f in os.listdir('/etc/secrets') if f.lower().endswith('.json')]
 if _found: _cred_path=os.path.join('/etc/secrets',_found[0])
if not firebase_admin._apps:
 if _cred_json:
  firebase_admin.initialize_app(credentials.Certificate(json.loads(_cred_json)))
 elif _cred_path and os.path.isfile(_cred_path):
  firebase_admin.initialize_app(credentials.Certificate(_cred_path))
 else:
  # No JSON in env and no credentials file found on disk.
  # Fail loudly with a clear message instead of crashing later with a
  # confusing generic error on every request.
  raise RuntimeError(
   'Firebase is not configured: no FIREBASE_SERVICE_ACCOUNT_JSON env var, '
   'no FIREBASE_SERVICE_ACCOUNT_PATH/GOOGLE_APPLICATION_CREDENTIALS, and '
   'no .json file found under /etc/secrets. If you added a Render Secret '
   'File, double-check its mount path in the Render dashboard.'
  )
db=firestore.client()

# ---- Cloudinary ----
cloudinary.config(
 cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
 api_key=os.getenv('CLOUDINARY_API_KEY'),
 api_secret=os.getenv('CLOUDINARY_API_SECRET'),
 secure=True
)

# ---- Complaint categories & subcategories ----
# 'enabled': True subcategories use the existing Snap-Pin-Report form.
# 'enabled': False subcategories are shown in the menu but are not yet wired up (placeholder page).
CATEGORIES={
 'waste':{'title':'Waste & Garbage Management','icon':'🗑️','subcats':{
   'dirty-spot':{'title':'Cleanliness Target Unit (Dirty Spot)','enabled':True},
   'garbage-dump':{'title':'Garbage Dump','enabled':True},
   'garbage-vehicle':{'title':'Garbage Vehicle Not Arrived','enabled':True,'requires_photo':False},
   'open-burning':{'title':'Burning of Garbage in Open Space','enabled':True},
   'sweeping':{'title':'Sweeping Not Done','enabled':True},
   'dustbins':{'title':'Dustbins Not Cleaned','enabled':True},
   'debris':{'title':'Removal of Debris / Construction Material','enabled':True},
   'wild-grass':{'title':'Wild Grass Cutting','enabled':True},
 }},
 'infra':{'title':'Public Infrastructure & Street Maintenance','icon':'💡','subcats':{
   'open-manhole':{'title':'Open Manholes or Drains','enabled':True},
   'unsafe-manhole':{'title':'Unsafe Manhole Entry','enabled':True},
   'repair-streetlight':{'title':'Repair Street Light','enabled':True},
   'streetlight-required':{'title':'Street Light Required','enabled':True},
 }},
 'toilet':{'title':'Public Toilet & Sanitation Issues','icon':'🚻','subcats':{
   'open-defecation':{'title':'Open Defecation','enabled':True},
   'yellow-spot':{'title':'Yellow Spot (Public Urination Spot)','enabled':True},
   'no-electricity':{'title':'No Electricity in Public Toilet','enabled':True},
   'no-water':{'title':'No Water Supply in Public Toilet','enabled':True},
   'blockage':{'title':'Blockage in Public Toilet','enabled':True},
   'uncleaned':{'title':'Uncleaning Public Toilet','enabled':True},
   'fecal-disposal':{'title':'Improper Disposal of Fecal Waste / Septage','enabled':True},
 }},
 'water':{'title':'Water, Drainage & Miscellaneous','icon':'💧','subcats':{
   'sewerage-overflow':{'title':'Overflow of Sewerage or Storm Water','enabled':True},
   'stagnant-water':{'title':'Stagnant Water on Road / Open Area','enabled':True},
   'septic-overflow':{'title':'Overflow of Septic Tanks','enabled':True},
   'dead-animal':{'title':'Removal of Dead Animals','enabled':True},
   'other-complaint':{'title':'Complaint Other Citizen Make','enabled':True,'media_type':'photo_video_voice'},
 }},
}
def category_label(cat_key,sub_key):
 cat=CATEGORIES.get(cat_key)
 if not cat: return ''
 sub=cat['subcats'].get(sub_key)
 if not sub: return cat['icon']+' '+t('cat_'+cat_key)
 return cat['icon']+' '+t('sub_'+sub_key)

# ---- Citizen Cleanliness Survey ----
SURVEY_QUESTIONS=[
 {'id':'q1','text':'Is waste collected from your household/shop on a daily basis?','options':[
   'Yes, From household','Yes, From common collection points',
   'Yes, Collected daily but specific types of waste are collected on designated days','No']},
 {'id':'q2','text':'Does your household/shop segregate waste at least into dry and wet categories before disposal?','options':[
   'Yes, Always','Yes, Sometimes','No, Unaware of segregation','Aware but do not segregate waste']},
 {'id':'q3','text':'Does the waste collector collect and load waste into the vehicle in a segregated manner (by category), or is it mixed during collection/loading?','options':[
   'Waste is collected and loaded separately by category (segregated)',
   'Waste is collected separately but mixed during loading',
   'Waste is collected and loaded in mixed form']},
 {'id':'q4','text':'Does daily sweeping take place in your area?','options':['Yes','No']},
 {'id':'q5','text':'How would you rate the cleanliness of your residential area in terms of visible cleanliness?','options':[
   'Very poor','Poor','Average','Good','Excellent']},
 {'id':'q6','text':'How often do you see unattended garbage dumps or garbage piles near your area?','options':[
   'Never','Rarely','Sometimes','Often','Very often']},
 {'id':'q7','text':'How effective do you think the local authorities are in maintaining cleanliness in public spaces like markets, bazaars, parks, gardens or other areas?','options':[
   'Very ineffective','Ineffective','Neutral','Effective','Very effective']},
 {'id':'q8','text':'Do you see people openly urinating or defecating in public or nearby areas?','options':[
   'No, Never','Yes, Rarely','Yes, Sometimes','Yes, Frequently']},
 {'id':'q9','text':'How satisfied are you with the cleanliness and maintenance of public toilets in your area?','options':[
   'Very dissatisfied','Dissatisfied','Neutral','Satisfied','Very satisfied',
   'Have not used any public toilet recently','No public toilet available in my area']},
 {'id':'q10','text':'Are you aware of the Reduce, Reuse, Recycle (RRR) centers in your city for waste management?','options':[
   'Yes, I know about them and have used their services','Yes, I have heard of them but never used their services',
   'No, I am not aware of RRR centers']},
 {'id':'q11','text':'Who do you contact when your sewer or septic tank needs cleaning?','options':[
   'Local Municipality/ULB','Private Licensed Operator/Contractor','Private Individual/Local Laborer']},
 {'id':'q12','text':'How do you report cleanliness-related issues (e.g., garbage dumping, overflowing bins, lack of sanitation) to the local authorities?','options':[
   'I use the Swachhata App','I use a local city app','I use other mode such as helpline number/portal, social media etc.',
   'No grievance redressal mechanism works effectively','I am not aware of any Grievance Redressal Mechanism']},
 {'id':'q13','text':"How would you rate the city's response to your cleanliness-related complaints (e.g., garbage dumping, overflowing bins)?",'options':[
   '5 (The issue was addressed and resolved quickly.)','4 (The issue was resolved, but required follow-up)',
   '3 (The issue was addressed but not resolved satisfactorily.)','2 (The complaint was acknowledged, but no action was taken.)',
   '1 (The complaint was ignored or not registered.)','I did not feel the need to report any issue']},
]

# ---- Firestore helpers ----
def _doc(snap):
 if not snap or not snap.exists: return None
 d=snap.to_dict() or {}; d['id']=snap.id; return d
def _count(query):
 return query.count().get()[0][0].value
def now_iso(): return datetime.datetime.utcnow().isoformat()
def badge(points):
 return ('🌱','Green Starter') if points<20 else ('🌿','Eco Hero') if points<50 else ('🏆','Clean City Champion')
def allowed(n): return '.' in n and n.rsplit('.',1)[1].lower() in ALLOWED
def allowed_ext(n,exts): return '.' in n and n.rsplit('.',1)[1].lower() in exts
def parse_dt(s):
 try: return datetime.datetime.fromisoformat(s) if s else None
 except Exception: return None
def save_image(f):
 result=cloudinary.uploader.upload(f, folder='clean-city-reports')
 return result['secure_url']
def save_media(f,folder='clean-city-reports'):
 result=cloudinary.uploader.upload(f, folder=folder, resource_type='auto')
 return result['secure_url'], result.get('resource_type','image')

# ---- Site visit tracking (for the admin "Site Visits" widget) ----
@app.before_request
def track_visit():
 if request.endpoint=='static' or request.path.startswith('/static'): return
 if session.get('admin_logged_in'): return  # don't count the admin's own browsing
 if not session.get('_visited'):
  db.collection('visits').document(uuid.uuid4().hex).set({'created_at':now_iso()})
  session['_visited']=True

@app.context_processor
def inject_visit_stats():
 if not session.get('admin_logged_in'): return {}
 now=datetime.datetime.utcnow()
 today_start=now.replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
 week_start=(now-datetime.timedelta(days=7)).isoformat()
 month_start=(now-datetime.timedelta(days=30)).isoformat()
 visits=db.collection('visits')
 return {'visit_stats':{
  'today':_count(visits.where('created_at','>=',today_start)),
  'week':_count(visits.where('created_at','>=',week_start)),
  'month':_count(visits.where('created_at','>=',month_start)),
  'all':_count(visits),
 }}

@app.route('/')
def home():
 if session.get('citizen_id'): return render_template('categories.html',categories=CATEGORIES)
 users_pts=[u.to_dict().get('points',0) for u in db.collection('users').stream()]
 stats={
  'reports':str(_count(db.collection('complaints'))),
  'users':str(len(users_pts)),
  'points':str(sum(users_pts)),
  'areas':str(_count(db.collection('complaints').where('status','==','Resolved'))),
 }
 return render_template('landing.html',stats=stats)
@app.route('/report/<cat_key>')
def report_category(cat_key):
 if not session.get('citizen_id'): return redirect(url_for('citizen_login'))
 cat=CATEGORIES.get(cat_key)
 if not cat: return redirect(url_for('home'))
 return render_template('subcategories.html',cat_key=cat_key,category=cat)
@app.route('/report/<cat_key>/<sub_key>')
def report_subcategory(cat_key,sub_key):
 if not session.get('citizen_id'): return redirect(url_for('citizen_login'))
 cat=CATEGORIES.get(cat_key)
 sub=cat['subcats'].get(sub_key) if cat else None
 if not cat or not sub: return redirect(url_for('home'))
 if not sub['enabled']: return render_template('report_unavailable.html',cat_key=cat_key,sub_key=sub_key,category=cat,subcategory=sub)
 if sub.get('media_type')=='photo_video_voice': return render_template('report_form_media.html',cat_key=cat_key,sub_key=sub_key,category=cat,subcategory=sub)
 return render_template('report_form.html',cat_key=cat_key,sub_key=sub_key,category=cat,subcategory=sub)
@app.route('/register',methods=['GET','POST'])
def register():
 if request.method=='POST':
  u=request.form.get('username','').strip(); e=request.form.get('email','').strip().lower(); p=request.form.get('password',''); cp=request.form.get('confirm_password','')
  if len(u)<3 or u.lower() in {'admin','administrator','cleancity'}: flash('Please choose a valid username.'); return redirect(url_for('register'))
  if not e or '@' not in e or p!=cp or len(p)<4: flash('Please check your email and passwords.'); return redirect(url_for('register'))
  dup_u=next(db.collection('users').where('username','==',u).limit(1).stream(),None)
  dup_e=next(db.collection('users').where('email','==',e).limit(1).stream(),None)
  if dup_u or dup_e: flash('Username or email already exists.'); return redirect(url_for('register'))
  uid=uuid.uuid4().hex; lang=request.form.get('language','en')
  if lang not in ('en','hi'): lang='en'
  db.collection('users').document(uid).set({'username':u,'email':e,'password':generate_password_hash(p),'points':0,'reset_token':None,'reset_token_expires':None,'created_at':now_iso(),'language':lang})
  session['lang']=lang
  session['prefill_username']=u; session['prefill_password']=p; flash('Account created!'); return redirect(url_for('citizen_login'))
 return render_template('register.html')
@app.route('/citizen-login',methods=['GET','POST'])
def citizen_login():
 if request.method=='POST':
  u=request.form.get('username','').strip(); p=request.form.get('password','')
  snap=next(db.collection('users').where('username','==',u).limit(1).stream(),None)
  x=_doc(snap) if snap else None
  if x and check_password_hash(x['password'],p):
   session.permanent=True; session['citizen_id']=x['id']; session['citizen_username']=x['username']; session['lang']=x.get('language','en')
   return redirect(url_for('home'))
  flash('Incorrect username or password.')
 return render_template('citizen_login.html',prefill_username=session.pop('prefill_username',''),prefill_password=session.pop('prefill_password',''))
@app.route('/citizen-logout')
def citizen_logout(): session.clear(); return redirect(url_for('citizen_login'))
@app.route('/profile')
def profile():
 if not session.get('citizen_id'): return redirect(url_for('citizen_login'))
 u=_doc(db.collection('users').document(session['citizen_id']).get())
 if not u: session.clear(); return redirect(url_for('citizen_login'))
 reps=[_doc(d) for d in db.collection('complaints').where('citizen_id','==',session['citizen_id']).stream()]
 reps.sort(key=lambda r:r.get('created_at') or '',reverse=True)
 icon,name=badge(u.get('points',0)); total=len(reps); resolved=sum(1 for x in reps if x.get('status')=='Resolved')
 return render_template('profile.html',user=u,complaints=reps,badge_icon=icon,badge_name=name,total=total,resolved=resolved)
@app.route('/delete-account',methods=['POST'])
def delete_account():
 if session.get('citizen_id'): db.collection('users').document(session['citizen_id']).delete(); session.clear(); flash('Account deleted.')
 return redirect(url_for('citizen_login'))
@app.route('/leaderboard')
def leaderboard():
 data=[]
 docs=db.collection('users').order_by('points',direction=firestore.Query.DESCENDING).limit(20).stream()
 for i,u in enumerate(docs,1):
  ud=u.to_dict() or {}; ic,b=badge(ud.get('points',0)); data.append({'rank':i,'username':ud.get('username',''),'points':ud.get('points',0),'icon':ic,'badge':b})
 return render_template('leaderboard.html',users=data)
@app.route('/survey',methods=['GET','POST'])
def survey():
 if not session.get('citizen_id'): return redirect(url_for('citizen_login'))
 if request.method=='POST':
  answers={}; missing=False
  for q in SURVEY_QUESTIONS:
   v=request.form.get(q['id'],'').strip()
   if not v: missing=True
   answers[q['id']]=v
  if missing:
   flash('Please answer every question before submitting.')
   return render_template('survey.html',questions=SURVEY_QUESTIONS,answers=answers)
  db.collection('surveys').document(uuid.uuid4().hex).set({
   'citizen_id':session['citizen_id'],'citizen_username':session.get('citizen_username','Citizen'),
   'answers':answers,'created_at':now_iso(),
  })
  flash('🙏 Thank you — your survey response has been recorded!')
  return redirect(url_for('home'))
 return render_template('survey.html',questions=SURVEY_QUESTIONS,answers={})
@app.route('/feedback',methods=['GET','POST'])
def feedback_form():
 if not session.get('citizen_id'): return redirect(url_for('citizen_login'))
 if request.method=='POST':
  try: rating=int(request.form.get('rating',''))
  except (TypeError,ValueError): rating=None
  if rating is None or rating<0 or rating>5:
   flash('Please choose a rating from 0 to 5.')
   return redirect(url_for('feedback_form'))
  db.collection('feedback').document(uuid.uuid4().hex).set({
   'citizen_id':session['citizen_id'],'citizen_username':session.get('citizen_username','Citizen'),
   'rating':rating,'comment':request.form.get('comment','')[:1000],'created_at':now_iso(),
  })
  flash('🙏 Thanks for your feedback!')
  return redirect(url_for('home'))
 return render_template('feedback.html')
@app.route('/submit',methods=['POST'])
def submit():
 if not session.get('citizen_id'): return redirect(url_for('citizen_login'))
 cat_key=request.form.get('category',''); sub_key=request.form.get('subcategory','')
 cat=CATEGORIES.get(cat_key); sub=cat['subcats'].get(sub_key) if cat else None
 if not cat or not sub or not sub['enabled']: flash('Please choose a valid, available complaint type.'); return redirect(url_for('home'))
 img=''; media_type='image'; audio_url=''
 if sub.get('media_type')=='photo_video_voice':
  f=request.files.get('media')
  if not f or not f.filename or not (allowed(f.filename) or allowed_ext(f.filename,ALLOWED_VIDEO)):
   flash('Please attach a photo or video.'); return redirect(url_for('report_subcategory',cat_key=cat_key,sub_key=sub_key))
  img,media_type=save_media(f)
  a=request.files.get('audio')
  if a and a.filename: audio_url,_=save_media(a,folder='clean-city-voice-notes')
 elif not sub.get('requires_photo',True):
  f=request.files.get('image')
  if f and f.filename and allowed(f.filename): img=save_image(f)
 else:
  f=request.files.get('image')
  if not f or not f.filename or not allowed(f.filename): flash('Please upload a valid image.'); return redirect(url_for('report_subcategory',cat_key=cat_key,sub_key=sub_key))
  img=save_image(f)
 num=_count(db.collection('complaints'))+1; cid=uuid.uuid4().hex
 db.collection('complaints').document(cid).set({
  'report_number':num,'name':session.get('citizen_username','Citizen'),
  'description':request.form.get('description','')[:1000],'location':request.form.get('location',''),
  'image':img,'media_type':media_type,'audio':audio_url,'status':'Reported','coordinates':request.form.get('coordinates',''),
  'address':request.form.get('address',''),'citizen_id':session['citizen_id'],
  'citizen_username':session.get('citizen_username','Citizen'),'points_awarded':0,'denial_reason':'',
  'created_at':now_iso(),'category':cat_key,'subcategory':sub_key,
 })
 flash('🎉 Report received!'); return redirect(url_for('profile'))
@app.route('/login',methods=['GET','POST'])
def login():
 if request.method=='POST' and request.form.get('username')==ADMIN_USERNAME and ADMIN_PASSWORD_HASH and check_password_hash(ADMIN_PASSWORD_HASH,request.form.get('password','')): session['admin_logged_in']=True; return redirect(url_for('admin'))
 if request.method=='POST': flash('Wrong municipality username or password.')
 return render_template('login.html')
@app.route('/logout')
def logout(): session.pop('admin_logged_in',None); return redirect(url_for('login'))
@app.route('/admin')
def admin():
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 tiles=[]
 for cat_key,cat in CATEGORIES.items():
  base=db.collection('complaints').where('category','==',cat_key)
  total=_count(base); resolved=_count(base.where('status','==','Resolved')); denied=_count(base.where('status','==','Denied'))
  tiles.append({'cat_key':cat_key,'icon':cat['icon'],'title':t('cat_'+cat_key),'pending':total-resolved-denied})
 all_complaints=[d.to_dict() or {} for d in db.collection('complaints').stream()]
 uncategorized=[c for c in all_complaints if not c.get('category')]
 total_uncategorized=len(uncategorized)
 uncategorized_resolved=sum(1 for c in uncategorized if c.get('status')=='Resolved')
 uncategorized_denied=sum(1 for c in uncategorized if c.get('status')=='Denied')
 uncategorized_pending=total_uncategorized-uncategorized_resolved-uncategorized_denied
 return render_template('admin_categories.html',tiles=tiles,total_uncategorized=total_uncategorized,uncategorized_pending=uncategorized_pending)
@app.route('/admin/reports/<cat_key>')
def admin_reports(cat_key):
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 if cat_key=='uncategorized':
  comps=[_doc(d) for d in db.collection('complaints').stream()]
  comps=[c for c in comps if not c.get('category')]
  cat_title='🗂️ '+t('uncategorized')
 else:
  cat=CATEGORIES.get(cat_key)
  if not cat: return redirect(url_for('admin'))
  comps=[_doc(d) for d in db.collection('complaints').where('category','==',cat_key).stream()]
  cat_title=cat['icon']+' '+t('cat_'+cat_key)
 for c in comps: c['created_at']=parse_dt(c.get('created_at'))
 comps.sort(key=lambda c:c['created_at'] or datetime.datetime.min,reverse=True)
 return render_template('admin.html',complaints=comps,cat_title=cat_title,cat_key=cat_key)
@app.route('/admin/report/<complaint_id>')
def admin_report_detail(complaint_id):
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 c=_doc(db.collection('complaints').document(complaint_id).get())
 if not c: return redirect(url_for('admin'))
 c['created_at']=parse_dt(c.get('created_at'))
 citizen=_doc(db.collection('users').document(c['citizen_id']).get()) if c.get('citizen_id') else None
 from_cat=request.args.get('from_cat','')
 back_url=url_for('admin_reports',cat_key=from_cat) if from_cat else url_for('admin')
 return render_template('admin_report_detail.html',c=c,citizen=citizen,back_url=back_url)
@app.route('/admin/users')
def admin_users():
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 users=[_doc(d) for d in db.collection('users').stream()]
 users.sort(key=lambda u:(u.get('username') or '').lower())
 out=[]
 for u in users:
  u['badge_icon'],u['badge_name']=badge(u.get('points',0)); out.append(u)
 return render_template('admin_users.html',users=out)
@app.route('/admin/delete-user/<user_id>',methods=['POST'])
def admin_delete_user(user_id):
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 db.collection('users').document(user_id).delete()
 return redirect(url_for('admin_users'))
@app.route('/admin/surveys')
def admin_surveys():
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 subs=[_doc(d) for d in db.collection('surveys').stream()]
 for s in subs: s['created_at']=parse_dt(s.get('created_at'))
 subs.sort(key=lambda s:s['created_at'] or datetime.datetime.min,reverse=True)
 tallies=[]
 for q in SURVEY_QUESTIONS:
  counts={opt:0 for opt in q['options']}
  for s in subs:
   ans=(s.get('answers') or {}).get(q['id'])
   if ans in counts: counts[ans]+=1
  total=sum(counts.values()) or 1
  tallies.append({'id':q['id'],'text':q['text'],
   'rows':[{'label':opt,'count':counts[opt],'pct':round(counts[opt]*100/total)} for opt in q['options']]})
 return render_template('admin_surveys.html',subs=subs,tallies=tallies,total=len(subs))
@app.route('/admin/feedback')
def admin_feedback():
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 rows=[_doc(d) for d in db.collection('feedback').stream()]
 for r in rows: r['created_at']=parse_dt(r.get('created_at'))
 rows.sort(key=lambda r:r['created_at'] or datetime.datetime.min,reverse=True)
 ratings=[r.get('rating',0) for r in rows]
 avg=round(sum(ratings)/len(ratings),1) if ratings else 0
 dist={n:ratings.count(n) for n in range(6)}
 return render_template('admin_feedback.html',rows=rows,avg=avg,total=len(rows),dist=dist)
@app.route('/admin/map')
def admin_map():
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 rng=request.args.get('range','all')
 if rng not in ('today','week','month','all'): rng='all'
 now=datetime.datetime.utcnow()
 comps=[_doc(d) for d in db.collection('complaints').stream()]
 def in_range(dt):
  if not dt: return False
  if rng=='today': return dt.date()==now.date()
  if rng=='week': return dt>=now-datetime.timedelta(days=7)
  if rng=='month': return dt>=now-datetime.timedelta(days=30)
  return True
 pins=[]
 for c in comps:
  dt=parse_dt(c.get('created_at'))
  if not in_range(dt): continue
  coords=(c.get('coordinates') or '').split(',')
  if len(coords)!=2: continue
  try: lat,lng=float(coords[0]),float(coords[1])
  except ValueError: continue
  pins.append({
   'lat':lat,'lng':lng,'status':c.get('status','Reported'),
   'title':(c.get('description') or category_label(c.get('category',''),c.get('subcategory',''))or 'Report')[:80],
   'report_number':c.get('report_number'),'id':c['id'],
   'created_at':dt.strftime('%d %b %Y') if dt else '',
  })
 resolved=sum(1 for p in pins if p['status']=='Resolved')
 denied=sum(1 for p in pins if p['status']=='Denied')
 pending=len(pins)-resolved-denied
 return render_template('admin_map.html',pins=pins,rng=rng,total=len(pins),resolved=resolved,pending=pending,denied=denied)
@app.route('/update/<complaint_id>',methods=['POST'])
def update_status(complaint_id):
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 s=request.form.get('status','Reported'); r=request.form.get('reason','').strip(); cat_key=request.form.get('cat_key','')
 ref=db.collection('complaints').document(complaint_id); c=_doc(ref.get())
 if c and s=='Resolved' and c.get('status')!='Resolved' and not c.get('points_awarded'):
  db.collection('users').document(c['citizen_id']).update({'points':firestore.Increment(10)})
  ref.update({'status':'Resolved','points_awarded':10})
 elif c:
  ref.update({'status':s,'denial_reason':r if s=='Denied' else c.get('denial_reason','')})
 return redirect(url_for('admin_reports',cat_key=cat_key) if cat_key else url_for('admin'))
@app.route('/delete/<complaint_id>',methods=['POST'])
def delete_complaint(complaint_id):
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 cat_key=request.form.get('cat_key',''); db.collection('complaints').document(complaint_id).delete()
 return redirect(url_for('admin_reports',cat_key=cat_key) if cat_key else url_for('admin'))
@app.route('/paurigarhwal')
def pauri_garhwal():
 return render_template('paurigarhwal.html')

@app.route('/forgot-password',methods=['GET','POST'])
def forgot_password(): flash('Password reset email is not configured yet.') if request.method=='POST' else None; return redirect(url_for('citizen_login')) if request.method=='POST' else render_template('forgot_password.html')
@app.route('/reset-password/<token>',methods=['GET','POST'])
def reset_password(token): flash('Password reset is unavailable right now.'); return redirect(url_for('forgot_password'))
if __name__=='__main__': app.run(debug=True)