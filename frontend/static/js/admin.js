/**
 * RUBIX PANEL SCHEDULER - Admin Dashboard JavaScript
 */

// State
let currentTimelineDate = new Date();
let allBookings = [];
let stats = null;
let bookingChart = null;
let statusChart = null;

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    checkAuth();
    loadDashboardData();
    setInterval(loadDashboardData, 60000);
});

// Auth check
async function checkAuth() {
    try {
        const response = await fetch('/api/session/validate');
        if (!response.ok || !(await response.json()).valid) {
            window.location.href = '/admin/login';
        }
    } catch (error) {
        window.location.href = '/admin/login';
    }
}

function showLoading() { document.getElementById('loadingOverlay').classList.add('active'); }
function hideLoading() { document.getElementById('loadingOverlay').classList.remove('active'); }

function showAlert(message, type = 'info') {
    const container = document.getElementById('alertContainer');
    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.innerHTML = `<span>${message}</span>`;
    container.appendChild(alert);
    setTimeout(() => alert.remove(), 5000);
}

function closeModal(id) { document.getElementById(id).classList.remove('active'); }

// Section navigation
function showSection(section) {
    document.querySelectorAll('.sidebar-nav-link').forEach(link => {
        link.classList.toggle('active', link.dataset.section === section);
    });
    ['overview', 'timeline', 'bookings', 'waitlist', 'panels', 'audit'].forEach(s => {
        const el = document.getElementById(`${s}-section`);
        if (el) el.style.display = s === section ? 'block' : 'none';
    });
    document.getElementById('pageTitle').textContent = section.charAt(0).toUpperCase() + section.slice(1);
    switch(section) {
        case 'overview': loadDashboardData(); break;
        case 'timeline': loadTimeline(); break;
        case 'bookings': loadAllBookings(); break;
        case 'waitlist': loadWaitlist(); break;
        case 'panels': loadPanels(); break;
        case 'audit': loadAuditLog(); break;
    }
}

// Dashboard data
async function loadDashboardData() {
    try {
        const response = await fetch('/api/admin/stats');
        if (response.ok) {
            stats = await response.json();
            updateStats(stats);
            updateCharts(stats);
            loadRecentBookings();
        }
    } catch (error) { console.error('Failed to load dashboard data:', error); }
}

function updateStats(stats) {
    document.getElementById('totalBookings').textContent = stats.total_bookings || 0;
    document.getElementById('activeInterviews').textContent = stats.active_interviews || 0;
    document.getElementById('waitlisted').textContent = stats.waitlisted_count || 0;
    document.getElementById('noShows').textContent = stats.no_shows || 0;
    document.getElementById('utilization').textContent = (stats.utilization_percent || 0) + '%';
}

function updateCharts(stats) {
    const statusCtx = document.getElementById('statusChart');
    if (statusCtx) {
        if (statusChart) statusChart.destroy();
        statusChart = new Chart(statusCtx, {
            type: 'doughnut',
            data: {
                labels: ['Booked', 'Completed', 'Waitlisted', 'No Show', 'Cancelled'],
                datasets: [{
                    data: [stats.active_interviews || 0, stats.completed_interviews || 0, stats.waitlisted_count || 0, stats.no_shows || 0, stats.cancelled_count || 0],
                    backgroundColor: ['#16a34a', '#2563eb', '#f59e0b', '#64748b', '#dc2626']
                }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
        });
    }
    const bookingCtx = document.getElementById('bookingChart');
    if (bookingCtx) {
        if (bookingChart) bookingChart.destroy();
        bookingChart = new Chart(bookingCtx, {
            type: 'line',
            data: {
                labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                datasets: [{ label: 'Bookings', data: [12, 19, 15, 25, 22, 10, 5], borderColor: '#2563eb', backgroundColor: 'rgba(37, 99, 235, 0.1)', fill: true, tension: 0.4 }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
        });
    }
}

async function loadRecentBookings() {
    try {
        const response = await fetch('/api/admin/bookings?limit=10');
        if (response.ok) {
            const data = await response.json();
            document.getElementById('recentBookingsBody').innerHTML = (data.bookings || []).slice(0, 10).map(b => `
                <tr><td><code>${b.BookingID || 'N/A'}</code></td><td>${b.StudentName || 'N/A'}</td><td>${b.Company || 'N/A'}</td>
                <td>${formatDate(b.InterviewDate)}</td><td>${b.StartTime || 'N/A'}</td>
                <td><span class="badge status-${(b.Status || '').toLowerCase()}">${b.Status || 'N/A'}</span></td></tr>
            `).join('');
        }
    } catch (error) { console.error('Failed to load recent bookings:', error); }
}

// Timeline
function changeTimelineDate(delta) { currentTimelineDate.setDate(currentTimelineDate.getDate() + delta); loadTimeline(); }

function formatDate(dateStr) { if (!dateStr) return 'N/A'; const d = new Date(dateStr + 'T00:00:00'); return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }); }
function formatDateDisplay(date) { return date.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }); }

