# RUBIX Panel Scheduler

A production-grade SaaS scheduling platform for training institutes and mock interview programs.

## Features

- **Student Booking System**: Students can book interview slots with automatic panel allocation
- **Waitlist Management**: FIFO-based waitlist with auto-promotion
- **Rescheduling**: Atomic reschedule operations with rollback support
- **Cancellation**: Slot release with automatic waitlist promotion
- **Admin Dashboard**: Full control over bookings, panels, and waitlist
- **Timeline Visualization**: Interactive panel schedule view
- **Audit Logging**: Complete history of all actions
- **Session Authentication**: Secure cookie-based authentication

## Tech Stack

- **Backend**: FastAPI (Python)
- **Database**: Google Sheets (only database)
- **Frontend**: HTML5, Bootstrap 5, Vanilla JavaScript, Chart.js
- **Authentication**: Session Cookie Authentication

## Installation

1. Clone the repository:
```bash
git clone <repo-url>
cd PanelScheduler
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your Google Sheets credentials
```

4. Configure Google Sheets:
   - Create a Google Sheet with the required structure
   - Set up Google Cloud credentials (see `.env.example`)
   - Share the sheet with the service account email

5. Run the application:
```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## Default Credentials

- **Admin Username**: `admin`
- **Admin Password**: `Rubix@123`

(Change these in environment variables for production!)

## Google Sheets Structure

The application requires the following sheets:

### MASTER_BOOKINGS
| Column | Description |
|--------|-------------|
| BookingID | Unique booking identifier |
| StudentID | Student identifier |
| StudentName | Student name |
| Company | Company name for interview |
| InterviewDate | Date of interview (YYYY-MM-DD) |
| StartTime | Start time (HH:MM) |
| EndTime | End time (HH:MM) |
| Duration | Duration in minutes |
| AllocatedPanel | Assigned panel name |
| Status | BOOKED, WAITLISTED, CANCELLED, COMPLETED, NO_SHOW, RESCHEDULED |
| PreviousBookingID | Reference to previous booking (for reschedules) |
| CreatedAt | Creation timestamp |
| ModifiedAt | Last modification timestamp |
| Notes | Additional notes |

### MASTER_STUDENTS
| Column | Description |
|--------|-------------|
| StudentID | Unique student identifier |
| StudentName | Student name |
| Email | Student email |
| Batch | Student batch |
| Status | ACTIVE or INACTIVE |
| EligibleForScheduling | TRUE or FALSE |

### PANEL_GRID
Auto-generated grid showing panel availability.

### CONFIG
Configuration key-value pairs.

### AUDIT_LOG
| Column | Description |
|--------|-------------|
| AuditID | Unique audit entry ID |
| Timestamp | When the action occurred |
| Action | Type of action |
| BookingID | Related booking ID |
| OldValue | Previous value |
| NewValue | New value |
| PerformedBy | Who performed the action |
| Reason | Reason for the action |

## API Endpoints

### Authentication
- `POST /api/verify-student` - Verify student by ID
- `POST /api/admin/login` - Admin login
- `POST /api/logout` - Logout
- `GET /api/session/validate` - Validate session

### Student
- `POST /api/bookings` - Create booking
- `GET /api/bookings/my` - Get student's bookings
- `POST /api/bookings/{id}/reschedule` - Reschedule booking
- `POST /api/bookings/{id}/cancel` - Cancel booking

### Status Check
- `POST /api/status/check` - Check booking status

### Admin
- `GET /api/admin/stats` - Dashboard statistics
- `GET /api/admin/bookings` - All bookings
- `GET /api/admin/waitlist` - Waitlist entries
- `POST /api/admin/waitlist/{id}/promote` - Promote waitlist entry
- `POST /api/admin/bookings/{id}/complete` - Mark as completed
- `POST /api/admin/bookings/{id}/no-show` - Mark as no-show
- `GET /api/admin/panel-grid` - Panel schedule
- `GET /api/admin/audit-logs` - Audit log

## Project Structure

```
PanelScheduler/
├── backend/
│   ├── __init__.py
│   ├── main.py              # FastAPI application
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py       # Pydantic models
│   ├── routes/
│   │   └── __init__.py
│   └── services/
│       ├── __init__.py
│       ├── sheets_service.py    # Google Sheets integration
│       ├── config_manager.py    # Configuration management
│       ├── allocation_engine.py # Booking allocation
│       ├── waitlist_engine.py   # Waitlist management
│       ├── reschedule_engine.py # Rescheduling logic
│       ├── cancellation_engine.py # Cancellation logic
│       └── auth_service.py      # Authentication
├── frontend/
│   ├── booking.html         # Student booking page
│   ├── status.html          # Status check page
│   ├── admin_login.html     # Admin login
│   ├── admin_dashboard.html # Admin dashboard
│   └── static/
│       ├── css/
│       │   ├── theme.css
│       │   ├── dashboard.css
│       │   └── timeline.css
│       └── js/
│           ├── booking.js
│           └── admin.js
├── requirements.txt
└── .env.example
```

## Edge Cases Handled

- Duplicate booking prevention
- Concurrent booking conflicts
- Session expiry handling
- Network failure recovery
- Invalid date/time validation
- Past date booking prevention
- Working hours enforcement
- Panel availability checking
- Waitlist auto-promotion
- Atomic transaction rollback

## License

MIT License# Rubix_panelScheduler
