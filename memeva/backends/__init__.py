from .base import Backend, NoMemory, Record, Retrieval


def make(arm: str, cfg: dict, journal=None) -> Backend:
    if arm == "none":
        return NoMemory()
    if arm in ("tdam_full", "tdam_l1"):
        from .tdam import TdamBackend
        return TdamBackend(cfg, mode=arm.split("_", 1)[1], journal=journal)
    if arm == "agentcore_spu":
        from .agentcore import AgentCoreBackend
        return AgentCoreBackend(cfg, journal=journal)
    raise ValueError(arm)
