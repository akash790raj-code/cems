from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
import sqlite3, os, hashlib, uuid, json, io
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
from functools import wraps
import qrcode
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
import tempfile

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'cems_mca_2024_secret_key')

# Security headers
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# Rate limiting storage (in production, use Redis or database)
login_attempts = {}

def is_rate_limited(identifier):
    """Check if user is rate limited"""
    now = datetime.now()
    if identifier in login_attempts:
        attempts, last_attempt = login_attempts[identifier]
        # Reset attempts if more than 15 minutes have passed
        if now - last_attempt > timedelta(minutes=15):
            del login_attempts[identifier]
            return False
        # Block if more than 5 attempts in 15 minutes
        return attempts >= 5
    return False

def record_login_attempt(identifier):
    """Record a failed login attempt"""
    now = datetime.now()
    if identifier in login_attempts:
        attempts, _ = login_attempts[identifier]
        login_attempts[identifier] = (attempts + 1, now)
    else:
        login_attempts[identifier] = (1, now)

def clear_login_attempts(identifier):
    """Clear login attempts for successful login"""
    if identifier in login_attempts:
        del login_attempts[identifier]

DB        = 'cems.db'
QR_FOLDER = 'static/qrcodes'
os.makedirs(QR_FOLDER, exist_ok=True)

# ─────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────

@app.route('/')
def home():
    """Home page with project information and login options"""
    return render_template('home.html')

# ─────────────────────────────────────────────────
#  DATABASE SETUP
# ─────────────────────────────────────────────────
def get_db():
    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        raise e

