import asyncio
import json
import paho.mqtt.client as mqtt
from src.database import get_pool, init_db

async def replay_dlq():
    print("Initializing Database Connection...")
    await init_db()
    pool = get_pool()
    
    print("Connecting to local Mosquitto broker...")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect("localhost", 1883, 60)
    client.loop_start()
    
    recovered = 0
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT id, payload FROM dead_letter_queue;")
                rows = await cur.fetchall()
                
                if not rows:
                    print("0 messages recovered from DLQ. Queue is empty!")
                    return
                
                for row_id, payload in rows:
                    # Parse JSONB payload back to string
                    payload_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)
                    
                    print(f"Republishing payload ID {row_id}...")
                    result = client.publish("sre/edge/telemetry", payload_str)
                    result.wait_for_publish(timeout=5.0)
                    
                    if result.rc == mqtt.MQTT_ERR_SUCCESS:
                        await cur.execute("DELETE FROM dead_letter_queue WHERE id = %s", (row_id,))
                        recovered += 1
                        
            await conn.commit()
    except Exception as e:
        print(f"Failed to replay DLQ: {e}")
    finally:
        client.loop_stop()
        client.disconnect()
        print(f"\nSUCCESS: {recovered} messages recovered from DLQ and republished.")

if __name__ == "__main__":
    asyncio.run(replay_dlq())
