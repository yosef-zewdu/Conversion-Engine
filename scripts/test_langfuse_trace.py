"""
One-shot script to verify Langfuse connectivity (Langfuse SDK v4).
Sends a single test trace and prints the trace ID.
"""
import os
from dotenv import load_dotenv

load_dotenv()

from langfuse import Langfuse

client = Langfuse(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    host=os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
)

# Raises if credentials are invalid
client.auth_check()

with client.start_as_current_observation(
    name="conversion-engine-connectivity-check",
    as_type="span",
    input={"message": "hello from conversion-engine"},
    output={"status": "ok"},
    metadata={"purpose": "task-0.2-verification"},
):
    trace_id = client.get_current_trace_id()

client.flush()

print("✓ Trace sent successfully")
print(f"  Trace ID : {trace_id}")
print(f"  Dashboard: {os.environ.get('LANGFUSE_BASE_URL', 'https://cloud.langfuse.com')}")
