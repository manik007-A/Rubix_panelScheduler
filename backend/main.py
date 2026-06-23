"""
RUBIX PANEL SCHEDULER - Main FastAPI Application
Production-ready SaaS scheduling platform for training institutes.
"""

import os
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Environment, FileSystemLoader
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables from .env, overriding existing ones
load_dotenv(override=True)

# Python 3.14 workaround: patch Jinja2's split_template_path
# to fix 'dict' object has no attribute 'split' bug in loaders.py
import jinja2.loaders as _jl
_original_split = _jl.split_template_path
def _patched_split(template):
    """Handle the case where template is a dict (Python 3.14 compat issue)."""
    if isinstance(template, dict):
        template = template.get('name', list(template.keys())[0] if template else 'unknown.html')
    return _original_split(template)
_jl.split_template_path = _patched_split

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import services
from backend.services.sheets_service import get_sheets_service, SheetsService
from backend.services.config_manager import get_config, ConfigManager
from backend.services.allocation_engine import get_allocation_engine, AllocationEngine
from backend.services.waitlist_engine import get_waitlist_engine, WaitlistEngine
from backend.services.reschedule_engine import get_reschedule_engine, RescheduleEngine
from backend.services.cancellation_engine import get_cancellation_engine, CancellationEngine
from backend.services.auth_service import get_auth_service, AuthService, SessionStore
from backend.models.schemas import (
    StudentVerificationRequest, BookingRequest, RescheduleRequest,
    CancelRequest, AdminLoginRequest, MoveBookingRequest, BlockSlotRequest,
    WaitlistRequest, StatusCheckRequest
)


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting RUBIX PANEL SCHEDULER...")
    try:
        # Initialize sheets service and ensure sheets exist
        sheets = get_sheets_service()
        sheets.ensure_sheets_exist()
        logger.info("Google Sheets initialized successfully")
    except Exception as e:
        logger.warning(f"Failed to initialize Google Sheets: {e}")
        logger.warning("Application will start but Sheets features may not work")
    
    yield
    
    # Shutdown
    logger.info("Shutting down RUBIX PANEL SCHEDULER...")


# Create FastAPI app
app = FastAPI(
    title="RUBIX PANEL SCHEDULER",
    description="Production-grade SaaS scheduling platform for training institutes and mock interview programs",
    version="1.0.0",
    lifespan=lifespan
)

# Security middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

# Templates - use a custom environment without cache to avoid
# Python 3.14 incompatibility with Jinja2's LRUCache (unhashable dict key)
_jinja_env = Environment(
    loader=FileSystemLoader("frontend"),
    cache_size=0,
    auto_reload=True
)
templates = Jinja2Templates(env=_jinja_env)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Get current user from session cookie."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None
    
    auth = get_auth_service()
    result = auth.validate_session(session_id)
    if result.get('valid'):
        return result.get('session')
    return None


def require_auth(request: Request) -> Dict[str, Any]:
    """Require authentication, raise 401 if not authenticated."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    return user


def require_admin(request: Request) -> Dict[str, Any]:
    """Require admin authentication, raise 403 if not admin."""
    user = require_auth(request)
    if user.get('user_type') != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user


def get_sheets() -> SheetsService:
    """Dependency for sheets service."""
    return get_sheets_service()


def get_config_manager() -> ConfigManager:
    """Dependency for config manager."""
    return get_config()


def get_allocation() -> AllocationEngine:
    """Dependency for allocation engine."""
    return get_allocation_engine()


def get_waitlist() -> WaitlistEngine:
    """Dependency for waitlist engine."""
    return get_waitlist_engine()


def get_reschedule() -> RescheduleEngine:
    """Dependency for reschedule engine."""
    return get_reschedule_engine()


def get_cancellation() -> CancellationEngine:
    """Dependency for cancellation engine."""
    return get_cancellation_engine()


def get_auth() -> AuthService:
    """Dependency for auth service."""
    return get_auth_service()


# ============================================================================
# FRONTEND ROUTES
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Landing page - redirect to booking."""
    return templates.TemplateResponse(request, "booking.html", {"request": request})


