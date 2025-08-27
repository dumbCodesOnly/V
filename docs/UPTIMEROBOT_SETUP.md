# UptimeRobot Setup for Render Deployment

## Why Use UptimeRobot?

Render's free tier puts services to sleep after 15 minutes of inactivity. UptimeRobot keeps your trading bot server alive by pinging it regularly, ensuring:

- **Continuous Operation**: Your trading bot stays active 24/7
- **Position Monitoring**: Active trades are continuously monitored
- **Real-time Execution**: Orders execute immediately without cold start delays
- **Free Solution**: UptimeRobot offers 50 free monitors

## Setup Instructions

### Step 1: Get Your Render URL
1. Deploy your trading bot to Render
2. Copy your Render app URL (e.g., `https://your-app-name.onrender.com`)

### Step 2: Create UptimeRobot Account
1. Go to [uptimerobot.com](https://uptimerobot.com)
2. Sign up for a free account
3. Verify your email address

### Step 3: Create Monitor
1. Click "Add New Monitor"
2. Configure settings:
   - **Monitor Type**: HTTP(s)
   - **Friendly Name**: "Trading Bot - Render"
   - **URL**: Your Render app URL + `/api/health` (e.g., `https://your-app-name.onrender.com/api/health`)
   - **Monitoring Interval**: 5 minutes (recommended)
   - **Monitor Timeout**: 30 seconds
   - **HTTP Method**: GET

### Step 4: Optional - Set Up Alerts
1. Add alert contacts (email/SMS)
2. Configure when to receive notifications:
   - When service goes down
   - When service comes back up

### Step 5: Verify Setup
1. Check that UptimeRobot shows "Up" status
2. Monitor your Render logs to see the ping requests
3. Verify your trading bot stays active

## Health Check Endpoint

Your trading bot already includes a health check endpoint at `/api/health` that returns:

```json
{
  "status": "healthy",
  "timestamp": "2025-08-27T22:05:19+03:30",
  "database": "connected",
  "services": {
    "cache": "active",
    "monitoring": "running",
    "circuit_breakers": "healthy"
  }
}
```

## Best Practices

### Monitoring Interval
- **5 minutes**: Recommended for trading bots (keeps server warm)
- **10 minutes**: Acceptable for less critical apps
- **1 minute**: Avoid - may trigger rate limits

### URL Endpoints to Monitor
- **Primary**: `/api/health` - Lightweight health check
- **Alternative**: `/` - Main app endpoint (heavier)
- **Avoid**: `/api/trades` or data endpoints (may cause side effects)

### Alert Configuration
- **Down Alert**: After 2-3 failed checks
- **Up Alert**: When service recovers
- **Maintenance Windows**: Schedule for known downtime

## Troubleshooting

### Common Issues
1. **Monitor Shows Down**: Check if Render URL is correct
2. **Still Going to Sleep**: Verify monitoring interval is frequent enough
3. **High Response Times**: Normal for Render free tier cold starts

### Logs to Check
- **Render Logs**: Look for UptimeRobot requests
- **UptimeRobot Dashboard**: Check response times and status history

## Cost Considerations

### Free Tier Limits
- **UptimeRobot Free**: 50 monitors, 5-minute intervals
- **Render Free**: 750 hours/month (sufficient with UptimeRobot)

### Paid Upgrades (Optional)
- **UptimeRobot Pro**: 1-minute intervals, more monitors
- **Render Starter**: Always-on service without sleep

## Alternative Solutions

If you need additional reliability:

1. **Multiple Monitors**: Set up redundant ping services
2. **Render Starter Plan**: Paid plan with no sleep
3. **Health Check Cron**: Additional server pinging your app
4. **Status Page**: Public status page for transparency

## Security Notes

- UptimeRobot only accesses your `/api/health` endpoint
- No sensitive data is exposed in health checks
- Monitor logs in Render to verify legitimate traffic
- Consider rate limiting if needed

## Implementation Complete

Your trading bot is already configured with:
- ✅ Health check endpoint at `/api/health`
- ✅ Proper JSON responses for monitoring
- ✅ Database connectivity checks
- ✅ Service status reporting

Simply set up UptimeRobot pointing to your Render URL + `/api/health` and your server will stay online 24/7.