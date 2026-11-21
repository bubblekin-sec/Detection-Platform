"""Deterministic Sigma -> KQL conversion for Microsoft Defender XDR Advanced Hunting."""

from dataclasses import dataclass

from sigma.backends.kusto import KustoBackend
from sigma.exceptions import SigmaError
from sigma.pipelines.microsoftxdr import microsoft_xdr_pipeline
from sigma.rule import SigmaRule


@dataclass
class KqlResult:
    kql: str | None
    error: str | None = None


def to_defender_xdr_kql(rule: SigmaRule) -> KqlResult:
    """Convert a validated rule. A rule with no Defender XDR mapping returns an error reason, not an exception."""
    backend = KustoBackend(processing_pipeline=microsoft_xdr_pipeline())
    try:
        queries = backend.convert_rule(rule)
    except (SigmaError, NotImplementedError) as error:
        # NotImplementedError is how pySigma backends report an unsupported feature, e.g. `|cased`.
        return KqlResult(kql=None, error=str(error))
    if not queries:
        return KqlResult(kql=None, error="Conversion produced no query.")
    return KqlResult(kql="\n".join(str(query) for query in queries))
