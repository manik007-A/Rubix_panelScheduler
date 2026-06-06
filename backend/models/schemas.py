"""
RUBIX PANEL SCHEDULER - Pydantic Models and Schemas
Defines all data models for request/response validation.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import date, time, datetime
import re


class StudentVerificationRequest(BaseModel):
    """Request model for student verification."""
    student_id: str = Field(..., min_length=1, max_length=50)
    
    @field_validator('student_id')
    @classmethod
    def validate_student_id(cls, v):
        return v.strip()


class StudentVerificationResponse(BaseModel):
    """Response model for student verification."""
    verified: bool
    student_id: Optional[str] = None
    student_name: Optional[str] = None
    email: Optional[str] = None
    batch: Optional[str] = None
    message: Optional[str] = None


class BookingRequest(BaseModel):
    """Request model for creating a booking."""
    student_id: str = Field(..., min_length=1, max_length=50)
    student_name: str = Field(..., min_length=1, max_length=200)
    company: str = Field(..., min_length=1, max_length=200)
    interview_date: str = Field(...)  # YYYY-MM-DD format
    start_time: str = Field(...)  # HH:MM format
    duration: int = Field(..., ge=30, le=180)  # minutes
    notes: Optional[str] = Field(None, max_length=500)
    
    @field_validator('student_id')
    @classmethod
    def validate_student_id(cls, v):
        return v.strip()
    
    @field_validator('interview_date')
    @classmethod
    def validate_date(cls, v):
        try:
            parsed_date = datetime.strptime(v, '%Y-%m-%d').date()
            if parsed_date < date.today():
                raise ValueError('Cannot book interviews in the past')
            return v
        except ValueError as e:
            if 'Cannot book' in str(e):
                raise
            raise ValueError('Invalid date format. Use YYYY-MM-DD')
    
    @field_validator('start_time')
    @classmethod
    def validate_time(cls, v):
        try:
            datetime.strptime(v, '%H:%M')
            return v
        except ValueError:
            raise ValueError('Invalid time format. Use HH:MM')


class BookingResponse(BaseModel):
    """Response model for booking operations."""
    success: bool
    message: str
    booking: Optional[Dict[str, Any]] = None
    booking_id: Optional[str] = None
    waitlist_id: Optional[str] = None
    is_waitlisted: bool = False


class RescheduleRequest(BaseModel):
    """Request model for rescheduling a booking."""
    booking_id: str = Field(..., min_length=1)
    new_date: str = Field(...)  # YYYY-MM-DD format
    new_time: str = Field(...)  # HH:MM format
    reason: Optional[str] = Field(None, max_length=500)
    
    @field_validator('booking_id')
    @classmethod
    def validate_booking_id(cls, v):
        return v.strip()
    
    @field_validator('new_date')
    @classmethod
    def validate_date(cls, v):
        try:
            parsed_date = datetime.strptime(v, '%Y-%m-%d').date()
            if parsed_date < date.today():
                raise ValueError('Cannot reschedule to a past date')
            return v
        except ValueError as e:
            if 'Cannot reschedule' in str(e):
                raise
            raise ValueError('Invalid date format. Use YYYY-MM-DD')
    
    @field_validator('new_time')
    @classmethod
    def validate_time(cls, v):
        try:
            datetime.strptime(v, '%H:%M')
            return v
        except ValueError:
            raise ValueError('Invalid time format. Use HH:MM')


class CancelRequest(BaseModel):
    """Request model for cancelling a booking."""
    booking_id: str = Field(..., min_length=1)
    reason: Optional[str] = Field(None, max_length=500)
    
    @field_validator('booking_id')
    @classmethod
    def validate_booking_id(cls, v):
        return v.strip()


class AdminLoginRequest(BaseModel):
    """Request model for admin login."""
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class AdminLoginResponse(BaseModel):
    """Response model for admin login."""
    success: bool
    message: str
    redirect: Optional[str] = None


class MoveBookingRequest(BaseModel):
    """Request model for moving a booking (admin)."""
    booking_id: str = Field(..., min_length=1)
    new_date: str = Field(...)
    new_time: str = Field(...)
    new_panel: str = Field(...)
    reason: Optional[str] = Field(None, max_length=500)


class BlockSlotRequest(BaseModel):
    """Request model for blocking a time slot."""
    panel: str = Field(..., min_length=1)
    date: str = Field(...)
    time_slot: str = Field(...)
    reason: Optional[str] = Field(None, max_length=500)


class UnblockSlotRequest(BaseModel):
    """Request model for unblocking a time slot."""
    panel: str = Field(..., min_length=1)
    date: str = Field(...)
    time_slot: str = Field(...)


class WaitlistRequest(BaseModel):
    """Request model for adding to waitlist."""
    student_id: str = Field(..., min_length=1)
    student_name: str = Field(..., min_length=1)
    company: str = Field(..., min_length=1)
    preferred_date: str = Field(...)
    preferred_time: str = Field(...)
    duration: int = Field(..., ge=30, le=180)
    notes: Optional[str] = Field(None, max_length=500)


class WaitlistResponse(BaseModel):
    """Response model for waitlist operations."""
    success: bool
    message: str
    waitlist_id: Optional[str] = None
    position: Optional[int] = None


class PanelStatus(BaseModel):
    """Model for panel status."""
    panel_name: str
    enabled: bool
    bookings_count: int
    blocked_slots_count: int


class DashboardStats(BaseModel):
    """Model for dashboard statistics."""
    total_bookings: int
    active_interviews: int
    completed_interviews: int
    waitlisted_count: int
    no_shows: int
    cancelled_count: int
    utilization_percent: float
    panels: List[PanelStatus]


class AuditLogEntry(BaseModel):
    """Model for audit log entry."""
    audit_id: str
    timestamp: str
    action: str
    booking_id: str
    old_value: str
    new_value: str
    performed_by: str
    reason: str


class TimeSlot(BaseModel):
    """Model for a time slot."""
    slot: str
    start_time: str
    end_time: str
    available_panels: List[str]
    blocked_panels: List[str]


class PanelGridCell(BaseModel):
    """Model for a panel grid cell."""
    date: str
    time_slot: str
    panel: str
    status: str  # FREE, BLOCKED, or BookingID
    booking: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Standard error response model."""
    success: bool = False
    error: str
    details: Optional[str] = None


class StatusCheckRequest(BaseModel):
    """Request model for checking booking status."""
    student_id: str = Field(..., min_length=1)
    booking_id: Optional[str] = Field(None, min_length=1)


class StatusCheckResponse(BaseModel):
    """Response model for booking status check."""
    bookings: List[Dict[str, Any]]
    student_name: Optional[str] = None
    message: Optional[str] = None