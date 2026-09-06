import os, uuid, datetime, secrets, smtplib, ssl
import psycopg2, psycopg2.extras, psycopg2.errors
from email.mime.text import MIMEText
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv(); BASE=os.path.dirname(os.path.abspath(__file__))
app=Flask(__name__); app.secret_key=os.getenv('SECRET_KEY','clean-city-local-secret-change-me')
app.jinja_env.globals['category_label']=lambda ck,sk: category_label(ck,sk)
app.config['PERMANENT_SESSION_LIFETIME']=datetime.timedelta(days=14)
UPLOAD=os.path.join(BASE,'static','uploads'); os.makedirs(UPLOAD,exist_ok=True)
DATABASE_URL=os.getenv('DATABASE_URL'); ALLOWED={'png','jpg','jpeg','gif','webp'}
ADMIN_USERNAME=os.getenv('ADMIN_USERNAME','admin'); ADMIN_PASSWORD_HASH=os.getenv('ADMIN_PASSWORD_HASH','')

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

def conn():
 c=psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor); return c

def _pg(q): return q.replace('?','%s')  # lets every existing '?' placeholder work unchanged

def init_db():
 c=conn(); cur=c.cursor()
 cur.execute('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE,email TEXT UNIQUE,password TEXT,points INTEGER DEFAULT 0,reset_token TEXT,reset_token_expires TEXT,created_at TEXT)''')
 cur.execute('''CREATE TABLE IF NOT EXISTS complaints(id TEXT PRIMARY KEY,report_number INTEGER UNIQUE,name TEXT,description TEXT,location TEXT,image TEXT,status TEXT,coordinates TEXT,address TEXT,citizen_id TEXT,points_awarded INTEGER DEFAULT 0,denial_reason TEXT DEFAULT '',created_at TEXT,category TEXT,subcategory TEXT)''')
 c.commit()
 # migration for pre-existing databases created before category/subcategory existed
 for col in ('category','subcategory'):
  try:
   cur.execute(f'ALTER TABLE complaints ADD COLUMN {col} TEXT'); c.commit()
  except psycopg2.errors.DuplicateColumn:
   c.rollback()
 cur.close(); c.close()
init_db()
def rows(q,p=()):
 c=conn(); cur=c.cursor(); cur.execute(_pg(q),p); r=cur.fetchall(); cur.close(); c.close(); return r
def one(q,p=()):
 c=conn(); cur=c.cursor(); cur.execute(_pg(q),p); r=cur.fetchone(); cur.close(); c.close(); return r
def run(q,p=()):
 c=conn(); cur=c.cursor(); cur.execute(_pg(q),p); c.commit(); cur.close(); c.close()
def badge(points):
 return ('🌱','Green Starter') if points<20 else ('🌿','Eco Hero') if points<50 else ('🏆','Clean City Champion')
def allowed(n): return '.' in n and n.rsplit('.',1)[1].lower() in ALLOWED
def parse_dt(s):
 try: return datetime.datetime.fromisoformat(s) if s else None
 except Exception: return None

def save_image(f):
 ext=f.filename.rsplit('.',1)[1].lower(); name=f'{uuid.uuid4().hex}.{ext}'; f.save(os.path.join(UPLOAD,name)); return url_for('static',filename='uploads/'+name)

@app.route('/')
def home():
 if session.get('citizen_id'): return render_template('categories.html',categories=CATEGORIES)
 stats={'reports':str(one('SELECT COUNT(*) n FROM complaints')['n']),'users':str(one('SELECT COUNT(*) n FROM users')['n']),'points':str(one('SELECT COALESCE(SUM(points),0) n FROM users')['n']),'areas':str(one("SELECT COUNT(*) n FROM complaints WHERE status='Resolved'")['n'])}; return render_template('landing.html',stats=stats)
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
  if one('SELECT id FROM users WHERE username=? OR email=?',(u,e)): flash('Username or email already exists.'); return redirect(url_for('register'))
  run('INSERT INTO users VALUES(?,?,?,?,0,NULL,NULL,?)',(uuid.uuid4().hex,u,e,generate_password_hash(p),datetime.datetime.utcnow().isoformat())); session['prefill_username']=u; session['prefill_password']=p; flash('Account created!'); return redirect(url_for('citizen_login'))
 return render_template('register.html')
@app.route('/citizen-login',methods=['GET','POST'])
def citizen_login():
 if request.method=='POST':
  u=request.form.get('username','').strip(); p=request.form.get('password',''); x=one('SELECT * FROM users WHERE username=?',(u,))
  if x and check_password_hash(x['password'],p): session.permanent=True; session['citizen_id']=x['id']; session['citizen_username']=x['username']; return redirect(url_for('home'))
  flash('Incorrect username or password.')
 return render_template('citizen_login.html',prefill_username=session.pop('prefill_username',''),prefill_password=session.pop('prefill_password',''))