async function loadTimeline() {
    const dateStr = currentTimelineDate.toISOString().split('T')[0];
    document.getElementById('timelineDate').textContent = formatDateDisplay(currentTimelineDate);
    showLoading();
    try {
        const response = await fetch(`/api/admin/panel-grid?date=${dateStr}`);
        if (response.ok) renderTimeline(await response.json());
    } catch (error) { console.error('Failed to load timeline:', error); }
    finally { hideLoading(); }
}

function renderTimeline(data) {
    const container = document.getElementById('timelineBody');
    const panels = data.panels || [];
    const grid = data.grid || [];
    let html = '<div class="timeline-grid">';
    html += `<div class="timeline-grid-header" style="grid-template-columns: 80px repeat(${panels.length}, 1fr);">`;
    html += '<div class="timeline-time-header">Time</div>';
    panels.forEach(p => { html += `<div class="timeline-panel-header">${p}</div>`; });
    html += '</div>';
    grid.forEach(row => {
        html += `<div class="timeline-row" style="grid-template-columns: 80px repeat(${panels.length}, 1fr);">`;
        html += `<div class="timeline-time-cell">${row.time_slot || ''}</div>`;
        panels.forEach(panel => {
            const cell = row[panel] || {};
            const status = cell.status || 'FREE';
            const booking = cell.booking;
            let cellClass = 'timeline-cell';
            let cellContent = '';
            if (status === 'BLOCKED') { cellClass += ' blocked'; cellContent = '<div class="blocked-indicator">BLOCKED</div>'; }
            else if (booking) { cellClass += ' booked'; cellContent = `<div class="booking-block" onclick="showBookingDetail('${booking.BookingID}')"><div class="booking-student">${booking.StudentName || ''}</div><div class="booking-company">${booking.Company || ''}</div></div>`; }
            else { cellContent = '<div class="free-slot-indicator">Free</div>'; }
            html += `<div class="${cellClass}">${cellContent}</div>`;
        });
        html += '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
}

function refreshTimeline() { loadTimeline(); }

// All Bookings
async function loadAllBookings() {
    showLoading();
    try {
        const params = new URLSearchParams();
        if (document.getElementById('statusFilter').value) params.append('status', document.getElementById('statusFilter').value);
        if (document.getElementById('dateFilter').value) params.append('date', document.getElementById('dateFilter').value);
        const response = await fetch('/api/admin/bookings?' + params.toString());
        if (response.ok) { allBookings = (await response.json()).bookings || []; renderAllBookings(allBookings); }
    } catch (error) { console.error('Failed to load bookings:', error); }
    finally { hideLoading(); }
}

function renderAllBookings(bookings) {
    document.getElementById('allBookingsBody').innerHTML = bookings.map(b => `
        <tr><td><code>${b.BookingID || 'N/A'}</code></td><td>${b.StudentName || 'N/A'}</td><td>${b.Company || 'N/A'}</td>
        <td>${formatDate(b.InterviewDate)}</td><td>${b.StartTime || 'N/A'}</td><td>${b.AllocatedPanel || 'N/A'}</td>
        <td><span class="badge status-${(b.Status || '').toLowerCase()}">${b.Status || 'N/A'}</span></td>
        <td><div class="d-flex gap-1">
            <button class="btn btn-sm btn-outline" onclick="showBookingDetail('${b.BookingID}')" title="View">👁</button>
            ${b.Status === 'BOOKED' ? `<button class="btn btn-sm btn-success" onclick="markCompleted('${b.BookingID}')" title="Complete">✓</button>
            <button class="btn btn-sm btn-warning" onclick="markNoShow('${b.BookingID}')" title="No Show">!</button>
            <button class="btn btn-sm btn-danger" onclick="adminCancelBooking('${b.BookingID}')" title="Cancel">✗</button>` : ''}
        </div></td></tr>
    `).join('');
}

function filterBookings() { loadAllBookings(); }

// Booking detail
async function showBookingDetail(bookingId) {
    try {
        const response = await fetch(`/api/bookings/${bookingId}`);
        if (response.ok) {
            const booking = await response.json();
            document.getElementById('bookingModalBody').innerHTML = `
                <div class="row"><div class="col-6"><div class="form-group"><label class="form-label">Booking ID</label><p class="form-control" style="background:#f8fafc">${booking.BookingID || 'N/A'}</p></div></div>
                <div class="col-6"><div class="form-group"><label class="form-label">Status</label><p><span class="badge status-${(booking.Status || '').toLowerCase()}">${booking.Status || 'N/A'}</span></p></div></div></div>
                <div class="row"><div class="col-6"><div class="form-group"><label class="form-label">Student</label><p class="form-control" style="background:#f8fafc">${booking.StudentName || 'N/A'}</p></div></div>
                <div class="col-6"><div class="form-group"><label class="form-label">Student ID</label><p class="form-control" style="background:#f8fafc">${booking.StudentID || 'N/A'}</p></div></div></div>
                <div class="row"><div class="col-6"><div class="form-group"><label class="form-label">Company</label><p class="form-control" style="background:#f8fafc">${booking.Company || 'N/A'}</p></div></div>
                <div class="col-6"><div class="form-group"><label class="form-label">Panel</label><p class="form-control" style="background:#f8fafc">${booking.AllocatedPanel || 'N/A'}</p></div></div></div>
                <div class="row"><div class="col-6"><div class="form-group"><label class="form-label">Date</label><p class="form-control" style="background:#f8fafc">${formatDate(booking.InterviewDate)}</p></div></div>
                <div class="col-6"><div class="form-group"><label class="form-label">Time</label><p class="form-control" style="background:#f8fafc">${booking.StartTime} - ${booking.EndTime}</p></div></div></div>
                <div class="form-group"><label class="form-label">Duration</label><p class="form-control" style="background:#f8fafc">${booking.Duration} min</p></div>
                ${booking.Notes ? `<div class="form-group"><label class="form-label">Notes</label><p class="form-control" style="background:#f8fafc;white-space:pre-wrap">${booking.Notes}</p></div>` : ''}`;
            document.getElementById('bookingModal').classList.add('active');
        }
    } catch (error) { showAlert('Failed to load booking details', 'danger'); }
}

// Actions
async function markCompleted(id) { if (confirm('Mark as completed?')) { await fetch(`/api/admin/bookings/${id}/complete`, { method: 'POST' }); showAlert('Done', 'success'); loadAllBookings(); loadDashboardData(); } }
async function markNoShow(id) { if (confirm('Mark as no-show?')) { await fetch(`/api/admin/bookings/${id}/no-show`, { method: 'POST' }); showAlert('Done', 'success'); loadAllBookings(); loadDashboardData(); } }
async function adminCancelBooking(id) { const reason = prompt('Reason:'); if (reason) { await fetch(`/api/admin/bookings/${id}/cancel?reason=${encodeURIComponent(reason)}`, { method: 'POST' }); showAlert('Done', 'success'); loadAllBookings(); loadDashboardData(); } }

// Waitlist
async function loadWaitlist() {
    try {
        const response = await fetch('/api/admin/waitlist');
        if (response.ok) {
            const waitlist = (await response.json()).waitlist || [];
            document.getElementById('waitlistCount').textContent = waitlist.length;
            document.getElementById('waitlistContainer').innerHTML = waitlist.length === 0 ? '<p class="text-center text-muted" style="padding:2rem">No entries</p>' :
                waitlist.map((e, i) => `<div class="waitlist-item"><div class="waitlist-position">${i + 1}</div><div class="waitlist-info"><div class="waitlist-student">${e.StudentName || 'N/A'}</div><div class="waitlist-details">${e.Company || 'N/A'} | ${formatDate(e.PreferredDate)} at ${e.PreferredTime} | ${e.Duration} min</div></div>
                <div class="waitlist-actions"><button class="btn btn-sm btn-success" onclick="promoteWaitlist('${e.WaitlistID}')">Promote</button><button class="btn btn-sm btn-danger" onclick="removeWaitlist('${e.WaitlistID}')">Remove</button></div></div>`).join('');
        }
    } catch (error) { console.error('Failed to load waitlist:', error); }
}

async function promoteWaitlist(id) { await fetch(`/api/admin/waitlist/${id}/promote`, { method: 'POST' }); showAlert('Promoted', 'success'); loadWaitlist(); loadDashboardData(); }
async function removeWaitlist(id) { if (confirm('Remove?')) { await fetch(`/api/admin/waitlist/${id}`, { method: 'DELETE' }); showAlert('Removed', 'success'); loadWaitlist(); } }

// Panels
async function loadPanels() {
    try {
        const config = await (await fetch('/api/config')).json();
        document.getElementById('panelGrid').innerHTML = (config.panels || []).map(p => `
            <div class="panel-card"><div class="panel-card-header"><h5 class="panel-card-name">${p}</h5><div class="panel-card-status active"></div></div>
            <div class="panel-card-stats"><div class="panel-card-stat"><span class="panel-card-stat-value">0</span><span class="panel-card-stat-label">Bookings</span></div></div>
            <div class="panel-card-actions"><button class="btn btn-sm btn-outline" onclick="showAlert('Panel management coming soon', 'info')">Configure</button></div></div>
        `).join('');
    } catch (error) { console.error('Failed to load panels:', error); }
}

// Audit Log
async function loadAuditLog() {
    try {
        const response = await fetch('/api/admin/audit-logs?limit=50');
        if (response.ok) {
            const logs = (await response.json()).logs || [];
            document.getElementById('auditLogContainer').innerHTML = logs.length === 0 ? '<p class="text-center text-muted" style="padding:2rem">No audit logs</p>' :
                logs.map(l => `<div class="audit-log-item"><div class="audit-log-time">${new Date(l.Timestamp).toLocaleString()}</div><div class="audit-log-content"><div class="audit-log-action">${l.Action || 'N/A'}</div><div class="audit-log-details">${l.OldValue || ''} → ${l.NewValue || ''}</div></div><div class="audit-log-user">${l.PerformedBy || 'N/A'}</div></div>`).join('');
        }
    } catch (error) { console.error('Failed to load audit log:', error); }
}

// Logout
async function logout() { await fetch('/api/logout', { method: 'POST' }); window.location.href = '/admin/login'; }

// Close modal on outside click
document.addEventListener('click', function(e) { if (e.target.classList.contains('modal-overlay')) e.target.classList.remove('active'); });