"""
RUBIX PANEL SCHEDULER - Booking Allocation Engine
Handles slot allocation, conflict detection, and booking validation.
"""

import threading
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, time, timedelta
from .sheets_service import get_sheets_service
from .config_manager import get_config

_logger = __import__('logging').getLogger(__name__)


class AllocationEngine:
    """Thread-safe booking allocation engine with conflict detection."""
    
    _lock = threading.Lock()
    
    def __init__(self):
        self._sheets = get_sheets_service()
        self._config = get_config()
    
    def find_available_slot(self, date_str: str, start_time: str, duration: int, 
                           preferred_panel: str = None) -> Optional[Dict[str, Any]]:
        """
        Find an available slot for a booking.
        Returns dict with panel, date, start_time, end_time if found.
        """
        with self._lock:
            panels = self._config.get_panels()
            slot_duration = self._config.get_slot_duration()
            blocked_slots = self._config.get_blocked_slots()
            
            # Calculate required slots based on duration
            slots_needed = (duration + slot_duration - 1) // slot_duration
            
            # Generate all time slots for the date
            time_slots = self._config.generate_time_slots(date_str)
            
            # Get existing bookings for the date
            bookings = self._sheets.get_bookings_by_date(date_str)
            active_bookings = [b for b in bookings if b.get('Status') in ['BOOKED', 'WAITLISTED']]

            # Try to find a slot starting at the requested time only
            return self._find_slot_at_time(
                time_slots, start_time, slots_needed, panels,
                active_bookings, blocked_slots, preferred_panel
            )

    def _find_slot_at_time(self, time_slots: List[str], start_time: str, 
                          slots_needed: int, panels: List[str],
                          bookings: List[Dict], blocked_slots: Dict,
                          preferred_panel: str = None) -> Optional[Dict[str, Any]]:
        """Try to find a slot starting at the exact requested time."""
        # Find the index of the start time slot
        start_idx = -1
        for idx, slot in enumerate(time_slots):
            if slot.startswith(start_time):
                start_idx = idx
                break
        
        if start_idx == -1 or start_idx + slots_needed > len(time_slots):
            return None
        
        # Get the slots we need
        required_slots = time_slots[start_idx:start_idx + slots_needed]
        
        # Try each panel (preferred first if specified)
        panel_order = list(panels)
        if preferred_panel and preferred_panel in panel_order:
            panel_order.remove(preferred_panel)
            panel_order.insert(0, preferred_panel)
        
        for panel in panel_order:
            if self._are_slots_available(required_slots, panel, bookings, blocked_slots):
                end_time = required_slots[-1].split('-')[1]
                return {
                    'panel': panel,
                    'date': None,  # Will be set by caller
                    'start_time': start_time,
                    'end_time': end_time,
                    'slots_used': required_slots
                }
        
        return None
    
    def _find_next_available_slot(self, time_slots: List[str], slots_needed: int,
                                 panels: List[str], bookings: List[Dict],
                                 blocked_slots: Dict,
                                 preferred_panel: str = None) -> Optional[Dict[str, Any]]:
        """Find the next available slot."""
        for start_idx in range(len(time_slots) - slots_needed + 1):
            required_slots = time_slots[start_idx:start_idx + slots_needed]
            
            panel_order = list(panels)
            if preferred_panel and preferred_panel in panel_order:
                panel_order.remove(preferred_panel)
                panel_order.insert(0, preferred_panel)
            
            for panel in panel_order:
                if self._are_slots_available(required_slots, panel, bookings, blocked_slots):
                    start_time = required_slots[0].split('-')[0]
                    end_time = required_slots[-1].split('-')[1]
                    return {
                        'panel': panel,
                        'date': None,
                        'start_time': start_time,
                        'end_time': end_time,
                        'slots_used': required_slots
                    }
        
        return None
    
    def _are_slots_available(self, slots: List[str], panel: str,
                            bookings: List[Dict], blocked_slots: Dict) -> bool:
        """Check if all slots are available for a panel."""
        # Check if panel is blocked for any of the slots
        for slot in slots:
            if self._config.is_slot_blocked(panel, slot):
                return False
        
        # Check for overlapping bookings
        for booking in bookings:
            if booking.get('AllocatedPanel') != panel:
                continue
            if booking.get('Status') not in ['BOOKED', 'WAITLISTED']:
                continue
            
            booking_start = booking.get('StartTime', '')
            booking_end = booking.get('EndTime', '')
            booking_date = booking.get('InterviewDate', '')
            
            # Check if this booking overlaps with our required slots
            if self._does_booking_overlap_slots(booking_start, booking_end, slots):
                return False
        
        return True
    
    def _does_booking_overlap_slots(self, booking_start: str, booking_end: str,
                                    slots: List[str]) -> bool:
        """Check if a booking overlaps with any of the required slots."""
        if not booking_start or not booking_end:
            return False
        
        for slot in slots:
            slot_parts = slot.split('-')
            if len(slot_parts) != 2:
                continue
            
            slot_start = slot_parts[0].strip()
            slot_end = slot_parts[1].strip()
            
            # Check overlap: booking overlaps if it starts before slot ends AND ends after slot starts
            if booking_start < slot_end and booking_end > slot_start:
                return True
        
        return False
    
    def check_conflicts(self, date_str: str, start_time: str, end_time: str,
                       panel: str, exclude_booking_id: str = None) -> List[Dict[str, Any]]:
        """
        Check for conflicts with existing bookings.
        Returns list of conflicting bookings.
        """
        conflicts = []
        bookings = self._sheets.get_bookings_by_date(date_str)
        
        for booking in bookings:
            if booking.get('BookingID') == exclude_booking_id:
                continue
            if booking.get('AllocatedPanel') != panel:
                continue
            if booking.get('Status') not in ['BOOKED', 'WAITLISTED']:
                continue
            
            booking_start = booking.get('StartTime', '')
            booking_end = booking.get('EndTime', '')
            
            # Check time overlap
            if start_time < booking_end and end_time > booking_start:
                conflicts.append(booking)
        
        return conflicts
    
    def validate_booking_request(self, student_id: str, date_str: str, 
                                start_time: str, duration: int) -> Tuple[bool, str]:
        """
        Validate a booking request before creation.
        Returns (is_valid, error_message).
        """
        # Validate date is not in the past
        try:
            booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            if booking_date < datetime.now().date():
                return False, "Cannot book interviews in the past"
        except ValueError:
            return False, "Invalid date format. Use YYYY-MM-DD"
        
        # Validate date is not too far in the future (e.g., 90 days)
        max_date = datetime.now().date() + timedelta(days=90)
        if booking_date > max_date:
            return False, f"Cannot book more than 90 days in advance"
        
        # Validate time format
        try:
            datetime.strptime(start_time, '%H:%M')
        except ValueError:
            return False, "Invalid time format. Use HH:MM"
        
        # Validate duration
        min_duration = self._config.get_min_duration()
        max_duration = self._config.get_max_duration()
        
        if duration < min_duration:
            return False, f"Minimum duration is {min_duration} minutes"
        if duration > max_duration:
            return False, f"Maximum duration is {max_duration} minutes"
        
        if duration % self._config.get_slot_duration() != 0:
            return False, f"Duration must be a multiple of {self._config.get_slot_duration()} minutes"
        
        # Check if student already has a booking at this time
        student_bookings = self._sheets.get_bookings_by_student(student_id)
        for booking in student_bookings:
            if (booking.get('InterviewDate') == date_str and 
                booking.get('Status') in ['BOOKED', 'WAITLISTED']):
                booking_start = booking.get('StartTime', '')
                booking_end = booking.get('EndTime', '')
                end_time = self._calculate_end_time(start_time, duration)
                
                if start_time < booking_end and end_time > booking_start:
                    return False, "You already have a booking at this time"
        
        # Check working hours
        work_start = self._config.get_working_hours_start()
        work_end = self._config.get_working_hours_end()
        
        start_t = datetime.strptime(start_time, '%H:%M').time()
        end_t = self._calculate_end_time_obj(start_time, duration)
        
        if start_t < work_start or end_t > work_end:
            return False, "Booking must be within working hours"
        
        return True, ""
    
    def _calculate_end_time(self, start_time: str, duration_minutes: int) -> str:
        """Calculate end time from start time and duration."""
        start_dt = datetime.strptime(start_time, '%H:%M')
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        return end_dt.strftime('%H:%M')
    
    def _calculate_end_time_obj(self, start_time: str, duration_minutes: int) -> time:
        """Calculate end time as time object."""
        start_dt = datetime.strptime(start_time, '%H:%M')
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        return end_dt.time()
    
    def get_slot_availability(self, date_str: str) -> Dict[str, Dict[str, str]]:
        """
        Get availability for all slots on a given date.
        Returns {time_slot: {panel: status}}
        """
        time_slots = self._config.generate_time_slots(date_str)
        panels = self._config.get_panels()
        blocked_slots = self._config.get_blocked_slots()
        bookings = self._sheets.get_bookings_by_date(date_str)
        
        availability = {}
        
        for slot in time_slots:
            availability[slot] = {}
            for panel in panels:
                if self._config.is_slot_blocked(panel, slot):
                    availability[slot][panel] = 'BLOCKED'
                else:
                    # Check for bookings
                    is_booked = False
                    for booking in bookings:
                        if (booking.get('AllocatedPanel') == panel and
                            booking.get('Status') in ['BOOKED', 'WAITLISTED']):
                            booking_start = booking.get('StartTime', '')
                            booking_end = booking.get('EndTime', '')
                            if self._does_booking_overlap_slots(booking_start, booking_end, [slot]):
                                is_booked = True
                                break
                    
                    availability[slot][panel] = 'BOOKED' if is_booked else 'FREE'
        
        return availability
    
    def get_panel_utilization(self, date_str: str = None) -> Dict[str, Dict[str, Any]]:
        """
        Calculate panel utilization statistics.
        """
        panels = self._config.get_panels()
        time_slots = self._config.generate_time_slots(date_str)
        bookings = self._sheets.get_all_bookings() if not date_str else self._sheets.get_bookings_by_date(date_str)
        blocked_slots = self._config.get_blocked_slots()
        
        utilization = {}
        
        for panel in panels:
            total_slots = len(time_slots)
            booked_slots = 0
            blocked_count = 0
            
            for slot in time_slots:
                if self._config.is_slot_blocked(panel, slot):
                    blocked_count += 1
                else:
                    for booking in bookings:
                        if (booking.get('AllocatedPanel') == panel and
                            booking.get('Status') in ['BOOKED', 'WAITLISTED']):
                            booking_start = booking.get('StartTime', '')
                            booking_end = booking.get('EndTime', '')
                            if self._does_booking_overlap_slots(booking_start, booking_end, [slot]):
                                booked_slots += 1
                                break
            
            available_slots = total_slots - blocked_count
            utilization_rate = (booked_slots / available_slots * 100) if available_slots > 0 else 0
            
            utilization[panel] = {
                'total_slots': total_slots,
                'booked_slots': booked_slots,
                'blocked_slots': blocked_count,
                'available_slots': available_slots,
                'utilization_percent': round(utilization_rate, 2)
            }
        
        return utilization


# Singleton instance
_allocation_engine = None


def get_allocation_engine() -> AllocationEngine:
    """Get the singleton AllocationEngine instance."""
    global _allocation_engine
    if _allocation_engine is None:
        _allocation_engine = AllocationEngine()
    return _allocation_engine