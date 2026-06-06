"""
RUBIX PANEL SCHEDULER - Waitlist Engine
Handles waitlist management with FIFO ordering and auto-promotion.
"""

import threading
from typing import Any, Dict, List, Optional
from datetime import datetime
from .sheets_service import get_sheets_service
from .config_manager import get_config
from .allocation_engine import get_allocation_engine

_logger = __import__('logging').getLogger(__name__)


class WaitlistEngine:
    """Thread-safe waitlist management engine."""
    
    _lock = threading.Lock()
    
    def __init__(self):
        self._sheets = get_sheets_service()
        self._config = get_config()
        self._allocation = get_allocation_engine()
    
    def add_to_waitlist(self, student_id: str, student_name: str, company: str,
                       preferred_date: str, preferred_time: str, duration: int,
                       notes: str = None) -> Dict[str, Any]:
        """
        Add a student to the waitlist.
        Returns waitlist entry details including position.
        """
        with self._lock:
            waitlist_data = {
                'StudentID': student_id,
                'StudentName': student_name,
                'Company': company,
                'PreferredDate': preferred_date,
                'PreferredTime': preferred_time,
                'Duration': str(duration),
                'Notes': notes or ''
            }
            
            waitlist_id = self._sheets.add_to_waitlist(waitlist_data)
            
            # Get position in queue
            waitlist = self._sheets.get_waitlist()
            position = len(waitlist)
            
            # Log audit
            self._sheets.log_audit(
                action='ADD_TO_WAITLIST',
                booking_id='',
                old_value='',
                new_value=f"WaitlistID:{waitlist_id}, Student:{student_name}, Company:{company}",
                performed_by=student_id,
                reason=notes or 'Added to waitlist'
            )
            
            # Try auto-promotion if enabled
            if self._config.is_auto_promote_enabled():
                self._try_promote_waitlist(preferred_date, preferred_time, duration)
            
            return {
                'waitlist_id': waitlist_id,
                'position': position,
                'student_id': student_id,
                'student_name': student_name,
                'company': company,
                'preferred_date': preferred_date,
                'preferred_time': preferred_time,
                'duration': duration
            }
    
    def _try_promote_waitlist(self, preferred_date: str = None, 
                             preferred_time: str = None,
                             duration: int = None) -> bool:
        """
        Try to promote waitlist entries when slots become available.
        """
        waitlist = self._sheets.get_waitlist()
        
        for entry in waitlist:
            # Filter by preferences if specified
            if preferred_date and entry.get('PreferredDate') != preferred_date:
                continue
            if preferred_time and entry.get('PreferredTime') != preferred_time:
                continue
            
            entry_duration = int(entry.get('Duration', '30'))
            if duration and entry_duration != duration:
                continue
            
            # Try to find a slot for this waitlist entry
            slot = self._allocation.find_available_slot(
                entry.get('PreferredDate', ''),
                entry.get('PreferredTime', ''),
                entry_duration
            )
            
            if slot:
                # Promote this entry
                return self._promote_entry(entry, slot)
        
        return False
    
    def _promote_entry(self, entry: Dict[str, Any], slot: Dict[str, Any]) -> bool:
        """
        Promote a waitlist entry to a confirmed booking.
        """
        try:
            # Create the booking
            booking_data = {
                'StudentID': entry.get('StudentID', ''),
                'StudentName': entry.get('StudentName', ''),
                'Company': entry.get('Company', ''),
                'InterviewDate': slot.get('date', entry.get('PreferredDate', '')),
                'StartTime': slot.get('start_time', ''),
                'Duration': entry.get('Duration', '30'),
                'AllocatedPanel': slot.get('panel', ''),
                'Notes': f"Promoted from waitlist {entry.get('WaitlistID', '')}"
            }
            
            # Calculate end time
            from datetime import timedelta
            start_dt = datetime.strptime(slot.get('start_time', '00:00'), '%H:%M')
            end_dt = start_dt + timedelta(minutes=int(entry.get('Duration', '30')))
            booking_data['EndTime'] = end_dt.strftime('%H:%M')
            
            booking = self._sheets.create_booking(booking_data)
            
            # Mark waitlist entry as promoted
            self._sheets.promote_from_waitlist(entry.get('WaitlistID', ''))
            
            # Regenerate panel grid
            self._regenerate_grid_for_date(booking.get('InterviewDate', ''))
            
            # Log audit
            self._sheets.log_audit(
                action='PROMOTE_FROM_WAITLIST',
                booking_id=booking.get('BookingID', ''),
                old_value=f"WaitlistID:{entry.get('WaitlistID', '')}",
                new_value=f"BookingID:{booking.get('BookingID', '')}",
                performed_by='SYSTEM',
                reason='Auto-promotion from waitlist'
            )
            
            _logger.info(f"Promoted waitlist entry {entry.get('WaitlistID')} to booking {booking.get('BookingID')}")
            return True
            
        except Exception as e:
            _logger.error(f"Failed to promote waitlist entry {entry.get('WaitlistID')}: {e}")
            return False
    
    def promote_specific_entry(self, waitlist_id: str, target_date: str = None,
                              target_time: str = None) -> Dict[str, Any]:
        """
        Manually promote a specific waitlist entry.
        """
        with self._lock:
            # Get the waitlist entry
            waitlist = self._sheets.get_waitlist()
            entry = None
            
            for e in waitlist:
                if e.get('WaitlistID') == waitlist_id:
                    entry = e
                    break
            
            if not entry:
                return {'success': False, 'message': 'Waitlist entry not found'}
            
            # Try to find a slot
            duration = int(entry.get('Duration', '30'))
            preferred_date = target_date or entry.get('PreferredDate', '')
            preferred_time = target_time or entry.get('PreferredTime', '')
            
            slot = self._allocation.find_available_slot(
                preferred_date, preferred_time, duration
            )
            
            if not slot:
                # Try to find any available slot on the preferred date
                slot = self._allocation.find_available_slot(preferred_date, None, duration)
            
            if not slot:
                return {
                    'success': False, 
                    'message': 'No available slots for this waitlist entry'
                }
            
            # Promote the entry
            success = self._promote_entry(entry, slot)
            
            if success:
                return {
                    'success': True,
                    'message': 'Waitlist entry promoted successfully',
                    'booking_id': slot.get('booking_id')
                }
            else:
                return {'success': False, 'message': 'Failed to promote waitlist entry'}
    
    def get_waitlist(self, date_filter: str = None) -> List[Dict[str, Any]]:
        """
        Get the current waitlist, optionally filtered by date.
        """
        waitlist = self._sheets.get_waitlist()
        
        if date_filter:
            waitlist = [e for e in waitlist if e.get('PreferredDate') == date_filter]
        
        # Add position numbers
        for idx, entry in enumerate(waitlist, 1):
            entry['Position'] = idx
        
        return waitlist
    
    def remove_from_waitlist(self, waitlist_id: str) -> bool:
        """
        Remove an entry from the waitlist.
        """
        with self._lock:
            # Get the entry first for audit
            waitlist = self._sheets.get_waitlist()
            entry = None
            
            for e in waitlist:
                if e.get('WaitlistID') == waitlist_id:
                    entry = e
                    break
            
            if not entry:
                return False
            
            # Mark as removed (we don't delete, just change status)
            values = self._sheets._get_sheet_values(self._sheets.WAITLIST)
            
            for idx, row in enumerate(values[1:], start=2):
                if row and row[0] == waitlist_id:
                    range_str = f"H{idx}:H{idx}"  # Status column
                    self._sheets._update_sheet_values(self._sheets.WAITLIST, range_str, [['REMOVED']])
                    
                    # Log audit
                    self._sheets.log_audit(
                        action='REMOVE_FROM_WAITLIST',
                        booking_id='',
                        old_value=f"WaitlistID:{waitlist_id}",
                        new_value='REMOVED',
                        performed_by='ADMIN',
                        reason=f"Removed waitlist entry for {entry.get('StudentName', '')}"
                    )
                    
                    return True
            
            return False
    
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
    
    def get_waitlist_count(self, date_filter: str = None) -> int:
        """Get the total number of waitlisted entries."""
        return len(self.get_waitlist(date_filter))
    
    def check_waitlist_for_slot(self, date_str: str, time_slot: str, 
                               panel: str = None) -> Optional[Dict[str, Any]]:
        """
        Check if any waitlist entry matches a newly available slot.
        Returns the first matching entry or None.
        """
        waitlist = self._sheets.get_waitlist()
        
        for entry in waitlist:
            if entry.get('PreferredDate') != date_str:
                continue
            
            # Check time preference match
            if entry.get('PreferredTime') == time_slot.split('-')[0]:
                return entry
            
            # Also check if they're flexible (no specific time)
            if not entry.get('PreferredTime'):
                return entry
        
        return None


# Singleton instance
_waitlist_engine = None


def get_waitlist_engine() -> WaitlistEngine:
    """Get the singleton WaitlistEngine instance."""
    global _waitlist_engine
    if _waitlist_engine is None:
        _waitlist_engine = WaitlistEngine()
    return _waitlist_engine