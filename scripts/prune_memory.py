import os
import sys
import json
import uuid
import asyncio
import datetime
import tempfile
import boto3
from dotenv import load_dotenv

# Add parent directory to path to allow importing src modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import get_db_uri
import psycopg_pool

load_dotenv()

# Custom JSON encoder to handle UUIDs and Datetimes
class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, uuid.UUID):
            return str(o)
        if isinstance(o, datetime.datetime):
            return o.isoformat()
        return super().default(o)

async def prune_memory():
    retention_days = int(os.getenv("MEMORY_RETENTION_DAYS", "90"))
    s3_bucket = os.getenv("AWS_S3_ARCHIVE_BUCKET")
    
    if not s3_bucket:
        print("ERROR: AWS_S3_ARCHIVE_BUCKET is not set. Cannot run pruning.")
        sys.exit(1)
        
    db_uri = get_db_uri()
    print(f"Starting memory pruning. Retention threshold: {retention_days} days.")
    
    try:
        async with psycopg_pool.AsyncConnectionPool(db_uri) as pool:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    # 1. Fetch old records
                    # Note: We select the vector as a string/list using array format if needed, 
                    # but pgvector handles casting to string natively.
                    fetch_query = """
                        SELECT id, alert_type, error_log, resolution_steps, created_at, embedding::text
                        FROM incident_memory
                        WHERE created_at < current_timestamp() - INTERVAL '%s days'
                    """
                    await cur.execute(fetch_query, (retention_days,))
                    rows = await cur.fetchall()
                    
                    if not rows:
                        print("No old records found to prune. Exiting.")
                        return
                    
                    print(f"Found {len(rows)} records to archive.")
                    
                    # 2. Format as JSON Lines
                    archive_records = []
                    record_ids = []
                    for row in rows:
                        record_id = row[0]
                        record_ids.append(record_id)
                        
                        record_dict = {
                            "id": record_id,
                            "alert_type": row[1],
                            "error_log": row[2],
                            "resolution_steps": row[3],
                            "created_at": row[4],
                            "embedding": json.loads(row[5]) if row[5] else None
                        }
                        archive_records.append(record_dict)
                    
                    # 3. Write to temporary file
                    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"incident_memory_archive_{timestamp_str}.jsonl"
                    
                    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as temp_file:
                        temp_path = temp_file.name
                        for record in archive_records:
                            temp_file.write(json.dumps(record, cls=EnhancedJSONEncoder) + "\n")
                    
                    print(f"Archived data written to temporary file: {temp_path}")
                    
                    # 4. Upload to S3
                    try:
                        s3_client = boto3.client('s3')
                        s3_key = f"archives/incident_memory/{filename}"
                        
                        print(f"Uploading {temp_path} to s3://{s3_bucket}/{s3_key} ...")
                        # Run blocking boto3 upload in a separate thread
                        await asyncio.to_thread(
                            s3_client.upload_file,
                            temp_path,
                            s3_bucket,
                            s3_key
                        )
                        print("S3 upload confirmed successful.")
                        
                    except Exception as s3_err:
                        print(f"CRITICAL ERROR: Failed to upload to S3: {s3_err}")
                        print("Aborting database deletion to prevent data loss.")
                        os.unlink(temp_path)
                        sys.exit(1)
                    
                    # Clean up temp file after successful upload
                    os.unlink(temp_path)
                    
                    # 5. Delete records from database
                    try:
                        print(f"Deleting {len(record_ids)} records from CockroachDB...")
                        delete_query = "DELETE FROM incident_memory WHERE id = ANY(%s)"
                        await cur.execute(delete_query, (record_ids,))
                        await conn.commit()
                        print("Successfully deleted archived records.")
                    except Exception as db_err:
                        print(f"ERROR: Failed to delete records from DB: {db_err}")
                        print("Warning: Records were archived to S3 but remain in the database.")
                        sys.exit(1)
                        
    except Exception as e:
        print(f"ERROR during prune execution: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(prune_memory())
