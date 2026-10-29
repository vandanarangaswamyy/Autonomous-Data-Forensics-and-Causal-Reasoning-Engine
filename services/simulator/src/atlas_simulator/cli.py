"""Evaluator/operator CLI. Never expose these mutation commands as agent tools."""

import argparse
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from atlas_database.connection import engine_for
from atlas_evaluation.registry import PrivateRegistry

from atlas_simulator.domain import FaultConfig, FaultKind, GenerationConfig, Profile, Scenario
from atlas_simulator.engine import Simulator
from atlas_simulator.settings import SimulatorSettings
from atlas_simulator.streaming import KafkaPublisher, consume_stream, publish_outbox


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="ATLAS enterprise simulator (dedicated fixture DB only)"
    )
    commands = root.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--profile", choices=list(Profile), default="small")
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--config", type=Path, help="JSON GenerationConfig overrides profile")
    fault = commands.add_parser("inject")
    fault.add_argument("--config", required=True, type=Path, help="JSON FaultConfig")
    commands.add_parser("reset")
    replay = commands.add_parser("replay")
    replay.add_argument("fault_id", type=UUID)
    commands.add_parser("ground-truth")
    scenario = commands.add_parser("scenario")
    scenario.add_argument("config", type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("--replay", action="store_true")
    consume = commands.add_parser("consume")
    consume.add_argument("--group", default="atlas-observations")
    consume.add_argument("--max-messages", type=int, default=1000)
    commands.add_parser("schemas")
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "schemas":
        from atlas_contracts.simulator_events import EVENT_ADAPTER

        print(json.dumps(EVENT_ADAPTER.json_schema(), indent=2))
        return
    settings = SimulatorSettings()
    engine = engine_for(settings.database_url.get_secret_value())
    simulator = Simulator(engine)
    private = PrivateRegistry(settings.private_dir)
    try:
        match args.command:
            case "generate":
                config = (
                    GenerationConfig.model_validate_json(args.config.read_text())
                    if args.config
                    else GenerationConfig.profile(Profile(args.profile), args.seed)
                )
                print(json.dumps(private.initialize(simulator, config), indent=2))
            case "inject":
                fault = FaultConfig.model_validate_json(args.config.read_text())
                record = private.inject(simulator, fault)
                print(record.model_dump_json(indent=2))
            case "reset":
                print(json.dumps(private.reset(simulator), indent=2))
            case "replay":
                print(private.replay(simulator, args.fault_id).model_dump_json(indent=2))
            case "ground-truth":
                print(private.dump())
            case "scenario":
                scenario = Scenario.model_validate_json(args.config.read_text())
                baseline = private.scenario_baseline(simulator, scenario.config)
                record = private.inject(simulator, scenario.fault)
                before, after = (
                    Decimal(baseline[scenario.metric]),
                    Decimal(record.affected_metrics[scenario.metric]),
                )
                changed = after > before if scenario.direction == "increase" else after < before
                if not changed:
                    raise RuntimeError("Scenario failed to produce its expected symptom")
                print(
                    json.dumps(
                        {
                            "scenario": scenario.name,
                            "baseline": baseline,
                            "affected": record.affected_metrics,
                            "fault_id": str(record.fault_id),
                            "expected_evidence": scenario.expected_evidence,
                        },
                        indent=2,
                    )
                )
            case "publish":
                publisher = KafkaPublisher(settings.kafka_bootstrap, settings.kafka_topic)
                print(json.dumps({"published": publish_outbox(engine, publisher, args.replay)}))
            case "consume":
                if args.max_messages < 1:
                    raise ValueError("max-messages must be positive")
                received, applied = consume_stream(
                    engine,
                    settings.kafka_bootstrap,
                    settings.kafka_topic,
                    args.group,
                    args.max_messages,
                )
                print(json.dumps({"received": received, "applied": applied}))
            case _:
                raise ValueError(f"Unknown command; choices include {list(FaultKind)}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
