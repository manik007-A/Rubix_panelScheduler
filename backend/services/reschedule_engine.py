"""
RUBIX PANEL SCHEDULER - Reschedule Engine
Handles atomic reschedule operations with rollback capability.
"""

import threading
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from .sheets_service import get_sheets_service
from .config_manager import get_config
from .allocation_engine import get_allocation_engine
from .waitlist_engine import get_waitlist_engine

_logger = __import__('logging').getLogger(__name__)


class RescheduleEngine:
    """Thread-safe reschedule engine with atomic operations."""
    
    _lock = threading.Lock()
    
    def __init__(self):
        self._sheets = get_sheets_service()
        self._config = get_config()
        self._allocation = get_allocation_engine()
        self._waitlist = get_waitlist_engine()
    
    def reschedule_booking(self, booking_id: str, new_date: str, new_time: str,
                          reason: str = None, performed_by: str = 'STUDENT') -> Dict[str, Any]:
        """
        Reschedule a booking atomically.
        
        Steps:
        1. Validate the existing booking
        2. Validate the new slot
        3. Mark old booking as RESCHEDULED
        4. Create new booking with reference to old
        5. Update panel grid
        6. Promote waitlist if applicable
        7. Log audit
        
        Rollback on any failure.
        """
        with self._lock:
            rollback_steps = []
            
            try:
                # Step 1: Get and validate the existing booking
                original_booking = self._sheets.get_booking_by_id(booking_id)
                
                if not original_booking:
                    return {'success': False, 'message': 'Booking not found'}
                
                if original_booking.get('Status') not in ['BOOKED', 'WAITLISTED']:
                    return {
                        'success': False, 
                        'message': f"Cannot reschedule booking with status: {original_booking.get('Status')}"
                    }
                
                # Check if booking is in the past
                booking_date = datetime.strptime(original_booking.get('InterviewDate', ''), '%Y-%m-%d').date()
                if booking_date < datetime.now().date():
                    return {'success': False, 'message': 'Cannot reschedule past bookings'}
                
                # Check if booking is today and already started
                if booking_date == datetime.now().date():
                    booking_start = original_booking.get('StartTime', '')
                    current_time = datetime.now().strftime('%H:%M')
                    if booking_start <= current_time:
                        return {'success': False, 'message': 'Cannot reschedule a booking that has already started'}
                
                # Step 2: Validate and find available slot
                duration = int(original_booking.get('Duration', '30'))
                
                slot = self._allocation.find_available_slot(
                    new_date, new_time, duration
                )
                
                if not slot:
                    return {
                        'success': False,
                        'message': 'No available slots at the requested time. Consider joining the waitlist.'
                    }
                
                # Check if the only available slot is the current booking's own slot
                if (slot.get('panel') == original_booking.get('AllocatedPanel') and
                    new_date == original_booking.get('InterviewDate') and
                    new_time == original_booking.get('StartTime')):
                    return {'success': False, 'message': 'Booking is already scheduled at this time'}
                
                # Store original values for audit and rollback
                original_values = {
                    'booking_id': booking_id,
                    'date': original_booking.get('InterviewDate'),
                    'time': original_booking.get('StartTime'),
                    'panel': original_booking.get('AllocatedPanel'),
                    'status': original_booking.get('Status')
                }
                
                # Step 3: Mark old booking as RESCHEDULED
                self._sheets.update_booking(booking_id, {'Status': 'RESCHEDULED'})
                rollback_steps.append(('restore_booking', booking_id, original_values))
                
                # Step 4: Create new booking
                new_booking_data = {
                    'StudentID': original_booking.get('StudentID', ''),
                    'StudentName': original_booking.get('StudentName', ''),
                    'Company': original_booking.get('Company', ''),
                    'InterviewDate': new_date,
                    'StartTime': slot.get('start_time', new_time),
                    'EndTime': slot.get('end_time', ''),
                    'Duration': str(duration),
                    'AllocatedPanel': slot.get('panel', ''),
                    'Status': 'BOOKED',
                    'PreviousBookingID': booking_id,
                    'Notes': f"Rescheduled from {original_booking.get('InterviewDate')} {original_booking.get('StartTime')}. Reason: {reason or 'N/A'}"
                }
                
                new_booking = self._sheets.create_booking(new_booking_data)
                rollback_steps.append(('delete_booking', new_booking.get('BookingID')))
                
                # Step 5: Regenerate panel grid for affected dates
                affected_dates = set()
                affected_dates.add(original_booking.get('InterviewDate', ''))
                affected_dates.add(new_date)
                
                for date_str in affected_dates:
                    if date_str:
                        self._regenerate_grid_for_date(date_str)
                
                # Step 6: Try to promote waitlist for the freed slot
                if self._config.is_auto_promote_enabled():
                    self._waitlist._try_promote_waitlist(
                        original_booking.get('InterviewDate'),
                        original_booking.get('StartTime')
                    )
                
                # Step 7: Log audit
                self._sheets.log_audit(
                    action='RESCHEDULE',
                    booking_id=new_booking.get('BookingID', ''),
                    old_value=f"Date:{original_values['date']}, Time:{original_values['time']}, Panel:{original_values['panel']}",
                    new_value=f"Date:{new_date}, Time:{slot.get('start_time')}, Panel:{slot.get('panel')}",
                    performed_by=performed_by,
                    reason=reason or 'Rescheduled'
                )
                
                # Log the original booking change too
                self._sheets.log_audit(
                    action='RESCHEDULE_OLD',
                    booking_id=booking_id,
                    old_value='BOOKED',
                    new_value='RESCHEDULED',
                    performed_by=performed_by,
                    reason=f"Rescheduled to new booking {new_booking.get('BookingID')}"
                )
                
                _logger.info(f"Successfully rescheduled booking {booking_id} to {new_booking.get('BookingID')}")
                
                return {
                    'success': True,
                    'message': 'Booking rescheduled successfully',
                    'old_booking_id': booking_id,
                    'new_booking': new_booking
                }
                
            except Exception as e:
                _logger.error(f"Failed to reschedule booking {booking_id}: {e}")
                
                # Rollback any completed steps
                self._rollback(rollback_steps)
                
                return {
                    'success': False,
                    'message': f'Failed to reschedule: {str(e)}'
                }
    
    def _rollback(self, steps: List[Tuple[str, ...]]):
        """Rollback completed steps in reverse order."""
        for step in reversed(steps):
            try:
                if step[0] == 'restore_booking':
                    booking_id = step[1]
                    original_values = step[2]
                    self._sheets.update_booking(booking_id, {
                        'Status': original_values['status']
                    })
                    _logger.info(f"Rolled back booking {booking_id} to status {original_values['status']}")
                
                elif step[0] == 'delete_booking':
                    # Mark the new booking as cancelled since we can't delete
                    booking_id = step[1]
                    self._sheets.update_booking(booking_id, {'Status': 'CANCELLED'})
                    _logger.info(f"Rolled back by cancelling booking {booking_id}")
                    
            except Exception as e:
                _logger.error(f"Failed to rollback step {step}: {e}")
    
    def admin_move_booking(self, booking_id: str, new_date: str, new_time: str,
                          new_panel: str, reason: str = None) -> Dict[str, Any]:
        """
        Admin function to move a booking to a specific panel and time.
        More flexible than student reschedule.
        """
        with self._lock:
            try:
                # Get the existing booking
                original_booking = self._sheets.get_booking_by_id(booking_id)
                
                if not original_booking:
                    return {'success': False, 'message': 'Booking not found'}
                
                if original_booking.get('Status') not in ['BOOKED', 'WAITLISTED']:
                    return {
                        'success': False,
                        'message': f"Cannot move booking with status: {original_booking.get('Status')}"
                    }
                
                # Check if the panel is enabled
                if not self._config.is_panel_enabled(new_panel):
                    return {'success': False, 'message': f'Panel {new_panel} is not enabled'}
                
                # Check if the slot is blocked
                time_slot = self._get_time_slot_for_time(new_time)
                if self._config.is_slot_blocked(new_panel, time_slot):
                    return {'success': False, 'message': f'This slot is blocked for {new_panel}'}
                
                # Check for conflicts
                conflicts = self._allocation.check_conflicts(
                    new_date, new_time, 
                    self._calculate_end_time(new_time, int(original_booking.get('Duration', '30'))),
                    new_panel,
                    exclude_booking_id=booking_id
                )
                
                if conflicts:
                    return {
                        'success': False,
                        'message': 'Target slot conflicts with existing booking'
                    }
                
                # Calculate end time
                duration = int(original_booking.get('Duration', '30'))
                end_time = self._calculate_end_time(new_time, duration)
                
                # Store original values
                original_values = {
                    'date': original_booking.get('InterviewDate'),
                    'time': original_booking.get('StartTime'),
                    'panel': original_booking.get('AllocatedPanel')
                }
                
                # Update the booking
                updates = {
                    'InterviewDate': new_date,
                    'StartTime': new_time,
                    'EndTime': end_time,
                    'AllocatedPanel': new_panel,
                    'Notes': f"Moved by admin. Original: {original_values['date']} {original_values['time']} {original_values['panel']}. Reason: {reason or 'Admin move'}"
                }
                
                self._sheets.update_booking(booking_id, updates)
                
                # Regenerate panel grid for affected dates
                affected_dates = set()
                affected_dates.add(original_values['date'])
                affected_dates.add(new_date)
                
                for date_str in affected_dates:
                    if date_str:
                        self._regenerate_grid_for_date(date_str)
                
                # Log audit
                self._sheets.log_audit(
                    action='ADMIN_MOVE_BOOKING',
                    booking_id=booking_id,
                    old_value=f"Date:{original_values['date']}, Time:{original_values['time']}, Panel:{original_values['panel']}",
                    new_value=f"Date:{new_date}, Time:{new_time}, Panel:{new_panel}",
                    performed_by='ADMIN',
                    reason=reason or 'Admin move'
                )
                
                # Try to promote waitlist for freed slot
                if self._config.is_auto_promote_enabled():
                    self._waitlist._try_promote_waitlist(
                        original_values['date'],
                        original_values['time']
                    )
                
                return {
                    'success': True,
                    'message': 'Booking moved successfully',
                    'booking_id': booking_id
                }
                
            except Exception as e:
                _logger.error(f"Failed to move booking {booking_id}: {e}")
                return {'success': False, 'message': f'Failed to move booking: {str(e)}'}
    
    def _get_time_slot_for_time(self, time_str: str) -> str:
        """Get the time slot string for a given time."""
        slot_duration = self._config.get_slot_duration()
        
        try:
            t = datetime.strptime(time_str, '%H:%M')
            end_t = t + timedelta(minutes=slot_duration)
            return f"{t.strftime('%H:%M')}-{end_t.strftime('%H:%M')}"
        except ValueError:
            return time_str
    
    def _calculate_end_time(self, start_time: str, duration_minutes: int) -> str:
        """Calculate end time from start time and duration."""
        start_dt = datetime.strptime(start_time, '%H:%M')
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        return end_dt.strftime('%H:%M')
    
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
    
    def can_reschedule(self, booking_id: str) -> Tuple[bool, str]:
        """
        Check if a booking can be rescheduled.
        Returns (can_reschedule, reason).
        """
        booking = self._sheets.get_booking_by_id(booking_id)
        
        if not booking:
            return False, "Booking not found"
        
        if booking.get('Status') not in ['BOOKED', 'WAITLISTED']:
            return False, f"Cannot reschedule booking with status: {booking.get('Status')}"
        
        booking_date = datetime.strptime(booking.get('InterviewDate', ''), '%Y-%m-%d').date()
        
        if booking_date < datetime.now().date():
            return False, "Cannot reschedule past bookings"
        
        if booking_date == datetime.now().date():
            booking_start = booking.get('StartTime', '')
            current_time = datetime.now().strftime('%H:%M')
            if booking_start <= current_time:
                return False, "Cannot reschedule a booking that has already started"
        
        return True, ""


# Singleton instance
_reschedule_engine = None


def get_reschedule_engine() -> RescheduleEngine:
    """Get the singleton RescheduleEngine instance."""
    global _reschedule_engine
    if _reschedule_engine is None:
        _reschedule_engine = RescheduleEngine()
    return _reschedule_engine