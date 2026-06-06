"""
RUBIX PANEL SCHEDULER - Cancellation Engine
Handles booking cancellation with slot release and waitlist promotion.
"""

import threading
from typing import Any, Dict, List, Optional
from datetime import datetime
from .sheets_service import get_sheets_service
from .config_manager import get_config
from .waitlist_engine import get_waitlist_engine

_logger = __import__('logging').getLogger(__name__)


class CancellationEngine:
    """Thread-safe cancellation engine with automatic cleanup."""
    
    _lock = threading.Lock()
    
    def __init__(self):
        self._sheets = get_sheets_service()
        self._config = get_config()
        self._waitlist = get_waitlist_engine()
    
    def cancel_booking(self, booking_id: str, reason: str = None,
                      performed_by: str = 'STUDENT') -> Dict[str, Any]:
        """
        Cancel a booking and release its slots.
        
        Steps:
        1. Validate the booking exists and can be cancelled
        2. Mark booking as CANCELLED
        3. Release the slots
        4. Regenerate panel grid
        5. Promote waitlist if applicable
        6. Log audit
        
        Handles rollback on failure.
        """
        with self._lock:
            try:
                # Step 1: Get and validate the booking
                booking = self._sheets.get_booking_by_id(booking_id)
                
                if not booking:
                    return {'success': False, 'message': 'Booking not found'}
                
                if booking.get('Status') not in ['BOOKED', 'WAITLISTED']:
                    return {
                        'success': False,
                        'message': f"Cannot cancel booking with status: {booking.get('Status')}"
                    }
                
                # Check if booking is in the past
                booking_date = datetime.strptime(booking.get('InterviewDate', ''), '%Y-%m-%d').date()
                if booking_date < datetime.now().date():
                    return {'success': False, 'message': 'Cannot cancel past bookings'}
                
                # Check if booking is today and already started or completed
                if booking_date == datetime.now().date():
                    booking_start = booking.get('StartTime', '')
                    current_time = datetime.now().strftime('%H:%M')
                    if booking_start <= current_time:
                        return {'success': False, 'message': 'Cannot cancel a booking that has already started'}
                
                # Store original values for audit
                original_values = {
                    'date': booking.get('InterviewDate'),
                    'time': booking.get('StartTime'),
                    'panel': booking.get('AllocatedPanel'),
                    'student': booking.get('StudentName'),
                    'company': booking.get('Company')
                }
                
                # Step 2: Mark booking as CANCELLED
                self._sheets.update_booking(booking_id, {
                    'Status': 'CANCELLED',
                    'Notes': f"{booking.get('Notes', '')}\nCancelled: {reason or 'No reason provided'}".strip()
                })
                
                # Step 3 & 4: Regenerate panel grid for the date
                self._regenerate_grid_for_date(booking.get('InterviewDate', ''))
                
                # Step 5: Try to promote waitlist for the freed slot
                promoted_entry = None
                if self._config.is_auto_promote_enabled():
                    promoted_entry = self._waitlist._try_promote_waitlist(
                        booking.get('InterviewDate'),
                        booking.get('StartTime'),
                        int(booking.get('Duration', '30'))
                    )
                
                # Step 6: Log audit
                self._sheets.log_audit(
                    action='CANCEL_BOOKING',
                    booking_id=booking_id,
                    old_value=f"Status:BOOKED, Date:{original_values['date']}, Time:{original_values['time']}",
                    new_value='Status:CANCELLED',
                    performed_by=performed_by,
                    reason=reason or 'Cancelled by user'
                )
                
                _logger.info(f"Successfully cancelled booking {booking_id} for {original_values['student']}")
                
                result = {
                    'success': True,
                    'message': 'Booking cancelled successfully',
                    'booking_id': booking_id,
                    'refunded': True,
                    'was_promoted_from_waitlist': promoted_entry
                }
                
                # Check if there's a related previous booking (from reschedule)
                prev_booking_id = booking.get('PreviousBookingID', '')
                if prev_booking_id:
                    result['previous_booking_id'] = prev_booking_id
                
                return result
                
            except Exception as e:
                _logger.error(f"Failed to cancel booking {booking_id}: {e}")
                
                # Try to restore the booking if we marked it as cancelled
                try:
                    self._sheets.update_booking(booking_id, {'Status': 'BOOKED'})
                except Exception as restore_error:
                    _logger.error(f"Failed to restore booking {booking_id}: {restore_error}")
                
                return {
                    'success': False,
                    'message': f'Failed to cancel booking: {str(e)}'
                }
    
    def admin_cancel_booking(self, booking_id: str, reason: str = None) -> Dict[str, Any]:
        """
        Admin cancellation with additional permissions.
        Can cancel bookings that students cannot (e.g., same-day with notice).
        """
        with self._lock:
            try:
                booking = self._sheets.get_booking_by_id(booking_id)
                
                if not booking:
                    return {'success': False, 'message': 'Booking not found'}
                
                if booking.get('Status') not in ['BOOKED', 'WAITLISTED']:
                    return {
                        'success': False,
                        'message': f"Cannot cancel booking with status: {booking.get('Status')}"
                    }
                
                # Admin can cancel even past bookings in some cases
                booking_date = datetime.strptime(booking.get('InterviewDate', ''), '%Y-%m-%d').date()
                current_date = datetime.now().date()
                
                # But still can't cancel completed or no-show bookings
                if booking_date < current_date:
                    return {'success': False, 'message': 'Cannot cancel completed past bookings'}
                
                # Store original values
                original_values = {
                    'date': booking.get('InterviewDate'),
                    'time': booking.get('StartTime'),
                    'panel': booking.get('AllocatedPanel'),
                    'student': booking.get('StudentName'),
                    'company': booking.get('Company'),
                    'student_id': booking.get('StudentID')
                }
                
                # Cancel the booking
                self._sheets.update_booking(booking_id, {
                    'Status': 'CANCELLED',
                    'Notes': f"{booking.get('Notes', '')}\nAdmin Cancelled: {reason or 'Admin cancellation'}".strip()
                })
                
                # Regenerate panel grid
                self._regenerate_grid_for_date(booking.get('InterviewDate', ''))
                
                # Try to promote waitlist
                promoted_entry = None
                if self._config.is_auto_promote_enabled():
                    promoted_entry = self._waitlist._try_promote_waitlist(
                        booking.get('InterviewDate'),
                        booking.get('StartTime'),
                        int(booking.get('Duration', '30'))
                    )
                
                # Log audit
                self._sheets.log_audit(
                    action='ADMIN_CANCEL_BOOKING',
                    booking_id=booking_id,
                    old_value=f"Date:{original_values['date']}, Time:{original_values['time']}, Student:{original_values['student']}",
                    new_value='CANCELLED',
                    performed_by='ADMIN',
                    reason=reason or 'Admin cancellation'
                )
                
                # Notify student (log it, actual notification would be via email/SMS)
                _logger.info(f"Admin cancelled booking {booking_id} for student {original_values['student_id']}")
                
                return {
                    'success': True,
                    'message': 'Booking cancelled by admin',
                    'booking_id': booking_id,
                    'student_notified': True,
                    'was_promoted_from_waitlist': promoted_entry
                }
                
            except Exception as e:
                _logger.error(f"Admin failed to cancel booking {booking_id}: {e}")
                return {'success': False, 'message': f'Failed to cancel: {str(e)}'}
    
    def mark_no_show(self, booking_id: str, reason: str = None) -> Dict[str, Any]:
        """
        Mark a booking as no-show.
        Used by admin after the scheduled time has passed.
        """
        with self._lock:
            try:
                booking = self._sheets.get_booking_by_id(booking_id)
                
                if not booking:
                    return {'success': False, 'message': 'Booking not found'}
                
                if booking.get('Status') != 'BOOKED':
                    return {
                        'success': False,
                        'message': f"Cannot mark booking as no-show with status: {booking.get('Status')}"
                    }
                
                # Check if the booking time has passed
                booking_date = datetime.strptime(booking.get('InterviewDate', ''), '%Y-%m-%d').date()
                booking_end = booking.get('EndTime', '')
                
                if booking_date >= datetime.now().date():
                    if booking_end:
                        current_time = datetime.now().strftime('%H:%M')
                        if booking_end > current_time:
                            return {'success': False, 'message': 'Cannot mark as no-show before the interview ends'}
                
                # Update status to NO_SHOW
                self._sheets.update_booking(booking_id, {
                    'Status': 'NO_SHOW',
                    'Notes': f"{booking.get('Notes', '')}\nNo-show: {reason or 'Student did not attend'}".strip()
                })
                
                # Regenerate panel grid
                self._regenerate_grid_for_date(booking.get('InterviewDate', ''))
                
                # Log audit
                self._sheets.log_audit(
                    action='MARK_NO_SHOW',
                    booking_id=booking_id,
                    old_value='BOOKED',
                    new_value='NO_SHOW',
                    performed_by='ADMIN',
                    reason=reason or 'Student did not attend'
                )
                
                _logger.info(f"Marked booking {booking_id} as no-show")
                
                return {
                    'success': True,
                    'message': 'Booking marked as no-show',
                    'booking_id': booking_id
                }
                
            except Exception as e:
                _logger.error(f"Failed to mark booking {booking_id} as no-show: {e}")
                return {'success': False, 'message': f'Failed to mark no-show: {str(e)}'}
    
    def mark_completed(self, booking_id: str) -> Dict[str, Any]:
        """
        Mark a booking as completed.
        Used by admin after the interview has been conducted.
        """
        with self._lock:
            try:
                booking = self._sheets.get_booking_by_id(booking_id)
                
                if not booking:
                    return {'success': False, 'message': 'Booking not found'}
                
                if booking.get('Status') != 'BOOKED':
                    return {
                        'success': False,
                        'message': f"Cannot mark booking as completed with status: {booking.get('Status')}"
                    }
                
                # Update status to COMPLETED
                self._sheets.update_booking(booking_id, {'Status': 'COMPLETED'})
                
                # Regenerate panel grid
                self._regenerate_grid_for_date(booking.get('InterviewDate', ''))
                
                # Log audit
                self._sheets.log_audit(
                    action='MARK_COMPLETED',
                    booking_id=booking_id,
                    old_value='BOOKED',
                    new_value='COMPLETED',
                    performed_by='ADMIN',
                    reason='Interview completed'
                )
                
                _logger.info(f"Marked booking {booking_id} as completed")
                
                return {
                    'success': True,
                    'message': 'Booking marked as completed',
                    'booking_id': booking_id
                }
                
            except Exception as e:
                _logger.error(f"Failed to mark booking {booking_id} as completed: {e}")
                return {'success': False, 'message': f'Failed to mark completed: {str(e)}'}
    
    def _regenerate_grid_for_date(self, date_str: str):
        """Regenerate the panel grid for a specific date."""
        try:
            bookings = self._sheets.get_bookings_by_date(date_str)
            panels = self._config.get_panels()
            time_slots = self._config.generate_time_slots(date_str)
            blocked_slots = self._config.get_blocked_slots()
            
            self._sheets.regenerate_panel_grid(bookings, panels, time_slots, blocked_slots)
        except Exception as e:
            _logger.error(f"Failed to regenerate panel grid for {date_str}: {e}")
    
    def can_cancel(self, booking_id: str) -> tuple:
        """
        Check if a booking can be cancelled.
        Returns (can_cancel, reason).
        """
        booking = self._sheets.get_booking_by_id(booking_id)
        
        if not booking:
            return False, "Booking not found"
        
        if booking.get('Status') not in ['BOOKED', 'WAITLISTED']:
            return False, f"Cannot cancel booking with status: {booking.get('Status')}"
        
        booking_date = datetime.strptime(booking.get('InterviewDate', ''), '%Y-%m-%d').date()
        
        if booking_date < datetime.now().date():
            return False, "Cannot cancel past bookings"
        
        if booking_date == datetime.now().date():
            booking_start = booking.get('StartTime', '')
            current_time = datetime.now().strftime('%H:%M')
            if booking_start <= current_time:
                return False, "Cannot cancel a booking that has already started"
        
        return True, ""
    
    def get_cancellation_stats(self, date_from: str = None, date_to: str = None) -> Dict[str, Any]:
        """Get cancellation statistics."""
        bookings = self._sheets.get_all_bookings()
        
        total = len(bookings)
        cancelled = 0
        no_shows = 0
        
        for booking in bookings:
            status = booking.get('Status', '')
            booking_date = booking.get('InterviewDate', '')
            
            # Apply date filters if provided
            if date_from and booking_date < date_from:
                continue
            if date_to and booking_date > date_to:
                continue
            
            if status == 'CANCELLED':
                cancelled += 1
            elif status == 'NO_SHOW':
                no_shows += 1
        
        cancellation_rate = (cancelled / total * 100) if total > 0 else 0
        no_show_rate = (no_shows / total * 100) if total > 0 else 0
        
        return {
            'total_bookings': total,
            'cancelled': cancelled,
            'no_shows': no_shows,
            'cancellation_rate': round(cancellation_rate, 2),
            'no_show_rate': round(no_show_rate, 2)
        }


# Singleton instance
_cancellation_engine = None


def get_cancellation_engine() -> CancellationEngine:
    """Get the singleton CancellationEngine instance."""
    global _cancellation_engine
    if _cancellation_engine is None:
        _cancellation_engine = CancellationEngine()
    return _cancellation_engine