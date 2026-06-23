"""
RUBIX PANEL SCHEDULER - Reschedule Engine
Handles atomic reschedule operations with rollback capability,
waiting list replacement, and reschedule history tracking.
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
        3. Mark old booking as RESCHEDULED with RescheduledFlag=TRUE and PreviousSlot
        4. Create new booking with reference to old
        5. Log reschedule history in RESCHEDULE_HISTORY sheet
        6. Check waitlist for the vacated slot and auto-promote
        7. Update panel grid
        8. Log audit
        
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
                
                # Prevent double booking: check if student already has a booking at the new slot
                student_id = original_booking.get('StudentID', '')
                student_bookings = self._sheets.get_bookings_by_student(student_id)
                for b in student_bookings:
                    if (b.get('BookingID') != booking_id and
                        b.get('InterviewDate') == new_date and
                        b.get('StartTime') == new_time and
                        b.get('Status') in ['BOOKED', 'WAITLISTED']):
                        return {
                            'success': False,
                            'message': 'You already have a booking at this new time slot'
                        }
                
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
                
                # Prevent selecting occupied slots by checking conflicts explicitly
                end_time = slot.get('end_time', self._calculate_end_time(new_time, duration))
                conflicts = self._allocation.check_conflicts(
                    new_date, new_time, end_time,
                    slot.get('panel', ''),
                    exclude_booking_id=booking_id
                )
                if conflicts:
                    return {
                        'success': False,
                        'message': 'Selected slot is occupied. Please choose a different time.'
                    }
                
                # Check if the only available slot is the current booking's own slot
                if (slot.get('panel') == original_booking.get('AllocatedPanel') and
                    new_date == original_booking.get('InterviewDate') and
                    new_time == original_booking.get('StartTime')):
                    return {'success': False, 'message': 'Booking is already scheduled at this time'}
                
                # Store original values for audit, rollback, and history
                original_values = {
                    'booking_id': booking_id,
                    'date': original_booking.get('InterviewDate'),
                    'time': original_booking.get('StartTime'),
                    'panel': original_booking.get('AllocatedPanel'),
                    'status': original_booking.get('Status'),
                    'student_name': original_booking.get('StudentName', ''),
                    'student_id': original_booking.get('StudentID', ''),
                    'company': original_booking.get('Company', '')
                }
                
                old_slot_str = f"{original_values['date']} {original_values['time']} ({original_values['panel']})"
                new_slot_str = f"{new_date} {slot.get('start_time', new_time)} ({slot.get('panel', '')})"
                
                # Step 3: Mark old booking as RESCHEDULED with reschedule flag and previous slot
                self._sheets.update_booking(booking_id, {
                    'Status': 'RESCHEDULED',
                    'Notes': f"Rescheduled to {new_slot_str}. Reason: {reason or 'N/A'}"
                })
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
                    'Notes': f"Rescheduled from {original_values['date']} {original_values['time']}. Reason: {reason or 'N/A'}"
                }
                
                new_booking = self._sheets.create_booking(new_booking_data)
                rollback_steps.append(('delete_booking', new_booking.get('BookingID')))
                
                # Step 5: Log reschedule history in RESCHEDULE_HISTORY sheet
                # Get student email from the student record
                student_record = self._sheets.verify_student(original_values['student_id'])
                student_email = student_record.get('Email', '') if student_record else ''
                
                history_data = {
                    'StudentName': original_values['student_name'],
                    'Email': student_email,
                    'OldSlot': old_slot_str,
                    'NewSlot': new_slot_str,
                    'RescheduledBy': performed_by
                }
                history_id = self._sheets.log_reschedule_history(history_data)
                rollback_steps.append(('delete_history', history_id))
                
                # Step 6: Check waitlist for the vacated slot and auto-promote
                promoted_entry = None
                vacated_date = original_booking.get('InterviewDate', '')
                vacated_time = original_booking.get('StartTime', '')
                vacated_duration = int(original_booking.get('Duration', '30'))
                
                if self._config.is_auto_promote_enabled():
                    promoted_entry = self._try_promote_waitlist_for_slot(
                        vacated_date, vacated_time, vacated_duration
                    )
                
                # Step 7: Regenerate panel grid for affected dates
                affected_dates = set()
                affected_dates.add(original_booking.get('InterviewDate', ''))
                affected_dates.add(new_date)
                
                for date_str in affected_dates:
                    if date_str:
                        self._regenerate_grid_for_date(date_str)
                
                # Step 8: Log audit
                self._sheets.log_audit(
                    action='RESCHEDULE',
                    booking_id=new_booking.get('BookingID', ''),
                    old_value=f"Date:{original_values['date']}, Time:{original_values['time']}, Panel:{original_values['panel']}",
                    new_value=f"Date:{new_date}, Time:{slot.get('start_time')}, Panel:{slot.get('panel')}",
                    performed_by=performed_by,
                    reason=reason or 'Rescheduled'
                )
                
                self._sheets.log_audit(
                    action='RESCHEDULE_OLD',
                    booking_id=booking_id,
                    old_value='BOOKED',
                    new_value='RESCHEDULED',
                    performed_by=performed_by,
                    reason=f"Rescheduled to new booking {new_booking.get('BookingID')}"
                )
                
                # Log waitlist promotion if happened
                if promoted_entry:
                    self._sheets.log_audit(
                        action='WAITLIST_PROMOTED_FROM_RESCHEDULE',
                        booking_id=promoted_entry.get('BookingID', ''),
                        old_value=f"WaitlistID:{promoted_entry.get('WaitlistID', '')}",
                        new_value=f"BookingID:{promoted_entry.get('BookingID', '')}, Slot:{vacated_date} {vacated_time}",
                        performed_by='SYSTEM',
                        reason=f'Vacated by reschedule of booking {booking_id}'
                    )
                
                _logger.info(
                    f"Successfully rescheduled booking {booking_id} to {new_booking.get('BookingID')}. "
                    f"Promoted from waitlist: {promoted_entry is not None}"
                )
                
                return {
                    'success': True,
                    'message': 'Booking rescheduled successfully',
                    'old_booking_id': booking_id,
                    'new_booking': new_booking,
                    'history_id': history_id,
                    'waitlist_promoted': promoted_entry is not None,
                    'promoted_booking': promoted_entry
                }
                
            except Exception as e:
                _logger.error(f"Failed to reschedule booking {booking_id}: {e}")
                
                # Rollback any completed steps
                self._rollback(rollback_steps)
                
                return {
                    'success': False,
                    'message': f'Failed to reschedule: {str(e)}'
                }
    
    def _try_promote_waitlist_for_slot(self, date_str: str, time_str: str, duration: int) -> Optional[Dict[str, Any]]:
        """
        Check the waiting list for a specific freed slot and auto-promote
        the first matching waiting-list student into it.
        Returns the promoted booking details or None.
        """
        try:
            waitlist = self._sheets.get_waitlist()
            
            # Find first matching waitlist entry for this exact slot
            matching_entry = None
            for entry in waitlist:
                if (entry.get('PreferredDate') == date_str and
                    entry.get('PreferredTime') == time_str and
                    int(entry.get('Duration', '30')) == duration):
                    matching_entry = entry
                    break
            
            if not matching_entry:
                # Try with just date match if exact time doesn't match
                for entry in waitlist:
                    if (entry.get('PreferredDate') == date_str and
                        int(entry.get('Duration', '30')) == duration):
                        matching_entry = entry
                        break
            
            if not matching_entry:
                return None
            
            # Find an available slot (should be the vacated one)
            slot = self._allocation.find_available_slot(
                date_str, time_str, duration
            )
            
            if not slot:
                return None
            
            # Create booking for the promoted student
            promoted_student_id = matching_entry.get('StudentID', '')
            promoted_student_name = matching_entry.get('StudentName', '')
            promoted_company = matching_entry.get('Company', '')
            promoted_waitlist_id = matching_entry.get('WaitlistID', '')
            
            from datetime import timedelta
            start_dt = datetime.strptime(slot.get('start_time', time_str), '%H:%M')
            end_dt = start_dt + timedelta(minutes=duration)
            
            booking_data = {
                'StudentID': promoted_student_id,
                'StudentName': promoted_student_name,
                'Company': promoted_company,
                'InterviewDate': date_str,
                'StartTime': slot.get('start_time', time_str),
                'EndTime': end_dt.strftime('%H:%M'),
                'Duration': str(duration),
                'AllocatedPanel': slot.get('panel', ''),
                'Status': 'BOOKED',
                'Notes': f"Promoted from waitlist {promoted_waitlist_id} (vacated by reschedule)"
            }
            
            new_booking = self._sheets.create_booking(booking_data)
            
            # Mark waitlist entry as promoted
            self._sheets.promote_from_waitlist(promoted_waitlist_id)
            
            # Log reschedule history for the promoted student
            student_record = self._sheets.verify_student(promoted_student_id)
            student_email = student_record.get('Email', '') if student_record else ''
            
            old_slot_wl = f"Waitlist:{date_str} {time_str}"
            new_slot_wl = f"{date_str} {slot.get('start_time', time_str)} ({slot.get('panel', '')})"
            
            self._sheets.log_reschedule_history({
                'StudentName': promoted_student_name,
                'Email': student_email,
                'OldSlot': old_slot_wl,
                'NewSlot': new_slot_wl,
                'RescheduledBy': 'SYSTEM (Waitlist Promotion)'
            })
            
            # Log audit
            self._sheets.log_audit(
                action='PROMOTE_FROM_WAITLIST',
                booking_id=new_booking.get('BookingID', ''),
                old_value=f"WaitlistID:{promoted_waitlist_id}",
                new_value=f"BookingID:{new_booking.get('BookingID', '')}",
                performed_by='SYSTEM',
                reason='Auto-promotion from waitlist (slot vacated by reschedule)'
            )
            
            _logger.info(
                f"Promoted waitlist entry {promoted_waitlist_id} ({promoted_student_name}) "
                f"to booking {new_booking.get('BookingID')} at slot {date_str} {time_str}"
            )
            
            return {
                'BookingID': new_booking.get('BookingID'),
                'WaitlistID': promoted_waitlist_id,
                'StudentName': promoted_student_name,
                'StudentID': promoted_student_id
            }
            
        except Exception as e:
            _logger.error(f"Failed to promote waitlist for slot {date_str} {time_str}: {e}")
            return None
    
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
                
                elif step[0] == 'delete_history':
                    # History entries are append-only; we note the rollback
                    history_id = step[1]
                    _logger.info(f"History entry {history_id} will remain (append-only)")
                    
            except Exception as e:
                _logger.error(f"Failed to rollback step {step}: {e}")
    
    def admin_reschedule_booking(self, booking_id: str, new_date: str, new_time: str,
                                new_panel: str = None, reason: str = None) -> Dict[str, Any]:
        """
        Admin reschedule: more flexible than student reschedule.
        Admin can assign a different slot/panel if required.
        """
        with self._lock:
            rollback_steps = []
            
            try:
                # Get the existing booking
                original_booking = self._sheets.get_booking_by_id(booking_id)
                
                if not original_booking:
                    return {'success': False, 'message': 'Booking not found'}
                
                if original_booking.get('Status') not in ['BOOKED', 'WAITLISTED']:
                    return {
                        'success': False,
                        'message': f"Cannot reschedule booking with status: {original_booking.get('Status')}"
                    }
                
                # Check if the target panel is enabled (if specified)
                target_panel = new_panel or original_booking.get('AllocatedPanel', '')
                if not self._config.is_panel_enabled(target_panel):
                    return {'success': False, 'message': f'Panel {target_panel} is not enabled'}
                
                # Check if the slot is blocked
                time_slot = self._get_time_slot_for_time(new_time)
                if self._config.is_slot_blocked(target_panel, time_slot):
                    return {'success': False, 'message': f'This slot is blocked for {target_panel}'}
                
                # Check for conflicts
                duration = int(original_booking.get('Duration', '30'))
                end_time = self._calculate_end_time(new_time, duration)
                conflicts = self._allocation.check_conflicts(
                    new_date, new_time, end_time,
                    target_panel,
                    exclude_booking_id=booking_id
                )
                
                if conflicts:
                    return {
                        'success': False,
                        'message': 'Target slot is occupied. Please choose a different slot.'
                    }
                
                # Store original values
                original_values = {
                    'date': original_booking.get('InterviewDate'),
                    'time': original_booking.get('StartTime'),
                    'panel': original_booking.get('AllocatedPanel'),
                    'status': original_booking.get('Status'),
                    'student_name': original_booking.get('StudentName', ''),
                    'student_id': original_booking.get('StudentID', '')
                }
                
                old_slot_str = f"{original_values['date']} {original_values['time']} ({original_values['panel']})"
                new_slot_str = f"{new_date} {new_time} ({target_panel})"
                
                # Reschedule: mark old as RESCHEDULED and create new booking
                if new_date != original_values['date'] or new_time != original_values['time'] or new_panel != original_values['panel']:
                    # Mark old as RESCHEDULED
                    self._sheets.update_booking(booking_id, {
                        'Status': 'RESCHEDULED',
                        'Notes': f"Admin rescheduled to {new_slot_str}. Reason: {reason or 'Admin action'}"
                    })
                    rollback_steps.append(('restore_booking', booking_id, original_values))
                    
                    # Create new booking
                    new_booking_data = {
                        'StudentID': original_booking.get('StudentID', ''),
                        'StudentName': original_booking.get('StudentName', ''),
                        'Company': original_booking.get('Company', ''),
                        'InterviewDate': new_date,
                        'StartTime': new_time,
                        'EndTime': end_time,
                        'Duration': str(duration),
                        'AllocatedPanel': target_panel,
                        'Status': 'BOOKED',
                        'PreviousBookingID': booking_id,
                        'Notes': f"Admin rescheduled from {old_slot_str}. Reason: {reason or 'Admin action'}"
                    }
                    
                    new_booking = self._sheets.create_booking(new_booking_data)
                    rollback_steps.append(('delete_booking', new_booking.get('BookingID')))
                    
                    # Log reschedule history
                    student_record = self._sheets.verify_student(original_values['student_id'])
                    student_email = student_record.get('Email', '') if student_record else ''
                    
                    history_data = {
                        'StudentName': original_values['student_name'],
                        'Email': student_email,
                        'OldSlot': old_slot_str,
                        'NewSlot': new_slot_str,
                        'RescheduledBy': 'ADMIN'
                    }
                    history_id = self._sheets.log_reschedule_history(history_data)
                    rollback_steps.append(('delete_history', history_id))
                    
                    # Try waitlist promotion for vacated slot
                    promoted_entry = None
                    if self._config.is_auto_promote_enabled():
                        promoted_entry = self._try_promote_waitlist_for_slot(
                            original_values['date'],
                            original_values['time'],
                            duration
                        )
                    
                    # Regenerate grids
                    affected_dates = set()
                    affected_dates.add(original_values['date'])
                    affected_dates.add(new_date)
                    for date_str in affected_dates:
                        if date_str:
                            self._regenerate_grid_for_date(date_str)
                    
                    # Log audit
                    self._sheets.log_audit(
                        action='ADMIN_RESCHEDULE',
                        booking_id=new_booking.get('BookingID', ''),
                        old_value=old_slot_str,
                        new_value=new_slot_str,
                        performed_by='ADMIN',
                        reason=reason or 'Admin reschedule'
                    )
                    
                    return {
                        'success': True,
                        'message': 'Booking rescheduled by admin successfully',
                        'old_booking_id': booking_id,
                        'new_booking': new_booking,
                        'history_id': history_id,
                        'waitlist_promoted': promoted_entry is not None
                    }
                else:
                    return {'success': False, 'message': 'No changes to the booking slot'}
                    
            except Exception as e:
                _logger.error(f"Admin failed to reschedule booking {booking_id}: {e}")
                self._rollback(rollback_steps)
                return {'success': False, 'message': f'Failed to reschedule: {str(e)}'}
    
    def admin_move_booking(self, booking_id: str, new_date: str, new_time: str,
                          new_panel: str, reason: str = None) -> Dict[str, Any]:
        """
        Admin function to move a booking to a specific panel and time.
        More flexible than student reschedule.
        This is kept for backward compatibility but delegates to admin_reschedule_booking.
        """
        return self.admin_reschedule_booking(
            booking_id=booking_id,
            new_date=new_date,
            new_time=new_time,
            new_panel=new_panel,
            reason=reason
        )
    
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
    
    def get_reschedule_history(self, student_name: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get reschedule history from the RESCHEDULE_HISTORY sheet."""
        return self._sheets.get_reschedule_history(student_name, limit)


# Singleton instance
_reschedule_engine = None


def get_reschedule_engine() -> RescheduleEngine:
    """Get the singleton RescheduleEngine instance."""
    global _reschedule_engine
    if _reschedule_engine is None:
        _reschedule_engine = RescheduleEngine()
    return _reschedule_engine