"""
RUBIX PANEL SCHEDULER - Authentication Service
Handles session-based authentication for students and admins.
"""

import os
import hashlib
import secrets
import time
import threading
from typing import Any, Dict, Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(override=True)

_logger = __import__('logging').getLogger(__name__)


class SessionStore:
    """Thread-safe in-memory session store."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.sessions = {}
                    cls._instance.cleanup_interval = 300  # 5 minutes
                    cls._instance.last_cleanup = time.time()
        return cls._instance
    
    def create_session(self, user_id: str, user_type: str, data: Dict[str, Any] = None) -> str:
        """Create a new session and return session token."""
        session_id = secrets.token_urlsafe(32)
        
        self.sessions[session_id] = {
            'user_id': user_id,
            'user_type': user_type,
            'data': data or {},
            'created_at': time.time(),
            'expires_at': time.time() + self._get_session_expiry()
        }
        
        # Trigger cleanup if needed
        self._maybe_cleanup()
        
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data by token."""
        session = self.sessions.get(session_id)
        
        if session is None:
            return None
        
        # Check if expired
        if time.time() > session.get('expires_at', 0):
            self.destroy_session(session_id)
            return None
        
        return session
    
    def update_session(self, session_id: str, data: Dict[str, Any]) -> bool:
        """Update session data."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        
        session['data'].update(data)
        session['expires_at'] = time.time() + self._get_session_expiry()
        return True
    
    def destroy_session(self, session_id: str) -> bool:
        """Destroy a session."""
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False
    
    def _get_session_expiry(self) -> int:
        """Get session expiry time in seconds."""
        return int(os.getenv('SESSION_EXPIRE_MINUTES', '60')) * 60
    
    def _maybe_cleanup(self):
        """Clean up expired sessions if interval has passed."""
        current_time = time.time()
        if current_time - self.last_cleanup > self.cleanup_interval:
            self._cleanup()
            self.last_cleanup = current_time
    
    def _cleanup(self):
        """Remove all expired sessions."""
        current_time = time.time()
        expired = [
            sid for sid, session in self.sessions.items()
            if current_time > session.get('expires_at', 0)
        ]
        for sid in expired:
            del self.sessions[sid]
    
    def get_all_sessions_for_user(self, user_id: str, user_type: str) -> list:
        """Get all sessions for a user."""
        return [
            {'session_id': sid, **session}
            for sid, session in self.sessions.items()
            if session['user_id'] == user_id and session['user_type'] == user_type
        ]
    
    def destroy_all_sessions_for_user(self, user_id: str, user_type: str) -> int:
        """Destroy all sessions for a user. Returns count of destroyed sessions."""
        sessions = self.get_all_sessions_for_user(user_id, user_type)
        count = len(sessions)
        for session in sessions:
            self.destroy_session(session['session_id'])
        return count


class AuthService:
    """Authentication service for students and admins."""
    
    def __init__(self):
        self._session_store = SessionStore()
        self._admin_username = os.getenv('ADMIN_USERNAME', 'admin')
        self._admin_password_hash = self._hash_password(os.getenv('ADMIN_PASSWORD', 'Rubix@123'))
    
    def _hash_password(self, password: str) -> str:
        """Hash a password using SHA-256 with a salt."""
        salt = secrets.token_hex(16)
        password_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return f"{salt}:{password_hash}"
    
    def _verify_password(self, password: str, stored_hash: str) -> bool:
        """Verify a password against a stored hash."""
        try:
            salt, password_hash = stored_hash.split(':')
            check_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
            return check_hash == password_hash
        except ValueError:
            return False
    
    def verify_student(self, student_id: str, sheets_service) -> Dict[str, Any]:
        """
        Verify a student by ID.
        Returns verification result with student details if verified.
        """
        try:
            student = sheets_service.verify_student(student_id)
            
            if student is None:
                return {
                    'verified': False,
                    'message': 'You are not authorized for interview scheduling. Please contact the administrator.'
                }
            
            # Create session for student
            session_id = self._session_store.create_session(
                user_id=student_id,
                user_type='student',
                data={
                    'student_name': student.get('StudentName', ''),
                    'email': student.get('Email', ''),
                    'batch': student.get('Batch', '')
                }
            )
            
            return {
                'verified': True,
                'message': 'Student verified successfully',
                'session_id': session_id,
                'student': {
                    'student_id': student_id,
                    'student_name': student.get('StudentName', ''),
                    'email': student.get('Email', ''),
                    'batch': student.get('Batch', '')
                }
            }
            
        except Exception as e:
            _logger.error(f"Error verifying student {student_id}: {e}")
            return {
                'verified': False,
                'message': 'Error during verification. Please try again.'
            }
    
    def authenticate_admin(self, username: str, password: str) -> Dict[str, Any]:
        """
        Authenticate an admin user.
        Returns authentication result with session if successful.
        """
        if username != self._admin_username:
            return {
                'success': False,
                'message': 'Invalid username or password'
            }
        
        if not self._verify_password(password, self._admin_password_hash):
            return {
                'success': False,
                'message': 'Invalid username or password'
            }
        
        # Create session for admin
        session_id = self._session_store.create_session(
            user_id=username,
            user_type='admin',
            data={
                'username': username,
                'authenticated_at': datetime.now().isoformat()
            }
        )
        
        return {
            'success': True,
            'message': 'Admin authenticated successfully',
            'session_id': session_id,
            'redirect': '/admin/dashboard'
        }
    
    def validate_session(self, session_id: str, required_user_type: str = None) -> Dict[str, Any]:
        """
        Validate a session token.
        Returns session data if valid, error otherwise.
        """
        session = self._session_store.get_session(session_id)
        
        if session is None:
            return {
                'valid': False,
                'message': 'Invalid or expired session'
            }
        
        if required_user_type and session['user_type'] != required_user_type:
            return {
                'valid': False,
                'message': f'Access denied. Required role: {required_user_type}'
            }
        
        return {
            'valid': True,
            'session': session
        }
    
    def refresh_session(self, session_id: str) -> bool:
        """Refresh a session's expiry time."""
        return self._session_store.update_session(session_id, {})
    
    def logout(self, session_id: str) -> bool:
        """Destroy a session (logout)."""
        return self._session_store.destroy_session(session_id)
    
    def logout_all(self, user_id: str, user_type: str) -> int:
        """Logout from all devices."""
        return self._session_store.destroy_all_sessions_for_user(user_id, user_type)
    
    def get_session_store(self) -> SessionStore:
        """Get the session store instance."""
        return self._session_store


# Singleton instance
_auth_service = None


def get_auth_service() -> AuthService:
    """Get the singleton AuthService instance."""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


def get_session_store() -> SessionStore:
    """Get the singleton SessionStore instance."""
    return SessionStore()