@app.get("/booking", response_class=HTMLResponse)
async def booking_page(request: Request):
    """Student booking page."""
    return templates.TemplateResponse(request, "booking.html", {"request": request})


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    """Booking status check page."""
    return templates.TemplateResponse(request, "status.html", {"request": request})


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    """Admin login page."""
    return templates.TemplateResponse(request, "admin_login.html", {"request": request})


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, user: Dict = Depends(require_admin)):
    """Admin dashboard page."""
    return templates.TemplateResponse(request, "admin_dashboard.html", {
        "request": request,
        "user": user
    })


# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.post("/api/verify-student")
async def verify_student(
    request: StudentVerificationRequest,
    sheets: SheetsService = Depends(get_sheets),
    auth: AuthService = Depends(get_auth)
):
    """Verify a student by ID."""
    result = auth.verify_student(request.student_id, sheets)
    
    if result.get('verified'):
        response = JSONResponse(content=result)
        response.set_cookie(
            key="session_id",
            value=result['session_id'],
            max_age=3600,  # 1 hour
            httponly=True,
            samesite="lax"
        )
        return response
    
    return JSONResponse(
        status_code=401,
        content=result
    )


@app.post("/api/admin/login")
async def admin_login(
    request: AdminLoginRequest,
    auth: AuthService = Depends(get_auth)
):
    """Admin login."""
    result = auth.authenticate_admin(request.username, request.password)
    
    if result.get('success'):
        response = JSONResponse(content=result)
        response.set_cookie(
            key="session_id",
            value=result['session_id'],
            max_age=3600,  # 1 hour
            httponly=True,
            samesite="lax"
        )
        return response
    
    return JSONResponse(
        status_code=401,
        content=result
    )


@app.post("/api/logout")
async def logout(request: Request, auth: AuthService = Depends(get_auth)):
    """Logout current session."""
    session_id = request.cookies.get("session_id")
    if session_id:
        auth.logout(session_id)
    
    response = JSONResponse(content={"success": True, "message": "Logged out successfully"})
    response.delete_cookie(key="session_id")
    return response


@app.get("/api/session/validate")
async def validate_session(user: Dict = Depends(require_auth)):
    """Validate current session."""
    return {
        "valid": True,
        "user_type": user.get('user_type'),
        "user_data": user.get('data', {})
    }


# ============================================================================
# STUDENT BOOKING ROUTES
# ============================================================================

@app.post("/api/bookings")
async def create_booking(
    booking: BookingRequest,
    request: Request,
    user: Dict = Depends(require_auth),
    sheets: SheetsService = Depends(get_sheets),
    allocation: AllocationEngine = Depends(get_allocation),
    waitlist: WaitlistEngine = Depends(get_waitlist)
):
    """Create a new booking or add to waitlist."""
    # Verify student owns this booking
    if user.get('user_type') != 'student':
        raise HTTPException(status_code=403, detail="Students only")
    
    student_id = user.get('data', {}).get('student_id', booking.student_id)
    
    # Check for duplicate bookings at the same time
    existing = sheets.get_bookings_by_student(student_id)
    for b in existing:
        if (b.get('InterviewDate') == booking.interview_date and
            b.get('Status') in ['BOOKED', 'WAITLISTED']):
            if b.get('StartTime') == booking.start_time:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "message": "You already have a booking at this time"}
                )
    
    # Validate booking request
    is_valid, error_msg = allocation.validate_booking_request(
        student_id, booking.interview_date, booking.start_time, booking.duration
    )
    
    if not is_valid:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": error_msg}
        )
    
    # Try to find an available slot
    slot = allocation.find_available_slot(
        booking.interview_date,
        booking.start_time,
        booking.duration
    )
    
    if slot:
        # Create booking
        booking_data = {
            'StudentID': student_id,
            'StudentName': booking.student_name,
            'Company': booking.company,
            'InterviewDate': booking.interview_date,
            'StartTime': slot['start_time'],
            'EndTime': slot['end_time'],
            'Duration': str(booking.duration),
            'AllocatedPanel': slot['panel'],
            'Notes': booking.notes or ''
        }
        
        created = sheets.create_booking(booking_data)
        
        # Log audit
        sheets.log_audit(
            action='CREATE_BOOKING',
            booking_id=created['BookingID'],
            old_value='',
            new_value=f"Date:{booking.interview_date}, Time:{slot['start_time']}, Panel:{slot['panel']}",
            performed_by=student_id,
            reason='New booking'
        )
        
        # Regenerate panel grid
        try:
            config = get_config()
            panels = config.get_panels()
            time_slots = config.generate_time_slots(booking.interview_date)
            blocked = config.get_blocked_slots()
            bookings = sheets.get_bookings_by_date(booking.interview_date)
            sheets.regenerate_panel_grid(bookings, panels, time_slots, blocked)
        except Exception as e:
            logger.error(f"Failed to regenerate panel grid: {e}")
        
        return JSONResponse(content={
            "success": True,
            "message": "Booking confirmed",
            "booking": created,
            "is_waitlisted": False
        })
    else:
        # Add to waitlist
        waitlist_data = {
            'StudentID': student_id,
            'StudentName': booking.student_name,
            'Company': booking.company,
            'PreferredDate': booking.interview_date,
            'PreferredTime': booking.start_time,
            'Duration': booking.duration,
            'Notes': booking.notes or ''
        }
        
        wl_result = waitlist.add_to_waitlist(**waitlist_data)
        
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "message": "No slots available. You've been added to the waitlist.",
                "waitlist_id": wl_result['waitlist_id'],
                "position": wl_result['position'],
                "is_waitlisted": True
            }
        )


