import os, uuid, json, datetime
import firebase_admin
from firebase_admin import credentials, firestore
import cloudinary, cloudinary.uploader
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv(); BASE=os.path.dirname(os.path.abspath(__file__))
app=Flask(__name__); app.secret_key=os.getenv('SECRET_KEY','clean-city-local-secret-change-me')
app.jinja_env.globals['category_label']=lambda ck,sk: category_label(ck,sk)
app.config['PERMANENT_SESSION_LIFETIME']=datetime.timedelta(days=14)
ALLOWED={'png','jpg','jpeg','gif','webp'}
ADMIN_USERNAME=os.getenv('ADMIN_USERNAME','admin'); ADMIN_PASSWORD_HASH=os.getenv('ADMIN_PASSWORD_HASH','')

# ---- Firebase / Firestore ----
_cred_json=os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
if _cred_json and not firebase_admin._apps:
 firebase_admin.initialize_app(credentials.Certificate(json.loads(_cred_json)))
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
   'garbage-vehicle':{'title':'Garbage Vehicle Not Arrived','enabled':False},
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
   'no-electricity':{'title':'No Electricity in Public Toilet','enabled':False},
   'no-water':{'title':'No Water Supply in Public Toilet','enabled':False},
   'blockage':{'title':'Blockage in Public Toilet','enabled':True},
   'uncleaned':{'title':'Uncleaning Public Toilet','enabled':True},
   'fecal-disposal':{'title':'Improper Disposal of Fecal Waste / Septage','enabled':True},
 }},
 'water':{'title':'Water, Drainage & Miscellaneous','icon':'💧','subcats':{
   'sewerage-overflow':{'title':'Overflow of Sewerage or Storm Water','enabled':True},
   'stagnant-water':{'title':'Stagnant Water on Road / Open Area','enabled':True},
   'septic-overflow':{'title':'Overflow of Septic Tanks','enabled':True},
   'dead-animal':{'title':'Removal of Dead Animals','enabled':True},
   'other-complaint':{'title':'Complaint Other Citizen Make','enabled':False},
 }},
}
def category_label(cat_key,sub_key):
 cat=CATEGORIES.get(cat_key)
 if not cat: return ''
 sub=cat['subcats'].get(sub_key)
 if not sub: return cat['icon']+' '+cat['title']
 return cat['icon']+' '+sub['title']

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
def parse_dt(s):
 try: return datetime.datetime.fromisoformat(s) if s else None
 except Exception: return None
def save_image(f):
 result=cloudinary.uploader.upload(f, folder='clean-city-reports')
 return result['secure_url']

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
 if not sub['enabled']: return render_template('report_unavailable.html',cat_key=cat_key,category=cat,subcategory=sub)
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
  uid=uuid.uuid4().hex
  db.collection('users').document(uid).set({'username':u,'email':e,'password':generate_password_hash(p),'points':0,'reset_token':None,'reset_token_expires':None,'created_at':now_iso()})
  session['prefill_username']=u; session['prefill_password']=p; flash('Account created!'); return redirect(url_for('citizen_login'))
 return render_template('register.html')
@app.route('/citizen-login',methods=['GET','POST'])
def citizen_login():
 if request.method=='POST':
  u=request.form.get('username','').strip(); p=request.form.get('password','')
  snap=next(db.collection('users').where('username','==',u).limit(1).stream(),None)
  x=_doc(snap) if snap else None
  if x and check_password_hash(x['password'],p): session.permanent=True; session['citizen_id']=x['id']; session['citizen_username']=x['username']; return redirect(url_for('home'))
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
@app.route('/submit',methods=['POST'])
def submit():
 if not session.get('citizen_id'): return redirect(url_for('citizen_login'))
 cat_key=request.form.get('category',''); sub_key=request.form.get('subcategory','')
 cat=CATEGORIES.get(cat_key); sub=cat['subcats'].get(sub_key) if cat else None
 if not cat or not sub or not sub['enabled']: flash('Please choose a valid, available complaint type.'); return redirect(url_for('home'))
 f=request.files.get('image')
 if not f or not f.filename or not allowed(f.filename): flash('Please upload a valid image.'); return redirect(url_for('report_subcategory',cat_key=cat_key,sub_key=sub_key))
 img=save_image(f)
 num=_count(db.collection('complaints'))+1; cid=uuid.uuid4().hex
 db.collection('complaints').document(cid).set({
  'report_number':num,'name':session.get('citizen_username','Citizen'),
  'description':request.form.get('description','')[:1000],'location':request.form.get('location',''),
  'image':img,'status':'Reported','coordinates':request.form.get('coordinates',''),
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
  tiles.append({'cat_key':cat_key,'icon':cat['icon'],'title':cat['title'],'pending':total-resolved-denied})
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
  cat_title='🗂️ Uncategorized'
 else:
  cat=CATEGORIES.get(cat_key)
  if not cat: return redirect(url_for('admin'))
  comps=[_doc(d) for d in db.collection('complaints').where('category','==',cat_key).stream()]
  cat_title=cat['icon']+' '+cat['title']
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