# 🎓 EventPass – College Event Management System
### MCA Final Year Project | Python Flask

---

## 🚀 How to Run

### Development Mode
```bash
# Step 1 – Open Anaconda Prompt or CMD

# Step 2 – Go to project folder
cd path\to\college_event_system

# Step 3 – Install dependencies
pip install -r requirements.txt
# OR if that fails:
python -m pip install -r requirements.txt

# Step 4 – Set up environment (optional)
cp .env.example .env
# Edit .env file with your preferred settings

# Step 5 – Run the app
python app.py

# Step 6 – Open in browser
# http://localhost:5000
```

### Production Deployment
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export FLASK_SECRET_KEY="your-secret-key-here"
export ADMIN_USER="your-admin-username"
export ADMIN_PASS="your-secure-password"

# Run with Gunicorn
gunicorn --bind 0.0.0.0:8000 wsgi:app
```

---

## 🔑 Login Credentials

| Role      | Login                        | Password   |
|-----------|------------------------------|------------|
| Admin     | Username: `admin`            | `admin@123`|
| Student   | Register a new account first |            |

**Student login works with any ONE of:**
- Username
- Examination Roll No
- Phone Number

---

## ✨ Features

### Student Side
- ✅ Sign Up with Name, Class Roll No, Exam Roll No, Father's Name, Unique Username, Unique Phone
- ✅ Login via Username OR Exam Roll No OR Phone + Password
- ✅ Dashboard with event search & category filter
- ✅ Register for events
- ✅ Pass status tracking (Pending / Approved / Rejected)
- ✅ Download QR Pass image (only after admin approval)
- ✅ Real-time notifications for new events & pass approvals
- ✅ Profile management

### Admin Side
- ✅ Single admin account (admin / admin@123)
- ✅ Full event CRUD (Create, Edit, Delete)
- ✅ Pass approval system — passes only generated AFTER admin approves
- ✅ QR Pass with full student details embedded
- ✅ View all students with search
- ✅ View attendees per event
- ✅ Bulk notifications to all / registered students
- ✅ Dashboard with live statistics

### QR Pass Contains
- Student Name, Father's Name
- Class Roll No, Exam Roll No
- Phone, Course, Branch, Year
- Event details (title, date, time, venue)
- Unique Pass ID
- Approval timestamp

---

## 📁 Project Structure

```
college_event_system/
├── app.py                        # Main Flask app (all routes)
├── requirements.txt              # Dependencies
├── cems.db                       # SQLite DB (auto-created)
├── static/
│   └── qrcodes/                  # Generated pass images
└── templates/
    ├── base.html                 # Shared layout (sidebar + topbar)
    ├── login.html                # Student login
    ├── signup.html               # Student registration
    ├── dashboard.html            # Student dashboard + event search
    ├── event_detail.html         # Event info + register button
    ├── my_passes.html            # All passes (pending/approved)
    ├── pass_detail.html          # Individual pass with QR
    ├── notifications.html        # Notification center
    ├── profile.html              # Student profile
    ├── admin_login.html          # Admin login
    ├── admin_dashboard.html      # Admin overview + stats
    ├── admin_events.html         # Event management table
    ├── admin_create_event.html   # Create event form
    ├── admin_edit_event.html     # Edit event form
    ├── admin_passes.html         # Pass approval panel ⭐
    ├── admin_students.html       # Student list + search
    ├── admin_student_detail.html # Individual student profile
    └── admin_attendees.html      # Event attendees list
```

---

## 🛠 Tech Stack

| Layer     | Technology              |
|-----------|-------------------------|
| Backend   | Python 3, Flask         |
| Database  | SQLite (built-in)       |
| Pass/QR   | Pillow (PIL)            |
| Frontend  | HTML5, CSS3, JavaScript |
| Fonts     | Google Fonts (Outfit)   |

No external CSS frameworks — fully custom design!
