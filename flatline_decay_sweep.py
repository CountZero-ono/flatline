from datetime import datetime

DECAY_THRESHOLDS = {
    "ARCHITECTURAL": 180,
    "OPERATIONAL": 30,
    "TRANSIENT": 7,
    "PERSONAL": None,
}


def is_decayed(fact, now):
    """Returns True if the fact has exceeded its decay threshold."""
    if fact.get("status") not in ("ACTIVE", "VALIDATED"):
        return False
    decay_class = fact.get("decay_class")
    if decay_class == "PERSONAL":
        return False
    threshold = DECAY_THRESHOLDS.get(decay_class)
    if threshold is None:
        return False
    valid_from = fact.get("valid_from")
    if not valid_from:
        return False
    if isinstance(valid_from, str):
        valid_from = datetime.fromisoformat(valid_from)
    elapsed = (now - valid_from).days
    return elapsed >= threshold


def _get_facts(neo4j_session):
    """Queries Neo4j for all facts with status ACTIVE or VALIDATED."""
    result = neo4j_session.run(
        """
        MATCH (f:Fact)
        WHERE f.status IN ['ACTIVE', 'VALIDATED']
        RETURN f.id AS id, f.statement AS statement,
               f.decay_class AS decay_class,
               f.valid_from AS valid_from,
               f.status AS status
        """
    )
    return [record.data() for record in result]


def get_decay_candidates(neo4j_session, now=None):
    """Returns facts that would be decayed without writing anything."""
    if now is None:
        now = datetime.utcnow()
    facts = _get_facts(neo4j_session)
    return [f for f in facts if is_decayed(f, now)]


def sweep_facts(neo4j_session, now=None):
    """Decays all facts that have exceeded their decay threshold."""
    if now is None:
        now = datetime.utcnow()
    facts = _get_facts(neo4j_session)
    total = len(facts)
    decayed = 0
    for fact in facts:
        if is_decayed(fact, now):
            neo4j_session.run(
                """
                MATCH (f:Fact {id: $fact_id})
                SET f.status = 'DECAYED', f.valid_until = $valid_until
                """,
                fact_id=fact["id"],
                valid_until=now.isoformat(),
            )
            decayed += 1
    return {
        "swept": total,
        "decayed": decayed,
        "timestamp": now.isoformat(),
    }
