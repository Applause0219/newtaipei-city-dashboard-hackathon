"""Quick smoke test: can we reach Bedrock and get a response?

Usage:
    cd Taipei-City-Dashboard-Agent
    # source your .env first, or export the vars manually:
    #   export BEDROCK_MODEL=us.anthropic.claude-opus-4-6-v1
    #   export BEDROCK_API_KEY=ABSK...
    uv run scripts/probe_bedrock.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time


async def main() -> None:
    # ── 1. env check ────────────────────────────────────────────────
    model_id = os.environ.get("BEDROCK_MODEL", "")
    api_key = os.environ.get("BEDROCK_API_KEY", "")
    region = os.environ.get("BEDROCK_REGION", "us-west-2")
    aws_key = os.environ.get("AWS_ACCESS_KEY_ID", "")

    print(f"BEDROCK_MODEL  = {model_id or '(not set)'}")
    print(f"BEDROCK_REGION = {region}")
    print(f"BEDROCK_API_KEY= {'set (' + api_key[:8] + '...)' if api_key else '(not set)'}")
    print(f"AWS_ACCESS_KEY = {'set (' + aws_key[:8] + '...)' if aws_key else '(not set)'}")
    print()

    if not model_id:
        sys.exit("BEDROCK_MODEL is required. Export it and retry.")

    # ── 2. bridge env vars (same logic as config.py) ────────────────
    if api_key and not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = api_key
        print("→ Bridged BEDROCK_API_KEY → AWS_BEARER_TOKEN_BEDROCK")
    if not os.environ.get("AWS_DEFAULT_REGION"):
        os.environ["AWS_DEFAULT_REGION"] = region
        print(f"→ Set AWS_DEFAULT_REGION = {region}")
    print()

    # ── 3. try pydantic-ai agent ────────────────────────────────────
    from pydantic_ai import Agent

    model_str = f"bedrock:{model_id}"
    print(f"Creating Agent with model={model_str!r} ...")
    agent = Agent(model=model_str, system_prompt="Reply in one short sentence.")

    print("Sending test prompt: 'hello, reply one sentence' ...")
    t0 = time.time()
    try:
        result = await agent.run("hello, reply one sentence to confirm connectivity")
        elapsed = time.time() - t0
        print(f"\n[OK] Success ({elapsed:.1f}s)")
        print(f"  Response: {result.output}")
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"\n[FAIL] ({elapsed:.1f}s)")
        print(f"  Error type: {type(exc).__name__}")
        print(f"  Detail:     {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