@app.get("/api/bookings/my")
async def get_my_bookings(
    request: Request,
    user: Dict = Depends(require_auth),
    sheets: SheetsService = Depends(get_sheets)
):
    """Get current student's bookings."""
    if user.get('user_type') != 'student':
        raise HTTPException(status_code=403, detail="Students only")
    
    # student_id is stored as user_id at the session level (from auth_service)
    student_id = user.get('user_id') or user.get('data', {}).get('student_id')
    if not student_id:
        raise HTTPException(status_code=400, detail="Student ID not found in session")
    
    bookings = sheets.get_bookings_by_student(student_id)
    
    # Filter to only show active/recent bookings
    active_statuses = ['BOOKED', 'WAITLISTED', 'RESCHEDULED']
    my_bookings = [b for b in bookings if b.get('Status') in active_statuses]
    
    return {"bookings": my_bookings}


@app.get("/api/bookings/{booking_id}")
async def get_booking(
    booking_id: str,
    user: Dict = Depends(require_auth),
    sheets: SheetsService = Depends(get_sheets)
):
    """Get a specific booking."""
    booking = sheets.get_booking_by_id(booking_id)
    
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    # Students can only view their own bookings
    if user.get('user_type') == 'student':
        student_id = user.get('user_id') or user.get('data', {}).get('student_id')
        if booking.get('StudentID') != student_id:
            raise HTTPException(status_code=403, detail="Access denied")
    
    return booking


@app.post("/api/bookings/{booking_id}/reschedule")
async def reschedule_booking(
    booking_id: str,
    reschedule: RescheduleRequest,
    user: Dict = Depends(require_auth),
    sheets: SheetsService = Depends(get_sheets),
    reschedule_engine: RescheduleEngine = Depends(get_reschedule)
):
    """Reschedule a booking."""
    booking = sheets.get_booking_by_id(booking_id)
    
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    # Students can only reschedule their own bookings
    if user.get('user_type') == 'student':
        student_id = user.get('user_id') or user.get('data', {}).get('student_id')
        if booking.get('StudentID') != student_id:
            raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if can reschedule
    can_reschedule, reason = reschedule_engine.can_reschedule(booking_id)
    if not can_reschedule:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": reason}
        )
    
    result = reschedule_engine.reschedule_booking(
        booking_id=booking_id,
        new_date=reschedule.new_date,
        new_time=reschedule.new_time,
        reason=reschedule.reason,
        performed_by=booking.get('StudentID')
    )
    
    if result.get('success'):
        return JSONResponse(content=result)
    
    return JSONResponse(
        status_code=400,
        content=result
    )


