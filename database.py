import sqlite3, hashlib, os, fcntl
from datetime import datetime

DB_DIR = os.environ.get('DB_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db'))
DB_PATH = os.path.join(DB_DIR, 'sinelab.db')

def get_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def hp(pw): return hashlib.sha256(pw.encode()).hexdigest()

def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    lock_path = os.path.join(DB_DIR, 'init.lock')
    with open(lock_path, 'w') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            conn = get_db()
            c = conn.cursor()
            c.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'staff',
                department TEXT DEFAULT 'Sales',
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT DEFAULT '',
                location TEXT DEFAULT '',
                district TEXT DEFAULT '',
                type TEXT DEFAULT 'Residential',
                source TEXT DEFAULT '',
                bill TEXT DEFAULT '',
                phase TEXT DEFAULT '',
                roof TEXT DEFAULT '',
                subsidy TEXT DEFAULT 'Unknown',
                kw TEXT DEFAULT '',
                status TEXT DEFAULT 'New Lead',
                temp TEXT DEFAULT 'Warm',
                probability INTEGER DEFAULT 20,
                telecaller TEXT DEFAULT '',
                assigned_to INTEGER,
                notes TEXT DEFAULT '',
                follow_up_date TEXT DEFAULT '',
                quoted_amount TEXT DEFAULT '',
                survey_date TEXT DEFAULT '',
                survey_time TEXT DEFAULT '',
                survey_eng TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER,
                name TEXT NOT NULL,
                location TEXT DEFAULT '',
                kw TEXT DEFAULT '',
                status TEXT DEFAULT 'Handover to Operations',
                progress INTEGER DEFAULT 5,
                payment TEXT DEFAULT 'Partially Paid',
                engineer TEXT DEFAULT '',
                supervisor TEXT DEFAULT '',
                panels TEXT DEFAULT '',
                inverter TEXT DEFAULT '',
                structure TEXT DEFAULT '',
                net_meter_date TEXT DEFAULT '',
                comm_date TEXT DEFAULT '',
                start_date TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                customer_id INTEGER,
                customer_name TEXT NOT NULL,
                kw TEXT DEFAULT '',
                location TEXT DEFAULT '',
                type TEXT DEFAULT '',
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'Open',
                source TEXT DEFAULT '',
                technician TEXT DEFAULT 'Unassigned',
                root_cause TEXT DEFAULT '',
                action_taken TEXT DEFAULT '',
                spare_parts TEXT DEFAULT '',
                remarks TEXT DEFAULT '',
                closed_date TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                name TEXT NOT NULL,
                location TEXT DEFAULT '',
                kw TEXT DEFAULT '',
                panels TEXT DEFAULT '',
                inverter TEXT DEFAULT '',
                comm_date TEXT DEFAULT '',
                warranty_end TEXT DEFAULT '',
                amc INTEGER DEFAULT 0,
                monitoring_link TEXT DEFAULT '',
                inv_login TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT DEFAULT 'System',
                action TEXT NOT NULL,
                entity_type TEXT DEFAULT '',
                entity_id TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS lead_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            ''')
            conn.commit()
            if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                _seed(conn, c)
            # Migrations — safely add new columns to existing databases
            existing_cols = [row[1] for row in c.execute("PRAGMA table_info(leads)").fetchall()]
            for col in ['survey_date', 'survey_time', 'survey_eng']:
                if col not in existing_cols:
                    c.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT DEFAULT ''")
            conn.commit()
            conn.close()
            print(f"DB ready at {DB_PATH}")
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)

def _seed(conn, c):
    users = [
        ('Admin',     'admin@sinelab.in',  hp('Sinelab@123'), 'admin',      'Management'),
        ('Tele1',     'tele1@sinelab.in',  hp('tele1123'),    'telecaller', 'Sales'),
        ('Tele2',     'tele2@sinelab.in',  hp('tele2123'),    'telecaller', 'Sales'),
        ('Tele3',     'tele3@sinelab.in',  hp('tele3123'),    'telecaller', 'Sales'),
        ('Sales1',    'sales1@sinelab.in', hp('sales1123'),   'sales',      'Sales'),
        ('Sales2',    'sales2@sinelab.in', hp('sales2123'),   'sales',      'Sales'),
        ('Sales3',    'sales3@sinelab.in', hp('sales3123'),   'sales',      'Sales'),
        ('Engineer1', 'eng1@sinelab.in',   hp('eng1123'),     'engineer',   'Operations'),
        ('Engineer2', 'eng2@sinelab.in',   hp('eng2123'),     'engineer',   'Operations'),
        ('Engineer3', 'eng3@sinelab.in',   hp('eng3123'),     'engineer',   'Operations'),
    ]
    c.executemany("INSERT INTO users (name,email,password_hash,role,department) VALUES (?,?,?,?,?)", users)

    leads = [
        ('Rajesh Kumar',     '9876543210','Chennai',     'Residential','Meta Ads',   '6500', 'Single Phase','RCC Flat',     'Yes','5',   'Order Confirmed',             'Hot', 95,'Tele1', 'Advance paid. Ready for handover.'),
        ('Meena Textiles',   '9765432109','Coimbatore',  'Commercial', 'Google Ads', '45000','3-Phase',     'Metal Sheet',  'No', '25',  'Site Survey Completed',       'Warm',60,'Tele1', 'Survey done. Quotation pending.'),
        ('Kavitha R.',       '9654321098','Chennai',     'Residential','WhatsApp',   '3500', 'Single Phase','RCC Flat',     'Yes','3',   'Quotation Under Preparation', 'Warm',50,'Tele2', 'Waiting for quotation.'),
        ('Ganesh Factory',   '9543210987','Coimbatore',  'Industrial', 'Call',       '180000','3-Phase',    'Metal Sheet',  'No', '100', 'New Lead',                    'Hot', 40,'Tele1', 'Large industrial rooftop.'),
        ('Priya Residence',  '9432109876','Madurai',     'Residential','Referral',   '4200', 'Single Phase','Mangalore Tile','Yes','3.5','Quotation Sent',              'Warm',55,'Sales1','Proposal sent, deciding.'),
        ('Kumar & Sons',     '9321098765','Salem',       'Commercial', 'Website',    '22000','3-Phase',     'RCC Flat',     'No', '15',  'Negotiation',                 'Hot', 75,'Tele2', 'Final price discussion.'),
        ('Suresh Nadar',     '9210987654','Madurai',     'Residential','Meta Ads',   '5100', 'Single Phase','RCC Flat',     'Yes','4',   'Site Survey Scheduled',       'Warm',45,'Tele1', 'Survey on 10-Jan.'),
        ('Annamalai College','9109876543','Chidambaram', 'Commercial', 'Walk-in',    '75000','3-Phase',     'RCC Flat',     'No', '50',  'Qualified',                   'Warm',30,'Sales1','Budget approval pending.'),
        ('Vijaya Lakshmi',   '9098765432','Trichy',      'Residential','Referral',   '2100', 'Single Phase','RCC Flat',     'Yes','2',   'Contacted',                   'Cold',15,'Tele3', 'Not reachable, follow up.'),
        ('Selvam Agro',      '9087654321','Erode',       'Commercial', 'Google Ads', '52000','3-Phase',     'Open Land',    'No', '30',  'Quotation Sent',              'Hot', 70,'Sales1','Strong interest.'),
        ('Ramesh Dhurai',    '9076543210','Thanjavur',   'Residential','WhatsApp',   '6800', 'Single Phase','RCC Flat',     'Yes','5',   'Qualified',                   'Warm',50,'Tele2', 'Schedule site visit.'),
        ('SP Industries',    '9065432109','Tirupur',     'Industrial', 'Referral',   '140000','3-Phase',    'Metal Sheet',  'No', '75',  'Site Survey Completed',       'Hot', 80,'Sales1','Excellent prospect.'),
    ]
    for l in leads:
        c.execute("INSERT INTO leads (name,phone,location,type,source,bill,phase,roof,subsidy,kw,status,temp,probability,telecaller,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", l)

    projects = [
        (1,'S. Annadurai',     'Chennai',    '10','Commissioned',            100,'Payment Completed','Engineer1','Ram',  'Adani 440W x23', 'Solis 10kW', 'GI','2024-01-07','2024-01-07','2023-12-20','Commissioned successfully.'),
        (2,'Balaji Enterprises','Coimbatore','20','Installation In Progress', 65,'Partially Paid',   'Engineer2','Raj',  'Waaree 540W x37','Growatt 20kW','MS','',         '',          '2024-01-05','Structure done, modules in progress.'),
        (3,'Murugan Residency', 'Madurai',   '5', 'Net Metering in Process',  88,'Partially Paid',   'Engineer1','Kumar','Adani 415W x12', 'Solis 5kW',  'GI','2024-01-08','',          '2023-12-28','Application submitted.'),
        (4,'GRT Jewellers',    'Trichy',     '40','Material Dispatched',       30,'Partially Paid',   'Engineer2','Suresh','Waaree 540W x74','Huawei 40kW','GI','',        '',          '2024-01-06','Material in transit.'),
        (5,'Kaveri Mills',     'Erode',      '60','Installation Scheduled',    15,'Partially Paid',   'Engineer1','Anbu','Adani 440W x136','Solis 60kW', 'MS','',          '',          '2024-01-08','Team assigned for 15-Jan.'),
    ]
    for p in projects:
        c.execute("INSERT INTO projects (lead_id,name,location,kw,status,progress,payment,engineer,supervisor,panels,inverter,structure,net_meter_date,comm_date,start_date,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", p)

    customers = [
        (1,'Arumugam Solar',    'Chennai',    '8', 'Adani 440W x18', 'Solis 8kW',   '2023-09-15','2033-09-15',1,'https://solarmanpv.com'),
        (2,'Dharani Farms',     'Coimbatore', '15','Waaree 540W x28','Growatt 15kW', '2023-10-02','2033-10-02',0,''),
        (3,'Gowri Residence',   'Madurai',    '3', 'Adani 415W x7',  'Solis 3kW',   '2023-11-20','2033-11-20',1,'https://solarmanpv.com'),
        (4,'Nadar Cold Storage','Trichy',     '25','Waaree 540W x47','Huawei 25kW',  '2023-08-10','2033-08-10',1,''),
        (5,'Parvathi Textiles', 'Tirupur',    '12','Adani 440W x28', 'Solis 12kW',  '2023-12-05','2033-12-05',0,''),
        (1,'S. Annadurai',      'Chennai',    '10','Adani 440W x23', 'Solis 10kW',  '2024-01-07','2034-01-07',0,''),
    ]
    for cu in customers:
        c.execute("INSERT INTO customers (project_id,name,location,kw,panels,inverter,comm_date,warranty_end,amc,monitoring_link) VALUES (?,?,?,?,?,?,?,?,?,?)", cu)

    tickets = [
        ('SRV-018',1,'Arumugam Solar',    '8', 'Chennai',   'Inverter fault',  'high',  'Assigned',   'Phone',           'Engineer3'),
        ('SRV-017',2,'Dharani Farms',     '15','Coimbatore','Low generation',  'medium','In Progress', 'WhatsApp',        'Engineer2'),
        ('SRV-016',3,'Gowri Residence',   '3', 'Madurai',   'Monitoring issue','low',   'Open',        'WhatsApp',        'Unassigned'),
        ('SRV-015',4,'Nadar Cold Storage','25','Trichy',    'No generation',   'high',  'In Progress', 'Monitoring Alert','Engineer3'),
        ('SRV-014',5,'Parvathi Textiles', '12','Tirupur',   'Net meter issue', 'medium','Resolved',    'Phone',           'Engineer2'),
    ]
    c.executemany("INSERT INTO tickets (id,customer_id,customer_name,kw,location,type,priority,status,source,technician) VALUES (?,?,?,?,?,?,?,?,?,?)", tickets)

    acts = [
        ('System','Rajesh Kumar confirmed Order — 5 kWp Residential','lead','1'),
        ('System','Meena Textiles — Site Survey Completed','lead','2'),
        ('System','SRV-018 — Inverter fault raised','ticket','SRV-018'),
        ('System','S. Annadurai — 10 kWp Commissioned','project','1'),
        ('Admin','Database initialized and seeded','',''),
    ]
    c.executemany("INSERT INTO activity (user_name,action,entity_type,entity_id) VALUES (?,?,?,?)", acts)
    conn.commit()
    print("Seed data inserted.")
