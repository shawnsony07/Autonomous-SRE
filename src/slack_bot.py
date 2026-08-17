import os
import json
import asyncio
from typing import Dict
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

# Global registry to map incident IDs to their corresponding futures
hitl_futures: Dict[str, asyncio.Future] = {}

# Initialize the Slack App asynchronously
app = AsyncApp(token=os.environ.get("SLACK_BOT_TOKEN", ""))

async def send_hitl_alert(incident_id: str, action: str, args: dict):
    """Sends an interactive Block Kit message to Slack."""
    channel = os.environ.get("SLACK_CHANNEL", "#sre-alerts")
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "⚠️ Autonomous SRE Agent HITL Alert ⚠️",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Incident ID:* {incident_id}\n*Action:* `{action}`\n*Args:* `{json.dumps(args)}`\n\nThe system is paused pending operator approval."
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Approve",
                        "emoji": True
                    },
                    "style": "primary",
                    "value": incident_id,
                    "action_id": "hitl_approve"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Deny",
                        "emoji": True
                    },
                    "style": "danger",
                    "value": incident_id,
                    "action_id": "hitl_deny"
                }
            ]
        }
    ]

    try:
        await app.client.chat_postMessage(
            channel=channel,
            blocks=blocks,
            text=f"HITL Alert for {incident_id}"
        )
        print(" -> Interactive Slack notification sent.")
    except Exception as e:
        print(f" -> Failed to send Slack notification: {e}")

@app.action("hitl_approve")
async def handle_approve_action(ack, body, respond):
    await ack()
    user_id = body["user"]["id"]
    incident_id = body["actions"][0]["value"]
    
    # Resolve the future if it exists and hasn't timed out
    if incident_id in hitl_futures and not hitl_futures[incident_id].done():
        hitl_futures[incident_id].set_result('y')
    
    # Update the Slack message to reflect the decision
    await respond(
        text=f"✅ *Approved* by <@{user_id}>",
        replace_original=True
    )

@app.action("hitl_deny")
async def handle_deny_action(ack, body, respond):
    await ack()
    user_id = body["user"]["id"]
    incident_id = body["actions"][0]["value"]
    
    # Resolve the future if it exists and hasn't timed out
    if incident_id in hitl_futures and not hitl_futures[incident_id].done():
        hitl_futures[incident_id].set_result('n')
    
    # Update the Slack message to reflect the decision
    await respond(
        text=f"❌ *Denied* by <@{user_id}>",
        replace_original=True
    )

async def start_slack_bot():
    """Starts the Socket Mode handler for the Slack app."""
    app_token = os.environ.get("SLACK_APP_TOKEN")
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    
    if not app_token or not bot_token:
        print("WARNING: SLACK_APP_TOKEN or SLACK_BOT_TOKEN is not set. Two-way Slack approvals are disabled.")
        return
    
    handler = AsyncSocketModeHandler(app, app_token)
    try:
        await handler.start_async()
    except Exception as e:
        print(f"Failed to start Slack Socket Mode handler: {e}")