@app.post("/api/bookings/{booking_id}/cancel")
async def cancel_booking(
    booking_id: str,
    cancel: CancelRequest = None,
    user: Dict = Depends(require_auth),
    sheets: SheetsService = Depends(get_sheets),
    cancellation: CancellationEngine = Depends(get_cancellation)
):
    """Cancel a booking."""
    booking = sheets.get_booking_by_id(booking_id)
    
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    # Students can only cancel their own bookings
    if user.get('user_type') == 'student':
        student_id = user.get('user_id') or user.get('data', {}).get('student_id')
        if booking.get('StudentID') != student_id:
            raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if can cancel
    can_cancel, reason = cancellation.can_cancel(booking_id)
    if not can_cancel:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": reason}
        )
    
    result = cancellation.cancel_booking(
        booking_id=booking_id,
        reason=cancel.reason if cancel else None,
        performed_by=booking.get('StudentID')
    )
    
    if result.get('success'):
        return JSONResponse(content=result)
    
    return JSONResponse(
        status_code=400,
        content=result
    )


# ============================================================================
# STATUS CHECK ROUTES
# ============================================================================

@app.post("/api/status/check")
async def check_booking_status(
    request: StatusCheckRequest,
    sheets: SheetsService = Depends(get_sheets)
):
    """Check booking status without authentication."""
    student_id = request.student_id.strip()
    
    # Verify student exists
    student = sheets.verify_student(student_id)
    if not student:
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "message": "You are not authorized. Please contact the administrator."
            }
        )
    
    # Get bookings
    if request.booking_id:
        booking = sheets.get_booking_by_id(request.booking_id)
        if booking and booking.get('StudentID', '').strip().upper() == student_id.upper():
            bookings = [booking]
        else:
            bookings = []
    else:
        bookings = sheets.get_bookings_by_student(student_id)
    
    # Filter active bookings
    active_statuses = ['BOOKED', 'WAITLISTED', 'RESCHEDULED', 'COMPLETED', 'NO_SHOW', 'CANCELLED']
    active_bookings = [b for b in bookings if b.get('Status') in active_statuses]
    
    # Sort by date descending
    active_bookings.sort(key=lambda x: x.get('InterviewDate', ''), reverse=True)
    
    return {
        "success": True,
        "student_name": student.get('StudentName'),
        "bookings": active_bookings
    }


# ============================================================================
# ADMIN ROUTES
# ============================================================================

@app.get("/api/admin/stats")
async def get_dashboard_stats(
    user: Dict = Depends(require_admin),
    sheets: SheetsService = Depends(get_sheets),
    allocation: AllocationEngine = Depends(get_allocation)
):
    """Get dashboard statistics."""
    bookings = sheets.get_all_bookings()
    
    total = len(bookings)
    active = len([b for b in bookings if b.get('Status') == 'BOOKED'])
    completed = len([b for b in bookings if b.get('Status') == 'COMPLETED'])
    waitlisted = len([b for b in bookings if b.get('Status') == 'WAITLISTED'])
    no_shows = len([b for b in bookings if b.get('Status') == 'NO_SHOW'])
    cancelled = len([b for b in bookings if b.get('Status') == 'CANCELLED'])
    
    # Calculate utilization
    utilization = allocation.get_panel_utilization()
    avg_utilization = sum(v['utilization_percent'] for v in utilization.values()) / len(utilization) if utilization else 0
    
    # Panel statuses
    config = get_config()
    panels = config.get_panels()
    panel_statuses = []
    
    for panel in panels:
        panel_bookings = len([b for b in bookings if b.get('AllocatedPanel') == panel and b.get('Status') == 'BOOKED'])
        panel_blocked = len(config.get_blocked_slots().get(panel, []))
        
        panel_statuses.append({
            "panel_name": panel,
            "enabled": config.is_panel_enabled(panel),
            "bookings_count": panel_bookings,
            "blocked_slots_count": panel_blocked
        })
    
    return {
        "total_bookings": total,
        "active_interviews": active,
        "completed_interviews": completed,
        "waitlisted_count": waitlisted,
        "no_shows": no_shows,
        "cancelled_count": cancelled,
        "utilization_percent": round(avg_utilization, 2),
        "panels": panel_statuses
    }