def init_db():
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS students (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT NOT NULL,
                class_roll_no  TEXT NOT NULL,
                exam_roll_no   TEXT NOT NULL UNIQUE,
                father_name    TEXT NOT NULL,
                username       TEXT NOT NULL UNIQUE,
                phone          TEXT NOT NULL UNIQUE,
                password       TEXT NOT NULL,
                course         TEXT DEFAULT '',
                branch         TEXT DEFAULT '',
                year           TEXT DEFAULT '',
                email          TEXT DEFAULT '',
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT DEFAULT '',
                category    TEXT DEFAULT 'General',
                date        TEXT NOT NULL,
                time        TEXT DEFAULT '',
                venue       TEXT DEFAULT '',
                capacity    INTEGER DEFAULT 100,
                registered  INTEGER DEFAULT 0,
                fee         REAL DEFAULT 0,
                status      TEXT DEFAULT 'upcoming',
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS registrations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id    INTEGER NOT NULL,
                event_id      INTEGER NOT NULL,
                pass_id       TEXT UNIQUE,
                serial_number TEXT DEFAULT '',
                qr_path       TEXT DEFAULT '',
                pass_status   TEXT DEFAULT 'pending',
                approved_at   TEXT DEFAULT '',
                approved_by   TEXT DEFAULT '',
                registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(student_id) REFERENCES students(id),
                FOREIGN KEY(event_id)   REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                message    TEXT NOT NULL,
                is_read    INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(student_id) REFERENCES students(id)
            );
        ''')
        
        # Add new columns if they don't exist (for existing databases)
        try:
            db.execute("ALTER TABLE registrations ADD COLUMN serial_number TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        try:
            db.execute("ALTER TABLE registrations ADD COLUMN approved_by TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Seed sample events
        if db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
            sample_events = [
                ('Tech Fest 2024',   'Annual technology festival with hackathon, coding contest & robotics', 'Technical', '2024-12-20', '10:00 AM', 'Main Auditorium', 300, 0),
                ('Cultural Night',   'Grand evening of music, dance & drama by talented students',           'Cultural',  '2024-12-22', '06:00 PM', 'Open Air Theatre', 200, 50),
                ('Sports Week',      'Inter-college tournament – cricket, football & badminton',             'Sports',    '2024-12-25', '08:00 AM', 'Sports Complex',   150, 0),
                ('AI Guest Lecture', 'Industry expert on Artificial Intelligence & future of technology',    'Academic',  '2024-12-18', '11:00 AM', 'Seminar Hall',     100, 0),
            ]
            for ev in sample_events:
                db.execute("INSERT INTO events(title,description,category,date,time,venue,capacity,fee) VALUES(?,?,?,?,?,?,?,?)", ev)
        
        # Seed sample students
        if db.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0:
            sample_students = [
                ('Demo Student', 'CS001', 'MCA001', 'Demo Father', 'student1', '9876543210', hpw('pass123'), 'MCA', 'Computer Science', 'IV Semester', 'student1@demo.com'),
                ('Test User', 'CS002', 'MCA002', 'Test Father', 'student2', '9876543211', hpw('test123'), 'MCA', 'Computer Science', 'IV Semester', 'student2@demo.com'),
                ('Sample Student', 'CS003', 'MCA003', 'Sample Father', 'demo', '9876543212', hpw('demo123'), 'MCA', 'Computer Science', 'IV Semester', 'demo@demo.com'),
            ]
            for stu in sample_students:
                db.execute("INSERT INTO students(name,class_roll_no,exam_roll_no,father_name,username,phone,password,course,branch,year,email) VALUES(?,?,?,?,?,?,?,?,?,?,?)", stu)
        db.commit()

def backup_database():
    """Create a backup of the database"""
    try:
        import shutil
        from datetime import datetime
        backup_name = f"cems_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(DB, backup_name)
        return backup_name
    except Exception as e:
        print(f"Backup failed: {e}")
        return None

# ─────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────
def hpw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

init_db()

def login_required(f):
    @wraps(f)
    def dec(*a, **k):
        if 'student_id' not in session:
            return redirect(url_for('login'))
        return f(*a, **k)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*a, **k):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login'))
        return f(*a, **k)
    return dec

def notify(db, student_id, msg):
    db.execute("INSERT INTO notifications(student_id,message) VALUES(?,?)", (student_id, msg))

def _load_fonts():
    """Try Windows fonts, then Linux, then fall back to default."""
    candidates = [
        ('C:/Windows/Fonts/arialbd.ttf',  'C:/Windows/Fonts/arial.ttf'),
        ('C:/Windows/Fonts/calibrib.ttf', 'C:/Windows/Fonts/calibri.ttf'),
        ('C:/Windows/Fonts/verdanab.ttf', 'C:/Windows/Fonts/verdana.ttf'),
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    ]
    for bold_path, reg_path in candidates:
        if os.path.exists(bold_path) and os.path.exists(reg_path):
            try:
                return {
                    'big':   ImageFont.truetype(bold_path, 28),
                    'head':  ImageFont.truetype(bold_path, 14),
                    'body':  ImageFont.truetype(reg_path,  12),
                    'small': ImageFont.truetype(reg_path,  11),
                    'tiny':  ImageFont.truetype(reg_path,  10),
                }
            except Exception:
                pass
    d = ImageFont.load_default()
    return {'big': d, 'head': d, 'body': d, 'small': d, 'tiny': d}

def generate_serial_number(db, event_id):
    """Generate sequential serial number for event passes"""
    # Get the count of approved passes for this event
    count = db.execute(
        "SELECT COUNT(*) FROM registrations WHERE event_id=? AND pass_status='approved'", 
        (event_id,)
    ).fetchone()[0]
    
    # Generate serial number starting from 0001
    serial_num = f"EVEPASS{count + 1:04d}"
    return serial_num

def generate_passes_pdf(event_id, student_ids=None, exclude_rejected=False):
    """
    Generate PDF with passes for selected students
    Each pass on a separate page
    exclude_rejected: If True, exclude rejected students from bulk operations
    """
    try:
        with get_db() as db:
            # Get event details
            event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not event:
                return None, "Event not found"
            
            # Build query for students
            if student_ids:
                placeholders = ','.join(['?' for _ in student_ids])
                query = f"""
                    SELECT r.*, s.name, s.class_roll_no, s.exam_roll_no, s.father_name,
                        s.phone, s.course, s.branch, s.year
                    FROM registrations r
                    JOIN students s ON r.student_id = s.id
                    WHERE r.event_id = ? AND r.student_id IN ({placeholders})
                    ORDER BY r.registered_at
                """
                params = [event_id] + student_ids
            else:
                # Base query for all students
                query = """
                    SELECT r.*, s.name, s.class_roll_no, s.exam_roll_no, s.father_name,
                        s.phone, s.course, s.branch, s.year
                    FROM registrations r
                    JOIN students s ON r.student_id = s.id
                    WHERE r.event_id = ?
                """
                params = [event_id]
                
                # Add condition to exclude rejected students if requested
                if exclude_rejected:
                    query += " AND r.pass_status != 'rejected'"
                
                query += " ORDER BY r.registered_at"
            
            registrations = db.execute(query, params).fetchall()
            
            if not registrations:
                return None, "No registrations found"
            
            # Create temporary PDF file
            temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            temp_pdf.close()
            
            # Generate passes and collect image paths
            pass_images = []
            admin_name = session.get('admin_name', 'Administrator')
            
            for reg in registrations:
                # Generate serial number if not exists
                if not reg['serial_number']:
                    serial_number = generate_serial_number(db, event_id)
                    db.execute(
                        "UPDATE registrations SET serial_number=?, pass_status='approved', approved_at=?, approved_by=? WHERE id=?",
                        (serial_number, datetime.now().strftime('%Y-%m-%d %H:%M'), admin_name, reg['id'])
                    )
                else:
                    serial_number = reg['serial_number']
                
                # Prepare student and event data
                student = {k: reg[k] for k in ['name','class_roll_no','exam_roll_no','father_name','phone','course','branch','year']}
                event_data = {'title': event['title'], 'date': event['date'], 'time': event['time'],
                             'venue': event['venue'], 'category': event['category'], 'fee': event['fee']}
                
                # Determine pass status - if it's already rejected, keep it rejected
                current_status = reg['pass_status'] if reg['pass_status'] else 'approved'
                pass_status = 'rejected' if current_status == 'rejected' else 'approved'
                
                # Generate pass image
                pass_path = make_pass_image(student, event_data, reg['pass_id'], serial_number, pass_status=pass_status)
                pass_images.append(pass_path)
                
                # Update database with QR path
                db.execute(
                    "UPDATE registrations SET qr_path=? WHERE id=?",
                    (pass_path, reg['id'])
                )
                
                # Send notification
                notify(db, reg['student_id'],
                    f"🎟 Your pass for <b>{event['title']}</b> has been <b style='color:#4682b4'>APPROVED</b>! Serial: {serial_number}. Download from My Passes.")
            
            db.commit()
            
            # Create PDF with passes using canvas for better control
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import inch
            
            c = canvas.Canvas(temp_pdf.name, pagesize=A4)
            page_width, page_height = A4
            
            for i, img_path in enumerate(pass_images):
                if os.path.exists(img_path):
                    # Calculate image dimensions to fit nicely on page
                    img_width = 7 * inch  # 7 inches wide
                    img_height = 4 * inch  # 4 inches tall
                    
                    # Center the image on the page
                    x = (page_width - img_width) / 2
                    y = (page_height - img_height) / 2
                    
                    # Draw the image
                    c.drawImage(img_path, x, y, width=img_width, height=img_height)
                    
                    # Add page break except for last image
                    if i < len(pass_images) - 1:
                        c.showPage()
            
            c.save()
            
            return temp_pdf.name, f"Generated {len(pass_images)} passes successfully"
            
    except Exception as e:
        return None, f"Error generating PDF: {str(e)}"

def bulk_generate_passes(event_id, admin_name='Administrator'):
    """Generate passes for all pending registrations in an event"""
    results = {'success': 0, 'failed': 0, 'errors': []}
    
    with get_db() as db:
        # Get all pending registrations for this event
        pending_regs = db.execute(
            """SELECT r.*, s.name, s.class_roll_no, s.exam_roll_no, s.father_name,
                s.phone, s.course, s.branch, s.year,
                e.title, e.date, e.time, e.venue, e.category, e.fee
            FROM registrations r
            JOIN students s ON r.student_id=s.id
            JOIN events   e ON r.event_id=e.id
            WHERE r.event_id=? AND r.pass_status='pending'
            ORDER BY r.registered_at""", (event_id,)
        ).fetchall()
        
        for reg in pending_regs:
            try:
                # Generate serial number
                serial_number = generate_serial_number(db, event_id)
                
                # Prepare student and event data
                student = {k: reg[k] for k in ['name','class_roll_no','exam_roll_no','father_name','phone','course','branch','year']}
                event = {'title': reg['title'], 'date': reg['date'], 'time': reg['time'],
                        'venue': reg['venue'], 'category': reg['category'], 'fee': reg['fee']}
                
                # Generate pass image with serial number
                qr_path = make_pass_image(student, event, reg['pass_id'], serial_number, pass_status='approved')
                
                # Update registration
                db.execute(
                    "UPDATE registrations SET pass_status='approved', serial_number=?, qr_path=?, approved_at=?, approved_by=? WHERE pass_id=?",
                    (serial_number, qr_path, datetime.now().strftime('%Y-%m-%d %H:%M'), admin_name, reg['pass_id'])
                )
                
                # Send notification
                notify(db, reg['student_id'],
                    f"🎟 Your pass for <b>{reg['title']}</b> has been <b style='color:#4682b4'>APPROVED</b>! Serial: {serial_number}. Go to My Passes to download it.")
                
                results['success'] += 1
                
            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"Failed for {reg['name']}: {str(e)}")
        
        db.commit()
    
    return results
    """Try Windows fonts, then Linux, then fall back to default."""
    candidates = [
        ('C:/Windows/Fonts/arialbd.ttf',  'C:/Windows/Fonts/arial.ttf'),
        ('C:/Windows/Fonts/calibrib.ttf', 'C:/Windows/Fonts/calibri.ttf'),
        ('C:/Windows/Fonts/verdanab.ttf', 'C:/Windows/Fonts/verdana.ttf'),
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    ]
    for bold_path, reg_path in candidates:
        if os.path.exists(bold_path) and os.path.exists(reg_path):
            try:
                return {
                    'big':   ImageFont.truetype(bold_path, 28),
                    'head':  ImageFont.truetype(bold_path, 14),
                    'body':  ImageFont.truetype(reg_path,  12),
                    'small': ImageFont.truetype(reg_path,  11),
                    'tiny':  ImageFont.truetype(reg_path,  10),
                }
            except Exception:
                pass
    d = ImageFont.load_default()
    return {'big': d, 'head': d, 'body': d, 'small': d, 'tiny': d}


def _make_qr_image(data):
    """
    Generate a real, scannable QR code image.
    Rules for a reliable QR:
      - Pure black (0,0,0) modules on pure white (255,255,255) background
      - box_size chosen so the native pixel size needs NO upscaling
      - border=4 (QR spec minimum quiet zone)
      - NEVER use LANCZOS/anti-alias resize on a QR — use NEAREST only
      - Convert to '1' (1-bit) then back to 'RGB' to guarantee pure B&W pixels
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,  # L = lowest correction for more data capacity
        box_size=5,    # 5px per module for text data (more compact than URLs)
        border=4,      # 4-module quiet zone (QR spec minimum)
    )
    qr.add_data(data)
    qr.make(fit=True)

    # fill_color / back_color must be pure black & white
    qr_pil = qr.make_image(fill_color=(0, 0, 0), back_color=(255, 255, 255))

    # Convert through '1' mode to guarantee every pixel is exactly 0 or 255
    qr_rgb = qr_pil.convert('1').convert('RGB')
    return qr_rgb   # size is native, no resize applied


def _format_pass_data(student, event, pass_id, serial_number=None, pass_status='approved'):
    """
    Format student and event details as readable text for QR code.
    This text will be displayed directly when the QR is scanned.
    """
    status_text = pass_status.upper()
    if pass_status == 'rejected':
        status_text = "REJECTED - NOT VALID FOR ENTRY"
    elif pass_status == 'approved':
        status_text = "APPROVED - VALID FOR ENTRY"
    else:
        status_text = "PENDING APPROVAL"
    
    lines = [
        "=== OFFICIAL EVENT PASS ===",
        f"Serial Number: {serial_number or 'PENDING'}",
        f"Pass ID: {pass_id}",
        f"Status: {status_text}",
        "",
        "STUDENT INFORMATION:",
        f"Name: {student['name']}",
        f"Father's Name: {student['father_name']}",
        f"Class Roll No: {student['class_roll_no']}",
        f"Exam Roll No: {student['exam_roll_no']}",
        f"Phone: {student['phone']}",
        f"Course: {student['course'] if student['course'] else 'N/A'}",
        f"Branch: {student['branch'] if student['branch'] else 'N/A'}",
        f"Year: {student['year'] if student['year'] else 'N/A'}",
        "",
        "EVENT INFORMATION:",
        f"Event: {event['title']}",
        f"Date: {event['date']}",
        f"Time: {event['time'] or 'TBA'}",
        f"Venue: {event['venue']}",
        f"Category: {event['category']}",
        f"Fee: Rs.{event['fee']}" if float(event['fee'] or 0) > 0 else "Fee: FREE",
        "",
        f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}",
        f"Status: {status_text}",
        "",
    ]
    
    if pass_status == 'rejected':
        lines.extend([
            "⚠️  IMPORTANT NOTICE:",
            "• This pass has been REJECTED",
            "• NOT VALID for event entry",
            "• Contact admin for clarification",
            "",
        ])
    else:
        lines.extend([
            "IMPORTANT INSTRUCTIONS:",
            "• This pass is non-transferable",
            "• Present valid ID at venue entrance",
            "• Arrive 30 minutes before event time",
            "• Follow all event guidelines",
            "",
        ])
    
    lines.extend([
        "For verification, scan this QR code",
        f"or visit: /verify/{pass_id}"
    ])
    
    return "\n".join(lines)


def make_pass_image(student, event, pass_id, serial_number=None, verify_url=None, pass_status='approved'):
    """
    Generate a professional A5-landscape event pass PNG with light blue theme.
    Layout: left 2/3 = pass details, right 1/3 = QR panel.
    The QR contains all student and event details as readable text.
    pass_status: 'approved', 'rejected', or 'pending'
    """
    try:
        F = _load_fonts()

        # ── 1. Generate QR with student/event data as text ──────────────────────
        qr_data = _format_pass_data(student, event, pass_id, serial_number, pass_status)
        qr_img = _make_qr_image(qr_data)
        QR_NAT = qr_img.size[0]   # native QR size (square)

        # ── 2. Canvas dimensions ────────────────────────────────────────────────
        PADDING   = 16
        QR_PANEL  = QR_NAT + PADDING * 2          # right panel width
        MAIN_W    = 750                            # left content width
        W         = MAIN_W + QR_PANEL
        H         = max(QR_NAT + PADDING * 2 + 60, 450)   # tall enough for QR + labels

        # Custom color theme colors (using provided hex codes)
        if pass_status == 'rejected':
            # Red theme for rejected passes
            bg_color = (255, 235, 235)      # Light red background
            primary_color = (139, 0, 0)     # Dark red
            secondary_color = (178, 34, 34) # Fire brick
            accent_color = (205, 92, 92)    # Indian red
            dark_text = (139, 0, 0)         # Dark red text
            light_accent = (255, 235, 235)  # Light red
            white = (255, 255, 255)         # White
        else:
            # Default sage theme for approved/pending
            bg_color = (210, 225, 204)      # #d2e1cc - light sage background
            primary_color = (15, 68, 76)    # #0f444c - teal
            secondary_color = (17, 69, 56)  # #114538 - forest green
            accent_color = (94, 141, 131)   # #5e8d83 - sage green
            dark_text = (22, 29, 35)        # #161d23 - dark slate
            light_accent = (210, 225, 204)  # #d2e1cc - light sage
            white = (255, 255, 255)         # White

        img  = Image.new('RGB', (W, H), bg_color)
        draw = ImageDraw.Draw(img)

        # ── 3. Left panel gradient (custom theme based on status) ────────────────────────────
        if pass_status == 'rejected':
            # Red gradient for rejected passes
            for y in range(H):
                t = y / H
                r = int(255 - t * 20)  # 255 to 235
                g = int(235 - t * 30)  # 235 to 205
                b = int(235 - t * 30)  # 235 to 205
                draw.line([(0, y), (MAIN_W - 1, y)], fill=(r, g, b))
            
            # Subtle diagonal texture (red)
            for x in range(-H, MAIN_W + H, 60):
                draw.line([(x, 0), (x + H, H)], fill=(240, 200, 200), width=1)
        else:
            # Default sage gradient
            for y in range(H):
                t = y / H
                r = int(210 - t * 15)  # 210 to 195
                g = int(225 - t * 20)  # 225 to 205
                b = int(204 - t * 15)  # 204 to 189
                draw.line([(0, y), (MAIN_W - 1, y)], fill=(r, g, b))

            # Subtle diagonal texture
            for x in range(-H, MAIN_W + H, 60):
                draw.line([(x, 0), (x + H, H)], fill=(180, 200, 180), width=1)

        # Left accent stripe (sage green)
        draw.rectangle([0, 0, 6, H], fill=accent_color)
        # Top & bottom lines
        draw.rectangle([0, 0, MAIN_W, 4], fill=primary_color)
        draw.rectangle([0, H - 4, MAIN_W, H], fill=primary_color)

        # ── 4. Header band ───────────────────────────────────────────────────────
        header_bg = (240, 200, 200) if pass_status == 'rejected' else (190, 210, 190)
        draw.rectangle([0, 0, MAIN_W, 54], fill=header_bg)
        draw.text((18, 8),  'COLLEGE EVENT MANAGEMENT SYSTEM', font=F['head'], fill=primary_color)
        
        # Status-specific header text
        if pass_status == 'rejected':
            draw.text((18, 28), 'OFFICIAL EVENT PASS  ·  REJECTED', font=F['small'], fill=secondary_color)
        else:
            draw.text((18, 28), 'OFFICIAL EVENT PASS  ·  APPROVED', font=F['small'], fill=secondary_color)
        
        draw.line([(0, 54), (MAIN_W, 54)], fill=accent_color, width=2)

        # ── 5. Event title and serial number ────────────────────────────────────
        draw.text((18, 62), event['title'][:44], font=F['big'], fill=dark_text)
        if serial_number:
            # Serial number in top right of main panel
            serial_text = f"Serial: {serial_number}"
            draw.text((MAIN_W - 200, 62), serial_text, font=F['head'], fill=primary_color)

        # ── 6. Event meta row ────────────────────────────────────────────────────
        metas = [
            ('DATE',     event['date']),
            ('TIME',     event['time'] or 'TBA'),
            ('VENUE',    event['venue'][:22]),
            ('CATEGORY', event['category']),
            ('FEE',      f"Rs.{event['fee']}" if float(event['fee'] or 0) > 0 else 'FREE'),
        ]
        mx, my = 18, 104
        col_w = (MAIN_W - 36) // len(metas)
        for label, val in metas:
            draw.text((mx, my),      label, font=F['tiny'],  fill=secondary_color)
            draw.text((mx, my + 14), val,   font=F['small'], fill=dark_text)
            mx += col_w

        draw.line([(18, 140), (MAIN_W - 18, 140)], fill=accent_color, width=2)

        # ── 7. Student details (2-column grid) ───────────────────────────────────
        draw.text((18, 148), 'STUDENT DETAILS', font=F['head'], fill=primary_color)

        fields = [
            ('Full Name',      student['name']),
            ("Father's Name",  student['father_name']),
            ('Class Roll No',  student['class_roll_no']),
            ('Exam Roll No',   student['exam_roll_no']),
            ('Phone',          student['phone']),
            ('Course',         student['course'] if student['course'] else 'N/A'),
            ('Branch',         student['branch'] if student['branch'] else 'N/A'),
            ('Year',           student['year'] if student['year'] else 'N/A'),
        ]

        sy, row_h, col_w2 = 168, 38, (MAIN_W - 36) // 2
        for i, (label, val) in enumerate(fields):
            col = i % 2
            row = i // 2
            sx  = 18 + col * col_w2
            ry  = sy + row * row_h
            # Light background per field (status-dependent)
            field_bg = (250, 220, 220) if pass_status == 'rejected' else (200, 215, 195)
            draw.rectangle([sx - 3, ry - 1, sx + col_w2 - 6, ry + row_h - 4],
                           fill=field_bg)
            draw.text((sx,      ry + 2),  label + ':', font=F['tiny'],  fill=secondary_color)
            draw.text((sx,      ry + 15), str(val)[:36], font=F['body'], fill=dark_text)

        # ── 8. Bottom bar ────────────────────────────────────────────────────────
        bottom_bg = (240, 200, 200) if pass_status == 'rejected' else (180, 200, 180)
        draw.rectangle([0, H - 38, MAIN_W, H], fill=bottom_bg)
        pass_id_text = f'PASS ID: {pass_id.upper()}'
        if serial_number:
            pass_id_text = f'SERIAL: {serial_number} | PASS ID: {pass_id[:8].upper()}...'
        draw.text((18, H - 30), pass_id_text, font=F['tiny'], fill=primary_color)
        draw.text((18, H - 16),
                  'Non-transferable  ·  Carry valid ID  ·  Present at venue entrance',
                  font=F['tiny'], fill=secondary_color)

        # ── 9. QR panel (right side) ─────────────────────────────────────────────
        qpx = MAIN_W
        qr_panel_bg = (250, 220, 220) if pass_status == 'rejected' else (195, 215, 190)
        draw.rectangle([qpx, 0, W, H], fill=qr_panel_bg)
        draw.line([(qpx, 0), (qpx, H)], fill=primary_color, width=3)
        draw.rectangle([qpx, 0, W, 4], fill=primary_color)
        draw.rectangle([qpx, H - 4, W, H], fill=primary_color)

        # "SCAN FOR DETAILS" label
        draw.text((qpx + PADDING, 10), 'SCAN FOR', font=F['head'], fill=primary_color)
        draw.text((qpx + PADDING, 26), 'FULL DETAILS', font=F['head'], fill=primary_color)
        draw.line([(qpx + PADDING, 44), (W - PADDING, 44)], fill=accent_color, width=2)

        # ── 10. Paste QR — NO resize, NO anti-alias ──────────────────────────────
        qx = qpx + (QR_PANEL - QR_NAT) // 2
        qy = 52
        img.paste(qr_img, (qx, qy))   # pure paste, pixels untouched

        # Status-specific border around QR
        border_color = (139, 0, 0) if pass_status == 'rejected' else primary_color
        draw.rectangle([qx - 3, qy - 3, qx + QR_NAT + 3, qy + QR_NAT + 3],
                       outline=border_color, width=3)

        # ── 11. Status badge below QR ────────────────────────────────────────────
        badge_y = qy + QR_NAT + 10
        badge_bg = (139, 0, 0) if pass_status == 'rejected' else primary_color
        badge_text = '✗  REJECTED' if pass_status == 'rejected' else '✓  APPROVED'
        
        draw.rectangle([qx - 2, badge_y, qx + QR_NAT + 2, badge_y + 26],
                       fill=badge_bg)
        draw.text((qx + QR_NAT // 2 - 30, badge_y + 6),
                  badge_text, font=F['head'], fill=white)

        approved_date_y = badge_y + 34
        draw.text((qpx + PADDING, approved_date_y),
                  datetime.now().strftime('%d %b %Y'), font=F['tiny'], fill=secondary_color)
        
        # Add serial number below date if available
        if serial_number:
            draw.text((qpx + PADDING, approved_date_y + 12),
                      serial_number, font=F['tiny'], fill=primary_color)

        # ── 12. Save ─────────────────────────────────────────────────────────────
        filename = f'{serial_number}_{pass_id[:8]}' if serial_number else pass_id
        path = f'{QR_FOLDER}/{filename}.png'
        img.save(path, quality=95)
        return path
    
    except Exception as e:
        print(f"Error generating pass image: {e}")
        # Return a fallback path or raise the exception
        raise e

# ─────────────────────────────────────────────────
#  STUDENT AUTH
# ─────────────────────────────────────────────────
@app.route('/dashboard_redirect')
def index():
    if 'student_id' in session: return redirect(url_for('dashboard'))
    if session.get('is_admin'):  return redirect(url_for('admin_dashboard'))
    return redirect(url_for('home'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        identifier = request.form.get('identifier','').strip()
        password   = request.form.get('password','').strip()
        
        # Check rate limiting
        if is_rate_limited(identifier):
            flash('Too many failed login attempts. Please try again in 15 minutes.', 'error')
            return render_template('login.html')
        
        with get_db() as db:
            stu = db.execute(
                "SELECT * FROM students WHERE (username=? OR exam_roll_no=? OR phone=?) AND password=?",
                (identifier, identifier, identifier, hpw(password))
            ).fetchone()
        if stu:
            clear_login_attempts(identifier)
            session['student_id'] = stu['id']
            session['name']       = stu['name']
            flash(f"Welcome back, {stu['name']}! 🎉", 'success')
            return redirect(url_for('dashboard'))
        else:
            record_login_attempt(identifier)
            flash('Invalid credentials. Check username / exam roll no / phone and password.', 'error')
    return render_template('login.html')

@app.route('/signup', methods=['GET','POST'])
def signup():
    if request.method == 'POST':
        f  = request.form
        name        = f.get('name','').strip()
        class_roll  = f.get('class_roll_no','').strip()
        exam_roll   = f.get('exam_roll_no','').strip()
        father_name = f.get('father_name','').strip()
        username    = f.get('username','').strip()
        phone       = f.get('phone','').strip()
        password    = f.get('password','').strip()
        confirm_pw  = f.get('confirm_password','').strip()
        course      = f.get('course','').strip()
        branch      = f.get('branch','').strip()
        year        = f.get('year','').strip()
        email       = f.get('email','').strip()

        errors = []
        if not all([name, class_roll, exam_roll, father_name, username, phone, password]):
            errors.append('All required fields (*) must be filled.')
        if password != confirm_pw:
            errors.append('Passwords do not match.')
        if len(password) < 6:
            errors.append('Password must be at least 6 characters.')
        if phone and not phone.isdigit():
            errors.append('Phone number must contain only digits.')
        if len(phone) < 10:
            errors.append('Phone number must be at least 10 digits.')
        if len(username) < 3:
            errors.append('Username must be at least 3 characters.')
        if not name.replace(' ', '').isalpha():
            errors.append('Name should contain only letters and spaces.')
        if not father_name.replace(' ', '').isalpha():
            errors.append("Father's name should contain only letters and spaces.")

        if not errors:
            with get_db() as db:
                if db.execute("SELECT id FROM students WHERE username=?", (username,)).fetchone():
                    errors.append(f'Username "{username}" is already taken. Please choose another.')
                if db.execute("SELECT id FROM students WHERE phone=?", (phone,)).fetchone():
                    errors.append('This phone number is already registered.')
                if db.execute("SELECT id FROM students WHERE exam_roll_no=?", (exam_roll,)).fetchone():
                    errors.append('This Exam Roll No is already registered.')

                if not errors:
                    db.execute(
                        "INSERT INTO students(name,class_roll_no,exam_roll_no,father_name,username,phone,password,course,branch,year,email) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (name,class_roll,exam_roll,father_name,username,phone,hpw(password),course,branch,year,email)
                    )
                    db.commit()
                    flash('Account created successfully! Please login.', 'success')
                    return redirect(url_for('login'))

        for e in errors:
            flash(e, 'error')
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─────────────────────────────────────────────────
#  STUDENT DASHBOARD & EVENTS
# ─────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    q    = request.args.get('q','')
    cat  = request.args.get('cat','')
    with get_db() as db:
        # Build events query
        eq, ep = "SELECT * FROM events WHERE status='upcoming'", []
        if q:   eq += " AND title LIKE ?";    ep.append(f'%{q}%')
        if cat: eq += " AND category=?";      ep.append(cat)
        eq += " ORDER BY date"
        events  = db.execute(eq, ep).fetchall()

        my_regs = [r['event_id'] for r in db.execute(
            "SELECT event_id FROM registrations WHERE student_id=?", (session['student_id'],)).fetchall()]
        notif_count = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE student_id=? AND is_read=0",
            (session['student_id'],)).fetchone()[0]
        total_my    = db.execute("SELECT COUNT(*) FROM registrations WHERE student_id=?",
            (session['student_id'],)).fetchone()[0]
        approved    = db.execute("SELECT COUNT(*) FROM registrations WHERE student_id=? AND pass_status='approved'",
            (session['student_id'],)).fetchone()[0]
        pending     = db.execute("SELECT COUNT(*) FROM registrations WHERE student_id=? AND pass_status='pending'",
            (session['student_id'],)).fetchone()[0]
    return render_template('dashboard.html', events=events, my_regs=my_regs,
        notif_count=notif_count, total_my=total_my, approved=approved, pending=pending,
        q=q, cat=cat)

@app.route('/event/<int:eid>')
@login_required
def event_detail(eid):
    with get_db() as db:
        ev  = db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        reg = db.execute("SELECT * FROM registrations WHERE student_id=? AND event_id=?",
            (session['student_id'], eid)).fetchone()
    if not ev: return redirect(url_for('dashboard'))
    return render_template('event_detail.html', ev=ev, reg=reg)

@app.route('/register_event/<int:eid>', methods=['POST'])
@login_required
def register_event(eid):
    with get_db() as db:
        ev = db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if not ev:
            flash('Event not found.', 'error')
            return redirect(url_for('dashboard'))
        if db.execute("SELECT id FROM registrations WHERE student_id=? AND event_id=?",
                (session['student_id'], eid)).fetchone():
            flash('You are already registered for this event!', 'warning')
            return redirect(url_for('my_passes'))
        if ev['registered'] >= ev['capacity']:
            flash('Sorry, this event is full!', 'error')
            return redirect(url_for('event_detail', eid=eid))

        pass_id = str(uuid.uuid4())
        db.execute("INSERT INTO registrations(student_id,event_id,pass_id,pass_status) VALUES(?,?,?,'pending')",
            (session['student_id'], eid, pass_id))
        
        # Update event count properly
        db.execute("UPDATE events SET registered = (SELECT COUNT(*) FROM registrations WHERE event_id = ?) WHERE id = ?", (eid, eid))
        
        notify(db, session['student_id'],
            f"✅ You registered for <b>{ev['title']}</b>. Your pass is <b>pending admin approval</b>.")
        db.commit()
    flash('Registered! Your pass will be issued once admin approves it. 🎉', 'success')
    return redirect(url_for('my_passes'))

@app.route('/cancel_registration/<int:eid>', methods=['POST'])
@login_required
def cancel_registration(eid):
    with get_db() as db:
        reg = db.execute("SELECT * FROM registrations WHERE student_id=? AND event_id=?",
            (session['student_id'], eid)).fetchone()
        if reg:
            if reg['qr_path'] and os.path.exists(reg['qr_path']):
                try: os.remove(reg['qr_path'])
                except: pass
            db.execute("DELETE FROM registrations WHERE student_id=? AND event_id=?",
                (session['student_id'], eid))
            
            # Update event count properly
            db.execute("UPDATE events SET registered = (SELECT COUNT(*) FROM registrations WHERE event_id = ?) WHERE id = ?", (eid, eid))
            
            ev = db.execute("SELECT title FROM events WHERE id=?", (eid,)).fetchone()
            notify(db, session['student_id'],
                f"❌ Your registration for <b>{ev['title']}</b> has been cancelled.")
            db.commit()
            flash('Registration cancelled.', 'info')
    return redirect(url_for('my_passes'))

# ─────────────────────────────────────────────────
#  STUDENT PASSES
# ─────────────────────────────────────────────────
@app.route('/my_passes')
@login_required
def my_passes():
    with get_db() as db:
        passes = db.execute(
            """SELECT r.*, e.title, e.date, e.time, e.venue, e.category
            FROM registrations r JOIN events e ON r.event_id=e.id
            WHERE r.student_id=? ORDER BY r.registered_at DESC""",
            (session['student_id'],)).fetchall()
    return render_template('my_passes.html', passes=passes)

@app.route('/pass/<pass_id>')
@login_required
def view_pass(pass_id):
    with get_db() as db:
        reg = db.execute(
            """SELECT r.*, e.title, e.date, e.time, e.venue, e.category, e.fee
            FROM registrations r JOIN events e ON r.event_id=e.id
            WHERE r.pass_id=? AND r.student_id=?""", (pass_id, session['student_id'])).fetchone()
        stu = db.execute("SELECT * FROM students WHERE id=?", (session['student_id'],)).fetchone()
    if not reg: return redirect(url_for('my_passes'))
    return render_template('pass_detail.html', reg=reg, stu=stu)

@app.route('/download_pass/<pass_id>')
@login_required
def download_pass(pass_id):
    with get_db() as db:
        reg = db.execute("SELECT qr_path, pass_status, serial_number FROM registrations WHERE pass_id=? AND student_id=?",
            (pass_id, session['student_id'])).fetchone()
    if reg and reg['qr_path'] and os.path.exists(reg['qr_path']):
        # Allow download for both approved and rejected passes
        if reg['pass_status'] in ['approved', 'rejected']:
            # Use serial number in filename if available
            status_suffix = '_REJECTED' if reg['pass_status'] == 'rejected' else ''
            filename = f'EventPass_{reg["serial_number"]}{status_suffix}.png' if reg['serial_number'] else f'EventPass_{pass_id[:8].upper()}{status_suffix}.png'
            return send_file(reg['qr_path'], as_attachment=True, download_name=filename)
    
    if reg and reg['pass_status'] == 'pending':
        flash('Pass is still pending admin approval.', 'warning')
    else:
        flash('Pass not found or not ready for download.', 'warning')
    return redirect(url_for('my_passes'))

@app.route('/admin/download_pass/<pass_id>')
@admin_required
def admin_download_pass(pass_id):
    with get_db() as db:
        reg = db.execute("SELECT qr_path, pass_status, serial_number FROM registrations WHERE pass_id=?",
            (pass_id,)).fetchone()
    if reg and reg['qr_path'] and os.path.exists(reg['qr_path']):
        # Allow admin to download any pass (approved, rejected, or pending)
        status_suffix = ''
        if reg['pass_status'] == 'rejected':
            status_suffix = '_REJECTED'
        elif reg['pass_status'] == 'pending':
            status_suffix = '_PENDING'
        
        filename = f'EventPass_{reg["serial_number"]}{status_suffix}.png' if reg['serial_number'] else f'EventPass_{pass_id[:8].upper()}{status_suffix}.png'
        return send_file(reg['qr_path'], as_attachment=True, download_name=filename)
    
    flash('Pass not found or no pass image available.', 'error')
    return redirect(url_for('admin_passes'))

# ─────────────────────────────────────────────────
#  NOTIFICATIONS
# ─────────────────────────────────────────────────
@app.route('/notifications')
@login_required
def notifications():
    with get_db() as db:
        notifs = db.execute("SELECT * FROM notifications WHERE student_id=? ORDER BY created_at DESC",
            (session['student_id'],)).fetchall()
        db.execute("UPDATE notifications SET is_read=1 WHERE student_id=?", (session['student_id'],))
        db.commit()
    return render_template('notifications.html', notifs=notifs)

@app.route('/notif_count')
@login_required
def notif_count():
    with get_db() as db:
        c = db.execute("SELECT COUNT(*) FROM notifications WHERE student_id=? AND is_read=0",
            (session['student_id'],)).fetchone()[0]
    return jsonify({'count': c})

# ─────────────────────────────────────────────────
#  STUDENT PROFILE
# ─────────────────────────────────────────────────
@app.route('/profile', methods=['GET','POST'])
@login_required
def profile():
    with get_db() as db:
        stu = db.execute("SELECT * FROM students WHERE id=?", (session['student_id'],)).fetchone()
        if request.method == 'POST':
            db.execute("UPDATE students SET email=?,course=?,branch=?,year=? WHERE id=?",
                (request.form.get('email',''), request.form.get('course',''),
                 request.form.get('branch',''), request.form.get('year',''),
                 session['student_id']))
            db.commit()
            flash('Profile updated!', 'success')
            return redirect(url_for('profile'))
        total_regs = db.execute("SELECT COUNT(*) FROM registrations WHERE student_id=?",
            (session['student_id'],)).fetchone()[0]
    return render_template('profile.html', stu=stu, total_regs=total_regs)

# ─────────────────────────────────────────────────
#  ADMIN AUTH
# ─────────────────────────────────────────────────
ADMIN_USER = os.getenv('ADMIN_USER', 'admin')
ADMIN_PASS = hpw(os.getenv('ADMIN_PASS', 'admin123'))

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USER and hpw(request.form.get('password','')) == ADMIN_PASS:
            session['is_admin']   = True
            session['admin_name'] = 'Administrator'
            return redirect(url_for('admin_dashboard'))
        flash('Invalid admin credentials!', 'error')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

# ─────────────────────────────────────────────────
#  ADMIN DASHBOARD
# ─────────────────────────────────────────────────
@app.route('/admin')
@admin_required
def admin_dashboard():
    with get_db() as db:
        stats = {
            'total_students':  db.execute("SELECT COUNT(*) FROM students").fetchone()[0],
            'total_events':    db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            'total_regs':      db.execute("SELECT COUNT(*) FROM registrations").fetchone()[0],
            'pending_passes':  db.execute("SELECT COUNT(*) FROM registrations WHERE pass_status='pending'").fetchone()[0],
            'approved_passes': db.execute("SELECT COUNT(*) FROM registrations WHERE pass_status='approved'").fetchone()[0],
            'rejected_passes': db.execute("SELECT COUNT(*) FROM registrations WHERE pass_status='rejected'").fetchone()[0],
        }
        recent = db.execute(
            """SELECT r.*, s.name as sname, e.title as etitle
            FROM registrations r JOIN students s ON r.student_id=s.id JOIN events e ON r.event_id=e.id
            ORDER BY r.registered_at DESC LIMIT 8""").fetchall()
    return render_template('admin_dashboard.html', stats=stats, recent=recent)

# ─────────────────────────────────────────────────
#  ADMIN EVENTS
# ─────────────────────────────────────────────────
@app.route('/admin/events')
@admin_required
def admin_events():
    with get_db() as db:
        evts = db.execute("SELECT * FROM events ORDER BY date DESC").fetchall()
    return render_template('admin_events.html', events=evts)

@app.route('/admin/events/create', methods=['GET','POST'])
@admin_required
def admin_create_event():
    if request.method == 'POST':
        f = request.form
        with get_db() as db:
            db.execute("INSERT INTO events(title,description,category,date,time,venue,capacity,fee) VALUES(?,?,?,?,?,?,?,?)",
                (f['title'], f['description'], f['category'], f['date'],
                 f['time'], f['venue'], int(f.get('capacity',100)), float(f.get('fee',0))))
            stus = db.execute("SELECT id FROM students").fetchall()
            for s in stus:
                notify(db, s['id'], f"📢 New event added: <b>{f['title']}</b> on {f['date']} at {f['venue']}. Register now!")
            db.commit()
        flash('Event created! All students notified.', 'success')
        return redirect(url_for('admin_events'))
    return render_template('admin_create_event.html')

@app.route('/admin/events/edit/<int:eid>', methods=['GET','POST'])
@admin_required
def admin_edit_event(eid):
    with get_db() as db:
        ev = db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if request.method == 'POST':
            f = request.form
            db.execute("UPDATE events SET title=?,description=?,category=?,date=?,time=?,venue=?,capacity=?,fee=?,status=? WHERE id=?",
                (f['title'],f['description'],f['category'],f['date'],f['time'],
                 f['venue'],int(f['capacity']),float(f['fee']),f['status'],eid))
            db.commit()
            flash('Event updated!', 'success')
            return redirect(url_for('admin_events'))
    return render_template('admin_edit_event.html', ev=ev)

@app.route('/admin/events/delete/<int:eid>', methods=['POST'])
@admin_required
def admin_delete_event(eid):
    with get_db() as db:
        # Clean up pass images
        for r in db.execute("SELECT qr_path FROM registrations WHERE event_id=?", (eid,)).fetchall():
            if r['qr_path'] and os.path.exists(r['qr_path']):
                try: os.remove(r['qr_path'])
                except: pass
        
        # Delete registrations first (foreign key constraint)
        db.execute("DELETE FROM registrations WHERE event_id=?", (eid,))
        # Then delete the event
        db.execute("DELETE FROM events WHERE id=?", (eid,))
        db.commit()
    flash('Event deleted.', 'info')
    return redirect(url_for('admin_events'))

@app.route('/admin/events/<int:eid>/attendees')
@admin_required
def admin_attendees(eid):
    with get_db() as db:
        ev   = db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if not ev:
            flash('Event not found.', 'error')
            return redirect(url_for('admin_events'))
            
        # Get ALL attendees for this event (no LIMIT)
        atts = db.execute(
            """SELECT r.*, s.name, s.class_roll_no, s.exam_roll_no, s.father_name, s.phone, s.course, s.branch
            FROM registrations r JOIN students s ON r.student_id=s.id
            WHERE r.event_id=? ORDER BY r.registered_at""", (eid,)).fetchall()
        
        # Count statistics
        stats = {
            'total': len(atts),
            'pending': len([a for a in atts if a['pass_status'] == 'pending']),
            'approved': len([a for a in atts if a['pass_status'] == 'approved']),
            'rejected': len([a for a in atts if a['pass_status'] == 'rejected'])
        }
        
        # Debug info
        print(f"Event ID: {eid}, Total attendees found: {len(atts)}")
        for att in atts[:5]:  # Print first 5 for debugging
            print(f"  - {att['name']} ({att['pass_status']})")
        if len(atts) > 5:
            print(f"  ... and {len(atts) - 5} more")
            
    return render_template('admin_attendees.html', ev=ev, attendees=atts, stats=stats)

@app.route('/admin/events/<int:eid>/generate_passes', methods=['GET', 'POST'])
@admin_required
def generate_passes_route(eid):
    """Generate passes for selected students"""
    with get_db() as db:
        ev = db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if not ev:
            flash('Event not found.', 'error')
            return redirect(url_for('admin_events'))
        
        if request.method == 'GET':
            # Get ALL registered students for this event (no LIMIT)
            students = db.execute(
                """SELECT r.*, s.name, s.class_roll_no, s.exam_roll_no, s.father_name, s.phone
                FROM registrations r
                JOIN students s ON r.student_id = s.id
                WHERE r.event_id = ?
                ORDER BY s.name""", (eid,)
            ).fetchall()
            
            # Debug info
            print(f"Generate passes - Event ID: {eid}, Students found: {len(students)}")
            
            return render_template('admin_generate_passes.html', ev=ev, students=students)
        
        elif request.method == 'POST':
            selection_type = request.form.get('selection_type')
            
            if selection_type == 'all':
                # Generate for all registered students EXCEPT rejected ones
                pdf_path, message = generate_passes_pdf(eid, exclude_rejected=True)
            elif selection_type == 'selected':
                # Generate for selected students
                selected_ids = request.form.getlist('student_ids')
                if not selected_ids:
                    flash('Please select at least one student.', 'error')
                    return redirect(url_for('generate_passes_route', eid=eid))
                
                selected_ids = [int(sid) for sid in selected_ids]
                pdf_path, message = generate_passes_pdf(eid, selected_ids)
            else:
                flash('Invalid selection type.', 'error')
                return redirect(url_for('generate_passes_route', eid=eid))
            
            if pdf_path:
                flash(message, 'success')
                # Return PDF file for download
                return send_file(
                    pdf_path, 
                    as_attachment=True, 
                    download_name=f'Event_Passes_{ev["title"].replace(" ", "_")}.pdf',
                    mimetype='application/pdf'
                )
            else:
                flash(message, 'error')
                return redirect(url_for('generate_passes_route', eid=eid))

@app.route('/admin/events/<int:eid>/bulk_approve', methods=['POST'])
@admin_required
def bulk_approve_passes(eid):
    """Bulk approve all pending passes for an event"""
    admin_name = session.get('admin_name', 'Administrator')
    results = bulk_generate_passes(eid, admin_name)
    
    if results['success'] > 0:
        flash(f'✅ Successfully generated {results["success"]} passes!', 'success')
    if results['failed'] > 0:
        flash(f'❌ Failed to generate {results["failed"]} passes. Check logs.', 'error')
        for error in results['errors'][:3]:  # Show first 3 errors
            flash(error, 'error')
    
    return redirect(url_for('admin_attendees', eid=eid))

@app.route('/admin/events/<int:eid>/bulk_reject', methods=['POST'])
@admin_required
def bulk_reject_passes(eid):
    """Bulk reject all pending passes for an event"""
    reason = request.form.get('reason', 'Bulk rejection by admin')
    
    with get_db() as db:
        # Get all pending registrations
        pending_regs = db.execute(
            """SELECT r.student_id, r.pass_id, e.title 
            FROM registrations r JOIN events e ON r.event_id=e.id
            WHERE r.event_id=? AND r.pass_status='pending'""", (eid,)
        ).fetchall()
        
        # Update all to rejected
        db.execute(
            "UPDATE registrations SET pass_status='rejected' WHERE event_id=? AND pass_status='pending'",
            (eid,)
        )
        
        # Send notifications
        for reg in pending_regs:
            notify(db, reg['student_id'],
                f"❌ Your pass for <b>{reg['title']}</b> was rejected. Reason: {reason}")
        
        db.commit()
    
    flash(f'❌ Rejected {len(pending_regs)} pending passes.', 'info')
    return redirect(url_for('admin_attendees', eid=eid))

# ─────────────────────────────────────────────────
#  ADMIN PASSES
# ─────────────────────────────────────────────────
@app.route('/admin/passes')
@admin_required
def admin_passes():
    status = request.args.get('status', 'pending')
    with get_db() as db:
        passes = db.execute(
            """SELECT r.*, s.name, s.class_roll_no, s.exam_roll_no, s.father_name,
                s.phone, s.course, s.branch, s.year,
                e.title as event_title, e.date as event_date, e.venue, e.category, e.fee
            FROM registrations r
            JOIN students s ON r.student_id=s.id
            JOIN events e ON r.event_id=e.id
            WHERE r.pass_status=? ORDER BY r.registered_at DESC""", (status,)).fetchall()
        counts = {
            'pending':  db.execute("SELECT COUNT(*) FROM registrations WHERE pass_status='pending'").fetchone()[0],
            'approved': db.execute("SELECT COUNT(*) FROM registrations WHERE pass_status='approved'").fetchone()[0],
            'rejected': db.execute("SELECT COUNT(*) FROM registrations WHERE pass_status='rejected'").fetchone()[0],
        }
    return render_template('admin_passes.html', passes=passes, status=status, counts=counts)

@app.route('/admin/passes/approve/<pass_id>', methods=['POST'])
@admin_required
def approve_pass(pass_id):
    admin_name = session.get('admin_name', 'Administrator')
    with get_db() as db:
        row = db.execute(
            """SELECT r.*, s.name, s.class_roll_no, s.exam_roll_no, s.father_name,
                s.phone, s.course, s.branch, s.year,
                e.title, e.date, e.time, e.venue, e.category, e.fee
            FROM registrations r
            JOIN students s ON r.student_id=s.id
            JOIN events   e ON r.event_id=e.id
            WHERE r.pass_id=?""", (pass_id,)).fetchone()
        if row:
            # Generate serial number
            serial_number = generate_serial_number(db, row['event_id'])
            
            student = {k: row[k] for k in ['name','class_roll_no','exam_roll_no','father_name','phone','course','branch','year']}
            event   = {'title': row['title'], 'date': row['date'], 'time': row['time'],
                       'venue': row['venue'], 'category': row['category'], 'fee': row['fee']}
            qr_path = make_pass_image(student, event, pass_id, serial_number, pass_status='approved')
            db.execute("UPDATE registrations SET pass_status='approved', serial_number=?, qr_path=?, approved_at=?, approved_by=? WHERE pass_id=?",
                (serial_number, qr_path, datetime.now().strftime('%Y-%m-%d %H:%M'), admin_name, pass_id))
            notify(db, row['student_id'],
                f"🎟 Your pass for <b>{row['title']}</b> has been <b style='color:#34d399'>APPROVED</b>! Serial: {serial_number}. Go to My Passes to download it.")
            db.commit()
    flash('Pass approved and QR pass generated!', 'success')
    return redirect(url_for('admin_passes'))

@app.route('/admin/passes/reject/<pass_id>', methods=['POST'])
@admin_required
def reject_pass(pass_id):
    reason = request.form.get('reason','No reason provided.')
    admin_name = session.get('admin_name', 'Administrator')
    
    with get_db() as db:
        row = db.execute(
            """SELECT r.*, s.name, s.class_roll_no, s.exam_roll_no, s.father_name,
                s.phone, s.course, s.branch, s.year,
                e.title, e.date, e.time, e.venue, e.category, e.fee
            FROM registrations r
            JOIN students s ON r.student_id=s.id
            JOIN events   e ON r.event_id=e.id
            WHERE r.pass_id=?""", (pass_id,)).fetchone()
        
        if row:
            # Generate serial number for rejected pass (for tracking)
            serial_number = generate_serial_number(db, row['event_id'])
            
            # Generate rejected pass image
            student = {k: row[k] for k in ['name','class_roll_no','exam_roll_no','father_name','phone','course','branch','year']}
            event   = {'title': row['title'], 'date': row['date'], 'time': row['time'],
                       'venue': row['venue'], 'category': row['category'], 'fee': row['fee']}
            qr_path = make_pass_image(student, event, pass_id, serial_number, pass_status='rejected')
            
            # Update registration status
            db.execute(
                "UPDATE registrations SET pass_status='rejected', serial_number=?, qr_path=?, approved_at=?, approved_by=? WHERE pass_id=?",
                (serial_number, qr_path, datetime.now().strftime('%Y-%m-%d %H:%M'), admin_name, pass_id)
            )
            
            # Send notification
            notify(db, row['student_id'],
                f"❌ Your pass for <b>{row['title']}</b> was <b style='color:#ef4444'>REJECTED</b>. Reason: {reason}")
            db.commit()
    
    flash('Pass rejected and rejection pass generated.', 'info')
    return redirect(url_for('admin_passes'))

@app.route('/admin/passes/reapprove/<pass_id>', methods=['POST'])
@admin_required
def reapprove_pass(pass_id):
    """Re-approve a previously rejected pass"""
    admin_name = session.get('admin_name', 'Administrator')
    with get_db() as db:
        row = db.execute(
            """SELECT r.*, s.name, s.class_roll_no, s.exam_roll_no, s.father_name,
                s.phone, s.course, s.branch, s.year,
                e.title, e.date, e.time, e.venue, e.category, e.fee
            FROM registrations r
            JOIN students s ON r.student_id=s.id
            JOIN events   e ON r.event_id=e.id
            WHERE r.pass_id=?""", (pass_id,)).fetchone()
        if row:
            # Generate new serial number for re-approved pass
            serial_number = generate_serial_number(db, row['event_id'])
            
            student = {k: row[k] for k in ['name','class_roll_no','exam_roll_no','father_name','phone','course','branch','year']}
            event   = {'title': row['title'], 'date': row['date'], 'time': row['time'],
                       'venue': row['venue'], 'category': row['category'], 'fee': row['fee']}
            qr_path = make_pass_image(student, event, pass_id, serial_number, pass_status='approved')
            
            db.execute("UPDATE registrations SET pass_status='approved', serial_number=?, qr_path=?, approved_at=?, approved_by=? WHERE pass_id=?",
                (serial_number, qr_path, datetime.now().strftime('%Y-%m-%d %H:%M'), admin_name, pass_id))
            notify(db, row['student_id'],
                f"🎟 Your pass for <b>{row['title']}</b> has been <b style='color:#34d399'>RE-APPROVED</b>! Serial: {serial_number}. Go to My Passes to download it.")
            db.commit()
    flash('Pass re-approved and new QR pass generated!', 'success')
    return redirect(url_for('admin_passes'))

@app.route('/admin/view_pass/<pass_id>')
@admin_required
def admin_view_pass(pass_id):
    """View pass in browser for admin (similar to student view but for admin)"""
    with get_db() as db:
        # Get registration with student and event details
        reg = db.execute("""
            SELECT r.*, s.name, s.father_name, s.class_roll_no, s.exam_roll_no, s.phone,
                   s.course, s.branch, s.year,
                   e.title, e.date, e.time, e.venue, e.category, e.fee
            FROM registrations r
            JOIN students s ON r.student_id = s.id
            JOIN events e ON r.event_id = e.id
            WHERE r.pass_id = ?
        """, (pass_id,)).fetchone()
        
        if not reg:
            flash('Pass not found.', 'error')
            return redirect(url_for('admin_passes'))
        
        # Convert to dict for template
        reg_dict = dict(reg)
        stu_dict = {k: reg[k] for k in ['name','father_name','class_roll_no','exam_roll_no','phone','course','branch','year']}
        
        return render_template('admin_view_pass.html', reg=reg_dict, stu=stu_dict)

# ─────────────────────────────────────────────────
#  ADMIN STUDENTS
# ─────────────────────────────────────────────────
@app.route('/admin/students')
@admin_required
def admin_students():
    search = request.args.get('q','')
    with get_db() as db:
        q, p = "SELECT * FROM students WHERE 1=1", []
        if search:
            q += " AND (name LIKE ? OR username LIKE ? OR exam_roll_no LIKE ? OR phone LIKE ?)"
            p += [f'%{search}%']*4
        stus = db.execute(q + " ORDER BY created_at DESC", p).fetchall()
    return render_template('admin_students.html', students=stus, search=search)

@app.route('/admin/students/<int:sid>')
@admin_required
def admin_student_detail(sid):
    with get_db() as db:
        stu  = db.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
        regs = db.execute(
            """SELECT r.*, e.title, e.date, e.venue FROM registrations r
            JOIN events e ON r.event_id=e.id WHERE r.student_id=? ORDER BY r.registered_at DESC""",
            (sid,)).fetchall()
    return render_template('admin_student_detail.html', stu=stu, regs=regs)

# ─────────────────────────────────────────────────
#  ADMIN NOTIFY
# ─────────────────────────────────────────────────
@app.route('/admin/notify', methods=['POST'])
@admin_required
def admin_notify():
    msg    = request.form.get('message','').strip()
    target = request.form.get('target','all')
    if not msg:
        flash('Message cannot be empty.', 'error')
        return redirect(url_for('admin_dashboard'))
    with get_db() as db:
        if target == 'all':
            stus = db.execute("SELECT id FROM students").fetchall()
        else:
            stus = db.execute("SELECT DISTINCT student_id as id FROM registrations").fetchall()
        for s in stus: notify(db, s['id'], msg)
        db.commit()
    flash(f'Notification sent to {len(stus)} students!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/check_username')
def check_username():
    u = request.args.get('u','').strip()
    if not u:
        return jsonify({'available': False})
    with get_db() as db:
        exists = db.execute("SELECT id FROM students WHERE username=?", (u,)).fetchone()
    return jsonify({'available': not exists})

# ─────────────────────────────────────────────────
#  PUBLIC PASS VERIFICATION (QR scan target)
# ─────────────────────────────────────────────────
@app.route('/verify/<pass_id>')
def verify_pass(pass_id):
    with get_db() as db:
        row = db.execute(
            """SELECT r.pass_id, r.serial_number, r.pass_status, r.approved_at, r.registered_at,
                s.name, s.father_name, s.class_roll_no, s.exam_roll_no,
                s.phone, s.course, s.branch, s.year, s.email,
                e.title as event_title, e.date as event_date, e.time as event_time,
                e.venue, e.category, e.fee, e.description
            FROM registrations r
            JOIN students s ON r.student_id = s.id
            JOIN events   e ON r.event_id   = e.id
            WHERE r.pass_id = ?""", (pass_id,)).fetchone()
    if not row:
        return render_template('verify_pass.html', valid=False, pass_id=pass_id)
    return render_template('verify_pass.html', valid=True, row=row)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
