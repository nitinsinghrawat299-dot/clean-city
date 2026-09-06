import os, sqlite3, uuid, datetime, secrets, smtplib, ssl
from email.mime.text import MIMEText
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv(); BASE=os.path.dirname(os.path.abspath(__file__))
app=Flask(__name__); app.secret_key=os.getenv('SECRET_KEY','clean-city-local-secret-change-me')
app.config['PERMANENT_SESSION_LIFETIME']=datetime.timedelta(days=14)
UPLOAD=os.path.join(BASE,'static','uploads'); os.makedirs(UPLOAD,exist_ok=True)
DB=os.path.join(BASE,'clean_city.db'); ALLOWED={'png','jpg','jpeg','gif','webp'}
ADMIN_USERNAME=os.getenv('ADMIN_USERNAME','admin'); ADMIN_PASSWORD_HASH=os.getenv('ADMIN_PASSWORD_HASH','')

def conn():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
 c=conn(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE,email TEXT UNIQUE,password TEXT,points INTEGER DEFAULT 0,reset_token TEXT,reset_token_expires TEXT,created_at TEXT);CREATE TABLE IF NOT EXISTS complaints(id TEXT PRIMARY KEY,report_number INTEGER UNIQUE,name TEXT,description TEXT,location TEXT,image TEXT,status TEXT,coordinates TEXT,address TEXT,citizen_id TEXT,points_awarded INTEGER DEFAULT 0,denial_reason TEXT DEFAULT '',created_at TEXT);'''); c.commit(); c.close()
init_db()
def rows(q,p=()):
 c=conn(); r=c.execute(q,p).fetchall(); c.close(); return r
def one(q,p=()):
 c=conn(); r=c.execute(q,p).fetchone(); c.close(); return r
def run(q,p=()):
 c=conn(); c.execute(q,p); c.commit(); c.close()
def badge(points):
 return ('🌱','Green Starter') if points<20 else ('🌿','Eco Hero') if points<50 else ('🏆','Clean City Champion')
def allowed(n): return '.' in n and n.rsplit('.',1)[1].lower() in ALLOWED

def save_image(f):
 ext=f.filename.rsplit('.',1)[1].lower(); name=f'{uuid.uuid4().hex}.{ext}'; f.save(os.path.join(UPLOAD,name)); return url_for('static',filename='uploads/'+name)

@app.route('/')
def home():
 if session.get('citizen_id'): return render_template('index.html')
 stats={'reports':str(one('SELECT COUNT(*) n FROM complaints')['n']),'users':str(one('SELECT COUNT(*) n FROM users')['n']),'points':str(one('SELECT COALESCE(SUM(points),0) n FROM users')['n']),'areas':str(one("SELECT COUNT(*) n FROM complaints WHERE status='Resolved'")['n'])}; return render_template('landing.html',stats=stats)
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
 u=one('SELECT * FROM users WHERE id=?',(session['citizen_id'],)); reps=rows('SELECT * FROM complaints WHERE citizen_id=? ORDER BY created_at DESC',(session['citizen_id'],)); icon,name=badge(u['points']); return render_template('profile.html',user=dict(u),reports=[dict(x) for x in reps],badge_icon=icon,badge_name=name)
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
 f=request.files.get('image');
 if not f or not f.filename or not allowed(f.filename): flash('Please upload a valid image.'); return redirect(url_for('home'))
 num=one('SELECT COALESCE(MAX(report_number),0)+1 n FROM complaints')['n']; cid=uuid.uuid4().hex; img=save_image(f)
 run('INSERT INTO complaints VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(cid,num,session.get('citizen_username','Citizen'),request.form.get('description','')[:1000],request.form.get('location',''),img,'Reported',request.form.get('coordinates',''),request.form.get('address',''),session['citizen_id'],0,'',datetime.datetime.utcnow().isoformat())); flash('🎉 Report received!'); return redirect(url_for('profile'))
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
 q='SELECT c.*,u.username citizen_username FROM complaints c LEFT JOIN users u ON c.citizen_id=u.id ORDER BY c.created_at DESC'; return render_template('admin.html',complaints=[dict(x) for x in rows(q)])
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