@app.get("/api/admin/bookings")
async def get_all_bookings(
    date: str = None,
    status: str = None,
    user: Dict = Depends(require_admin),
    sheets: SheetsService = Depends(get_sheets)
):
    """Get all bookings with optional filters."""
    if date:
        bookings = sheets.get_bookings_by_date(date)
    else:
        bookings = sheets.get_all_bookings()
    
    if status:
        bookings = [b for b in bookings if b.get('Status') == status]
    
    # Sort by date and time
    bookings.sort(key=lambda x: (x.get('InterviewDate', ''), x.get('StartTime', '')))
    
    return {"bookings": bookings}


@app.get("/api/admin/waitlist")
async def get_waitlist(
    user: Dict = Depends(require_admin),
    waitlist_engine: WaitlistEngine = Depends(get_waitlist)
):
    """Get waitlist entries."""
    entries = waitlist_engine.get_waitlist()
    return {"waitlist": entries}


@app.post("/api/admin/waitlist/{waitlist_id}/promote")
async def promote_waitlist_entry(
    waitlist_id: str,
    user: Dict = Depends(require_admin),
    waitlist_engine: WaitlistEngine = Depends(get_waitlist)
):
    """Manually promote a waitlist entry."""
    result = waitlist_engine.promote_specific_entry(waitlist_id)
    
    if result.get('success'):
        return JSONResponse(content=result)
    
    return JSONResponse(
        status_code=400,
        content=result
    )


@app.delete("/api/admin/waitlist/{waitlist_id}")
async def remove_waitlist_entry(
    waitlist_id: str,
    user: Dict = Depends(require_admin),
    waitlist_engine: WaitlistEngine = Depends(get_waitlist)
):
    """Remove an entry from waitlist."""
    success = waitlist_engine.remove_from_waitlist(waitlist_id)
    
    if success:
        return JSONResponse(content={"success": True, "message": "Removed from waitlist"})
    
    return JSONResponse(
        status_code=404,
        content={"success": False, "message": "Waitlist entry not found"}
    )


@app.post("/api/admin/bookings/{booking_id}/move")
async def move_booking(
    booking_id: str,
    move: MoveBookingRequest,
    user: Dict = Depends(require_admin),
    reschedule_engine: RescheduleEngine = Depends(get_reschedule)
):
    """Admin move booking to different slot/panel."""
    result = reschedule_engine.admin_move_booking(
        booking_id=booking_id,
        new_date=move.new_date,
        new_time=move.new_time,
        new_panel=move.new_panel,
        reason=move.reason
    )
    
    if result.get('success'):
        return JSONResponse(content=result)
    
    return JSONResponse(
        status_code=400,
        content=result
    )


@app.post("/api/admin/bookings/{booking_id}/reschedule")
async def admin_reschedule_booking(
    booking_id: str,
    reschedule: RescheduleRequest,
    user: Dict = Depends(require_admin),
    reschedule_engine: RescheduleEngine = Depends(get_reschedule)
):
    """Admin reschedule any student's interview."""
    booking = reschedule_engine._sheets.get_booking_by_id(booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    # Check if booking can be rescheduled
    can_reschedule, reason = reschedule_engine.can_reschedule(booking_id)
    if not can_reschedule:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": reason}
        )
    
    result = reschedule_engine.admin_reschedule_booking(
        booking_id=booking_id,
        new_date=reschedule.new_date,
        new_time=reschedule.new_time,
        new_panel=None,
        reason=reschedule.reason
    )
    
    if result.get('success'):
        return JSONResponse(content=result)
    
    return JSONResponse(
        status_code=400,
        content=result
    )


@app.get("/api/admin/reschedule-history")
async def get_reschedule_history(
    student_name: str = None,
    limit: int = 100,
    user: Dict = Depends(require_admin),
    reschedule_engine: RescheduleEngine = Depends(get_reschedule)
):
    """Get reschedule history."""
    history = reschedule_engine.get_reschedule_history(student_name, limit)
    return {"history": history}


