// Dashboard JavaScript for real-time updates
class TradingBotDashboard {
    constructor() {
        this.updateInterval = 5000; // 5 seconds
        this.init();
    }

    init() {
        this.loadBotStatus();
        this.loadRecentMessages();
        this.loadRecentTrades();
        
        // Set up auto-refresh
        setInterval(() => {
            this.loadBotStatus();
            this.loadRecentMessages();
            this.loadRecentTrades();
        }, this.updateInterval);
    }

    async loadBotStatus() {
        try {
            const response = await fetch('/api/status');
            const data = await response.json();
            
            if (response.ok) {
                this.updateStatusDisplay(data);
            } else {
                console.error('Error loading bot status:', data.error);
                this.showError('Failed to load bot status');
            }
        } catch (error) {
            console.error('Network error loading bot status:', error);
            this.showError('Network error loading bot status');
        }
    }

    updateStatusDisplay(data) {
        // Update status
        const statusElement = document.getElementById('bot-status');
        const statusIcon = document.getElementById('status-icon');
        
        statusElement.textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);
        
        // Update status icon and color
        statusIcon.className = 'fas fa-power-off fa-2x';
        if (data.status === 'active') {
            statusIcon.classList.add('text-success');
        } else if (data.status === 'inactive') {
            statusIcon.classList.add('text-warning');
        } else {
            statusIcon.classList.add('text-danger');
        }

        // Update counters
        document.getElementById('total-messages').textContent = data.total_messages || 0;
        document.getElementById('total-trades').textContent = data.total_trades || 0;
        document.getElementById('error-count').textContent = data.error_count || 0;

        // Update last heartbeat
        const heartbeatElement = document.getElementById('last-heartbeat');
        if (data.last_heartbeat) {
            const date = new Date(data.last_heartbeat);
            heartbeatElement.textContent = this.formatDateTime(date);
        } else {
            heartbeatElement.textContent = 'No activity recorded';
        }
    }

    async loadRecentMessages() {
        try {
            const response = await fetch('/api/recent-messages');
            const data = await response.json();
            
            if (response.ok) {
                this.updateMessagesTable(data);
            } else {
                console.error('Error loading messages:', data.error);
            }
        } catch (error) {
            console.error('Network error loading messages:', error);
        }
    }

    updateMessagesTable(messages) {
        const tbody = document.getElementById('recent-messages');
        
        if (messages.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="3" class="text-center text-muted py-4">
                        <i class="fas fa-comment-slash me-2"></i>No recent messages
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = '';
        messages.forEach(message => {
            const row = document.createElement('tr');
            
            const username = message.username || `User ${message.user_id.substring(0, 6)}...`;
            const command = message.command_type || 'message';
            const time = this.formatTime(new Date(message.timestamp));
            
            row.innerHTML = `
                <td>
                    <span class="text-truncate" style="max-width: 100px; display: inline-block;" 
                          title="${username}">${username}</span>
                </td>
                <td>
                    <span class="badge bg-secondary">${command}</span>
                </td>
                <td class="text-muted small">${time}</td>
            `;
            
            tbody.appendChild(row);
        });
    }

    async loadRecentTrades() {
        try {
            const response = await fetch('/api/recent-trades');
            const data = await response.json();
            
            if (response.ok) {
                this.updateTradesTable(data);
            } else {
                console.error('Error loading trades:', data.error);
            }
        } catch (error) {
            console.error('Network error loading trades:', error);
        }
    }

    updateTradesTable(trades) {
        const tbody = document.getElementById('recent-trades');
        
        if (trades.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="4" class="text-center text-muted py-4">
                        <i class="fas fa-chart-line me-2"></i>No recent trades
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = '';
        trades.forEach(trade => {
            const row = document.createElement('tr');
            
            const statusBadge = this.getStatusBadge(trade.status);
            const actionBadge = this.getActionBadge(trade.action);
            const time = this.formatTime(new Date(trade.timestamp));
            
            row.innerHTML = `
                <td><strong>${trade.symbol}</strong></td>
                <td>${actionBadge}</td>
                <td>${statusBadge}</td>
                <td class="text-muted small">${time}</td>
            `;
            
            tbody.appendChild(row);
        });
    }

    getStatusBadge(status) {
        const badges = {
            'executed': '<span class="badge bg-success">Executed</span>',
            'pending': '<span class="badge bg-warning">Pending</span>',
            'failed': '<span class="badge bg-danger">Failed</span>'
        };
        return badges[status] || `<span class="badge bg-secondary">${status}</span>`;
    }

    getActionBadge(action) {
        const badges = {
            'buy': '<span class="badge bg-success">BUY</span>',
            'sell': '<span class="badge bg-danger">SELL</span>'
        };
        return badges[action] || `<span class="badge bg-secondary">${action}</span>`;
    }

    formatDateTime(date) {
        return date.toLocaleString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    formatTime(date) {
        return date.toLocaleString('en-US', {
            hour: '2-digit',
            minute: '2-digit'
        });
    }

    showError(message) {
        // Create a simple error notification
        const alertDiv = document.createElement('div');
        alertDiv.className = 'alert alert-danger alert-dismissible fade show position-fixed';
        alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 1050; max-width: 400px;';
        alertDiv.innerHTML = `
            <i class="fas fa-exclamation-triangle me-2"></i>${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        document.body.appendChild(alertDiv);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            if (alertDiv.parentNode) {
                alertDiv.remove();
            }
        }, 5000);
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new TradingBotDashboard();
});