@app.route('/citizen-logout')
def citizen_logout(): session.clear(); return redirect(url_for('citizen_login'))
@app.route('/profile')
def profile():
 if not session.get('citizen_id'): return redirect(url_for('citizen_login'))
 u=one('SELECT * FROM users WHERE id=?',(session['citizen_id'],)); reps=rows('SELECT * FROM complaints WHERE citizen_id=? ORDER BY created_at DESC',(session['citizen_id'],)); icon,name=badge(u['points']); total=len(reps); resolved=sum(1 for x in reps if x['status']=='Resolved'); return render_template('profile.html',user=dict(u),complaints=[dict(x) for x in reps],badge_icon=icon,badge_name=name,total=total,resolved=resolved)
@app.route('/delete-account',methods=['POST'])
def delete_account():
 if session.get('citizen_id'): run('DELETE FROM users WHERE id=?',(session['citizen_id'],)); session.clear(); flash('Account deleted.')
 return redirect(url_for('citizen_login'))
@app.route('/leaderboard')
def leaderboard():
 data=[]
 for i,u in enumerate(rows('SELECT username,points FROM users ORDER BY points DESC LIMIT 20'),1):
  ic,b=badge(u['points']); data.append({'rank':i,'username':u['username'],'points':u['points'],'icon':ic,'badge':b})
 return render_template('leaderboard.html',users=data)
@app.route('/submit',methods=['POST'])
def submit():
 if not session.get('citizen_id'): return redirect(url_for('citizen_login'))
 cat_key=request.form.get('category',''); sub_key=request.form.get('subcategory','')
 cat=CATEGORIES.get(cat_key); sub=cat['subcats'].get(sub_key) if cat else None
 if not cat or not sub or not sub['enabled']: flash('Please choose a valid, available complaint type.'); return redirect(url_for('home'))
 f=request.files.get('image');
 if not f or not f.filename or not allowed(f.filename): flash('Please upload a valid image.'); return redirect(url_for('report_subcategory',cat_key=cat_key,sub_key=sub_key))
 num=one('SELECT COALESCE(MAX(report_number),0)+1 n FROM complaints')['n']; cid=uuid.uuid4().hex; img=save_image(f)
 run('INSERT INTO complaints VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(cid,num,session.get('citizen_username','Citizen'),request.form.get('description','')[:1000],request.form.get('location',''),img,'Reported',request.form.get('coordinates',''),request.form.get('address',''),session['citizen_id'],0,'',datetime.datetime.utcnow().isoformat(),cat_key,sub_key)); flash('🎉 Report received!'); return redirect(url_for('profile'))
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
 q='SELECT c.*,u.username citizen_username FROM complaints c LEFT JOIN users u ON c.citizen_id=u.id ORDER BY c.created_at DESC'
 comps=[]
 for x in rows(q):
  d=dict(x); d['created_at']=parse_dt(d['created_at']); comps.append(d)
 return render_template('admin.html',complaints=comps)
@app.route('/admin/users')
def admin_users():
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 out=[]
 for u in rows('SELECT * FROM users ORDER BY username'):
  d=dict(u); d['badge_icon'],d['badge_name']=badge(d['points']); out.append(d)
 return render_template('admin_users.html',users=out)
@app.route('/admin/delete-user/<user_id>',methods=['POST'])
def admin_delete_user(user_id): run('DELETE FROM users WHERE id=?',(user_id,)); return redirect(url_for('admin_users'))
@app.route('/update/<complaint_id>',methods=['POST'])
def update_status(complaint_id):
 if not session.get('admin_logged_in'): return redirect(url_for('login'))
 s=request.form.get('status','Reported'); r=request.form.get('reason','').strip(); c=one('SELECT * FROM complaints WHERE id=?',(complaint_id,))
 if c and s=='Resolved' and c['status']!='Resolved' and not c['points_awarded']:
  run('UPDATE users SET points=points+10 WHERE id=?',(c['citizen_id'],)); run("UPDATE complaints SET status='Resolved',points_awarded=10 WHERE id=?",(complaint_id,))
 else: run('UPDATE complaints SET status=?,denial_reason=? WHERE id=?',(s,r if s=='Denied' else c['denial_reason'],complaint_id))
 return redirect(url_for('admin'))
@app.route('/delete/<complaint_id>',methods=['POST'])
def delete_complaint(complaint_id): run('DELETE FROM complaints WHERE id=?',(complaint_id,)); return redirect(url_for('admin'))
@app.route('/paurigarhwal')
def pauri_garhwal():
 return render_template('paurigarhwal.html')

@app.route('/forgot-password',methods=['GET','POST'])
def forgot_password(): flash('Password reset email is not configured for local SQLite mode.') if request.method=='POST' else None; return redirect(url_for('citizen_login')) if request.method=='POST' else render_template('forgot_password.html')
@app.route('/reset-password/<token>',methods=['GET','POST'])
def reset_password(token): flash('Password reset is unavailable in local mode.'); return redirect(url_for('forgot_password'))
if __name__=='__main__': app.run(debug=True)