@app.post("/api/admin/bookings/{booking_id}/complete")
async def complete_booking(
    booking_id: str,
    user: Dict = Depends(require_admin),
    cancellation: CancellationEngine = Depends(get_cancellation)
):
    """Mark a booking as completed."""
    result = cancellation.mark_completed(booking_id)
    
    if result.get('success'):
        return JSONResponse(content=result)
    
    return JSONResponse(
        status_code=400,
        content=result
    )


@app.post("/api/admin/bookings/{booking_id}/no-show")
async def mark_no_show(
    booking_id: str,
    reason: str = None,
    user: Dict = Depends(require_admin),
    cancellation: CancellationEngine = Depends(get_cancellation)
):
    """Mark a booking as no-show."""
    result = cancellation.mark_no_show(booking_id, reason)
    
    if result.get('success'):
        return JSONResponse(content=result)
    
    return JSONResponse(
        status_code=400,
        content=result
    )


@app.post("/api/admin/bookings/{booking_id}/cancel")
async def admin_cancel_booking(
    booking_id: str,
    reason: str = None,
    user: Dict = Depends(require_admin),
    cancellation: CancellationEngine = Depends(get_cancellation)
):
    """Admin cancel a booking."""
    result = cancellation.admin_cancel_booking(booking_id, reason)
    
    if result.get('success'):
        return JSONResponse(content=result)
    
    return JSONResponse(
        status_code=400,
        content=result
    )


@app.post("/api/admin/panels/block")
async def block_slot(
    request: BlockSlotRequest,
    user: Dict = Depends(require_admin),
    config: ConfigManager = Depends(get_config),
    sheets: SheetsService = Depends(get_sheets)
):
    """Block a time slot for a panel."""
    success = config.add_blocked_slot(request.panel, request.time_slot)
    
    if success:
        # Regenerate panel grid
        try:
            panels = config.get_panels()
            time_slots = config.generate_time_slots(request.date)
            blocked = config.get_blocked_slots()
            bookings = sheets.get_bookings_by_date(request.date)
            sheets.regenerate_panel_grid(bookings, panels, time_slots, blocked)
        except Exception as e:
            logger.error(f"Failed to regenerate panel grid: {e}")
        
        # Log audit
        sheets.log_audit(
            action='BLOCK_SLOT',
            booking_id='',
            old_value='FREE',
            new_value='BLOCKED',
            performed_by='ADMIN',
            reason=f"Panel:{request.panel}, Slot:{request.time_slot}. {request.reason or ''}"
        )
        
        return JSONResponse(content={"success": True, "message": "Slot blocked successfully"})
    
    return JSONResponse(
        status_code=400,
        content={"success": False, "message": "Failed to block slot"}
    )


@app.post("/api/admin/panels/unblock")
async def unblock_slot(
    request: BlockSlotRequest,
    user: Dict = Depends(require_admin),
    config: ConfigManager = Depends(get_config),
    sheets: SheetsService = Depends(get_sheets)
):
    """Unblock a time slot for a panel."""
    success = config.remove_blocked_slot(request.panel, request.time_slot)
    
    if success:
        # Regenerate panel grid
        try:
            panels = config.get_panels()
            time_slots = config.generate_time_slots(request.date)
            blocked = config.get_blocked_slots()
            bookings = sheets.get_bookings_by_date(request.date)
            sheets.regenerate_panel_grid(bookings, panels, time_slots, blocked)
        except Exception as e:
            logger.error(f"Failed to regenerate panel grid: {e}")
        
        # Log audit
        sheets.log_audit(
            action='UNBLOCK_SLOT',
            booking_id='',
            old_value='BLOCKED',
            new_value='FREE',
            performed_by='ADMIN',
            reason=f"Panel:{request.panel}, Slot:{request.time_slot}"
        )
        
        return JSONResponse(content={"success": True, "message": "Slot unblocked successfully"})
    
    return JSONResponse(
        status_code=400,
        content={"success": False, "message": "Failed to unblock slot"}
    )


