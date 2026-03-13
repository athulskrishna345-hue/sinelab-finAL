from flask import Flask, request, jsonify, send_file, send_from_directory
from database import get_db, init_db, hp
from functools import wraps
from datetime import datetime, timedelta
import secrets, os, json

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['JSON_SORT_KEYS'] = False

# Initialize DB on startup (works with gunicorn)
with app.app_context():
    init_db()

BASE = os.path.dirname(__file__)

# ── Auth ────────────────────────────────────────────────────
def require_auth(f):
    @wraps(f)
    def wrap(*a, **kw):
        token = request.headers.get('X-Token') or ''
        conn  = get_db()
        row   = conn.execute(
            "SELECT s.user_id,u.name,u.email,u.role,u.department FROM sessions s "
            "JOIN users u ON s.user_id=u.id WHERE s.token=? AND s.expires_at>datetime('now')",
            (token,)).fetchone()
        conn.close()
        if not row: return jsonify({'error':'Unauthorized'}), 401
        request.uid   = row['user_id']
        request.uname = row['name']
        request.urole = row['role']
        return f(*a, **kw)
    return wrap

def log(user, action, etype='', eid=''):
    try:
        conn = get_db()
        conn.execute("INSERT INTO activity (user_name,action,entity_type,entity_id) VALUES (?,?,?,?)", (user,action,etype,str(eid)))
        conn.commit(); conn.close()
    except: pass

def rows(cur): return [dict(r) for r in cur]
def row(r):    return dict(r) if r else None

PROGRESS = {'Handover to Operations':5,'Material Planning':15,'Procurement in Progress':25,'Material Ready':35,
            'Material Dispatched':40,'Installation Scheduled':45,'Installation In Progress':65,
            'Installation Completed':80,'Net Metering in Process':88,'Awaiting Approval':90,
            'Commissioned':95,'Payment Completed':100}

def next_srv():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    conn.close()
    return f"SRV-{n+20:03d}"

# ── Static / Frontend ────────────────────────────────────────
@app.route('/')
def index():
    return send_file(os.path.join(BASE, 'templates', 'index.html'))

@app.route('/static/<path:p>')
def static_f(p):
    return send_from_directory(os.path.join(BASE, 'static'), p)

