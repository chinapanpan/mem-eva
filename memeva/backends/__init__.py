from .base import Backend, NoMemory, Record, Retrieval


def make(arm: str, cfg: dict, journal=None) -> Backend:
    if arm == "none":
        return NoMemory()
    if arm in ("tdam_full", "tdam_l1"):
        from .tdam import TdamBackend
        return TdamBackend(cfg, mode=arm.split("_", 1)[1], journal=journal)
    if arm in ("tdam_shared_full", "tdam_shared_l1"):
        # same engine, shared-team topology: every user under one team+agent (L2/L3 shared by design)
        from .tdam import TdamBackend
        sid = cfg["tdam"]["service_id"] + "-shared"
        c = dict(cfg, tdam=dict(cfg["tdam"], team_id=f"team-{sid}", service_id=sid))  # standalone ignores service_id for L2/L3
        be = TdamBackend(c, mode=arm.rsplit("_", 1)[1], journal=journal)
        be.name = arm
        return be
    if arm == "agentcore_spu":
        from .agentcore import AgentCoreBackend
        return AgentCoreBackend(cfg, journal=journal)
    raise ValueError(arm)