@app.post("/api/admin/panels/enable")
async def enable_panel(
    panel: str,
    user: Dict = Depends(require_admin),
    config: ConfigManager = Depends(get_config),
    sheets: SheetsService = Depends(get_sheets)
):
    """Enable a panel."""
    success = config.enable_panel(panel)
    
    if success:
        sheets.log_audit(
            action='ENABLE_PANEL',
            booking_id='',
            old_value='DISABLED',
            new_value='ENABLED',
            performed_by='ADMIN',
            reason=f"Panel:{panel}"
        )
        return JSONResponse(content={"success": True, "message": f"Panel {panel} enabled"})
    
    return JSONResponse(
        status_code=400,
        content={"success": False, "message": "Panel may already be enabled"}
    )


@app.post("/api/admin/panels/disable")
async def disable_panel(
    panel: str,
    user: Dict = Depends(require_admin),
    config: ConfigManager = Depends(get_config),
    sheets: SheetsService = Depends(get_sheets)
):
    """Disable a panel."""
    success = config.disable_panel(panel)
    
    if success:
        sheets.log_audit(
            action='DISABLE_PANEL',
            booking_id='',
            old_value='ENABLED',
            new_value='DISABLED',
            performed_by='ADMIN',
            reason=f"Panel:{panel}"
        )
        return JSONResponse(content={"success": True, "message": f"Panel {panel} disabled"})
    
    return JSONResponse(
        status_code=400,
        content={"success": False, "message": "Panel may already be disabled"}
    )


@app.get("/api/admin/audit-logs")
async def get_audit_logs(
    booking_id: str = None,
    limit: int = 100,
    user: Dict = Depends(require_admin),
    sheets: SheetsService = Depends(get_sheets)
):
    """Get audit logs."""
    logs = sheets.get_audit_logs(booking_id, limit)
    return {"logs": logs}


@app.get("/api/admin/panel-grid")
async def get_panel_grid(
    date: str = None,
    user: Dict = Depends(require_admin),
    sheets: SheetsService = Depends(get_sheets),
    config: ConfigManager = Depends(get_config)
):
    """Get panel grid for a date."""
    if not date:
        date = datetime.now().strftime('%Y-%m-%d')
    
    panels = config.get_panels()
    time_slots = config.generate_time_slots(date)
    blocked = config.get_blocked_slots()
    bookings = sheets.get_bookings_by_date(date)
    
    # Build grid
    grid = []
    for slot in time_slots:
        row = {"time_slot": slot}
        for panel in panels:
            cell_status = "FREE"
            cell_booking = None
            
            if config.is_slot_blocked(panel, slot):
                cell_status = "BLOCKED"
            else:
                for booking in bookings:
                    if (booking.get('AllocatedPanel') == panel and
                        booking.get('Status') in ['BOOKED', 'WAITLISTED']):
                        booking_start = booking.get('StartTime', '')
                        booking_end = booking.get('EndTime', '')
                        slot_start = slot.split('-')[0]
                        slot_end = slot.split('-')[1]
                        
                        if booking_start < slot_end and booking_end > slot_start:
                            cell_status = booking.get('BookingID', '')
                            cell_booking = booking
                            break
            
            row[panel] = {
                "status": cell_status,
                "booking": cell_booking
            }
        grid.append(row)
    
    return {
        "date": date,
        "panels": panels,
        "time_slots": time_slots,
        "grid": grid
    }


@app.get("/api/admin/availability")
async def get_availability(
    date: str,
    user: Dict = Depends(require_admin),
    allocation: AllocationEngine = Depends(get_allocation)
):
    """Get slot availability for a date."""
    availability = allocation.get_slot_availability(date)
    return {"availability": availability}


# ============================================================================
# CONFIG ROUTES
# ============================================================================

@app.get("/api/config")
async def get_public_config(
    config: ConfigManager = Depends(get_config)
):
    """Get public configuration."""
    return {
        "working_hours_start": config.get_working_hours_start().strftime('%H:%M'),
        "working_hours_end": config.get_working_hours_end().strftime('%H:%M'),
        "slot_duration": config.get_slot_duration(),
        "max_duration": config.get_max_duration(),
        "min_duration": config.get_min_duration(),
        "panels": config.get_panels()
    }


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.detail}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions."""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "Internal server error"}
    )


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=os.getenv("DEBUG", "False").lower() == "true"
    )