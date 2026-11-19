"""mem-eva CLI.

  python -m memeva provision                       # create AgentCore Memory (SPU) in config.region
  python -m memeva ingest   --run R --dataset web3_zh [--stores tdam,agentcore] [--limit N]
  python -m memeva wait     --run R --dataset web3_zh
  python -m memeva evaluate --run R --dataset web3_zh [--arms none,tdam_full,tdam_l1,agentcore_spu]
  python -m memeva probe    --run R --dataset web3_zh --name isolation|secrets|spoof|lag|perf|deletion|failover
  python -m memeva report   --run R --dataset web3_zh
  python -m memeva all      --run R --dataset web3_zh       # ingest → wait → evaluate → report
  python -m memeva cleanup                                  # delete the AgentCore Memory created by provision
"""
from __future__ import annotations

import argparse
import json

from . import probes
from .backends.agentcore import cleanup, provision
from .common import load_config
from .pipeline import Run, log
from .report import summarize


def main() -> None:
    ap = argparse.ArgumentParser(prog="memeva")
    ap.add_argument("cmd")
    ap.add_argument("--run", default="r1")
    ap.add_argument("--dataset", default="web3_zh", choices=["web3_zh", "lme"])
    ap.add_argument("--config")
    ap.add_argument("--stores", default="tdam,agentcore")
    ap.add_argument("--arms")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--name")
    ap.add_argument("--kill-cmd", default="docker restart tdam-core")
    ap.add_argument("--no-coverage", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    stores = [s for s in a.stores.split(",") if s]
    arms = a.arms.split(",") if a.arms else cfg["arms"]
    workers = a.workers or cfg["workers"]

    if a.cmd == "provision":
        print(json.dumps(provision(cfg), indent=2))
        return
    if a.cmd == "cleanup":
        cleanup(cfg)
        return
    run = Run(cfg, a.run, a.dataset)
    run.data(a.limit)
    if a.cmd in ("ingest", "all"):
        run.ingest(stores, workers)
    if a.cmd in ("wait", "all"):
        run.wait(stores)
    if a.cmd in ("evaluate", "all"):
        run.evaluate(arms, workers, coverage=not a.no_coverage)
    if a.cmd == "rejudge":
        run.rejudge(arms, workers)
    if a.cmd in ("report", "all", "rejudge"):
        rep = summarize(run, arms)
        print((run.dir / "results.md").read_text())
        log(f"report written to {run.dir}/report.json")
    if a.cmd == "probe":
        fn = {"isolation": lambda: probes.isolation(run, stores), "secrets": lambda: probes.secrets(run, stores),
              "spoof": lambda: probes.spoof(run), "lag": lambda: probes.lag(run, stores),
              "perf": lambda: probes.perf(run, [x for x in arms if x != "none"]),
              "deletion": lambda: probes.deletion(run, stores),
              "failover": lambda: probes.failover(run, a.kill_cmd),
              "tdam_team_scope": lambda: probes.tdam_team_scope(run)}[a.name]
        fn()


if __name__ == "__main__":
    main()