# ── Auth Routes ──────────────────────────────────────────────
@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    email = d.get('email','').strip().lower()
    pw    = d.get('password','')
    conn  = get_db()
    u = conn.execute("SELECT * FROM users WHERE email=? AND password_hash=? AND active=1",
                     (email, hp(pw))).fetchone()
    if not u:
        conn.close()
        return jsonify({'error':'Invalid email or password'}), 401
    token   = secrets.token_hex(32)
    expires = (datetime.now()+timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("INSERT INTO sessions (token,user_id,expires_at) VALUES (?,?,?)",(token,u['id'],expires))
    conn.commit(); conn.close()
    log(u['name'], 'Logged in')
    return jsonify({'token':token,'user':{'id':u['id'],'name':u['name'],'email':u['email'],'role':u['role'],'department':u['department']}})

@app.route('/api/logout', methods=['POST'])
@require_auth
def logout():
    token = request.headers.get('X-Token','')
    conn  = get_db()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/me')
@require_auth
def me():
    conn = get_db()
    u = conn.execute("SELECT id,name,email,role,department FROM users WHERE id=?", (request.uid,)).fetchone()
    conn.close()
    return jsonify({'user': row(u)})

# ── Dashboard ────────────────────────────────────────────────
@app.route('/api/dashboard')
@require_auth
def dashboard():
    conn = get_db()
    def cnt(sql, *p): return conn.execute(sql,p).fetchone()[0]
    stats = {
        'leads_total':    cnt("SELECT COUNT(*) FROM leads"),
        'leads_new':      cnt("SELECT COUNT(*) FROM leads WHERE status IN ('New Lead','Contacted')"),
        'active_projects':cnt("SELECT COUNT(*) FROM projects WHERE status NOT IN ('Commissioned','Payment Completed')"),
        'commissioned':   cnt("SELECT COUNT(*) FROM projects WHERE status IN ('Commissioned','Payment Completed')"),
        'open_tickets':   cnt("SELECT COUNT(*) FROM tickets WHERE status NOT IN ('Resolved','Closed')"),
        'high_tickets':   cnt("SELECT COUNT(*) FROM tickets WHERE priority='high' AND status NOT IN ('Resolved','Closed')"),
        'customers':      cnt("SELECT COUNT(*) FROM customers"),
    }
    funnel = {
        'total':      stats['leads_total'],
        'qualified':  cnt("SELECT COUNT(*) FROM leads WHERE status IN ('Qualified','Site Survey Scheduled','Site Survey Completed','Quotation Under Preparation','Quotation Sent','Negotiation','Order Confirmed')"),
        'surveyed':   cnt("SELECT COUNT(*) FROM leads WHERE status IN ('Site Survey Completed','Quotation Under Preparation','Quotation Sent','Negotiation','Order Confirmed')"),
        'quoted':     cnt("SELECT COUNT(*) FROM leads WHERE status IN ('Quotation Sent','Negotiation','Order Confirmed')"),
        'negotiation':cnt("SELECT COUNT(*) FROM leads WHERE status IN ('Negotiation','Order Confirmed')"),
        'confirmed':  cnt("SELECT COUNT(*) FROM leads WHERE status='Order Confirmed'"),
    }
    sla   = rows(conn.execute("SELECT id,name,location,type,status,telecaller FROM leads WHERE status IN ('New Lead','Site Survey Completed','Quotation Under Preparation') ORDER BY id DESC LIMIT 8").fetchall())
    activ = rows(conn.execute("SELECT action,user_name,created_at FROM activity ORDER BY id DESC LIMIT 10").fetchall())
    dept  = {
        'telecaller':    cnt("SELECT COUNT(*) FROM leads WHERE status IN ('New Lead','Contacted')"),
        'sales_engineer':cnt("SELECT COUNT(*) FROM leads WHERE status='Site Survey Scheduled'"),
        'design_team':   cnt("SELECT COUNT(*) FROM leads WHERE status IN ('Site Survey Completed','Quotation Under Preparation')"),
        'operations':    cnt("SELECT COUNT(*) FROM projects WHERE status IN ('Handover to Operations','Material Planning','Installation Scheduled')"),
        'documentation': cnt("SELECT COUNT(*) FROM projects WHERE status='Net Metering in Process'"),
        'service_team':  stats['open_tickets'],
    }
    conn.close()
    return jsonify({'stats':stats,'funnel':funnel,'sla_alerts':sla,'activity':activ,'dept_tasks':dept})

# ── Leads ────────────────────────────────────────────────────
@app.route('/api/leads', methods=['GET'])
@require_auth
def get_leads():
    fs = request.args.get('status',''); fso = request.args.get('source','')
    ft = request.args.get('type','');   fm  = request.args.get('temp','')
    sq = request.args.get('search','')
    q  = "SELECT * FROM leads WHERE 1=1"; p = []
    if fs:  q += " AND status=?";  p.append(fs)
    if fso: q += " AND source=?";  p.append(fso)
    if ft:  q += " AND type=?";    p.append(ft)
    if fm:  q += " AND temp=?";    p.append(fm)
    if sq:  q += " AND (name LIKE ? OR phone LIKE ? OR location LIKE ?)"; p += [f'%{sq}%']*3
    q += " ORDER BY id DESC"
    conn = get_db()
    data = rows(conn.execute(q,p).fetchall())
    conn.close()
    return jsonify({'leads':data,'total':len(data)})

@app.route('/api/leads', methods=['POST'])
@require_auth
def create_lead():
    d = request.json or {}
    for f in ['name','phone','source']:
        if not d.get(f): return jsonify({'error':f+' is required'}), 400
    conn = get_db()
    conn.execute("""INSERT INTO leads (name,phone,email,location,type,source,bill,phase,roof,subsidy,kw,temp,telecaller,notes,follow_up_date,probability)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d['name'],d['phone'],d.get('email',''),d.get('location',''),d.get('type','Residential'),
         d['source'],d.get('bill',''),d.get('phase',''),d.get('roof',''),d.get('subsidy','Unknown'),
         d.get('kw',''),d.get('temp','Warm'),d.get('telecaller',''),d.get('notes',''),
         d.get('follow_up_date',''),20))
    lid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    lead = row(conn.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone())
    conn.close()
    log(request.uname, f"Created lead: {d['name']}", 'lead', lid)
    return jsonify({'lead':lead}), 201

@app.route('/api/leads/<int:lid>', methods=['GET'])
@require_auth
def get_lead(lid):
    conn = get_db()
    l = row(conn.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone())
    notes = rows(conn.execute("SELECT * FROM lead_notes WHERE lead_id=? ORDER BY id DESC", (lid,)).fetchall())
    conn.close()
    if not l: return jsonify({'error':'Not found'}), 404
    l['notes_history'] = notes
    return jsonify({'lead':l})

@app.route('/api/leads/<int:lid>', methods=['PUT'])
@require_auth
def update_lead(lid):
    d = request.json or {}
    allowed = ['name','phone','email','location','type','source','bill','phase','roof','subsidy','kw','status','temp','telecaller','notes','follow_up_date','quoted_amount','probability']
    sets=[]; params=[]
    for k in allowed:
        if k in d: sets.append(f"{k}=?"); params.append(d[k])
    if not sets: return jsonify({'error':'Nothing to update'}), 400
    sets.append("updated_at=datetime('now')"); params.append(lid)
    conn = get_db()
    conn.execute(f"UPDATE leads SET {','.join(sets)} WHERE id=?", params)
    conn.commit()
    lead = row(conn.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone())
    conn.close()
    log(request.uname, f"Updated lead: {lead['name']} → {d.get('status',lead['status'])}", 'lead', lid)
    return jsonify({'lead':lead})

@app.route('/api/leads/<int:lid>', methods=['DELETE'])
@require_auth
def delete_lead(lid):
    conn = get_db()
    l = conn.execute("SELECT name FROM leads WHERE id=?", (lid,)).fetchone()
    if not l: return jsonify({'error':'Not found'}), 404
    conn.execute("DELETE FROM leads WHERE id=?", (lid,))
    conn.commit(); conn.close()
    log(request.uname, f"Deleted lead: {l['name']}", 'lead', lid)
    return jsonify({'ok':True})

@app.route('/api/leads/<int:lid>/note', methods=['POST'])
@require_auth
def add_note(lid):
    content = (request.json or {}).get('content','').strip()
    if not content: return jsonify({'error':'Content required'}), 400
    conn = get_db()
    conn.execute("INSERT INTO lead_notes (lead_id,user_name,content) VALUES (?,?,?)", (lid,request.uname,content))
    # Also update the main notes field with latest
    conn.execute("UPDATE leads SET notes=?, updated_at=datetime('now') WHERE id=?", (content, lid))
    conn.commit(); conn.close()
    log(request.uname, f"Note added to lead #{lid}", 'lead', lid)
    return jsonify({'ok':True})

@app.route('/api/leads/import', methods=['POST'])
@require_auth
def import_leads():
    data = (request.json or {}).get('rows', [])
    if not data: return jsonify({'error':'No rows'}), 400
    today = datetime.now().strftime('%Y-%m-%d')
    imported=0; skipped=0; errors=[]
    conn = get_db()
    for i, r in enumerate(data):
        name  = str(r.get('name','')).strip()
        phone = str(r.get('phone','')).strip().replace(' ','')
        if not name or not phone:
            skipped+=1; errors.append(f"Row {i+2}: missing name/phone"); continue
        if conn.execute("SELECT id FROM leads WHERE phone=?", (phone,)).fetchone():
            skipped+=1; errors.append(f"Row {i+2}: {name} — duplicate phone"); continue
        conn.execute("""INSERT INTO leads (name,phone,source,location,type,bill,phase,roof,subsidy,kw,temp,telecaller,notes,status,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'New Lead',?)""",
            (name, phone, r.get('source','Walk-in'), r.get('location',''),
             r.get('type','Residential'), r.get('bill',''), r.get('phase',''),
             r.get('roof',''), r.get('subsidy','Unknown'), r.get('kw',''),
             r.get('temp','Warm'), r.get('telecaller',''), r.get('notes',''), today))
        imported += 1
    conn.commit(); conn.close()
    log(request.uname, f"Imported {imported} leads from Excel", 'lead')
    return jsonify({'imported':imported,'skipped':skipped,'errors':errors})

# ── Projects ─────────────────────────────────────────────────
@app.route('/api/projects', methods=['GET'])
@require_auth
def get_projects():
    fs = request.args.get('status',''); fe = request.args.get('engineer','')
    q="SELECT * FROM projects WHERE 1=1"; p=[]
    if fs: q+=" AND status=?"; p.append(fs)
    if fe: q+=" AND engineer=?"; p.append(fe)
    q+=" ORDER BY id DESC"
    conn = get_db()
    data = rows(conn.execute(q,p).fetchall())
    conn.close()
    return jsonify({'projects':data,'total':len(data)})

@app.route('/api/projects', methods=['POST'])
@require_auth
def create_project():
    d = request.json or {}
    if not d.get('name'): return jsonify({'error':'Name required'}), 400
    conn = get_db()
    conn.execute("""INSERT INTO projects (lead_id,name,location,kw,status,progress,payment,engineer,supervisor,panels,inverter,structure,notes,start_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d.get('lead_id'),d['name'],d.get('location',''),d.get('kw',''),
         d.get('status','Handover to Operations'),d.get('progress',5),
         d.get('payment','Partially Paid'),d.get('engineer','Unassigned'),
         d.get('supervisor',''),d.get('panels',''),d.get('inverter',''),
         d.get('structure',''),d.get('notes',''),datetime.now().strftime('%Y-%m-%d')))
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    proj = row(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
    conn.close()
    log(request.uname, f"Created project: {d['name']}", 'project', pid)
    return jsonify({'project':proj}), 201

@app.route('/api/projects/<int:pid>', methods=['PUT'])
@require_auth
def update_project(pid):
    d = request.json or {}
    allowed=['name','location','kw','status','progress','payment','engineer','supervisor','panels','inverter','structure','notes','net_meter_date','comm_date']
    sets=[]; params=[]
    for k in allowed:
        if k in d: sets.append(f"{k}=?"); params.append(d[k])
    if 'status' in d and d['status'] in PROGRESS and 'progress' not in d:
        sets.append('progress=?'); params.append(PROGRESS[d['status']])
    if not sets: return jsonify({'error':'Nothing to update'}), 400
    sets.append("updated_at=datetime('now')"); params.append(pid)
    conn = get_db()
    conn.execute(f"UPDATE projects SET {','.join(sets)} WHERE id=?", params)
    conn.commit()
    proj = row(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
    conn.close()
    log(request.uname, f"Project {proj['name']} → {d.get('status',proj['status'])}", 'project', pid)
    return jsonify({'project':proj})

@app.route('/api/projects/<int:pid>', methods=['DELETE'])
@require_auth
def delete_project(pid):
    if request.urole != 'admin': return jsonify({'error':'Admin only'}), 403
    conn = get_db()
    conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    conn.commit(); conn.close()
    log(request.uname, f"Project {pid} deleted", 'project', pid)
    return jsonify({'ok': True})

# ── Tickets ──────────────────────────────────────────────────
@app.route('/api/tickets', methods=['GET'])
@require_auth
def get_tickets():
    tab=request.args.get('tab','all'); fp=request.args.get('priority','')
    q="SELECT * FROM tickets WHERE 1=1"; p=[]
    if tab=='open':     q+=" AND status IN ('Open','Assigned')"
    elif tab=='progress': q+=" AND status='In Progress'"
    elif tab=='resolved': q+=" AND status IN ('Resolved','Closed')"
    if fp: q+=" AND priority=?"; p.append(fp)
    q+=" ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id DESC"
    conn = get_db()
    data = rows(conn.execute(q,p).fetchall())
    conn.close()
    return jsonify({'tickets':data,'total':len(data)})

@app.route('/api/tickets', methods=['POST'])
@require_auth
def create_ticket():
    d = request.json or {}
    if not d.get('customer_name'): return jsonify({'error':'Customer name required'}), 400
    tid  = next_srv()
    tech = d.get('technician','Unassigned')
    status = 'Assigned' if tech!='Unassigned' else 'Open'
    conn = get_db()
    conn.execute("INSERT INTO tickets (id,customer_id,customer_name,kw,location,type,priority,status,source,technician) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tid,d.get('customer_id'),d['customer_name'],d.get('kw','—'),d.get('location','—'),
         d.get('type','General inspection'),d.get('priority','medium'),status,d.get('source','Phone'),tech))
    conn.commit()
    ticket = row(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
    conn.close()
    log(request.uname, f"Ticket {tid}: {d['customer_name']} — {d.get('type','')}", 'ticket', tid)
    return jsonify({'ticket':ticket}), 201

@app.route('/api/tickets/<tid>', methods=['PUT'])
@require_auth
def update_ticket(tid):
    d = request.json or {}
    allowed=['status','priority','technician','root_cause','action_taken','spare_parts','remarks']
    sets=[]; params=[]
    for k in allowed:
        if k in d: sets.append(f"{k}=?"); params.append(d[k])
    if d.get('status')=='Resolved':
        sets.append('closed_date=?'); params.append(datetime.now().strftime('%Y-%m-%d'))
    if not sets: return jsonify({'error':'Nothing to update'}), 400
    sets.append("updated_at=datetime('now')"); params.append(tid)
    conn = get_db()
    conn.execute(f"UPDATE tickets SET {','.join(sets)} WHERE id=?", params)
    conn.commit()
    ticket = row(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
    conn.close()
    log(request.uname, f"Ticket {tid} → {d.get('status','updated')}", 'ticket', tid)
    return jsonify({'ticket':ticket})

@app.route('/api/tickets/<tid>', methods=['DELETE'])
@require_auth
def delete_ticket(tid):
    if request.urole != 'admin': return jsonify({'error':'Admin only'}), 403
    conn = get_db()
    conn.execute("DELETE FROM tickets WHERE id=?", (tid,))
    conn.commit(); conn.close()
    log(request.uname, f"Ticket {tid} deleted", 'ticket', tid)
    return jsonify({'ok': True})

# ── Customers ────────────────────────────────────────────────
@app.route('/api/customers', methods=['GET'])
@require_auth
def get_customers():
    conn = get_db()
    data = rows(conn.execute("""SELECT c.*, 
        (SELECT COUNT(*) FROM tickets t WHERE t.customer_id=c.id) as ticket_count
        FROM customers c ORDER BY c.id DESC""").fetchall())
    conn.close()
    return jsonify({'customers':data})

@app.route('/api/customers', methods=['POST'])
@require_auth
def create_customer():
    d = request.json or {}
    if not d.get('name'): return jsonify({'error':'Name required'}), 400
    conn = get_db()
    conn.execute("""INSERT INTO customers (project_id,name,location,kw,panels,inverter,comm_date,warranty_end,amc,monitoring_link,inv_login)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (d.get('project_id'),d['name'],d.get('location',''),d.get('kw',''),
         d.get('panels',''),d.get('inverter',''),d.get('comm_date',''),
         d.get('warranty_end',''),1 if d.get('amc') else 0,
         d.get('monitoring_link',''),d.get('inv_login','')))
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    customer = row(conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone())
    conn.close()
    log(request.uname, f"Added customer: {d['name']}", 'customer', cid)
    return jsonify({'customer':customer}), 201

# ── Reports ──────────────────────────────────────────────────
@app.route('/api/reports')
@require_auth
def get_reports():
    conn = get_db()
    by_src   = rows(conn.execute("SELECT source, COUNT(*) c FROM leads GROUP BY source ORDER BY c DESC").fetchall())
    by_type  = rows(conn.execute("SELECT type, COUNT(*) c FROM leads GROUP BY type ORDER BY c DESC").fetchall())
    by_stage = rows(conn.execute("SELECT status, COUNT(*) c FROM leads GROUP BY status ORDER BY c DESC").fetchall())
    monthly  = rows(conn.execute("SELECT strftime('%Y-%m',created_at) m, COUNT(*) c FROM leads GROUP BY m ORDER BY m DESC LIMIT 6").fetchall())
    total_kw = conn.execute("SELECT COALESCE(SUM(CAST(kw AS REAL)),0) FROM projects").fetchone()[0]
    confirmed= conn.execute("SELECT COUNT(*) FROM leads WHERE status='Order Confirmed'").fetchone()[0]
    total_l  = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()
    return jsonify({'by_source':by_src,'by_type':by_type,'by_stage':by_stage,
                    'monthly_leads':monthly,'total_kw':total_kw,
                    'confirmed':confirmed,'total_leads':total_l})

# ── Users ────────────────────────────────────────────────────
@app.route('/api/users', methods=['GET'])
@require_auth
def get_users():
    conn = get_db()
    data = rows(conn.execute("SELECT id,name,email,role,department,active,created_at FROM users ORDER BY id").fetchall())
    conn.close()
    return jsonify({'users':data})

@app.route('/api/users', methods=['POST'])
@require_auth
def create_user():
    if request.urole != 'admin': return jsonify({'error':'Admin only'}), 403
    d = request.json or {}
    if not d.get('email') or not d.get('password'): return jsonify({'error':'Email & password required'}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (name,email,password_hash,role,department) VALUES (?,?,?,?,?)",
                     (d.get('name',''),d['email'].lower(),hp(d['password']),d.get('role','staff'),d.get('department','Sales')))
        conn.commit()
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        u = row(conn.execute("SELECT id,name,email,role,department FROM users WHERE id=?", (uid,)).fetchone())
        conn.close()
        log(request.uname, f"Created user: {d['email']}", 'user', uid)
        return jsonify({'user':u}), 201
    except:
        conn.close()
        return jsonify({'error':'Email already exists'}), 400

# ── Search ───────────────────────────────────────────────────
@app.route('/api/search')
@require_auth
def search():
    q = request.args.get('q','').strip()
    if len(q) < 2: return jsonify({'results':[]})
    lk = f'%{q}%'
    conn = get_db()
    leads     = rows(conn.execute("SELECT id,'lead' type,name,location,status FROM leads WHERE name LIKE ? OR phone LIKE ? OR location LIKE ? LIMIT 5",(lk,lk,lk)).fetchall())
    customers = rows(conn.execute("SELECT id,'customer' type,name,location,kw FROM customers WHERE name LIKE ? OR location LIKE ? LIMIT 3",(lk,lk)).fetchall())
    tickets   = rows(conn.execute("SELECT id,'ticket' type,customer_name name,location,status FROM tickets WHERE customer_name LIKE ? OR id LIKE ? LIMIT 3",(lk,lk)).fetchall())
    conn.close()
    return jsonify({'results': leads+customers+tickets})

# ── Activity ─────────────────────────────────────────────────
@app.route('/api/activity')
@require_auth
def get_activity():
    conn = get_db()
    data = rows(conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 50").fetchall())
    conn.close()
    return jsonify({'activity':data})

# ── Health ───────────────────────────────────────────────────
@app.route('/api/health')
def health():
    return jsonify({'status':'ok','app':'Sinelab CRM','version':'2.0'})

if __name__ == '__main__':
    init_db()
    print("\n" + "="*52)
    print("  🌞  SINELAB CRM — Full Stack Backend")
    print("  URL:   http://localhost:5000")
    print("  Login: admin@sinelab.in / admin123")
    print("="*52 + "\n")
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
