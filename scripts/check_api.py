"""One inexpensive live request: verifies actual gateway transport and low reasoning."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from research_config import Config
from research_llm import ModelClient


async def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    config = Config(root / "examples/regression/task.md", root / "test-output/api-probe")
    client = ModelClient(config)
    try:
        message, usage = await client.complete([{"role": "user", "content": "Reply with the single word OK."}])
        print({"response": message["content"], "usage": usage, "reasoning_effort": config.reasoning_effort})
    except Exception as error:
        print({"error_type": type(error).__name__, "status_code": getattr(error, "status_code", None)})
        raise SystemExit(1)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
