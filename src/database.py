import os
import uuid
import asyncio
import psycopg_pool
from dotenv import load_dotenv

load_dotenv()

from src.llm_factory import get_embeddings

_global_pool = None

def get_pool():
    global _global_pool
    if _global_pool is None:
        raise RuntimeError("Database connection pool has not been initialized.")
    return _global_pool

def _format_vector(vector: list[float]) -> str:
    """Formats a python list of floats into a pgvector string."""
    return "[" + ",".join(map(str, vector)) + "]"

def get_db_uri():
    db_url = os.getenv("COCKROACH_DATABASE_URL")
    if not db_url:
        print("OPERATIONAL ERROR: COCKROACH_DATABASE_URL environment variable is missing.")
        print("Please configure it (e.g. in your .env file) before running the agent.")
        raise RuntimeError("COCKROACH_DATABASE_URL environment variable is missing.")
    return db_url

async def init_db():
    global _global_pool
    db_uri = get_db_uri()
    print("Initializing CockroachDB tables...")

    try:
        _global_pool = psycopg_pool.AsyncConnectionPool(db_uri, open=False)
        await _global_pool.open()
        async with _global_pool.connection() as conn:
                async with conn.cursor() as cur:

                    # Note: pgvector extension must be created by a database superuser out-of-band:
                    # CREATE EXTENSION IF NOT EXISTS vector;

                    # Drop old table to clear conflicting 1536-dimension embeddings
                    await cur.execute("DROP TABLE IF EXISTS incident_memory;")
                    
                    # Create incident_memory table with 768 dimensions for Gemini
                    await cur.execute("""
                    CREATE TABLE IF NOT EXISTS incident_memory (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        alert_type TEXT,
                        error_log TEXT,
                        resolution_steps TEXT,
                        embedding VECTOR(768),
                        created_at TIMESTAMP DEFAULT current_timestamp()
                    );
                    """)
                    
                    # CockroachDB Distributed Vector Indexing
                    await cur.execute("""
                    CREATE VECTOR INDEX IF NOT EXISTS incident_memory_embedding_idx 
                    ON incident_memory (embedding);
                    """)
                    
                    # Create DLQ table
                    await cur.execute("""
                    CREATE TABLE IF NOT EXISTS dead_letter_queue (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        payload JSONB,
                        error_reason TEXT,
                        created_at TIMESTAMP DEFAULT current_timestamp()
                    );
                    """)

                    # Check if we need to seed
                    await cur.execute("SELECT COUNT(*) FROM incident_memory;")
                    count_row = await cur.fetchone()
                    count = count_row[0] if count_row else 0

                    if count == 0:
                        print("Seeding incident_memory with mock historical incidents...")

                        # OllamaEmbeddings construction is synchronous (may probe the
                        # server at build time). Wrap in to_thread to keep the event
                        # loop free during the seeding phase.
                        embedder = await asyncio.to_thread(get_embeddings)

                        incidents = [
                            (str(uuid.uuid4()), "Thermal Runaway", "ESP32 thermal sensor reports temperature exceeding 85C for 60 seconds.", "Triggered active cooling fans and throttled CPU clock to 80MHz.", await embedder.aembed_query("ESP32 thermal sensor reports temperature exceeding 85C for 60 seconds.")),
                            (str(uuid.uuid4()), "Sensor Disconnect", "I2C bus error: timeout reading from BMP280 pressure sensor.", "Reset I2C bus via hardware pin and re-initialized BMP280 driver.", await embedder.aembed_query("I2C bus error: timeout reading from BMP280 pressure sensor.")),
                            (str(uuid.uuid4()), "Voltage Drop", "VCC rail dipped below 2.9V, risk of brownout.", "Disabled non-essential peripherals and enabled deep sleep mode.", await embedder.aembed_query("VCC rail dipped below 2.9V, risk of brownout."))
                        ]

                        await cur.executemany(
                            "INSERT INTO incident_memory (id, alert_type, error_log, resolution_steps, embedding) VALUES (%s::uuid, %s, %s, %s, %s::vector)",
                            incidents
                        )
                        print("Seeding completed successfully.")
                    else:
                        print(f"incident_memory table already contains {count} records. Skipping seed.")

                await conn.commit()
                print("Database initialization complete.")
    except Exception as e:
        print(f"CRITICAL ERROR during database initialization: {e}")
        raise RuntimeError(f"Database initialization failed: {e}") from e

async def route_to_dlq(payload: dict, error_reason: str):
    import json
    global _global_pool
    if _global_pool is None:
        print("Error: Database pool not initialized when attempting to route to DLQ.")
        return
    
    try:
        async with _global_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO dead_letter_queue (payload, error_reason) VALUES (%s, %s)",
                    (json.dumps(payload), error_reason)
                )
            await conn.commit()
        print("Successfully routed failed payload to Dead Letter Queue.")
    except Exception as e:
        print(f"FATAL ERROR: Failed to write to Dead Letter Queue: {e}")

if __name__ == "__main__":
    asyncio.run(init_db())
