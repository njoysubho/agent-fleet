from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from shared.protocol import AgentMessage, InterAgentMessage
from shared.redis_client import RedisCoordinator


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _send_one(
    *,
    coordinator: RedisCoordinator,
    job_id: str,
    to_agent: str,
    from_agent: str,
    text: str,
) -> None:
    payload = InterAgentMessage(
        job_id=job_id,
        from_agent=from_agent,
        to_agent=to_agent,
        text=text,
        timestamp=_utc_iso(),
    )
    msg = AgentMessage(
        from_agent=from_agent,
        text=payload.model_dump_json(),
        summary=f"message to {to_agent}",
    )
    await coordinator.send_message(job_id, to_agent, msg)
    await coordinator.publish_job_event(
        job_id,
        {
            "type": "agent_message_sent",
            "from": from_agent,
            "to": to_agent,
        },
    )


async def amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-message")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_send = sub.add_parser("send")
    p_send.add_argument("--to", required=True)
    p_send.add_argument("--text", required=True)

    p_bcast = sub.add_parser("broadcast")
    p_bcast.add_argument("--text", required=True)
    p_bcast.add_argument("--include-self", action="store_true")

    sub.add_parser("list")

    args = parser.parse_args(argv)

    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    job_id = os.environ.get("JOB_ID", "").strip()
    from_agent = os.environ.get("AGENT_ID", "").strip()
    if not job_id or not from_agent:
        raise SystemExit("JOB_ID and AGENT_ID must be set")

    coordinator = RedisCoordinator(redis_url)
    try:
        if args.cmd == "list":
            agents = await coordinator.list_job_agents(job_id)
            for aid in agents:
                print(aid)
            return 0

        if args.cmd == "send":
            await _send_one(
                coordinator=coordinator,
                job_id=job_id,
                to_agent=args.to,
                from_agent=from_agent,
                text=args.text,
            )
            return 0

        if args.cmd == "broadcast":
            agents = await coordinator.list_job_agents(job_id)
            for aid in agents:
                if not args.include_self and aid == from_agent:
                    continue
                await _send_one(
                    coordinator=coordinator,
                    job_id=job_id,
                    to_agent=aid,
                    from_agent=from_agent,
                    text=args.text,
                )
            return 0

        raise SystemExit("unknown cmd")
    finally:
        try:
            await coordinator.close()
        except Exception:
            pass


def main() -> None:
    import asyncio

    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
