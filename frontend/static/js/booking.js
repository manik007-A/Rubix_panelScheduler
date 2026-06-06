/**
 * RUBIX PANEL SCHEDULER - Booking Page JavaScript
 */

// State
let currentUser = null;
let selectedDate = new Date();
let selectedDuration = 30;
let selectedTime = null;
let config = null;

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    checkExistingSession();
    loadConfig();
});

// Tab switching
function switchTab(tab) {
    document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
    
    document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
    document.getElementById(`${tab}Form`).classList.add('active');
}

// Loading overlay
function showLoading() {
    document.getElementById('loadingOverlay').classList.add('active');
}

function hideLoading() {
    document.getElementById('loadingOverlay').classList.remove('active');
}

// Alert system
function showAlert(message, type = 'info') {
    const container = document.getElementById('alertContainer');
    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        <span>${message}</span>
    `;
    container.appendChild(alert);
    
    setTimeout(() => {
        alert.remove();
    }, 5000);
}

// Check existing session
async function checkExistingSession() {
    try {
        const response = await fetch('/api/session/validate');
        if (response.ok) {
            const data = await response.json();
            if (data.valid) {
                currentUser = data;
                if (data.user_type === 'student') {
                    showBookingView();
                } else if (data.user_type === 'admin') {
                    window.location.href = '/admin/dashboard';
                }
            }
        }
    } catch (error) {
        console.error('Session check failed:', error);
    }
}

// Load config
async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        if (response.ok) {
            config = await response.json();
        }
    } catch (error) {
        console.error('Failed to load config:', error);
    }
}

// Student verification
async function verifyStudent() {
    const studentId = document.getElementById('studentId').value.trim();
    
    if (!studentId) {
        showAlert('Please enter your student ID', 'danger');
        return;
    }
    
    showLoading();
    
    try {
        const response = await fetch('/api/verify-student', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ student_id: studentId })
        });
        
        const data = await response.json();
        
        if (data.verified) {
            currentUser = {
                user_type: 'student',
                data: data.student
            };
            showBookingView();
            showAlert('Verification successful!', 'success');
        } else {
            showAlert(data.message || 'Verification failed', 'danger');
        }
    } catch (error) {
        showAlert('Verification failed. Please try again.', 'danger');
        console.error('Verification error:', error);
    } finally {
        hideLoading();
    }
}

// Admin login
async function adminLogin() {
    const username = document.getElementById('adminUsername').value.trim();
    const password = document.getElementById('adminPassword').value;
    
    if (!username || !password) {
        showAlert('Please enter username and password', 'danger');
        return;
    }
    
    showLoading();
    
    try {
        const response = await fetch('/api/admin/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password })
        });
        
        const data = await response.json();
        
        if (data.success) {
            window.location.href = data.redirect || '/admin/dashboard';
        } else {
            showAlert(data.message || 'Login failed', 'danger');
        }
    } catch (error) {
        showAlert('Login failed. Please try again.', 'danger');
        console.error('Login error:', error);
    } finally {
        hideLoading();
    }
}

// Show booking view
function showBookingView() {
    document.getElementById('authView').style.display = 'none';
    document.getElementById('bookingView').classList.add('active');
    document.getElementById('studentName').textContent = currentUser.data.student_name || 'Student';
    
    // Set min date to today
    const today = new Date();
    const todayStr = today.toISOString().split('T')[0];
    document.getElementById('dateInput').min = todayStr;
    
    // Initialize date
    selectedDate = today;
    updateDateDisplay();
    loadTimeSlots();
}

// Date navigation
function changeDate(delta) {
    selectedDate.setDate(selectedDate.getDate() + delta);
    updateDateDisplay();
    document.getElementById('dateInput').value = formatDate(selectedDate);
    loadTimeSlots();
}

function onDateChange() {
    const input = document.getElementById('dateInput').value;
    if (input) {
        selectedDate = new Date(input + 'T00:00:00');
        updateDateDisplay();
        loadTimeSlots();
    }
}

function updateDateDisplay() {
    const options = { weekday: 'short', month: 'short', day: 'numeric' };
    document.getElementById('selectedDate').textContent = selectedDate.toLocaleDateString('en-US', options);
}

function formatDate(date) {
    return date.toISOString().split('T')[0];
}

// Duration selection
function selectDuration(duration) {
    selectedDuration = duration;
    document.querySelectorAll('#durationGrid .slot-btn').forEach(btn => {
        btn.classList.toggle('selected', parseInt(btn.dataset.duration) === duration);
    });
    loadTimeSlots();
}

// Load time slots
async function loadTimeSlots() {
    const grid = document.getElementById('timeSlotGrid');
    grid.innerHTML = '<div class="spinner"></div>';
    
    try {
        const dateStr = formatDate(selectedDate);
        const response = await fetch(`/api/admin/availability?date=${dateStr}`);
        
        let availability = {};
        if (response.ok) {
            const data = await response.json();
            availability = data.availability || {};
        }
        
        // Generate time slots based on config
        const slots = generateTimeSlots();
        grid.innerHTML = '';
        
        slots.forEach(slot => {
            const btn = document.createElement('button');
            const slotStart = slot.split('-')[0];
            const status = getSlotStatus(slotStart, availability);
            
            btn.className = 'slot-btn';
            btn.textContent = slotStart;
            
            if (status === 'unavailable') {
                btn.classList.add('unavailable');
                btn.disabled = true;
            } else if (status === 'selected' && selectedTime === slotStart) {
                btn.classList.add('selected');
            }
            
            btn.onclick = () => selectTimeSlot(slotStart);
            grid.appendChild(btn);
        });
    } catch (error) {
        console.error('Failed to load time slots:', error);
        grid.innerHTML = '<p class="text-muted">Failed to load time slots</p>';
    }
}

function generateTimeSlots() {
    const slots = [];
    const startHour = 9;
    const endHour = 18;
    
    for (let hour = startHour; hour < endHour; hour++) {
        slots.push(`${String(hour).padStart(2, '0')}:00-${String(hour + 1).padStart(2, '0')}:00`);
        if (hour < endHour - 1) {
            slots.push(`${String(hour).padStart(2, '0')}:30-${String(hour + 1).padStart(2, '0')}:30`);
        }
    }
    
    return slots;
}

function getSlotStatus(slotStart, availability) {
    // Check if slot is available
    for (const [slot, panels] of Object.entries(availability)) {
        if (slot.startsWith(slotStart)) {
            const hasFree = Object.values(panels).some(v => v === 'FREE');
            return hasFree ? 'available' : 'unavailable';
        }
    }
    return 'available';
}

function selectTimeSlot(time) {
    selectedTime = time;
    
    document.querySelectorAll('#timeSlotGrid .slot-btn').forEach(btn => {
        btn.classList.toggle('selected', btn.textContent === time);
    });
    
    showBookingForm();
}

function showBookingForm() {
    const summary = document.getElementById('bookingSummary');
    summary.style.display = 'block';
    
    document.getElementById('summaryDate').textContent = selectedDate.toLocaleDateString('en-US', { 
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' 
    });
    document.getElementById('summaryTime').textContent = selectedTime;
    document.getElementById('summaryDuration').textContent = selectedDuration + ' minutes';
    
    // Scroll to form
    summary.scrollIntoView({ behavior: 'smooth' });
}

// Create booking
async function createBooking() {
    const company = document.getElementById('companyName').value.trim();
    const notes = document.getElementById('bookingNotes').value.trim();
    
    if (!company) {
        showAlert('Please enter company name', 'danger');
        return;
    }
    
    if (!selectedTime) {
        showAlert('Please select a time slot', 'danger');
        return;
    }
    
    showLoading();
    
    try {
        const response = await fetch('/api/bookings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                student_id: currentUser.data.student_id,
                student_name: currentUser.data.student_name,
                company: company,
                interview_date: formatDate(selectedDate),
                start_time: selectedTime,
                duration: selectedDuration,
                notes: notes
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            if (data.is_waitlisted) {
                showAlert(`Added to waitlist! Position: ${data.position}`, 'warning');
            } else {
                showAlert('Booking confirmed successfully!', 'success');
            }
            
            // Reset form
            resetBookingForm();
            loadTimeSlots();
        } else {
            showAlert(data.message || data.error || 'Booking failed', 'danger');
        }
    } catch (error) {
        showAlert('Booking failed. Please try again.', 'danger');
        console.error('Booking error:', error);
    } finally {
        hideLoading();
    }
}

function resetBookingForm() {
    selectedTime = null;
    document.getElementById('bookingSummary').style.display = 'none';
    document.getElementById('companyName').value = '';
    document.getElementById('bookingNotes').value = '';
    document.querySelectorAll('#timeSlotGrid .slot-btn').forEach(btn => {
        btn.classList.remove('selected');
    });
}

// Show my bookings
async function showMyBookings() {
    showLoading();
    
    try {
        const response = await fetch('/api/bookings/my');
        const data = await response.json();
        
        const list = document.getElementById('myBookingsList');
        
        if (data.bookings && data.bookings.length > 0) {
            list.innerHTML = data.bookings.map(booking => `
                <div class="card mb-3">
                    <div class="card-body">
                        <div class="d-flex justify-content-between align-items-start">
                            <div>
                                <h5 style="margin: 0;">${booking.Company || 'N/A'}</h5>
                                <p class="text-muted mb-2" style="font-size: 0.875rem;">
                                    ${formatDisplayDate(booking.InterviewDate)} at ${booking.StartTime} 
                                    (${booking.Duration} min)
                                </p>
                                <span class="badge status-${booking.Status?.toLowerCase()}">${booking.Status || 'BOOKED'}</span>
                            </div>
                            <div class="d-flex gap-2">
                                ${booking.Status === 'BOOKED' ? `
                                    <button class="btn btn-sm btn-outline" onclick="rescheduleBooking('${booking.BookingID}')">Reschedule</button>
                                    <button class="btn btn-sm btn-danger" onclick="cancelBooking('${booking.BookingID}')">Cancel</button>
                                ` : ''}
                            </div>
                        </div>
                    </div>
                </div>
            `).join('');
        } else {
            list.innerHTML = '<p class="text-center text-muted">No bookings found</p>';
        }
        
        document.getElementById('bookingsModal').classList.add('active');
    } catch (error) {
        showAlert('Failed to load bookings', 'danger');
        console.error('Error:', error);
    } finally {
        hideLoading();
    }
}

function formatDisplayDate(dateStr) {
    if (!dateStr) return 'N/A';
    const date = new Date(dateStr + 'T00:00:00');
    return date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
}

// Reschedule booking
async function rescheduleBooking(bookingId) {
    // For simplicity, redirect to booking page with context
    showAlert('Rescheduling feature - select a new date and time', 'info');
    closeModal('bookingsModal');
}

// Cancel booking
async function cancelBooking(bookingId) {
    if (!confirm('Are you sure you want to cancel this booking?')) {
        return;
    }
    
    showLoading();
    
    try {
        const response = await fetch(`/api/bookings/${bookingId}/cancel`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });
        
        const data = await response.json();
        
        if (data.success) {
            showAlert('Booking cancelled successfully', 'success');
            showMyBookings(); // Refresh list
        } else {
            showAlert(data.message || 'Cancellation failed', 'danger');
        }
    } catch (error) {
        showAlert('Cancellation failed', 'danger');
        console.error('Error:', error);
    } finally {
        hideLoading();
    }
}

// Logout
async function logout() {
    try {
        await fetch('/api/logout', { method: 'POST' });
        window.location.reload();
    } catch (error) {
        console.error('Logout error:', error);
        window.location.reload();
    }
}

// Close modal on outside click
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('active');
    }
});