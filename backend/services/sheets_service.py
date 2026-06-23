"""
RUBIX PANEL SCHEDULER - Google Sheets Service
Handles all interactions with Google Sheets API with retry logic and error recovery.
"""

import os
import time
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)


class SheetsService:
    """Google Sheets service with retry logic and error handling."""
    
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
    MAX_RETRIES = 3
    RETRY_DELAY = 1  # seconds
    RETRY_BACKOFF = 2  # multiplier
    
    # Sheet names
    MASTER_BOOKINGS = 'MASTER_BOOKINGS'
    MASTER_STUDENTS = 'MASTER_STUDENTS'
    PANEL_GRID = 'PANEL_GRID'
    CONFIG = 'CONFIG'
    AUDIT_LOG = 'AUDIT_LOG'
    WAITLIST = 'WAITLIST'
    RESCHEDULE_HISTORY = 'RESCHEDULE_HISTORY'
    
    def __init__(self):
        self.spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
        self.service_account_file = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', 'credentials.json')
        self._service = None
        self._initialize_service()
    
    def _initialize_service(self):
        """Initialize the Google Sheets API service."""
        try:
            credentials = service_account.Credentials.from_service_account_file(
                self.service_account_file, scopes=self.SCOPES
            )
            self._service = build('sheets', 'v4', credentials=credentials)
            logger.info("Google Sheets service initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets service: {e}")
            raise
    
    def _execute_with_retry(self, func, *args, **kwargs):
        """Execute a function with exponential backoff retry logic."""
        last_exception = None
        for attempt in range(self.MAX_RETRIES):
            try:
                if attempt > 0:
                    delay = self.RETRY_DELAY * (self.RETRY_BACKOFF ** (attempt - 1))
                    logger.warning(f"Retry attempt {attempt + 1}/{self.MAX_RETRIES} after {delay}s")
                    time.sleep(delay)
                return func(*args, **kwargs)
            except HttpError as e:
                if e.resp.status == 429:  # Rate limit
                    retry_after = int(e.resp.get('Retry-After', self.RETRY_DELAY * (2 ** attempt)))
                    logger.warning(f"Rate limited. Waiting {retry_after}s before retry")
                    time.sleep(retry_after)
                    continue
                last_exception = e
                logger.error(f"HTTP error on attempt {attempt + 1}: {e}")
            except Exception as e:
                last_exception = e
                logger.error(f"Error on attempt {attempt + 1}: {e}")
        
        raise last_exception
    
    def _get_sheet_values(self, sheet_name: str, range_str: str = '') -> List[List[Any]]:
        """Get values from a sheet with retry logic."""
        def _fetch():
            range_notation = f"{sheet_name}!{range_str}" if range_str else f"{sheet_name}!A:Z"
            result = self._service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_notation,
                valueRenderOption='FORMATTED_VALUE',
                dateTimeRenderOption='FORMATTED_STRING'
            ).execute()
            return result.get('values', [])
        
        return self._execute_with_retry(_fetch)
    
    def _update_sheet_values(self, sheet_name: str, range_str: str, values: List[List[Any]]):
        """Update values in a sheet with retry logic."""
        def _update():
            body = {'values': values}
            return self._service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{sheet_name}!{range_str}",
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
        
        return self._execute_with_retry(_update)
    
    def _append_sheet_values(self, sheet_name: str, values: List[List[Any]]):
        """Append values to a sheet with retry logic."""
        def _append():
            body = {'values': values}
            return self._service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{sheet_name}!A:Z",
                valueInputOption='USER_ENTERED',
                insertDataOption='INSERT_ROWS',
                body=body
            ).execute()
        
        return self._execute_with_retry(_append)
    
    def _batch_update(self, sheet_name: str, updates: List[Dict[str, Any]]):
        """Perform batch update on a sheet."""
        def _batch():
            body = {
                'requests': [
                    {
                        'updateCells': {
                            'range': {
                                'sheetId': self._get_sheet_id(sheet_name),
                                'startRowIndex': update['row'],
                                'endRowIndex': update['row'] + 1,
                                'startColumnIndex': update['col'],
                                'endColumnIndex': update['col'] + 1
                            },
                            'rows': [{'values': [{'userEnteredValue': {'stringValue': str(update['value'])}}]}],
                            'fields': 'userEnteredValue'
                        }
                    }
                    for update in updates
                ]
            }
            return self._service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body=body
            ).execute()
        
        return self._execute_with_retry(_batch)
    
    def _get_sheet_id(self, sheet_name: str) -> int:
        """Get the sheet ID for a given sheet name."""
        try:
            metadata = self._service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id
            ).execute()
            for sheet in metadata.get('sheets', []):
                if sheet['properties']['title'] == sheet_name:
                    return sheet['properties']['sheetId']
        except Exception:
            pass
        return 0
    
    def ensure_sheets_exist(self):
        """Ensure all required sheets exist with proper headers."""
        try:
            metadata = self._service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id
            ).execute()
            
            existing_sheets = [sheet['properties']['title'] for sheet in metadata.get('sheets', [])]
            
            requests = []
            
            # Define sheet structures
            sheet_structures = {
                self.MASTER_BOOKINGS: [
                    ['BookingID', 'StudentID', 'StudentName', 'Company', 'InterviewDate', 
                     'StartTime', 'EndTime', 'Duration', 'AllocatedPanel', 'Status', 
                     'PreviousBookingID', 'CreatedAt', 'ModifiedAt', 'Notes']
                ],
                self.MASTER_STUDENTS: [
                    ['StudentID', 'StudentName', 'Email', 'Batch', 'Status', 'EligibleForScheduling']
                ],
                self.PANEL_GRID: [
                    ['TimeSlot', 'Panel1', 'Panel2', 'Panel3', 'Panel4', 'Panel5', 'Panel6']
                ],
                self.CONFIG: [
                    ['Key', 'Value']
                ],
                self.AUDIT_LOG: [
                    ['AuditID', 'Timestamp', 'Action', 'BookingID', 'OldValue', 'NewValue', 'PerformedBy', 'Reason']
                ],
                self.WAITLIST: [
                    ['WaitlistID', 'StudentID', 'StudentName', 'Company', 'PreferredDate', 
                     'PreferredTime', 'Duration', 'Status', 'CreatedAt', 'Notes']
                ],
                self.RESCHEDULE_HISTORY: [
                    ['HistoryID', 'StudentName', 'Email', 'OldSlot', 'NewSlot', 
                     'RescheduledBy', 'Timestamp']
                ]
            }
            
            for sheet_name, headers in sheet_structures.items():
                if sheet_name not in existing_sheets:
                    requests.append({
                        'addSheet': {
                            'properties': {'title': sheet_name}
                        }
                    })
            
            if requests:
                self._service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={'requests': requests}
                ).execute()
                
                # Add headers to new sheets
                for sheet_name, headers in sheet_structures.items():
                    if sheet_name not in existing_sheets:
                        self._append_sheet_values(sheet_name, headers)
                
                logger.info(f"Created missing sheets: {[r['addSheet']['properties']['title'] for r in requests]}")
            
            # Initialize default config values if not present
            self._initialize_default_config()
            
        except Exception as e:
            logger.error(f"Error ensuring sheets exist: {e}")
            raise
    
    def _initialize_default_config(self):
        """Initialize default configuration values."""
        config_values = self.get_all_config()
        
        defaults = {
            'working_hours_start': '09:00',
            'working_hours_end': '18:00',
            'slot_duration': '30',
            'max_duration': '90',
            'min_duration': '30',
            'auto_promote_waitlist': 'TRUE',
            'panels': 'Panel1,Panel2,Panel3',
            'blocked_slots': '',
            'last_booking_counter': '0'
        }
        
        updates = []
        for key, value in defaults.items():
            if key not in config_values:
                updates.append([key, value])
        
        if updates:
            self._append_sheet_values(self.CONFIG, updates)
    
    # MASTER_BOOKINGS operations
    def get_all_bookings(self) -> List[Dict[str, Any]]:
        """Get all bookings from the sheet."""
        values = self._get_sheet_values(self.MASTER_BOOKINGS)
        if not values or len(values) < 2:
            return []
        
        headers = values[0]
        bookings = []
        for row in values[1:]:
            if row and row[0]:  # Has BookingID
                booking = {}
                for i, header in enumerate(headers):
                    booking[header] = row[i] if i < len(row) else ''
                bookings.append(booking)
        return bookings
    
    def get_booking_by_id(self, booking_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific booking by ID."""
        bookings = self.get_all_bookings()
        for booking in bookings:
            if booking.get('BookingID') == booking_id:
                return booking
        return None
    
    def get_bookings_by_student(self, student_id: str) -> List[Dict[str, Any]]:
        """Get all bookings for a specific student."""
        bookings = self.get_all_bookings()
        student_id_upper = student_id.strip().upper()
        return [b for b in bookings if b.get('StudentID', '').strip().upper() == student_id_upper]
    
    def get_bookings_by_date(self, date_str: str) -> List[Dict[str, Any]]:
        """Get all bookings for a specific date."""
        bookings = self.get_all_bookings()
        return [b for b in bookings if b.get('InterviewDate') == date_str]
    
    def create_booking(self, booking_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new booking."""
        # Get next booking ID
        config = self.get_all_config()
        counter = int(config.get('last_booking_counter', '0')) + 1
        booking_id = f"BK{counter:06d}"
        
        # Update counter
        self.update_config('last_booking_counter', str(counter))
        
        # Prepare row data
        now = datetime.now().isoformat()
        row = [
            booking_id,
            booking_data.get('StudentID', ''),
            booking_data.get('StudentName', ''),
            booking_data.get('Company', ''),
            booking_data.get('InterviewDate', ''),
            booking_data.get('StartTime', ''),
            booking_data.get('EndTime', ''),
            booking_data.get('Duration', ''),
            booking_data.get('AllocatedPanel', ''),
            booking_data.get('Status', 'BOOKED'),
            booking_data.get('PreviousBookingID', ''),
            now,  # CreatedAt
            now,  # ModifiedAt
            booking_data.get('Notes', '')
        ]
        
        self._append_sheet_values(self.MASTER_BOOKINGS, [row])
        
        booking_data['BookingID'] = booking_id
        booking_data['CreatedAt'] = now
        booking_data['ModifiedAt'] = now
        return booking_data
    
    def update_booking(self, booking_id: str, updates: Dict[str, Any]) -> bool:
        """Update an existing booking."""
        bookings = self.get_all_bookings()
        headers = ['BookingID', 'StudentID', 'StudentName', 'Company', 'InterviewDate', 
                   'StartTime', 'EndTime', 'Duration', 'AllocatedPanel', 'Status', 
                   'PreviousBookingID', 'CreatedAt', 'ModifiedAt', 'Notes']
        
        for idx, booking in enumerate(bookings):
            if booking.get('BookingID') == booking_id:
                row_num = idx + 2  # +2 for header and 1-based indexing
                row_data = [booking.get(h, '') for h in headers]
                
                # Apply updates
                for key, value in updates.items():
                    if key in headers:
                        row_data[headers.index(key)] = value
                
                # Update ModifiedAt
                row_data[headers.index('ModifiedAt')] = datetime.now().isoformat()
                
                # Update the row
                range_str = f"A{row_num}:N{row_num}"
                self._update_sheet_values(self.MASTER_BOOKINGS, range_str, [row_data])
                return True
        
        return False
    
    def cancel_booking(self, booking_id: str) -> bool:
        """Cancel a booking by updating its status."""
        return self.update_booking(booking_id, {'Status': 'CANCELLED'})
    
    # MASTER_STUDENTS operations
    def get_all_students(self) -> List[Dict[str, Any]]:
        """Get all students from the sheet."""
        values = self._get_sheet_values(self.MASTER_STUDENTS)
        if not values or len(values) < 2:
            return []
        
        headers = values[0]
        students = []
        for row in values[1:]:
            if row and row[0]:  # Has StudentID
                student = {}
                for i, header in enumerate(headers):
                    student[header] = row[i] if i < len(row) else ''
                students.append(student)
        return students
    
    def verify_student(self, student_id: str) -> Optional[Dict[str, Any]]:
        """Verify a student by ID (case-insensitive, trimmed)."""
        students = self.get_all_students()
        student_id_normalized = student_id.strip().upper()
        
        for student in students:
            if student.get('StudentID', '').strip().upper() == student_id_normalized:
                # Check if active and eligible
                status = student.get('Status', '').strip().upper()
                eligible = student.get('EligibleForScheduling', '').strip().upper()
                
                if status not in ['ACTIVE', 'ACTIVATE']:
                    return None
                if eligible != 'TRUE':
                    return None
                    
                return student
        
        return None
    
    # CONFIG operations
    def get_all_config(self) -> Dict[str, str]:
        """Get all configuration values."""
        values = self._get_sheet_values(self.CONFIG)
        config = {}
        for row in values[1:]:  # Skip header
            if row and len(row) >= 2:
                config[row[0]] = row[1]
        return config
    
    def get_config(self, key: str) -> Optional[str]:
        """Get a specific configuration value."""
        config = self.get_all_config()
        return config.get(key)
    
    def update_config(self, key: str, value: str) -> bool:
        """Update a configuration value."""
        values = self._get_sheet_values(self.CONFIG)
        
        for idx, row in enumerate(values[1:], start=2):
            if row and row[0] == key:
                range_str = f"B{idx}:B{idx}"
                self._update_sheet_values(self.CONFIG, range_str, [[value]])
                return True
        
        # Key not found, add new
        self._append_sheet_values(self.CONFIG, [[key, value]])
        return True
    
    # PANEL_GRID operations
    def get_panel_grid(self) -> List[List[Any]]:
        """Get the panel grid."""
        return self._get_sheet_values(self.PANEL_GRID)
    
    def update_panel_grid_cell(self, row: int, panel: str, value: str):
        """Update a specific cell in the panel grid."""
        values = self._get_sheet_values(self.PANEL_GRID)
        
        # Find column index for panel
        if not values:
            raise ValueError("Panel grid is empty")
        
        headers = values[0]
        try:
            col_idx = headers.index(panel)
        except ValueError:
            raise ValueError(f"Panel '{panel}' not found in grid")
        
        range_str = f"{chr(65 + col_idx)}{row + 1}:{chr(65 + col_idx)}{row + 1}"
        self._update_sheet_values(self.PANEL_GRID, range_str, [[value]])
    
    def regenerate_panel_grid(self, bookings: List[Dict[str, Any]], panels: List[str], 
                              time_slots: List[str], blocked_slots: Dict[str, List[str]]):
        """Regenerate the entire panel grid based on current bookings."""
        # Build grid structure
        grid = [["TimeSlot"] + panels]  # Header row
        
        for slot in time_slots:
            row = [slot]
            for panel in panels:
                cell_value = 'FREE'
                
                # Check if slot is blocked for this panel
                if panel in blocked_slots and slot in blocked_slots[panel]:
                    cell_value = 'BLOCKED'
                else:
                    # Check for bookings
                    for booking in bookings:
                        if (booking.get('AllocatedPanel') == panel and 
                            booking.get('Status') in ['BOOKED', 'WAITLISTED'] and
                            self._does_booking_overlap_slot(
                                booking.get('StartTime', ''),
                                booking.get('EndTime', ''),
                                slot
                            )):
                            cell_value = booking.get('BookingID', '')
                            break
                
                row.append(cell_value)
            grid.append(row)
        
        # Write entire grid
        range_str = f"A1:{chr(65 + len(panels))}{len(time_slots) + 1}"
        self._update_sheet_values(self.PANEL_GRID, range_str, grid)
    
    def _does_booking_overlap_slot(self, booking_start: str, booking_end: str, slot_str: str) -> bool:
        """Check whether a booking overlaps with a specific time slot."""
        if not booking_start or not booking_end or not slot_str:
            return False
        
        try:
            booking_start_dt = datetime.strptime(booking_start, '%H:%M')
            booking_end_dt = datetime.strptime(booking_end, '%H:%M')
            slot_parts = slot_str.split('-')
            if len(slot_parts) != 2:
                return False
            slot_start_dt = datetime.strptime(slot_parts[0].strip(), '%H:%M')
            slot_end_dt = datetime.strptime(slot_parts[1].strip(), '%H:%M')
            return booking_start_dt < slot_end_dt and booking_end_dt > slot_start_dt
        except Exception:
            return False
    
    # AUDIT_LOG operations
    def log_audit(self, action: str, booking_id: str = '', old_value: str = '', 
                  new_value: str = '', performed_by: str = '', reason: str = '') -> str:
        """Log an audit entry."""
        audit_id = f"AUD{int(time.time() * 1000)}"
        timestamp = datetime.now().isoformat()
        
        row = [audit_id, timestamp, action, booking_id, old_value, new_value, performed_by, reason]
        self._append_sheet_values(self.AUDIT_LOG, [row])
        
        return audit_id
    
    def get_audit_logs(self, booking_id: str = '', limit: int = 100) -> List[Dict[str, Any]]:
        """Get audit logs, optionally filtered by booking ID."""
        values = self._get_sheet_values(self.AUDIT_LOG)
        if not values or len(values) < 2:
            return []
        
        headers = values[0]
        logs = []
        
        for row in values[1:]:
            if row and row[0]:
                log = {}
                for i, header in enumerate(headers):
                    log[header] = row[i] if i < len(row) else ''
                
                if not booking_id or log.get('BookingID') == booking_id:
                    logs.append(log)
                
                if len(logs) >= limit:
                    break
        
        return logs
    
    # WAITLIST operations
    def get_waitlist(self) -> List[Dict[str, Any]]:
        """Get all waitlisted entries."""
        values = self._get_sheet_values(self.WAITLIST)
        if not values or len(values) < 2:
            return []
        
        headers = values[0]
        waitlist = []
        
        for row in values[1:]:
            if row and row[0]:
                entry = {}
                for i, header in enumerate(headers):
                    entry[header] = row[i] if i < len(row) else ''
                if entry.get('Status', '').upper() == 'WAITLISTED':
                    waitlist.append(entry)
        
        # Sort by creation time (FIFO)
        waitlist.sort(key=lambda x: x.get('CreatedAt', ''))
        return waitlist
    
    def add_to_waitlist(self, waitlist_data: Dict[str, Any]) -> str:
        """Add an entry to the waitlist."""
        waitlist_id = f"WL{int(time.time() * 1000)}"
        now = datetime.now().isoformat()
        
        row = [
            waitlist_id,
            waitlist_data.get('StudentID', ''),
            waitlist_data.get('StudentName', ''),
            waitlist_data.get('Company', ''),
            waitlist_data.get('PreferredDate', ''),
            waitlist_data.get('PreferredTime', ''),
            waitlist_data.get('Duration', ''),
            'WAITLISTED',
            now,
            waitlist_data.get('Notes', '')
        ]
        
        self._append_sheet_values(self.WAITLIST, [row])
        return waitlist_id
    
    def promote_from_waitlist(self, waitlist_id: str) -> bool:
        """Promote a waitlisted entry (mark as processed)."""
        values = self._get_sheet_values(self.WAITLIST)
        
        for idx, row in enumerate(values[1:], start=2):
            if row and row[0] == waitlist_id:
                range_str = f"H{idx}:H{idx}"  # Status column
                self._update_sheet_values(self.WAITLIST, range_str, [['PROMOTED']])
                return True
        
        return False
    
    def get_next_waitlist_entry(self, preferred_date: str = None, preferred_time: str = None) -> Optional[Dict[str, Any]]:
        """Get the next eligible waitlist entry (FIFO)."""
        waitlist = self.get_waitlist()
        
        for entry in waitlist:
            # If preferences are specified, check for match
            if preferred_date and entry.get('PreferredDate') != preferred_date:
                continue
            if preferred_time and entry.get('PreferredTime') != preferred_time:
                continue
            return entry
        
        return None
    
    # RESCHEDULE_HISTORY operations
    def log_reschedule_history(self, history_data: Dict[str, Any]) -> str:
        """Log a reschedule history entry."""
        history_id = f"RH{int(time.time() * 1000)}"
        timestamp = datetime.now().isoformat()
        
        row = [
            history_id,
            history_data.get('StudentName', ''),
            history_data.get('Email', ''),
            history_data.get('OldSlot', ''),
            history_data.get('NewSlot', ''),
            history_data.get('RescheduledBy', ''),
            timestamp
        ]
        
        self._append_sheet_values(self.RESCHEDULE_HISTORY, [row])
        return history_id
    
    def get_reschedule_history(self, student_name: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get reschedule history entries, optionally filtered by student name."""
        values = self._get_sheet_values(self.RESCHEDULE_HISTORY)
        if not values or len(values) < 2:
            return []
        
        headers = values[0]
        history = []
        
        for row in values[1:]:
            if row and row[0]:
                entry = {}
                for i, header in enumerate(headers):
                    entry[header] = row[i] if i < len(row) else ''
                
                if not student_name or entry.get('StudentName', '').upper() == student_name.upper():
                    history.append(entry)
                
                if len(history) >= limit:
                    break
        
        # Sort by timestamp descending
        history.sort(key=lambda x: x.get('Timestamp', ''), reverse=True)
        return history


# Singleton instance
_sheets_service = None


def get_sheets_service() -> SheetsService:
    """Get the singleton SheetsService instance."""
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = SheetsService()
    return _sheets_service